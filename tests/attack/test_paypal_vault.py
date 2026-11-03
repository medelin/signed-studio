"""Tampered vault charges under a signed mandate (attack classes 10-12 of the PayPal plan).

10. a charge over the mandate's caps (per charge, per period);
11. a charge after the mandate's revocation;
12. a charge replayed against another mandate (or counted twice).

Each is refused at run time (nothing reaches PayPal, or the hold is voided), and each is
caught offline by ``studio verify`` (P-V5) when the server forges its own log: the
attacker holds the ledger key, so every forged log below is re-chained and re-signed with
it. Only the owner's signed mandate (its ``funding`` block: the saved PayPal's hash, both
caps, the period) and the rules of ``studio verify`` stand in the way.
"""

from __future__ import annotations

import copy
import json
import uuid

import pytest

from studio import mandates
from studio.approvals import Keys, Verified
from studio.paypal.hooks import FundingRefused
from studio.receipts import GENESIS, PAYPAL_KIND, Receipt, _manifest_bytes, verify
from tests.test_paypal_vault import CHARGE, Studio


def rewrite(s, edit):
    """The server rewrites its log (``edit(receipts) -> receipts``: edit, add or reorder),
    re-chains and re-signs every receipt and the manifest with its own ledger key. Returns
    the verify report against the owner's real approver keys."""
    key = s.ledger._key
    lines = [json.loads(x) for x in s.ledger.export_jsonl().splitlines()]
    old = next(x["manifest"] for x in lines if "manifest" in x)
    receipts = edit([x for x in lines if "manifest" not in x])
    heads: dict[str, str] = {}
    counts: dict[str, int] = {}
    for x in receipts:
        x["prev"] = heads.get(x["run_id"], GENESIS)
        x["hash"] = Receipt(**{k: v for k, v in x.items() if k not in ("hash", "sig")}
                            ).compute_hash()
        x["sig"] = key.sign(bytes.fromhex(x["hash"])).hex()
        heads[x["run_id"]] = x["hash"]
        counts[x["run_id"]] = counts.get(x["run_id"], 0) + 1
    body = {"seq": old["seq"], "ts": max(old["ts"], *(x["ts"] for x in receipts)),
            "epoch": old["epoch"],
            "runs": {r: {"head": heads[r], "count": counts[r]} for r in heads}}
    manifest = {**body, "sig": key.sign(_manifest_bytes(body)).hex()}
    path = s.tmp / f"forged-{uuid.uuid4().hex[:8]}.jsonl"
    path.write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in receipts)
                    + json.dumps({"manifest": manifest}, sort_keys=True) + "\n")
    return verify(path, s.ledger.public_key, approver_keys=Keys.registry(s.conn))


def charges(receipts):
    return [x for x in receipts if x["kind"] == PAYPAL_KIND
            and x["details"].get("step") == "paypal.vault_charge" and x["details"].get("ok")]


def mandate_approval(receipts, pending_id):
    return next(x for x in receipts if x["kind"] == "approval"
                and x["details"].get("pending_id") == pending_id)


def caught(report, text):
    assert not report.ok
    assert any(text in e for e in report.errors), report.errors
    return True


@pytest.fixture(scope="module")
def log(tmp_path_factory):
    """Mandate A (caps 300 per charge, 450 per 7 days) pays two runs (2 × 220.50 = 441);
    mandate B, signed too, names the same saved PayPal and is never used; A is revoked
    afterwards. The clean log verifies."""
    s = Studio(tmp_path_factory.mktemp("vault-attack"))
    _, s.mid_a = s.grant(funding_cap="450")
    _, s.mid_b = s.grant(funding_cap="450")
    s.runs = [s.run(), s.run()]
    assert [r["payment"]["outcome"] for r in s.runs] == ["mandate_charge"] * 2
    assert {r["mandate"]["mandate_id"] for r in s.runs} == {s.mid_a}
    assert mandates.revoke(s.conn, s.ledger, s.acct, s.mid_a)
    report = s.verify()
    assert report.ok, report.errors
    assert rewrite(s, lambda rs: rs).ok  # the rewriter itself changes nothing
    return s


