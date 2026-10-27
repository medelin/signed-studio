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
import math
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
APPROVAL_TTL_S = 900  # an approval authorises one write within this many seconds of issue
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


_OPAQUE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}")
_HEX = re.compile(r"[0-9a-f]+")


def is_opaque(value: Any) -> bool:
    """True for values that cannot carry PII: ints, hex digests, short internal ids.

    An internal id must match ``[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}`` and be left unchanged by
    ``redact`` (so ``c-600123456`` or an e-mail address is not an id).
    """
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if not isinstance(value, str):
        return False
    if _HEX.fullmatch(value) and len(value) in (64, 128):
        return True
    return bool(_OPAQUE_ID.fullmatch(value)) and redact(value) == value


_ID_FIELDS = ("model", "provider", "tier")


def normalize_id(value: Any) -> tuple[str, str]:
    """An externally reported id in receipt-safe form: ``(value, "")`` when it is opaque,
    otherwise ``("unrecognized", sha256 of the raw value)``. Never raises, so a value from
    a server response can never prevent the receipt for an action already taken."""
    if isinstance(value, str) and is_opaque(value):
        return value, ""
    raw = value if isinstance(value, str) else repr(value)
    return "unrecognized", sha256_hex(raw)


def _redact_details(kind: str, details: Mapping[str, Any]) -> dict[str, Any]:
    """Redact PII. Approval receipts keep the signed fields byte-exact, so those fields
    must be opaque (ids, digests, key ids, nonce, ts); anything else is refused."""
    if kind != "approval":
        return dict(redact(dict(details)))
    keep = (*APPROVAL_SIGNED_FIELDS, "sig")
    for k in keep:
        if k in details and not is_opaque(details[k]):
            raise ValueError(f"approval field {k!r} must be an opaque value, not free text")
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

    def seq(self) -> int: ...


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

    def seq(self) -> int:
        with self._lock:
            return self._seq


class Ledger:
    """Signs and appends receipts; storage is pluggable (memory here, SQLite in ``db``)."""

    def __init__(
        self, signer: Ed25519PrivateKey | None = None, store: Store | None = None
    ) -> None:
        self._key = signer or Ed25519PrivateKey.generate()
        self._store: Store = store or MemoryStore()
        self._signed: dict[str, Any] | None = None  # latest signed manifest (one per seq)
        self._signed_lock = threading.Lock()

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
        # The export is public: ids must be opaque, free text is redacted.
        self.validate_ids(run_id, agent, **{n: fields.get(n, "") for n in _ID_FIELDS})
        if "tier_reason" in fields:
            fields["tier_reason"] = redact(str(fields["tier_reason"]))
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

    def model_spend_since(self, ts: float) -> float:
        """Sum of ``cost_usd`` over ``model_call`` receipts at or after ``ts`` (durable when
        the store is SQLite): the source of truth for the daily budget after a restart."""
        return sum(r.cost_usd for r in self._store.receipts()
                   if r.kind == "model_call" and r.ts >= ts)

    @staticmethod
    def validate_ids(
        run_id: str, agent: str, *, provider: str = "", model: str = "", tier: str = ""
    ) -> None:
        """Raise ``ValueError`` unless the ids are opaque (``provider``/``model``/``tier`` may
        be empty). Callers run this *before* any side effect, so a bad caller-supplied id
        fails fast instead of producing an action without a receipt."""
        for name, value, required in (("run_id", run_id, True), ("agent", agent, True),
                                      ("provider", provider, False), ("model", model, False),
                                      ("tier", tier, False)):
            if (required or value) and not is_opaque(value):
                raise ValueError(f"receipt field {name!r} must be an opaque id, not free text")

    def seq(self) -> int:
        """Current durable ledger version; read-only (no signing), for display."""
        return self._store.seq()

    def _sign_manifest(self, seq: int, receipts: list[Receipt]) -> dict[str, Any]:
        """The signed manifest for ``seq``, signed once and reused until the ledger changes
        (``seq`` grows with every write, so equal ``seq`` means equal contents)."""
        with self._signed_lock:
            if self._signed is not None and self._signed["seq"] == seq:
                return dict(self._signed)
        runs: dict[str, dict[str, Any]] = {}
        for r in receipts:
            entry = runs.setdefault(r.run_id, {"head": GENESIS, "count": 0})
            entry["head"] = r.hash
            entry["count"] += 1
        body = {"seq": seq, "ts": time.time(), "epoch": self.epoch, "runs": runs}
        signed = {**body, "sig": self._key.sign(_manifest_bytes(body)).hex()}
        with self._signed_lock:
            if self._signed is None or self._signed["seq"] <= seq:
                self._signed = signed
        return signed

    def manifest(self) -> dict[str, Any]:
        """Signed statement of every run's head and length.

        ``seq`` is the ledger version: a durable counter that grows with every write, so an
        older export cannot be passed off as current when the verifier knows the latest
        ``seq`` (published at ``/api/ledger-manifest``). ``epoch`` names the signing key.
        Reading it never changes ``seq``; a new manifest is signed only after a write.
        """
        seq = self._store.seq()
        with self._signed_lock:
            if self._signed is not None and self._signed["seq"] == seq:
                return dict(self._signed)
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


