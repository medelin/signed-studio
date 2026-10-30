# Export and import

The studio's data leaves in the formats an owner's tools read: leads for a spreadsheet, a
CRM or a phone's contacts, and a monthly ad statement for the accountant. Every ad expense
in the statement is tied to the owner's signature and its receipt, so the statement
reconciles with Meta's invoice line by line. Code: `studio/exports.py` (pure functions),
`studio/routes_export.py` (web UI), `studio/routes_api.py` (`/api/v1`).

## Who sees what

| | Signed-in owner (full account) | Trial or demo account | Visitor without a session |
|---|---|---|---|
| Leads export (`/leads`, `GET /api/v1/leads?format=…`) | names and contacts | initials, `[email]`, `[phone]`, free text redacted (as on `/leads`) | no (401) |
| Leads import (`/leads`) | yes | hidden and refused (403) | no |
| Monthly statement (`/report`, `GET /api/v1/statement`) | yes | yes (no personal data in it) | no (401) |

- The masking is applied to the rows before any writer runs, so no format (CSV, JSON,
  vCard) can carry more than the page shows. A token acts with its account's rules.
- On the mock adapter the leads are synthetic people (`@example.com`, `+48 000 000 xxx`),
  so every signed-in account sees them, as on `/leads`.
- Runs of briefs drafted through another account's token are never in a statement.
- Import exists only in the web UI (session, same-origin, the CSRF form field). No API
  token can write personal data in. Nothing is uploaded to Meta: the studio never uploads
  user data.

## Leads

### Export

`/leads` → *Download*: CSV, JSON or vCard. API: `GET /api/v1/leads?format=json|csv|vcf`
with a `read` token (without `format` the endpoint keeps its JSON listing with `limit`).

Columns (CSV and JSON keys), newest lead first:

| Column | Meaning |
|---|---|
| `id` | the studio's lead id |
| `name`, `email`, `phone` | as stored (masked for trial/demo accounts) |
| `source` | `lead_form`, `comment`, `import`, … |
| `message` | what the person wrote |
| `company`, `city`, `country` | when known |
| `score`, `temp` | the lead scorer's score (0–100) and `HOT` / `WARM` / `COLD` |
| `created_at` | ISO 8601, UTC |

- **CSV**: UTF-8 with a BOM (Excel then reads Polish letters right), a header row, CRLF
  line ends, RFC 4180 quoting. A field with a comma, a semicolon, a quote, a line break or
  an edge space is quoted, so a spreadsheet set to `;` as the separator does not split it.
  A text that a spreadsheet would run as a formula (starting with `=`, `@`, or `+` / `-`
  followed by more than a number) gets a leading `'`; the import removes it again.
- **JSON**: a list of objects with the columns above.
- **vCard 4.0** (RFC 6350): `FN`, `EMAIL`, `TEL;VALUE=uri:tel:+48…`, and `NOTE` with
  `source: …; score: … (HOT)` and the message. Lines are folded at 75 octets. A masked
  card has no `EMAIL` or `TEL`.

Example (CSV):

```
id,name,email,phone,source,message,company,city,country,score,temp,created_at
l1,Joanna Kowalska,joanna@example.com,+48000000101,lead_form,"Mugs; two, please",,,,82,HOT,2026-10-29T09:14:00Z
```

### Import

`/leads` → *Import leads*: a file and its format.

- **CSV** with a header row; `,`, `;` or tab as the separator (taken from the header).
  Columns are matched case-insensitively: `name` / `full name` / `imię i nazwisko`,
  `email` / `e-mail`, `phone` / `telefon`, `source`, `message` / `note` (also `notatka`,
  `wiadomość`). Other columns are ignored.
- **JSON**: a list of objects with the same keys (or `{"leads": [...]}`).
- **vCard**: `FN`, `EMAIL`, `TEL`, `NOTE` of each card.

Rules:

