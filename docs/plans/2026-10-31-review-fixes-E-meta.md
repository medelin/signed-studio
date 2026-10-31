# Group E — Meta/Google adapters, disapprovals, leads, exports, budget
Owns: `studio/disapprovals.py`, `studio/leadads.py`, `studio/meta/graph.py`,
`studio/meta/adapter.py`, `studio/google/rest.py`, `studio/exports.py`,
`studio/budget_advice.py`, `studio/agents/producer.py` (only campaign_payload /
schedule), `studio/agents/media_buyer.py` (only activation of a replacement ad),
their tests. Read common rules.

Disapproval recovery (make the whole flow work end to end, with one test that
walks it: disapproved ad → propose → sign → replace → activate):
1. `_brief` (~215) validates stored brief_v1 with its `schema` key → ValidationError.
   Use one shared loader (add `brief.load_v1(json)` in brief.py if none; group C
   owns brief.py but this tiny addition is allowed — list it in the report).
2. Replacement ad on an ACTIVE campaign can never be activated (~362-366;
   media_buyer.request_activation refuses non-PAUSED). Add a signed path to
   activate just the replacement ad (new change kind, human-signed, PAUSED→ACTIVE
   for that ad only).
3. Renewed approval → `not_proposed` (~312-313): match via the supersede chain.
4. `failed` / expired `requested` can never be re-proposed (~229-230): allow them.
5. `propose` skips `copywriter.unsupported_character` and `pack.unfilled_facts`
   (~262): apply the same checks as producer.request_approval.
Others:
6. **Lifetime budget has no schedule** (meta/adapter.py ~356-373, producer campaign_payload):
   carry start/end into the signed payload and send `start_time`/`end_time` in
   adset fields (daily budget kept). Signed payload change must keep receipts
   verifiable (it is part of content_sha256 — new runs only).
7. **Lead phone dropped** (leadads.py ~98, FIELD_MAP ~52): make form keys and
   FIELD_MAP agree (`phone`).
8. **No paging** (graph.py ~463 list_leads, ~568 list_comments): follow
   `paging.next` (bounded pages), and only advance `polled_ts` after all pages.
9. **Writes retried** (graph.py ~202; google/rest.py ~159): never retry POST
   creates/mutates on transient errors; retry reads only.
10. **Google pageSize** (google/rest.py ~171): drop `pageSize`, paginate on
    `nextPageToken`.
11. **Statement drops overlapping windows** (exports.py ~578): compute per-day spend
    without losing days (prefer daily-granularity rows if the snapshot has them;
    otherwise allocate the non-overlapping part of each window) and book days in
    their own month.
12. **Statement counts unexecuted approvals** (exports.py ~510-516): join executions,
    count only `status = 'done'`.
13. **budget_advice hard-codes 100 minor units** (~49, ~91-96): use
    `meta.currency.offset/to_minor`; unknown currencies must not default to GBP
    scale silently (no advice, with a reason).
Tests: one per item.
