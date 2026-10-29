# Review policy

**Muse explains Meta's rules, it does not invent new ones** ([scenarios.md](scenarios.md)).
Every finding carries a **source** (the Meta policy page or format specification it stands
on, a Google Ads policy for copy that also runs on Google, or `muse:grounding` for the
anti-invention rules) and a **level**:

- **block** only where the platform would reject the ad or flag the account;
- **warn** where it would show the ad worse;
- **tip** for best practice, including Meta's own recommended lengths. A tip never makes
  the verdict WARN or BLOCK;
- **info** is a note from a code check (no issue).

The models tag a level and a source too, but **code has the final say**: one table
(`studio/agents/rules.py`, `RULES`) gives each rule its highest level per channel and its
source; `reviewer.normalize` caps every finding (the models', `policy_net`'s,
`grounding_net`'s, F1's, SAM's) and derives the verdict from the capped levels. A model
verdict that names no finding at all is kept as given (fail closed).

## Levels by rule

| Rule | On Meta | When the copy also runs on Google Search | Source |
|---|---|---|---|
| P1 personal attributes | block | block | https://transparency.meta.com/policies/ad-standards/objectionable-content/privacy-violations-personal-attributes/ |
| P2 before/after, body image | block | block | https://transparency.meta.com/policies/ad-standards/restricted-goods-services/health-wellness |
| P3 unrealistic promises, misleading affiliation | block | block | https://transparency.meta.com/policies/ad-standards/fraud-scams/unacceptable-business-practices/ |
| P4 price and offer accuracy | block | block | same |
| P5 brand name | **tip** (the Page name shows on every ad) | tip | https://www.facebook.com/business/ads-guide/update/image/facebook-feed |
| P6 image text, fake buttons | block (a fake control), the model may give less | block | https://transparency.meta.com/policies/ad-standards/deceptive-content/non-existent-functionality/ |
| P7 prohibited or sensitive content | block | block | https://transparency.meta.com/policies/ad-standards/ |
| P8 regulated claims | block | block | https://transparency.meta.com/policies/ad-standards/restricted-goods-services/health-wellness |
| P9 official services, absolute tax/legal promises | **tip**, with its Google source | **block** | https://support.google.com/adspolicy/answer/13156083 |
| P10 grounding in the confirmed brief | block | block | `muse:grounding` |
| F1 hard format limit / recommended length | block / **tip** | block / tip | the spec URL named in the finding |
| S1 product fidelity (SAM) | block (product missing), warn (redrawn, cropped) | same | `muse:grounding` |
| S2 people in the image | **tip** | tip | `muse:prompt` |
| a finding without a known rule (`UNMAPPED`) | the model's own level (fail closed: a block stays a block) | same | `muse:unmapped-rule` |

Why P9 is a tip on Meta and not omitted: it is a Google Ads rule with a real history (two
ads disapproved by Google, below), and the same copy can be exported to Google later; on a
Meta-only campaign the owner sees it, with its Google source, and it never blocks.
Channels come from the confirmed brief (`channels.google_search` adds `google`).

Levels were applied to the recorded replay sets by re-deriving every verdict from the
recorded findings. Changed outcomes (docs/eval.md seeded set): `p5-abbrev` WARN → OK and
`p5-meta` BLOCK → OK (P5 is a tip; a misleading affiliation with Meta is now listed under
P3 in the checklist), `p6-fake-ui` stays flagged (block). The P9 set is scored as copy that
also runs on Google Search (5/5 blocked).

Every creative is checked three ways before a human can sign it:

1. **Code checks, before any model.** Rule **F1** (format limits, below) and the **P9** phrase
   check (`reviewer.policy_net`). Deterministic; findings land in the `review` receipt
   (`details.format`, `details.policy_net`).
2. **Two model reviewers** against the checklist P1–P9 (plus P10 for ads made from a
   confirmed Brief v1, below) (`studio/agents/prompts/_policy.md`):
   Muse Spark 1.3 vision and Muse Spark Open on Baseten. BLOCK from either side wins; every
   disagreement is shown on the approval screen.
3. **The human** reads the exact payload and signs it, or not.

## Checklist P1–P9 (both model reviewers)

P1 personal attributes · P2 before/after and body image · P3 unrealistic promises · P4 price
and offer accuracy · P5 brand name · P6 image text, fake buttons or UI · P7 prohibited or
sensitive content · P8 regulated claims · **P9 official services and absolute tax/legal
promises** (a Google Ads rule: block on Google, tip on Meta) — the advertiser must not claim to register companies or provide government
documents/official services ("register your company", "załóż spółkę"), nor promise absolute
tax or legal outcomes ("no ZUS obligation", "pay no tax"); Google Ads policies "Government
documents and official services" and "Exaggerated or inaccurate claims".

Why P9 and the unfilled-`[FACT]` rule exist: a model-invented legal/tax claim once ran in a
paid promotion of a small firm's ad, and two human-written ads promising company formation
and "brak obowiązku ZUS" were disapproved by Google Ads. Since then the copywriter may not
invent a fact (it writes `[FACT]` and the variant cannot be signed until a person fills it),
and P9 is checked by both models and by code.

## P10 — grounding in the confirmed Brief v1

For an ad made from a confirmed Brief v1 ([brief.md](brief.md)) both model reviewers get,
in the user message, the owner's confirmed statements (facts, offer, proposition,
disclaimers, banned claims; inside an untrusted-content envelope, source `brief_facts`) and
rule P10 (`studio/agents/prompts/_grounding.md`): every factual claim in the copy or the
image (a price, number, date, time, duration, guarantee, availability, what is included)
must be supported by a confirmed statement; an unsupported claim, or a claim the brief
bans, is a **block**. Code checks the same before the models answer
(`reviewer.grounding_net`): a number in the copy that appears nowhere in what the owner
confirmed, or a banned claim, is a P10 block finding, and `request_approval` refuses such a
variant even if a reviewer missed it. P10 is not part of the shared system prompt: it varies
with each brief, and free-text runs (and their recordings) are reviewed as before, P1–P9.

