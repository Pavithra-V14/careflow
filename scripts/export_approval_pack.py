"""Make ONE single file per owner out of docs/approval-pack/ (and the drafts and sheet each owner is asked to read), so a person can open it, answer it and send it back.

    python scripts/export_approval_pack.py            # writes docs/approval-pack/export/<owner>.docx when pandoc is installed, otherwise <owner>.md

The CONTENTS of every source file are included UNCHANGED, in the order the pack's README gives; this script only adds a header at the top (who the file is for, what they must decide, how to answer, and the line that
AI drafts are proposals) and a sign-off block at the end. It writes no clinical or legal wording of its own. It installs nothing: if `pandoc` is not on the PATH it writes the combined .md files and says so.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACK = ROOT / "docs" / "approval-pack"
OUT = PACK / "export"
PROPOSAL_LINE = "AI drafts are proposals. Nothing in this file is approved, in force or loaded by the software until the named person signs it."
HOW = (
    "For each numbered item or question, tick one of the options or write your answer on the Answer line (or in the Answer column). Sign and date the block at the end and return this file to the person who sent it. "
    "An item you leave blank stays unanswered, and the software keeps its safe default for it."
)

OWNERS = {
    "priya-clinician": {
        "title": "Clinician pack",
        "for": "Priya, the clinician",
        "decide": "the clinician items in the sheet (including the knowledge-card lifecycle, item 38, with its question about versions, and the retrieval gold set, item 39) and the options prepared for items 23 to 34. Each item says what the software does until it is answered.",
        "parts": [PACK / "README-clinician.md", PACK / "clinician-options.md", ROOT / "docs" / "design" / "clinician-sheet.md"],
    },
    "counsel": {
        "title": "Counsel pack",
        "for": "Counsel (the clinic's legal adviser)",
        "decide": "the wording of the drafts listed in the cover note (consent notices, grievance and rights procedure, retention schedule, breach notification, patient-facing text) and the exact questions the cover note asks for each.",
        "parts": [
            PACK / "README-counsel.md", PACK / "counsel-cover-note.md", PACK / "counsel-patient-facing-text.md", ROOT / "docs" / "drafts" / "consent-notices-draft.md",
            ROOT / "docs" / "drafts" / "grievance-and-rights-procedure-draft.md", ROOT / "docs" / "drafts" / "retention-schedule-proposal-draft.md", ROOT / "docs" / "drafts" / "breach-notification-wording-draft.md",
        ],
    },
    "privacy-owner": {
        "title": "Privacy owner pack",
        "for": "The privacy owner",
        "decide": "whether each pre-filled statement about where data goes and how it is kept is correct, and the questions the pre-filled document marks as QUESTION.",
        "parts": [PACK / "README-privacy-owner.md", PACK / "privacy-owner-confirmation-prefilled.md"],
    },
    "tamil-reader": {
        "title": "Tamil review pack",
        "for": "The Tamil reader (a native speaker who reviews the Tamil screens)",
        "decide": "whether each Tamil string is acceptable, string by string; the notes table lists the strings the AI was least sure of and why. Nothing becomes reviewed until you say so, string by string.",
        "parts": [PACK / "tamil-review-README.md", PACK / "tamil-review.md", PACK / "tamil-review-notes.tsv"],
    },
    "clinic-owner": {
        "title": "Clinic owner pack",
        "for": "The clinic owner",
        "decide": "the proposed values, each with its reason: the break-glass reasons and limits, the grievance categories, the retention periods and the clinic time zone.",
        "parts": [PACK / "README-clinic-owner.md", PACK / "clinic-proposed-values.md"],
    },
}


def tsv_as_table(path: Path) -> str:
    """The notes file, mechanically shown as a table (one row per line, cells unchanged except that a pipe is escaped)."""
    rows = [line.split("\t") for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    width = max(len(r) for r in rows)
    esc = lambda c: c.replace("|", "\\|")
    out = ["| " + " | ".join(["String", "Group", "Note"][:width] + [""] * max(0, width - 3)) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(esc(c) for c in r + [""] * (width - len(r))) + " |" for r in rows]
    return "\n".join(out)


def build(name: str, spec: dict) -> str:
    head = [
        f"# {spec['title']}: one file to answer and send back",
        "",
        f"**Who this is for:** {spec['for']}.",
        "",
        f"**What you must decide:** {spec['decide']}",
        "",
        f"**How to answer:** {HOW}",
        "",
        f"> **{PROPOSAL_LINE}**",
        "",
        "---",
        "",
    ]
    body: list[str] = []
    for n, part in enumerate(spec["parts"], start=1):
        text = tsv_as_table(part) if part.suffix == ".tsv" else part.read_text(encoding="utf-8")
        body += [f"# Part {n}: {part.name}", "", text.rstrip("\n"), "", "---", ""]
    sign = [
        "# Your answer",
        "",
        f"{PROPOSAL_LINE}",
        "",
        "Items answered (numbers): ______________________________",
        "",
        "Items you want to discuss before answering: ______________________________",
        "",
        "Name: ______________________  Role: ______________________",
        "",
        "Date: ______________________  Signature: ______________________",
        "",
    ]
    return "\n".join(head + body + sign)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    pandoc = shutil.which("pandoc")
    if pandoc is None:
        print("pandoc is not installed: writing one combined .md per owner instead (nothing was installed)")
    for name, spec in OWNERS.items():
        missing = [p for p in spec["parts"] if not p.exists()]
        if missing:
            print(f"{name}: a source file is missing: {missing[0].name}")
            return 1
        combined = build(name, spec)
        md = OUT / f"{name}.md"
        md.write_text(combined, encoding="utf-8", newline="\n")
        if pandoc is None:
            print(f"wrote {md.relative_to(ROOT)}")
            continue
        target = OUT / f"{name}.docx"
        done = subprocess.run([pandoc, "-f", "gfm", "-t", "docx", "-o", str(target), str(md)], capture_output=True, text=True, check=False)
        if done.returncode != 0:
            print(f"{name}: pandoc failed ({done.returncode}); the combined .md was kept")
            continue
        md.unlink()  # the .docx is the deliverable; the .md is only its source (kept when pandoc is missing)
        print(f"wrote {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