- At most 2 MB and 5,000 rows; a larger file is refused as a whole and nothing is added.
- The text must be UTF-8 ("CSV UTF-8" in Excel's *Save as*).
- E-mail is trimmed and lower-cased; phone keeps a leading `+` and digits (`00` becomes
  `+`), 7 to 15 digits.
- A row whose e-mail or phone is already in the studio, or earlier in the same file, is a
  duplicate: skipped and counted.
- A row with neither an e-mail nor a phone, or with an invalid one, is rejected; the page
  lists the line numbers (for JSON: the position in the list, from 1).
- `source` defaults to `import`.
- The import is one transaction and leaves one `tool_call` receipt `leads.import` with the
  counts (rows, added, skipped, rejected) and the file's hash: never a name or a contact.

## Monthly statement

`/report` → *Monthly statement* (signed in): pick a month, see the first 20 rows and the
totals, download. API: `GET /api/v1/statement?month=YYYY-MM&format=json|csv|hledger&profile=generic|xero|quickbooks|wfirma`
with a `read` token. Every API call is a receipt (`api.statement`); the download carries
its id in `X-Receipt-Id`.

### Rows

One row per campaign per reporting day, from the stored insights snapshots (the hourly
`studio insights-pull`). A snapshot covers `date_start … date_stop` (as Meta reported it)
and is booked on `date_stop`; when two snapshots of a campaign overlap, the newer one wins
and the older one is never counted, so no spend is counted twice. A one-day snapshot gives
the day's spend; the pull today asks Meta for `last_7d`, so a row stands for the seven days
ending on its date, and a day that no kept snapshot covers (insights not pulled for a
week) is missing from the statement rather than guessed.

| Column | Meaning |
|---|---|
| `date` | the reporting day (`YYYY-MM-DD`) |
| `campaign_item_id` | the studio's campaign id (the approval's `item_id`) |
| `campaign_name` | as created on Meta |
| `meta_campaign_id` | Meta's campaign id (what Ads Manager and the invoice show) |
| `currency` | the ad account's currency |
| `spend` | Meta's reported spend for the window, in `currency` |
| `approved_daily_budget` | the daily budget in force that day, as signed |
| `approved_by` | the approver's key id, short (`ed25519:` + 8 hex) |
| `approved_at` | when that approval was signed (UTC) |
| `approval_id` | the signed approval (`campaign.create`, `campaign.activate` or a budget `campaign.update`) that set that budget |
| `approval_receipt_hash` | the hash of that approval's receipt in the ledger |
| `model_cost_usd` | the model cost of the campaign's run in the month (sum of its receipts' `cost_usd`), on the run's first row of the month; `0` on the others |
| `run_id` | the run |

A run that cost model money in the month but spent nothing on ads (a brief that was never
launched, a report, a triage) gets one row of its own: `model_cost_usd` and `run_id`, the
campaign columns empty. The totals are the spend per currency and the model cost in USD.

### Formats

- **JSON**: `{"month", "columns", "rows", "totals"}`; model cost to 6 decimal places.
- **hledger** (also reads in ledger): one transaction per row, each commodity balanced.

  ```
  2026-10-02 Meta ads | Mugs  ; run:run_a, campaign:c1, meta:1201
      ; approval:ap_1, approver:ed25519:abababab
      ; receipt:3f5c…
      expenses:advertising:meta    10.10 PLN
      liabilities:meta    -10.10 PLN
      expenses:software:muse-models    0.013000 USD
      liabilities:muse-models    -0.013000 USD
  ```

  `approval:`, `receipt:`, `run:`, `campaign:` and `meta:` are hledger tags
  (`hledger reg tag:approval=ap_1`).

- **CSV profiles** (UTF-8 with a BOM, dot as the decimal separator, dates ISO 8601
  `YYYY-MM-DD`). Besides `generic`, each row becomes one line per amount: the ad spend in
  the campaign's currency and the model cost in USD rounded to the cent (lines that round
  to zero are left out; the JSON and hledger files keep the exact amounts).

| Profile | Columns | Amounts |
|---|---|---|
| `generic` | all the columns above | positive |
| `xero` | `Date, Amount, Payee, Description, Reference` (Xero's bank-statement import) | **negative** (money out); `Reference` = approval id (spend) or run id (models); the currency in `Description` |
| `quickbooks` | `Date, Description, Amount` (the three-column bank file) | **negative** (money out); currency and reference in `Description` |
| `wfirma` | `Data, Kontrahent, Opis, Kwota, Waluta` | positive, with the currency in `Waluta` |

Payees: `Meta Platforms` for ad spend, `Muse Studio models` for model costs. When an
importer asks for the date format, choose year-month-day.

## Reconciling with Meta's invoice

1. Download the month's statement (generic CSV or hledger) and Meta's invoice (or the
   payment activity export) for the ad account.
2. Group the statement by `meta_campaign_id` and sum `spend`: each sum matches the
   invoice's line for that campaign in the same currency. Meta reports days in the ad
   account's time zone and the rows keep Meta's days (a snapshot stored before the
   reporting window was recorded is booked on the UTC day it was pulled). Spend of a
   seven-day snapshot that starts in the previous month is booked on its last day, so at
   a month's edge compare the per-row `date` with the invoice period.
3. Each row names the approval that allowed its budget. `studio verify` checks that the
   approval receipt (`approval_receipt_hash`) is in the signed ledger and that the
   signature verifies with the approver key, so a line can be traced from the invoice to
   the human tap that allowed it.
4. Model costs are a separate line (`expenses:software:muse-models`, USD), per run, from
   the receipts' `cost_usd`.
