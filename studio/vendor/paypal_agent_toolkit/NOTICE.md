# Vendored: PayPal Agent Toolkit

- Source: https://github.com/paypal/agent-toolkit (Apache-2.0, see `LICENSE`), as published on PyPI
- Package: `paypal-agent-toolkit` 1.11.0
- Wheel: `paypal_agent_toolkit-1.11.0-py3-none-any.whl`
  (https://files.pythonhosted.org/packages/27/a5/5019931e7e8d02246b5b194c330f9df8ee9ca00d209041b7d77bdbb34dca/paypal_agent_toolkit-1.11.0-py3-none-any.whl)
- Wheel SHA-256: `207614c35541426405466620c96c554de9d980d558a9fd2cd1ed86cc3ffcc06c`
- Copied: `paypal_agent_toolkit/__init__.py` and `paypal_agent_toolkit/shared/` (tool registry,
  parameter models, prompts, handlers). The framework wrappers (openai, langchain, crewai,
  bedrock) are not included.

## Removed files

- `shared/api.py`, `shared/paypal_client.py`, `shared/telemetry.py`, `shared/logger_util.py`:
  the toolkit's own HTTP client, telemetry and request logging. The studio runs the handlers on
  its receipted sandbox client instead (`studio/paypal/agent_tools.py`).
- `shared/constants.py`: only imported by the removed `paypal_client.py`; it holds the live API
  URL, which the studio must not carry.

## Local edits

- Empty `__init__.py` added in `shared/disputes`, `shared/insights`, `shared/invoices`,
  `shared/tracking` and `shared/transactions` so that packaging (`setuptools.packages.find`)
  includes them. No other file was changed; no import needed fixing.

The studio exposes only the read tools (`list_transactions`, `get_order_details`,
`list_disputes`, `get_dispute`); see `studio/paypal/agent_tools.py`.
