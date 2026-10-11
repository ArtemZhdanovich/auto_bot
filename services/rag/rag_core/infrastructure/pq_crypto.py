"""Постквантовое шифрование документов at rest (гибрид PQX-style).

Схема (RFC-совместимые примитивы, NIST FIPS 203 / "harvest now, decrypt later"):
- KEM: ML-KEM-768 (Kyber, FIPS 203) + X25519 (классический гибрид — устойчиво
  и к взлому решёток, и к будущему компрометанию ML-KEM);
- вывод ключа: HKDF-SHA256 по конкатенации обоих общих секретов;
- AEAD: AES-256-GCM (каждый документ — свой эпемеральный ключ и nonce).

Формат контейнера (v1):
    MAGIC "AUPQ1" | version=1 | x25519_pub (32B) | kem_ct (2B len + bytes)
    | nonce (12B) | AES-256-GCM(ciphertext + tag)

Ключи передаются hex-строками сырых байт (env: PQ_ENC_PUBLIC_KEY /
PQ_ENC_PRIVATE_KEY; приватный ключ — 64-байтовый seed ML-KEM + 32 байта
X25519). Генерация: ``generate_keypair()``.
"""

from __future__ import annotations

import os
import struct

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
_PQ_PUBLIC_SIZE = 1184  # ML-KEM-768 public key (FIPS 203)
_PQ_SEED_SIZE = 64  # ML-KEM-768 private key stored as 64-byte seed
_X25519_SIZE = 32
_INFO = b"autoselect/pq-hybrid-document/v1"


class PQCryptoError(ValueError):
    """Некорректный контейнер, ключ или версия формата."""


def generate_keypair() -> tuple[str, str]:
    """Сгенерировать пару ключей (public_hex, private_hex) для env."""
    pq_private = MLKEM768PrivateKey.generate()
    classical_private = X25519PrivateKey.generate()
    return (
        pq_private.public_key().public_bytes_raw().hex()
        + classical_private.public_key().public_bytes_raw().hex(),
        pq_private.private_bytes_raw().hex()
        + classical_private.private_bytes_raw().hex(),
    )


def _split_public(public_hex: str) -> tuple[MLKEM768PublicKey, X25519PublicKey]:
    raw = bytes.fromhex(public_hex)
    if len(raw) != _PQ_PUBLIC_SIZE + _X25519_SIZE:
        raise PQCryptoError(
            f"Public key must be {_PQ_PUBLIC_SIZE + _X25519_SIZE} bytes "
            f"(ML-KEM-768 + X25519), got {len(raw)}"
        )
    return MLKEM768PublicKey.from_public_bytes(raw[:_PQ_PUBLIC_SIZE]), (
        X25519PublicKey.from_public_bytes(raw[_PQ_PUBLIC_SIZE:])
    )


def _split_private(private_hex: str) -> tuple[MLKEM768PrivateKey, X25519PrivateKey]:
    raw = bytes.fromhex(private_hex)
    if len(raw) != _PQ_SEED_SIZE + _X25519_SIZE:
        raise PQCryptoError(
            f"Private key must be {_PQ_SEED_SIZE + _X25519_SIZE} bytes "
            f"(ML-KEM-768 64-byte seed + X25519), got {len(raw)}"
        )
    return MLKEM768PrivateKey.from_seed_bytes(raw[:_PQ_SEED_SIZE]), (
        X25519PrivateKey.from_private_bytes(raw[_PQ_SEED_SIZE:])
    )


def _derive_key(mlkem_shared: bytes, x25519_shared: bytes, salt: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=_INFO,
    ).derive(mlkem_shared + x25519_shared)


def encrypt_document(public_key_hex: str, plaintext: bytes, aad: bytes = b"") -> bytes:
    """Зашифровать документ гибридным PQ-контейнером."""
    pq_public, classical_public = _split_public(public_key_hex)
    kem_a, kem_b = pq_public.encapsulate()
    # Порядок кортежа зависит от версии cryptography: различаем по размерам
    # (shared secret ML-KEM-768 = 32B, ciphertext = 1088B).
    mlkem_shared, kem_ct = (kem_a, kem_b) if len(kem_a) == 32 else (kem_b, kem_a)
    eph = X25519PrivateKey.generate()
    x25519_shared = eph.exchange(classical_public)
    nonce = os.urandom(_NONCE_SIZE)
    salt = bytes([_VERSION]) + eph.public_key().public_bytes_raw() + kem_ct
    key = _derive_key(mlkem_shared, x25519_shared, salt)
    body = AESGCM(key).encrypt(nonce, plaintext, aad or None)
    return (
        MAGIC
        + bytes([_VERSION])
        + eph.public_key().public_bytes_raw()
        + struct.pack("<H", len(kem_ct))
        + kem_ct
        + nonce
        + body
    )


def decrypt_document(private_key_hex: str, blob: bytes, aad: bytes = b"") -> bytes:
    """Расшифровать PQ-контейнер документа."""
    header = len(MAGIC) + 1 + 32 + 2
    if len(blob) < header or blob[: len(MAGIC)] != MAGIC:
        raise PQCryptoError("Not a PQ document container (bad magic)")
    if blob[len(MAGIC)] != _VERSION:
        raise PQCryptoError(f"Unsupported container version {blob[len(MAGIC)]}")
    offset = len(MAGIC) + 1
    eph_pub = X25519PublicKey.from_public_bytes(blob[offset : offset + 32])
    offset += 32
    (kem_len,) = struct.unpack("<H", blob[offset : offset + 2])
    offset += 2
    kem_ct = blob[offset : offset + kem_len]
    offset += kem_len
    nonce, body = blob[offset : offset + _NONCE_SIZE], blob[offset + _NONCE_SIZE :]

    pq_private, classical_private = _split_private(private_key_hex)
    mlkem_shared = pq_private.decapsulate(kem_ct)
    x25519_shared = classical_private.exchange(eph_pub)
    eph_pub_raw = blob[len(MAGIC) + 1 : len(MAGIC) + 1 + 32]
    salt = bytes([_VERSION]) + eph_pub_raw + kem_ct  # как при шифровании
    key = _derive_key(mlkem_shared, x25519_shared, salt)
    try:
        return AESGCM(key).decrypt(nonce, body, aad or None)
    except Exception as exc:
        raise PQCryptoError("Decryption failed (wrong key, AAD or corrupted data)") from exc


def is_pq_container(blob: bytes) -> bool:
    """Проверить, является ли blob PQ-контейнером."""
    return blob[: len(MAGIC)] == MAGIC
