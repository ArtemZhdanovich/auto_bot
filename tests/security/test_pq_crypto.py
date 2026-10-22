"""Постквантовое шифрование документов: ML-KEM-768 + X25519 → HKDF → AES-GCM."""

import pytest

from app.infrastructure.security import pq_crypto as pq


@pytest.fixture(scope="module")
def keypair():
    return pq.generate_keypair()


def test_keypair_sizes(keypair):
    pub, priv = keypair
    # ML-KEM-768 public (1184) + X25519 (32); seed (64) + X25519 (32)
    assert len(bytes.fromhex(pub)) == pq._PQ_PUBLIC_SIZE + 32
    # version (1B) + seed (64B) + X25519 (32B)
    assert len(bytes.fromhex(priv)) == 1 + pq._PQ_SEED_SIZE + 32


def test_roundtrip(keypair):
    pub, priv = keypair
    plaintext = b"PTS/dogovor.pdf" * 1000
    blob = pq.encrypt_document(pub, plaintext, aad=b"doc:42")
    assert pq.is_pq_container(blob)
    assert pq.decrypt_document(priv, blob, aad=b"doc:42") == plaintext


def test_empty_and_small(keypair):
    pub, priv = keypair
    for msg in (b"", b"x", os_random(1)):
        blob = pq.encrypt_document(pub, msg)
        assert pq.decrypt_document(priv, blob) == msg


def os_random(n: int) -> bytes:
    import os

    return os.urandom(n)


def test_wrong_aad_rejected(keypair):
    pub, priv = keypair
    blob = pq.encrypt_document(pub, b"secret", aad=b"kb/faq.md")
    with pytest.raises(pq.PQCryptoError):
        pq.decrypt_document(priv, blob, aad=b"kb/other.md")


def test_wrong_key_rejected(keypair):
    pub, _ = keypair
    other_pub, _ = pq.generate_keypair()
    blob = pq.encrypt_document(other_pub, b"secret")
    _, priv = keypair
    with pytest.raises(pq.PQCryptoError):
        pq.decrypt_document(priv, blob)


def test_bad_magic(keypair):
    _, priv = keypair
    with pytest.raises(pq.PQCryptoError):
        pq.decrypt_document(priv, b"plain text not a container")


def test_container_is_incomprehensible_without_key(keypair):
    """Без приватного ключа контейнер не расшифровывается (даже с публичным)."""
    pub, _ = keypair
    blob = pq.encrypt_document(pub, b"secret")
    pub2, priv2 = pq.generate_keypair()
    assert pub != pub2
    with pytest.raises(pq.PQCryptoError):
        pq.decrypt_document(priv2, blob)
