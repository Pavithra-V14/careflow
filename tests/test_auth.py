"""JWT verification (careflow/auth.py). Pure: keys are generated per run and the JWKS endpoint is a local
fake behind httpx.MockTransport, so there is no network access and no committed key.
"""

import time
import uuid

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from careflow.auth import AuthError, AuthUnavailable, JWKSClient, verify_token

ISSUER = "https://example-project.supabase.co/auth/v1"
URL = "https://example-project.supabase.co/auth/v1/.well-known/jwks.json"


def make_key(kid: str):
    private = ec.generate_private_key(ec.SECP256R1())
    jwk = jwt.algorithms.ECAlgorithm.to_jwk(private.public_key(), as_dict=True)
    jwk.update({"kid": kid, "alg": "ES256", "use": "sig", "key_ops": ["verify"]})
    return private, jwk


class FakeJWKS:
    """A JWKS endpoint whose document and availability the test controls, counting every request."""

    def __init__(self, *jwks):
        self.keys = list(jwks)
        self.down = False
        self.body_override = None
        self.requests = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if self.down:
            return httpx.Response(503)
        if self.body_override is not None:
            return httpx.Response(200, content=self.body_override)
        return httpx.Response(200, json={"keys": self.keys})

    def client(self, **kw) -> JWKSClient:
        return JWKSClient(URL, http=httpx.AsyncClient(transport=httpx.MockTransport(self.handler)), **kw)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def token(private, kid="k1", *, alg="ES256", headers=None, **claims):
    payload = {
        "sub": str(uuid.uuid4()),
        "iss": ISSUER,
        "aud": "authenticated",
        "role": "authenticated",
        "exp": int(time.time()) + 600,
        **claims,
    }
    payload = {k: v for k, v in payload.items() if v is not None}
    hdr = {"kid": kid} if kid is not None else {}
    hdr.update(headers or {})
    return jwt.encode(payload, private, algorithm=alg, headers=hdr)


@pytest.fixture
def k1():
    return make_key("k1")


# ---- accepted ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_valid_token_returns_its_verified_claims(k1):
    private, jwk = k1
    sub = str(uuid.uuid4())
    claims = await verify_token(token(private, sub=sub, email="a@example.test"), FakeJWKS(jwk).client(), issuer=ISSUER)
    assert claims["sub"] == sub and claims["role"] == "authenticated" and claims["email"] == "a@example.test"


@pytest.mark.asyncio
async def test_the_key_is_chosen_by_kid_not_by_position():
    (p1, j1), (p2, j2) = make_key("old"), make_key("new")
    keys = FakeJWKS(j1, j2).client()
    assert (await verify_token(token(p2, "new"), keys, issuer=ISSUER))["role"] == "authenticated"
    assert (await verify_token(token(p1, "old"), keys, issuer=ISSUER))["role"] == "authenticated"
    with pytest.raises(AuthError):  # right key material, wrong kid label
        await verify_token(token(p2, "old"), keys, issuer=ISSUER)


# ---- rejected ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        {"exp": int(time.time()) - 3600},  # expired
        {"iss": "https://evil.example/auth/v1"},  # wrong issuer
        {"aud": "service_role"},  # wrong audience
        {"aud": None},  # no audience
        {"exp": None},  # no expiry: never accepted
        {"sub": None},  # no subject
        {"sub": "not-a-uuid"},
        {"role": "anon"},  # the project's anonymous key
        {"role": "service_role"},
        {"role": None},
        {"iss": None},
        {"nbf": int(time.time()) + 3600},  # not valid yet
    ],
)
async def test_bad_claims_are_rejected(k1, mutation):
    private, jwk = k1
    with pytest.raises(AuthError):
        await verify_token(token(private, **mutation), FakeJWKS(jwk).client(), issuer=ISSUER)


@pytest.mark.asyncio
async def test_a_token_signed_by_another_key_under_the_same_kid_is_rejected(k1):
    _, jwk = k1
    attacker, _ = make_key("k1")
    with pytest.raises(AuthError):
        await verify_token(token(attacker, "k1"), FakeJWKS(jwk).client(), issuer=ISSUER)


@pytest.mark.asyncio
async def test_a_tampered_payload_is_rejected(k1):
    private, jwk = k1
    header, _, signature = token(private).split(".")
    forged = jwt.utils.base64url_encode(b'{"sub":"%s","iss":"%s","aud":"authenticated","role":"authenticated","exp":9999999999}' % (str(uuid.uuid4()).encode(), ISSUER.encode())).decode()
    with pytest.raises(AuthError):
        await verify_token(f"{header}.{forged}.{signature}", FakeJWKS(jwk).client(), issuer=ISSUER)


@pytest.mark.asyncio
async def test_missing_or_unknown_kid_is_rejected(k1):
    private, jwk = k1
    keys = FakeJWKS(jwk).client()
    with pytest.raises(AuthError):
        await verify_token(token(private, None), keys, issuer=ISSUER)
    with pytest.raises(AuthError):
        await verify_token(token(private, "who-is-this"), keys, issuer=ISSUER)


@pytest.mark.asyncio
async def test_algorithm_downgrade_attempts_are_rejected_before_any_key_is_used(k1):
    _, jwk = k1
    fake = FakeJWKS(jwk)
    keys = fake.client()
    public_pem_as_secret = b"-----BEGIN PUBLIC KEY-----shared-secret-confusion"
    hs256 = jwt.encode({"sub": str(uuid.uuid4()), "iss": ISSUER, "aud": "authenticated", "role": "authenticated",
                        "exp": int(time.time()) + 600}, public_pem_as_secret * 3, algorithm="HS256", headers={"kid": "k1"})
    none = jwt.encode({"sub": str(uuid.uuid4()), "iss": ISSUER, "aud": "authenticated", "role": "authenticated",
                       "exp": int(time.time()) + 600}, None, algorithm="none", headers={"kid": "k1"})
    for bad in (hs256, none):
        with pytest.raises(AuthError, match="algorithm"):
            await verify_token(bad, keys, issuer=ISSUER)
    assert fake.requests == 0  # refused on the header alone; the key source was never even contacted


