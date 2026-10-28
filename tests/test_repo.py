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


def test_cassette_images_are_small_and_not_duplicated():
    """Day 2 fix round: recorded PNGs are downscaled and the producer loop reuses the
    image_series files instead of carrying its own copies."""
    import hashlib
    import json

    from PIL import Image

    root = ROOT
    files = sorted((root / "fixtures" / "cassettes" / "files").glob("*.png"))
    assert files and not [f for f in files if f.name.startswith("producer_loop-")]
    for f in files:
        assert f.stat().st_size <= 300_000, f.name
        assert max(Image.open(f).size) <= 512, f.name
    digests = [hashlib.sha256(f.read_bytes()).hexdigest() for f in files]
    assert len(set(digests)) == len(digests)
    loop = json.dumps(json.loads((root / "fixtures" / "cassettes" /
                                  "producer_loop.json").read_text()))
    assert "@file:fixtures/cassettes/files/image_series-1.png" in loop


def test_readme_describes_sam_as_streamed_transport_batch_parse():
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    assert "streamed transport, batch parse" in " ".join(readme.split())


def test_source_has_no_invisible_characters():
    """Zero-width, bidi and other format characters are written as ``\\u`` escapes, so a
    reviewer can see them (and none hides in code)."""
    import unicodedata
    root = ROOT
    bad = []
    for path in [*root.glob("studio/**/*"), *root.glob("tests/**/*"), *root.glob("scripts/*")]:
        if path.suffix not in (".py", ".js", ".html", ".css", ".md", ".yaml"):
            continue
        text = path.read_text(encoding="utf-8")
        bad += [f"{path.relative_to(root)}: U+{ord(c):04X}" for c in text
                if unicodedata.category(c) == "Cf"]
    assert bad == []
