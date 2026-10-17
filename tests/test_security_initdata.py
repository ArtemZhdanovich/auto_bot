"""Юнит-тесты validate_init_data: канон + Telegram Desktop (decoded-signature) вариант."""

import hashlib
import hmac
from urllib.parse import quote

import pytest

from app.core.security import InitDataError, validate_init_data

BOT_TOKEN = "7000000000:AAHtesttoken_used_only_in_tests_XonfsQ"
USER = '{"id":777,"first_name":"Test"}'


def _sign(raw_pairs: dict[str, str], decoded: bool) -> str:
    values = {k: (quote(v) if decoded else v) for k, v in raw_pairs.items()}
    dcs = "\n".join(f"{k}={values[k]}" for k in sorted(values))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    return hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()


def _init_data(raw_pairs: dict[str, str], signed_decoded: bool) -> str:
    pairs = {k: quote(v) for k, v in raw_pairs.items()}
    pairs["hash"] = _sign(raw_pairs, decoded=signed_decoded)
    return "&".join(f"{k}={pairs[k]}" for k in pairs)


def _pairs() -> dict[str, str]:
    return {"auth_date": "1792253382", "query_id": "AAFtest", "user": USER}


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
