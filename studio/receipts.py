"""Receipts: a hash-chained, signed provenance log for every agent step.

Each receipt names the agent, the model or tool, what went in and out (as hashes), the
cost, and the hash of the previous receipt in the same run. The server signs every
receipt hash with its Ed25519 ledger key, so a changed log cannot be "repaired" by
recomputing the hashes. ``verify`` checks signatures, hashes, links and timestamps, and
that every external write is preceded by a human approval for exactly the same content.
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

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

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
    sig: str = ""

    def body(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("hash")
        data.pop("sig")
        return data

    def compute_hash(self) -> str:
        return sha256_hex(canonical(self.body()))


class Ledger:
    """In-memory ledger; the database layer persists the same receipts."""

    def __init__(self, signing_key: Ed25519PrivateKey | None = None) -> None:
        self._key = signing_key or Ed25519PrivateKey.generate()
        self._receipts: list[Receipt] = []
        self._heads: dict[str, str] = {}
        self._last_ts: dict[str, float] = {}
        self._export_seq = 0
        self._lock = threading.Lock()

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self._key.public_key()

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
            digest = draft.compute_hash()
            sig = self._key.sign(bytes.fromhex(digest)).hex()
            receipt = Receipt(**{**asdict(draft), "hash": digest, "sig": sig})
            self._receipts.append(receipt)
            self._heads[run_id] = receipt.hash
            self._last_ts[run_id] = ts
            return receipt

    def receipts(self, run_id: str | None = None) -> list[Receipt]:
        return [r for r in self._receipts if run_id is None or r.run_id == run_id]

    def manifest(self) -> dict[str, Any]:
        """Signed statement of every run's head and length at export time.

        ``seq`` grows with every export and ``ts`` is the signing time, so an old
        manifest cannot be passed off as current when the verifier knows the latest
        ``seq`` (published by the server at ``/api/ledger-manifest``).
        """
        with self._lock:
            self._export_seq += 1
            runs = {
                run: {"head": self.chain_head(run), "count": len(self.receipts(run))}
                for run in dict.fromkeys(r.run_id for r in self._receipts)
            }
            body = {"seq": self._export_seq, "ts": time.time(), "runs": runs}
        return {**body, "sig": self._key.sign(_manifest_bytes(body)).hex()}

    def export_jsonl(self) -> str:
        """All receipts followed by exactly one signed manifest line."""
        lines = [json.dumps(asdict(r), sort_keys=True) for r in self._receipts]
        lines.append(json.dumps({"manifest": self.manifest()}, sort_keys=True))
        return "".join(line + "\n" for line in lines)


def _manifest_bytes(body: dict[str, Any]) -> bytes:
    return canonical({"type": "manifest", **{k: body[k] for k in ("seq", "ts", "runs")}})


@dataclass
class Report:
    ok: bool
    count: int
    errors: list[str]


def verify(path: str | Path, public_key: Ed25519PublicKey, min_seq: int = 0) -> Report:
    """Check signatures, hashes, links, timestamps and approval-before-write.

    ``public_key`` is the ledger key published by the server (``/api/ledger-key``).
    Without it a forged log with recomputed hashes would look valid. ``min_seq`` is the
    latest manifest sequence the verifier knows; an older (rolled-back) export fails.
    """
    errors: list[str] = []
    heads: dict[str, str] = {}
    last_ts: dict[str, float] = {}
    approved: dict[str, set[str]] = {}
    counts: dict[str, int] = {}
    manifests: list[dict[str, Any]] = []
    count = 0
    for line_no, line in enumerate(Path(path).read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except ValueError as exc:
            errors.append(f"line {line_no}: unreadable line ({exc})")
            continue
        if isinstance(raw, dict) and "manifest" in raw:
            manifests.append(raw["manifest"])
            continue
        count += 1
        try:
            receipt = Receipt(**raw)
        except (TypeError, ValueError) as exc:
            errors.append(f"line {line_no}: unreadable receipt ({exc})")
            continue
        run = receipt.run_id
        if receipt.compute_hash() != receipt.hash:
            errors.append(f"{receipt.id}: hash mismatch (content was changed)")
        else:
            try:
                public_key.verify(bytes.fromhex(receipt.sig), bytes.fromhex(receipt.hash))
            except (InvalidSignature, ValueError):
                errors.append(f"{receipt.id}: bad ledger signature")
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
        counts[run] = counts.get(run, 0) + 1
    errors += _check_manifest(manifests, heads, counts, public_key, min_seq)
    return Report(ok=not errors, count=count, errors=errors)


def _check_manifest(
    manifests: list[dict[str, Any]],
    heads: dict[str, str],
    counts: dict[str, int],
    public_key: Ed25519PublicKey,
    min_seq: int,
) -> list[str]:
    if len(manifests) != 1:
        return [f"expected exactly one signed manifest, found {len(manifests)}"]
    man = manifests[0]
    try:
        body: dict[str, Any] = {
            "seq": int(man["seq"]),
            "ts": float(man["ts"]),
            "runs": dict(man["runs"]),
        }
        public_key.verify(bytes.fromhex(str(man["sig"])), _manifest_bytes(body))
    except (KeyError, TypeError, ValueError, InvalidSignature):
        return ["bad manifest signature"]
    errors: list[str] = []
    if body["seq"] < min_seq:
        errors.append(f"manifest seq {body['seq']} is older than the latest known {min_seq}")
    listed: dict[str, Any] = body["runs"]
    for run in sorted(set(listed) | set(counts)):
        entry = listed.get(run)
        if entry is None:
            errors.append(f"run {run}: receipts not covered by the manifest")
        elif run not in counts:
            errors.append(f"run {run}: listed in the manifest but missing from the log")
        elif entry.get("head") != heads[run] or entry.get("count") != counts[run]:
            errors.append(f"run {run}: log does not match manifest (truncated or receipts missing)")
    return errors
