"""The secret that keys the hashes on agent-read receipts.

Receipts are public. A plain SHA-256 of an agent's answer would let anyone confirm a
guessed low-entropy answer ("this account's dispute list is empty"), so those receipts
carry an HMAC-SHA256 under a key that never leaves the data directory: it is not
exported, not logged, not part of any answer.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
from typing import Any

from studio.receipts import canonical

KEY_FILE = "receipt-hmac.key"
KEY_BYTES = 32


def receipt_key(data_dir: Path) -> bytes:
    """The 32-byte key in ``<data_dir>/receipt-hmac.key``, created (mode 0600) if missing."""
    path = Path(data_dir) / KEY_FILE
    try:
        key = path.read_bytes()
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:  # another process won the race
            key = path.read_bytes()
        else:
            with os.fdopen(fd, "wb") as f:
                f.write(os.urandom(KEY_BYTES))
            key = path.read_bytes()
    if len(key) != KEY_BYTES:
        raise RuntimeError(f"{path} must hold exactly {KEY_BYTES} bytes")
    return key


def keyed_sha256(key: bytes, answer: Any) -> str:
    """HMAC-SHA256 (hex) over the canonical JSON of ``answer``."""
    return hmac.new(key, canonical(answer), hashlib.sha256).hexdigest()
