"""Receipts: a hash-chained provenance log for every agent step.

Each receipt names the agent, the model or tool, what went in and out (as hashes), the
cost, and the hash of the previous receipt in the same run. ``verify`` recomputes every
hash, checks the links and timestamps, and checks that every external write is preceded
by a human approval for exactly the same content.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

GENESIS = "0" * 64
KINDS = frozenset({"model_call", "tool_call", "approval", "external_write", "review"})

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d \-()]{7,}\d(?!\w)")


def canonical(obj: Any) -> bytes:
    """Deterministic JSON bytes: sorted keys, no whitespace, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def redact(value: Any) -> Any:
    """Replace e-mail addresses and phone numbers in any nested structure."""
    if isinstance(value, str):
        return _PHONE.sub("[phone]", _EMAIL.sub("[email]", value))
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


@dataclass(frozen=True)
class Receipt:
    id: str
    prev: str
    ts: float
    run_id: str
    agent: str
    kind: str
    provider: str = ""
    model: str = ""
    tier: str = ""
    tier_reason: str = ""
    prompt_sha256: str = ""
    input_sha256: list[str] = field(default_factory=list)
    output_sha256: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    envelope: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    hash: str = ""

    def body(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("hash")
        return data

    def compute_hash(self) -> str:
        return sha256_hex(canonical(self.body()))


class Ledger:
    """In-memory ledger; the database layer persists the same receipts."""

    def __init__(self) -> None:
        self._receipts: list[Receipt] = []
        self._heads: dict[str, str] = {}
        self._last_ts: dict[str, float] = {}
        self._lock = threading.Lock()

    def chain_head(self, run_id: str) -> str:
        return self._heads.get(run_id, GENESIS)

    def append(self, run_id: str, *, agent: str, kind: str, **fields: Any) -> Receipt:
        if kind not in KINDS:
            raise ValueError(f"unknown receipt kind: {kind!r}")
        details = redact(fields.pop("details", {}) or {})
        with self._lock:
            ts = max(time.time(), self._last_ts.get(run_id, 0.0))
            draft = Receipt(
                id=uuid.uuid4().hex,
                prev=self.chain_head(run_id),
                ts=ts,
                run_id=run_id,
                agent=agent,
                kind=kind,
                details=details,
                **fields,
            )
            receipt = Receipt(**{**asdict(draft), "hash": draft.compute_hash()})
            self._receipts.append(receipt)
            self._heads[run_id] = receipt.hash
            self._last_ts[run_id] = ts
            return receipt

    def receipts(self, run_id: str | None = None) -> list[Receipt]:
        return [r for r in self._receipts if run_id is None or r.run_id == run_id]

    def export_jsonl(self, run_id: str | None = None) -> str:
        return "".join(json.dumps(asdict(r), sort_keys=True) + "\n" for r in self.receipts(run_id))


@dataclass
class Report:
    ok: bool
    count: int
    errors: list[str]


def verify(path: str | Path) -> Report:
    """Check hashes, links, timestamps and approval-before-write for a JSONL export."""
    errors: list[str] = []
    heads: dict[str, str] = {}
    last_ts: dict[str, float] = {}
    approved: dict[str, set[str]] = {}
    count = 0
    for line_no, line in enumerate(Path(path).read_text().splitlines(), start=1):
        if not line.strip():
            continue
        count += 1
        try:
            receipt = Receipt(**json.loads(line))
        except (TypeError, ValueError) as exc:
            errors.append(f"line {line_no}: unreadable receipt ({exc})")
            continue
        run = receipt.run_id
        if receipt.compute_hash() != receipt.hash:
            errors.append(f"{receipt.id}: hash mismatch (content was changed)")
        if receipt.prev != heads.get(run, GENESIS):
            errors.append(f"{receipt.id}: broken link to previous receipt")
        if receipt.ts < last_ts.get(run, 0.0):
            errors.append(f"{receipt.id}: timestamp goes backwards")
        if receipt.kind == "approval":
            digest = str(receipt.details.get("content_sha256", ""))
            approved.setdefault(run, set()).add(digest)
        if receipt.kind == "external_write" and receipt.output_sha256 not in approved.get(
            run, set()
        ):
            errors.append(f"{receipt.id}: external_write without approval for this content")
        heads[run] = receipt.hash
        last_ts[run] = receipt.ts
    return Report(ok=not errors, count=count, errors=errors)
