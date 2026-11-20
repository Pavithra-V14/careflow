# External-service decisions: what is open, what each would cost you, and the line to write to approve one

**Nothing was added.** This page is a decision aid. No service, account, key, dependency, SDK or adapter was added or ordered because of it, and none will be until you write the approval line for that service to the backend window (`CLAUDE.md` rule 13). Nothing here is a legal conclusion and nothing here is a quote.

**How to read it.**
- "What the code does without it" is what the product does today, so you can see that nothing is broken by waiting.
- "Verify before relying" marks a point taken from the specification or general knowledge that no one here has confirmed against the current rules or a vendor's current terms. Check current terms with the operator, the vendor or counsel before you rely on it.
- Costs are given as a *type* of cost (a registration fee, a monthly plan, per-message billing, your own machine). No price is stated, because none was checked.
- A "data-protection consequence" is a plain description of what would leave our systems. Whether that is allowed is for counsel and the privacy owner, not for this page.
- The two operating profiles are in `BUILD-SPEC.md` section 2: `demo` (synthetic data only) and `clinical` (real patients). Every external call must be listed in `data-flow-register.yaml` with its data class, and the `clinical` profile refuses any call not approved for patient data.

**The safest overall order** (my recommendation, yours to change): decide hosting and the region first (it fixes where everything else lives and cannot be undone cheaply), start the SMS registration at once (it has the longest waiting time and costs nothing to engineering), pick the scanner (small, local, no patient data leaves), and leave voice, hosted tracing, e-mail and any hosted model route until the first pilot has run on the rest.

The six sections follow the six open items in `docs/handover.md` section 7, plus the clinical model route at the end.

---

## 1. SMS and patient sign-in codes

- **What it unlocks.** Real patients signing in to the portal with a phone code (`docs/user-guide-patient.md`), and appointment nudges by SMS. Checklist rows 29, 55 and 96. Staff never use SMS (their second factor is an authenticator app).
- **What the code does without it.** Nothing is sent. In the demo, sign-in for walks uses an e-mail and password instead (`CAREFLOW_DEMO_EMAIL_SIGNIN`; demo and synthetic data only; the application refuses to start with it on anywhere else). In `clinical` only a template whose status is `registered` can be sent, and a send guard checks the +91 allowlist, a per-phone limit and a daily spend cap (`careflow/sms.py`, `config/sms-templates.yaml`). There is no provider adapter, so a real patient cannot sign in by phone today.
- **Options in plain words.**
  1. An India-first SMS provider (the spec names MSG91 as an example), connected to Supabase Auth through its SMS hook. Verify before relying: whether that connection path works for this project.
  2. A global provider (the spec names Twilio), which has its own separate India registration flow.
  3. Do not offer phone sign-in at first: the front desk gives a patient an e-mail-based or in-person route. This is *not built* for real patients today, so it would itself be a decision and a build.
- **Data-protection consequences.** The patient's phone number goes to the SMS provider and to the carrier every time a code or nudge is sent. The spec's rule is that no health detail ever appears in an SMS (booking and message nudges only), and the send guard refuses a free-text variable. You would need the provider's data-processing terms and counsel's view of them. Today the `clinical` profile sends nothing, because there is no provider and no registered template.
- **India-specific points (verify before relying).**
  - The spec says registration with the telecom regulator's DLT scheme is needed before any commercial SMS to Indian numbers, and that a message that does not match a registered template is silently blocked by the carrier, with no error.
  - The spec lists three registrations, done by the hospital as the registered principal entity: the entity, the sender header (brand name), and each message template (Tamil and English separately).
  - The spec says a template carries the brand name and a limited number of variables; the code allows five, with kinds `link`, `brand`, `slot_time`, `short_code`.
  - The spec records fee and timing figures. They are the spec's recollection, not checked: ask the operator for the current ones.
  - The registration steps are in `docs/runbooks/sms-dlt-registration.md`.
- **What you would have to do.** Start the entity, header and template registration as the hospital (weeks of lead time; nobody else can do it for you). Choose a provider. Write the approval line. Then engineering builds one adapter, adds the data-flow register entry, and you set the per-phone limit, daily spend cap and price (no defaults; unset means refused).
- **What it costs to ask for.** A registration fee and waiting time from the telecom side; the provider's per-message billing and possibly a monthly plan; hospital staff time. A silently blocked template shows up only as a falling delivery rate, so someone must watch that number after launch.
- **Safest default.** Start the registration now, in parallel, because it costs engineering nothing and has the longest wait. Approve a provider only once a registered template exists. Until then keep real patient phone sign-in off and pilot with front-desk-assisted sign-up.
- **Approval line to write to the backend window** (edit the provider name): `I approve adding an SMS provider for patient sign-in codes and appointment nudges: <provider name>. Build one adapter behind the existing SMS port, add its data-flow register entry as not approved for patient data until the privacy owner confirms in writing, and keep every real send blocked until a registered template exists.`

