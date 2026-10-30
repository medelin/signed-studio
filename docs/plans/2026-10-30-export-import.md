# Plan D — Export and import of leads and expenses, in several formats, with API endpoints

**Goal.** An owner (and their accountant, CRM or assistant) gets the studio's data out in
the formats their tools read, and can bring existing leads in. The selling point is
"savings, security, settlements": every ad expense is tied to the owner's signature and a
receipt, so the monthly statement reconciles with Meta's invoice line by line.

## Data that exists
- Leads: table `leads` in `studio/db.py` (name, email, phone, source, message, score,
  temp, created_ts) + `tasks`; pages `/leads` (`routes_inbox.py`/`leads.html`; find the
  route) masked by `personal(app, request)` for trial/demo accounts
  (`Account.sees_personal_data`).
- Spend: `insights_snapshots` (`studio/meta/measurement.py`; body = Meta insights incl.
  `spend`, `date_start/date_stop`, currency) per `campaign_id`; campaigns table
  (`media_buyer.campaign`), approvals (`approvals` table + receipts with
  `kind="approval"`), model costs in receipts (`cost_usd` per receipt, per `run_id`).
- API: `/api/v1/*` in `studio/routes_api.py` with bearer tokens (`studio/api_tokens.py`,
  scopes `read` / `draft`; never `sign`).

## Scope
1. **`studio/exports.py`** (pure functions, no I/O besides the passed connection):
   - `leads_rows(conn, *, masked: bool) -> list[dict]`; writers `to_csv(rows, columns)`
     (UTF-8 with BOM, `;`-safe quoting, RFC 4180), `to_json(rows)`, `leads_to_vcf(rows)`
     (vCard 4.0: FN, EMAIL, TEL, NOTE with source/score), and
   - `statement(conn, month: "YYYY-MM") -> list[dict]`: one row per campaign per day:
     `date, campaign_item_id, campaign_name, meta_campaign_id, currency, spend,
     approved_daily_budget, approved_by (approver key id, short), approved_at,
     approval_id, approval_receipt_hash, model_cost_usd (sum of the run's receipts),
     run_id`; plus one row per run without spend (model cost only). Totals by currency.
   - `statement_hledger(rows) -> str`: hledger/ledger journal, one transaction per row
     (`expenses:advertising:meta` / `liabilities:meta` in the ad account currency;
     `expenses:software:muse-models` in USD), the approval id and receipt hash as
     comments/tags (`; approval:…`, `; receipt:…`) so a line can be traced and verified.
   - CSV column sets: `generic` (all columns), `xero` (Date, Amount, Payee, Description,
     Reference — Xero's bank-statement import shape), `quickbooks` (Date, Description,
     Amount), `wfirma` (Data, Kontrahent, Opis, Kwota, Waluta) — document each in
     `docs/export.md`; amounts with a dot decimal, negative for expenses where the
     target expects it (say which in the doc).
2. **Import leads** (`import_leads(conn, data, kind) -> ImportResult`): CSV (header row;
   columns matched case-insensitively from name/full name/imię i nazwisko, email/e-mail,
   phone/telefon, source, message/note), JSON (list of objects with the same keys), vCard.
   Limits: 2 MB, 5 000 rows; e-mail/phone normalised; duplicates (same e-mail or phone)
   skipped and counted; `source` defaults to "import"; rows with neither e-mail nor phone
   rejected with their line number. One `tool_call` receipt `leads.import` with counts only
   (never names or contacts). No upload of anything to Meta (the studio never uploads
   user data — keep it so).
3. **UI**
   - `/leads`: "Export" (CSV · JSON · vCard) and "Import" (file input + format select)
     for accounts that see personal data; trial/demo: export is masked like the page,
     import hidden.
   - `/report`: a "Monthly statement" section: month picker, table preview (first 20
     rows + totals), downloads: CSV (generic / Xero / QuickBooks / wFirma), hledger, JSON;
     a line "every row traces to a signature: `studio verify` checks the receipts".
4. **API** (`routes_api.py`, scope `read`; same masking rules by the token's account):
   - `GET /api/v1/leads?format=json|csv|vcf`
   - `GET /api/v1/statement?month=YYYY-MM&format=json|csv|hledger&profile=generic|xero|quickbooks|wfirma`
   - add both to the OpenAPI document the module already serves; content types and
     `Content-Disposition: attachment; filename=…`.
   - Import stays session + CSRF only (no token can write personal data in).
5. Docs: `docs/export.md` (formats, columns, examples, how to reconcile with Meta's
   invoice); a short line in `README.md` is the operator's job — don't edit README.

## Tests
- `tests/test_exports.py`: CSV round-trip (export → import gives the same leads, duplicates
  skipped), BOM and quoting, vCard shape, masking for a trial account (no e-mail/phone in
  any format), statement rows from fixture snapshots/approvals/receipts with exact
  totals, hledger output balances (each transaction sums to zero per commodity), the four
  CSV profiles' headers, import limits and line-numbered errors, API auth (no token 401,
  `read` token OK, another account's data never), CSRF on import.
- i18n parity for new keys.

## Constraints (all mandatory)
Same as plans A/B: `~/code/muse-studio-lite`, ruff + mypy clean, full pytest (~13 min)
green before the final commit; English code; EN+PL copy; CSP; accessibility. Signed commits
with the configured identity, no Co-Authored-By / Generated-with lines. Never push,
deploy, touch `.env`/secrets, call live APIs. No new third-party dependency (no openpyxl,
no pandas). **Reserved for the parallel agent (plan C), do not edit:**
`studio/billing.py`, `studio/routes_billing.py`, `studio/ui/legal_texts.py`,
`studio/routes_help.py`, `studio/ui/templates/account.html`, `studio/ui/templates/base.html`,
`studio/ui/templates/login.html`. Report: files changed, test line, open points.
