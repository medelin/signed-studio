# Review fixes (31 Oct 2026) — common rules for every group

Source: a whole-code review (local, 8 finders) and three cloud ultrareviews of
`main` at `c39d5fc`. Findings are claims, not facts: **reproduce each one with a
failing test first**. If a finding is wrong, do not change code; say why in the
report. Line numbers refer to `c39d5fc`.

Constraints (all mandatory):
- Work only in your own worktree (path given in the dispatch), on your branch.
  Edit only the files your plan owns; if a fix truly needs another file, keep
  the change minimal and list it under "touched outside scope" in the report.
- Python: `/home/divi/code/muse-studio-lite/.venv/bin/python -m pytest` run
  from your worktree root (it imports the worktree's `studio`). ruff + mypy
  clean (`.venv/bin/ruff check`, `.venv/bin/mypy studio`). Targeted tests
  while working; the full suite (~14 min, `-q -p no:cacheprovider`) once,
  green, before the last commit.
- English code and comments; EN+PL parity for any new user copy (i18n parity
  tests exist). Keep the surrounding style: small functions, docstrings that
  state the rule, no new dependencies.
- Security rules of the studio stay intact: PAUSED-only creates
  (`check_paused`), human-signed approvals, hash-chained receipts with no
  personal data, demo/trial limits.
- Commits: signed, author as configured in the repo (Piotr Kaczmarski), one
  commit per finding or tight group, message `fix(<area>): <what>`. **No
  Co-Authored-By, no "Generated with".**
- Never push, deploy, touch `.env`/secrets, or call live APIs (Stripe, Meta,
  Google, Muse). Mocks/fakes only.
- Report (≤ 40 lines) to the report path given: per finding — fixed (commit,
  test name) / not a bug (why) / deferred (why); full-suite line; anything
  where the fix rests on an assumption.