## 2. E-mail

- **What it unlocks.** E-mail as a second channel for overdue-task nudges to staff (an escalation past its deadline), as a fallback for patient notifications, and possibly Supabase Auth e-mails. Spec sections 13.4 and 18.3; the spec says alerts must not depend only on a third-party chat tool.
- **What the code does without it.** Alerts and escalations are in-app (a pinned task, a banner, the watchdog). Only a port and a fake exist for e-mail; nothing is sent. A failed delivery on any channel is meant to raise an admin banner, so a dead channel is not silent.
- **Options in plain words.** (a) A transactional e-mail provider chosen by you (the spec names SendGrid and says it has no permanent free tier; free tiers of others change often: check current terms). (b) The e-mail sending that Supabase Auth already provides for sign-in messages, if that is all you need (verify before relying on its limits for anything beyond sign-in). (c) None at first: in-app alerts only.
- **Data-protection consequences.** The spec requires e-mail content to be class, count, an opaque task id and a sign-in link only, never a name, diagnosis or value. The provider would still see the recipient's e-mail address (staff and patients) and the timing of alerts. You would need its data-processing terms. A sending domain and its mail authentication records would be needed so mail is not marked as spam (verify with the provider).
- **What you would have to do.** Decide whether staff e-mail nudges are wanted at all in the pilot; own a sending domain; approve a provider.
- **What it costs to ask for.** A monthly or per-message plan, a domain, and the time to keep the sender reputation healthy.
- **Safest default.** None for the first pilot. In-app escalation already works and is the primary channel by design; add e-mail when a real night-time overdue case shows a need.
- **Approval line:** `I approve adding an e-mail provider for staff escalation nudges only: <provider name>. Content is class, count, opaque task id and sign-in link only. Build one adapter behind the existing e-mail port and add its data-flow register entry.`

## 3. Voice engine

- **What it unlocks.** The spoken channel for a patient (a mic button), behind `FEATURE_VOICE_CHANNEL`, the patient's own `voice_enabled` preference, a consent recorded before any audio, and a rule set that can assess. Checklist rows 34 and 52; `docs/design/phase8-voice.md`; spec section 23.
- **What the code does without it.** No audio, no WebSocket and no engine call exists. The mechanism is built and tested against a scripted fake: consent first, the red-flag rules run on every transcribed chunk before anything else, a fixed pre-approved safety template is forced on a match (never composed), a narrow scope, and a session that ends cleanly if the engine fails. With no engine the mic button does not appear and the patient continues in text; every other channel is unaffected.
- **Options in plain words.** (a) The speech-to-speech engine the spec names (Gemini Live), which needs a data agreement before real patient speech. (b) A different engine that you would name. (c) Never, or later: text only. A real engine is also needed to *measure* the interrupt latency the spec asks for, which has not been measured.
- **Data-protection consequences.** The patient's voice and its transcript would go to the engine's operator for the whole session. That is the most sensitive new flow on this page. It would need a written agreement approved for patient data, the privacy owner's confirmation, the consent notice wording from counsel, and a decision on retention (`CAREFLOW_VOICE_TRANSCRIPT_RETENTION_DAYS` has no default). Today the `clinical` profile refuses it (the capability table in spec section 2 lists voice as off).
- **India-specific points (verify before relying).** Whether the engine's processing can be kept in India, and which languages it handles well enough for Tamil safety wording, are not known here. The fixed safety response must be pre-approved and pre-translated by the clinician and a Tamil reader (their content, not engineering's).
- **What you would have to do.** Get the clinician's governed texts (safety response, decline sentence, handoff wording), counsel's voice consent notice, and the agreement; then approve.
- **What it costs to ask for.** Per-minute or per-token billing for the engine, a spend cap per call (the per-call cap table already exists), clinician and counsel time, and a measured test of the live safety path before anything ships.
- **Safest default.** Not in the first pilot. It is the highest-urgency channel (`CLAUDE.md` rule 11), so it should come last.
- **Approval line:** `I approve building the real voice engine adapter for the synthetic demo only, using <engine name>. It stays refused in the clinical profile until a separate written approval names the agreement and the privacy owner has confirmed the data-flow entry.`

## 4. Hosting and deployment

