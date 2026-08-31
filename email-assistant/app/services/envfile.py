"""Reading and writing .env without disturbing the rest of it.

Written because hand-editing went wrong in a way that was nobody's fault:
appending to a file whose last line had no newline glued two settings onto one
line, and every command then refused to start.  A setting is a thing the
program should be able to change safely on its owner's behalf.

**Values are read through python-dotenv**, the same parser the application
loads its settings with, rather than by splitting on the first ``=``.  Its
rules are not obvious and getting them wrong means reporting a value the
program does not actually have: an unquoted ``#`` after whitespace begins a
comment (``development  # or staging`` is ``development``) but one inside the
value does not (``pass#word`` is intact), a quoted value keeps its hashes, and
where a name is set twice **the last one wins**.

Writing is done by editing lines, so comments, blank lines and ordering
survive — a .env is documentation as much as configuration here.
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
    # True when the name is assigned more than once.  Harmless to read — the
    # last assignment is what the application sees — but a trap to edit, and
    # worth saying out loud.
    duplicated: bool = False

    @property
    def secret(self) -> bool:
        return bool(SECRET_NAME.search(self.name))

    def display(self) -> str:
        """The value, or enough of a secret to recognise it by."""
        if not self.secret or not self.value:
            return self.value or "(empty)"
        return f"({len(self.value)} characters, hidden)"


def read(path: Path) -> list[Setting]:
    """Every setting, in the order the file introduces it.

    Values come from dotenv, so what is reported is what the application
    loads; the file scan only supplies the order and spots repeats.
    """
    if not path.exists():
        return []

    from dotenv import dotenv_values

    effective = dotenv_values(path)

    seen: dict[str, int] = {}
    order: list[tuple[str, int]] = []
    for number, raw in enumerate(path.read_text().splitlines()):
        if raw.lstrip().startswith("#"):
            continue
        match = ASSIGNMENT.match(raw)
        if not match:
            continue
        name = match.group(1)
        if name in seen:
            seen[name] += 1
        else:
            seen[name] = 1
            order.append((name, number))

    return [
        Setting(name, effective.get(name) or "", line, duplicated=seen[name] > 1)
        for name, line in order
    ]


def get(path: Path, name: str) -> Setting | None:
    return next((s for s in read(path) if s.name == name.upper()), None)


def set_value(path: Path, name: str, value: str) -> tuple[bool, str]:
    """Set *name*, in place if it is there and appended if not.

    Returns ``(created, previous)``.  The file always ends in a newline
    afterwards, which is the whole reason this exists.
    """
    name = name.upper()
    lines = path.read_text().splitlines() if path.exists() else []
    previous = get(path, name)

    at: list[int] = []
    for index, raw in enumerate(lines):
        if raw.lstrip().startswith("#"):
            continue
        match = ASSIGNMENT.match(raw)
        if match and match.group(1) == name:
            at.append(index)

    if not at:
        lines.append(f"{name}={value}")
        _write(path, lines)
        return True, ""

    # The last assignment is the one in force, so that is the one to change —
    # editing the first would leave a later line still overriding it, and the
    # command would appear to do nothing.  Earlier repeats are dropped, since
    # leaving them is how the confusion started.
    lines[at[-1]] = f"{name}={value}"
    for index in reversed(at[:-1]):
        del lines[index]
    _write(path, lines)
    return False, previous.value if previous else ""


def _write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip("\n") + "\n")


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value