@pytest.mark.asyncio
@pytest.mark.parametrize("garbage", ["", "abc", "a.b.c", "....", "Bearer x", "e30.e30."])
async def test_garbage_is_rejected(k1, garbage):
    _, jwk = k1
    with pytest.raises(AuthError):
        await verify_token(garbage, FakeJWKS(jwk).client(), issuer=ISSUER)


@pytest.mark.asyncio
async def test_clock_leeway_is_small(k1):
    private, jwk = k1
    keys = FakeJWKS(jwk).client()
    await verify_token(token(private, exp=int(time.time()) - 5), keys, issuer=ISSUER)  # within leeway
    with pytest.raises(AuthError):
        await verify_token(token(private, exp=int(time.time()) - 60), keys, issuer=ISSUER)


# ---- the key cache ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_keys_are_fetched_once_and_cached(k1):
    private, jwk = k1
    fake = FakeJWKS(jwk)
    keys = fake.client()
    for _ in range(5):
        await verify_token(token(private), keys, issuer=ISSUER)
    assert fake.requests == 1


@pytest.mark.asyncio
async def test_a_rotated_in_key_is_picked_up_after_the_throttle(k1):
    (p1, j1), (p2, j2) = k1, make_key("k2")
    clock = Clock()
    fake = FakeJWKS(j1)
    keys = fake.client(clock=clock, min_refetch=30)
    await verify_token(token(p1, "k1"), keys, issuer=ISSUER)
    fake.keys.append(j2)
    clock.t += 31
    assert (await verify_token(token(p2, "k2"), keys, issuer=ISSUER))["role"] == "authenticated"
    assert fake.requests == 2


@pytest.mark.asyncio
async def test_bogus_kids_cannot_make_the_service_hammer_the_key_endpoint(k1):
    private, jwk = k1
    clock = Clock()
    fake = FakeJWKS(jwk)
    keys = fake.client(clock=clock, min_refetch=30)
    await verify_token(token(private), keys, issuer=ISSUER)
    for i in range(50):
        with pytest.raises(AuthError):
            await verify_token(token(private, f"bogus-{i}"), keys, issuer=ISSUER)
    assert fake.requests == 1  # the 50 unknown kids caused no refetch inside the throttle window
    clock.t += 31
    with pytest.raises(AuthError):
        await verify_token(token(private, "bogus-again"), keys, issuer=ISSUER)
    assert fake.requests == 2


@pytest.mark.asyncio
async def test_a_cached_key_survives_a_short_endpoint_outage_but_not_a_long_one(k1):
    private, jwk = k1
    clock = Clock()
    fake = FakeJWKS(jwk)
    keys = fake.client(clock=clock, ttl=600, min_refetch=1, max_stale=3600)
    await verify_token(token(private), keys, issuer=ISSUER)
    fake.down = True
    clock.t += 700  # past the ttl: a refresh is attempted, fails, and the cached key is still within grace
    assert (await verify_token(token(private), keys, issuer=ISSUER))["role"] == "authenticated"
    clock.t += 4000  # past max_stale: fail closed
    with pytest.raises(AuthUnavailable):
        await verify_token(token(private), keys, issuer=ISSUER)


@pytest.mark.asyncio
async def test_no_keys_ever_loaded_and_endpoint_down_is_unavailable_not_unauthorised(k1):
    private, jwk = k1
    fake = FakeJWKS(jwk)
    fake.down = True
    with pytest.raises(AuthUnavailable):
        await verify_token(token(private), fake.client(), issuer=ISSUER)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [b"not json", b"{}", b'{"keys": "nope"}', b'{"keys": []}', b'{"keys": [{"kty": "oct", "k": "AAAA", "kid": "k1"}]}'])
async def test_a_malformed_or_unusable_key_document_is_unavailable(k1, body):
    private, jwk = k1
    fake = FakeJWKS(jwk)
    fake.body_override = body
    with pytest.raises(AuthUnavailable):
        await verify_token(token(private), fake.client(), issuer=ISSUER)


@pytest.mark.asyncio
async def test_an_unusable_refresh_never_wipes_a_working_cache(k1):
    private, jwk = k1
    clock = Clock()
    fake = FakeJWKS(jwk)
    keys = fake.client(clock=clock, ttl=600, min_refetch=1, max_stale=3600)
    await verify_token(token(private), keys, issuer=ISSUER)
    fake.body_override = b'{"keys": []}'
    clock.t += 700
    assert (await verify_token(token(private), keys, issuer=ISSUER))["role"] == "authenticated"


@pytest.mark.asyncio
async def test_only_es256_p256_signing_keys_are_loaded(k1):
    private, jwk = k1
    other = {**jwk, "kid": "rsa-looking", "kty": "RSA"}
    wrong_alg = {**make_key("hs")[1], "alg": "HS256"}
    wrong_use = {**make_key("enc")[1], "use": "enc"}
    keys = FakeJWKS(other, wrong_alg, wrong_use, jwk).client()
    assert (await verify_token(token(private), keys, issuer=ISSUER))["role"] == "authenticated"
    for kid in ("rsa-looking", "hs", "enc"):
        with pytest.raises(AuthError):
            await verify_token(token(private, kid), keys, issuer=ISSUER)
