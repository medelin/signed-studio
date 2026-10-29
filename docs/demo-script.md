# Demo video script (3:00) and the 15-second cut

**First line (on screen and spoken, 0:00):** "I built Muse Studio Lite from scratch during this
hackathon: an AI ad agency that works on Meta's real ad platform and can't spend a cent
without my signature."

Record 1080p30 in a fresh browser profile, English subtitles, model ids visible on screen
(the receipt ticker shows them). Every beat has a backup clip from `make serve-replay`
(recorded answers, labelled **replay** in the header) in case the API is slow on the day.

## Six beats

**1. Problem and owner (0:00-0:20)**
- On screen: title card with the first line, then a customer comment under a real ad at 21:03, unanswered; before/after card.
- Say: "I run ads for my own small firm. A campaign takes an evening and a comment waits until morning. I would never give an AI my ad account."
- Proves: Impact, theme "Build for Your World".

**2. Brief by voice or text, Brief v1 with [FACT] questions (0:20-0:50)**
- On screen: record a 15-second Polish voice brief; transcript with turns appears and is edited; Brief v1 with the [FACT] questions, one answered, one struck, Confirm.
- Say: "I speak the brief. The studio writes a Brief v1 and asks about every fact it does not know instead of inventing it."
- Proves: `muse-voice-transcribe-1.0` (`studio/muse/voice.py`), `studio/brief.py`, the `brief.confirmed` receipt.

**3. Agents plan, copy and images, with a live receipts ticker (0:50-1:30)**
- On screen: Producer plan; ticker shows `run_copywriter`, `run_art_director` with model ids; three copy variants; SAM cut-out of the product, then 1:1, 4:5 and 9:16 placements as one series.
- Say: "Each step is a receipt with its model id. Copy runs on the contributor tier because the input has no personal data; the reason is in the receipt."
- Proves: `muse-spark-1.3` (`studio/agents/producer.py`), `muse-spark-1.3-contributor` (`studio/pii.py`), `sam-3.1`, `muse-image-1.0`, `studio/receipts.py`.

**4. The copywriter refuses a forbidden claim; two reviewers disagree out loud (1:30-2:05)**
- On screen: a brief whose owner insists on "obniża ciśnienie w 7 dni, gwarantujemy" (a health claim and a guarantee); the three copy variants come back without it and the copywriter's `risks` list names the dropped claim and the rule (P8 health, P3 guarantees). Then the review panel: Muse Spark's vision verdict per placement beside the open-weights Spark Open opinion, one disagreement highlighted (WARN vs OK on the missing advertiser name), and the SAM 3.1 product check with its numbers and the mask toggle.
- Say: "I asked for a claim Meta forbids. The copywriter left it out and told me why, in writing. Then two different model families review every ad and every image, and Muse shows where they disagree instead of averaging. A block from either side would stop the run and send the findings back for one rewrite."
- Proves: `studio/agents/copywriter.py` (`risks`), `studio/agents/reviewer.py`, `meta-models/muse-spark-open` (`studio/muse/openweights.py`), the S1 product check (`studio/muse/sam.py`), the rewrite loop (`studio/agents/producer.py`, `tests/test_rewrite_loop.py`), policy rules ([docs/policy.md](policy.md)), [docs/eval.md](eval.md).
- Note: in seven live recordings the copywriter never produced a blockable claim, so no BLOCK is staged; do not fake one on screen.

**5. The owner signs on a phone; the PAUSED campaign appears in Ads Manager (2:05-2:40)**
- On screen: approval panel on a phone (content hash, chain head, expiry) -> "Approve and sign" -> campaign, ad set, creative, ad created **PAUSED**; cut to Meta Ads Manager showing the PAUSED campaign and the lead form.
- Say: "I sign the exact bytes with a key that never leaves my phone. Only then does code, not a model, create the campaign, paused."
- Proves: `studio/approvals.py` (WebCrypto Ed25519 signature bound to the payload hash), `studio/executor.py`, the Graph adapter (live PAUSED run of 2026-10-29), `studio/leadads.py`.

**6. `studio verify` on the exported receipts, and what is next (2:40-3:00)**
- On screen: terminal `studio verify receipts.jsonl --server https://muse.udun.eu` -> `chain OK, N receipts, M signatures valid`; edit one byte -> `chain INVALID` naming the receipt (exit 2); end card with repo URL, sandbox https://muse.udun.eu, "Built solo during the event, 27 Oct - 4 Nov 2026".
- Say: "Anyone can check the whole history offline; one changed byte fails it. Next: more channels and per-client accounts."
- Proves: `studio verify` (`studio/cli.py`), 600/600 tampered exports rejected in [docs/eval.md](eval.md).

## 15-second loopable cut for X

1. 0–3 s: brief typed → three images appear (fast cut).
2. 3–7 s: "Approve and sign" → signature hash flashes → campaign **PAUSED**.
3. 7–11 s: P9 beat: rejected human ad text → reviewer **BLOCK · P9**.
4. 11–15 s: `studio verify` → `chain OK`, one byte edited → `chain INVALID` → cut back to frame 1 (the brief box, empty) so the clip loops.

Caption: "7 AI agents run my ad agency. They can't spend a cent without my signature."

## Blur list (never on screen)

Ad account id (`act_…`), Page ids, Meta object ids in Ads Manager, any access token, `.env`,
the judge password, real commenter names. The studio already hashes Meta ids for visitors;
record signed in only where the shot needs it.

## Before recording

- `make demo-replay` passes (whole loop offline, `studio verify` OK).
- Sandbox up (`/healthz`), daily budget not near the cap.
- For the Ads Manager shot: the PAUSED campaign from the 2026-10-29 live run is visible in Ads Manager (blur ids).
- The P9 left-hand screen is operator-supplied and shown in the video only (kept outside
  the repository, thumbnails blurred): `muse-studio/assets/p9-google-ads/google-ads-disapproved-redacted.png`.
  Paste its ad text into the studio for the right-hand side; `tests/test_policy_p9.py` checks
  that the code check catches all three texts.
