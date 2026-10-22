"""Постквантовое шифрование документов at rest (гибрид PQX-style).

Схема (RFC-совместимые примитивы, NIST FIPS 203 / "harvest now, decrypt later"):
- KEM: ML-KEM-768 (Kyber, FIPS 203) + X25519 (классический гибрид — устойчив
  и к взлому решёток, и к будущему компрометанию ML-KEM);
- вывод ключа: HKDF-SHA256 по конкатенации обоих общих секретов;
- AEAD: AES-256-GCM (каждый документ — свой эпемеральный ключ и nonce).

Формат контейнера (v1):
    MAGIC "AUPQ1" | version=1 | x25519_pub (32B) | kem_ct (2B len + bytes)
    | nonce (12B) | AES-256-GCM(ciphertext + tag)

Ключи передаются hex-строками сырых байт (env: PQ_ENC_PUBLIC_KEY /
PQ_ENC_PRIVATE_KEY). Формат приватного ключа (v1):
    version (1B = 0x01) | ML-KEM-768 seed (64B) | X25519 (32B)

`cryptography` 50+ возвращает из `private_bytes_raw()` 64-байтовый seed
(`d || z`), а не полный FIPS 203 `dk` (2400B). Оба формата принимаются на
чтение: старые ключи с полным `dk` продолжают работать, новые хранятся
компактно (97 байт вместо 2433).

Генерация: ``generate_keypair()``.
"""

from __future__ import annotations

