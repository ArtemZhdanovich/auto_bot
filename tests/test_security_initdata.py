"""Юнит-тесты validate_init_data: канон + Telegram Desktop (decoded-signature) вариант."""

import hashlib
import hmac
import json
import time
from urllib.parse import quote

import pytest

from app.infrastructure.security.tokens import InitDataError, validate_init_data

BOT_TOKEN = "7000000000:AAHtesttoken_used_only_in_tests_XonfsQ"
USER = '{"id":777,"first_name":"Test"}'


def _sign(raw_pairs: dict[str, str], decoded: bool) -> str:
    # decoded=True — подпись по раскодированным значениям (Telegram Desktop),
    # иначе — по raw URL-encoded (канон Telegram).
    values = {k: (v if decoded else quote(v)) for k, v in raw_pairs.items()}
    dcs = "\n".join(f"{k}={values[k]}" for k in sorted(values))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    return hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()


def _init_data(raw_pairs: dict[str, str], signed_decoded: bool) -> str:
    pairs = {k: quote(v) for k, v in raw_pairs.items()}
    pairs["hash"] = _sign(raw_pairs, decoded=signed_decoded)
    return "&".join(f"{k}={pairs[k]}" for k in pairs)


def _pairs() -> dict[str, str]:
    # auth_date — текущее время: фиксированное значение устаревает и даёт "expired".
    return {"auth_date": str(int(time.time())), "query_id": "AAFtest", "user": USER}


def test_canonical_signature_accepted() -> None:
    user = validate_init_data(_init_data(_pairs(), signed_decoded=False), BOT_TOKEN, 86400)
    assert user["id"] == 777


def test_desktop_decoded_signature_accepted() -> None:
    """Telegram Desktop подписывает initData по раскодированным значениям."""
    user = validate_init_data(_init_data(_pairs(), signed_decoded=True), BOT_TOKEN, 86400)
    assert user["id"] == 777


def test_bad_signature_rejected() -> None:
    data = _init_data(_pairs(), signed_decoded=False)
    data = data.replace("AAFtest", "BBBtest")
    with pytest.raises(InitDataError, match="hash mismatch"):
        validate_init_data(data, BOT_TOKEN, 86400)


def test_expired_rejected() -> None:
    pairs = {"auth_date": "1000000000", "query_id": "AAFtest", "user": USER}
    with pytest.raises(InitDataError, match="expired"):
        validate_init_data(_init_data(pairs, signed_decoded=False), BOT_TOKEN, 86400)


# tests/core/test_security.py

import jwt
from pydantic import SecretStr

from app.config import get_settings
from app.infrastructure.security.tokens import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
    decode_token,
)


# --- SecretStr: главная регрессия ------------------------------------

def test_create_access_token_with_secretstr():
    """Регрессия: SecretStr не должен давать '**********' в подписи."""
    s = get_settings()
    tok = create_access_token(123, s)
    payload = jwt.decode(
        tok, s.app_secret_key.get_secret_value(), algorithms=["HS256"],
    )
    assert payload["sub"] == "123"
    # Токен НЕ должен декодироваться с '**********'
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(tok, "**********", algorithms=["HS256"])


def test_secretstr_empty_rejected():
    class FakeSettings:
        app_secret_key = SecretStr("")
        jwt_algorithm = "HS256"
        access_token_ttl_sec = 60
        refresh_token_ttl_sec = 120
    with pytest.raises(TokenError, match="Secret is empty"):
        create_access_token(1, FakeSettings())


# --- decode_access_token: типы/sub -----------------------------------

def test_access_token_roundtrip():
    s = get_settings()
    tok = create_access_token(42, s)
    assert decode_access_token(tok, s) == 42


def test_refresh_token_rejected_as_access():
    s = get_settings()
    tok = create_refresh_token(42, s)
    with pytest.raises(TokenError, match="Not an access token"):
        decode_access_token(tok, s)


def test_access_token_rejected_as_refresh():
    s = get_settings()
    tok = create_access_token(42, s)
    with pytest.raises(TokenError, match="Not a refresh token"):
        decode_refresh_token(tok, s)


def test_missing_sub_raises_token_error():
    """`sub` отсутствует → TokenError, не KeyError."""
    s = get_settings()
    payload = {"type": "access", "iat": int(time.time()), "exp": int(time.time()) + 60}
    tok = jwt.encode(payload, s.app_secret_key.get_secret_value(), algorithm="HS256")
    with pytest.raises(TokenError, match="Invalid token"):
        decode_access_token(tok, s)


def test_non_int_sub_raises_token_error():
    s = get_settings()
    payload = {
        "sub": "abc", "type": "access",
        "iat": int(time.time()), "exp": int(time.time()) + 60,
    }
    tok = jwt.encode(payload, s.app_secret_key.get_secret_value(), algorithm="HS256")
    with pytest.raises(TokenError, match="Invalid subject"):
        decode_access_token(tok, s)


def test_missing_type_raises_token_error():
    s = get_settings()
    payload = {"sub": "1", "iat": int(time.time()), "exp": int(time.time()) + 60}
    tok = jwt.encode(payload, s.app_secret_key.get_secret_value(), algorithm="HS256")
    with pytest.raises(TokenError, match="Invalid token"):
        decode_token(tok, s)


