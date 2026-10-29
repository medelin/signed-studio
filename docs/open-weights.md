# Open weights in Muse Studio Lite

Muse Studio Lite uses two of Meta's open-weight models next to the hosted Model API:
**Muse Spark Open** does real work (second-opinion reviewer, and in open-weights mode the
copywriter, the comment triage and the lead rationale), and **Muse Glimmer** has a code
path as a comment pre-filter that is off by default, because this project cannot call it
yet (see below). This page says what each model is, how we checked, what runs where, and
the numbers we measured.

## README paragraph

> **Built on Meta open weights.** Every ad is reviewed by two model families: Muse Spark 1.3
> on the Model API and **Muse Spark Open** (`meta-models/muse-spark-open`, Meta's open-weight
> 816B MoE, served on Baseten). BLOCK from either one wins. The open model is a second check
> with different weights, not the same model asked twice: on the 25 seeded ads it made the
> same OK-or-flag decision as Spark vision on **25/25** (same verdict on 22/25), and on 20
> inbox comments it gave the same intent on **17/20** and the same needs-a-human decision on
> **19/20**. With `STUDIO_OPEN_WEIGHTS=1` (or the per-run switch on the brief) the
> copywriter runs on Spark Open with the same strict schema, and **customer text (comments,
> leads, PII) is processed only on open weights**. It never reaches the hosted API, and you
> could move it to your own GPUs without changing a prompt. Receipts name the model family
> and `weights: open|hosted` of every step. The recorded `open` set replays the whole loop
> in this mode offline (`make demo-replay SET=open`). Muse Glimmer (30B, Apache 2.0) has
> a pre-filter path ready, but it is not on the Model API or Baseten, so it stays off and
> we publish no Glimmer number.

## What the two models are (checked 2026-10-29)

| | Muse Spark Open | Muse Glimmer |
|---|---|---|
| Id we call | `meta-models/muse-spark-open` (Baseten) | `meta-models/Muse-Glimmer-30B` (Together AI; HF repo `meta-models/Muse-Glimmer-30B`) |
| Size | 816B total, mixture of experts, 42B active | ~29.6B dense, including a ~1.8B ViT-G/14 vision encoder; distilled from Muse Spark |
| Input → output | text, image, video, audio → text | text, image (video as frames) → text; no audio |
| Context | 1,048,576 (Baseten listing) | 128K+ |
| Licence | open weights (BF16 / NVFP4 checkpoints) | Apache 2.0 |
| Runs on | SGLang, vLLM, TensorRT-LLM, llama.cpp; Mac Studio MLX recipe | vLLM, SGLang, Transformers, llama.cpp / Ollama / LM Studio (community quants), ExecuTorch; a ~4-bit build fits one 24–32 GB GPU |
| Strengths (model card) | agentic and coding work at scale | local agents: tool use (MCP Atlas 75.5), instruction following (IFBench 77.0), long context (AA-LCR 80.0), SWE-Bench Pro 51.2 |
| Hosted price | Baseten $1.25 in / $0.15 cached / $4.25 out per 1M | Together AI $0.35 in / $0.04 cached / $1.50 out per 1M (read 2026-08-11) |
| Callable with our keys | **yes** (Baseten) | **no** |

How we checked Glimmer access, with our own keys and without printing them:

- `GET https://inference.baseten.co/v1/models` lists 20 models. `meta-models/muse-spark-open`
  is one of them, and no Glimmer id is.
- `POST https://inference.baseten.co/v1/chat/completions` with `meta-models/Muse-Glimmer-30B`,
  `meta-models/muse-glimmer` and `meta-models/muse-glimmer-30b` returned **404** "please
  check the model you provided" for all three.
- `GET https://api.meta.ai/v1/models` lists the Muse Spark, Wonder, Image, Voice and SAM
  ids, and no Glimmer. Chat calls with `muse-glimmer`, `muse-glimmer-30b` and
  `muse-glimmer-1.0` returned **404** `model_not_found`. Meta's model page says the same:
  self-hosted Glimmer does not appear in the API model list.
