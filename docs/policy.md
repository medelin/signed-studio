# Review policy

Every creative is checked three ways before a human can sign it:

1. **Code checks, before any model.** Rule **F1** (format limits, below) and the **P9** phrase
   check (`reviewer.policy_net`). Deterministic; findings land in the `review` receipt
   (`details.format`, `details.policy_net`).
2. **Two model reviewers** against the checklist P1–P9 (`studio/agents/prompts/_policy.md`):
   Muse Spark 1.3 vision and Muse Spark Open on Baseten. BLOCK from either side wins; every
   disagreement is shown on the approval screen.
3. **The human** reads the exact payload and signs it, or not.

## Checklist P1–P9 (both model reviewers)

P1 personal attributes · P2 before/after and body image · P3 unrealistic promises · P4 price
and offer accuracy · P5 brand name · P6 image text, fake buttons or UI · P7 prohibited or
sensitive content · P8 regulated claims · **P9 official services and absolute tax/legal
promises** — the advertiser must not claim to register companies or provide government
documents/official services ("register your company", "załóż spółkę"), nor promise absolute
tax or legal outcomes ("no ZUS obligation", "pay no tax"); Google Ads policies "Government
documents and official services" and "Exaggerated or inaccurate claims".

Why P9 and the unfilled-`[FACT]` rule exist: a model-invented legal/tax claim once ran in a
paid promotion of a small firm's ad, and two human-written ads promising company formation
and "brak obowiązku ZUS" were disapproved by Google Ads. Since then the copywriter may not
invent a fact (it writes `[FACT]` and the variant cannot be signed until a person fills it),
and P9 is checked by both models and by code.

## F1 — format limits from each platform's own specifications

`studio/agents/formats.py`, `reviewer.format_check(placement, ad)`. A platform's **hard**
limit (the field or upload is refused) is a **BLOCK**; a **recommended** length (the text is
cut off or performs worse) is a **WARN** that names the truncation point. Only **Meta** is
wired to publishing; the **Google Ads** and **TikTok** rules check copy variants exported for
those platforms. All sources official, retrieved **2026-10-28**.

### Meta (Facebook / Instagram) — placements `feed_1x1`, `feed_4x5`, `story_9x16`

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