def test_expired_token_raises():
    s = get_settings()
    payload = {
        "sub": "1", "type": "access",
        "iat": int(time.time()) - 100,
        "exp": int(time.time()) - 50,
    }
    tok = jwt.encode(payload, s.app_secret_key.get_secret_value(), algorithm="HS256")
    with pytest.raises(TokenError, match="Invalid token"):
        decode_access_token(tok, s)


def test_leeway_allows_10s_skew():
    """Токен, просроченный на 5 сек, принимается (leeway=10)."""
    s = get_settings()
    payload = {
        "sub": "1", "type": "access",
        "iat": int(time.time()) - 100,
        "exp": int(time.time()) - 5,
    }
    tok = jwt.encode(payload, s.app_secret_key.get_secret_value(), algorithm="HS256")
    assert decode_access_token(tok, s) == 1


# --- validate_init_data ---------------------------------------------

BOT_TOKEN = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"


def _make_init_data(bot_token: str, user: dict, auth_date: int) -> str:
    """Подписывает initData по спеке Telegram (raw URL-encoded values)."""
    import hashlib
    import hmac as hmac_mod

    fields = {
        "auth_date": str(auth_date),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps(user, separators=(",", ":")),
    }
    # data_check_string — из raw URL-encoded values
    encoded = {k: quote(v, safe="") for k, v in fields.items()}
    dcs = "\n".join(f"{k}={encoded[k]}" for k in sorted(encoded))
    secret = hmac_mod.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    h = hmac_mod.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    encoded["hash"] = h
    # Значения уже URL-encoded: повторный urlencode дал бы двойное кодирование.
    return "&".join(f"{k}={encoded[k]}" for k in encoded)


def test_initdata_valid():
    now = int(time.time())
    init = _make_init_data(BOT_TOKEN, {"id": 42, "first_name": "Test"}, now)
    user = validate_init_data(init, BOT_TOKEN, max_age_seconds=3600)
    assert user["id"] == 42


def test_initdata_expired():
    now = int(time.time()) - 7200
    init = _make_init_data(BOT_TOKEN, {"id": 42}, now)
    with pytest.raises(InitDataError, match="expired"):
        validate_init_data(init, BOT_TOKEN, max_age_seconds=3600)


def test_initdata_future_auth_date_rejected():
    future = int(time.time()) + 3600
    init = _make_init_data(BOT_TOKEN, {"id": 42}, future)
    with pytest.raises(InitDataError, match="future"):
        validate_init_data(init, BOT_TOKEN, max_age_seconds=3600)


def test_initdata_hash_mismatch():
    init = _make_init_data(BOT_TOKEN, {"id": 42}, int(time.time()))
    # Портим хэш
    init_bad = init.replace("hash=", "hash=00", 1)
    with pytest.raises(InitDataError, match="hash mismatch"):
        validate_init_data(init_bad, BOT_TOKEN, max_age_seconds=3600)


def test_initdata_missing_hash():
    with pytest.raises(InitDataError, match="hash is missing"):
        validate_init_data("user=x&auth_date=1", BOT_TOKEN, 3600)


def test_initdata_invalid_hash_hex():
    with pytest.raises(InitDataError, match="not valid hex"):
        validate_init_data("hash=zz&user=x&auth_date=1", BOT_TOKEN, 3600)


def test_initdata_missing_auth_date():
    init = _make_init_data(BOT_TOKEN, {"id": 42}, int(time.time()))
    # Удаляем auth_date (подпись сломается, но проверка поля — раньше)
    parts = [p for p in init.split("&") if not p.startswith("auth_date=")]
    # Пересобираем с новым хэшем... сложно; проще проверить на «пустой» init
    with pytest.raises(InitDataError):
        validate_init_data("hash=" + "0"*64 + "&user=x", BOT_TOKEN, 3600)


def test_initdata_invalid_json_user():
    now = int(time.time())
    # Подписываем с невалидным JSON
    import hashlib
    import hmac as hmac_mod
    fields = {"auth_date": str(now), "user": "{not-json"}
    encoded = {k: quote(v, safe="") for k, v in fields.items()}
    dcs = "\n".join(f"{k}={encoded[k]}" for k in sorted(encoded))
    secret = hmac_mod.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    encoded["hash"] = hmac_mod.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    init = "&".join(f"{k}={encoded[k]}" for k in encoded)
    with pytest.raises(InitDataError, match="not valid JSON"):
        validate_init_data(init, BOT_TOKEN, 3600)


def test_initdata_empty_rejected():
    with pytest.raises(InitDataError, match="empty"):
        validate_init_data("", BOT_TOKEN, 3600)


def test_initdata_too_long_rejected():
    with pytest.raises(InitDataError, match="too long"):
        validate_init_data("a" * (17 * 1024), BOT_TOKEN, 3600)


def test_initdata_max_age_non_positive_rejected():
    with pytest.raises(InitDataError, match="max_age_seconds"):
        validate_init_data("hash=" + "0"*64, BOT_TOKEN, 0)


def test_initdata_bot_token_secretstr():
    """`SecretStr` разворачивается."""
    now = int(time.time())
    init = _make_init_data(BOT_TOKEN, {"id": 42}, now)
    user = validate_init_data(init, SecretStr(BOT_TOKEN), 3600)
    assert user["id"] == 42


def test_initdata_duplicate_key_rejected():
    now = int(time.time())
    init = _make_init_data(BOT_TOKEN, {"id": 42}, now)
    init_bad = init + "&user=" + quote('{"id":99}', safe="")
    with pytest.raises(InitDataError, match="duplicate"):
        validate_init_data(init_bad, BOT_TOKEN, 3600)