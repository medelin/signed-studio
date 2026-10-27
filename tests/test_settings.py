import os
import threading

import pytest

from studio.settings import (
    Budget,
    BudgetExceeded,
    ConfigError,
    ReadOnlyMode,
    Settings,
    read_env_file,
)

STRONG = "q7Vh2kLm9Xp4Rt8Wz1Nc6Bf3Jd5Gs0Ya"  # 34 chars, synthetic test value
JUDGE = "Tq4-wP9z_Lm2-Xc7_Rb5Hn"  # 22 chars, 4 classes, synthetic test value


def test_defaults_load_without_secrets():
    s = Settings.load({})
    assert s.meta_adapter == "mock"
    assert not s.read_only
    assert not s.judge_login_enabled
    assert len(s.session_secret) >= 32  # process-random fallback
    assert s.daily_budget_usd == 8.0


def test_random_fallback_differs_per_load():
    assert Settings.load({}).session_secret != Settings.load({}).session_secret


@pytest.mark.parametrize(
    "secret",
    ["", "short", "a" * 40, "abababababababababababababababab", "0123456789" * 3],
)
def test_weak_session_secret_refused_when_judge_login_on(secret):
    with pytest.raises(ConfigError, match="SESSION_SECRET"):
        Settings.load({"JUDGE_PASSWORD": "pw-for-tests", "SESSION_SECRET": secret})


def test_session_secret_must_not_contain_judge_password():
    with pytest.raises(ConfigError, match="SESSION_SECRET"):
        Settings.load({"JUDGE_PASSWORD": STRONG[:12], "SESSION_SECRET": STRONG})


def test_strong_secret_accepted_with_judge_login():
    s = Settings.load({"JUDGE_PASSWORD": JUDGE, "SESSION_SECRET": STRONG})
    assert s.judge_login_enabled
    assert s.session_secret == STRONG.encode()


def test_weak_secret_refused_even_without_judge_login():
    with pytest.raises(ConfigError, match="SESSION_SECRET"):
        Settings.load({"SESSION_SECRET": "short"})


def test_secrets_not_in_repr():
    s = Settings.load(
        {"JUDGE_PASSWORD": JUDGE, "SESSION_SECRET": STRONG, "MODEL_API_KEY": "mk-test"}
    )
    text = repr(s)
    assert STRONG not in text and JUDGE not in text and "mk-test" not in text


@pytest.mark.parametrize("value", ["maybe", "2", "on-ish"])
def test_bad_boolean_fails_fast(value):
    with pytest.raises(ConfigError, match="STUDIO_READ_ONLY"):
        Settings.load({"STUDIO_READ_ONLY": value})


def test_unknown_adapter_refused():
    with pytest.raises(ConfigError, match="META_ADAPTER"):
        Settings.load({"META_ADAPTER": "live"})


def test_graph_adapter_requires_meta_credentials():
    with pytest.raises(ConfigError, match="META_ACCESS_TOKEN"):
        Settings.load({"META_ADAPTER": "graph"})


@pytest.mark.parametrize("value", ["-1", "0", "nan", "inf", "abc"])
def test_bad_budget_refused(value):
    with pytest.raises(ConfigError, match="STUDIO_DAILY_BUDGET_USD"):
        Settings.load({"STUDIO_DAILY_BUDGET_USD": value})


def test_trusted_proxy_must_be_ip():
    assert Settings.load({"TRUSTED_PROXY": "10.0.0.2"}).trusted_proxies == frozenset({"10.0.0.2"})
    with pytest.raises(ConfigError, match="TRUSTED_PROXY"):
        Settings.load({"TRUSTED_PROXY": "proxy.example.com"})


def test_read_only_blocks_writes():
    s = Settings.load({"STUDIO_READ_ONLY": "1"})
    assert s.read_only
    with pytest.raises(ReadOnlyMode):
        s.require_writable()
    Settings.load({}).require_writable()  # no error


def test_budget_charge_and_exceeded():
    b = Budget(1.0)
    b.charge(0.4)
    b.charge(0.6)
    assert b.remaining() == pytest.approx(0.0)
    with pytest.raises(BudgetExceeded):
        b.charge(0.01)
    assert b.spent() == pytest.approx(1.0)  # a refused charge is not recorded


