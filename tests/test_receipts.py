import json
import time
import uuid

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from studio.receipts import (
    APPROVAL_TTL_S,
    GENESIS,
    Ledger,
    approval_message,
    canonical,
    key_id,
    sha256_hex,
    verify,
)

APPROVER = Ed25519PrivateKey.generate()
AID = key_id(APPROVER.public_key())
REGISTRY = {AID: APPROVER.public_key()}
CONTENT = sha256_hex(b"reply text")


def approve(led, run="run-1", content=CONTENT, action="reply.post", ts=None, nonce=None,
            signer=APPROVER, signed=True):
    """Append an approval receipt as the server would after verify_and_consume."""
    fields = {"action": action, "item_id": "c-1", "content_sha256": content,
              "chain_head": led.chain_head(run), "approver": AID,
              "nonce": nonce or "n_" + uuid.uuid4().hex,
              "ts": int(time.time() if ts is None else ts)}
    pending_id = uuid.uuid4().hex
    extra = {"sig": signer.sign(approval_message(fields)).hex()} if signed else {}
    led.append(run, agent="human", kind="approval", output_sha256=content,
               details={**fields, **extra, "pending_id": pending_id})
    return pending_id


def write(led, approval_id, run="run-1", content=CONTENT, action="reply.post"):
    led.append(run, agent="media_buyer", kind="external_write", output_sha256=content,
               details={"approval_id": approval_id, "action": action})


def check(led, tmp_path, keys=REGISTRY):
    path = tmp_path / "r.jsonl"
    path.write_text(led.export_jsonl())
    return verify(path, led.public_key, approver_keys=keys)


def test_canonical_is_stable_across_key_order():
    assert canonical({"b": 1, "a": [1, {"y": 2, "x": 1}]}) == canonical(
        {"a": [1, {"x": 1, "y": 2}], "b": 1}
    )


def test_chain_links_and_head():
    led = Ledger()
    r1 = led.append("run-1", agent="producer", kind="model_call", model="muse-spark")
    r2 = led.append("run-1", agent="copywriter", kind="model_call", model="muse-spark")
    assert r1.prev == GENESIS
    assert r2.prev == r1.hash
    assert led.chain_head("run-1") == r2.hash
    assert led.chain_head("other-run") == GENESIS


def test_verify_ok_and_tamper_detected(tmp_path):
    led = Ledger()
    for agent in ("producer", "copywriter", "reviewer"):
        led.append("run-1", agent=agent, kind="model_call", model="muse-spark")
    path = tmp_path / "receipts.jsonl"
    path.write_text(led.export_jsonl())
    assert verify(path, led.public_key).ok

    lines = path.read_text().splitlines()
    rec = json.loads(lines[1])
    rec["model"] = "something-else"
    lines[1] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n")
    report = verify(path, led.public_key)
    assert not report.ok
    assert report.errors[0].startswith(rec["id"])


def test_broken_prev_link_detected(tmp_path):
    led = Ledger()
    led.append("run-1", agent="a", kind="model_call")
    led.append("run-1", agent="b", kind="model_call")
    lines = led.export_jsonl().splitlines()
    path = tmp_path / "r.jsonl"
    path.write_text(lines[1] + "\n" + lines[-1] + "\n")  # receipt 2 without its predecessor
    assert not verify(path, led.public_key).ok


def test_timestamps_monotonic():
    led = Ledger()
    a = led.append("run-1", agent="a", kind="model_call")
    b = led.append("run-1", agent="b", kind="model_call")
    assert b.ts >= a.ts


def test_details_are_redacted():
    led = Ledger()
    r = led.append(
        "run-1",
        agent="community",
        kind="model_call",
        details={"comment": "write to jan.kowalski@example.org or +48 601 234 567"},
    )
    text = json.dumps(r.details)
    assert "jan.kowalski@example.org" not in text
    assert "601 234 567" not in text
    assert "[email]" in text and "[phone]" in text


def test_external_write_requires_matching_approval(tmp_path):
    led = Ledger()
    led.append("run-1", agent="media_buyer", kind="external_write", output_sha256="abc")
    path = tmp_path / "r.jsonl"
    path.write_text(led.export_jsonl())
    report = verify(path, led.public_key)
    assert not report.ok
    assert "external_write without approval" in report.errors[0]

    led2 = Ledger()
    write(led2, approve(led2))
    report = check(led2, tmp_path)
    assert report.ok, report.errors
    assert report.signatures == 1


