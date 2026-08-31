"""Reading and writing .env without disturbing the rest of it.

Written because hand-editing went wrong in a way that was nobody's fault:
appending to a file whose last line had no newline glued two settings onto one
line, and every command then refused to start.  A setting is a thing the
program should be able to change safely on its owner's behalf.

Comments, blank lines and ordering are preserved — a .env is documentation as
much as configuration here, and rewriting it as a bare dictionary would throw
that away.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Anything whose name looks like this holds a credential, and its value is
# never printed.  Terminal output gets pasted into chats and issue reports.
SECRET_NAME = re.compile(r"(SECRET|PASSWORD|TOKEN|_KEY|CREDENTIAL|DSN|DATABASE_URL)", re.I)

ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


@dataclass(frozen=True)
class Setting:
    name: str
    value: str
    line: int

    @property
    def secret(self) -> bool:
        return bool(SECRET_NAME.search(self.name))

    def display(self) -> str:
        """The value, or enough of a secret to recognise it by."""
        if not self.secret or not self.value:
            return self.value or "(empty)"
        return f"({len(self.value)} characters, hidden)"


def read(path: Path) -> list[Setting]:
    if not path.exists():
        return []
    settings: list[Setting] = []
    for number, raw in enumerate(path.read_text().splitlines()):
        if raw.lstrip().startswith("#"):
            continue
        match = ASSIGNMENT.match(raw)
        if match:
            name, value = match.groups()
            settings.append(Setting(name, _unquote(value.strip()), number))
    return settings


def get(path: Path, name: str) -> Setting | None:
    return next((s for s in read(path) if s.name == name.upper()), None)


def set_value(path: Path, name: str, value: str) -> tuple[bool, str]:
    """Set *name*, in place if it is there and appended if not.

    Returns ``(created, previous)``.  The file always ends in a newline
    afterwards, which is the whole reason this exists.
    """
    name = name.upper()
    lines = path.read_text().splitlines() if path.exists() else []

    for index, raw in enumerate(lines):
        if raw.lstrip().startswith("#"):
            continue
        match = ASSIGNMENT.match(raw)
        if match and match.group(1) == name:
            previous = _unquote(match.group(2).strip())
            lines[index] = f"{name}={value}"
            _write(path, lines)
            return False, previous

    lines.append(f"{name}={value}")
    _write(path, lines)
    return True, ""


def _write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip("\n") + "\n")


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value
