# WhatsApp: notifications and voice briefs

WhatsApp is a second notification channel next to e-mail, and a way to send a brief by voice
note. It only ever carries **links and counts**. Nothing is signed, approved or run from a
WhatsApp message: the Ed25519 signature is still made in the owner's browser (WebCrypto), on
the approval page the link opens.

Code: `studio/whatsapp.py` (client, webhook checks, notification channel, inbox),
`studio/routes_whatsapp.py` (routes), `scripts/whatsapp_templates.py` (template submission),
`studio/ui/i18n_whatsapp.py` (EN/PL strings). Graph API **v25.0** over plain `httpx`, no SDK.
Tests: `tests/test_whatsapp.py` (mock transport, no network).

## What it does

| Direction | Event | What the owner gets |
|---|---|---|
| out | an approval is waiting | template `muse_approval_waiting`, button "Review and sign" → `https://muse.udun.eu/approvals/<approval id>` → (login) → `/studio?run=<run>` where the owner signs in the browser |
| out | new leads | template `muse_new_leads`: the count and a button to `/leads` (rate-limited like the mail: at most once per ten minutes, held-back counts added to the next one) |
| out | an ad was disapproved | template `muse_ad_disapproved`, button to `/studio`; fired by `notifier.event("ad.disapproved")` (the hook name for the Meta-rules work; the e-mail channel gets the same event) |
| in | a voice note from a linked number | download → Muse Voice Transcribe (`studio/muse/voice.py`) → `producer.draft_brief` (Brief v1, enveloped as a transcript, exactly as the web voice brief) → stored as a draft for that account → one reply inside the 24 h window: "Brief ready: N facts, M questions — open <link>" (EN/PL by the account language) |
| in | a text message from a linked number | treated as a typed brief the same way; at confirmation it still runs as customer-grade text (enveloped, Standard tier) |
| in | anything from an unknown number | no reply, no processing, one receipt (sender by keyed hash) |

The link `/studio?brief=<id>` opens the stored draft on the studio page at the same step as a
draft made there (`GET /api/briefs/{id}`, signed-in only). The owner answers or strikes every
`[FACT]` question, confirms and later signs the campaign in the browser.

## Account settings

`/account#notifications`: the owner's WhatsApp number (E.164, validated: `+` and the country
code; spaces, dashes and a leading `00` are accepted and normalised) and an opt-in checkbox.

**Proof of control.** A saved number is *pending*: nothing is sent to it and nothing it
sends is processed. The page shows a one-time code (`MUSE-` + 6 digits, valid 15 minutes,
stored only as a hash bound to the account and the number; "Get a new code" replaces it),
and the owner sends it **from that WhatsApp** to the studio's number (shown with a `wa.me`
link when `STUDIO_WHATSAPP_NUMBER` is set). Only a message from exactly that number with a
live code verifies it (`accounts.whatsapp_verified_ts`); no approved template is needed.
A unique index allows one account per verified number. A number verified by another
account moves only by the same proof: the old account's link is cleared, with a receipt for
both (`verified`, `unlinked`). A WhatsApp brief belongs to that account: `GET
/api/briefs/{id}`, the confirmation, the re-draft and the MCP tools answer "not found" /
`unknown_draft` to any other account, the judge demo account included.
The number is stored on the account (`accounts.whatsapp_number`, `accounts.notify_whatsapp`;
migration in `studio/db.py`), included in the account export and erased with the account.
The campaign / leads switches apply to both channels. The shared judge (demo) account never
gets notifications.

## Security

- `POST /webhooks/whatsapp` is public (one narrow `PUBLIC_PATHS` entry) and the only
  CSRF-exempt path (`auth.CSRF_EXEMPT_PATHS`): Meta's servers post it. Its authentication is
  `X-Hub-Signature-256`, the HMAC-SHA256 of the raw body keyed with `META_APP_SECRET`,
  compared in constant time; unsigned or wrongly signed bodies get 403, bodies over 64 KiB
  413. Only messages for our `phone_number_id` are read.
- Each message is handled once (the SHA-256 of its `wamid` is kept 30 days); Meta's retries
  are acknowledged and dropped. At most 6 messages per sender per ten minutes are processed.
- The webhook answers at once; download, transcription and drafting run on one worker
  thread. Media URLs must be `https` on a Meta host; downloads are capped at 16 MB, no
  redirects; audio then goes through the same ffmpeg conversion as web uploads.
- Read-only mode or no Model API: inbound messages are receipted as `refused`, nothing runs.
- Receipts: `whatsapp.send` (template name, language, status, `recipient_sha256`,
  `message_sha256`) and `whatsapp.inbound` (`sender_sha256`, type, outcome, brief id and
  counts). Phone numbers are HMAC-SHA256 keyed with the app secret, never in clear; message
  ids are hashed because a `wamid` encodes the phone number. `side_effects` is `notice`
  (as for e-mail): `external` is reserved for adapter steps that ride on a signed approval,
  and `studio verify` would rightly flag an unapproved one.

## Setup (what was done in the Meta app)

1. In the Meta app (developers.facebook.com → the Muse Studio Lite app) the **WhatsApp** use
   case was added. Meta created a WhatsApp Business Account (`META_WHATSAPP_WABA_ID`) and the
   **test number** +1 555 198 2857 (`META_WHATSAPP_PHONE_ID`). The test number can message
   only verified recipients (up to five, added under *API Setup → To*).
