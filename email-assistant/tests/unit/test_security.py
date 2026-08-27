from app.core.security import (
    KEY_PREFIX,
    display_prefix,
    extract_bearer,
    generate_api_key,
    generate_oauth_state,
    hash_api_key,
    keys_match,
)


class TestKeyGeneration:
    def test_keys_are_prefixed_and_unique(self):
        keys = {generate_api_key() for _ in range(200)}
        assert len(keys) == 200
        assert all(k.startswith(KEY_PREFIX) for k in keys)

    def test_key_is_long_enough_to_be_unguessable(self):
        assert len(generate_api_key()) >= 40

    def test_hash_is_stable_and_hides_the_key(self):
        key = generate_api_key()
        assert hash_api_key(key) == hash_api_key(key)
        assert key not in hash_api_key(key)
        assert len(hash_api_key(key)) == 64

    def test_hash_ignores_surrounding_whitespace(self):
        key = generate_api_key()
        assert hash_api_key(f"  {key}\n") == hash_api_key(key)

    def test_different_keys_hash_differently(self):
        assert hash_api_key(generate_api_key()) != hash_api_key(generate_api_key())

    def test_display_prefix_is_a_short_stable_handle(self):
        key = generate_api_key()
        assert key.startswith(display_prefix(key))
        assert len(display_prefix(key)) == 12

    def test_keys_match_compares_equal_hashes(self):
        digest = hash_api_key(generate_api_key())
        assert keys_match(digest, digest)
        assert not keys_match(digest, hash_api_key(generate_api_key()))

    def test_oauth_states_are_unique(self):
        assert len({generate_oauth_state() for _ in range(200)}) == 200


class TestBearerParsing:
    def test_extracts_token(self):
        assert extract_bearer("Bearer abc123") == "abc123"

    def test_scheme_is_case_insensitive(self):
        assert extract_bearer("bearer abc123") == "abc123"

    def test_rejects_other_schemes(self):
        assert extract_bearer("Basic abc123") is None

    def test_rejects_malformed_values(self):
        for value in (None, "", "Bearer", "Bearer   ", "abc123"):
            assert extract_bearer(value) is None

    def test_tolerates_extra_spacing(self):
        assert extract_bearer("Bearer    abc123  ") == "abc123"
