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
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

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


APPROVAL_SIGNED_FIELDS = (
    "action",
    "item_id",
    "content_sha256",
    "chain_head",
    "approver",
    "nonce",
    "ts",
)


def approval_message(fields: Mapping[str, Any]) -> bytes:
    """The exact bytes a human approver signs (domain-separated canonical JSON)."""
    return canonical({"type": "approval", **{k: fields[k] for k in APPROVAL_SIGNED_FIELDS}})


def public_key_raw(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)


def key_id(public_key: Ed25519PublicKey) -> str:
    """Stable fingerprint of an Ed25519 public key: ``ed25519:<32 hex>``."""
    return "ed25519:" + sha256_hex(public_key_raw(public_key))[:32]


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


def _redact_details(kind: str, details: Mapping[str, Any]) -> dict[str, Any]:
    """Redact PII, except the fields a human signed: those must stay byte-exact."""
    if kind != "approval":
        return dict(redact(dict(details)))
    keep = (*APPROVAL_SIGNED_FIELDS, "sig")
    rest = redact({k: v for k, v in details.items() if k not in keep})
    return {**rest, **{k: details[k] for k in keep if k in details}}


class Store(Protocol):
    """Where receipts live. ``append_atomic`` reads the run head, builds and stores the
    receipt and bumps the ledger version in one atomic step (``conn`` joins a caller's
    SQLite transaction when the store supports it)."""

    transactional: bool

    def accepts(self, conn: Any) -> bool: ...

    def append_atomic(
        self, run_id: str, build: Callable[[str, float], Receipt], conn: Any = None
    ) -> Receipt: ...

    def head(self, run_id: str) -> str: ...

    def receipts(self, run_id: str | None = None) -> list[Receipt]: ...

    def snapshot(self) -> tuple[int, list[Receipt]]: ...


class MemoryStore:
    """Process memory; not transactional, so it cannot record approvals."""

    transactional = False

    def accepts(self, conn: Any) -> bool:
        return False

    def __init__(self) -> None:
        self._receipts: list[Receipt] = []
        self._heads: dict[str, tuple[str, float]] = {}
        self._seq = 0
        self._lock = threading.Lock()

    def append_atomic(
        self, run_id: str, build: Callable[[str, float], Receipt], conn: Any = None
    ) -> Receipt:
        with self._lock:
            prev, last_ts = self._heads.get(run_id, (GENESIS, 0.0))
            receipt = build(prev, last_ts)
            self._receipts.append(receipt)
            self._heads[run_id] = (receipt.hash, receipt.ts)
            self._seq += 1
            return receipt

    def head(self, run_id: str) -> str:
        with self._lock:
            return self._heads.get(run_id, (GENESIS, 0.0))[0]

    def receipts(self, run_id: str | None = None) -> list[Receipt]:
        with self._lock:
            return [r for r in self._receipts if run_id is None or r.run_id == run_id]

    def snapshot(self) -> tuple[int, list[Receipt]]:
        with self._lock:
            return self._seq, list(self._receipts)


class Ledger:
    """Signs and appends receipts; storage is pluggable (memory here, SQLite in ``db``)."""

    def __init__(
        self, signing_key: Ed25519PrivateKey | None = None, store: Store | None = None
    ) -> None:
        self._key = signing_key or Ed25519PrivateKey.generate()
        self._store: Store = store or MemoryStore()

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self._key.public_key()

    @property
    def epoch(self) -> str:
        """Key epoch id: fingerprint of the ledger key, signed into every manifest."""
        return key_id(self.public_key)

    def chain_head(self, run_id: str) -> str:
        return self._store.head(run_id)

    def joins(self, conn: Any) -> bool:
        """True when receipts appended with ``conn`` commit or roll back with its txn."""
        return self._store.transactional and self._store.accepts(conn)

    def append(
        self, run_id: str, *, agent: str, kind: str, conn: Any = None, **fields: Any
    ) -> Receipt:
        if kind not in KINDS:
            raise ValueError(f"unknown receipt kind: {kind!r}")
        details = _redact_details(kind, fields.pop("details", {}) or {})

        def build(prev: str, last_ts: float) -> Receipt:
            draft = Receipt(
                id=uuid.uuid4().hex,
                prev=prev,
                ts=max(time.time(), last_ts),
                run_id=run_id,
                agent=agent,
                kind=kind,
                details=details,
                **fields,
            )
            digest = draft.compute_hash()
            sig = self._key.sign(bytes.fromhex(digest)).hex()
            return Receipt(**{**asdict(draft), "hash": digest, "sig": sig})

        return self._store.append_atomic(run_id, build, conn)

    def receipts(self, run_id: str | None = None) -> list[Receipt]:
        return self._store.receipts(run_id)

    def _sign_manifest(self, seq: int, receipts: list[Receipt]) -> dict[str, Any]:
        runs: dict[str, dict[str, Any]] = {}
        for r in receipts:
            entry = runs.setdefault(r.run_id, {"head": GENESIS, "count": 0})
            entry["head"] = r.hash
            entry["count"] += 1
        body = {"seq": seq, "ts": time.time(), "epoch": self.epoch, "runs": runs}
        return {**body, "sig": self._key.sign(_manifest_bytes(body)).hex()}

    def manifest(self) -> dict[str, Any]:
        """Signed statement of every run's head and length.

        ``seq`` is the ledger version: a durable counter that grows with every write, so an
        older export cannot be passed off as current when the verifier knows the latest
        ``seq`` (published at ``/api/ledger-manifest``). ``epoch`` names the signing key.
        """
        return self._sign_manifest(*self._store.snapshot())

    def export_jsonl(self) -> str:
        """All receipts followed by exactly one signed manifest line (one snapshot)."""
        seq, receipts = self._store.snapshot()
        lines = [json.dumps(asdict(r), sort_keys=True) for r in receipts]
        lines.append(json.dumps({"manifest": self._sign_manifest(seq, receipts)}, sort_keys=True))
        return "".join(line + "\n" for line in lines)


