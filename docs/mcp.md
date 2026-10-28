# MCP server: `studio mcp`

`studio mcp` lets an external agent (Claude Code, an IDE assistant, another agent) drive
Muse Studio Lite over the [Model Context Protocol](https://modelcontextprotocol.io). The
agent can read and draft. It cannot sign, send or spend: a human still signs every send in
the web UI, and every call the agent makes is a receipt that `studio verify` checks.

Module: `studio/mcp_server.py` (official `mcp` Python SDK, low-level server, pinned
`mcp==1.30.0`). Tests: `tests/test_mcp.py`, `tests/attack/test_mcp.py`.

## Setup

The server is a local process of the operator, started by the MCP client over **stdio**; it
opens no port. It uses the studio's existing database and ledger key (`STUDIO_DATA_DIR` from
the env file) and acts for one existing, active account.

```bash
# once: the studio has run at least once, and the account exists
studio account create owner            # or use the demo account `judge`

# Claude Code (absolute paths: the client starts the process from its own directory)
claude mcp add muse-studio -- /path/to/muse-studio-lite/.venv/bin/studio mcp \
    --account owner --env-file /path/to/muse-studio-lite/.env \
    --ui-url http://localhost:8000
```

Or in a project's `.mcp.json`:

```json
{"mcpServers": {"muse-studio": {
  "command": "/path/to/muse-studio-lite/.venv/bin/studio",
  "args": ["mcp", "--account", "owner", "--env-file", "/path/to/muse-studio-lite/.env",
           "--ui-url", "http://localhost:8000"]}}}
```

| Option | Default | Meaning |
|---|---|---|
| `--account` | (required) | the account the agent acts for; checked at start and on every call (a disabled or deleted account is refused) |
| `--env-file` | `.env` | the studio's settings; keep `STUDIO_DATA_DIR` absolute when the client starts the server elsewhere |
| `--ui-url` | `http://localhost:8000` | the web UI the owner signs in; approval links point here |
| `--replay-set` | `brief` | the recorded answers drafts and runs are served from (`fixtures/replay/<set>`) |

Run the web UI (`make demo` or `make serve-replay`) next to it, on the same `STUDIO_DATA_DIR`,
so the owner can open the approval links. Stdout is the protocol channel: the server prints
nothing else there; warnings go to stderr.

## Tools

| Tool | Side effects | What it does |
|---|---|---|
| `list_briefs` | none | briefs, newest first: id, state (draft, confirmed), run, brief hash |
| `read_brief` | none | source text, the Brief v1 draft with its field problems and open `[FACT]` questions, the confirmed brief and `brief_sha256` |
| `list_campaigns` | none | runs with their status (and the approval page when one waits for a signature), campaigns created through the Meta adapter |
| `read_run` | none | plan, copy, images, reviews, the run's receipts and what waits for a signature |
| `read_campaign` | none | a created campaign (always PAUSED): ids, budget, currency, and the signature its activation needs; a Meta preview URL (it carries an access token) is never returned |
| `draft_brief` | local | the producer drafts a Brief v1 from the agent's text (`POST /api/briefs/draft`) |
| `run_pipeline` | local | confirms a draft with the UI's checks (`routes_studio.check_confirmation`: one resolution per open question, every field valid in the ad account's currency, the same 422 field errors), then runs the producer: plan, copy, images, reviews, a pending approval |
| `request_launch` | none | what an agent calls to send or turn on a campaign: it does neither, it returns `awaiting_human_signature` and the approval page |
| `read_receipts` | none | receipts exactly as exported, filtered by run and kind |
| `read_report` | none | the latest analyst report and measurement (e-mails and phones redacted, as for a visitor) |
| `verify_export` | none | `studio verify` with this studio's ledger key and approver keys, on a `.jsonl` export or on a fresh export of the whole ledger |

Drafts and runs are served from the recorded replay set (no Model API key, no network, no
spend) against the mock Meta and Google Ads adapters, wrapped so that only the account
currencies, the Page id, the Google customer id and the read-only targeting search are
reachable (never the Graph or Google Ads REST adapter, whatever the env file says). A brief
that chose Google Search gets its `google.create` approval pending next to the Meta one. Live drafting stays in the web UI. A replay
answers any brief with recorded output, so the copy and images are the recorded ones; the
point is to exercise the whole path up to the signature.

A run that ends at an approval returns:

```json
{"status": "awaiting_human_signature", "action": "campaign.create",
 "approval_id": "…", "approval_url": "http://localhost:8000/studio?run=run_…",
 "message": "Awaiting human signature. The owner opens …"}
```

The message is in the account's language (EN or PL), as are the error sentences.

## What the agent cannot do

- **Sign or send.** No tool reaches `approvals.verify_and_consume`, the approval renewal,
  approver key registration, `executor.authorize`, the media buyer, the inbox sender or any
  adapter write; `tests/attack/test_mcp.py` makes each of them a tripwire and calls every
  tool with valid, extra and malformed arguments, plus invented tools (`approve`,
  `sign_approval`, `activate_campaign`, `update_budget`, `post_reply`, `reset_password`…),
  which are refused as `unknown_tool`.
- **Change budgets or touch accounts.** No tool changes a budget, a recommendation, a
  password, two-step sign-in or a session.
- **Smuggle instructions.** The agent's text is untrusted: the draft and the run treat it
  like a voice transcript (enveloped, declared, the copywriter never on the Contributor
  tier). An injection in a brief field or a forged "approved" answer to a `[FACT]` question
  ends the run at the same pending approval as any other.
- **Pass off its own receipt as an approval.** An MCP receipt is a `tool_call`; `studio
  verify` rejects a write that names it, or the unsigned pending approval, as its approval.

## Receipts

Every call, refused, failed and unknown ones included, is a `tool_call` receipt in the
server's own run (`mcp_<hex>`), never in a campaign run (so it cannot move the chain head an
approval is bound to):

| Field | Value |
|---|---|
| `agent` | `mcp:<client name>` from the client's `initialize` (folded to an opaque id, `mcp:client` if nothing is left) |
| `input_sha256` | the hash of the canonical JSON arguments |
| `output_sha256` | the hash of the canonical JSON result (without `receipt_id`) |
| `details` | `tool` (`mcp.<name>`), `status` (`ok`, `refused`, `error`, `awaiting_human_signature`), `side_effects` (`none` or `local`), `account`, and the brief, run, approval or campaign id it concerns |

The brief a run confirms is a `brief.confirmed` receipt whose agent is `mcp:<client name>`,
not `owner`. Under `STUDIO_READ_ONLY=1` every call is refused before anything is written,
receipt included.

## How it differs from Meta's ads MCP server

Meta's own ads MCP server lets an agent work on the ad account through the Marketing API;
this one never gives the agent a write: every send waits for a person's Ed25519 signature
over the exact bytes in the web UI, and every call the agent makes is a receipt anyone can
check offline with `studio verify`.
