"""OPT-IN live check of the three hosted providers, run BY THE OWNER, by hand, on a developer machine (owner decision 2026-10-30; Appendix B discipline). NEVER run by CI and by nothing else.

    uv run python scripts/provider_live_check.py --provider gemini --yes-synthetic [--image path/to/DUMMY.png]
    uv run python scripts/provider_live_check.py --provider jev    --yes-synthetic
    uv run python scripts/provider_live_check.py --provider sarvam --yes-synthetic

What it does: ONE small request per run to the named provider, with SYNTHETIC input (a one-pixel PNG, or a DUMMY image you name with --image; a made-up sentence), using the same HTTP helper and request builders
as the application, then prints WHAT THE SERVICE DID: the HTTP outcome in fixed words, the key NAMES of the answer (never values), whether the usage fields the adapter reads are present, the elapsed time. It reads only
the repository's `.env` (no shell fallback, never `.env.cli`), REFUSES to run under CI or unless CAREFLOW_PROFILE=demo and CAREFLOW_DATA_POLICY=synthetic_only, needs the explicit `--yes-synthetic`, and
never prints or logs a key. A real document or a real patient sentence must NEVER be used here.

If every line says PASS, set `CAREFLOW_<PROVIDER>_VERIFIED=<today's date>` in `.env` (GEMINI -> CAREFLOW_GEMINI_VERIFIED, jev -> CAREFLOW_OPENROUTER_VERIFIED, sarvam -> CAREFLOW_SARVAM_VERIFIED). The application builds a
provider only after that date is recorded. A FAIL line means the adapter's assumption about the real service is wrong: do not record the date; send the printed facts back.
"""

import argparse
import asyncio
import base64
import os
import sys
import time
import zlib
from pathlib import Path

from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from careflow.adapters import gemini, openrouter, sarvam
from careflow.adapters.provider_http import new_client, post_json
from careflow.ports import PermanentError, TransientError

ROOT = Path(__file__).resolve().parent.parent
KEYS = {"gemini": "GEMINI_API_KEY", "jev": "OPENROUTER_API_KEY", "sarvam": "SARVAM_API_KEY"}
results: list[tuple[bool, str, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    results.append((ok, name, detail))
    print(("PASS  " if ok else "FAIL  ") + f"{name}: {detail}")


def tiny_png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return len(data).to_bytes(4, "big") + body + zlib.crc32(body).to_bytes(4, "big")

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", (1).to_bytes(4, "big") * 2 + b"\x08\x02\x00\x00\x00") + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff")) + chunk(b"IEND", b"")


def names(obj: object) -> str:
    return ", ".join(sorted(obj)) if isinstance(obj, dict) else type(obj).__name__


async def check_gemini(key: str, image: bytes, content_type: str) -> None:
    http = new_client(60)
    started = time.monotonic()
    body = {"contents": [{"role": "user", "parts": [{"text": gemini.INSTRUCTION}, {"inline_data": {"mime_type": content_type, "data": base64.b64encode(image).decode()}}]}], "generationConfig": {"temperature": 0}}
    try:
        answer = await post_json(http, f"{gemini.HOST}/v1beta/models/gemini-2.5-flash:generateContent", headers={"x-goog-api-key": key}, body=body, seconds=60)
    except (TransientError, PermanentError) as exc:
        record("1 request accepted", False, f"{exc}")
        return
    finally:
        await http.aclose()
    record("1 request accepted", True, f"HTTP 200 in {time.monotonic() - started:.1f}s; answer keys: {names(answer)}")
    cands = answer.get("candidates")
    record("2 candidates[0].content.parts shape", isinstance(cands, list) and bool(cands) and isinstance(cands[0], dict) and isinstance((cands[0].get("content") or {}).get("parts"), list),
           f"candidate keys: {names(cands[0]) if isinstance(cands, list) and cands else 'none'}")
    meta = answer.get("usageMetadata")
    record("3 usageMetadata has promptTokenCount and candidatesTokenCount", isinstance(meta, dict) and isinstance(meta.get("promptTokenCount"), int) and isinstance(meta.get("candidatesTokenCount"), int), f"usage keys: {names(meta)}")
    print("note: with the one-pixel image the transcription is expected to be empty; pass --image with a DUMMY page to see text come back.")