def test_budget_rejects_negative_and_nan():
    b = Budget(1.0)
    for bad in (-0.1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            b.charge(bad)


def test_budget_resets_daily():
    day = ["2026-10-27"]
    b = Budget(1.0, today=lambda: day[0])
    b.charge(1.0)
    with pytest.raises(BudgetExceeded):
        b.check(0.01)
    day[0] = "2026-10-28"
    b.charge(0.5)
    assert b.spent() == pytest.approx(0.5)


def test_budget_is_thread_safe():
    b = Budget(100.0)
    def work():
        for _ in range(1000):
            b.charge(0.01)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert b.spent() == pytest.approx(80.0)


def test_env_file_parsed_and_mode_enforced(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# comment\nMODEL_API_KEY=abc # trailing\nEMPTY=\nQUOTED='x y'\n")
    os.chmod(path, 0o600)
    assert read_env_file(path) == {"MODEL_API_KEY": "abc", "EMPTY": "", "QUOTED": "x y"}
    os.chmod(path, 0o644)
    with pytest.raises(ConfigError, match="permissions"):
        read_env_file(path)


def test_budget_record_counts_incurred_cost_even_over_cap():
    b = Budget(1.0)
    b.charge(0.9)
    b.record(0.5)  # the call already happened: its cost must be counted
    assert b.spent() == pytest.approx(1.4)
    with pytest.raises(BudgetExceeded):
        b.check()
    with pytest.raises(ValueError):
        b.record(float("nan"))


@pytest.mark.parametrize(
    "password",
    [
        "pw-for-tests",                 # too short
        "correct horse battery staple",  # long but only lower case + spaces (2 classes)
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaa",  # one class repeated
        "Aa1Aa1Aa1Aa1Aa1Aa1Aa1Aa1",      # 3 classes but almost no distinct characters
        "abcdefghijklmnopqrstuvwxyz",    # one class
    ],
)
def test_weak_judge_password_refused_at_startup(password):
    with pytest.raises(ConfigError, match="JUDGE_PASSWORD"):
        Settings.load({"JUDGE_PASSWORD": password, "SESSION_SECRET": STRONG})


def test_generated_judge_password_accepted():
    import secrets

    for _ in range(50):  # the documented generator always passes
        pw = secrets.token_urlsafe(24)
        try:
            Settings.load({"JUDGE_PASSWORD": pw, "SESSION_SECRET": STRONG})
        except ConfigError:
            # token_urlsafe can, very rarely, miss a class; it must then be the class rule
            assert sum(any(f(c) for c in pw) for f in (str.islower, str.isupper,
                                                        str.isdigit)) < 3


def test_env_example_documents_password_generation():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text()
    assert "secrets.token_urlsafe(24)" in text


# -- I6: the daily budget is reserved under the lock and survives restarts -------------


def test_reservations_are_atomic_under_concurrency():
    b = Budget(1.0)
    ok = []

    def work():
        try:
            b.reserve(0.3)
            ok.append(1)
        except BudgetExceeded:
            pass

    threads = [threading.Thread(target=work) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ok) == 3  # 0.9 reserved; a fourth would exceed the cap


def test_settle_swaps_reservation_for_actual_cost():
    b = Budget(1.0)
    b.reserve(0.5)
    assert b.remaining() == pytest.approx(0.5)
    b.settle(0.5, 0.1)
    assert b.spent() == pytest.approx(0.1)
    assert b.remaining() == pytest.approx(0.9)
    b.reserve(0.9)  # the released part is available again
    b.settle(0.9, 1.2)  # an incurred cost is counted even past the cap
    assert b.spent() == pytest.approx(1.3)


def test_budget_rebuilt_from_todays_model_call_receipts():
    import time as _time

    from studio.receipts import Ledger

    led = Ledger()
    led.append("run-1", agent="a", kind="model_call", cost_usd=0.75)
    led.append("run-1", agent="a", kind="model_call", cost_usd=0.5)
    led.append("run-1", agent="a", kind="review", cost_usd=9.0)  # not a model call
    b = Budget.from_ledger(1.0, led)
    assert b.spent() == pytest.approx(1.25)
    with pytest.raises(BudgetExceeded):
        b.reserve(0.01)
    tomorrow = Budget.from_ledger(1.0, led, now=_time.time() + 2 * 86_400)
    assert tomorrow.spent() == 0.0


def test_product_photo_defaults_to_the_synthetic_mug_and_is_configurable(tmp_path):
    from studio.settings import DEFAULT_PRODUCT_PHOTO

    s = Settings.load({})
    assert s.product_photo == DEFAULT_PRODUCT_PHOTO and s.product_photo.exists()
    assert s.product_concept == "mug"
    own = tmp_path / "mine.png"
    s = Settings.load({"STUDIO_PRODUCT_PHOTO": str(own), "STUDIO_PRODUCT_CONCEPT": "trophy"})
    assert s.product_photo == own and s.product_concept == "trophy"


@pytest.mark.parametrize("concept", ["mug<|box|>", "x" * 61, "a;b"])
def test_product_concept_must_be_a_noun_phrase(concept):
    with pytest.raises(ConfigError):
        Settings.load({"STUDIO_PRODUCT_CONCEPT": concept})
