# Built during the event

Muse Studio Lite, solo entry, Indie track, theme **Build for Your World**
(small-business owners running their own Meta ads safely).

## What git shows

- First commit: `Start: README and license`, 2026-10-27. Latest commit on `main` at the time of writing: 2026-10-31.
- Commit count on `main`: 347 (43 on 27 Oct, 126 on 28 Oct, 50 on 29 Oct, 50 on 30 Oct, 78 on 31 Oct).
- The repository starts empty at that first commit: every line was written during the event, and there is no earlier history.
- Check it yourself: `git log --reverse --format='%ad %s' --date=short | head -1` and `git rev-list --count main`.

## Main modules and the day they first appeared

Dates are from `git log --diff-filter=A --format=%ad --date=short -- <path>` (first commit that added the path).

| Module | Role | First added |
|---|---|---|
| `studio/receipts.py`, `studio/approvals.py` | hash-chained receipts; signature bound to the exact bytes | 2026-10-27 |
| `studio/agents/` | Producer, Copywriter, Art director, Reviewers, ... | 2026-10-27 |
| `studio/meta/`, `studio/muse/` | Meta Marketing API adapter; Muse model clients | 2026-10-27 |
| `studio/ui/`, `studio/cli.py` (`studio verify`) | web UI and CLI | 2026-10-27 |
| `tests/attack/` | attack suites | 2026-10-27 |
| `studio/executor.py` | code, not a model, creates the PAUSED campaign | 2026-10-28 |
| `studio/brief.py` | Brief v1 and [FACT] questions | 2026-10-28 |
| `studio/google/`, `studio/leadads.py` | Google Ads spec, lead ads | 2026-10-28 |
| `studio/mcp_server.py`, `studio/replay.py` | MCP server; offline replay | 2026-10-28 |
| `studio/routes_receipts.py`, `docs/eval.md` | receipts page; evaluation | 2026-10-28 |
| `studio/whatsapp.py`, `studio/assistants.py` | WhatsApp notices; assistant tokens | 2026-10-29 |
| `studio/exports.py`, `studio/billing.py`, `studio/mandates.py` | accountant exports; Stripe (test mode); mandates | 2026-10-30 |

## Meta models used (ids found in `studio/`)

| Model id | Where |
|---|---|
| `muse-spark-1.3` | `studio/agents/` (producer, reviewer, community, lead_scorer, analyst) |
| `muse-spark-1.3-contributor` | `studio/agents/copywriter.py`, `studio/pii.py` |
| `muse-image-1.0` | `studio/muse/image.py` |
| `sam-3.1` | `studio/muse/sam.py` |
| `muse-voice-transcribe-1.0`, `muse-wonder-1.0` | `studio/muse/voice.py`, `studio/agents/video.py` |
| `meta-models/muse-spark-open-1026` (open weights, served by Baseten) | `studio/muse/openweights.py` |

## Theme: Build for Your World

A one-person business can run Meta ads without handing an AI the ad account:
agents draft, a person signs the exact bytes, code creates the campaign PAUSED,
and every step is a receipt anyone can check offline with `studio verify`.
Numbers: [docs/eval.md](docs/eval.md). Judge path: the top of [README.md](README.md).
