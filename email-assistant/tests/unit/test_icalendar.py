"""Reading a meeting invitation, including the ones real clients actually send."""

from __future__ import annotations

import pytest

from app.services.extraction import ExtractionStatus, extract, strip_html
from app.services.icalendar_text import read_calendar, split_property, unescape, unfold
from tests.fixtures.documents import make_ics

HEARING = (
    "BEGIN:VEVENT\n"
    "SUMMARY:Pojednávanie 12C/45/2026\n"
    "DTSTART;TZID=Europe/Bratislava:20260915T093000\n"
    "DTEND;TZID=Europe/Bratislava:20260915T113000\n"
    "LOCATION:Záhradnícka 10\\, Bratislava\n"
    "ORGANIZER;CN=Okresný súd BA I:mailto:podatelna@osba1.justice.sk\n"
    "ATTENDEE;CN=Ján Novák;ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION:"
    "mailto:novak@advokat.sk\n"
    "STATUS:CONFIRMED\n"
    "END:VEVENT"
)


def read(body: str, **kwargs) -> str:
    return read_calendar(make_ics(body, **kwargs), strip_html)


class TestUnfolding:
    def test_a_fold_that_splits_a_utf8_character_is_rejoined(self):
        """RFC 5545 folds at 75 OCTETS, which cuts multi-byte characters."""
        folded = b"SUMMARY:Pojedn\xc3\r\n \xa1vanie"
        assert unfold(folded)[0].decode("utf-8") == "SUMMARY:Pojednávanie"

    def test_a_tab_continuation_is_rejoined(self):
        """Exchange folds with a tab, not a space."""
        assert unfold(b"ATTENDEE:mai\r\n\tlto:x@y.sk")[0] == b"ATTENDEE:mailto:x@y.sk"

    def test_only_one_whitespace_character_is_consumed(self):
        assert unfold(b"DESCRIPTION:a\r\n  b")[0] == b"DESCRIPTION:a b"

    @pytest.mark.parametrize("newline", ["\r\n", "\n", "\r"])
    def test_every_line_ending_in_the_wild_is_handled(self, newline):
        text = read(HEARING, newline=newline)
        assert "Pojednávanie 12C/45/2026" in text

    def test_one_endlessly_folded_line_cannot_exhaust_memory(self):
        monster = b"DESCRIPTION:x" + b"\r\n y" * 200_000
        joined = unfold(monster)
        assert len(joined) == 1
        assert len(joined[0]) <= 64 * 1024 + 16


class TestPropertySplitting:
    def test_a_quoted_parameter_may_contain_a_colon(self):
        """`line.split(":", 1)` mangles a court's name."""
        line = 'ORGANIZER;CN="Súd: Okresný súd BA I";ROLE=CHAIR:mailto:podatelna@sud.sk'
        name, params, value = split_property(line)
        assert name == "ORGANIZER"
        assert params["CN"] == "Súd: Okresný súd BA I"
        assert value == "mailto:podatelna@sud.sk"

    def test_a_group_prefix_is_stripped(self):
        assert split_property("item1.SUMMARY:Vec")[0] == "SUMMARY"


class TestEscaping:
    def test_an_escaped_backslash_is_not_a_newline(self):
        r"""`C:\nazov` is a backslash then 'n', not a line break."""
        assert unescape(r"C:\\nazov spisu") == r"C:\nazov spisu"

    def test_backslash_n_is_a_newline(self):
        assert unescape(r"riadok\ndruhy") == "riadok\ndruhy"

    def test_uppercase_backslash_n_is_also_a_newline(self):
        assert unescape(r"riadok\Ndruhy") == "riadok\ndruhy"

    def test_an_escaped_comma_survives_in_a_location(self):
        assert "Záhradnícka 10, Bratislava" in read(HEARING)


