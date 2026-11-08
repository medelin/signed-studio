"""Proof for anyone: what ``/trust`` and ``/verify/<hash>`` show without a session.

``Trust`` keeps two small caches: the ledger's public numbers (60 s) and the result of a
self-verification (10 min) that runs the same ``studio.receipts.verify`` that
``studio verify`` runs, on the ledger's own export, in-process. Nothing here returns receipt
contents: only keys, counts, hashes and pass/fail.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from studio.receipts import Ledger, Receipt, public_key_raw, verify

STATS_TTL_S = 60.0
VERIFY_TTL_S = 600.0
SUMMARY = Path(__file__).resolve().parents[1] / "docs" / "eval-summary.json"
HEX = frozenset("0123456789abcdef")


@dataclass
class SelfCheck:
    at: float
    ok: bool
    receipts: int
    signatures: int
    money_rules_ok: bool  # no problem names P-V1…P-V6
    problems: int
    seq: int = 0
    errors: list[str] = field(default_factory=list)  # first few, ids only (never contents)


def eval_summary(path: Path = SUMMARY) -> dict[str, Any] | None:
    """The test counts ``scripts/eval_summary.py`` wrote, or None when not shipped."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    ok = isinstance(data, dict) and all(isinstance(data.get(k), int)
                                        for k in ("total_tests", "attack_tests"))
    return data if ok else None


def is_hash(value: str) -> bool:
    return len(value) == 64 and set(value) <= HEX


class Trust:
    def __init__(self, ledger: Ledger, approver_keys: Callable[[], Mapping[str, Any]],
                 clock: Callable[[], float] = time.time) -> None:
        self.ledger = ledger
        self._approvers = approver_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._stats: tuple[float, dict[str, Any]] | None = None
        self._check: SelfCheck | None = None
        self._index: tuple[int, dict[str, Receipt]] | None = None

    def key_hex(self) -> str:
        return public_key_raw(self.ledger.public_key).hex()

    def stats(self) -> dict[str, Any]:
        """Receipt count and the newest receipt's hash, live, cached for 60 s."""
        now = self._clock()
        with self._lock:
            if self._stats and now - self._stats[0] < STATS_TTL_S:
                return self._stats[1]
        receipts = self.ledger.receipts()
        data = {"count": len(receipts), "head": receipts[-1].hash if receipts else "",
                "seq": self.ledger.seq()}
        with self._lock:
            self._stats = (now, data)
        return data

    def self_check(self) -> SelfCheck:
        """``studio.receipts.verify`` over the ledger's export, at most once per 10 min."""
        now = self._clock()
        with self._lock:  # held while verifying: concurrent visitors share one run
            if self._check and now - self._check.at < VERIFY_TTL_S:
                return self._check
            seq, key = self.ledger.seq(), self.ledger.public_key
            approvers = dict(self._approvers())
            with tempfile.TemporaryDirectory(prefix="studio-trust-") as tmp:
                path = Path(tmp) / "receipts.jsonl"
                path.write_text(self.ledger.export_jsonl())
                report = verify(path, key, min_seq=seq, approver_keys=approvers)
            self._check = SelfCheck(
                at=now, ok=report.ok, receipts=report.count, signatures=report.signatures,
                money_rules_ok=not any("P-V" in e for e in report.errors),
                problems=len(report.errors), seq=seq,
                errors=[e.split(":")[0][:80] for e in report.errors[:5]])
            return self._check

    def find(self, digest: str) -> Receipt | None:
        """The receipt with this hash (an index rebuilt whenever the ledger grows)."""
        seq = self.ledger.seq()
        with self._lock:
            if self._index and self._index[0] == seq:
                return self._index[1].get(digest)
        index = {r.hash: r for r in self.ledger.receipts()}
        with self._lock:
            self._index = (seq, index)
        return index.get(digest)

    def signed(self, r: Receipt) -> bool:
        """The hash matches the receipt's fields and the ledger key signed that hash."""
        try:
            if r.compute_hash() != r.hash:
                return False
            pub: Ed25519PublicKey = self.ledger.public_key
            pub.verify(bytes.fromhex(r.sig), bytes.fromhex(r.hash))
        except (InvalidSignature, ValueError):
            return False
        return True