@pytest.mark.parametrize("kind", ["unknown", ""])
def test_unknown_kind_rejected(kind):
    with pytest.raises(ValueError):
        Ledger().append("run-1", agent="a", kind=kind)


def test_recomputed_hashes_without_key_are_rejected(tmp_path):
    from studio.receipts import Receipt

    led = Ledger()
    led.append("run-1", agent="producer", kind="model_call", model="muse-spark")
    rec = json.loads(led.export_jsonl().splitlines()[0])  # first receipt
    rec["model"] = "forged"
    forged = Receipt(**{**rec, "hash": "", "sig": rec["sig"]})
    rec["hash"] = forged.compute_hash()  # attacker recomputes the hash
    path = tmp_path / "r.jsonl"
    path.write_text(json.dumps(rec) + "\n")
    report = verify(path, led.public_key)
    assert not report.ok
    assert "bad ledger signature" in report.errors[0]


def test_other_ledger_key_rejected(tmp_path):
    led = Ledger()
    led.append("run-1", agent="a", kind="model_call")
    path = tmp_path / "r.jsonl"
    path.write_text(led.export_jsonl())
    assert not verify(path, Ledger().public_key).ok


def test_truncated_tail_detected(tmp_path):
    led = Ledger()
    write(led, approve(led))
    lines = led.export_jsonl().splitlines()
    path = tmp_path / "r.jsonl"
    path.write_text(lines[0] + "\n" + lines[-1] + "\n")  # last receipt dropped
    report = verify(path, led.public_key)
    assert not report.ok
    assert "does not match manifest" in report.errors[0]


def test_missing_manifest_detected(tmp_path):
    led = Ledger()
    led.append("run-1", agent="a", kind="model_call")
    path = tmp_path / "r.jsonl"
    path.write_text(led.export_jsonl().splitlines()[0] + "\n")
    report = verify(path, led.public_key)
    assert not report.ok
    assert "exactly one signed manifest" in report.errors[0]


def test_whole_run_deletion_detected(tmp_path):
    led = Ledger()
    led.append("run-1", agent="a", kind="model_call")
    led.append("run-2", agent="b", kind="model_call")
    lines = led.export_jsonl().splitlines()
    path = tmp_path / "r.jsonl"
    path.write_text(lines[0] + "\n" + lines[-1] + "\n")  # run-2 removed entirely
    report = verify(path, led.public_key)
    assert not report.ok
    assert "run-2" in report.errors[0]


def test_rollback_to_older_export_detected(tmp_path):
    led = Ledger()
    led.append("run-1", agent="a", kind="model_call")
    old = led.export_jsonl()
    led.append("run-1", agent="b", kind="model_call")
    latest_seq = led.manifest()["seq"]
    path = tmp_path / "r.jsonl"
    path.write_text(old)
    assert verify(path, led.public_key).ok  # valid on its own...
    report = verify(path, led.public_key, min_seq=latest_seq)
    assert not report.ok  # ...but stale against the latest known manifest
    assert "older than the latest known" in report.errors[0]


def test_two_manifests_rejected(tmp_path):
    led = Ledger()
    led.append("run-1", agent="a", kind="model_call")
    first = led.export_jsonl()
    second_manifest = led.export_jsonl().splitlines()[-1]
    path = tmp_path / "r.jsonl"
    path.write_text(first + second_manifest + "\n")
    assert not verify(path, led.public_key).ok


# -- C1: one approval authorises exactly one write of the same action, within the TTL --


def test_one_approval_authorises_only_one_write(tmp_path):
    led = Ledger()
    aid = approve(led)
    write(led, aid)
    write(led, aid)  # replayed against the same approval
    report = check(led, tmp_path)
    assert not report.ok
    assert len(report.errors) == 1 and "already used" in report.errors[0]


def test_write_must_name_its_approval(tmp_path):
    led = Ledger()
    approve(led)
    led.append("run-1", agent="media_buyer", kind="external_write", output_sha256=CONTENT)
    assert "names no approval" in " ".join(check(led, tmp_path).errors)


def test_write_with_other_action_rejected(tmp_path):
    led = Ledger()
    write(led, approve(led, action="reply.post"), action="comment.hide")
    errors = check(led, tmp_path).errors
    assert len(errors) == 1 and "action" in errors[0]