_STR_FIELDS = ("id", "prev", "run_id", "agent", "kind", "provider", "model", "tier",
               "tier_reason", "prompt_sha256", "output_sha256", "hash", "sig")
_INT_FIELDS = ("tokens_in", "tokens_out", "cached_tokens", "duration_ms")


def _number(value: Any) -> bool:
    return (isinstance(value, int | float) and not isinstance(value, bool)
            and math.isfinite(value))


def _type_error(r: Receipt) -> str | None:
    """Why a parsed receipt has the wrong field types, or None. Checked before any use."""
    for name in _STR_FIELDS:
        if not isinstance(getattr(r, name), str):
            return f"field {name!r} must be a string"
    for name in _INT_FIELDS:
        value = getattr(r, name)
        if not isinstance(value, int) or isinstance(value, bool):
            return f"field {name!r} must be an integer"
    if not _number(r.ts) or not _number(r.cost_usd):
        return "fields 'ts' and 'cost_usd' must be finite numbers"
    if not isinstance(r.envelope, bool):
        return "field 'envelope' must be a boolean"
    if not isinstance(r.input_sha256, list) or not all(isinstance(h, str)
                                                        for h in r.input_sha256):
        return "field 'input_sha256' must be a list of strings"
    if not isinstance(r.details, dict):
        return "field 'details' must be an object"
    return None


@dataclass(frozen=True)
class _Grant:
    """What one valid approval authorises: one write of this action, to this item, with
    this content, in time."""

    action: str
    item_id: str
    content_sha256: str
    ts: int


def _check_approval(
    receipt: Receipt,
    seen: set[str],
    approver_keys: Mapping[str, Ed25519PublicKey] | None,
) -> tuple[list[str], _Grant | None]:
    """Signed fields well-formed, approver signature valid (when keys are given), and bound
    to a real chain head of the run (``GENESIS`` only for the run's first receipt)."""
    d = receipt.details
    missing = [k for k in APPROVAL_SIGNED_FIELDS if k not in d]
    if missing:
        return [f"{receipt.id}: approval is missing signed field {missing[0]!r}"], None
    if not all(isinstance(d[k], str) for k in APPROVAL_SIGNED_FIELDS if k != "ts") or not (
        isinstance(d["ts"], int) and not isinstance(d["ts"], bool)
    ):
        return [f"{receipt.id}: approval signed fields have the wrong types"], None
    errors: list[str] = []
    if approver_keys is not None:
        if not isinstance(d.get("sig"), str) or not d.get("sig"):
            return [f"{receipt.id}: approval is not signed by an approver"], None
        key = approver_keys.get(d["approver"])
        if key is None:
            return [f"{receipt.id}: unknown approver {d['approver']!r}"], None
        try:
            key.verify(bytes.fromhex(d["sig"]), approval_message(d))
        except (InvalidSignature, ValueError):
            errors.append(f"{receipt.id}: bad approver signature")
    head = d["chain_head"]
    if (head == GENESIS and seen) or (head != GENESIS and head not in seen):
        errors.append(f"{receipt.id}: approval chain_head is not an earlier receipt of this run")
    if errors:
        return errors, None
    return [], _Grant(d["action"], d["item_id"], d["content_sha256"], d["ts"])


