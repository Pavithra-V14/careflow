# Runbook: SMS and DLT registration (India)

BUILD-SPEC.md 18.2; `careflow/sms.py`; `config/sms-templates.yaml`. **Status: the process is documented; NOTHING HAS BEEN REGISTERED.** It takes weeks, is done by the hospital as the registered principal entity, and costs money, so it is the owner's to start. **Start it now**, in parallel with everything else; the Phase 10 plan says so.

**What the code does meanwhile:** `TemplatedSms` sends nothing to a provider. In the demo profile everything goes to a sandbox sink. In the clinical profile only a template whose `dlt_status` is `registered` can be sent, because an unregistered message is **silently blocked by the carrier** with no error. No provider adapter exists (a new external service: ask first).

**Steps (the hospital's, in this order):**
1. **Entity registration** on a telecom operator's DLT portal. The spec's figures are about 5,000 to 5,900 rupees and 2 to 7 business days; confirm the current figures when you do it.
2. **Sender header (brand) registration.**
3. **Per-template registration**, one per language: Tamil and English are separate. A template must include the brand name and uses at most 5 variables (the spec says 5 to 6; the code uses 5). Allowed variable kinds are `link`, `brand`, `slot_time`, `short_code`. There is **no** free-text kind, and no health detail may appear in any SMS.
4. **Record** the registered template's id, language, variable kinds and status in `config/sms-templates.yaml` (`pending`, then `registered`). The wording stays with the hospital; do not paste it into the repository.
5. **Choose a provider** (the spec names an India-first provider and a global one) and check its Supabase Auth hook path. This is an external service: the owner approves it before any adapter is written.

**Required settings before any real send (no defaults):** the per-phone limit and window, the daily spend cap and the per-message price. Numbers are limited to +91 by default. Unset means the send is refused, never allowed.

**Watch after launch:** a falling delivery rate (a silently blocked template shows up there, not as an error) needs an alert when a provider exists.
