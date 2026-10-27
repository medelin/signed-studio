# Changelog

## 2026-10-27

- D1-1 repo bootstrap: pyproject, Makefile, CI (pytest, ruff, gitleaks), env template.
- D1-3 receipts: hash-chained provenance log signed with the server Ed25519 ledger key (recomputed hashes without the key are rejected) (canonical JSON, redaction of e-mail/phone, `verify` checks hashes, links, timestamps and approval-before-external-write); 11 tests.
