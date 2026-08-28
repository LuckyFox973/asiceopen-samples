"""Importing the OAuth client file Google hands you as a download.

Google's console gives you a JSON file rather than something you can paste, so
the obvious move is to open it and copy two values across by hand. This does it
instead — and while it has the file open, it checks the redirect URIs against
what the application actually uses, which is the single most common reason a
first authorisation fails.

Secrets are never printed, and never logged.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

# Google names the download client_secret_<id>.apps.googleusercontent.com.json
DOWNLOAD_PATTERN = "client_secret*.json"
SEARCH_DIRS = ("~/Downloads", "~/Desktop", ".")


class CredentialsError(Exception):
    """The file is missing, unreadable, or not an OAuth client file."""


@dataclass(slots=True)
class GoogleClient:
    client_id: str
    client_secret: str
    project_id: str | None
    redirect_uris: list[str]
    kind: str  # "web" or "installed"

    @property
    def masked_id(self) -> str:
        """Enough to recognise it, not enough to be a credential in a log."""
        head = self.client_id.split("-", 1)[0]
        return f"{head}-….apps.googleusercontent.com"


def find_download(explicit: str | None = None) -> Path:
    """Locate the credentials file: an explicit path, or the newest download."""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise CredentialsError(f"No file at {path}")
        return path

    candidates: list[Path] = []
    for directory in SEARCH_DIRS:
        base = Path(directory).expanduser()
        if base.is_dir():
            candidates.extend(base.glob(DOWNLOAD_PATTERN))

    if not candidates:
        raise CredentialsError(
            "No client_secret*.json found in ~/Downloads, ~/Desktop or here.\n"
            "In the Google console: Google Auth Platform → Clients → your client "
            "→ the download icon. Or pass the path: "
            "python -m app.cli import-credentials /path/to/file.json"
        )
    # Newest wins: re-downloading after a change should supersede the old one.
    return max(candidates, key=lambda p: p.stat().st_mtime)


def parse(path: Path) -> GoogleClient:
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise CredentialsError(f"{path.name} is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise CredentialsError(f"Could not read {path}: {exc}") from exc

    # "web" for a Web application client, "installed" for a Desktop one.
    for kind in ("web", "installed"):
        if kind in data:
            block = data[kind]
            break
    else:
        raise CredentialsError(
            f"{path.name} does not look like an OAuth client file — it has no "
            "'web' or 'installed' section. Did you download a service account "
            "key by mistake?"
        )

    client_id = (block.get("client_id") or "").strip()
    client_secret = (block.get("client_secret") or "").strip()
    if not client_id or not client_secret:
        raise CredentialsError(f"{path.name} is missing the client id or secret.")

    return GoogleClient(
        client_id=client_id,
        client_secret=client_secret,
        project_id=block.get("project_id"),
        redirect_uris=list(block.get("redirect_uris") or []),
        kind=kind,
    )


def check_redirect_uris(client: GoogleClient, expected: str) -> list[str]:
    """Problems with the redirect configuration, most important first.

    Catching this here turns a baffling `redirect_uri_mismatch` during
    authorisation into a sentence before you ever open the browser.
    """
    problems: list[str] = []

    if client.kind == "installed":
        problems.append(
            "This is a Desktop app client. Create a Web application client "
            "instead — the assistant receives the callback on a local HTTP "
            "server, which Desktop clients handle differently."
        )

    if not client.redirect_uris:
        problems.append(
            "The client has no authorised redirect URIs. Add both of these in "
            "Google Auth Platform → Clients → your client:\n"
            "    http://localhost:8000/api/v1/auth/google/callback\n"
            "    http://127.0.0.1:8000/api/v1/auth/google/callback"
        )
        return problems

    if expected not in client.redirect_uris:
        problems.append(
            f"GOOGLE_OAUTH_REDIRECT_URI is {expected!r}, which is not one of the "
            "authorised redirect URIs on the client:\n    "
            + "\n    ".join(client.redirect_uris)
            + "\nAdd it in Google, or change .env to match one of the above."
        )

    # Both spellings, because browsers and CLI tools disagree about which they send.
    hosts = {"localhost", "127.0.0.1"}
    present = {host for host in hosts for uri in client.redirect_uris if f"://{host}:" in uri}
    if len(present) == 1:
        missing = (hosts - present).pop()
        problems.append(
            f"Only one of localhost / 127.0.0.1 is authorised. Add the {missing} "
            "spelling too — some tools send one and some the other."
        )
    return problems


def write_env(env_path: Path, values: dict[str, str], template: Path | None = None) -> list[str]:
    """Set keys in a .env file, preserving everything else. Returns keys changed."""
    if not env_path.exists():
        if template and template.exists():
            env_path.write_text(template.read_text())
        else:
            env_path.write_text("")

    text = env_path.read_text()
    changed: list[str] = []

    for key, value in values.items():
        line = f"{key}={value}"
        pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
        if pattern.search(text):
            existing = pattern.search(text).group(0)
            if existing != line:
                text = pattern.sub(lambda _m, ln=line: ln, text, count=1)
                changed.append(key)
        else:
            text = text.rstrip("\n") + f"\n{line}\n"
            changed.append(key)

    env_path.write_text(text)
    # A file holding an OAuth secret should not be world-readable.
    env_path.chmod(0o600)
    return changed
