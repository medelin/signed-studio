"""Public privacy and data-deletion pages: reachable without signing in, EN and PL."""

import pytest

from studio.ui import legal_texts
from tests.test_app import app, client  # noqa: F401 - fixtures (login required)


@pytest.mark.parametrize("path", ["/privacy", "/data-deletion"])
@pytest.mark.parametrize("lang", ["en", "pl"])
def test_legal_pages_are_public_in_both_languages(client, path, lang):  # noqa: F811
    r = client.get(f"{path}?lang={lang}", headers={"accept": "text/html"},
                   follow_redirects=False)
    assert r.status_code == 200
    title, _ = legal_texts.page(path.strip("/"), lang)
    assert title in r.text and legal_texts.CONTACT in r.text


def test_both_languages_have_the_same_sections():
    for by_lang in legal_texts.PAGES.values():
        en, pl = by_lang["en"][1], by_lang["pl"][1]
        assert [len(p) for _, p in en] == [len(p) for _, p in pl]
