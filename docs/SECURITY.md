# Security

Muse Studio Lite lets AI agents draft ads, replies and campaigns for a real ad account. The
security goal is narrow and testable: **nothing leaves the studio — no post, no campaign, no
spend — unless a human signed exactly that content, and anyone can check afterwards that this
held.** Report a vulnerability privately to the repository owner (GitHub security advisory)
rather than in a public issue.

## Threat model

| Asset | Threat | Control | Where |
|---|---|---|---|
| Ad account, Page | an agent (or a prompt injection steering it) posts, creates or activates | models have no external-write tools; external skills become pending approvals built by code; execution needs a consumed, signed approval; every Meta object is created `PAUSED`; activation is a second approval under `STUDIO_MAX_DAILY_BUDGET_PLN` | `agents/dispatcher.py`, `skills.yaml`, `executor.py`, `agents/media_buyer.py`, `meta/*` |
| Human signature | replay, edit after signing, signing a different item, stale approval | Ed25519 over `{action, item_id, content_sha256, chain_head, approver, nonce, ts}`; nonce consumed in the same SQLite transaction as the state change; TTL 900 s, skew 60 s; key bound to the login session | `approvals.py`, `routes_studio.py` |
| Review | content changed after review, an image nobody reviewed | review bound to the hash of the variant and every placement; each placement reviewed; P9 code check | `agents/producer.py`, `agents/reviewer.py` |
| Customer text (comments, leads, transcripts) | prompt injection | untrusted envelope (neutralized markers, declared by the caller and checked before I/O), no tools on customer-text calls, code tightens model output (unapproved prices, links, PII → human) | `envelope.py`, `agents/community.py`, `agents/lead_scorer.py` |
| Personal data | sent to the Contributor tier, shown to visitors, stored in receipts | PII routing in code before any call (`choose_tier`), redaction in receipts and in every anonymous view | `pii.py`, `receipts.py`, `routes_inbox.public_view` |
| Receipt log | edited, truncated, re-ordered, rolled back, writes without approval | hash chain per run, ledger signature, signed manifest (seq, heads, counts), approval-before-write and step checks in `studio verify` | `receipts.py`, `cli.py` |
| Secrets | Model API key, Meta tokens leak | env only, `.env` mode 600 enforced, never in receipts, errors, URLs or `repr`; Meta token only in the graph adapter; the sandbox has none; gitleaks + kingfisher | `settings.py`, `muse/client.py`, `meta/graph.py` |
| Budget | runaway agent loop or abuse burns credits | per-run step and cost caps, durable daily budget with reservation before each call, sandbox limits and kill switch | `settings.py`, `agents/producer.py`, `sandbox.py` |
| Web app | CSRF, session theft, brute force, oversized bodies, XSS | same-origin check on writes, HttpOnly/SameSite/Secure session, login limiter, body limits, CSP without inline script, autoescaped templates | `app.py`, `sandbox.py` |

## Attack suite (`tests/attack/`, run in CI)

| File | What it attacks |
|---|---|
| `test_dispatcher.py` | a scripted model asking for `create_campaign`, `activate_campaign`, `post_reply`, `hide_comment` with ACTIVE status; smuggled payloads; forged skill registry |
| `test_paused_only.py` | ACTIVE at creation (top level or nested, even signed); missing, forged, replayed, expired or mismatched approvals; swapped image file |
| `test_activation_chain.py` | activation without its own approval, over the ceiling, failure mid-chain |
| `test_external_steps.py` | adapter steps without an approval, on another run's approval, after the write, after the TTL, in an edited export |
| `test_injection.py` | 10-comment injection corpus with a fully obedient model: draft only, no tools, no approvals, no writes |
| `test_untrusted_paths.py` | every customer-text path wraps its input (canary per path, completeness over `studio/`) |
| `test_voice_transcript.py` | a voice brief stays enveloped customer text after edits |
| `test_comment_pii.py` | PII in comments never reaches the model unredacted |
| `test_guest_views.py` | anonymous views of campaigns, comments and leads |
| `test_review_binding.py`, `test_review_every_format.py` | content swapped after review; unreviewed placements |
| `test_unfilled_fact.py` | `[FACT]` placeholders hidden with look-alike characters |
| `test_parallel_cost.py`, `test_placements_cost.py` | cost cap bypass through parallel tool calls and placements |

Plus `tests/test_verify_cli.py` (19 tamper classes → exit 2 naming the receipt) and the
generated tamper suite in [`docs/eval.md`](eval.md).

## Residual risks

- **TOFU in `studio verify --server`.** Without a pinned key, `--server` trusts the key and
  `seq` the server serves at that moment; a compromised server can serve a matching forged
  key. Pin `ledger.pub` out of band (`--key-file`) and keep the highest `seq` you have seen
  (`--min-seq`).
- **A server that lies by omission.** The ledger key lives on the server. Verify catches a
  write logged without an approval, but not a write the server made and never logged. The
  Meta side (Ads Manager history) is the cross-check.
- **Heuristic PII detection.** `pii.py` finds e-mails, PL/UK phone numbers, NIP and PESEL
  (with checksums) and long digit runs; a phone number spelled out in words or split across
  fields is not detected, and relies on the reviewers and the human reading the payload.
- **Heuristic P9 check.** `reviewer.policy_net` matches known phrasings (EN/PL); a
  paraphrase relies on the two model reviewers and the human.
- **Prompt injection is mitigated, not solved.** The envelope reduces it; the boundary is
  "no tools on customer-text calls + a human signature on every send".
- **Graph API behaviour** (token transport, activation order, `appsecret_proof`) is tested
  against hand-written cassettes until the live smoke on the operator's own ad account.
- **Second-opinion reviewer** is unavailable without `BASETEN_API_KEY`; the approval screen
  says so.
- **Sessions live in process memory**: a restart signs everyone out and revokes their keys
  (past approvals stay verifiable).
