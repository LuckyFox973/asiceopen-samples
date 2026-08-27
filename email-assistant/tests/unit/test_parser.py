from datetime import UTC, datetime

import pytest

from app.gmail.addresses import OwnedAddressSet
from app.gmail.parser import (
    all_participants,
    decode_body_data,
    parse_and_resolve,
    parse_date_header,
    parse_epoch_ms,
    parse_message,
    parse_references,
)
from tests.fixtures import (
    attachment_part,
    b64url,
    gmail_message,
    header,
    multipart,
    text_part,
)

OWNED = OwnedAddressSet(["peter@foxgroup.sk", "info@foxgroup.sk"])


class TestHeaders:
    def test_basic_fields(self):
        msg = parse_message(gmail_message(subject="Kasačná sťažnosť"))
        assert msg.gmail_message_id == "msg-1"
        assert msg.gmail_thread_id == "thr-1"
        assert msg.history_id == 100
        assert msg.subject == "Kasačná sťažnosť"
        assert msg.from_address == "jan.novak@example.sk"
        assert msg.from_name == "Jan Novak"
        assert msg.rfc822_message_id == "CAF-abc123@mail.example.sk"
        assert msg.labels == ["INBOX", "UNREAD"]

    def test_encoded_subject_is_decoded(self):
        raw = gmail_message(subject="=?utf-8?B?RGHFiG92w6Ega29udHJvbGE=?=")
        assert parse_message(raw).subject == "Daňová kontrola"

    def test_recipients_split_by_kind(self):
        raw = gmail_message(
            to="peter@foxgroup.sk, kolega@foxgroup.sk",
            cc="Sef <sef@example.sk>",
            bcc="tajny@example.sk",
        )
        msg = parse_message(raw)
        assert [a for _, a in msg.recipients["to"]] == [
            "peter@foxgroup.sk",
            "kolega@foxgroup.sk",
        ]
        assert msg.recipients["cc"] == [("Sef", "sef@example.sk")]
        assert msg.recipients["bcc"] == [("", "tajny@example.sk")]

    def test_repeated_delivered_to_headers_all_captured(self):
        raw = gmail_message(delivered_to="info@foxgroup.sk|peter@foxgroup.sk")
        msg = parse_message(raw)
        assert {a for _, a in msg.recipients["delivered_to"]} == {
            "info@foxgroup.sk",
            "peter@foxgroup.sk",
        }

    def test_references_parsed_into_list(self):
        raw = gmail_message(references="<a@x> <b@x>,<c@x>")
        assert parse_message(raw).references == ["a@x", "b@x", "c@x"]

    def test_raw_headers_kept_for_interesting_names_only(self):
        raw = gmail_message(extra_headers=[header("X-Spam-Score", "0.1")])
        headers = parse_message(raw).raw_headers
        assert "from" in headers and "subject" in headers
        assert "x-spam-score" not in headers

    def test_missing_headers_do_not_crash(self):
        msg = parse_message({"id": "x", "threadId": "t", "payload": {}})
        assert msg.from_address is None
        assert msg.subject is None
        assert msg.sent_at is None


class TestDates:
    def test_internal_date_is_utc(self):
        msg = parse_message(gmail_message(internal_date_ms="1756296000000"))
        assert msg.internal_date == datetime(2025, 8, 27, 12, 0, tzinfo=UTC)

    def test_date_header_converted_to_utc(self):
        msg = parse_message(gmail_message(date_header="Wed, 27 Aug 2025 14:00:00 +0200"))
        assert msg.sent_at == datetime(2025, 8, 27, 12, 0, tzinfo=UTC)

    @pytest.mark.parametrize("bad", ["", "not a date", "32 Zzz 2025"])
    def test_malformed_date_returns_none(self, bad):
        assert parse_date_header(bad) is None

    def test_naive_date_assumed_utc(self):
        assert parse_date_header("Wed, 27 Aug 2025 14:00:00").tzinfo is UTC

    @pytest.mark.parametrize("bad", [None, "", "abc", "99999999999999999999"])
    def test_bad_epoch_returns_none(self, bad):
        assert parse_epoch_ms(bad) is None

    def test_parse_references_empty(self):
        assert parse_references(None) == []


