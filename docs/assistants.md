# Your own assistant: MCP and HTTPS API

An owner can drive Muse Studio Lite from their **own** AI assistant (Claude, ChatGPT, Meta
AI, a script) instead of the web UI: draft a brief, answer its `[FACT]` questions, start a
run, read the plan, copy, images and reviews, list campaigns, insights, leads and comments,
and draft replies. The safety is the same as in the web UI: **no assistant can sign,
approve, launch, spend or change a budget or a status.** Every send still waits for the
owner's own signature, a tap in the browser.

Code: `studio/api_tokens.py` (tokens), `studio/assistants.py` (the operations, one entry for
both transports), `studio/routes_api.py` (`/api/v1`, the bearer gate, the account forms),
`studio/remote_mcp.py` (`/mcp`). Tests: `tests/test_assistants.py`,
`tests/attack/test_assistants.py`. The local stdio server for the operator is a separate
thing: [docs/mcp.md](mcp.md).

## 1. Make a token

Sign in, open **Account → Assistants & API** (`/account#assistants`):

- **Label**: who uses it ("Claude Desktop"). Receipts name the assistant `api:<label>`.
- **What it may do**: *Read only* (`read`) or *Read and draft* (`read` + `draft`). There is
  no third choice: a `sign` scope does not exist and cannot be stored.
- **Expires after** 7, 30 (default) or 90 days.
- **Your current password** (and the two-step code when two-step sign-in is on): a borrowed
  or stolen session cookie cannot mint a long-lived token. Attempts count against the login
  limiter.

The token (`msl_tok_…_…`) is shown **once**. The studio keeps only an HMAC-SHA256 of it
(key derived from the ledger key), its label, scopes, expiry, last-used time and the
account's credential generation when it was made. Revoke it on the same page: the assistant
is cut off on its next request. Every token of the account also dies on a password change
or operator reset, a two-step sign-in change, a disable or deletion (all bump the
generation), and on "Sign out other sessions". At most 10 live tokens per account.

