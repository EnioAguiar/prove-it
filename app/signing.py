"""Ed25519 signatures for receipts, so a verdict cannot be edited after the run.

The server signs the canonical JSON of every receipt field except `signature`. Anyone can check a
receipt with POST /api/verify, or offline with the public key from GET /api/public-key."""

import base64
import binascii
import hashlib
import json
import logging
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

log = logging.getLogger(__name__)

SIGNATURE_FIELD = "signature"


def canonical(payload: dict[str, Any]) -> bytes:
    """The exact bytes that are signed: sorted keys, no spaces, UTF-8."""
    fields = {k: v for k, v in payload.items() if k != SIGNATURE_FIELD}
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


class Signer:
    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._private = private_key
        self._public = private_key.public_key()
        raw = self._public.public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.public_key_b64 = base64.b64encode(raw).decode()
        self.key_id = hashlib.sha256(raw).hexdigest()[:16]

    @classmethod
    def from_seed_b64(cls, seed_b64: str | None) -> "Signer":
        """Key from SIGNING_KEY (base64 of a 32-byte seed). Without one, a new key is made and
        receipts stop verifying when the process restarts, so production must set it."""
        if not seed_b64:
            log.warning("SIGNING_KEY not set: using a temporary signing key")
            return cls(Ed25519PrivateKey.generate())
        return cls(Ed25519PrivateKey.from_private_bytes(base64.b64decode(seed_b64)))

    def sign(self, payload: dict[str, Any]) -> str:
        return base64.b64encode(self._private.sign(canonical(payload))).decode()

    def verify(self, payload: dict[str, Any]) -> bool:
        signature = payload.get(SIGNATURE_FIELD)
        if not isinstance(signature, str):
            return False
        try:
            self._public.verify(base64.b64decode(signature, validate=True), canonical(payload))
        except (InvalidSignature, binascii.Error, ValueError):
            return False
        return True


def new_seed_b64() -> str:
    """For `python -m app.signing`: print a fresh SIGNING_KEY value."""
    from cryptography.hazmat.primitives.serialization import NoEncryption, PrivateFormat

    raw = Ed25519PrivateKey.generate().private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return base64.b64encode(raw).decode()


if __name__ == "__main__":
    print(new_seed_b64())
