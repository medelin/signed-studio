# Changelog

## 2026-10-27

- D1-1 repo bootstrap: pyproject, Makefile, CI (pytest, ruff, gitleaks), env template.
- D1-3 receipts: hash-chained provenance log signed with the server Ed25519 ledger key (recomputed hashes without the key are rejected) plus one signed export manifest (seq, ts, head + count per run): truncation, whole-run deletion and rollback to an older export are detected (canonical JSON, redaction of e-mail/phone, `verify` checks hashes, links, timestamps and approval-before-external-write); 16 tests.
