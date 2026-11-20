# CareFlow patient portal: user guide

This guide describes what the patient portal screens do, as built on 20 November 2026. It describes only what is on the screens. It gives no medical advice and says nothing about your care: for that, speak to your hospital.

**How to read the labels in this guide**

- **Not tested live** means the screen has been tried only against the portal's own made-up test data (unit tests and browser tests), not against the real hospital system. Where a section says **Walked live**, it was tried against the real (demonstration) backend on 20 November 2026 with a made-up patient; anything without that line is **not tested live**. The summary table below says exactly which.
- **Tamil:** the portal can be shown in English or Tamil. The Tamil wording has been checked by a machine only. **No Tamil string has been reviewed by a person**, and the Tamil reader must still approve all of it before real use.
- Some wording shown on the screens (notices you agree to, for example) is written by the hospital and its lawyers, not by this software. This guide does not repeat it.

## Emergencies

Every page carries an emergency banner. The portal is not for emergencies: if you need urgent help, follow the banner's instructions and contact emergency services instead of using the portal.

## 1. Signing in

1. Open the sign-in page. Enter your **mobile number** with the plus sign and country code, digits only (for example +91 followed by your number). If the number is not in that shape the screen says "Enter the number with a plus sign and the country code, using digits only." and sends nothing.
2. Press **Send me a code**. The screen says "A code was sent if the number can receive one." (it does not say whether your number is known). If the code cannot be sent: "The code could not be sent. Check the number and try again in a moment."
3. Type the **6-digit code** and press **Continue**. If it fails: "That code did not work. Check it and try again, or ask for a new one." You can choose **Use a different number**.
4. The same steps sign you in or create your sign-in account.

**Demo sign-in (demonstrations only).** In a demonstration build a second form, "Demo sign-in", appears under a boxed banner: email and password. It is for demonstrations with made-up patients only and is **not how patients sign in**. It is never present in the real portal.

**Staying signed in.** If the page is not used for a while, a box asks "Are you still there?" with a countdown; the button "Stay signed in" keeps your session. If the time runs out you are signed out and what was on the screen is cleared. How long it waits is a setting the hospital's privacy owner chooses (a demonstration uses a labelled placeholder).

**What the browser keeps.** Only your sign-in session. No name, number, code or message is kept in the browser.

## 2. Creating your patient account

If you are signed in but have no patient account yet, the screen says so and offers **Create my account**. Creating it does not link you to any record. If it is refused: "This sign-in cannot be used for a patient account."

Your account has a stage, shown as **Where your account is**: Created, New patient, Waiting for the hospital to confirm you., or Active. As the system works them out: **Created** is an invited account that has not been activated yet (only the notice and the activation are shown); **New patient** is an activated account with no record linked yet (the whole portal is shown, including linking a record); **Waiting for the hospital to confirm you.** is the stage while your request to link a record is waiting for a person at the hospital (this wording was chosen on 20 November 2026; its Tamil is machine-checked only and flagged for the human reviewer); **Active** is an account with a record linked after a person at the hospital approved it. What each stage may do is enforced by the system, which answers with a fixed refusal for anything not open yet.

## 3. Notices and your decisions

**Notices and your decisions** shows each notice the hospital has published, its version and your decision: Granted, Withdrawn or No decision yet. You can **Grant** or **Withdraw**, and grant again later. If the notice changes while you are reading it, the page refreshes and asks you to read the new version.

## 4. Activating your account

You can **Activate my account** once you have granted the care-delivery notice. Before that: "The care-delivery notice has not been granted yet, so the account was not activated."

## 5. Linking your record

**Walked live:** from the stage "New patient", one claim for a made-up patient came back "Your record is linked." and the page then showed "Linked to a record: Yes" and the name on the record. The hospital's name is filled in from the hospital's public details; if those are not published the form asks for a hospital code instead. Once a claim is waiting for a person, the button is turned off and says there is no need to send it again.

**Link your record:** give the full name and date of birth the hospital has for you. This also needs a verified mobile number and an activated account. The hospital's front desk must have confirmed the record's identity first. The result is one of: your record is linked; your request was received and a person at the hospital will decide; too many attempts (try again later or contact the hospital); already linked; or grant the care-delivery notice first.

## 6. Your details

**Your details** shows the name on your record (read-only) and lets you set **Sex** and **Preferred language** from the hospital's list of choices. If the hospital has not set up a list yet, the screen says so. **Save** records only the fields you changed; **Clear** removes a saved value after a confirmation.

## 7. Appointments

*Reads walked live (empty); booking not tested live.* On the demonstration system the appointment list and the open-times list loaded without error, but **no open time was offered**, so no appointment was booked. When there are no open times the screen says "There are no open times at the moment." with a "Check again" button and a link back to the top of the page; that is different from "Booking online is not turned on at this hospital yet." **Appointments** lists your appointments and, if the hospital has turned online booking on, the open times it lets patients book (enter the hospital code to see them).

