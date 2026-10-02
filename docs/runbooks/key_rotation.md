# Runbook: manual demographic key rotation

BUILD-SPEC.md §17.2. Applies to the AES-256-GCM key (patient name, date of birth, phone in `mpi.patients`) and the
HMAC key (identity tokens in `mpi.identity_tokens`). Both rotate together as one **key_version**.

**Who:** the operator holding the platform secret manager and the `DATABASE_URL_MIGRATOR` credential.
**When:** on a schedule the clinic sets, after any suspected key exposure, and when someone with key access leaves.
**Safe to interrupt:** yes. Every step below can be stopped and re-run; progress is kept.

## Before you start

- [ ] Pick the next version number: `N+1`, where `N` is the highest `CAREFLOW_ENC_SECRET_V<n>` now in the platform manager.
- [ ] Take (or confirm) a database backup. Rotation rewrites ciphertexts in place.
- [ ] Do it in a quiet period. Reads and writes keep working during rotation (both versions are loaded), but a
      patient being rewritten holds a row lock for the length of one batch.
- [ ] Use the `dev` or `ci` project first, with synthetic data, and confirm `verify` is clean there before production.

## 1. Add the new key to the platform secret manager

Generate two independent values, once each. They must differ from each other and from every earlier key:

```
python -c "import base64, secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

Run it twice. In the hosting provider's secret manager add:

| Name | Value |
|---|---|
| `CAREFLOW_ENC_SECRET_V<N+1>` | first value |
| `CAREFLOW_HMAC_SECRET_V<N+1>` | second value |

**Leave `CAREFLOW_ENC_SECRET_V<N>` and `CAREFLOW_HMAC_SECRET_V<N>` in place.** Do not paste the values into chat, a ticket,
a commit or a shell history you do not control. Keep a copy in the secret manager's own backup: losing the encryption key
makes the demographics encrypted under it unrecoverable.

Redeploy or restart so the API and workers load both versions. The vault refuses to start if an `ENC` key has no `HMAC`
partner (or the reverse), so a half-added pair fails loudly rather than running degraded.

**New writes keep using version `N` until you change which version the ingestion pipeline is constructed with**
(`PatientIngestionPipeline(vault, key_version=...)`). Switch it to `N+1` in the same release as step 3, so that nothing
new is written under the old key while you re-encrypt the old rows. Until then, step 3 simply runs again at the end.

## 2. See where things stand

```
python scripts/rotate_keys.py status
```

Prints, per key_version, how many ciphertext columns and tokens reference it. Expect only `N` before you start.

## 3. Rotate

```
python scripts/rotate_keys.py rotate --from <N> --to <N+1>
```

Optional: `--clinic <uuid>` to do one clinic first, `--batch-size <1-1000>` (default 200).

What it does, per batch of patients (one clinic and origin at a time):

1. locks the batch, decrypts each demographic with the key version recorded beside it;
2. re-encrypts under `N+1` and checks the new ciphertext reads back identically;
3. computes the `N+1` identity tokens and writes them **next to** the `N` tokens (both coexist, so matching works
   under either version throughout);
4. records one row in `ops.key_rotation_batches` with that batch's idempotency key;
5. commits, then moves on.

It connects with `DATABASE_URL_MIGRATOR` (a rotation crosses every clinic, so it is a maintenance task, not a clinic user's
action) and prints counts only. It never prints a key or a demographic.

**If it stops** (network drop, your laptop sleeps, a batch fails): read the error, fix the cause, run the same command
again. Finished patients are skipped automatically; the failed batch was rolled back whole. A failure naming a patient id
means that record's ciphertext did not authenticate under the key recorded for it. **Do not force it.** Stop and
investigate: a wrong key loaded, a corrupted row, or tampering. Re-running with the cause unfixed will fail at the same row.

Refused before touching the database (exit 2): the target or source version has no key loaded, the two are equal, or the
batch size is out of range.

## 4. Verify

```
python scripts/rotate_keys.py verify --to <N+1>
python scripts/rotate_keys.py status
```

`verify` is read-only. For every patient with data on `N+1` it checks that every ciphertext authenticates under its
recorded version and that the `N+1` identity tokens exist and equal what the decrypted data derives. It must report
`0 unreadable, 0 token problems` (exit 0). Anything else: stop, do not retire the old key, investigate.

`status` must show **`0 ciphertext columns` for version `N`**. If any remain (a patient registered under `N` while you
rotated), run step 3 again, then verify again.

Also check by hand that the ledger matches: `select count(*), sum(patients_rotated) from ops.key_rotation_batches
where from_version = <N> and to_version = <N+1>;` — the sum equals the number of patients rotated (summed across
re-runs; a re-run that found nothing adds no rows).

Spot-check in the app: open a handful of patients as front desk (names and dates read correctly), and register a
test patient who already exists to confirm it is recognised as a match rather than a duplicate.

## 5. Retire the old key

Wait a full release cycle, and confirm for that period that `status` still shows `0 ciphertext columns` on `N` and that
no code path or job still constructs a vault pipeline with `key_version=N`.

1. Remove `CAREFLOW_ENC_SECRET_V<N>` and `CAREFLOW_HMAC_SECRET_V<N>` from the platform manager, **both together**
   (the vault refuses a lone half).
2. Redeploy. The API and workers must start cleanly with only `N+1` (and later) loaded.
3. Run `verify --to <N+1>` once more.
4. Keep the offline backup of the retired keys only as long as a database backup that still holds version `N`
   ciphertexts exists; destroy it with that backup. A backup older than the rotation cannot be read without them.

The `N` identity tokens stay in `mpi.identity_tokens`. Lookups filter on key_version, so they are inert once `N` is
retired, and the application role has no DELETE by design. Removing them is a separate, reviewed migration, not part of
this runbook.

## If something goes wrong

| Symptom | Meaning | Do |
|---|---|---|
| `refused: no key loaded for key_version …` | the new pair is not in this process's environment | add both secrets, retry; nothing was changed |
| `FAILED: patient <id>: DecryptionError` | that row does not open under its recorded key | stop; check the right `N` key is loaded; escalate if it is |
| `verify` reports token problems | a new-version token is missing or wrong | re-run `rotate` (it repairs tokens deterministically), verify again |
| need to undo | old key still present, old ciphertexts already overwritten | `rotate --from <N+1> --to <N>` re-encrypts back, provided both keys are still loaded |
| a key was lost before retirement | demographics under it are unrecoverable | restore from backup, then restart this runbook |

Rotation and its ledger touch no clinical content and call no external service.
