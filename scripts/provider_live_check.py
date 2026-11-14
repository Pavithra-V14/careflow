"""OPT-IN live check of the three hosted providers, run BY THE OWNER, by hand, on a developer machine (owner decision 2026-10-30; Appendix B discipline). NEVER run by CI and by nothing else.

    uv run python scripts/provider_live_check.py --provider all    --yes-synthetic     (the three, one after the other, on a built-in DUMMY letter)
    uv run python scripts/provider_live_check.py --provider gemini --yes-synthetic     (reads the built-in dummy letter; --image path/to/DUMMY.png to use your own dummy)
    uv run python scripts/provider_live_check.py --provider jev    --yes-synthetic
    uv run python scripts/provider_live_check.py --provider sarvam --yes-synthetic

What it does: ONE small request per run to the named provider, with SYNTHETIC input (a DUMMY image you made, a made-up sentence), calling the APPLICATION'S OWN adapter (so a later change to an adapter is re-verified by
running this again), then prints WHAT THE SERVICE DID: pass or fail in fixed words, the usage seen, the elapsed time, and for Jev the downstream model that answered. It does not verify prices or rate limits. It reads only
the repository's `.env` (no shell fallback, never `.env.cli`), REFUSES to run under CI or unless CAREFLOW_PROFILE=demo and CAREFLOW_DATA_POLICY=synthetic_only, needs the explicit `--yes-synthetic`, and
never prints or logs a key. A real document or a real patient sentence must NEVER be used here.

If every line says PASS, set `CAREFLOW_<PROVIDER>_VERIFIED=<today's date>` in `.env` (GEMINI -> CAREFLOW_GEMINI_VERIFIED, jev -> CAREFLOW_OPENROUTER_VERIFIED, sarvam -> CAREFLOW_SARVAM_VERIFIED). The application builds a
provider only after that date is recorded. A FAIL line means the adapter's assumption about the real service is wrong: do not record the date; send the printed facts back.
"""

import argparse
import asyncio
import os
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path

from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from careflow import provider_setup, synthetic_letter
from careflow.adapters import gemini, openrouter, sarvam
from careflow.gateway import RoleDailyCeilings
from careflow.ports import (
    DataClass,
    PermanentError,
    TransientError,
    TranslationRequest,
    VisionRequest,
)

ROOT = Path(__file__).resolve().parent.parent
KEYS = {"gemini": "GEMINI_API_KEY", "jev": "OPENROUTER_API_KEY", "sarvam": "SARVAM_API_KEY"}
results: list[tuple[bool, str, str]] = []
PROVIDERS = ("gemini", "jev", "sarvam")


def cost_line(provider: str, tokens_in: int = 0, tokens_out: int = 0, chars: int = 0) -> str:
    """Only a number: the actual cost (the free tier is zero) and the at-scale estimate, from the PLACEHOLDER prices in config/demo-providers.yaml (not quotes). Never a key, never content."""
    cfg = provider_setup.load_provider_config()
    if provider == "sarvam":
        c = cfg.translation.price_per_1k_chars_usd * chars / 1000
        return f"cost: about ${c:.6f} (placeholder price)"
    row = next(r for r in cfg.rows if r.provider == provider)
    actual = row.price_in_per_1k * tokens_in / 1000 + row.price_out_per_1k * tokens_out / 1000
    shadow = row.shadow_in_per_1k * tokens_in / 1000 + row.shadow_out_per_1k * tokens_out / 1000
    return f"cost: actual ${actual:.6f}, at-scale estimate ${shadow:.6f} (placeholder prices)"


def record(name: str, ok: bool, detail: str) -> None:
    results.append((ok, name, detail))
    print(("PASS  " if ok else "FAIL  ") + f"{name}: {detail}")


def names(obj: object) -> str:
    return ", ".join(sorted(obj)) if isinstance(obj, dict) else type(obj).__name__


async def check_gemini(key: str, image: bytes, content_type: str) -> None:
    """Calls the APPLICATION'S adapter (so a change to the adapter is re-verified), with a DUMMY image you made yourself."""
    adapter = gemini.GeminiVisionAdapter(key, "gemini-2.5-flash", timeout_seconds=60)
    started = time.monotonic()
    try:
        out = await adapter(VisionRequest(image=image, content_type=content_type, page=1, clinic_id=uuid.uuid4()))
    except (TransientError, PermanentError) as exc:
        record("1 the adapter read the page (text came back, finishReason STOP)", False, f"{exc}")
        return
    record("1 the adapter read the page (text came back, finishReason STOP)", bool(out.value.text.strip()), f"{len(out.value.text)} characters in {time.monotonic() - started:.1f}s; compare them with your dummy page by eye")
    record("2 usage reported", out.usage.input_tokens > 0, f"input {out.usage.input_tokens}, output {out.usage.output_tokens} tokens (an ESTIMATE if the service omitted a count); " + cost_line("gemini", out.usage.input_tokens, out.usage.output_tokens))
    found = synthetic_letter.words_found(out.value.text) if image == synthetic_letter.png_bytes() else None
    if found is not None:
        record("3 the dummy letter was read (expected words found)", found >= 3, f"{found} of {len(synthetic_letter.EXPECTED_WORDS)} expected words")


