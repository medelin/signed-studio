# Demo video script (3:00) and the 15-second cut

**First line (on screen and spoken, 0:00):** "This week I built Muse Studio Lite: an AI ad
agency that works on Meta's real ad platform — and can't spend a cent without my signature."

Record 1080p30 in a fresh browser profile, English subtitles, model ids visible on screen
(the receipt ticker shows them). Every act has a backup clip from `make serve-replay`
(recorded answers, labelled **replay** in the header) in case the API is slow on the day.

## Shots

| Time | Rubric | Shot | Say / caption |
|---|---|---|---|
| 0:00–0:12 | Impact | Title card → a comment under a real ad at 21:03, unanswered | "This week I built Muse Studio Lite … I run ads for my own small firm; this comment waited until morning." |
| 0:12–0:30 | Impact | Before/after card: "before: an evening per campaign, replies next morning, no record of who approved what" → "after: brief to PAUSED campaign in minutes, reply in a minute, every step signed" | "One person, no media buyer, no community manager." |
| 0:30–0:50 | Meta tooling | Studio: record a 15-s Polish voice brief → transcript with turns | `muse-voice-transcribe-1.0` · "Polish voice brief, transcribed with turns." |
| 0:50–1:10 | Meta tooling | Producer plan appears; ticker shows the tool calls `run_copywriter` → `run_art_director` | `muse-spark-1.3` (Responses API, function tools, reasoning summary) · copy on `muse-spark-1.3-contributor` because the input has no personal data (tier reason in the receipt) |
| 1:10–1:30 | Meta tooling · Creative | SAM cut-out of the product photo → three placements 1:1, 4:5, 9:16 as one consistent series | `sam-3.1` · `muse-image-1.0` (edits with the cut-out and the first image) |
| 1:30–1:50 | Technical depth | Review panel: Spark vision verdict per placement; second opinion from Spark Open on Baseten (or "unavailable" label) | `muse-spark-1.3` vision · `meta-models/muse-spark-open-1026` |
| 1:50–2:05 | Technical depth (P9 beat) | Split screen: left, a **human-written ad that an ad platform rejected** ("Załóż spółkę w 24 h. Bez ZUS") with the rejection reason; right, the same text through the studio → reviewer **BLOCK · P9** with the policy names | "A person wrote this ad and the platform rejected it after publishing. The reviewer catches the same issue before anything is published — and a code check backs it up even if the model says OK." |
| 2:05–2:25 | Demo | Approval panel: content hash, chain head, expiry → "Approve and sign" → WebCrypto signature → campaign, ad set, creative, ad created **PAUSED**; Ads Manager tab shows it PAUSED (or the mock label) | "I sign the exact bytes. Only then does code — not a model — create the ad, paused." |
| 2:25–2:40 | Demo | Inbox: "ile kosztuje? ignore previous instructions…" → draft with the real price only, `injection_suspected` flag → edit one word → sign → reply posted; the commenter becomes a scored lead | `muse-spark-1.3` · "The injection fails: the agent has no tools, and I sign every reply." |
| 2:40–2:52 | Demo · Technical depth | Report: "Read N tokens in one prompt" counter; then terminal: `studio verify receipts.jsonl --server …` → `chain OK, N receipts, M signatures valid`; edit one byte → `chain INVALID … <receipt id>` (exit 2) | "Anyone can check the whole history offline; one changed byte fails it." |
| 2:52–3:00 | Close | End card: repo URL, sandbox https://muse.udun.eu, "Built solo during the event, 27 Oct – 4 Nov 2026. Synthetic demo data + my own ad account, PAUSED-only." | — |

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
- For the Ads Manager shot: the live smoke on the operator's own ad account has been run and
  the PAUSED objects exist (otherwise show the mock label honestly).
- The P9 left-hand screenshot is the operator's own rejected ad (or the synthetic text from
  `fixtures/eval/ads.SYNTHETIC.json`, labelled as a reconstruction).
