from app.gmail.addresses import (
    OwnedAddressSet,
    decode_mime_words,
    domain_of,
    match_keys,
    normalize_address,
    parse_address_list,
)


class TestNormalize:
    def test_lowercases_and_strips(self):
        assert normalize_address("  <Peter@FoxGroup.SK> ") == "peter@foxgroup.sk"

    def test_empty_input(self):
        assert normalize_address(None) == ""
        assert normalize_address("") == ""

    def test_local_part_with_at_sign_kept(self):
        assert normalize_address('"weird@local"@example.com') == '"weird@local"@example.com'


class TestMatchKeys:
    def test_plus_tag_matches_base(self):
        assert "peter@foxgroup.sk" in match_keys("peter+faktury@foxgroup.sk")

    def test_dots_significant_on_custom_domain(self):
        assert "jannovak@foxgroup.sk" not in match_keys("jan.novak@foxgroup.sk")

    def test_dots_ignored_on_gmail(self):
        keys = match_keys("jan.novak@gmail.com")
        assert "jannovak@gmail.com" in keys
        assert "jannovak@googlemail.com" in keys


class TestOwnedAddressSet:
    def test_recognises_alias_forms(self):
        owned = OwnedAddressSet(["Peter@FoxGroup.sk", "info@foxgroup.sk"])
        assert "peter+dane@foxgroup.sk" in owned
        assert "INFO@foxgroup.sk" in owned
        assert "protistrana@example.com" not in owned

    def test_canonical_returns_configured_form(self):
        owned = OwnedAddressSet(["Peter@FoxGroup.sk"])
        assert owned.canonical("peter+x@FOXGROUP.sk") == "peter@foxgroup.sk"
        assert owned.canonical("nikto@example.com") is None

    def test_len_counts_distinct_addresses(self):
        owned = OwnedAddressSet(["a@x.sk", "b@x.sk", "a@x.sk"])
        assert len(owned) == 2

    def test_empty_set_is_falsy(self):
        assert not OwnedAddressSet([])


class TestParseAddressList:
    def test_decodes_encoded_words(self):
        parsed = parse_address_list("=?utf-8?B?SsOhbiBOb3bDoWs=?= <jan@example.sk>")
        assert parsed == [("Ján Novák", "jan@example.sk")]

    def test_multiple_and_dedup(self):
        parsed = parse_address_list("a@x.sk, B@X.sk, a@x.sk")
        assert parsed == [("", "a@x.sk"), ("", "b@x.sk")]

    def test_drops_group_syntax_without_address(self):
        assert parse_address_list("undisclosed-recipients:;") == []

    def test_handles_none(self):
        assert parse_address_list(None) == []


def test_decode_mime_words_survives_garbage():
    assert decode_mime_words("=?broken?X?zzz?=") == "=?broken?X?zzz?="


def test_domain_of():
    assert domain_of("Peter@FoxGroup.sk") == "foxgroup.sk"
    assert domain_of("not-an-address") is None