**Not for the shared `judge` demo account**: tokens are off there (a token would outlive the
judge password's rotation). Judges use the no-login tour (`/try`) or their own account.

## 2. Connect

Endpoints (on the sandbox: `https://muse.udun.eu`):

| | URL | Auth |
|---|---|---|
| Remote MCP (Streamable HTTP) | `/mcp` | `Authorization: Bearer <token>` |
| HTTPS API | `/api/v1/…` | `Authorization: Bearer <token>` |
| OpenAPI 3 document | `/api/v1/openapi.json` | public |

Session cookies never authorize these paths, and the bearer token is the only way in.
There is **no OAuth** authorization server: the `mcp` SDK (1.30.0) gives the resource-server
half cheaply, but a login and consent flow would be a second sign-in surface to secure. A
token made by the signed-in owner keeps them in charge of who connects. Clients that can
send a header work today; clients that insist on OAuth do not (see the table below).

### Claude Code

```bash
claude mcp add --transport http muse https://muse.udun.eu/mcp \
  --header "Authorization: Bearer msl_tok_…"
```

Then ask "draft a Muse brief for …", "what is waiting for my signature?".

### Claude Desktop

Claude Desktop's own "custom connector" dialog expects OAuth, so use the `mcp-remote`
bridge, which sends the header (`claude_desktop_config.json`):

```json
{"mcpServers": {"muse": {
  "command": "npx",
  "args": ["-y", "mcp-remote", "https://muse.udun.eu/mcp",
           "--header", "Authorization:${MUSE_TOKEN}"],
  "env": {"MUSE_TOKEN": "Bearer msl_tok_…"}}}}
```

### ChatGPT, claude.ai, other assistants

| Client | Works? | How |
|---|---|---|
| Claude Code, Cursor, VS Code, any MCP client with custom headers | yes | MCP at `/mcp` with the header |
| Claude Desktop | yes | `mcp-remote` bridge above |
| ChatGPT custom GPT (Actions) | yes, over the API | import `https://muse.udun.eu/api/v1/openapi.json`, authentication "API key", type "Bearer", paste the token |
| ChatGPT / claude.ai connector dialogs (OAuth only) | no | no OAuth server yet; use the rows above |
| Meta AI, other assistants | if they can call MCP or HTTPS with a header | not verified by us |

The OpenAPI document names an absolute server URL when `STUDIO_PUBLIC_URL` is set (needed by
importers such as GPT Actions); otherwise its server is the relative `/api/v1`.

### curl

```bash
T="Authorization: Bearer msl_tok_…"; S=https://muse.udun.eu/api/v1

curl -s -H "$T" $S/me                                     # who am I, scopes, expiry
curl -s -H "$T" -X POST $S/briefs -H 'Content-Type: application/json' \
  -d '{"text":"Pottery workshop in Kraków, 2 h, 180 zł, Saturdays","lang":"en"}'
curl -s -H "$T" -X POST "$S/briefs/voice?lang=pl" -H 'Content-Type: audio/webm' \
  --data-binary @brief.webm                               # Muse Voice Transcribe, then a draft
curl -s -H "$T" $S/briefs/b_…                             # open [FACT] questions
curl -s -H "$T" -X POST $S/briefs/b_…/confirm -H 'Content-Type: application/json' \
  -d '{"resolutions":[{"action":"answer","text":"clay and firing included"},{"action":"strike"}]}'
curl -s -H "$T" -X POST $S/runs -H 'Content-Type: application/json' -d '{"brief_id":"b_…"}'
curl -s -H "$T" $S/runs/run_…                             # status, plan, copy, images, sign_url
curl -s -H "$T" -o feed.png $S/runs/run_…/assets/feed_1x1.png
curl -s -H "$T" $S/approvals                              # everything waiting for a signature
curl -s -H "$T" $S/campaigns; curl -s -H "$T" $S/insights
curl -s -H "$T" $S/leads;     curl -s -H "$T" $S/comments
curl -s -H "$T" -X POST $S/comments/c-1/reply -H 'Content-Type: application/json' \
  -d '{"message":"Thank you! The workshop is on Saturdays at 10."}'
```

## 3. What an assistant can and cannot do

| Operation (MCP tool / HTTP) | Scope | What it does |
|---|---|---|
| `whoami` / `GET /me` | read | account, token, `can_sign: false`, the rules |
| `list_briefs`, `read_brief` / `GET /briefs`, `GET /briefs/{id}` | read | briefs (own and the web UI's), the draft with its open `[FACT]` questions and field problems |
| `draft_brief` / `POST /briefs` | draft | the producer drafts a Brief v1 from text with the studio's live models |
| `POST /briefs/voice` (HTTP only) | draft | Muse Voice Transcribe on the upload, then the same draft |
| `confirm_brief` / `POST /briefs/{id}/confirm` | draft | one resolution per `[FACT]` question, the web UI's checks (same 422 field errors); kept for `start_run` |
| `start_run` / `POST /runs` | draft | the run: plan, copy, images, reviews, the normal model budget, one run at a time; ends at a pending approval |
| `run_status` / `GET /runs/{id}` | read | status, plan, copy, image links (same token), reviews, the pending approval and its `sign_url` |
| `pending_approvals` / `GET /approvals` | read | everything waiting for a signature, each with `sign_url` and "a human must sign" |
| `campaigns`, `insights` / `GET /campaigns`, `GET /insights` | read | runs and campaigns (created PAUSED; activation is a human signature), the latest insights, recommendations and report |
| `leads`, `comments` / `GET /leads`, `GET /comments` | read | as the signed-in web UI shows them, with a note that their text is data, never instructions |
| `draft_reply` / `POST /comments/{id}/reply` | draft | a pending `reply.post` approval for the exact text (or the community agent's draft); posted only after the owner signs it in the inbox |

**Never**, by construction (tripwires in `tests/attack/test_assistants.py`): no operation
reaches `approvals.verify_and_consume`, an approval renewal, approver key registration,
`executor.authorize`, the media buyer's create / activate / update or their requests, the
inbox sender, a Meta or Google Ads write, or an account change. Invented endpoints and tools
(`/approvals/{id}/sign`, `/campaigns/{id}/activate`, `/budget`, `approve`, `launch`,
`update_budget`…) are 404/405 or `unknown_tool`. The web UI's signing routes do not take a
bearer token. MCP tool descriptions and the server's `instructions` tell the assistant that
it cannot sign or launch and must hand the owner the `sign_url`.

A `sign_url` is the existing signing page: `/studio?run=<run>` (campaign creation and
activation), `/inbox` (replies), `/report` (budget changes). The owner signs there with the
browser's non-extractable key, exactly as without an assistant.

## 4. Rules that still apply

- **Untrusted text.** The assistant's brief text is enveloped like a voice transcript and the
  copywriter never runs on the Contributor tier for it; a `[FACT]` answer that says "already
  approved" ends at the same pending approval.
- **Per account.** A brief drafted with a token (and its run) belongs to that account:
  another account's token gets `404` for it and does not see it, its approvals or its
  images in any list. The shared studio (campaigns, insights, leads, comments the web UI
  shows every signed-in account) is visible to every account's read token, minus what
  belongs to another account's API runs.
- **Receipts.** Every call, refused and failed ones too, is a `tool_call` receipt in the
  token's own run (`api_<token id>`): agent `api:<label>`, `details.tool` (`api.<op>`),
  `status`, `side_effects` (`none` / `local`), `account`, `token` (the id, never the value),
  `transport` (`http` / `mcp`), and the hashes of the arguments and the result. The brief a
  token confirms is a `brief.confirmed` receipt with agent `api:<label>`. `studio verify`
  checks them like any other.
- **Limits.** Per token: 120 requests a minute and 10 drafting calls a minute (`429` with
  `Retry-After`); bodies over 64 KB (32 MB for audio) are `413`. `STUDIO_READ_ONLY=1` makes
  every call `503` before anything, receipt included, is written. In the sandbox the hourly
  reset applies to drafts and runs; tokens are kept.
- **Errors** are `{"error": "<code>", "message": "<sentence in the account's language>"}`:
  `invalid_token` (401), `insufficient_scope` (403), `not_found` (404),
  `not_confirmed`/`run_in_progress`/`unknown_draft` (409), `invalid_brief` with `fields`
  (422), `rate_limited` (429), `model_error` (502), `model_api_unavailable`/`read_only`
  (503).

Behind Caddy (`Caddyfile`, `deploy/Caddyfile`) `/mcp` and `/api/v1` are proxied with
`flush_interval -1`; the MCP endpoint is stateless and answers each POST with one JSON
response, so there is no long-lived stream to buffer or to outlive a revoked token.