async def check_jev(key: str) -> None:
    client = openrouter.JevRouterClient(key, timeout_seconds=20)
    started = time.monotonic()
    try:
        reply = await client.decide({"task": "choice", "question": "triage_band", "input": "A made-up sentence: the synthetic patient asks for a routine check.", "options": ["routine", "urgent"]})
    except (TransientError, PermanentError) as exc:
        record("1 the router answered with a strict-JSON choice", False, f"{exc}")
        return
    probs = reply.get("probabilities") if isinstance(reply, dict) else None
    record("1 the router answered with a strict-JSON choice", isinstance(probs, dict) and set(probs) == {"routine", "urgent"}, f"in {time.monotonic() - started:.1f}s; probabilities keys: {names(probs)}")
    usage = reply.get("usage") if isinstance(reply, dict) else None
    tin = int(usage.get("prompt_tokens", 0)) if isinstance(usage, dict) else 0
    tout = int(usage.get("completion_tokens", 0)) if isinstance(usage, dict) else 0
    record("2 usage reported (prompt_tokens, completion_tokens)", usage is not None, f"usage: {usage}; " + cost_line("jev", tin, tout))
    print(f"info: the router says the DOWNSTREAM MODEL that answered was: {reply.get('served_by')!r} (the text went to it; check its data handling before you rely on this row)")


async def check_sarvam(key: str) -> None:
    adapter = sarvam.SarvamTranslation(key, timeout_seconds=5, price_per_1k_chars_usd=Decimal("0.06"), ceilings=RoleDailyCeilings({"translation": Decimal(1)}))
    text = ("Hello, this is a made-up test sentence. " * 30)[: sarvam.MAX_CHARS]
    started = time.monotonic()
    try:
        out = await adapter.translate(TranslationRequest(text, "en", "ta", DataClass.SYNTHETIC))
    except (TransientError, PermanentError) as exc:
        record("1 a text of exactly 1,000 characters is accepted", False, f"{exc}")
        return
    record("1 a text of exactly 1,000 characters is accepted", bool(out.value.text.strip()), f"{len(text)} characters in, {len(out.value.text)} out, {time.monotonic() - started:.1f}s; " + cost_line("sarvam", chars=len(text)))
    print("info: this script does NOT verify the price per character or the rate limits (Appendix B): read them on Sarvam's pricing page and update config/demo-providers.yaml if they differ.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", required=True, choices=[*PROVIDERS, "all"])
    ap.add_argument("--yes-synthetic", action="store_true", required=True, help="confirm that the input is synthetic (a dummy image, a made-up sentence)")
    ap.add_argument("--image", type=Path, help="gemini only, optional: a DUMMY .png or .jpg page you made yourself (never a real document); the built-in dummy letter is used when left out")
    args = ap.parse_args()
    if (os.environ.get("CAREFLOW_ENV") or "").lower() == "ci" or os.environ.get("GITHUB_ACTIONS"):
        print("refusing to run under CI: this check is for the owner's own machine")
        return 2
    env = dict(dotenv_values(ROOT / ".env"))  # ONLY the repository's .env
    if env.get("CAREFLOW_PROFILE") != "demo" or (env.get("CAREFLOW_DATA_POLICY") or "").strip().lower() != "synthetic_only":
        print("refusing: .env must say CAREFLOW_PROFILE=demo and CAREFLOW_DATA_POLICY=synthetic_only")
        return 2
    chosen = list(PROVIDERS) if args.provider == "all" else [args.provider]
    for provider in chosen:
        key = (env.get(KEYS[provider]) or "").strip()
        if not key:
            print(f"refusing {provider}: {KEYS[provider]} is not set in the repository's .env")
            results.append((False, f"{provider} key present", "not set"))
            continue
        print(f"--- {provider}")
        if provider == "gemini":
            if args.image:
                asyncio.run(check_gemini(key, args.image.read_bytes(), "image/jpeg" if args.image.suffix.lower() in (".jpg", ".jpeg") else "image/png"))
            else:
                asyncio.run(check_gemini(key, synthetic_letter.png_bytes(), "image/png"))
        elif provider == "jev":
            asyncio.run(check_jev(key))
        else:
            asyncio.run(check_sarvam(key))
    failed = [name for ok, name, _ in results if not ok]
    print("\nALL EXPECTATIONS HELD: you may record the VERIFIED date" if not failed else f"\nEXPECTATIONS NOT MET: {failed}. Do NOT record the VERIFIED date.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
