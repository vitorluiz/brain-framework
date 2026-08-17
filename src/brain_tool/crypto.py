"""Assinatura Ed25519 e gestão de chaves — âncora de confiança fora do banco.

A chave privada **nunca** vive no brain.db: quem altera o banco não consegue
reassinar os commits. Padrão (decisão aprovada):

- `BRAIN_SIGNING_KEY` / `BRAIN_SIGNING_KEY_PUB` (base64) sobrepõem o disco.
- Caso contrário, gera o par no **primeiro uso** e persiste em
  `$BRAIN_ROOT/.signing/ed25519.key` (0600) + `ed25519.pub`.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

_KEY_ID = "default"  # v1: chave única (rotação fica para fases futuras)


def _signing_dir() -> Path:
    root = os.environ.get("BRAIN_ROOT") or str(Path.home() / ".hermes" / "brain")
    return Path(root) / ".signing"


def _private_path() -> Path:
    return _signing_dir() / "ed25519.key"


def _public_path() -> Path:
    return _signing_dir() / "ed25519.pub"


def _b64decode(raw: str) -> bytes:
    return base64.b64decode(raw, validate=True)


def _b64encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def key_id() -> str:
    return _KEY_ID


def load_or_create_signing_key() -> Ed25519PrivateKey:
    """Devolve a chave privada para assinar (gera no primeiro uso)."""
    env = os.environ.get("BRAIN_SIGNING_KEY")
    if env:
        return Ed25519PrivateKey.from_private_bytes(_b64decode(env))

    priv_path = _private_path()
    if priv_path.exists():
        return Ed25519PrivateKey.from_private_bytes(priv_path.read_bytes())

    key = Ed25519PrivateKey.generate()
    _signing_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
    priv_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
    )
    os.chmod(priv_path, 0o600)
    pub_path = _public_path()
    pub_path.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    )
    os.chmod(pub_path, 0o644)
    return key


def load_public_key() -> Optional[Ed25519PublicKey]:
    """Âncora de confiança para verificação. None se nenhuma chave configurada.

    Nunca gera chave: verificar contra uma chave recém-gerada não prova nada.
    """
    env = os.environ.get("BRAIN_SIGNING_KEY_PUB")
    if env:
        return Ed25519PublicKey.from_public_bytes(_b64decode(env))

    pub_path = _public_path()
    if pub_path.exists():
        return Ed25519PublicKey.from_public_bytes(pub_path.read_bytes())

    priv_path = _private_path()
    if priv_path.exists():
        return Ed25519PrivateKey.from_private_bytes(priv_path.read_bytes()).public_key()

    return None


def generate_keypair() -> tuple[str, str]:
    """Gera um par novo (priv, pub) em base64 — para bootstrap/setup manual."""
    key = Ed25519PrivateKey.generate()
    priv = _b64encode(
        key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
    )
    pub = _b64encode(
        key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
    )
    return priv, pub


def sign(key: Ed25519PrivateKey, data: bytes) -> bytes:
    return key.sign(data)


def verify(pub: Ed25519PublicKey, signature: bytes, data: bytes) -> bool:
    try:
        pub.verify(signature, data)
        return True
    except Exception:
        return False
