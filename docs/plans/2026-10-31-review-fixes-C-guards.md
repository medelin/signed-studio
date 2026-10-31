# Group C — content guards: reviewer, PII, grounding, community
Owns: `studio/agents/reviewer.py`, `studio/agents/rules.py`, `studio/pii.py`,
`studio/brief.py` (only `ungrounded_numbers` and `problems()`),
`studio/agents/community.py`, `studio/routes_inbox.py`, `studio/agents/lead_scorer.py`,
their tests. Read common rules.

1. **BLOCK becomes OK** (reviewer.py ~242-286). (a) `normalize` recomputes the verdict
   from capped severities whenever there is ≥1 finding, so a model BLOCK with an
   info/TIP-capped finding becomes OK; (b) `with_findings`/`with_policy_net`
   rebuild the review from code findings and lose the fail-closed floor. Rule:
   final verdict = max(model verdict, verdict derived from findings) — a model
   BLOCK/WARN can never be lowered by code. Keep rule caps for findings shown.
   Test the prompt-injection case (model says BLOCK with severity 'info').
2. **PII digit runs touching letters** (pii.py ~35, ~172): `tel600123456`,
   `PESEL44051401458` pass unmasked. Mask digit runs of phone/ID length even when
   adjacent to letters; keep existing tests green. Also the email regex is
   ASCII-only (~33): support IDN local/domain letters (e.g. `kontakt@sklep-żabka.pl`).
   Covers community.triage, lead_scorer.explain, receipts.redact (receipts.py:
   change only the call if needed).
3. **ungrounded_numbers** (brief.py ~436): compares bare digit runs as sets, so
   `19.99` and `50%` pass when 19, 99, 50 appear in facts. Compare whole numeric
   tokens (decimals with . or , as one token, with % / currency attached) against
   numbers in the facts.
4. **Lifetime budget skips the ceiling** (brief.py ~355-358): compare
   `daily_budget(brief)` with the ceiling for both kinds.
5. **Re-triage downgrades replied/hidden comments** (community.py ~221-227,
   routes_inbox.py ~152-176): `save` keeps terminal status; the triage route
   refuses terminal comments (409).
6. **Unapproved price guard** (community.py ~54-123): parse amount+currency in both
   orders (`$20`, `PLN 49`, `€15`, `49 zł`) and compare (amount, currency) pairs
   with the price sheet; a bare number next to buy words stays flagged per
   current rule.
Tests: one per item.
