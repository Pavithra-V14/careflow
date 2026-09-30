# CareFlow — Patient Workflows

Every distinct way a patient (or their guardian) uses this system, end to end. Each workflow cites
the `BUILD-SPEC.md` sections it's built from — this document doesn't introduce anything new, it
walks the spec from the patient's seat rather than the system's.

Notation: **[auto]** happens without a human; **[human]** a staff member decides; **[locked]** can
never be automated without a human deciding first, in any autonomy mode; **[fail-safe]** is the
opposite — always automatic, never waits for a human first, because the cost of delay exceeds the
cost of occasionally acting on a false alarm; **§X** points at `BUILD-SPEC.md`.

---

## A. Getting in

### A1. Self-registration (patient arrives with no prior contact)
1. Lands on the public page → hospital name, an "in an emergency call 108" banner, about/departments,
   features, privacy promise, FAQ (§19.2).
2. Enters mobile number → OTP → verified **[auto]** (§18.1).
3. Fills required fields: full name, date of birth, sex, preferred language; optionally email
   (§19.4).
4. **Fetches the current privacy notice from the server and shows it** — the client never
   hard-codes a version, because consent is recorded against exactly what was shown (§20.1, and
   this was a defect found and fixed earlier: a hard-coded version once rejected every
   registration).
5. Ticks consent to care delivery (required); may separately tick AI-assisted processing and
   service-improvement-on-de-identified-data (both optional, independently withdrawable later).
6. Account created in state `registered` → immediately re-evaluated to `new_patient` once the MPI
   resolves identity (§17.2, §17.5). **No referral exists yet, so this is a brand-new record.**
7. Lands on the portal home: can book a first visit; cannot yet message the clinic about anything
   clinical (§1, §17.5).

### A2. Registering after a referral already exists (the common path)
A doctor elsewhere faxed/uploaded a referral before this patient ever opened the app (§10.4, §17.2
— the Front Door agent already resolved an MPI record from the letter).
1. Same signup screen as A1.
2. **Claiming step:** the system checks whether the phone/email **and** the date of birth match an
   existing referral-created record. Both must match — a shared or guessed detail alone is not
   enough (§17.2). If they match, this account is linked to that record, not a new one.
3. If they don't match cleanly, the account is created but flagged; front desk resolves it by hand
   rather than the system guessing (§17.2, §11.2's "unverified is never treated as true" principle
   applied to identity).
4. State is `new_patient`. The patient can already see the appointment offer the Scheduling agent
   sent before they even signed up (topic `appointments` is visible to `new_patient` accounts,
   §17.5) — this is why the claiming step matters: it's what lets a stranger's referral become
   *their* inbox on first login.

