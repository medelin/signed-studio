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
    s = Settings.load({"JUDGE_PASSWORD": "pw-for-tests", "SESSION_SECRET": STRONG})
    assert s.judge_login_enabled
    assert s.session_secret == STRONG.encode()


def test_weak_secret_refused_even_without_judge_login():
    with pytest.raises(ConfigError, match="SESSION_SECRET"):
        Settings.load({"SESSION_SECRET": "short"})


def test_secrets_not_in_repr():
    s = Settings.load(
        {"JUDGE_PASSWORD": "pw-for-tests", "SESSION_SECRET": STRONG, "MODEL_API_KEY": "mk-test"}
    )
    text = repr(s)
    assert STRONG not in text and "pw-for-tests" not in text and "mk-test" not in text


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
