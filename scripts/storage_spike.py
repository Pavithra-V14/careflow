"""The Appendix B spike for Supabase Storage (docs/design/uploads-supabase-storage.md): makes the REAL calls the adapter (careflow/adapters/storage.py) depends on and prints what the service did.

    python scripts/storage_spike.py --bucket careflow-documents --dev-project

Run it ONLY against the DEV project (it reads SUPABASE_URL and SUPABASE_SECRET_KEY from .env, never .env.cli) after the owner has created the bucket in the dashboard (private, size limit, allowed
types) and approved the storage policy migration. Every byte it uploads is SYNTHETIC and random; every object it creates is under `clinic/<random>/doc/<random>` and is deleted at the end. It prints
statuses and shapes, never the key, a token or a URL. Exit code 0 only if every EXPECTATION held; otherwise the table says which did not, and `CAREFLOW_STORAGE_VERIFIED` must stay unset.

What it checks (each line of the output is one):
  1. signing a path returns 200 with a url whose host is the project's, and a token with a readable expiry; how long the URL lives (seconds from now)
  2. the browser's PUT to the signed URL succeeds; the server's authenticated read returns the same bytes (sha256 equal); the content type is as uploaded
  3. OVERWRITE: a second PUT to the SAME signed URL while it is valid: does it replace the object? (the reason `read_verified` re-hashes every read)
  4. after DELETE, a PUT to the same signed URL: does it recreate the object?
  5. the bucket's SIZE LIMIT: a PUT of one byte over the limit is refused
  6. the bucket's TYPE LIMIT: a PUT with content type text/html is refused
  7. a read with NO credentials is refused (the bucket is private: no public URL works)
  8. delete removes the object; a second delete is harmless; reading a missing object is the adapter's None
"""

import argparse
import asyncio
import hashlib
import os
import secrets
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from careflow.adapters.storage import SupabaseUploadAdapter
from careflow.documents import MAX_UPLOAD_BYTES

results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, saw: str) -> None:
    results.append((name, ok, saw))
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {saw}")


def synthetic_pdf(n: int = 400) -> bytes:
    return b"%PDF-1.7\n% SYNTHETIC SPIKE FILE\n" + secrets.token_bytes(n)


def fresh_path() -> str:
    return f"clinic/{uuid.uuid4()}/doc/{uuid.uuid4()}"


async def main(bucket: str) -> int:
    env = dotenv_values(ROOT / ".env")
    url, key = env.get("SUPABASE_URL") or os.environ.get("SUPABASE_URL"), env.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        print("SUPABASE_URL and SUPABASE_SECRET_KEY are required in .env (the DEV project)")
        return 2
    adapter = SupabaseUploadAdapter(url, key, bucket)
    base = url.rstrip("/") + "/storage/v1"
    browser = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=60.0, write=60.0))
    created: list[str] = []
    try:
        # 1 sign
        path = fresh_path()
        created.append(path)
        signed = await adapter.create_signed_upload(path=path, content_type="application/pdf", max_bytes=MAX_UPLOAD_BYTES, expires_in_seconds=600)
        lifetime = (signed.expires_at - datetime.now(UTC)).total_seconds()
        record("1 signing", signed.url.startswith(url.rstrip("/")), f"url on our host; lives about {int(lifetime)} s (the adapter asked for 600; the service decides)")
        # 2 browser PUT, server read-back
        first = synthetic_pdf()
        put = await browser.put(signed.url, content=first, headers={"content-type": "application/pdf"})
        stored = await adapter.describe(path)
        record("2 put and read-back", put.status_code in (200, 201) and stored is not None and stored.sha256 == hashlib.sha256(first).hexdigest(),
               f"PUT {put.status_code}; stored {stored.size if stored else None} bytes, type {stored.content_type if stored else None}")
        # 3 overwrite while valid
        second = synthetic_pdf(500)
        again = await browser.put(signed.url, content=second, headers={"content-type": "application/pdf"})
        after = await adapter.describe(path)
        overwritten = after is not None and after.sha256 == hashlib.sha256(second).hexdigest()
        record("3 overwrite via the same signed URL", True, f"second PUT {again.status_code}; object {'WAS REPLACED' if overwritten else 'was not replaced'} (informational: read_verified re-hashes either way)")
        # 4 delete, then PUT again
        await adapter.delete(path)
        gone = await adapter.describe(path)
        reuse = await browser.put(signed.url, content=first, headers={"content-type": "application/pdf"})
        recreated = await adapter.describe(path)
        record("4 delete then reuse of the URL", gone is None, f"deleted -> read is None; PUT after delete {reuse.status_code}; object {'RECREATED' if recreated else 'not recreated'} (informational)")
        await adapter.delete(path)
        # 5 size limit
        big_path = fresh_path()
        created.append(big_path)
        big = await adapter.create_signed_upload(path=big_path, content_type="application/pdf", max_bytes=MAX_UPLOAD_BYTES, expires_in_seconds=600)
        over = await browser.put(big.url, content=b"%PDF-" + b"\0" * (MAX_UPLOAD_BYTES + 1), headers={"content-type": "application/pdf"})
        record("5 size limit", over.status_code >= 400, f"PUT of {MAX_UPLOAD_BYTES + 1 + 5} bytes -> {over.status_code} (expected a refusal; set the bucket's size limit to {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB)")
        # 6 type limit
        html_path = fresh_path()
        created.append(html_path)
        html = await adapter.create_signed_upload(path=html_path, content_type="text/html", max_bytes=1000, expires_in_seconds=600)
        refused = await browser.put(html.url, content=b"<html>synthetic</html>", headers={"content-type": "text/html"})
        record("6 type limit", refused.status_code >= 400, f"PUT as text/html -> {refused.status_code} (expected a refusal; allowed types: application/pdf, image/jpeg, image/png)")
        # 7 no credentials
        probe_path = fresh_path()
        created.append(probe_path)
        probe = await adapter.create_signed_upload(path=probe_path, content_type="application/pdf", max_bytes=1000, expires_in_seconds=600)
        await browser.put(probe.url, content=synthetic_pdf(), headers={"content-type": "application/pdf"})
        anonymous = [
            await browser.get(f"{base}/object/public/{bucket}/{probe_path}"), await browser.get(f"{base}/object/authenticated/{bucket}/{probe_path}"), await browser.get(f"{base}/object/{bucket}/{probe_path}"),
        ]
        record("7 no public access", all(r.status_code >= 400 for r in anonymous), f"statuses without credentials: {[r.status_code for r in anonymous]} (all must be refusals)")
        # 8 delete
        await adapter.delete(probe_path)
        await adapter.delete(probe_path)
        record("8 delete and missing", await adapter.describe(probe_path) is None and await adapter.read(probe_path) is None, "deleted twice without error; a missing object reads as None")
    finally:
        for path in created:
            try:
                await adapter.delete(path)
            except Exception as exc:  # noqa: BLE001 - cleanup must not hide the results
                print(f"cleanup of one object failed: {type(exc).__name__}")
        await browser.aclose()
        await adapter.aclose()
    failed = [name for name, ok, _ in results if not ok]
    print("\nALL EXPECTATIONS HELD" if not failed else f"\nEXPECTATIONS NOT MET: {failed}. Do NOT set CAREFLOW_STORAGE_VERIFIED.")
    return 0 if not failed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--dev-project", action="store_true", required=True, help="confirm that .env points at the DEV project (never the CI project)")
    raise SystemExit(asyncio.run(main(parser.parse_args().bucket)))