- **What it unlocks.** A real deployment: the backend, the worker, and everything the drills need. Checklist rows 27, 28, 54, 75 and 18. Until it exists the rollback and restore drills are not evidence (a drill on a laptop does not count) and the production-readiness go/no-go stays NO-GO.
- **What the code does without it.** It runs locally against a hosted dev database. In dev the console is slow only because the backend (in India) is far from the hosted dev project, which sits in Tokyo (about 330 ms per round trip, `docs/design/dev-latency.md`). `/readyz` reports the `clinical` profile not ready while required routes or the scanner are missing.
- **Options in plain words.** (a) A cloud provider region in India for the backend and the worker, with the database project in the same region. (b) Machines you operate yourself for the workers (the spec splits topology: local models and document processing run on real machines, not containers, and there is no Docker anywhere in this project). (c) Keep the demo on your own machine and decide later. Also needed: a secret manager and backups with point-in-time recovery.
- **Data-protection consequences.** Everything would live with the host: encrypted patient data, audit rows, backups. The spec requires the database in an India region. The region cannot be changed cheaply later, and the database project region also decides latency. Verify before relying: whether your chosen database host offers an India region on the plan you want, and what its data-processing terms and backup retention say. Storage region for uploaded documents is a separate decision (checklist row 18), and there is a known Tokyo-versus-India question for it.
- **What you would have to do.** Pick the region and host; decide who operates the worker machines; plan the first restore and rollback drills on it (`docs/runbooks/restore-drill.md`, `rollback-drill.md`), and have the production secret manager hold the keys. Keep the owner and migrator credentials out of day-to-day use (row 82).
- **What it costs to ask for.** Recurring hosting and database fees (compute, storage, backup), a secret manager, your time to operate it, and on-call responsibility. Prices vary by host and plan: check current terms.
- **Safest default.** Decide this first. Put the backend in the same region as the database, in India, on a host with a written data-processing agreement; start with the smallest setup that lets you run the drills and record their times.
- **Approval line:** `I approve <host> in <region> as the production host for the backend and the database, with <secret manager>. Engineering may prepare deployment files and the drill evidence path, but nothing is deployed and no real data is loaded until I write a separate go-live line.`

## 5. Observability (traces, dashboards, alert delivery)

- **What it unlocks.** Hosted traces (Langfuse), dashboards (Grafana) and chat or e-mail alert delivery, beyond the in-app watchdog. Spec section 13; checklist row 26; the cost canvas needs about a week of real traffic to mean anything.
- **What the code does without it.** The in-app alerts and the watchdog are real. The metrics catalogue (`metrics-catalog.yaml`) and the live/test dashboard are generated and work against the application's own data. The hosted exporters are fakes; nothing leaves.
- **Options in plain words.** (a) The hosted tools the spec names, with metadata only (opaque ids, engine, model, token counts, cost, latency, status). (b) The same tools self-hosted on machines you operate. (c) None at first, the in-app view only.
- **Data-protection consequences.** The spec's rule: for patient data the hosted tool receives *metadata only*; prompt and response bodies stay in the database. Even metadata can identify timing and volume of activity, so the privacy owner should confirm the field list. Chat and e-mail content is class, count, opaque task id and sign-in link only. In `clinical`, any call not approved for patient data is refused.
- **What you would have to do.** Create accounts, approve the field list, and decide who watches alerts and when. The alert is only as good as the person who receives it.
- **What it costs to ask for.** Free or paid plans of the hosted tools (check current terms; free tiers change), your time, and an on-call habit.
- **Safest default.** In-app alerts only for the first pilot. Add hosted traces once there is a week of real traffic to look at and the privacy owner has confirmed the metadata field list.
- **Approval line:** `I approve adding <tool names> for traces and dashboards, metadata only, never prompt or response bodies. Build the exporters behind the existing ports with a bounded queue so tracing can never block the app, and add the data-flow register entries.`

## 6. Malware scanner for uploads