Why: P9 and the `[FACT]` placeholder stop invented claims in the copy; P10 closes the gap
between the copy and the owner, so an ad can only say what the owner confirmed, and the
owner's signature covers those facts (`brief_sha256`).

## Every creative and every text variant

Carousel cards, the video's poster frame and every copy variant that will run (copy A/B)
go through the same three checks as the feed and story images: F1, both model reviewers
(P1–P9, P10 for a Brief v1 run), then the human signature. The video itself is cut by code
from images that were reviewed; its poster is reviewed as its own creative. For a Brief v1
run the reviewers judge the brand name (P5) and prices and offers (P4) against the owner's
advertiser and confirmed statements, not against the demo brand sheet of the system
prompt.

## Changes after a campaign exists

A recommendation from the measurement rules (`studio/meta/measurement.py`) is a proposal.
A budget change (within the daily ceiling, in the ad account's currency) or a pause is a
new `campaign.update` approval the owner signs; the executor makes the one call it names.
A recommendation can never activate anything: activation stays its own signed
`campaign.activate`.

## F1 — format limits from each platform's own specifications

`studio/agents/formats.py`, `reviewer.format_check(placement, ad)`. A platform's **hard**
limit (the field or upload is refused) is a **BLOCK**; a **recommended** length (the text is
cut off or performs worse) is Meta's own recommendation, so it is a **tip** that names the
truncation point and links the spec (it never raises the verdict). Only **Meta** is
wired to publishing; the **Google Ads** and **TikTok** rules check copy variants exported for
those platforms. All sources official, retrieved **2026-10-28**.

### Meta (Facebook / Instagram) — placements `feed_1x1`, `feed_4x5`, `story_9x16`, carousel cards, the video

| Check | Value | Kind | Source |
|---|---|---|---|
| Facebook Feed primary text | 50–150 characters (warn above 150) | recommended | https://www.facebook.com/business/ads-guide/update/image/facebook-feed |
| Facebook Feed headline | 27 characters | recommended | same |
| Facebook Feed image (4:5) | ratio 4:5, tolerance 3 %, min width 600 px, min height 750 px; 1440×1800 recommended; JPG/PNG ≤ 30 MB | hard | same |
| Instagram Feed primary text / headline | 125 / 40 characters | recommended | https://www.facebook.com/business/ads-guide/update/image/instagram-feed |
| Instagram Feed image | ratio between 4:5 and 1.91:1, tolerance 1 %, min width 500 px | hard | same |
| Instagram Stories primary text | 125 characters | recommended | https://www.facebook.com/business/ads-guide/update/image/instagram-story |
| Instagram Reels primary text | 44 characters | recommended | https://www.facebook.com/business/ads-guide/update/image/instagram-reels |
| Stories / Reels image | 9:16, tolerance 1 %, min width 500 px; 1440×2560 recommended | hard | Stories and Reels pages above |
| Stories / Reels safe zone | keep ~14 % top, 35 % bottom, 6 % each side free of text and logos | guidance (model reviewers, P6; not measured by code) | same |
| Link URL | 1000 characters | hard | https://developers.facebook.com/docs/marketing-api/reference/ad-creative |
| Carousel cards | 2 to 10 | hard (refused before signing) | https://www.facebook.com/business/ads-guide/update/carousel/facebook-feed |
| Carousel card image (`card_<n>`) | 1:1, tolerance 3 %, min 600 × 600 px; at least 1080 × 1080 recommended (warn) | hard / recommended | same |
| Carousel primary text / headline / description | 80 / 20 / 18 characters | recommended | same |
| Reels video (`video_9x16`) | 9:16 poster, tolerance 1 %, min width 500 px; duration up to 15 minutes; file up to 4 GB; MP4 | hard | https://www.facebook.com/business/ads-guide/update/video/instagram-reels |
| Marketing API title / body | 25 / 90 characters, labelled "recommended" by the API reference | not enforced (the placement lengths above apply) | same |

Text in images: none of the official pages read states a current percentage rule; the
studio keeps its own P6 (at most the headline as text in the image), judged by the models.

### Google Ads (exported copy only)

| Format | Limits (hard) | Source |
|---|---|---|
| Responsive search ad | headline ≤ 30, description ≤ 90, path ≤ 15 characters; 3–15 headlines, 2–4 descriptions; double-width (CJK) characters count as 2 | https://support.google.com/google-ads/answer/7684791 |
| Performance Max | headline ≤ 30, long headline ≤ 90, description ≤ 90, business name ≤ 25 (double-width count 2); images 1.91:1 (min 600×314), 1:1 (min 300×300), 4:5 (min 480×600), ≤ 5120 KB | https://support.google.com/google-ads/answer/17091269 |
| Demand Gen | headline ≤ 40, description ≤ 90, business name ≤ 25; images 1.91:1 (min 600×314), 1:1 (min 300×300), 4:5 (min 480×600) | https://support.google.com/google-ads/answer/17091672 |

### TikTok (exported copy only)

| Check | Value | Kind | Source |
|---|---|---|---|
| Display (account) name | ≤ 20 characters, ≤ 10 for Chinese/Japanese/Korean | hard | https://ads.tiktok.com/help/article/tiktok-auction-in-feed-ads |
| Non-Spark ad caption | no clickable links, `@` or hashtags | warn | same |
| Ad text length | not stated on the official pages that loaded on 2026-10-28 (the dedicated ad-text help articles returned "no longer exists") | not enforced | — |
