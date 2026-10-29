# Scenarios: Muse works for the owner

Two kinds of scenarios, one principle: **Muse explains Meta's rules, it does not invent new
ones.** The owner pays for a campaign that runs, not for an AI that adds its own
restrictions. Every limit Muse shows names its source (a Meta policy page or Meta's own
format specification), says what would happen on Meta's side (rejection, account flag,
truncated text), and comes with a compliant way to say what the owner meant.

Status of each scenario: **works** (in the code and a recording), **partly** (the parts
exist, the scenario needs a small change), **proposal** (not built).

## A. Meta's rules, explained (for the owner and for judges from Meta)

| # | The owner asks for | What Meta would do | What Muse does | Status |
|---|---|---|---|---|
| A1 | "Our mug lowers blood pressure in 7 days, guaranteed" | Disapproves the ad: health claims and guaranteed results ([Advertising Standards: Unacceptable Business Practices / Health](https://transparency.meta.com/policies/ad-standards/)) | The copywriter keeps the intent (a calm break with a good mug), leaves the claim out and lists it under `risks` with the rule and the reason; the owner sees the dropped claim and can still overrule in the brief | **works** (7 live recordings: the claim never reached the ad) |
| A2 | "Masz nadwagę? Schudnij do świąt" | Disapproves: personal attributes (asserting or implying a person's health condition) | Rephrases from "you are X" to "for people who want Y"; the approval screen shows a "Left out to keep the ad within Meta's rules" box: the owner's words, the rule in one sentence (EN/PL), the Meta policy link, the phrasing used, and a link back to the brief to overrule | **works** in code (`CopyPack.left_out`, signed in the payload; `tests/test_left_out.py`); older recordings show their free-text `risks` as plain lines |
| A3 | A 94-character primary text for Reels | Shows it truncated after ~44 characters | Says where the cut falls ("shown as 'Z jednego briefu: plan kampanii, 3 warianty '…") and offers a short version; this is Meta's own recommendation, so it is a tip, never a block | **works** (F1 is a tip with the spec URL, never raises the verdict; `studio/agents/rules.py`) |
| A4 | An ad set for Poland without naming who pays | Refuses the ad set in the EU (DSA: beneficiary and payor are mandatory) | Asks for the two names once, in plain words, and fills them in for every EU ad set | **works** (live run of 2026-10-29) |
| A5 | A lead form without a privacy page | Refuses the form | Asks for the owner's own privacy URL; never writes a policy for the owner | **works** (live instant form of 2026-10-29) |
| A6 | Targeting 25–35-year-old women for a loan offer | Special ad categories (credit, employment, housing) restrict age, gender and location targeting | Detects the category from the brief, explains which targeting Meta will not allow and builds the ad set within the allowed options | **proposal** |
| A7 | Meta disapproved an ad after launch | The ad stops; the reason is a policy code in Ads Manager | Every insights pull reads each ad's `effective_status` and `ad_review_feedback` (read-only, one `ad.review` receipt); the inbox explains Meta's reason in plain EN/PL with the policy link next to Meta's own words; the copywriter writes one variant from the same facts, both reviewers re-check it, and the owner signs one `campaign.update` (`replace_creative`): a new creative and a new ad in the same ad set, PAUSED. The disapproved ad is left as it is | **works** with the mock adapter and the Graph contract (fake transport); not yet seen on a live disapproval |

What Muse must not do (fixed on 2026-10-29; one table in code, `studio/agents/rules.py`,
decides every finding's level and source, see [policy.md](policy.md)):

- **P9** (company formation, "no ZUS obligation") comes from **Google Ads** policies and from
  one firm's own history; it is not a Meta rule. **Done:** a block only when the brief also
  runs on Google Search; on a Meta-only campaign a tip that names its Google source (kept
  visible, not omitted, because the same copy can be exported to Google later).
- **P5 "brand name missing"**: Meta does not require the advertiser's name in the copy (the
  Page name is shown on every ad). **Done:** a tip. A misleading affiliation with Meta or
  another company is listed under P3 (Meta: Unacceptable business practices).
- **P10** (every claim must come from what the owner confirmed) stays a block: it protects
  the owner from claims the *model* invented, not from the owner's own words. It never
  blocks a fact the owner confirmed.
- **Never remove the owner's intent silently**: whatever the copywriter drops or rephrases is
  shown on the approval screen (A2) and signed with the ad.

Rule for every finding: **BLOCK** only when Meta would reject or flag it (link to the
policy), **WARN** when Meta would show it worse, **tip** for best practice (Meta's own
recommended lengths included), and never a finding without a source except the
anti-invention rules (`muse:grounding`). The models tag a level and a source; code caps
both, and a tip never makes the verdict WARN or BLOCK.

## B. Muse's model superpowers, used for the owner

| # | Scenario (one sentence the owner would say) | Models | What the owner gains | Status |
|---|---|---|---|---|
| B1 | "I said the brief into my phone in the car, in Polish, in 15 seconds." | Muse Voice Transcribe → Muse Spark | An evening of copywriting becomes a 15-second voice note; [FACT] questions catch anything it would have guessed | **works** |
| B2 | "I took one photo of the mug on my kitchen table." | SAM 3.1 cut-out → Muse Image (feed 1:1, 4:5, story 9:16) → SAM check | No photographer, no designer; every placement keeps the real product (IoU 0.83–0.93 in the demo) | **works** |
| B3 | "A customer asked the price at 21:03 and got an answer at 21:04." | Muse Spark triage (prompt-injection safe) → draft reply → owner signs on the phone | No lost evening leads; nothing is posted without the owner's tap | **works** |
| B4 | "The form sent me 12 leads; Muse told me which 3 to call first and why." | Muse Spark lead rationale (or Spark Open in open-weights mode, so personal data stays on open weights) | Calls go to the warm leads first; the reason is written down | **works** |
| B5 | "Muse read all of last month's results and said: pause variant B, move £2/day to A." | Muse Spark, long context (the whole campaign history in one call) | Budget follows results; every change is a signed recommendation, never automatic | **works** (signed recommendations); the month-long history in one call is in the analyst |
| B6 | "I got a 15-second Reel from my three photos." | Muse Wonder shot list → ffmpeg | Video without filming | **partly** (shot list and assembly; no generated motion) |
| B7 | "Meta rejected my ad; Muse fixed it before my coffee was cold." | Muse Spark reads the disapproval, rewrites, reviewers re-check | The campaign runs again the same morning (A7 from the owner's side) | **partly** (as A7): the replacement ad is created PAUSED; in a campaign that is already live, switching that one ad on is not yet a signed action in the studio (Ads Manager today) |
| B8 | "Same campaign, now for UK customers, in English, in GBP." | Muse Spark translation + local review | A second market from one confirmed brief, facts unchanged | **proposal** |
| B9 | "My customers' comments and leads never left an open model I could run myself." | Muse Spark Open (open-weights mode, `pii` tier `open`) | Privacy by choice of weights, not by promise | **works** |

## For the 3-minute video

Beat 4 shows **A1** (the claim the owner asked for, left out with the rule and the reason)
and the two reviewers disagreeing out loud. Beats 2, 3 and 5 already show B1, B2, B3 and the
lead form. A 15-second cut for X can be B3 alone: comment at 21:03 → signed answer at 21:04.
