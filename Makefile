.PHONY: install test lint demo demo-replay verify smoke-live
PY ?= .venv/bin/python

install:
	uv venv --quiet .venv && uv pip install --quiet --python $(PY) -e '.[dev]'

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check studio tests scripts
	$(PY) -m mypy studio

demo:
	META_ADAPTER=mock $(PY) -m uvicorn studio.app:app --port 8000

demo-replay:
	STUDIO_REPLAY=1 META_ADAPTER=mock $(PY) -m uvicorn studio.app:app --port 8000

verify:
	$(PY) -m studio.cli verify $(or $(FILE),receipts.jsonl)

smoke-live:
	$(PY) -m studio.cli smoke-live
