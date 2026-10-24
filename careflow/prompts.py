"""Load a prompt template from `prompts/` with its version and content hash (BUILD-SPEC.md §6.2, §17.4).

"Prompts live in the repository with a version and content hash; every model call records which version answered." A template is two files,
`NAME.vN.system.txt` and `NAME.vN.user.txt`; the version is the `vN` in the file name and the hash is the sha256 of the two files' bytes. The
CI scan (`careflow/prompt_scan.py`, run over `prompts/` by tests) guarantees the system prompt has no placeholder and the user template uses
only allowlisted variables; this loader refuses to fill a template with a variable the scan would not allow, so the two cannot disagree.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from careflow.prompt_scan import load_allowlist

PROMPT_DIR: Final = Path(__file__).resolve().parent.parent / "prompts"
_NAME: Final = re.compile(r"^[a-z][a-z0-9_]*$")
_VERSION: Final = re.compile(r"^v[0-9]+$")
_SLOT: Final = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class PromptError(Exception):
    """A template is missing, malformed, or asked to be filled with a variable it does not allow."""


@dataclass(frozen=True)
class PromptSet:
    name: str
    version: str
    sha256: str
    system: str
    user_template: str

    @property
    def label(self) -> str:
        """What a model call records as the prompt that answered: name, version and the first 12 hex digits of the hash."""
        return f"{self.name}.{self.version}@{self.sha256[:12]}"

    def render_user(self, **variables: str) -> str:
        """Fill the user template. Exactly the slots it declares must be supplied, each allowlisted; a value is inserted as-is and never
        re-parsed (a value containing braces is not a template)."""
        slots = set(_SLOT.findall(self.user_template))
        if set(variables) != slots:
            raise PromptError(f"the template takes exactly {sorted(slots)}")
        allowed, _ = load_allowlist(PROMPT_DIR / "variables.allowlist")
        if not slots <= allowed:
            raise PromptError("a template slot is not allowlisted")
        return _SLOT.sub(lambda m: variables[m.group(1)], self.user_template)


def load_prompt(name: str, version: str, *, directory: Path = PROMPT_DIR) -> PromptSet:
    if not _NAME.match(name) or not _VERSION.match(version):
        raise PromptError("a prompt name is lower_snake and a version looks like v1")
    system_path, user_path = directory / f"{name}.{version}.system.txt", directory / f"{name}.{version}.user.txt"
    try:
        system_bytes, user_bytes = system_path.read_bytes(), user_path.read_bytes()
    except FileNotFoundError:
        raise PromptError(f"prompt {name}.{version} is not in the prompt directory") from None
    digest = hashlib.sha256(system_bytes + b"\x00" + user_bytes).hexdigest()
    return PromptSet(name, version, digest, system_bytes.decode("utf-8"), user_bytes.decode("utf-8"))
