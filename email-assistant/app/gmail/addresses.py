"""E-mail address normalisation and ownership matching.

Getting this right is what lets the assistant tell *my* messages from the
other side's, and which of my addresses a message actually arrived at.
"""

from __future__ import annotations

from email.header import decode_header, make_header
from email.utils import getaddresses

GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}


def decode_mime_words(value: str | None) -> str:
    """Decode RFC 2047 encoded-words (``=?utf-8?B?...?=``) into plain text."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001 - malformed headers must never break ingest
        return value


def normalize_address(address: str | None) -> str:
    """Canonical storage form: lower-cased, trimmed, no angle brackets."""
    if not address:
        return ""
    cleaned = address.strip().strip("<>").strip()
    if "@" not in cleaned:
        return cleaned.lower()
    local, _, domain = cleaned.rpartition("@")
    return f"{local.lower()}@{domain.lower()}"


def match_keys(address: str | None) -> set[str]:
    """All forms under which *address* may legitimately refer to one mailbox.

    Covers ``+tag`` sub-addressing everywhere, and the dot-insensitivity and
    googlemail alias that apply to consumer Gmail only — dots are significant
    in a Workspace custom domain, so they are left alone there.
    """
    base = normalize_address(address)
    if not base or "@" not in base:
        return {base} if base else set()

    local, _, domain = base.rpartition("@")
    keys = {base}

    stripped_local = local.split("+", 1)[0]
    if stripped_local != local:
        keys.add(f"{stripped_local}@{domain}")

    if domain in GMAIL_DOMAINS:
        for candidate_local in (local, stripped_local):
            no_dots = candidate_local.replace(".", "")
            for candidate_domain in GMAIL_DOMAINS:
                keys.add(f"{no_dots}@{candidate_domain}")
    return {k for k in keys if k}


def domain_of(address: str | None) -> str | None:
    normalized = normalize_address(address)
    if "@" not in normalized:
        return None
    return normalized.rpartition("@")[2] or None


def parse_address_list(header_value: str | None) -> list[tuple[str, str]]:
    """Parse an address header into ``(display_name, normalised_address)`` pairs.

    Entries without an address (e.g. a bare group name like ``undisclosed
    recipients:;``) are dropped.
    """
    if not header_value:
        return []
    decoded = decode_mime_words(header_value)
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_name, raw_addr in getaddresses([decoded]):
        addr = normalize_address(raw_addr)
        if not addr or "@" not in addr:
            continue
        if addr in seen:
            continue
        seen.add(addr)
        result.append((decode_mime_words(raw_name).strip(), addr))
    return result


class OwnedAddressSet:
    """The set of addresses that belong to me, with alias-aware matching."""

    def __init__(self, addresses: list[str] | set[str] | None = None) -> None:
        self._keys: set[str] = set()
        self._canonical: dict[str, str] = {}
        for address in addresses or []:
            self.add(address)

    def add(self, address: str) -> None:
        canonical = normalize_address(address)
        if not canonical:
            return
        for key in match_keys(canonical):
            # First address registered under a key wins, so an explicitly
            # configured primary address is not shadowed by a later alias.
            self._keys.add(key)
            self._canonical.setdefault(key, canonical)

    def __contains__(self, address: object) -> bool:
        if not isinstance(address, str):
            return False
        return bool(match_keys(address) & self._keys)

    def __len__(self) -> int:
        return len(set(self._canonical.values()))

    def __bool__(self) -> bool:
        return bool(self._keys)

    def canonical(self, address: str | None) -> str | None:
        """Return my canonical form of *address*, or None if it is not mine."""
        for key in match_keys(address):
            if key in self._canonical:
                return self._canonical[key]
        return None

    def all(self) -> set[str]:
        return set(self._canonical.values())