import os
import struct
from typing import NamedTuple

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.mlkem import (
    MLKEM768PrivateKey,
    MLKEM768PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"AUPQ1"
_VERSION = 1
_NONCE_SIZE = 12

# ML-KEM-768 (FIPS 203).
_PQ_PUBLIC_SIZE = 1184  # ek
_PQ_DK_SIZE = 2400      # dk (полный, не seed)
_PQ_CT_SIZE = 1088      # ciphertext из encapsulate()
_PQ_SHARED_SIZE = 32    # shared secret
_PQ_SEED_SIZE = 64      # seed ML-KEM (d || z), cryptography >= 50

_X25519_SIZE = 32
# TECHDEBT 12.18: info-строка привязана к версии контейнера — после
# появления v2 (другой KEM/шейфры) старые контейнеры останутся
# расшифровываемыми: каждая версия деривирует свой ключ.
_INFO_TEMPLATE = "autoselect/pq-hybrid-document/v{version}"


def _info_for_version(version: int) -> bytes:
    return _INFO_TEMPLATE.format(version=version).encode()

# Приватный ключ v1: version | (seed | dk) | x25519.
_PRIVATE_V1_SIZE = 1 + _PQ_DK_SIZE + _X25519_SIZE
_PRIVATE_SEED_V1_SIZE = 1 + _PQ_SEED_SIZE + _X25519_SIZE
# Публичный ключ: ek | x25519.
_PUBLIC_SIZE = _PQ_PUBLIC_SIZE + _X25519_SIZE

_MAX_BLOB_SIZE = 100 * 1024 * 1024  # 100 MB — защита от DoS при decrypt


class PQCryptoError(ValueError):
    """Некорректный контейнер, ключ или версия формата."""


class Keypair(NamedTuple):
    """Пара ключей (публичный, приватный) — hex-строки для env."""

    public_hex: str
    private_hex: str


def generate_keypair() -> Keypair:
    """Сгенерировать пару ключей для env.

    Приватный ключ — полный `dk` ML-KEM-768 (2400B) + X25519 (32B),
    с version-префиксом. Публичный — `ek` (1184B) + X25519 (32B).

    Returns:
        `Keypair` с hex-строками.
    """
    pq_private = MLKEM768PrivateKey.generate()
    classical_private = X25519PrivateKey.generate()
    dk = pq_private.private_bytes_raw()
    if len(dk) not in (_PQ_SEED_SIZE, _PQ_DK_SIZE):
        # Защита от смены API: ожидаем либо seed (cryptography >= 50),
        # либо полный FIPS 203 dk (старые версии).
        raise PQCryptoError(
            f"MLKEM768PrivateKey.private_bytes_raw() returned {len(dk)} bytes, "
            f"expected {_PQ_SEED_SIZE} (seed) or {_PQ_DK_SIZE} (full dk). "
            f"This likely means the cryptography version changed its API; "
            f"update generate_keypair() and _split_private() together."
        )
    private_blob = (
        bytes([_VERSION])
        + dk
        + classical_private.private_bytes_raw()
    )
    public_blob = (
        pq_private.public_key().public_bytes_raw()
        + classical_private.public_key().public_bytes_raw()
    )
    return Keypair(public_hex=public_blob.hex(), private_hex=private_blob.hex())


def _split_public(public_hex: str) -> tuple[MLKEM768PublicKey, X25519PublicKey]:
    try:
        raw = bytes.fromhex(public_hex)
    except ValueError as exc:
        raise PQCryptoError(f"Public key is not valid hex: {exc}") from exc
    if len(raw) != _PUBLIC_SIZE:
        raise PQCryptoError(
            f"Public key must be {_PUBLIC_SIZE} bytes "
            f"(ML-KEM-768 ek + X25519), got {len(raw)}"
        )
    try:
        return (
            MLKEM768PublicKey.from_public_bytes(raw[:_PQ_PUBLIC_SIZE]),
            X25519PublicKey.from_public_bytes(raw[_PQ_PUBLIC_SIZE:]),
        )
    except ValueError as exc:
        raise PQCryptoError(f"Invalid public key: {exc}") from exc


def _split_private(
    private_hex: str,
) -> tuple[MLKEM768PrivateKey, X25519PrivateKey]:
    try:
        raw = bytes.fromhex(private_hex)
    except ValueError as exc:
        raise PQCryptoError(f"Private key is not valid hex: {exc}") from exc
    if len(raw) not in (_PRIVATE_V1_SIZE, _PRIVATE_SEED_V1_SIZE):
        raise PQCryptoError(
            f"Private key must be {_PRIVATE_SEED_V1_SIZE} bytes (seed) "
            f"or {_PRIVATE_V1_SIZE} bytes (full dk), got {len(raw)}"
        )
    if raw[0] != _VERSION:
        raise PQCryptoError(f"Unsupported private key version {raw[0]}")
    body = raw[1:]
    pq_size = _PQ_SEED_SIZE if len(raw) == _PRIVATE_SEED_V1_SIZE else _PQ_DK_SIZE
    dk_bytes = body[:pq_size]
    x25519_bytes = body[pq_size:]
    try:
        pq = _mlkem_private_from_dk(dk_bytes)
        classical = X25519PrivateKey.from_private_bytes(x25519_bytes)
    except ValueError as exc:
        raise PQCryptoError(f"Invalid private key: {exc}") from exc
    return pq, classical


def _mlkem_private_from_dk(dk_bytes: bytes) -> MLKEM768PrivateKey:
    """Восстанавливает MLKEM768PrivateKey из полного `dk`.

    `cryptography` не предоставляет `from_private_bytes` для ML-KEM;
    единственный путь — seed (64B). Если `dk` содержит seed внутри
    (первые 64 байта — `d || z`, как в FIPS 203 Algorithm 7: dk
    начинается с `d` (32B) + `z` (32B) + ...), то `from_seed_bytes`
    восстановит тот же ключ.

    Внимание: это верно **только** если `cryptography` реализует
    `dk` в порядке FIPS 203 (`d || z || ...`). В противном случае
    нужно либо хранить seed отдельно, либо использовать HSM.

    Проверить: `decrypt_document(encrypt_document(pub, msg), priv) == msg`.
    """
    # seed (64B) — это уже `d || z`; полный dk начинается с тех же байт
    # (FIPS 203 Algorithm 7).
    seed = dk_bytes[:_PQ_SEED_SIZE]
    try:
        return MLKEM768PrivateKey.from_seed_bytes(seed)
    except Exception as exc:  # noqa: BLE001
        raise PQCryptoError(
            f"MLKEM768PrivateKey.from_seed_bytes failed; cryptography "
            f"version likely incompatible with this storage format: {exc}"
        ) from exc


def _derive_key(
    mlkem_shared: bytes, x25519_shared: bytes, salt: bytes, version: int = _VERSION
) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=_info_for_version(version),
    ).derive(mlkem_shared + x25519_shared)


