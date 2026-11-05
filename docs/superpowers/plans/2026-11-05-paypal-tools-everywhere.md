# PayPal tools everywhere — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use every tool PayPal promoted in the hackathon webinars inside Signed Studio — the PayPal Agent Toolkit (behind the signature/mandate gate), PayPal's MCP server alongside ours, sandbox negative testing, a Postman collection, AG Grid, the APIMatic and PayPal AI Toolkit coding plugins — and show each one in the product, the docs and the demo.

**Architecture:** The Agent Toolkit's `shared` tool registry (handlers + pydantic parameters, Apache-2.0) is vendored and run on the studio's own receipted sandbox `PayPalClient` through a small adapter, so every toolkit call is a `paypal.call` receipt, payer data is stripped, and only read tools are reachable; money still moves only through signed approvals or a signed mandate. New assistant ops expose the read tools, a reconciliation of the owner's fundings against PayPal's transaction list, and refund *proposals* that a human signs. A `/money` page shows the same data in AG Grid.

**Tech Stack:** Python 3.12+, FastAPI, SQLite, httpx, pydantic v2; vendored `paypal_agent_toolkit/shared` 1.11.0; vendored AG Grid Community (MIT) JS; pytest; ruff.

**Spec:** `docs/plans/2026-11-05-paypal-judging-fit.md` (priorities P1–P4) and the webinar research notes summarised there (PayPal AI Toolkit, Agent Toolkit tools, `PayPal-Mock-Response` negative testing, APIMatic and AG Grid sessions).

## Global Constraints

- PayPal **sandbox only**: base URL stays the constant `studio.paypal.SANDBOX_BASE`; nothing may add a live URL or key.
- **No new pip dependencies** and no change to `deploy/requirements.lock`: third-party code is vendored with its license.
- **No payer data** (name, e-mail, address, payer id, phone) in receipts, API/MCP answers, logs or the money page.
- An account sees only its **own** fundings and the PayPal transactions that match them (by `custom_field` = a funding's `content_sha256`, or by the funding's order/authorization/capture id).
- Assistant tools **never move money**: refunds are proposals that create a pending `funding.refund` approval with a `sign_url`.
- CSP stays `script-src 'self'` (+ the existing PayPal origins in rest mode): vendored JS is served from `/static/vendor/`.
- Every user-visible UI string exists in **EN and PL** (`studio/ui/i18n*.py`; `tests/test_i18n.py` rules).
- `studio verify` must accept ledgers containing the new receipts (run it in the task tests).
- Commits: author `Piotr Kaczmarski <5693149+medelin@users.noreply.github.com>`, SSH-signed (repo config), **no `Co-Authored-By` trailer, no "Generated with" line**.
- Tests: `.venv/bin/python -m pytest -q -p no:cacheprovider <files>`; lint: `.venv/bin/ruff check studio tests`.
- English names everywhere in code, keys and paths.

## Review Focus

1. A capture or authorization made minutes ago is not yet in PayPal's Transaction Search (it lags up to ~3 h): reconciliation must say `pending`, never `missing` or `mismatch`, for moves younger than `RECON_LAG_S` (Task 2 test `test_recent_move_is_pending_not_missing`).
2. Two accounts on one sandbox merchant: account B must never see account A's transactions through `paypal_tool` or `paypal_transactions` (Task 1 `test_foreign_transactions_are_filtered_out`, Task 2 `test_reconcile_is_per_account`).
3. A toolkit answer full of payer fields (`payer_info`, `shipping_info`, e-mail strings): nothing of it reaches the receipt or the answer (Task 1 `test_payer_data_never_leaves_the_adapter`).
4. A `PayPal-Mock-Response` code outside the allow-list, or set on a non-capture step: refused before any PayPal call (Task 3 `test_unknown_mock_code_is_refused`).
5. `/money` for an account with no fundings, and with PayPal off (`PAYPAL_ADAPTER` empty): renders an empty state, no 500 (Task 4 `test_money_page_empty_and_paypal_off`).

---

### Task 1: Vendored Agent Toolkit on the studio's receipted client (read tools only)