def _manifest_bytes(body: dict[str, Any]) -> bytes:
    return canonical(
        {"type": "manifest", **{k: body[k] for k in ("seq", "ts", "epoch", "runs")}}
    )


@dataclass
class Report:
    ok: bool
    count: int
    errors: list[str]
    signatures: int = 0


def _check_approval(
    receipt: Receipt,
    seen: set[str],
    approver_keys: Mapping[str, Ed25519PublicKey],
) -> list[str]:
    """Approver signature over the approval fields, bound to a real chain head of the run."""
    d = receipt.details
    if not d.get("sig"):
        return [f"{receipt.id}: approval is not signed by an approver"]
    try:
        message = approval_message(d)
    except KeyError as exc:
        return [f"{receipt.id}: approval is missing signed field {exc}"]
    key = approver_keys.get(str(d["approver"]))
    if key is None:
        return [f"{receipt.id}: unknown approver {d['approver']!r}"]
    errors: list[str] = []
    try:
        key.verify(bytes.fromhex(str(d["sig"])), message)
    except (InvalidSignature, ValueError):
        errors.append(f"{receipt.id}: bad approver signature")
    if d["chain_head"] != GENESIS and d["chain_head"] not in seen:
        errors.append(f"{receipt.id}: approval chain_head is not an earlier receipt of this run")
    return errors


def verify(
    path: str | Path,
    public_key: Ed25519PublicKey,
    min_seq: int = 0,
    approver_keys: Mapping[str, Ed25519PublicKey] | None = None,
) -> Report:
    """Check signatures, hashes, links, timestamps and approval-before-write.

    ``public_key`` is the ledger key published by the server (``/api/ledger-key``).
    Without it a forged log with recomputed hashes would look valid. ``min_seq`` is the
    latest manifest sequence the verifier knows; an older (rolled-back) export fails.
    ``approver_keys`` (``/api/approver-keys``) maps approver ids to their public keys; when
    given, every approval must carry a valid approver signature over its fields.
    """
    errors: list[str] = []
    signatures = 0
    seen: dict[str, set[str]] = {}
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
        problems: list[str] = []
        if receipt.kind == "approval" and approver_keys is not None:
            problems = _check_approval(receipt, seen.get(run, set()), approver_keys)
            errors += problems
            signatures += not problems
        if receipt.kind == "approval" and not problems:  # a bad approval authorises nothing
            digest = str(receipt.details.get("content_sha256", ""))
            approved.setdefault(run, set()).add(digest)
        if receipt.kind == "external_write" and receipt.output_sha256 not in approved.get(
            run, set()
        ):
            errors.append(f"{receipt.id}: external_write without approval for this content")
        heads[run] = receipt.hash
        seen.setdefault(run, set()).add(receipt.hash)
        last_ts[run] = receipt.ts
        counts[run] = counts.get(run, 0) + 1
    errors += _check_manifest(manifests, heads, counts, public_key, min_seq)
    return Report(ok=not errors, count=count, errors=errors, signatures=signatures)


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
            "epoch": str(man["epoch"]),
            "runs": dict(man["runs"]),
        }
        public_key.verify(bytes.fromhex(str(man["sig"])), _manifest_bytes(body))
    except (KeyError, TypeError, ValueError, InvalidSignature):
        return ["bad manifest signature"]
    errors: list[str] = []
    if body["epoch"] != key_id(public_key):
        errors.append(f"manifest key epoch {body['epoch']} does not match the ledger key")
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
