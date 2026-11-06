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
import secrets
import stat
from pathlib import Path
from typing import Any

from studio.receipts import canonical

KEY_FILE = "receipt-hmac.key"
KEY_BYTES = 32


def receipt_key(data_dir: Path) -> bytes:
    """The 32-byte key in ``<data_dir>/receipt-hmac.key``, created (mode 0600) if missing.

    Creation is atomic in content as well as mode: the bytes are written and synced to a
    private temp file, which is hard-linked to the final name (``os.link`` fails if it
    exists), so no reader or crash ever leaves a partial key. A file of the wrong size is
    an operator error and is reported, never overwritten."""
    path = Path(data_dir) / KEY_FILE
    try:
        key = path.read_bytes()
    except FileNotFoundError:
        key = _create(path)
    if len(key) != KEY_BYTES:
        raise RuntimeError(f"{path} must hold exactly {KEY_BYTES} bytes (found {len(key)}): "
                           "remove it only if no receipt hash needs to stay comparable")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise RuntimeError(f"{path}: permissions {oct(mode)} too open for the receipt key (600)")
    return key


def _create(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{KEY_FILE}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(os.urandom(KEY_BYTES))
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError:  # another process won: its file is complete
            pass
        return path.read_bytes()
    finally:
        tmp.unlink(missing_ok=True)


def keyed_sha256(key: bytes, answer: Any, chain_id: str) -> str:
    """HMAC-SHA256 (hex) over ``chain_id``, a NUL and the canonical JSON of ``answer``.
    Bound to the receipt's chain, so two accounts that got the same answer (both empty,
    say) do not show equal hashes on the public receipts page."""
    return hmac.new(key, chain_id.encode() + b"\x00" + canonical(answer),
                    hashlib.sha256).hexdigest()