# 10. over the caps ----------------------------------------------------------------------


def test_10_a_charge_above_the_per_charge_cap_is_caught(log):
    def edit(rs):
        c = charges(rs)[0]
        ap = mandate_approval(rs, c["details"]["approval_id"])
        for d in (c["details"], ap["details"]["charge"]):
            d["amount_minor"] = 30001  # the cap is 300.00
        return rs
    assert caught(rewrite(log, edit), "above its per-charge cap (P-V5)")


def test_10_charges_above_the_period_cap_are_caught(log):
    def edit(rs):
        c = charges(rs)[1]
        ap = mandate_approval(rs, c["details"]["approval_id"])
        for d in (c["details"], ap["details"]["charge"]):
            d["amount_minor"] = 45000 - CHARGE + 1  # 450.01 in the period
        return rs
    assert caught(rewrite(log, edit), "exceed its funding cap")


def test_10_a_charge_above_what_the_approval_declared_is_caught(log):
    def edit(rs):
        charges(rs)[0]["details"]["amount_minor"] += 100
        return rs
    assert caught(rewrite(log, edit), "charged another amount than the approval declared")


def test_10_a_charge_with_no_declaration_is_caught(log):
    def edit(rs):
        c = charges(rs)[0]
        del mandate_approval(rs, c["details"]["approval_id"])["details"]["charge"]
        return rs
    assert caught(rewrite(log, edit), "without a mandate approval that declares it")


def test_10_over_cap_runs_never_reach_paypal(tmp_path):
    s = Studio(tmp_path)
    s.grant(max_charge="220")  # 220.50 needed
    run = s.run()
    assert "max_charge" in run["mandate"]["reasons"]
    assert s.orders_made() == []
    assert s.conn.execute("SELECT count(*) FROM mandate_charges").fetchone()[0] == 0


# 11. after revocation --------------------------------------------------------------------


def _replay_after_revoke(rs, *, void=False):
    """In the second run (its confirmed brief and all), after the revoke receipt: a copy
    of its by-mandate approval (fresh nonce and pending id) and of its charge (fresh
    PayPal ids)."""
    c = charges(rs)[1]
    ap = mandate_approval(rs, c["details"]["approval_id"])
    run = ap["run_id"]
    last = max(x["ts"] for x in rs) + 5
    pid = uuid.uuid4().hex
    ap2, c2 = copy.deepcopy(ap), copy.deepcopy(c)
    ap2.update(id=uuid.uuid4().hex, run_id=run, ts=last)
    ap2["details"].update(pending_id=pid, nonce="n_" + uuid.uuid4().hex)
    ap2["details"]["charge"]["period"] = ap["details"]["charge"]["period"]
    c2.update(id=uuid.uuid4().hex, run_id=run, ts=last + 1)
    c2["details"].update(approval_id=pid, order_id="5ZZZZZZZZZZZZZZZZ",
                         authorization_id="0ZZZZZZZZZZZZZZZZ")
    out = [*rs, ap2, c2]
    if void:
        v = copy.deepcopy(c2)
        v.update(id=uuid.uuid4().hex, ts=last + 2)
        v["details"] = {"step": "paypal.void", "ok": True, "reason": "mandate_revoked",
                        "authorization_id": "0ZZZZZZZZZZZZZZZZ", "funding_id":
                        c2["details"]["funding_id"], "status": "VOIDED"}
        out.append(v)
    return out


def test_11_a_charge_after_revocation_is_caught(log):
    report = rewrite(log, _replay_after_revoke)
    assert caught(report, "after it was revoked")
    assert caught(report, "names no valid approval of this run (P-V6)")


def test_11_a_charge_landing_after_the_revocation_must_be_voided(log):
    """The race: the approval came before the revocation, PayPal's answer after it. The
    studio voids such a hold at once; a log that keeps it held (or captures it) fails."""
    def edit(rs):
        c = charges(rs)[1]
        rs.remove(c)
        c["ts"] = max(x["ts"] for x in rs) + 1
        return [*rs, c]
    assert caught(rewrite(log, edit),
                  "recorded after its mandate was revoked and never voided (P-V5)")


