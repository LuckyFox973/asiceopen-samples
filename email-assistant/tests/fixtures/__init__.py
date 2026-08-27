"""Builders for realistic Gmail API payloads used across the test suite."""

from __future__ import annotations

import base64
from typing import Any


def b64url(data: bytes | str, charset: str = "utf-8") -> str:
    raw = data.encode(charset) if isinstance(data, str) else data
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def header(name: str, value: str) -> dict[str, str]:
    return {"name": name, "value": value}


def text_part(
    body: str,
    mime_type: str = "text/plain",
    charset: str = "utf-8",
    part_id: str = "0",
) -> dict[str, Any]:
    encoded = b64url(body, charset)
    return {
        "partId": part_id,
        "mimeType": mime_type,
        "filename": "",
        "headers": [header("Content-Type", f'{mime_type}; charset="{charset}"')],
        "body": {"size": len(body.encode(charset)), "data": encoded},
    }


def attachment_part(
    filename: str,
    mime_type: str = "application/pdf",
    size: int = 12345,
    attachment_id: str = "ANGjdJ_attachment_token",
    part_id: str = "1",
    inline: bool = False,
    content_id: str | None = None,
    inline_data: bytes | None = None,
) -> dict[str, Any]:
    headers = [
        header("Content-Type", f'{mime_type}; name="{filename}"'),
        header(
            "Content-Disposition",
            f'{"inline" if inline else "attachment"}; filename="{filename}"',
        ),
    ]
    if content_id:
        headers.append(header("Content-ID", f"<{content_id}>"))
    body: dict[str, Any] = {"size": size}
    if inline_data is not None:
        body["data"] = b64url(inline_data)
    else:
        body["attachmentId"] = attachment_id
    return {
        "partId": part_id,
        "mimeType": mime_type,
        "filename": filename,
        "headers": headers,
        "body": body,
    }


def gmail_message(
    *,
    message_id: str = "msg-1",
    thread_id: str = "thr-1",
    history_id: str = "100",
    internal_date_ms: str = "1756296000000",  # 2025-08-27T12:00:00Z
    subject: str = "Test",
    from_: str = "Jan Novak <jan.novak@example.sk>",
    to: str | None = "peter@foxgroup.sk",
    cc: str | None = None,
    bcc: str | None = None,
    delivered_to: str | None = None,
    date_header: str = "Wed, 27 Aug 2025 14:00:00 +0200",
    rfc822_id: str = "<CAF-abc123@mail.example.sk>",
    in_reply_to: str | None = None,
    references: str | None = None,
    labels: list[str] | None = None,
    snippet: str = "Test snippet",
    payload: dict[str, Any] | None = None,
    extra_headers: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build a payload shaped exactly like users.messages.get(format='full')."""
    headers = [
        header("From", from_),
        header("Subject", subject),
        header("Date", date_header),
        header("Message-ID", rfc822_id),
    ]
    if to:
        headers.append(header("To", to))
    if cc:
        headers.append(header("Cc", cc))
    if bcc:
        headers.append(header("Bcc", bcc))
    if delivered_to:
        for value in delivered_to.split("|"):
            headers.append(header("Delivered-To", value))
    if in_reply_to:
        headers.append(header("In-Reply-To", in_reply_to))
    if references:
        headers.append(header("References", references))
    if extra_headers:
        headers.extend(extra_headers)

    if payload is None:
        payload = {
            "partId": "",
            "mimeType": "text/plain",
            "filename": "",
            "headers": [header("Content-Type", 'text/plain; charset="UTF-8"')],
            "body": {"size": 11, "data": b64url("Ahoj svete")},
        }
    payload = {**payload, "headers": headers + list(payload.get("headers") or [])}

    return {
        "id": message_id,
        "threadId": thread_id,
        "labelIds": labels if labels is not None else ["INBOX", "UNREAD"],
        "snippet": snippet,
        "historyId": history_id,
        "internalDate": internal_date_ms,
        "sizeEstimate": 4096,
        "payload": payload,
    }


def multipart(
    mime_type: str,
    parts: list[dict[str, Any]],
    part_id: str = "",
) -> dict[str, Any]:
    return {
        "partId": part_id,
        "mimeType": mime_type,
        "filename": "",
        "headers": [header("Content-Type", f"{mime_type}; boundary=xyz")],
        "body": {"size": 0},
        "parts": parts,
    }
