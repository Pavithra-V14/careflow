"""Patient portal sign-in by e-mail and password: A DEMO CONVENIENCE ONLY (owner decision 2026-11-20). Phone-code sign-in needs a paid SMS provider, a new external service that is not approved.

What it changes, and what it does not. Supabase Auth does the sign-in (the backend only verifies the token). The portal's CLAIM step needs a verified phone claim: a patient binds to the record whose phone token and
name+date-of-birth token match. A patient who signed in by e-mail has no phone claim, so `contact_phone` supplies a DETERMINISTIC SYNTHETIC phone derived from the token's ACCOUNT ID (`phone_for_account`, the `sub`
claim, which Supabase assigns and nobody can choose), and the claim then runs through the SAME function with the SAME tokens as the phone flow: the consent check, the matching rule (name + date of birth AND the
contact token), the pending-review path, the front-desk verification and row-level security are all unchanged. Nothing binds an account except `mpi.claim_patient_record`. The record being claimed is created by
`scripts/seed_dev_portal_patient.py --account-id <the auth user's id>`, which derives the same synthetic phone.

Why the account id and not the e-mail address (adversarial review, 2026-11-20): an e-mail address can be signed up first by someone else on a project that allows open sign-up, and addresses can fold differently in
Python and in the auth service. The account id is assigned by the auth service at creation, so only that one user's token can ever derive that phone. The token must still carry a plain e-mail address (that is what
makes it an e-mail sign-in) and must not be marked unconfirmed.

The refusal. The e-mail path exists only when ALL hold: the switch `CAREFLOW_DEMO_EMAIL_SIGNIN` is exactly `on`, the deployment is profile=demo with data_policy=synthetic_only, and the token carries a plain e-mail
address (and is not marked unverified). A real phone claim ALWAYS wins and is never touched by this module. In every other situation `contact_phone` returns None and the route answers the same fixed 403 as for a token with no
phone. Outside demo + synthetic_only the application REFUSES TO START with the switch on (`refuse_outside_demo`, called while the app is built). The clinical profile therefore keeps phone-code sign-in only.
"""

import hashlib
import re
import uuid
from collections.abc import Mapping
from typing import Any, Final

from careflow.mpi import InvalidPhone, canonicalize_phone

SETTING: Final = "CAREFLOW_DEMO_EMAIL_SIGNIN"
_EMAIL: Final = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,189}$")


class EmailSigninRefused(Exception):
    """The e-mail sign-in switch is on outside the synthetic demo. Fixed words."""


def enabled(environ: Mapping[str, str]) -> bool:
    return (environ.get(SETTING) or "").strip().lower() == "on"


def refuse_outside_demo(environ: Mapping[str, str], *, demo_allowed: bool) -> None:
    """Called while the app is built: the switch outside profile=demo with data_policy=synthetic_only is a refusal to start."""
    if enabled(environ) and not demo_allowed:
        raise EmailSigninRefused(f"{SETTING} may be on only in profile=demo with data_policy=synthetic_only: refusing to start")


def phone_for_account(account_id: str | uuid.UUID) -> str:
    """A deterministic, canonical +91 mobile number derived from the account id (a UUID, case-insensitive). Synthetic: it is nobody's number and is never dialled, shown or stored as a phone."""
    canonical = str(uuid.UUID(str(account_id).strip()))
    seed = ("careflow-demo-email-signin:account:" + canonical).encode("utf-8")
    for counter in range(100):
        number = int.from_bytes(hashlib.sha256(seed + bytes([counter])).digest()[:8], "big")
        digits = str(6 + number % 4) + f"{(number // 4) % 10**9:09d}"  # a mobile-looking number starting 6 to 9
        try:
            return canonicalize_phone(digits)
        except InvalidPhone:  # ten identical digits and the like: the next counter value
            continue
    raise EmailSigninRefused("no usable number could be derived")  # unreachable in practice


def contact_phone(claims: Mapping[str, Any], environ: Mapping[str, str], *, demo_allowed: bool) -> str | None:
    """The phone the claim step uses: the token's verified phone if there is one (always), otherwise, ONLY in the demo with the switch on and for a token that carries a plain e-mail address, the synthetic phone of its
    account id. None means the claim is refused."""
    phone = claims.get("phone")
    if isinstance(phone, str) and phone.strip():
        return phone
    if not (enabled(environ) and demo_allowed):
        return None
    email = claims.get("email")
    if not isinstance(email, str) or not _EMAIL.match(email.strip()):
        return None
    meta = claims.get("user_metadata")
    if isinstance(meta, Mapping) and meta.get("email_verified") is False:
        return None  # an address the project itself says is not confirmed is not a contact
    try:
        return phone_for_account(str(claims.get("sub")))
    except ValueError:
        return None
