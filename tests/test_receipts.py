import json

import pytest

from studio.receipts import GENESIS, Ledger, canonical, verify


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
    assert verify(path).ok

    lines = path.read_text().splitlines()
    rec = json.loads(lines[1])
    rec["model"] = "something-else"
    lines[1] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n")
    report = verify(path)
    assert not report.ok
    assert report.errors[0].startswith(rec["id"])


def test_broken_prev_link_detected(tmp_path):
    led = Ledger()
    led.append("run-1", agent="a", kind="model_call")
    led.append("run-1", agent="b", kind="model_call")
    lines = led.export_jsonl().splitlines()
    path = tmp_path / "r.jsonl"
    path.write_text(lines[1] + "\n")  # second receipt without its predecessor
    assert not verify(path).ok


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
    report = verify(path)
    assert not report.ok
    assert "external_write without approval" in report.errors[0]

    led2 = Ledger()
    led2.append("run-1", agent="human", kind="approval", details={"content_sha256": "abc"})
    led2.append("run-1", agent="media_buyer", kind="external_write", output_sha256="abc")
    path.write_text(led2.export_jsonl())
    assert verify(path).ok


@pytest.mark.parametrize("kind", ["unknown", ""])
def test_unknown_kind_rejected(kind):
    with pytest.raises(ValueError):
        Ledger().append("run-1", agent="a", kind=kind)
