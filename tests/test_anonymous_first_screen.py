"""What a visitor without an account sees first: the no-login path is the primary action
(the recorded replay at /try), the proof page is one link away, and sign-in stays below."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from studio.ui import i18n
from tests.test_assistants import BASE, make_app


def anonymous(tmp_path, **env):
    return TestClient(make_app(tmp_path, **env), base_url=BASE)


def test_login_leads_with_the_no_account_button_then_the_proof_link_then_sign_in(tmp_path):
    html = anonymous(tmp_path).get("/login").text
    button = html.index(i18n.text("en", "login.try_button"))
    proof = html.index(i18n.text("en", "login.proof_link"))
    form = html.index('id="lp-user"')
    assert button < proof < form
    assert '<a class="ob-button" href="/try"' in html
    assert 'href="/trust"' in html[proof - 80:proof + 80]
    assert "See it work without an account (1 min)" in html
    assert "Proof anyone can check" in html


def test_the_login_page_in_polish_points_at_the_polish_replay(tmp_path):
    html = anonymous(tmp_path).get("/login?lang=pl").text
    assert i18n.text("pl", "login.try_button") in html and 'href="/pl/try"' in html


def test_a_login_required_instance_sends_the_visitor_to_that_screen(tmp_path):
    c = anonymous(tmp_path, STUDIO_REQUIRE_LOGIN="1")
    r = c.get("/", follow_redirects=False)
    assert r.status_code in (302, 303) and r.headers["location"].startswith("/login")
    assert i18n.text("en", "login.try_button") in c.get("/", follow_redirects=True).text


def test_the_home_page_offers_the_same_path_to_a_signed_out_visitor(tmp_path):
    html = anonymous(tmp_path).get("/").text
    assert i18n.text("en", "login.try_button") in html and 'href="/try"' in html


@pytest.mark.parametrize("key", ["login.try_button", "login.try_href", "login.proof_link",
                                 "login.proof_href"])
def test_the_strings_exist_in_en_and_pl(key):
    assert i18n.EN[key].strip() and i18n.PL[key].strip()