def test_11_a_revoked_mandates_charge_left_held_is_caught_even_if_voided_later(log):
    # voiding does not legitimise the approval made after the revocation
    assert caught(rewrite(log, lambda rs: _replay_after_revoke(rs, void=True)),
                  "after it was revoked")


def test_11_a_revoked_mandate_charges_nothing_at_run_time(tmp_path):
    s = Studio(tmp_path)
    _, mid = s.grant()
    assert mandates.revoke(s.conn, s.ledger, s.acct, mid)
    run = s.run()
    assert "revoked" in run["mandate"]["reasons"]
    assert run["payment"]["outcome"] == "pay_and_sign"
    assert s.orders_made() == []


# 12. replayed against another mandate ----------------------------------------------------


def test_12_a_charge_relabelled_to_another_mandate_is_caught(log):
    def edit(rs):
        charges(rs)[0]["details"]["mandate_id"] = log.mid_b
        return rs
    assert caught(rewrite(log, edit), "names another mandate than its approval's (P-V5)")


def test_12_a_charge_of_another_saved_paypal_is_caught(log):
    def edit(rs):
        charges(rs)[0]["details"]["vault_sha256"] = "ab" * 32
        return rs
    assert caught(rewrite(log, edit), "another saved PayPal than the mandate signs (P-V5)")


def test_12_one_paypal_charge_counted_under_a_second_mandate_is_caught(log):
    """The server replays charge 1 (same PayPal order) as the payment of a new approval by
    mandate B, e.g. to show a campaign as paid that never was."""
    def edit(rs):
        c = charges(rs)[0]
        ap = mandate_approval(rs, c["details"]["approval_id"])
        b = mandates.get(log.conn, log.mid_b)
        run = ap["run_id"]
        first_revoke = next(i for i, x in enumerate(rs) if x["kind"] == "mandate.revoke")
        ts = rs[first_revoke - 1]["ts"]
        pid = uuid.uuid4().hex
        ap2, c2 = copy.deepcopy(ap), copy.deepcopy(c)
        ap2.update(id=uuid.uuid4().hex, run_id=run, ts=ts)
        ap2["details"].update(pending_id=pid, nonce="n_" + uuid.uuid4().hex,
                              by_mandate=b.id,
                              approver="mandate:" + b.id, sig=b.sha256,
                              mandate_sha256=b.sha256)
        ap2["details"]["checked"]["n_of_max"] = [1, 3]
        c2.update(id=uuid.uuid4().hex, run_id=run, ts=ts)
        c2["details"].update(approval_id=pid, mandate_id=b.id)
        return [*rs[:first_revoke], ap2, c2, *rs[first_revoke:]]
    assert caught(rewrite(log, edit), "charged twice in this log (a replayed charge)")


def test_12_a_charge_cannot_be_replayed_at_run_time(log):
    """The funder pays once per approval: replaying the first run's approval under its own
    mandate or under mandate B reaches PayPal no more."""
    run = log.runs[0]
    pid = run["mandate"]["approval_id"]
    before = len(log.orders_made())
    v = Verified(pending_id=pid, run_id=run["run_id"], action="campaign.create",
                 item_id=run["mandate"]["result"]["campaign_item_id"],
                 content_sha256="0" * 64, approver="mandate:" + log.mid_a, signature="")
    row = mandates.charge_of(log.conn, pid)
    charge = {"schema": "charge/v1", "amount_minor": CHARGE, "currency": "PLN",
              "period": row["period"], "vault_sha256": row["vault_sha256"]}
    payload = json.loads(log.conn.execute("SELECT payload FROM pending_payloads WHERE "
                                          "approval_id = ?", (pid,)).fetchone()[0])
    for mid in (log.mid_a, log.mid_b):
        with pytest.raises(FundingRefused) as exc:
            log.funder.charge(v, payload, mandates.get(log.conn, mid), charge)
        assert exc.value.code == "charge_changed"
    assert len(log.orders_made()) == before