async def check_jev(key: str) -> None:
    http = new_client(20)
    client = openrouter.JevRouterClient(key, timeout_seconds=20, http=http)
    started = time.monotonic()
    try:
        reply = await client.decide({"task": "choice", "question": "triage_band", "input": "A made-up sentence: the synthetic patient asks for a routine check.", "options": ["routine", "urgent"]})
    except (TransientError, PermanentError) as exc:
        record("1 router answered with a strict-JSON choice", False, f"{exc}")
        return
    finally:
        await http.aclose()
    probs = reply.get("probabilities") if isinstance(reply, dict) else None
    record("1 router answered with a strict-JSON choice", isinstance(probs, dict) and set(probs) == {"routine", "urgent"}, f"in {time.monotonic() - started:.1f}s; probabilities keys: {names(probs)}")
    record("2 usage reported (prompt_tokens, completion_tokens)", reply.get("usage") is not None, f"usage: {reply.get('usage')}")


async def check_sarvam(key: str) -> None:
    http = new_client(5)
    started = time.monotonic()
    try:
        answer = await post_json(http, sarvam.ENDPOINT, headers={"api-subscription-key": key}, body={"input": "Hello, this is a made-up test sentence.", "source_language_code": "en-IN", "target_language_code": "ta-IN", "model": sarvam.MODEL}, seconds=5)
    except (TransientError, PermanentError) as exc:
        record("1 request accepted", False, f"{exc}")
        return
    finally:
        await http.aclose()
    record("1 request accepted", True, f"HTTP 200 in {time.monotonic() - started:.1f}s; answer keys: {names(answer)}")
    record("2 translated_text is a non-empty string", isinstance(answer.get("translated_text"), str) and bool(answer["translated_text"].strip()), "translated_text present" if "translated_text" in answer else "translated_text missing")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", required=True, choices=sorted(KEYS))
    ap.add_argument("--yes-synthetic", action="store_true", required=True, help="confirm that the input is synthetic (a dummy image, a made-up sentence)")
    ap.add_argument("--image", type=Path, help="gemini only: a DUMMY .png or .jpg you made yourself (never a real document)")
    args = ap.parse_args()
    if (os.environ.get("CAREFLOW_ENV") or "").lower() == "ci" or os.environ.get("GITHUB_ACTIONS"):
        print("refusing to run under CI: this check is for the owner's own machine")
        return 2
    env = dict(dotenv_values(ROOT / ".env"))  # ONLY the repository's .env
    if env.get("CAREFLOW_PROFILE") != "demo" or (env.get("CAREFLOW_DATA_POLICY") or "").strip().lower() != "synthetic_only":
        print("refusing: .env must say CAREFLOW_PROFILE=demo and CAREFLOW_DATA_POLICY=synthetic_only")
        return 2
    key = (env.get(KEYS[args.provider]) or "").strip()
    if not key:
        print(f"refusing: {KEYS[args.provider]} is not set in the repository's .env")
        return 2
    if args.provider == "gemini":
        image, content_type = (args.image.read_bytes(), "image/jpeg" if args.image.suffix.lower() in (".jpg", ".jpeg") else "image/png") if args.image else (tiny_png(), "image/png")
        asyncio.run(check_gemini(key, image, content_type))
    elif args.provider == "jev":
        asyncio.run(check_jev(key))
    else:
        asyncio.run(check_sarvam(key))
    failed = [name for ok, name, _ in results if not ok]
    print("\nALL EXPECTATIONS HELD: you may record the VERIFIED date" if not failed else f"\nEXPECTATIONS NOT MET: {failed}. Do NOT record the VERIFIED date.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
