"""The privacy notice describes the PayPal edition (Signed Studio at pay.udun.eu), in both
languages."""

import pytest

from studio.ui import legal_texts
from tests.test_app import app, client  # noqa: F401 - fixtures (login required)


def _text(lang):
    _, sections = legal_texts.page("privacy", lang)
    return " ".join(p for _, ps in sections for p in ps)


@pytest.mark.parametrize("lang", ["en", "pl"])
def test_privacy_is_written_for_the_paypal_edition(lang):
    text = _text(lang)
    assert "PayPal" in text and "pay.udun.eu" in text
    assert "PayPal (Europe) S.à r.l. et Cie, S.C.A." in text
    assert "SHA-256" in text and "SDK" in text
    assert "muse.udun.eu" not in text
    assert "no third-party scripts" not in text and "bez skryptów firm trzecich" not in text


@pytest.mark.parametrize("lang", ["en", "pl"])
def test_privacy_says_what_deletion_does_and_what_stays(lang):
    text = _text(lang)
    for word in ("mandat" if lang == "pl" else "mandate", "receipts" if lang == "en"
                 else "pokwitowa", "webhook"):
        assert word in text


@pytest.mark.parametrize("lang", ["en", "pl"])
def test_privacy_page_renders_paypal_and_host(client, lang):  # noqa: F811
    r = client.get(f"/privacy?lang={lang}", headers={"accept": "text/html"})
    assert "PayPal" in r.text and "pay.udun.eu" in r.text