def encrypt_document(
    public_key_hex: str, plaintext: bytes, aad: bytes = b""
) -> bytes:
    """Зашифровать документ гибридным PQ-контейнером.

    Args:
        public_key_hex: Публичный ключ (hex, 1216 байт).
        plaintext: Открытые данные.
        aad: Associated data (аутентифицируется, не шифруется).

    Returns:
        Контейнер `MAGIC | v | eph_pub | kem_ct | nonce | body`.
    """
    pq_public, classical_public = _split_public(public_key_hex)

    try:
        kem_a, kem_b = pq_public.encapsulate()
    except Exception as exc:  # noqa: BLE001
        raise PQCryptoError(f"ML-KEM encapsulate failed: {exc}") from exc

    # Порядок в возвращаемом кортеже зависит от версии cryptography.
    # Для ML-KEM-768: shared_key = 32B, ciphertext = 1088B.
    if len(kem_a) == _PQ_SHARED_SIZE and len(kem_b) == _PQ_CT_SIZE:
        mlkem_shared, kem_ct = kem_a, kem_b
    elif len(kem_a) == _PQ_CT_SIZE and len(kem_b) == _PQ_SHARED_SIZE:
        mlkem_shared, kem_ct = kem_b, kem_a
    else:
        raise PQCryptoError(
            f"Unexpected encapsulate() return sizes: {len(kem_a)}, {len(kem_b)}"
        )

    eph = X25519PrivateKey.generate()
    try:
        x25519_shared = eph.exchange(classical_public)
    except ValueError as exc:
        raise PQCryptoError(f"X25519 exchange failed: {exc}") from exc

    nonce = os.urandom(_NONCE_SIZE)
    eph_pub = eph.public_key().public_bytes_raw()
    salt = bytes([_VERSION]) + eph_pub + kem_ct
    key = _derive_key(mlkem_shared, x25519_shared, salt)
    body = AESGCM(key).encrypt(nonce, plaintext, aad)
    return (
        MAGIC
        + bytes([_VERSION])
        + eph_pub
        + struct.pack("<H", len(kem_ct))
        + kem_ct
        + nonce
        + body
    )


def decrypt_document(
    private_key_hex: str, blob: bytes, aad: bytes = b""
) -> bytes:
    """Расшифровать PQ-контейнер документа.

    Args:
        private_key_hex: Приватный ключ (hex, 2433 байта).
        blob: Контейнер.
        aad: Associated data (должен совпадать с encrypt).

    Returns:
        Открытые данные.

    Raises:
        PQCryptoError: При любом некорректном контейнере/ключе.
    """
    if len(blob) > _MAX_BLOB_SIZE:
        raise PQCryptoError(f"Container too large: {len(blob)} bytes")
    header = len(MAGIC) + 1 + _X25519_SIZE + 2
    if len(blob) < header or blob[: len(MAGIC)] != MAGIC:
        raise PQCryptoError("Not a PQ document container (bad magic)")
    if blob[len(MAGIC)] != _VERSION:
        raise PQCryptoError(f"Unsupported container version {blob[len(MAGIC)]}")

    offset = len(MAGIC) + 1
    try:
        eph_pub = X25519PublicKey.from_public_bytes(blob[offset : offset + 32])
    except ValueError as exc:
        raise PQCryptoError(f"Invalid ephemeral public key: {exc}") from exc
    offset += _X25519_SIZE
    try:
        (kem_len,) = struct.unpack("<H", blob[offset : offset + 2])
    except struct.error as exc:
        raise PQCryptoError(f"Truncated container header: {exc}") from exc
    offset += 2
    if kem_len != _PQ_CT_SIZE:
        raise PQCryptoError(
            f"Invalid ML-KEM ciphertext length {kem_len}, "
            f"expected {_PQ_CT_SIZE}"
        )
    if len(blob) < offset + kem_len + _NONCE_SIZE:
        raise PQCryptoError("Truncated container (not enough bytes)")
    kem_ct = blob[offset : offset + kem_len]
    offset += kem_len
    nonce = blob[offset : offset + _NONCE_SIZE]
    offset += _NONCE_SIZE
    body = blob[offset:]

    pq_private, classical_private = _split_private(private_key_hex)
    try:
        mlkem_shared = pq_private.decapsulate(kem_ct)
    except Exception as exc:  # noqa: BLE001
        raise PQCryptoError(f"ML-KEM decapsulate failed: {exc}") from exc
    try:
        x25519_shared = classical_private.exchange(eph_pub)
    except ValueError as exc:
        raise PQCryptoError(f"X25519 exchange failed: {exc}") from exc

    eph_pub_raw = blob[len(MAGIC) + 1 : len(MAGIC) + 1 + _X25519_SIZE]
    container_version = blob[len(MAGIC)]  # 12.18: ключ деривируется версией контейнера
    salt = bytes([container_version]) + eph_pub_raw + kem_ct
    key = _derive_key(mlkem_shared, x25519_shared, salt, version=container_version)
    try:
        return AESGCM(key).decrypt(nonce, body, aad)
    except Exception as exc:  # noqa: BLE001
        raise PQCryptoError(
            "Decryption failed (wrong key, AAD or corrupted data)"
        ) from exc


def is_pq_container(blob: bytes) -> bool:
    """Проверить, является ли blob PQ-контейнером поддерживаемой версии.

    Args:
        blob: Проверяемые данные.

    Returns:
        `True`, если magic совпадает и версия поддерживается.
    """
    if len(blob) < len(MAGIC) + 1:
        return False
    if blob[: len(MAGIC)] != MAGIC:
        return False
    return blob[len(MAGIC)] == _VERSION