**Files:**
- Create: `studio/vendor/paypal_agent_toolkit/` — copy of `paypal_agent_toolkit/__init__.py` and `paypal_agent_toolkit/shared/` from the PyPI wheel `paypal_agent_toolkit-1.11.0-py3-none-any.whl` **excluding** `shared/api.py`, `shared/paypal_client.py`, `shared/telemetry.py`, `shared/logger_util.py`; add `LICENSE` (Apache-2.0 text from github.com/paypal/agent-toolkit) and `NOTICE.md` (source, version, wheel SHA-256, list of removed files, any local edit). Fix imports only if a kept module imports a removed one.
- Create: `studio/paypal/agent_tools.py`
- Modify: `pyproject.toml` (ruff `extend-exclude` for `studio/vendor`), `.gitleaksignore` only if the vendored code trips it
- Test: `tests/test_paypal_agent_tools.py`

**Interfaces:**
- Produces:
  - `READ_TOOLS: frozenset[str] = frozenset({"list_transactions", "get_order_details", "list_disputes", "get_dispute"})`
  - `class ToolRefused(Exception)` with `.code: str` (`"not_allowed"`, `"invalid_arguments"`, `"paypal_error"`)
  - `def tool_specs() -> list[dict]` — for each `READ_TOOLS` name: `{"name", "description", "input_schema"}` taken from the vendored registry (`description` from its prompt, `input_schema` from the pydantic parameters model's `model_json_schema()`).
  - `def run_read_tool(*, client: PayPalClient, ledger: Ledger, conn, chain_id: str, account_refs: AccountRefs, name: str, params: dict) -> dict` — runs the vendored tool's `execute` with an adapter whose `get(uri, payload)` calls `client.get(uri + "?" + urlencode(payload))`, returns the JSON as a string, and appends one receipt per HTTP call: `kind=PAYPAL_KIND`, `agent="treasury"`, `provider="paypal"`, `details={"step": "paypal.agent_tool", "tool": name, "path": <path without query>, "status": <int>, "ok": bool, "body_sha256": <reply sha>}` on chain `chain_id`. Returns the tool's answer after `redact()` and `only_own()`.
  - `@dataclass(frozen=True) class AccountRefs: content_sha256: frozenset[str]; paypal_ids: frozenset[str]` and `def account_refs(conn, account_id: str) -> AccountRefs` (from the account's fundings: `content_sha256`, `order_id`, `authorization_id`, `capture_id`).
  - `def redact(obj: Any) -> Any` — drops keys `payer_info`, `shipping_info`, `payer`, `payment_source`, `buyer`, `seller`, `email_address`, `phone`, `name`, `address`, recursively; strings matching an e-mail regex become `"[redacted]"`.
  - `def only_own(name: str, answer: dict, refs: AccountRefs) -> dict` — `list_transactions`: keep `transaction_details` whose `transaction_info.custom_field` ∈ `refs.content_sha256` or `transaction_info.transaction_id`/`paypal_reference_id` ∈ `refs.paypal_ids`; `get_order_details`/`get_dispute`: raise `ToolRefused("not_allowed")` unless the id is in `refs.paypal_ids` (for disputes: the disputed transaction id); `list_disputes`: keep items whose `disputed_transactions[*].seller_transaction_id` ∈ `refs.paypal_ids`.
- Write tools (`create_order`, `pay_order`, `create_invoice`, `accept_dispute_claim`, …) and `get_merchant_insights` (unsupported in sandbox) → `ToolRefused("not_allowed")` before any call.

- [ ] **Step 1: Write the failing tests** in `tests/test_paypal_agent_tools.py` using an `httpx.MockTransport` sandbox (token endpoint + `/v1/reporting/transactions` + `/v2/checkout/orders/{id}` + `/v1/customer/disputes`) and a temp ledger:
  - `test_tool_specs_cover_exactly_the_read_tools` — names == `READ_TOOLS`, each has a JSON-schema `input_schema` with `"type": "object"`.
  - `test_write_tools_are_refused_before_any_call` — `run_read_tool(name="create_order", …)` and `"get_merchant_insights"` raise `ToolRefused` with `code == "not_allowed"`; the transport saw 0 requests.
  - `test_list_transactions_is_receipted` — one call → one receipt with `details["step"] == "paypal.agent_tool"`, `details["tool"] == "list_transactions"`, `details["path"] == "/v1/reporting/transactions"`; the exported ledger passes `studio.verify` (use the helper the existing PayPal tests use).
  - `test_payer_data_never_leaves_the_adapter` — fake answer carries `payer_info.email_address = "buyer@example.com"`, `shipping_info.name`; neither `"buyer@example.com"` nor `"shipping_info"` appears in the returned JSON or in the ledger export.
  - `test_foreign_transactions_are_filtered_out` — fake answer with two transactions (custom_field = own sha, custom_field = other sha) → only the own one is returned.
  - `test_order_details_of_a_foreign_order_is_refused` — `get_order_details` for an id not in `AccountRefs.paypal_ids` → `ToolRefused("not_allowed")`, 0 order requests.
- [ ] **Step 2: Run them; expect import failures.** `.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_paypal_agent_tools.py` → FAIL (module missing).
- [ ] **Step 3: Vendor the toolkit and implement `studio/paypal/agent_tools.py`** per the Interfaces (adapter `get` returns `json.dumps(reply.json)`; a `PayPalError` becomes `ToolRefused("paypal_error")` after its `ok: false` receipt).
- [ ] **Step 4: Run the tests and ruff; expect PASS and clean.**
- [ ] **Step 5: Commit** — `feat(paypal): PayPal Agent Toolkit read tools on the receipted sandbox client`.

### Task 2: Reconciliation and assistant ops (`paypal_transactions`, `paypal_tool`, `propose_refund`)

**Files:**
- Create: `studio/paypal/reconcile.py`
- Modify: `studio/assistants.py` (three `Op`s), `studio/routes_api.py` (REST routes `GET /api/v1/paypal/transactions`, `POST /api/v1/paypal/tools/{name}`, `POST /api/v1/fundings/{funding_id}/refund-proposal`), `docs/assistants.md` (the three tools)
- Test: `tests/test_paypal_reconcile.py`, additions to `tests/test_assistants.py`

**Interfaces:**
- Consumes: Task 1 `run_read_tool`, `account_refs`, `AccountRefs`, `ToolRefused`; existing `studio.funding.of_item/get/moves`, the existing refund-approval builder behind `POST /api/funding/{funding_id}/refund` in `studio/routes_paypal.py` (extract it into a function both routes call).
- Produces:
  - `RECON_LAG_S = 3 * 3600`
  - `@dataclass class ReconLine: funding_id: str; move: str; paypal_id: str; amount_minor: int; currency: str; status: str` where `status ∈ {"matched", "pending", "missing", "amount_mismatch"}`
  - `def reconcile(*, conn, client, ledger, account_id: str, now: float) -> list[ReconLine]` — one `list_transactions` call (via `run_read_tool`) over the window from the oldest open funding move to now (≤ 31 days per call, as the toolkit requires); each capture/authorization move of the account is matched by PayPal id or `custom_field`; younger than `RECON_LAG_S` and not found → `pending`.
  - Op `paypal_transactions` (scope `read`): `{"lines": [ReconLine as dict…], "matched": n, "pending": n, "missing": n, "amount_mismatch": n}`.
  - Op `paypal_tool` (scope `read`): args `{"name": enum(READ_TOOLS), "params": object}` → the redacted, own-only answer; description lists the four tools and says it cannot move money.
  - Op `propose_refund` (scope `draft`): args `{"funding_id": str, "amount": str}` → `{"approval_id", "sign_url"}`; it never refunds.
  - `ToolRefused.code` maps to `_STATUS`: `not_allowed` 403, `invalid_arguments` 422, `paypal_error` 502.

- [ ] **Step 1: Failing tests** in `tests/test_paypal_reconcile.py`: `test_capture_is_matched_by_id`, `test_custom_field_match_counts`, `test_recent_move_is_pending_not_missing` (move 10 min old, absent → `pending`), `test_old_absent_move_is_missing` (4 h old → `missing`), `test_amount_mismatch` (PayPal 100.00 vs funding 220.50), `test_reconcile_is_per_account` (account B's call returns none of A's lines). In `tests/test_assistants.py`: `test_paypal_tool_op_is_read_scope_and_refuses_write_tools`, `test_propose_refund_returns_sign_url_and_moves_nothing` (no `refund` move recorded, a pending `funding.refund` approval exists), `test_ops_listed_on_mcp` (the three names in the MCP `tools/list`).
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement** `reconcile.py`, the ops, the REST routes, extract the refund-approval builder.
- [ ] **Step 4: Run the two files + `tests/test_paypal_routes.py tests/test_paypal_vault.py tests/attack`; expect PASS; ruff clean.**
- [ ] **Step 5: Commit** — `feat(assistants): PayPal transactions, read tools and refund proposals for the owner's assistant`.

### Task 3: Sandbox negative testing (`PayPal-Mock-Response`) shown to the owner

**Files:**
- Modify: `studio/paypal/client.py` (`post(..., mock_response: str | None = None)` → header `PayPal-Mock-Response: {"mock_application_codes": "<code>"}`), `studio/paypal/orders.py` (`capture(..., mock_response=None)`), `studio/paypal/money.py` (`Money.capture(..., mock_response=None)`, recorded as `details["mock_response"] = code` in the receipt), the activation path that calls `Money.capture` (pass the code through), the activation UI (a checkbox "Sandbox: simulate a declined card on capture", EN/PL) and its JS
- Test: `tests/test_paypal_negative.py`

**Interfaces:**
- Produces: `MOCK_CODES: frozenset[str] = frozenset({"INSTRUMENT_DECLINED", "TRANSACTION_REFUSED"})` in `studio/paypal/client.py`; the activation request field `simulate` (string, optional, outside the signed payload).

- [ ] **Step 1: Failing tests:** `test_mock_header_sent_only_with_an_allowed_code` (transport sees the exact header value `{"mock_application_codes":"INSTRUMENT_DECLINED"}`), `test_unknown_mock_code_is_refused` (`"FOO"` → `ValueError` before any request), `test_declined_capture_leaves_money_held_and_activation_retryable` (fake sandbox answers 422 `UNPROCESSABLE_ENTITY`/`INSTRUMENT_DECLINED`; funding state stays `authorized`, an `ok: false` receipt with `issue == "INSTRUMENT_DECLINED"` and `mock_response == "INSTRUMENT_DECLINED"`, the campaign stays PAUSED, the owner can sign activation again), `test_simulate_is_not_part_of_the_signed_bytes` (the same signature verifies with and without `simulate`), and the exported ledger passes `studio verify`.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement** the plumbing; the UI shows PayPal's reason text from `client.REASONS` for the issue.
- [ ] **Step 4: Run the file + `tests/test_paypal_routes.py tests/test_i18n.py tests/attack`; expect PASS; ruff clean.**
- [ ] **Step 5: Commit** — `feat(paypal): show a sandbox-declined capture (PayPal-Mock-Response), money stays held`.

### Task 4: `/money` page with AG Grid

**Files:**
- Create: `studio/ui/static/vendor/ag-grid-community.min.js` (AG Grid Community **34.x**, the `dist/ag-grid-community.min.js` build from npm, exact version pinned) + `studio/ui/static/vendor/AG-GRID-LICENSE.txt` (MIT) + a line in `studio/vendor/NOTICE.md` with version and SHA-256; `studio/ui/templates/money.html`; `studio/ui/static/money.js`; `studio/routes_money.py`
- Modify: `studio/app.py` (register routes), `studio/ui/templates/base.html` (nav link "Money"/"Pieniądze", shown when PayPal is on), `studio/ui/i18n.py` (+PL)
- Test: `tests/test_money_page.py`

**Interfaces:**
- Consumes: Task 2 `reconcile` (optional column), `studio.funding` rows and moves, mandate charges.
- Produces: `GET /money` (session required, HTML); `GET /api/money` (session required) → `{"currency", "rows": [{"funding_id", "run_id", "created", "state", "amount", "authorized", "captured", "refunded", "via": "checkout"|"mandate", "receipt_ids": [...], "recon": "matched"|"pending"|"missing"|"amount_mismatch"|null}]}`; a per-row "Explain" button calling the existing `POST /api/funding/{funding_id}/treasurer`.

- [ ] **Step 1: Failing tests:** `test_money_requires_a_session` (anon → 303 to /login for HTML, 401 for `/api/money`), `test_money_lists_only_own_fundings`, `test_money_page_empty_and_paypal_off`, `test_money_page_loads_vendored_grid_under_csp` (page references `/static/vendor/ag-grid-community.min.js`; CSP header unchanged), `test_money_strings_exist_in_en_and_pl`.
- [ ] **Step 2: Run; expect FAIL.**
- [ ] **Step 3: Implement** (grid columns from the JSON keys, money formatted with `funding.format_amount`, no inline scripts).
- [ ] **Step 4: Run the file + `tests/test_i18n.py tests/test_app.py tests/test_ui_studio.py`; expect PASS; ruff clean.**
- [ ] **Step 5: Commit** — `feat(ui): money page in AG Grid — holds, captures, voids and mandate charges with receipts`.

### Task 5: Postman collection

**Files:**
- Create: `scripts/postman_collection.py` (builds a Postman v2.1 collection from `studio.routes_api` OpenAPI plus a "PayPal sandbox (what the studio calls)" folder: OAuth token, create order AUTHORIZE, authorize, capture, void, refund, Vault setup token, `GET /v1/reporting/transactions`; variables `{{base_url}}`, `{{token}}`, `{{paypal_client_id}}`, `{{paypal_client_secret}}`, never values), `docs/postman/signed-studio.postman_collection.json` (generated), a Makefile target `postman`
- Test: `tests/test_postman_collection.py`

- [ ] **Step 1: Failing tests:** `test_collection_is_v21_and_up_to_date` (regenerating equals the committed file), `test_every_api_op_has_a_request` (each `/api/v1` path in the OpenAPI appears once), `test_no_secret_values` (no `msl_tok_`, no `Bearer ` + non-variable, client id/secret only as `{{…}}`).
- [ ] **Step 2–4:** implement, run, pass, ruff clean.
- [ ] **Step 5: Commit** — `docs: Postman collection for the assistant API and the PayPal sandbox calls`.

### Task 6: Docs, demo script and submission text

**Files:**
- Modify: `README.md` (section "Agentic commerce, the PayPal way": our signed spending mandate vs PayPal Agent Ready delegated tokens — max amount, currency, expiry — built on public Orders v2 + Vault v3 because Agent Ready/Store Sync are gated; the Agent Toolkit behind the gate; PayPal's sandbox MCP server alongside ours; negative testing; the money page; Postman), `PAYPAL.md` (APIs table: Transaction Search, Disputes read), `docs/assistants.md` (Claude Desktop config with **both** MCP servers: ours `/mcp` and PayPal's `https://mcp.sandbox.paypal.com` — "PayPal's tools for reading, our server for anything that moves money"), `docs/demo-script-paypal.md` (beats: assistant reads PayPal transactions and proposes a refund the owner signs; one declined capture; the money page), `docs/devpost.md` (Tools + Built with: PayPal Agent Toolkit, PayPal MCP server, PayPal AI Toolkit, APIMatic context plugin, AG Grid, Postman), `docs/BUILD-LOG.md` (dated entries for each task and the controller's tool runs below)
- Test: `tests/test_docs.py` (add needles: `"Agent Toolkit"`, `"mcp.sandbox.paypal.com"`, `"PayPal-Mock-Response"`, `"AG Grid"`, `"docs/postman/"`)

- [ ] **Step 1: Add the needles; run `tests/test_docs.py`; expect FAIL.**
- [ ] **Step 2: Write the docs; run; expect PASS.**
- [ ] **Step 3: Commit** — `docs: PayPal Agent Toolkit, MCP, negative tests, money page and Postman in README, assistants, demo and Devpost text`.

### Controller tasks (not for implementer subagents; done in the main session)

- C1. Install the **PayPal AI Toolkit** plugin and the **APIMatic context plugin** in Claude Code before Task 1; run `/paypal:doctor` on the repo; fix what it reports or record why not; quote the result in BUILD-LOG (Task 6).
- C2. Enable **Transaction Search** for the sandbox REST app in the PayPal developer dashboard if `GET /v1/reporting/transactions` answers 403 (operator, in neo).
- C3. After Task 6: full suite (no game running on xanadu), push to Forgejo and GitHub, deploy to pay.udun.eu with `deploy/pay-install.sh`, check `/money`, a live `paypal_transactions` call and a declined capture on the sandbox.