class TestBodies:
    def test_plain_and_html_from_alternative(self):
        payload = multipart(
            "multipart/alternative",
            [
                text_part("Dobrý deň,\nposielam podklady.", part_id="0"),
                text_part("<p>Dobrý deň</p>", mime_type="text/html", part_id="1"),
            ],
        )
        msg = parse_message(gmail_message(payload=payload))
        assert "posielam podklady" in msg.body_text
        assert msg.body_html == "<p>Dobrý deň</p>"

    def test_windows_1250_body_decoded_correctly(self):
        text = "Žiadosť o predĺženie lehoty"
        part = text_part(text, charset="windows-1250")
        msg = parse_message(gmail_message(payload=part))
        assert msg.body_text == text

    def test_multiple_text_parts_are_concatenated(self):
        payload = multipart(
            "multipart/mixed",
            [text_part("prva cast", part_id="0"), text_part("druha cast", part_id="1")],
        )
        msg = parse_message(gmail_message(payload=payload))
        assert msg.body_text == "prva cast\ndruha cast"

    def test_nested_multipart_related_inside_alternative(self):
        payload = multipart(
            "multipart/mixed",
            [
                multipart(
                    "multipart/alternative",
                    [
                        text_part("text verzia", part_id="0.0"),
                        multipart(
                            "multipart/related",
                            [
                                text_part(
                                    "<p>html verzia</p>",
                                    mime_type="text/html",
                                    part_id="0.1.0",
                                ),
                                attachment_part(
                                    "logo.png",
                                    mime_type="image/png",
                                    part_id="0.1.1",
                                    inline=True,
                                    content_id="logo123",
                                ),
                            ],
                            part_id="0.1",
                        ),
                    ],
                    part_id="0",
                ),
                attachment_part("Rozhodnutie.pdf", part_id="1"),
            ],
        )
        msg = parse_message(gmail_message(payload=payload))
        assert msg.body_text == "text verzia"
        assert msg.body_html == "<p>html verzia</p>"
        assert {a.filename for a in msg.attachments} == {"logo.png", "Rozhodnutie.pdf"}

    def test_empty_body_yields_none(self):
        msg = parse_message(gmail_message(payload=multipart("multipart/mixed", [])))
        assert msg.body_text is None and msg.body_html is None

    def test_decode_body_data_handles_broken_base64(self):
        assert decode_body_data("!!!not base64!!!") == ""
        assert decode_body_data(None) == ""

    def test_decode_body_data_falls_back_on_wrong_charset(self):
        # Declared utf-8 but actually cp1250 — must not raise, must not be empty.
        raw = b64url("Žiadosť".encode("windows-1250"))
        assert decode_body_data(raw, "utf-8")


class TestAttachments:
    def test_metadata_captured(self):
        payload = multipart(
            "multipart/mixed",
            [
                text_part("body", part_id="0"),
                attachment_part(
                    "Rozhodnutie.pdf", size=98765, attachment_id="TOKEN123", part_id="1"
                ),
            ],
        )
        msg = parse_message(gmail_message(payload=payload))
        att = msg.attachments[0]
        assert att.filename == "Rozhodnutie.pdf"
        assert att.mime_type == "application/pdf"
        assert att.size_bytes == 98765
        assert att.gmail_attachment_id == "TOKEN123"
        assert att.part_id == "1"
        assert att.is_inline is False

    def test_inline_image_flagged(self):
        payload = multipart(
            "multipart/related",
            [
                text_part("<img src=cid:sig>", mime_type="text/html", part_id="0"),
                attachment_part(
                    "sig.png",
                    mime_type="image/png",
                    part_id="1",
                    inline=True,
                    content_id="sig",
                ),
            ],
        )
        msg = parse_message(gmail_message(payload=payload))
        assert msg.attachments[0].is_inline is True
        assert msg.attachments[0].content_id == "sig"

    def test_small_inline_data_preserved(self):
        payload = multipart(
            "multipart/mixed",
            [
                text_part("body", part_id="0"),
                attachment_part(
                    "note.txt",
                    mime_type="text/plain",
                    part_id="1",
                    inline_data=b"hello bytes",
                ),
            ],
        )
        msg = parse_message(gmail_message(payload=payload))
        assert msg.attachments[0].inline_data == b"hello bytes"
        assert msg.attachments[0].gmail_attachment_id is None

    def test_encoded_filename_decoded(self):
        payload = multipart(
            "multipart/mixed",
            [
                text_part("body", part_id="0"),
                attachment_part("=?utf-8?B?WsOhcGlzbmljYS5wZGY=?=", part_id="1"),
            ],
        )
        assert parse_message(gmail_message(payload=payload)).attachments[0].filename == (
            "Zápisnica.pdf"
        )

    def test_message_without_attachments(self):
        assert parse_message(gmail_message()).attachments == []