- **What it unlocks.** Real document uploads (a referral letter a person uploads) outside the demo. Checklist rows 2 and 18; `docs/design/malware-scanner-options.md`.
- **What the code does without it.** Uploads work only in the demo profile. The scanner port exists with a fake; only an explicit `clean` verdict lets a document be read, and anything else (scanner down, too large, unreadable) holds the document for a person. `/readyz` reports `clinical` not ready until the scanner has answered a test file.
- **Options in plain words.** Five are compared in `docs/design/malware-scanner-options.md`; the useful ones are: (1) ClamAV, an open-source scanner on a worker machine you operate (nothing leaves, no vendor); (4) a commercial scanner you host (stronger detection, a licence); (2) a hosted scanning API (the document's contents go to a third party). A public multi-user scanning site is rejected for any patient document because uploaded files become visible to others.
- **Data-protection consequences.** Option 1 and 4: none leave, apart from signature updates that carry no patient data. Option 2: every document's contents go to a third party, which would need a data-processing agreement and is not allowed in `clinical` without a new written decision.
- **What you would have to do.** Decide where the worker machine lives (it depends on the hosting decision above), who owns the daily signature update, and approve the option.
- **What it costs to ask for.** Option 1: the machine's memory and disk (about a gigabyte or more of RAM is the options note's general-knowledge estimate; verify) and someone's attention to keep signatures current. Option 4: a licence. Option 2: per-call or monthly billing.
- **Safest default.** Option 1 (ClamAV, self-operated), recorded as approved in direction on 2026-10-30 with nothing installed until the machine is decided. This is the only item here that already has a recorded direction.
- **Approval line:** `I approve building the ClamAV scanner adapter. The worker machine is <where>, and <who> owns the signature update. Nothing hosted, nothing in a container.`

## 7. A hosted model route for the clinical profile (embeddings, reading images, translation, decisions)

Is this a real question? **Yes**, because the specification states that a real clinical launch needs at least one language-model route and one vision route approved for patient data under an agreement (`BUILD-SPEC.md` section 2), and the build deliberately has none. The current hosted providers (Gemini, OpenRouter's Jev router, Sarvam) are approved by you for *synthetic demo use only*, every registry row is `synthetic_only`, and the spec forbids a hosted row ever being `phi_approved` without a new explicit decision.

- **What it unlocks.** In `clinical`: reading typed or photographed referrals, matching with a reranker, Tamil translation drafts, care-plan free-text parsing, pre-visit brief and adaptive intake, and building the card search index (the vector rebuild is demo-only; checklist rows 20, 57, 90 and 94; the sign-off sheet in `docs/design/phase10-pre-launch.md`).
- **What the code does without it.** Each of those degrades visibly and safely: front desk keys the facts in by hand, a photographed referral becomes a manual task with the image shown, matching uses fusion order, native Tamil red-flag rules still run on the original text, the doctor enters care-plan tasks by hand, the front desk runs intake from the card's question list. The deterministic red-flag rules and scheduling are unaffected.
- **Options in plain words.** For each capability, either (a) accept it off for a demo-only launch, signed by you (the sheet has a row for each), or (b) approve a route under an agreement approved for patient data, or (c) run a local model on a machine you operate (the spec runs some small models locally and says a CPU-only machine cannot usefully self-host a general model or a vision model: verify with your hardware).
- **Data-protection consequences.** For any hosted route, patient text or images would leave our systems. That needs a written data-processing agreement, the privacy owner's confirmation, a data-flow register entry marked approved for patient data, and counsel's view on consent wording. The red-flag rules must still run before any such call (`CLAUDE.md` rule 2), and a hosted model's opinion can only escalate, never lower a flagged case.
- **What you would have to do.** Per capability, choose off or on. For "on", name the vendor and send counsel and the privacy owner its terms.
- **What it costs to ask for.** Per-call or per-token billing against the spend caps you set, plus legal review time.
- **Safest default.** Sign every row of the sheet as "accepted off for a demo-only launch" for the first pilot. The pilot then runs on the deterministic rules and humans, which is also the safest thing to learn from.
- **Approval line (only after you have signed the sheet rows you want on):** `I approve evaluating <vendor> for <capability> for the clinical profile. Do not call it with real patient text until counsel and the privacy owner have approved the agreement in writing; build it behind the gateway with the red-flag rules first and the data-flow entry marked not yet approved.`

---

## The one-page summary

| Decision | Unlocks | Without it | Data leaves? | Safest default |
|---|---|---|---|---|
| SMS and sign-in codes | Real patient phone sign-in, nudges | Demo e-mail sign-in only; nothing sent | Phone numbers to provider and carrier | Start registration now; approve a provider later |
| E-mail | Staff escalation nudges, patient fallback | In-app alerts | Addresses and timing | Not for the first pilot |
| Voice engine | Spoken channel | Text only | Voice and transcript | Last; not in the pilot |
| Hosting and region | Real deployment, drills | Local run, demo | Everything lives there | Decide first, India region, same region as the database |
| Observability | Hosted traces and dashboards | In-app watchdog | Metadata | In-app only at first |
| Malware scanner | Real uploads | Uploads demo-only | None for ClamAV | ClamAV on an operated machine |
| Clinical model route | Reading referrals, search, drafts | Manual, visible degrade | Patient text or images | Accept off for the first pilot |

Nothing was added by this page. `docs/handover.md` section 7 holds the one-line version; this page holds the detail.