### A3. Guardian / dependent account
1. A guardian registers their own account normally (A1).
2. Adds a dependent through the "family-member switcher" (§19.3). Under-18 dependents require
   guardian consent and a recorded proxy-booking trail (§20.1 — flagged in the spec as needing
   counsel's confirmation on specifics, not settled law).
3. All subsequent workflows below (booking, messaging, intake) can be performed **by the guardian,
   on the dependent's behalf**, with the action attributed to the guardian in the audit log.

### A4. Returning patient, signing in
Phone/email OTP → straight to portal home, no re-registration. If they arrive on a new device, a
session/device entry is added, visible and revocable from Security (§18.1, §19.3).

---

## B. Getting an appointment

### B1. Booking directly (no referral, self-initiated)
1. From the portal, browse available visit types and providers.
2. Slot search runs against real availability (exclusion-constrained, so nothing shown can
   double-book, §17.1).
3. Selects a slot → it's **held** exclusively for them (not yet booked) **[auto]**.
4. Confirms → booking is created idempotently (a double-tap or a network retry produces the same
   booking, not two, §7.2's idempotency-key rule) **[auto]** — this is a Scheduling-agent-adjacent
   action but doesn't need the agent at all for a direct, non-triaged booking.

### B2. Accepting a triaged referral offer
This is the flow that follows A2, and it's where the Front Door and Scheduling agents actually run.
1. Patient sees a message in the `appointments` topic: *"We can offer: 1. Thu 9:30 with Dr. X. 2. Fri
   2:00 with Dr. Y. Reply with the option number."*
2. Patient replies in free text — "the second one please," "2," "can I get Wednesday instead" —
   whatever they actually write.
3. **A `decision`-engine call classifies the reply** (accept option N / decline / unclear) — an
   unclear reply is **re-asked, never guessed** (§5.2's Scheduling reply-interpretation pattern; a
   defect found earlier — "ok" to two options used to silently book option 1).
4. On a clear accept, the chosen slot is booked and the other hold released **[auto]**.
5. Patient gets a confirmation. Booking now visible under "my appointments."

### B3. Cancelling
1. Patient cancels a **future** appointment from the portal — past or in-progress appointments
   cannot be cancelled by the patient (server-enforced, not just hidden in the UI, the same
   enforced-server-side-per-feature discipline as account-state gating, §17.5; this closes a defect
   found earlier where a patient could cancel something that had already started).
2. Slot is freed **[auto]** → triggers workflow B4 for someone else.

### B4. Being offered a freed slot (backfill)
1. Someone else's cancellation (B3) frees a slot that matches this patient's waitlist entry.
2. The Scheduling agent ranks **every** eligible waitlist candidate by clinical priority — read from
   their matched card's rules and any recorded results — **not by who's waited longest** (§1, §22
   references this as the mechanism Visit Readiness's barrier-driven rebooking also reuses).
3. The highest-priority candidate is offered by message, or read aloud through the in-app voice
   channel (§23) if they've turned it on in their profile.

### B5. Rescheduling because of a no-show barrier
Continuation of the Visit Readiness workflow at E4 below — a patient who can't make an existing
appointment for a scheduling-conflict reason is routed straight back into this Scheduling flow
(§22.4), not handled as a separate cancellation-then-rebooking the patient has to do manually.

---

## C. Safety — the workflow that overrides every other one

### C1. Something the patient writes triggers a red flag
This can happen **inside any conversation** — a booking reason, a reply to an offer, an intake
answer, a free message, or a spoken word on a call. It is not a separate screen; it's a check that
runs on everything.
1. Patient writes (or says) something like "I have crushing chest pain since this morning."
2. **The deterministic phrase/value rules check this before any model is consulted, with no
   exception for which workflow the text arrived in** (§5.2 — the one rule referenced from more
   sections of the spec than any other).
3. If it fires: a nurse task is created **immediately**, due at once **[fail-safe]** — the
   opposite of [locked]: this happens before any human decides, not after.
4. **The patient is sent the emergency template — automatically, in every autonomy mode** — "please
   seek urgent care now... call 108" (§9.1, §12.1's one automatic-send exception). This is not
   waiting on a human first, because the harm of delaying it exceeds the harm of an occasional false
   trigger.
5. A nurse acknowledges the escalation afterward — the send already happened; the acknowledgment is
   about clinical follow-up, not gating the message (§9.1).
6. Whatever workflow the patient was originally in (booking, messaging, intake) continues
   separately and normally — the safety response doesn't silently cancel their appointment or their
   conversation.

### C2. A red flag during an in-app voice session
Same rule, spoken, in real time against the live transcript. If it fires, the patient hears the safety message read from the same pre-approved, pre-translated template (never composed live), and the same nurse-acknowledgment task is raised as C1 (§11.6). This has its own metric (`voice_safety_escalation_rate`) precisely because a missed voice escalation leaves no text trail the way a missed message would — and the spec is explicit that this channel's guardrail is a scope-restriction plus a parallel monitor, not an identical copy of the text-channel gate (§11.6's honestly-stated latency gap).

### C3. A near-miss the rules didn't catch, but the model flags
If the deterministic rules pass something clean but the `decision`-engine safety net independently
flags danger, it can **only escalate**, never override the rules in the other direction (§5.2). The
patient experiences this identically to C1 — they can't tell which layer caught it, and don't need
to.

---

## D. Talking to the clinic

### D1. Messaging before activation (new/pending patient)
A patient in `new_patient` or `pending_activation` state can read and reply in the `appointments`
and `safety` topics **only** — the `care` topic (general clinical conversation with the team) is
closed until their first visit (§17.5). If they try to write into a `care`-topic thread they don't
have access to, or someone else's thread entirely, the request is rejected — **and a rejected
attempt looks identical to a thread that doesn't exist**, so a guess can't be used to probe for
other patients' threads (§17.5, closing a defect found earlier where one patient could post into
another's thread and effectively hijack their scheduling conversation).

### D2. Messaging after activation
Once `active`, the `care` topic opens: general non-emergency messages to the clinic. **The agent
never answers a clinical question itself, with or without a disclaimer** (§11.4) — a message
classified as a clinical question, a refill request, or a results question gets an automatic,
templated acknowledgment **[auto]**, and a human actually answers it **[human]**. The patient's
experience is: instant "we've got your message," then a real reply later from a person.

### D3. Receiving a reminder for something overdue
Generated from a recurrence rule (annual review) or an open care-episode step (§9.3), surfaced
first on the Care Gaps panel internally (§24.1) and sent to the patient as a message once it's
past due — same channel rules as any other appointment/safety message.

---

## E. Before the visit (Visit Readiness agent, §22 — off by default behind a feature flag)

### E1. Reading the pre-visit brief
**The patient never sees this.** It's generated for the clinician only, from the patient's own
chart data (§22.1). Listed here only so the boundary is clear: nothing in this step is patient-
facing, and nothing about it is ever sent to the patient by mistake.

### E2. Answering intake questions
1. Roughly 48 hours before a visit whose matched protocol card declares intake questions, the
   patient receives them one at a time, in the `readiness` topic — each is the card's own
   clinician-written question text, sent as a template, not agent-composed prose (§22.2).
2. Patient answers in free text.
3. **Every reply passes through the same red-flag check as any other message first** (§C above,
   restated inside §22.2 specifically because "it's just a scripted intake" is exactly the kind of
   reasoning that would otherwise excuse skipping it).
4. If the reply actually answers the question, it's extracted, checked against the source text, and
   recorded as a fact the visit's protocol can use.
5. If the reply is a clinical question instead ("does this affect my diabetes?"), it's routed to a
   human exactly as D2 — the intake agent doesn't answer it either.
6. Anything left unanswered by visit time becomes a front-desk task at check-in — never silently
   dropped.

### E3. Confirming procedure readiness
1. For a procedure-type visit, the patient receives a checklist ("fasted 8 hours," "held your blood
   thinner as instructed," "arranged a driver") — clinician-authored per visit type (§22.3).
2. Each item's answer is classified `confirmed` / `not-done` / `unclear`.
3. **All confirmed** → visit proceeds, logged.
4. **Any not-done or unclear** → automatic nurse escalation, in every mode, no exception **[locked]**
   (§22.3, §12.1) — a nurse is notified just as reliably as they would be for a red flag, even
   though the two are different kinds of rule: this one waits for a human decision before the
   visit proceeds ([locked]); a red flag doesn't wait at all ([fail-safe]). What they share is
   only the outcome that a nurse is never left unaware — not the mechanism.

### E4. Reporting a barrier / can't make it
1. Patient replies to an intake or readiness message with something other than a clean confirmation
   — "I have no way to get there," "can we do Thursday instead," silence past the deadline.
2. Classified into a closed set: transport / cost / childcare / forgot / scheduling conflict / other
   (§22.4).
3. **Scheduling conflict** → handed straight into the B2/B4 booking-reply flow — the patient
   experiences one continuous conversation, not "cancel here, then go rebook over there."
4. **Transport, cost, childcare, other** → routed to front desk; the agent identifies the barrier,
   a person solves it. The barrier type is recorded so the clinic can see, over time, whether most
   no-shows on a given day are transport-related, say (§22.4).

---

## F. The visit itself and after (Care Journey agent, §1 item 3 — off by default behind a flag)

The patient's role during and immediately after the visit is thin by design — this is the doctor
confirming a plan, not the patient managing one — but two things reach the patient directly:

### F1. A follow-up gets booked from the care plan
The doctor's free-text plan ("echo in 2 weeks...") becomes a task graph the doctor confirms; a
bookable task (the echo) generates a booking request the patient experiences exactly as **B2** — an
offer arrives, they reply, they're booked. A non-bookable task (a medication change) is just
recorded; the patient doesn't see a booking for it because there isn't one.

### F2. The loop closes, sometimes into a new referral
If a result later meets the plan's condition ("if EF < 40%, refer to HF clinic"), the resulting
referral request re-enters the system at roughly **A2/B2** again — from the patient's side, it looks
like receiving a new referral and a new appointment offer, even though internally it's the same
Care Journey thread closing out (§9.5's provenance tracking is what makes this traceable back to
the original visit if anyone ever needs to ask "why was I referred here?").

**A report that doesn't contain the needed value never silently closes this loop as "not met" and
skips the referral** — a doctor reads it and decides instead (§9.5, §14.3's hard gate on exactly
this failure mode). The patient isn't shown this internal step; what they'd notice, if it went
wrong, is a referral that should have happened not happening — which is precisely the failure this
gate exists to prevent.

### F3. A value gets corrected after the fact
If a clinician corrects a result that already fed a decision the patient acted on (e.g. they already
attended a referred visit based on it), the patient isn't automatically told anything — **the
correction reopens a review task for the clinician who made the original decision** (§9.5), and
whatever the clinician decides to communicate is a human judgment call, not an automated message.

---

## G. Any channel, same rules underneath

### G1. Using voice instead of typing (§23, opt-in per patient, off by default; scope is deliberately narrow)
A patient who has turned on `voice_enabled` in their profile sees a mic button inside the portal — no phone number involved, since this rides on their already-signed-in session. They can **speak a reply** to a booking or backfill offer (B1/B2/B4) or **answer a readiness/intake question aloud** (E2/E3) instead of typing it. Anything outside that scope — a new referral, an open-ended clinical conversation — is never handled by voice at all: the session's own instructions never permit it in the first place (§23.2), rather than the session starting and then being redirected mid-way. The safety rule (C2) applies identically regardless of which of these tasks the session is for.

### G2. Using Tamil
Any workflow above can run in Tamil. Two different mechanisms depending on the workflow, and this
distinction matters more than it looks: **red-flag detection on Tamil text uses natively-authored
Tamil phrase rules**, not a translation pipeline (§5.2's Laya multilingual-checkpoint routing) — so
a translation-service outage degrades comprehension of routine messages, never safety screening.
Ordinary conversational text is translated for staff review with the original always retained
alongside it, never replacing it.

---

## H. Managing the account itself

### H1. Viewing and controlling privacy
From the profile menu (§19.3): withdraw any of the three independent consents; download my data;
request erasure — both routed through the grievance channel with tracking (§20.1). Withdrawing
consent to AI-assisted processing doesn't lock the patient out of the system; it changes what the
*system* is allowed to do with their data, per whichever consent was withdrawn.

### H2. Raising a grievance
Any patient can reach the grievance contact from the footer of the public page or the privacy menu,
without needing to be signed in — DPDP requires this channel to exist and function, not just be
listed (§20.1).

### H3. Managing sessions and devices
From Security in the profile menu: see every signed-in device, revoke any of them. A new sign-in
from an unrecognized device adds a new entry the patient can immediately see and act on (§18.1).

---

## What a patient can never do, in this system, regardless of anything above

**Three things never happen without a human deciding first, in any autonomy mode [locked]** (§1,
§12.1): a patient cannot trigger a merge of their own or anyone else's record; no free-text message
composed by the system on the patient's behalf is ever sent without a human approving it first; and
a procedure-readiness checklist answer that isn't a clean confirmation always reaches a nurse rather
than ever auto-resolving to "proceed."

**One thing runs the opposite way, and is a different kind of rule entirely, not a fourth item on
the list above [fail-safe]:** a red flag detected on any channel, including an in-app voice
session, always triggers the safety response and a nurse acknowledgment automatically — it is the
one case in this whole document where the system acts before a human decides, because the cost of
waiting exceeds the cost of an occasional false alarm. An earlier version of this document listed
this alongside the three "locked" items above as if it were a fourth example of the same rule. It
isn't, and `BUILD-SPEC.md` §1 and §12.1 were corrected to say so explicitly after the same
conflation was found there — this section is corrected here for the same reason.

The only text that reaches a patient automatically is either a clinician-authored template (an
intake question, a readiness checklist item, a slot offer) or the fixed emergency-safety message.
Nothing in between is ever auto-composed and auto-sent.