class TestDirection:
    def test_inbound_matches_receiving_alias(self):
        raw = gmail_message(from_="klient@abc.sk", to="info@foxgroup.sk")
        msg = parse_and_resolve(raw, OWNED)
        assert msg.direction == "inbound"
        assert msg.account_address == "info@foxgroup.sk"

    def test_outbound_uses_sending_address(self):
        raw = gmail_message(from_="Peter <peter@foxgroup.sk>", to="klient@abc.sk")
        msg = parse_and_resolve(raw, OWNED)
        assert msg.direction == "outbound"
        assert msg.account_address == "peter@foxgroup.sk"

    def test_internal_when_all_parties_are_mine(self):
        raw = gmail_message(from_="peter@foxgroup.sk", to="info@foxgroup.sk")
        assert parse_and_resolve(raw, OWNED).direction == "internal"

    def test_delivered_to_wins_over_to_header(self):
        raw = gmail_message(
            from_="klient@abc.sk",
            to="mailing-list@example.sk",
            delivered_to="info@foxgroup.sk",
        )
        msg = parse_and_resolve(raw, OWNED)
        assert msg.account_address == "info@foxgroup.sk"

    def test_plus_tag_recipient_resolves_to_canonical_alias(self):
        raw = gmail_message(from_="klient@abc.sk", to="peter+dane@foxgroup.sk")
        assert parse_and_resolve(raw, OWNED).account_address == "peter@foxgroup.sk"

    def test_bcc_only_delivery_is_inbound_with_unknown_alias(self):
        raw = gmail_message(from_="klient@abc.sk", to="niekto-iny@example.sk")
        msg = parse_and_resolve(raw, OWNED)
        assert msg.direction == "inbound"
        assert msg.account_address is None

    def test_no_owned_addresses_yields_unknown(self):
        msg = parse_and_resolve(gmail_message(), OwnedAddressSet([]))
        assert msg.direction == "unknown"

    def test_outbound_to_self_and_others_is_outbound(self):
        raw = gmail_message(from_="peter@foxgroup.sk", to="info@foxgroup.sk, cudzi@x.sk")
        assert parse_and_resolve(raw, OWNED).direction == "outbound"


class TestContentHash:
    def test_stable_across_identical_parses(self):
        raw = gmail_message()
        assert parse_message(raw).content_hash() == parse_message(raw).content_hash()

    def test_changes_when_body_changes(self):
        a = parse_message(gmail_message(payload=text_part("aaa")))
        b = parse_message(gmail_message(payload=text_part("bbb")))
        assert a.content_hash() != b.content_hash()

    def test_changes_when_labels_change(self):
        a = parse_message(gmail_message(labels=["INBOX"]))
        b = parse_message(gmail_message(labels=["INBOX", "STARRED"]))
        assert a.content_hash() != b.content_hash()

    def test_label_order_does_not_matter(self):
        a = parse_message(gmail_message(labels=["INBOX", "STARRED"]))
        b = parse_message(gmail_message(labels=["STARRED", "INBOX"]))
        assert a.content_hash() == b.content_hash()


def test_all_participants_flattens_every_header():
    raw = gmail_message(
        from_="klient@abc.sk",
        to="peter@foxgroup.sk, kolega@foxgroup.sk",
        cc="sef@example.sk",
    )
    rows = all_participants(parse_message(raw))
    kinds = [k for k, _, _, _ in rows]
    assert kinds.count("from") == 1
    assert kinds.count("to") == 2
    assert kinds.count("cc") == 1
    assert ("to", "", "kolega@foxgroup.sk", 1) in rows
