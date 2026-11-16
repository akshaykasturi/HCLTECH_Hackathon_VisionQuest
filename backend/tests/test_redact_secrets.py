"""Secret masking (app/common/redact.py): unit cases, provider key formats and no mutation.

Fake secrets are built from pieces so secret scanners don't flag this file, and no test
message ever contains a secret value (only kinds and counts).
"""
import copy
import json

from app.common.redact import find_leaks, hash_idempotency_key, mask, mask_record, mask_text

FAKE_JWT = "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiJmYWtlIn0" + "." + "c2lnbmF0dXJlX2Zha2U"
FAKE_LIVE = "demo_live_" + "AbC123dEf456"
FAKE_TEST = "demo_test_" + "ZyX987wVu654"
FAKE_MAIL = "jane.doe" + "@" + "example.com"

# --- mask_text, one kind at a time ------------------------------------------------------

def test_bearer_token_in_header_text() -> None:
    out, counts = mask_text(f"Bearer {FAKE_JWT}")
    assert out == "Bearer <TOKEN>"
    assert counts == {"bearer_token": 1}


def test_token_inside_error_message() -> None:
    out, counts = mask_text(f"rejected: Authorization was 'Bearer {FAKE_JWT}' (expired)")
    assert out == "rejected: Authorization was 'Bearer <TOKEN>' (expired)"
    assert counts == {"bearer_token": 1}


def test_bare_jwt_without_bearer() -> None:
    out, counts = mask_text(f"token={FAKE_JWT};")
    assert out == "token=<TOKEN>;"
    assert counts == {"bearer_token": 1}


def test_api_keys_keep_their_prefix() -> None:
    out, counts = mask_text(f"live {FAKE_LIVE} test {FAKE_TEST}")
    assert out == "live demo_live_<KEY> test demo_test_<KEY>"
    assert counts == {"api_key_live": 1, "api_key_test": 1}


def test_email_inside_error_message() -> None:
    out, counts = mask_text(f"field 'customer' is invalid (received customer_email={FAKE_MAIL})")
    assert out == "field 'customer' is invalid (received customer_email=<EMAIL>)"
    assert counts == {"email": 1}


def test_ip_keeps_the_slash_24() -> None:
    out, counts = mask_text("source ip 203.0.113.24 is not allowlisted.")
    assert out == "source ip 203.0.113.x is not allowlisted."
    assert counts == {"ip": 1}


def test_text_without_secrets_is_unchanged() -> None:
    text = ("Bearer token expired after 3600 s; upstream timeout (p95=850, timeout=1000); "
            "version 1.2.3.4.5, scope 'payments:write', key_prefix demo_live, sk-learn, 999.1.2.3")
    out, counts = mask_text(text)
    assert out == text
    assert counts == {}
    assert find_leaks(text) == []


# --- mask_record -------------------------------------------------------------------------

def _fake_record() -> dict:
    return {
        "correlation_id": "c-0001",
        "client_ip": "198.51.100.7",
        "status": 401,
        "latency_ms": 42,
        "request_headers": {
            "Authorization": f"Bearer {FAKE_JWT}",
            "X-Api-Key": FAKE_TEST,
            "Idempotency-Key": "idem_" + "abcdef0123456789",
            "User-Agent": "acme-sdk/1.4 (python)",
        },
        "error": {"code": "invalid_request", "message": f"bad customer_email={FAKE_MAIL}"},
        "auth": {"method": "bearer", "key_prefix": "demo_test", "token_age_s": 7300,
                 "scopes": ["payments:read"]},
    }


def test_record_is_masked_structurally() -> None:
    masked, counts = mask_record(_fake_record())
    h = masked["request_headers"]
    assert h["Authorization"] == "Bearer <TOKEN>"
    assert h["X-Api-Key"] == "demo_test_<KEY>"
    assert h["Idempotency-Key"] == hash_idempotency_key("idem_" + "abcdef0123456789")
    assert h["User-Agent"] == "acme-sdk/1.4 (python)"
    assert masked["client_ip"] == "198.51.100.x"
    assert masked["error"]["message"] == "bad customer_email=<EMAIL>"
    assert counts == {"bearer_token": 1, "api_key_test": 1, "idempotency_key": 1,
                      "ip": 1, "email": 1}
    assert find_leaks(json.dumps(masked)) == []


def test_non_secret_fields_are_kept() -> None:
    original = _fake_record()
    masked, _ = mask_record(original)
    for key in ("correlation_id", "status", "latency_ms", "auth"):
        assert masked[key] == original[key], key


def test_input_record_is_not_mutated() -> None:
    record = _fake_record()
    snapshot = copy.deepcopy(record)
    mask_record(record)
    unchanged = record == snapshot  # compared outside assert so a failure prints no values
    assert unchanged, "mask_record changed its input"


def test_unknown_api_key_format_is_fully_hidden() -> None:
    masked, counts = mask_record({"headers": {"x-api-key": "sk" + "-whatever-1234"}})
    assert masked["headers"]["x-api-key"] == "<KEY>"
    assert counts == {"api_key_other": 1}


# --- Idempotency-Key hashing -------------------------------------------------------------

def test_same_idempotency_key_same_hash() -> None:
    a = hash_idempotency_key("idem_" + "retry-me-123")
    assert a == hash_idempotency_key("idem_" + "retry-me-123")
    assert a.startswith("idem_") and len(a) == len("idem_") + 8
    assert "retry-me-123" not in a


def test_different_idempotency_keys_differ() -> None:
    assert hash_idempotency_key("idem_" + "one") != hash_idempotency_key("idem_" + "two")


# --- find_leaks --------------------------------------------------------------------------

def test_find_leaks_names_kinds_only() -> None:
    text = f"{FAKE_JWT} {FAKE_LIVE} {FAKE_TEST} {FAKE_MAIL} 192.0.2.10"
    assert find_leaks(text) == ["api_key_live", "api_key_test", "bearer_token", "email", "ip"]
    assert find_leaks(mask_text(text)[0]) == []


# --- provider key formats ----------------------------------------------------------------

PROVIDER_KEYS = {
    "sk-": "sk-" + "proj-" + "a1B2c3D4e5F6g7H8i9J0k1L2",
    "gsk_": "gsk_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3",
    "AIza": "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5",
    "gh_": "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6",
}


def test_provider_keys_are_masked_and_keep_their_prefix() -> None:
    for prefix, key in PROVIDER_KEYS.items():
        out, counts = mask_text(f"key={key} end")
        assert out == f"key={prefix}<KEY> end", prefix
        assert counts == {"api_key_other": 1}, prefix
        assert find_leaks(f"key={key}") == ["api_key_other"], prefix
        assert find_leaks(out) == [], prefix


def test_mask_returns_only_the_text() -> None:
    assert mask(f"mail {FAKE_MAIL}") == "mail <EMAIL>"