def _check_write(
    receipt: Receipt, grants: dict[str, _Grant], used: set[str]
) -> list[str]:
    """Consume the approval the write names; it must match action, content and TTL."""
    approval_id = receipt.details.get("approval_id")
    if not isinstance(approval_id, str) or not approval_id:
        return [f"{receipt.id}: external_write without approval: it names no approval "
                "(details.approval_id)"]
    grant = grants.pop(approval_id, None)
    if grant is None:
        if approval_id in used:
            return [f"{receipt.id}: approval {approval_id} was already used by an earlier write"]
        return [f"{receipt.id}: external_write without approval for this content"]
    used.add(approval_id)
    errors: list[str] = []
    if receipt.output_sha256 != grant.content_sha256:
        errors.append(f"{receipt.id}: external_write without approval for this content")
    if receipt.details.get("action") != grant.action:
        errors.append(f"{receipt.id}: write action does not match the approved action "
                      f"{grant.action!r}")
    if receipt.details.get("item_id") != grant.item_id:
        errors.append(f"{receipt.id}: write item does not match the approved item "
                      f"{grant.item_id!r}")
    if receipt.ts - grant.ts > APPROVAL_TTL_S:
        errors.append(f"{receipt.id}: approval expired before the write "
                      f"(older than {APPROVAL_TTL_S} s)")
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

    Every ``external_write`` must name (``details.approval_id``) a valid approval of the
    same run that no earlier write used, for the same content, ``details.action`` and
    ``details.item_id``, and
    happen within ``APPROVAL_TTL_S`` of the approval's signed ``ts``. A nonce may appear
    in only one approval of the whole log.
    """
    errors: list[str] = []
    signatures = 0
    seen: dict[str, set[str]] = {}
    heads: dict[str, str] = {}
    last_ts: dict[str, float] = {}
    grants: dict[str, dict[str, _Grant]] = {}
    used: dict[str, set[str]] = {}
    nonces: set[str] = set()
    counts: dict[str, int] = {}
    manifests: list[Any] = []
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
            if not isinstance(raw, dict):
                raise TypeError("not a JSON object")
            receipt = Receipt(**raw)
        except (TypeError, ValueError) as exc:
            errors.append(f"line {line_no}: unreadable receipt ({exc})")
            continue
        problem = _type_error(receipt)
        if problem is not None:
            errors.append(f"line {line_no}: unreadable receipt ({problem})")
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
            problems, grant = _check_approval(receipt, seen.get(run, set()), approver_keys)
            nonce = receipt.details.get("nonce")
            if isinstance(nonce, str) and nonce in nonces:
                problems.append(f"{receipt.id}: approval nonce {nonce} is used more than once")
                grant = None
            elif isinstance(nonce, str):
                nonces.add(nonce)
            errors += problems
            if grant is not None:  # a bad approval authorises nothing
                signatures += approver_keys is not None
                pending_id = receipt.details.get("pending_id")
                grants.setdefault(run, {})[str(pending_id or receipt.id)] = grant
        if receipt.kind == "external_write":
            errors += _check_write(receipt, grants.setdefault(run, {}),
                                   used.setdefault(run, set()))
        heads[run] = receipt.hash
        seen.setdefault(run, set()).add(receipt.hash)
        last_ts[run] = receipt.ts
        counts[run] = counts.get(run, 0) + 1
    errors += _check_manifest(manifests, heads, counts, public_key, min_seq)
    return Report(ok=not errors, count=count, errors=errors, signatures=signatures)


def _check_manifest(
    manifests: list[Any],
    heads: dict[str, str],
    counts: dict[str, int],
    public_key: Ed25519PublicKey,
    min_seq: int,
) -> list[str]:
    if len(manifests) != 1:
        return [f"expected exactly one signed manifest, found {len(manifests)}"]
    man = manifests[0]
    try:
        if not isinstance(man, dict):
            raise TypeError("manifest is not an object")
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
        elif (not isinstance(entry, dict) or entry.get("head") != heads[run]
              or entry.get("count") != counts[run]):
            errors.append(f"run {run}: log does not match manifest (truncated or receipts missing)")
    return errors