def test_write_with_other_content_rejected(tmp_path):
    led = Ledger()
    write(led, approve(led), content=sha256_hex(b"edited"))
    assert "without approval for this content" in " ".join(check(led, tmp_path).errors)


def test_write_after_approval_ttl_rejected(tmp_path):
    led = Ledger()
    write(led, approve(led, ts=time.time() - APPROVAL_TTL_S - 5))
    assert "expired" in " ".join(check(led, tmp_path).errors)


def test_write_within_ttl_accepted(tmp_path):
    led = Ledger()
    write(led, approve(led, ts=time.time() - APPROVAL_TTL_S + 60))
    assert check(led, tmp_path).ok


def test_approval_from_another_run_does_not_authorise(tmp_path):
    led = Ledger()
    aid = approve(led, run="run-a")
    led.append("run-b", agent="producer", kind="model_call")
    write(led, aid, run="run-b")
    assert "without approval" in " ".join(check(led, tmp_path).errors)


def test_repeated_nonce_in_log_rejected(tmp_path):
    led = Ledger()
    approve(led, nonce="n_" + "1" * 32)
    approve(led, run="run-2", nonce="n_" + "1" * 32)
    assert "nonce" in " ".join(check(led, tmp_path).errors)


def test_genesis_chain_head_only_on_first_receipt(tmp_path):
    led = Ledger()
    led.append("run-1", agent="producer", kind="model_call")
    fields = {"action": "reply.post", "item_id": "c-1", "content_sha256": CONTENT,
              "chain_head": GENESIS, "approver": AID, "nonce": "n_" + "2" * 32,
              "ts": int(time.time())}
    led.append("run-1", agent="human", kind="approval", output_sha256=CONTENT,
               details={**fields, "sig": APPROVER.sign(approval_message(fields)).hex(),
                        "pending_id": "p1"})
    assert "chain_head" in " ".join(check(led, tmp_path).errors)


# -- I5: the server cannot add approvals the human did not sign --------------------------


def test_server_added_unsigned_approval_rejected(tmp_path):
    led = Ledger()
    write(led, approve(led, signed=False))
    errors = " ".join(check(led, tmp_path).errors)
    assert "not signed" in errors and "external_write without approval" in errors


def test_server_added_approval_signed_by_other_key_rejected(tmp_path):
    led = Ledger()
    write(led, approve(led, signer=Ed25519PrivateKey.generate()))
    errors = " ".join(check(led, tmp_path).errors)
    assert "bad approver signature" in errors and "external_write without approval" in errors


def test_approval_by_unregistered_approver_rejected(tmp_path):
    led = Ledger()
    write(led, approve(led))
    errors = " ".join(check(led, tmp_path, keys={}).errors)
    assert "unknown approver" in errors and "external_write without approval" in errors


# -- verify never crashes on hostile input ------------------------------------------------


@pytest.mark.parametrize(
    "patch",
    [
        {"ts": "yesterday"},
        {"ts": None},
        {"details": "not a dict"},
        {"details": {"chain_head": ["unhashable"]}, "kind": "approval"},
        {"input_sha256": "abc"},
        {"tokens_in": "many"},
        {"sig": 42},
        {"hash": None},
        {"run_id": ["x"]},
        {"envelope": "yes"},
        {"cost_usd": "free"},
    ],
)
def test_verify_reports_bad_types_instead_of_crashing(tmp_path, patch):
    led = Ledger()
    write(led, approve(led))
    lines = led.export_jsonl().splitlines()
    rec = json.loads(lines[0])
    rec.update(patch)
    lines[0] = json.dumps(rec)
    path = tmp_path / "r.jsonl"
    path.write_text("\n".join(lines) + "\n")
    report = verify(path, led.public_key, approver_keys=REGISTRY)
    assert not report.ok


@pytest.mark.parametrize("line", ['[1, 2]', '"manifest"', '{"manifest": [1]}',
                                  '{"manifest": {"seq": 1, "ts": 1, "epoch": "e", "runs": '
                                  '{"run-1": 5}, "sig": "00"}}'])
def test_verify_survives_odd_lines(tmp_path, line):
    led = Ledger()
    led.append("run-1", agent="a", kind="model_call")
    path = tmp_path / "r.jsonl"
    path.write_text(led.export_jsonl() + line + "\n")
    assert not verify(path, led.public_key).ok
