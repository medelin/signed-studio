"""The PayPal decline messages in the page language. The scripts have no unit harness, so
the real ``i18n.js`` and ``reasonOf`` of ``app.js`` run in node against the real catalogs
(skipped without node); the catalog checks run everywhere."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from studio.ui import i18n

UI = Path(__file__).resolve().parent.parent / "studio" / "ui"
PLACEHOLDER = re.compile(r"\{(\w+)\}")


@pytest.mark.parametrize("lang", ["en", "pl"])
def test_the_decline_strings_have_their_placeholders(lang):
    cat = i18n.CATALOGS[lang]
    assert set(PLACEHOLDER.findall(cat["js.paypal_declined"])) == {"issue", "reason"}
    assert set(PLACEHOLDER.findall(cat["js.paypal_unconfirmed"])) == {"issue"}
    assert set(PLACEHOLDER.findall(cat["js.paypal_capture_declined"])) == {
        "issue", "reason", "debug"}
    assert set(PLACEHOLDER.findall(cat["js.paypal_capture_unconfirmed"])) == {"issue", "debug"}
    for issue in ("INSTRUMENT_DECLINED", "TRANSACTION_REFUSED", "PAYER_CANNOT_PAY"):
        assert cat["js.reason." + issue]
    assert "nothing was" not in cat["js.paypal_unconfirmed"]


def render(lang: str, calls: list[list]) -> list[str]:
    script = (UI / "static" / "app.js").read_text()
    fn = re.search(r"function reasonOf\(.*?\n}\n", script, re.S).group(0)
    strings = {k[3:]: v for k, v in i18n.CATALOGS[lang].items() if k.startswith("js.")}
    block = json.dumps(json.dumps(strings))
    fill = "{...v, reason: reasonOf(v.issue, v.reason)}"
    code = (
        "global.window = global;"
        f"global.document = {{getElementById: () => ({{textContent: {block}}})}};"
        + (UI / "static" / "i18n.js").read_text() + fn
        + f"console.log(JSON.stringify({json.dumps(calls)}.map(([k, v]) => T(k, {fill}))));"
    )
    node = shutil.which("node") or "node"
    out = subprocess.run([node, "-e", code], capture_output=True, text=True,  # noqa: S603
                         check=True)
    return json.loads(out.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_create_decline_shows_the_localized_reason_and_falls_back_to_paypals():
    calls = [["paypal_declined", {"issue": "INSTRUMENT_DECLINED", "reason": "english text"}],
             ["paypal_declined", {"issue": "SOMETHING_ELSE", "reason": "PayPal's own words"}]]
    pl = render("pl", calls)
    assert i18n.PL["js.reason.INSTRUMENT_DECLINED"] in pl[0] and "english text" not in pl[0]
    assert "PayPal's own words" in pl[1]  # no key: PayPal's reason is the fallback
    en = render("en", calls)
    assert i18n.EN["js.reason.INSTRUMENT_DECLINED"] in en[0]
    assert "PayPal's own words" in en[1]


def test_the_script_gates_both_paths_on_the_server_flag():
    script = (UI / "static" / "app.js").read_text()
    assert "funding_declined" in script and 'T("paypal_unconfirmed"' in script
    assert "execution_declined" in script and 'T("paypal_capture_unconfirmed"' in script
