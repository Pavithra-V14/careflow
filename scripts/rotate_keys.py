"""Manual demographic key rotation (BUILD-SPEC.md §17.2). Never prints a key, a demographic or a connection string.

    python scripts/rotate_keys.py status
    python scripts/rotate_keys.py rotate --from 1 --to 2 [--clinic <uuid>] [--batch-size 200]
    python scripts/rotate_keys.py verify --to 2 [--clinic <uuid>]

Run order and the human steps around it are in docs/runbooks/key_rotation.md. Message bodies (msg.messages, migration 0011)
are rotated, verified and counted by the same commands, but only ever over this migrator connection: the functions refuse
any connection that does not bypass row-level security.

Keys are read from the environment (CAREFLOW_ENC_SECRET_V<n> / CAREFLOW_HMAC_SECRET_V<n>), with `.env` as the local
fallback; the process environment wins. Both the old and the new version must be present. The database connection
is DATABASE_URL_MIGRATOR (session-mode pooler): a rotation crosses every clinic, so it runs as the maintenance
credential, not as a clinic user. The transaction is committed after every batch, so a stopped run keeps its progress
and re-running resumes (the work is idempotent). Exit status: 0 success, 1 the run failed or verify found problems,
2 bad arguments or configuration (nothing was read or written).
"""

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from careflow.crypto import CryptoError, DemographicVault
from careflow.rotation import (
    DEFAULT_BATCH_SIZE,
    RotationError,
    message_version_usage,
    messages_table_exists,
    rotate_key_version,
    rotate_message_bodies,
    verify_message_bodies,
    verify_rotation,
    version_usage,
)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--env-file", default=str(Path(__file__).resolve().parent.parent / ".env"))
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="rows still referencing each key_version")
    r = sub.add_parser("rotate", help="re-encrypt from one key_version to another")
    r.add_argument("--from", dest="from_version", type=int, required=True)
    r.add_argument("--to", dest="to_version", type=int, required=True)
    r.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    r.add_argument("--clinic", type=uuid.UUID, default=None, help="limit to one clinic")
    v = sub.add_parser("verify", help="read-only integrity check of everything on a key_version")
    v.add_argument("--to", dest="to_version", type=int, required=True)
    v.add_argument("--clinic", type=uuid.UUID, default=None)
    return p


async def _run(args: argparse.Namespace, env: dict[str, str], vault: DemographicVault) -> int:
    url = env.get("DATABASE_URL_MIGRATOR")
    if not url:
        print("DATABASE_URL_MIGRATOR is not set", file=sys.stderr)
        return 2
    engine = create_async_engine(
        make_url(url).set(drivername="postgresql+asyncpg"), connect_args={"statement_cache_size": 0}
    )
    try:
        async with engine.connect() as conn:
            if args.command == "status":
                for v, n in (await version_usage(conn)).items():
                    print(f"key_version {v}: {n['ciphertexts']} ciphertext columns, {n['tokens']} tokens")
                if await messages_table_exists(conn):
                    # Counted over this migrator connection, unscoped; an app-role count would be silently too low.
                    for v, n in (await message_version_usage(conn)).items():
                        print(f"key_version {v}: {n} message bodies")
                else:
                    print("message bodies: msg.messages does not exist yet (migration 0011 not applied)")
                print("a key may be retired only when BOTH the ciphertext columns AND the message bodies show 0")
                return 0
            if args.command == "verify":
                rep = await verify_rotation(conn, vault, to_version=args.to_version, clinic_id=args.clinic)
                print(f"checked {rep.patients_checked} patients on key_version {rep.to_version}: "
                      f"{len(rep.unreadable)} unreadable, {len(rep.token_problems)} token problems")
                for pid in [*rep.unreadable, *rep.token_problems]:
                    print(f"  problem: patient {pid}")
                ok = rep.ok
                if await messages_table_exists(conn):
                    mrep = await verify_message_bodies(conn, vault, to_version=args.to_version, clinic_id=args.clinic)
                    print(f"checked {mrep.messages_checked} message bodies on key_version {mrep.to_version}: {len(mrep.unreadable)} unreadable")
                    for mid in mrep.unreadable:
                        print(f"  problem: message {mid}")
                    ok = ok and mrep.ok
                return 0 if ok else 1

            def progress(rep) -> None:
                print(f"  batch {rep.batches}: {rep.patients_rotated} patients, {rep.tokens_written} tokens so far")

            rep = await rotate_key_version(
                conn, vault, from_version=args.from_version, to_version=args.to_version,
                batch_size=args.batch_size, clinic_id=args.clinic, commit_each_batch=True, on_batch=progress,
            )
            print(f"rotated {rep.patients_rotated} patients in {rep.batches} batches "
                  f"(v{rep.from_version} -> v{rep.to_version}), {rep.tokens_written} tokens written")
            if await messages_table_exists(conn):
                mrep = await rotate_message_bodies(
                    conn, vault, from_version=args.from_version, to_version=args.to_version,
                    batch_size=args.batch_size, clinic_id=args.clinic, commit_each_batch=True,
                )
                print(f"rotated {mrep.messages_rotated} message bodies in {mrep.batches} batches")
            else:
                print("message bodies: msg.messages does not exist yet (migration 0011 not applied); nothing to do")
            print("next: run `verify`, then follow the retirement checklist in docs/runbooks/key_rotation.md")
            return 0
    except (RotationError, CryptoError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()


def main() -> int:
    args = _parser().parse_args()
    env = {**{k: v for k, v in dotenv_values(args.env_file).items() if v is not None}, **os.environ}
    try:
        vault = DemographicVault.from_env(env)
        if args.command == "rotate":
            from careflow.rotation import (
                check_versions,  # same validation the engine does, before any connection
            )

            check_versions(vault, args.from_version, args.to_version)
        elif args.command == "verify" and args.to_version not in vault.key_versions:
            raise RotationError(f"no key loaded for key_version {args.to_version}")
    except (RotationError, CryptoError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return asyncio.run(_run(args, env, vault))


if __name__ == "__main__":
    sys.exit(main())
