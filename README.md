# Muse Studio Lite

**An AI ad agency for one-person businesses — every agent leaves a receipt, and no agent can spend a cent without your signature.**

Built for the Meta Global AI Developer Hackathon 2026 (Indie track · Build for Your World).

Small businesses run Facebook and Instagram ads, answer comments and chase leads by hand, and
they are afraid to let an AI post or spend on their behalf. Muse Studio Lite runs that work with
cooperating Meta Muse agents and keeps a human in charge of everything that leaves the system:

- **Producer** plans the campaign from a brief (Muse Spark).
- **Copywriter** and **Art director** write the ad and design the creative (Muse Image, SAM 3.1 cut-outs).
- **Reviewer** checks the result with Muse Spark vision, with a second opinion from open weights.
- **Community** and **Lead scorer** answer comments and route leads.
- **Human gate** — nothing is posted and no campaign is created until a person signs the exact content.

## Verifiable agents

Every model call, tool call, review and approval is a **receipt**: hash-chained, signed by the
server's Ed25519 ledger key, closed by a signed export manifest. `studio verify` checks the whole
log and rejects any edited, truncated or re-ordered history, and any external write that was not
preceded by a human approval for exactly the same content.

## Status

In active development during the event (see [CHANGELOG.md](CHANGELOG.md)). I'm using it to make and
run my own real ads during the hackathon, and improving it as I go.

## Run the tests

```bash
make install
make test        # offline: model calls replay recorded cassettes
make lint        # ruff + mypy
```

Every fixture says what it is: `"_provenance": "recorded"` (a real API response captured
by `scripts/record_*.py`) or `"synthetic"` (hand-written, file name `*.SYNTHETIC.json`,
with a `_note` on its source — the Graph API campaign flow, the open-weights review and the
ad insights, until live recordings exist). A report built on synthetic insights says so on
the page.

## Run the studio

```bash
make demo        # http://localhost:8000/studio — sign in as the judge, run a brief
```

One brief → plan (Muse Spark 1.3) → three copy variants (Spark Contributor) → SAM 3.1
cut-out of the product photo → three placements (Muse Image 1.0) → two reviews of every
placement (Spark vision + Spark Open on Baseten) → a pending approval. SAM 3.1 is a
streamed transport, batch parse: the cut-out request is a streamed Responses call, and the
mask lane is parsed once the stream has ended (no partial masks are used). The browser signs the exact approval
bytes with a non-extractable WebCrypto Ed25519 key; every step is a receipt in the live
ticker. The demo product photo is synthetic (model-generated); set `STUDIO_PRODUCT_PHOTO`
and `STUDIO_PRODUCT_CONCEPT` to use your own.

## Verify a receipt log

```bash
curl -sO https://<studio-host>/api/receipts.jsonl
studio verify receipts.jsonl --server https://<studio-host>
# offline: studio verify receipts.jsonl --key-file var/ledger.pub \
#          --approver-keys approvers.json --min-seq <seq from /api/ledger-manifest>
```

`--server` fetches the published ledger key, the latest manifest `seq` (so an older,
rolled-back export fails) and the approver public keys (so every approval signature is
checked). `make smoke-live` makes one real Model API call with the key from `.env` and
prints usage only (and refuses under `STUDIO_READ_ONLY=1`).

`--server` is **trust on first use (TOFU)**: it trusts whatever key and `seq` the server
serves at that moment, so a compromised server could serve a matching forged key. For a
real check, pin the ledger key once, out of band (`ledger.pub`, published with the
release or copied from the operator), pass it with `--key-file ledger.pub` together with
`--server` (a mismatch is refused), and keep the highest `seq` you have seen to pass as
`--min-seq` next time, so a rollback to an older log cannot go unnoticed.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
