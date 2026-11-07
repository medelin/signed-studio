"""Task E3: live tests are opt-in, the installer refuses local secrets, mandate amounts
respect zero-decimal currencies."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from studio.funding import display_amount
from studio.ui import i18n

ROOT = Path(__file__).resolve().parent.parent


def test_live_module_is_skipped_without_the_env_var(tmp_path):
    creds = tmp_path / "paypal-sandbox.env"
    creds.write_text("PAYPAL_CLIENT_ID=x\nPAYPAL_CLIENT_SECRET=y\n")
    env = {k: v for k, v in os.environ.items() if k != "RUN_PAYPAL_LIVE_TESTS"}
    env["MUSE_PAY_PAYPAL_ENV"] = str(creds)
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-m", "live", "-rs",
         "tests/test_paypal_live.py"], cwd=ROOT, env=env, capture_output=True, text=True)
    assert "skipped" in out.stdout and "passed" not in out.stdout and "failed" not in out.stdout


def _script_copy(tmp_path):
    (tmp_path / "deploy").mkdir()
    shutil.copy(ROOT / "deploy" / "pay-install.sh", tmp_path / "deploy" / "pay-install.sh")
    return tmp_path / "deploy" / "pay-install.sh"


def test_installer_refuses_a_planted_secret(tmp_path):
    script = _script_copy(tmp_path)
    (tmp_path / "sandbox.env").write_text("PAYPAL_CLIENT_SECRET=x\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "fixture.key").write_text("fine: inside tests/")
    out = subprocess.run(["bash", str(script), "abcdef1"], capture_output=True, text=True)  # noqa: S603,S607
    assert out.returncode == 1
    assert "sandbox.env" in out.stderr and "fixture.key" not in out.stderr


def test_installer_does_not_refuse_a_clean_tree(tmp_path):
    script = _script_copy(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "fixture.key").write_text("fine")
    out = subprocess.run(["bash", str(script), "abcdef1"], capture_output=True, text=True)  # noqa: S603,S607
    assert "refusing to install" not in out.stderr  # stops later (not root / no env)


def test_mandate_summary_uses_zero_decimal_amounts():
    env = Environment(loader=FileSystemLoader(ROOT / "studio" / "ui" / "templates"))  # noqa: S701
    env.filters["money"] = display_amount
    tpl = env.from_string('{{ text("mandate.limits_pay", charge=1500 | money("HUF"), '
                          'cap=300000 | money("HUF"), currency="HUF", days=7, kinds="x") }}'
                          ' {{ 1500 | money("GBP") }}')
    out = tpl.render(text=lambda key, **v: i18n.text("en", key, **v))
    assert "1500 HUF" in out and "15.00 HUF" not in out and "15.00" in out
    # the real template uses the filter, not a division
    src = (ROOT / "studio/ui/templates/account_assistants.html").read_text()
    assert "/ 100" not in src and "| money(l.currency)" in src
