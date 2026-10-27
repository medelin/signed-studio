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
make test
```

## License

Apache License 2.0 — see [LICENSE](LICENSE).