- **Book** books a time after a confirmation. Results: booked; already booked for you; "This time cannot be booked by this account"; "This time is no longer open, or you have reached the hospital's limit of future appointments."
- **Cancel appointment** frees the time and contacts nobody. "This appointment cannot be cancelled any more: it was already cancelled or it has started."
- If booking is not turned on: "Booking online is not turned on at this hospital yet."

## 8. What to prepare for a visit

*Not tested live.* Beside an appointment, **What to prepare** shows the hospital's own checklist for that kind of visit, exactly as the hospital sent it, in plain text. For each item you can **Mark done** or **Mark not done**; an item you have not marked says "Not marked yet". Messages: "Nothing to prepare for this visit."; "This visit was not found, or it has already started."; "Visit preparation is not turned on at this hospital yet."

## 9. Messages to the hospital

*Not tested live.* Choose what it is about (My care, Appointments, Safety), type your message and press **Send message** (after a confirmation). **Your conversations** lists your conversations; **Read** shows the messages in one of them (messages from you, from the hospital, or from the system). If messages are not turned on: "Messages are not turned on at this hospital yet." If a message is refused it was empty, too long, or your account is not active and linked.

**Do not use messages for an emergency.**

## 10. Asking the hospital about your record

*Not tested live.* **Ask the hospital about your record** lets you ask for your record to be **erased**, for a **detail to be corrected** (you choose only which detail: Name, Date of birth or Phone number; no new value is typed here), or for a **nominee** to be recorded. A request has no free text. **Your requests** shows each one's status: Open, Seen, Resolved or Declined. You also get a notice when it changes.

## 11. Family links

*Not tested live.* You can ask to be linked as the **guardian of a child** or the **carer of an adult** at a hospital (nothing is pre-chosen). A member of the hospital's front desk checks the request.

**A verified link does not give you access to the other person's record and does not let you act for them. It is only recorded.**

You can withdraw a request or a link yourself. If the section does not appear, family links are not turned on for you. If the hospital has not entered its rules: "Family links are not set up for this clinic: the clinic has not entered its rules." (a demonstration uses placeholder rules, not the real ones).

## 12. Notices about your requests

**Your notices** lists updates on your requests, on your messages to the hospital and appointment offers, with **Unread** count; **Mark as read** marks one.

## 13. Complaints

- **Send a complaint** (signed in, active and linked): choose a category from the hospital's list, write what you want to tell the hospital, **Send**. **Your earlier messages** shows each one's status (Open, Seen, Resolved).
- **Send a complaint without signing in:** on the sign-in page. It goes to the hospital without signing in and a person there reads it. If the hospital is not set up to receive it: "No hospital is set up to receive this right now. Nothing was sent."

## 14. Download my data

*Not tested live.* **Download my data** saves one file with everything the system holds about you that you may see. Each download is recorded. The screen then lists what the file holds (records, bookings, messages, consent decisions, messages you sent to the hospital) and your consent history. If a list was cut short, it says so.

## 15. Voice

*Not tested live; the audio connection is not set up.* **Voice** is optional and off until you turn it on. It lets you say a reply out loud instead of typing it, for three narrow purposes only: replying to an appointment offer, answering a question before a visit, or answering an item on a visit checklist. You must read the notice and agree before a session opens. In this build the screen **does not record or play sound**: opening a session only creates the session. Voice is never for an emergency.

## What has and has not been walked live (20 November 2026)

| Portal part | Status |
|---|---|
| Demo sign-in (email and password, demonstrations only) | walked live |
| Notice and consent (granting the care-delivery notice) | walked live |
| Activating the account | walked live |
| Linking a record (claim) | walked live: linked directly |
| Your details (profile), notices, requests lists, complaints categories, family section (loading), voice setting (loading) | walked live, read-only |
| Appointments list, open times, messages list | reads walked live (empty); **no open time was offered, so booking is not tested live**; opening a conversation and sending a message not tested live |
| What to prepare for a visit | not tested live (no appointment to open it from) |
| Sending a request, a complaint, a family-link request, withdrawing consent, downloading my data, a voice session | not tested live |
| Complaint without signing in | the category list loaded (eight categories, read-only); sending not tested live |
| Phone-code sign-in | not tested live (no approved SMS provider) |

## Fixed messages you may see

| Message | What it means |
|---|---|
| This is not available to you yet. | The screen needs your account to be active (or linked) first. |
| This cannot be done for this account: it must be active and linked to a record. | See sections 2, 4 and 5. |
| This is not available right now. Nothing was recorded / Nothing was sent. | The hospital has not turned this on, or the service is not reachable. Nothing was saved. |
| Check what you entered and try again. | A field was not accepted. |
| You were signed out because the page was not used for a while. | See "Staying signed in". |

These are fixed sentences; the portal never shows text sent back by the server.

## Where this guide stops

It describes the screens only. It gives no medical or legal content. Real use needs the hospital's own notices, the privacy owner's choices and a human review of all Tamil wording.