2. In Business Settings a **system user** was created and given **full control** of the
   WhatsApp Business Account and the app; its token has the scopes
   `whatsapp_business_messaging` (send, media) and `whatsapp_business_management`
   (templates, webhook subscription). It goes in `META_WHATSAPP_TOKEN`.
3. Environment (`.env`, mode 600):

   ```
   STUDIO_WHATSAPP=1                    # or "mock" for local work: no network
   META_WHATSAPP_TOKEN=...              # system-user token
   META_WHATSAPP_PHONE_ID=1385668691299457
   META_WHATSAPP_WABA_ID=1947426389722104
   META_APP_SECRET=...                  # webhook HMAC + appsecret_proof
   STUDIO_WHATSAPP_VERIFY_TOKEN=...     # 16-128 chars [A-Za-z0-9_.~-], your choice
   STUDIO_PUBLIC_URL=https://muse.udun.eu
   ```

   With `STUDIO_WHATSAPP=1` a missing token, phone id, app secret or verify token stops the
   start-up with a `ConfigError` naming the variable.
4. Templates: `.venv/bin/python scripts/whatsapp_templates.py` lists the account's templates
   and creates only the missing ones (UTILITY, `pl` and `en_US`); `--dry-run` prints the
   bodies. It sends no message. It was run once on 2026-10-29: all six
   (`muse_approval_waiting`, `muse_new_leads`, `muse_ad_disapproved` × `pl`, `en_US`) were
   accepted as **PENDING** review.
5. The phone number must be **registered** for the Cloud API before it can send (before
   that a send fails with `133010 Account not registered`). Registered on 2026-10-29
   (status CONNECTED, VERIFIED, CLOUD_API); the two-step PIN set here is kept in `.env` as
   `META_WHATSAPP_PIN` (the studio does not read it). The call can take over 30 s; use a
   timeout of at least 60 s:

   ```
   curl -m 90 -X POST "https://graph.facebook.com/v25.0/$META_WHATSAPP_PHONE_ID/register" \
     -H "Authorization: Bearer $META_WHATSAPP_TOKEN" -H "Content-Type: application/json" \
     -d "{\"messaging_product\":\"whatsapp\",\"pin\":\"$META_WHATSAPP_PIN\"}"
   ```

   The verified test recipient got Meta's `hello_world` template on 2026-10-29; the studio's
   own templates can be sent once Meta approves them.

## Configure the webhook (after deploy)

Not done yet: the sandbox does not run this code until it is deployed.

1. App dashboard → WhatsApp → Configuration → Webhook: callback URL
   `https://muse.udun.eu/webhooks/whatsapp`, verify token = `STUDIO_WHATSAPP_VERIFY_TOKEN`.
   Meta calls `GET /webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=…&hub.challenge=…`
   and the studio echoes the challenge. The same via the Graph API (app access token):

   ```
   curl -X POST "https://graph.facebook.com/v25.0/$META_APP_ID/subscriptions" \
     -d "object=whatsapp_business_account" \
     -d "callback_url=https://muse.udun.eu/webhooks/whatsapp" \
     -d "fields=messages" \
     -d "verify_token=$STUDIO_WHATSAPP_VERIFY_TOKEN" \
     -d "access_token=$META_APP_ID|$META_APP_SECRET"
   ```

2. Subscribe the field **`messages`** (in the dashboard: *Webhook fields → messages →
   Subscribe*).
3. Subscribe the app to the WhatsApp Business Account, so its events reach the webhook:

   ```
   curl -X POST "https://graph.facebook.com/v25.0/$META_WHATSAPP_WABA_ID/subscribed_apps" \
     -H "Authorization: Bearer $META_WHATSAPP_TOKEN"
   # check: GET /v25.0/$META_WHATSAPP_WABA_ID/subscribed_apps
   ```

4. On `/account#notifications`, enter your number and tick "Send my notifications on
   WhatsApp too". Send a voice note to the studio's number; the reply links to the draft.

## Templates

| Name | Body ({{1}}) | Button |
|---|---|---|
| `muse_approval_waiting` | – | URL, dynamic suffix = the approval id: `https://muse.udun.eu/approvals/{{1}}` |
| `muse_new_leads` | the count | URL `https://muse.udun.eu/leads` |
| `muse_ad_disapproved` | – | URL `https://muse.udun.eu/studio` |

A send (Graph: `POST /{PHONE_ID}/messages`):

```json
{"messaging_product": "whatsapp", "recipient_type": "individual", "to": "48600000000",
 "type": "template",
 "template": {"name": "muse_approval_waiting", "language": {"code": "pl"},
              "components": [{"type": "button", "sub_type": "url", "index": "0",
                              "parameters": [{"type": "text", "text": "<approval id>"}]}]}}
```

Free-form text (the brief reply) is sent only inside the 24 h customer-service window the
owner opened by writing; a reply to a message older than that is skipped.

## Operator tools vs. Muse's own MCP

Meta's **WhatsApp Business Tools MCP** (`https://mcp.facebook.com/whatsapp_business_tools`)
is a developer tool for operators (managing the account, templates and numbers from an AI
assistant). It is not part of the studio's runtime. Muse Studio Lite's own MCP server
(`studio/mcp_server.py`, [docs/mcp.md](mcp.md)) is separate and exposes the studio's tools,
not WhatsApp.
