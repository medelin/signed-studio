"""Repository hygiene the build plan asks for."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_pre_commit_runs_gitleaks_and_ruff():
    text = (ROOT / ".pre-commit-config.yaml").read_text()
    assert "gitleaks" in text and "ruff" in text
    assert "rev: v" in text  # pinned


def test_build_log_directory_is_documented():
    readme = ROOT / "docs" / "build-log" / "README.md"
    assert "Muse Code" in readme.read_text()


@pytest.mark.parametrize("phrase", ["TOFU", "--min-seq", "ledger.pub"])
def test_readme_explains_trust_on_first_use_for_verify(phrase):
    assert phrase in (ROOT / "README.md").read_text()
