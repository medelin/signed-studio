.PHONY: install test lint demo demo-replay serve-replay record-demo verify smoke-live eval
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
	$(PY) -m studio.cli demo --set $(or $(SET),demo)

serve-replay:
	STUDIO_REPLAY=1 STUDIO_REPLAY_SET=$(or $(SET),demo) META_ADAPTER=mock $(PY) -m uvicorn studio.app:app --port 8000

record-demo:
	$(PY) -m studio.cli demo --record --set $(or $(SET),demo)

verify:
	$(PY) -m studio.cli verify $(or $(FILE),receipts.jsonl) $(ARGS)

smoke-live:
	$(PY) -m studio.cli smoke-live

eval:
	$(PY) scripts/eval.py --write docs/eval.md
