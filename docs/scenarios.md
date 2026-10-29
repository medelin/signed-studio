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
| A2 | "Masz nadwagę? Schudnij do świąt" | Disapproves: personal attributes (asserting or implying a person's health condition) | Rephrases from "you are X" to "for people who want Y" and explains the personal-attributes rule in one sentence | **partly**: rephrasing happens; the one-sentence explanation with the policy link is a proposal |
| A3 | A 94-character primary text for Reels | Shows it truncated after ~44 characters | Says where the cut falls ("shown as 'Z jednego briefu: plan kampanii, 3 warianty '…") and offers a short version; this is Meta's own recommendation, so it is a tip, never a block | **works** (F1 with the source link); downgrading F1 to a tip is a proposal |
| A4 | An ad set for Poland without naming who pays | Refuses the ad set in the EU (DSA: beneficiary and payor are mandatory) | Asks for the two names once, in plain words, and fills them in for every EU ad set | **works** (live run of 2026-10-29) |
| A5 | A lead form without a privacy page | Refuses the form | Asks for the owner's own privacy URL; never writes a policy for the owner | **works** (live instant form of 2026-10-29) |
| A6 | Targeting 25–35-year-old women for a loan offer | Special ad categories (credit, employment, housing) restrict age, gender and location targeting | Detects the category from the brief, explains which targeting Meta will not allow and builds the ad set within the allowed options | **proposal** |
| A7 | Meta disapproved an ad after launch | The ad stops; the reason is a policy code in Ads Manager | Reads the disapproval reason, explains it in Polish, proposes a compliant variant and asks for one signature to resubmit | **proposal** |

What Muse must not do (and the review currently does in two places — to fix):

- **P9** (company formation, "no ZUS obligation") comes from **Google Ads** policies and from
  one firm's own history; for a Meta campaign it is not a Meta rule. Proposal: apply P9 only
  to the Google Ads channel, or show it on Meta as a tip with its real source.
- **P5 "brand name missing"** is reported as a WARN although Meta does not require the
  advertiser's name in the copy (the Page name is shown on every ad). Proposal: a tip.
- **P10** (every claim must come from what the owner confirmed) stays: it protects the owner
  from claims the *model* invented, not from the owner's own words. It never blocks a fact
  the owner confirmed.

Rule of thumb for every finding: **BLOCK** only when Meta would reject or flag it (link to
the policy), **WARN** when Meta would degrade it (truncation, low quality), **tip** for
best practice, and never a finding that has no Meta source except the anti-invention rule.

## B. Muse's model superpowers, used for the owner

| # | Scenario (one sentence the owner would say) | Models | What the owner gains | Status |
|---|---|---|---|---|
| B1 | "I said the brief into my phone in the car, in Polish, in 15 seconds." | Muse Voice Transcribe → Muse Spark | An evening of copywriting becomes a 15-second voice note; [FACT] questions catch anything it would have guessed | **works** |
| B2 | "I took one photo of the mug on my kitchen table." | SAM 3.1 cut-out → Muse Image (feed 1:1, 4:5, story 9:16) → SAM check | No photographer, no designer; every placement keeps the real product (IoU 0.83–0.93 in the demo) | **works** |
| B3 | "A customer asked the price at 21:03 and got an answer at 21:04." | Muse Spark triage (prompt-injection safe) → draft reply → owner signs on the phone | No lost evening leads; nothing is posted without the owner's tap | **works** |
| B4 | "The form sent me 12 leads; Muse told me which 3 to call first and why." | Muse Spark lead rationale (or Spark Open in open-weights mode, so personal data stays on open weights) | Calls go to the warm leads first; the reason is written down | **works** |
| B5 | "Muse read all of last month's results and said: pause variant B, move £2/day to A." | Muse Spark, long context (the whole campaign history in one call) | Budget follows results; every change is a signed recommendation, never automatic | **works** (signed recommendations); the month-long history in one call is in the analyst |
| B6 | "I got a 15-second Reel from my three photos." | Muse Wonder shot list → ffmpeg | Video without filming | **partly** (shot list and assembly; no generated motion) |
| B7 | "Meta rejected my ad; Muse fixed it before my coffee was cold." | Muse Spark reads the disapproval, rewrites, reviewers re-check | The campaign runs again the same morning (A7 from the owner's side) | **proposal** |
| B8 | "Same campaign, now for UK customers, in English, in GBP." | Muse Spark translation + local review | A second market from one confirmed brief, facts unchanged | **proposal** |
| B9 | "My customers' comments and leads never left an open model I could run myself." | Muse Spark Open (open-weights mode, `pii` tier `open`) | Privacy by choice of weights, not by promise | **works** |

## For the 3-minute video

Beat 4 shows **A1** (the claim the owner asked for, left out with the rule and the reason)
and the two reviewers disagreeing out loud. Beats 2, 3 and 5 already show B1, B2, B3 and the
lead form. A 15-second cut for X can be B3 alone: comment at 21:03 → signed answer at 21:04.