class TestRendering:
    def test_the_essentials_of_a_hearing_are_present(self):
        text = read(HEARING)
        for expected in (
            "SUMMARY: Pojednávanie 12C/45/2026",
            "START: 2026-09-15 09:30 (Europe/Bratislava)",
            "LOCATION: Záhradnícka 10, Bratislava",
            "STATUS: CONFIRMED",
        ):
            assert expected in text

    def test_a_participant_is_rendered_with_name_and_answer(self):
        text = read(HEARING)
        assert "Ján Novák <novak@advokat.sk>" in text
        assert "REQ-PARTICIPANT" in text and "NEEDS-ACTION" in text

    def test_a_cancellation_is_stated_before_the_events(self):
        """METHOD:CANCEL is the most consequential line in the file."""
        text = read("METHOD:CANCEL\n" + HEARING)
        assert text.startswith("CALENDAR METHOD: CANCEL")

    def test_an_unzoned_time_is_not_silently_called_utc(self):
        text = read("BEGIN:VEVENT\nSUMMARY:X\nDTSTART:20260915T093000\nEND:VEVENT")
        assert "(floating local time)" in text

    def test_a_utc_time_says_utc(self):
        text = read("BEGIN:VEVENT\nSUMMARY:X\nDTSTART:20260915T073000Z\nEND:VEVENT")
        assert "2026-09-15 07:30 UTC" in text

    def test_a_non_iana_timezone_is_echoed_rather_than_resolved(self):
        """Exchange writes zone names that zoneinfo raises on."""
        text = read(
            "BEGIN:VEVENT\nSUMMARY:X\n"
            'DTSTART;TZID="Central Europe Standard Time":20260915T093000\nEND:VEVENT'
        )
        assert "(Central Europe Standard Time)" in text

    def test_an_all_day_end_date_is_made_inclusive(self):
        """DTEND of a DATE event is exclusive; a one-day deadline is one day."""
        text = read(
            "BEGIN:VEVENT\nSUMMARY:Lehota\n"
            "DTSTART;VALUE=DATE:20260915\nDTEND;VALUE=DATE:20260916\nEND:VEVENT"
        )
        assert "START: 2026-09-15 (all day)" in text
        assert "END: 2026-09-15 (all day)" in text
        assert "2026-09-16" not in text


class TestNoise:
    def test_a_reminder_does_not_overwrite_the_event(self):
        """VALARM carries its own DESCRIPTION, the literal word 'Reminder'."""
        text = read(
            "BEGIN:VEVENT\nSUMMARY:Pojednávanie\nDESCRIPTION:Priniesť originály\n"
            "BEGIN:VALARM\nACTION:DISPLAY\nDESCRIPTION:Reminder\nTRIGGER:-PT15M\n"
            "END:VALARM\nEND:VEVENT"
        )
        assert "Priniesť originály" in text
        assert "Reminder" not in text

    def test_a_timezone_definition_does_not_become_an_event(self):
        """VTIMEZONE children carry DTSTART values in 1601."""
        text = read(
            "BEGIN:VTIMEZONE\nTZID:Europe/Bratislava\n"
            "BEGIN:STANDARD\nDTSTART:16011028T030000\nEND:STANDARD\n"
            "END:VTIMEZONE\n" + HEARING
        )
        assert "1601" not in text
        assert "Pojednávanie" in text

    def test_an_inline_attachment_is_dropped(self):
        payload = "A" * 5000
        text = read(
            f"BEGIN:VEVENT\nSUMMARY:X\nATTACH;ENCODING=BASE64;VALUE=BINARY:{payload}\nEND:VEVENT"
        )
        assert payload[:100] not in text

    def test_a_recurrence_rule_is_echoed_never_expanded(self):
        """FREQ=SECONDLY;COUNT=2000000000 is a two-line denial of service."""
        text = read(
            "BEGIN:VEVENT\nSUMMARY:X\nDTSTART:20260915T093000Z\n"
            "RRULE:FREQ=SECONDLY;COUNT=2000000000\nEND:VEVENT"
        )
        assert "RECURS: FREQ=SECONDLY;COUNT=2000000000" in text
        assert len(text) < 500


class TestMalformedInput:
    def test_an_invitation_truncated_in_transit_still_yields_what_arrived(self):
        raw = b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Pojednavanie\r\n"
        assert "Pojednavanie" in read_calendar(raw, strip_html)

    def test_thousands_of_unclosed_components_are_bounded(self):
        raw = b"BEGIN:VCALENDAR\r\n" + b"BEGIN:VEVENT\r\nSUMMARY:x\r\n" * 5_000
        text = read_calendar(raw, strip_html)
        assert text.count("# VEVENT") <= 2_000

    def test_a_calendar_with_nothing_renderable_is_empty(self):
        result = extract(make_ics("BEGIN:VTIMEZONE\nTZID:X\nEND:VTIMEZONE"), filename="a.ics")
        assert result.status is ExtractionStatus.EMPTY

    def test_legacy_quoted_printable_slovak_is_decoded(self):
        """Ancient Outlook sends vCalendar 1.0 like this."""
        raw = (
            b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n"
            b"SUMMARY;ENCODING=QUOTED-PRINTABLE;CHARSET=windows-1250:Pojedn=E1vanie\r\n"
            b"END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        assert "Pojednávanie" in read_calendar(raw, strip_html)


class TestThroughTheExtractor:
    def test_an_ics_attachment_is_routed_to_the_calendar_reader(self):
        result = extract(make_ics(HEARING), mime_type="application/ics", filename="invite.ics")
        assert result.status is ExtractionStatus.EXTRACTED
        assert result.method == "ics"
        assert "Pojednávanie 12C/45/2026" in result.text