- The cookbook's Glimmer `hosted/` folder names one provider, Together AI
  (`https://api.together.xyz/v1`, `TOGETHER_API_KEY`). We have no Together key, so no
  Glimmer call was made.

Sources: [meta-oss-cookbook](https://github.com/meta-models/meta-oss-cookbook)
(`muse-glimmer/hosted/together-ai.md`, `muse-spark/hosted/`,
`muse-glimmer/recipes/structured-output`); [dev.meta.ai/docs/models](https://dev.meta.ai/docs/models)
(open-weight section); [huggingface.co/meta-models/Muse-Glimmer-30B](https://huggingface.co/meta-models/Muse-Glimmer-30B)
(model card); Baseten `/v1/models` (live listing, 2026-10-29).

## What runs where

| Step | Default | Open-weights mode (`STUDIO_OPEN_WEIGHTS=1`, or the run's switch) |
|---|---|---|
| Producer (plan, tool loop) | Muse Spark 1.3 | Muse Spark 1.3 (operator's brief, Responses API tools) |
| Copywriter (`CopyPack`) | Spark 1.3 Contributor / Standard | **Muse Spark Open**, tier `open`, same prompt and strict schema |
| Art director, image, SAM | Model API | Model API (no open-weight image model) |
| Reviewers (`Review`) | Spark 1.3 vision **+ Spark Open** | same: two families, BLOCK from either wins |
| Comment triage (`Triage`) | Spark 1.3 Standard | **Muse Spark Open** only (the run switch does not change it: the studio setting does) |
| Lead rationale (`Score`) | Spark 1.3 Standard | **Muse Spark Open** only |
| Comment pre-filter (`PreScreen`) | off | **Muse Glimmer** when `STUDIO_GLIMMER=1` (not callable with our access) |

Routing is decided in code, before any request. `pii.choose_tier(text, requested, source=…,
open_weights=…)` has a third tier, `open`. In open-weights mode every call through it goes
to `open`, with a reason that names the kind of data and never its value (`customer text
(comment): kept on open weights`, `pii detected (email): kept on open weights`). The tier
is tied to the client. The hosted client refuses `open`, and the open client refuses every
other tier, both before any I/O. So a wiring mistake fails closed and cannot send a comment
to the hosted API. A run started with the switch but without `BASETEN_API_KEY` gets
`503 open_weights_unavailable`. `STUDIO_OPEN_WEIGHTS=1` without the key stops startup.

Receipts: every `model_call` receipt now has `details.model_family` (`muse-spark`,
`muse-spark-open`, `muse-glimmer`, `muse-image`, `sam`, …) and `details.weights`
(`hosted` or `open`), next to the existing `provider`, `model`, `tier` and `tier_reason`.
The inbox's `draft` receipt repeats the family that wrote the draft.

## Muse Glimmer as a pre-filter

Why this job: Glimmer is small (30B dense), fast and cheap ($0.35 / $1.50 per 1M on
Together, or free on one local GPU). The card stresses instruction following and tool use,
and that fits a structured classifier. A pre-filter in front of comment triage is the place
where a self-hosted model makes most sense. It sees every comment from the public, and it
can run on the shop owner's own hardware.

What the code does (`studio/muse/glimmer.py`, `community.apply_prescreen`):

- `STUDIO_GLIMMER=1` + `GLIMMER_API_KEY` (+ optional `GLIMMER_BASE_URL`, default
  `https://api.together.xyz/v1`, and `GLIMMER_MODEL`, default `meta-models/Muse-Glimmer-30B`;
  any OpenAI-compatible self-hosted server works). Missing key: startup fails with a
  `ConfigError` that says Glimmer is not on the Model API or Baseten and where to get it.
  Called without a client or key, `prescreen` raises `GlimmerUnavailable` before any
  request.
- Glimmer gets the same masked, enveloped comment as the main model and returns a strict
  `PreScreen` (`label`, `injection`, `pii`, `spam`, `confidence`).
- It can only make the triage stricter. It adds `prescreen_*` flags, raises risk to at
  least medium for spam, injection and abuse, and requires a human for injection and abuse.
  It never clears a code flag, never lowers the model's risk, and never skips the main
  triage. A failed or unavailable pre-screen leaves the triage as it was (with an error
  receipt if a call was made).
- Agreement with the main triage is computed per comment (`injection` vs the code's
  `injection_suspected`, `pii` vs `pii_in_comment`, `spam` vs intent `spam`). It is stored in
  the triage result and in the `draft` receipt (`prescreen_agrees`), and
  `scripts/open_weights_eval.py --record --glimmer` aggregates it.

**Glimmer agreement vs Muse Spark on the recorded set: not measured.** Glimmer was not
callable with this project's access, and the tests run against a mocked Glimmer. Once a
Together key (or a local vLLM) is configured, run
`python scripts/open_weights_eval.py --record --glimmer` to fill the number.

## Numbers

**Review, 25 seeded ads** (`docs/eval.md` §4, recorded 2026-10-28): Spark vision vs Spark
Open gave the same OK-or-flag decision on **25/25** and the same verdict on 22/25 (3 WARN vs
BLOCK). Spark Open blocks 5/5 P9 ads and 0/5 clean ones. Cost: $0.075.

**Comment triage, 20 comments** (`fixtures/eval/comments.SYNTHETIC.json`, hand-written,
c-1..c-5 are the seeded inbox; results in `fixtures/eval/triage_open.json`, recorded
2026-10-29 through `community.triage` with the same prompt, schema and code rules):

| Measure | Count |
|---|---|
| Same intent | 17/20 |
| Same needs-a-human decision | 19/20 |
| Same risk level | 15/20 |
| Intent as a human labelled it: Spark 1.3 | 17/20 |
| Intent as a human labelled it: Spark Open | 18/20 |
| Muse Glimmer pre-filter agreement | not measured |

| Comment | Human label | Spark 1.3 | Spark Open |
|---|---|---|---|
| c-1 (price question + injection) | price | question | price |
| c-2 (delivery question + order for four, e-mail) | lead | lead | question |
| e-19 (fake "SYSTEM:" instructions) | other | spam | other |

Both models' drafts go through the same code rules. The injection in c-1 is flagged by code
for both. Cost: $0.157 for 40 calls (Spark 1.3 at Model API prices, Spark Open at Baseten's).

**The `open` replay set** (`fixtures/replay/open/`, recorded 2026-10-29): the demo brief
with `STUDIO_OPEN_WEIGHTS=1`, recorded end to end with 21 real calls for $0.144. Six went
to Muse Spark Open: the copywriter, three placement reviews and the two inbox comments.
The other 15 stayed on the hosted API: the plan, the tool loop, the hosted reviews, the art
director's direction, three images, one SAM cut-out and the analyst report. The run ended
`awaiting_approval`, the signed campaign was created PAUSED (mock Meta), the injection
comment was flagged by code and its reply signed, and `studio verify` reported
`chain OK, 45 receipts, 3 signatures valid`. In this recording Spark Open read c-2 ("Do you
deliver on Saturdays? I'd like four mugs…") as a *question*, not a *lead*, so no CRM card
was made. That is the same c-2 disagreement as in the table above, and we kept the
recording as it came out. The lead rationale on open weights is covered by
`tests/test_open_weights.py`. Every replayed `model_call` receipt names its family
(`tests/e2e/test_replay_open.py`). No request to the hosted API in the set contains a
seeded comment.

## Running it

- Offline: `make demo-replay SET=open` (or `studio demo --set open`).
- Live: `STUDIO_OPEN_WEIGHTS=1` with `MODEL_API_KEY` and `BASETEN_API_KEY`, or tick
  "Run on open weights" on the brief for a single run.
- Re-record: `studio demo --record --set open --env-file .env`;
  agreement: `python scripts/open_weights_eval.py --record --env-file .env`.
