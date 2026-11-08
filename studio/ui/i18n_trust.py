"""The public proof page (``/trust``), ``/verify/<hash>`` and the printable receipt,
English and Polish. Merged into ``studio.ui.i18n`` (``EN``/``PL``)."""

from __future__ import annotations

EN_TRUST: dict[str, str] = {
    "meta.trust": "Check Signed Studio's proof without an account: the ledger's public key, "
                  "the latest receipt hash, a live self-check and the test counts.",
    "trust.title": "Proof, without an account",
    "trust.lead": "Every step the studio takes leaves a receipt, and every receipt is "
                  "signed with one key and chained to the one before it. You can check that "
                  "yourself; nothing on this page needs a login.",
    "trust.what_h": "What this proves",
    "trust.what": "A receipt is a signed record of one step (a model call, an approval, a "
                  "PayPal call). If anyone changes, deletes, reorders or invents a receipt, "
                  "its hash or the signature stops matching, and the checker says which one. "
                  "The six money rules (P-V1 to P-V6) go further: no capture without the "
                  "owner's signature on that exact order, no refund beyond what was "
                  "captured, no charge outside a signed mandate. What it does not prove: "
                  "that PayPal or Meta told the truth, or that the ads perform.",
    "trust.key_h": "The ledger's public key",
    "trust.key_hint": "Ed25519, 32 bytes, hex. This is the key `studio verify` expects "
                      "(--key). Also at /api/ledger-key.",
    "trust.copy": "Copy", "trust.copied": "Copied",
    "trust.chain_h": "The ledger now",
    "trust.count": "Receipts", "trust.head": "Newest receipt hash",
    "trust.head_none": "no receipts yet",
    "trust.check_h": "Last self-check",
    "trust.check_ok": "verified at {time}: chain OK, {n} receipts, {m} signatures, "
                      "P-V1…P-V6 OK",
    "trust.check_bad": "verified at {time}: chain INVALID, {n} receipts, {m} signatures, "
                       "{p} problems, P-V1…P-V6 {rules}",
    "trust.rules_ok": "OK", "trust.rules_fail": "FAIL",
    "trust.check_hint": "The server runs the same checks as `studio verify` on its own "
                        "ledger, at most every 10 minutes. Do not trust it: run the "
                        "command below yourself.",
    "trust.tests_h": "Tests",
    "trust.tests": "{total} tests, {attack} of them attack tests (tampered exports, forged "
                   "approvals, replayed payments) as of {date}.",
    "trust.tests_none": "Test counts are not shipped with this build.",
    "trust.links_h": "Check it yourself",
    "trust.cmd_h": "One line",
    "trust.link_export": "Export of the whole ledger (receipts.jsonl; needs a session, see "
                         "the help)",
    "trust.link_help": "How to verify an export",
    "trust.link_repo": "Source code on GitHub",
    "trust.link_video": "Demo video",
    "trust.link_try": "Try it without an account",
    "trust.link_manifest": "Signed manifest (public)",
    "verify.title": "Receipt check",
    "verify.found": "This receipt exists in the chain and is signed by the ledger key.",
    "verify.unsigned": "A receipt with this hash exists, but its signature or hash does not "
                       "check out.",
    "verify.unknown": "No receipt with this hash is in the chain.",
    "verify.bad_hash": "That is not a SHA-256 hash (64 hex characters).",
    "verify.step": "Step", "verify.time": "Recorded at (UTC)", "verify.hash": "Hash",
    "verify.back": "Back to the proof page",
    "print.title": "Receipt",
    "print.prev": "Previous hash", "print.sig": "Signature (Ed25519, hex)",
    "print.hash": "Hash (SHA-256)", "print.qr": "Scan to check this receipt",
    "print.qr_label": "QR code with the address that checks this receipt",
    "print.print": "Print", "print.back": "All receipts",
    "print.fields": "Fields",
    "foot.trust": "Proof",
}

PL_TRUST: dict[str, str] = {
    "meta.trust": "Sprawdź dowody Signed Studio bez konta: klucz publiczny księgi, hash "
                  "ostatniego pokwitowania, samosprawdzenie na żywo i liczby testów.",
    "trust.title": "Dowód, bez konta",
    "trust.lead": "Każdy krok studia zostawia pokwitowanie, a każde pokwitowanie jest "
                  "podpisane jednym kluczem i spięte z poprzednim. Możesz to sprawdzić "
                  "sam; nic na tej stronie nie wymaga logowania.",
    "trust.what_h": "Co to dowodzi",
    "trust.what": "Pokwitowanie to podpisany zapis jednego kroku (wywołania modelu, "
                  "zatwierdzenia, wywołania PayPal). Jeśli ktoś zmieni, usunie, przestawi "
                  "albo dopisze pokwitowanie, jego hash lub podpis przestaje się zgadzać, "
                  "a weryfikator wskazuje które. Sześć reguł pieniężnych (P-V1 do P-V6) "
                  "idzie dalej: żadnego pobrania bez podpisu właściciela na to konkretne "
                  "zamówienie, żadnego zwrotu ponad pobraną kwotę, żadnego obciążenia "
                  "poza podpisanym mandatem. Czego nie dowodzi: że PayPal lub Meta "
                  "powiedziały prawdę ani że reklamy się sprawdzą.",
    "trust.key_h": "Klucz publiczny księgi",
    "trust.key_hint": "Ed25519, 32 bajty, hex. Tego klucza oczekuje `studio verify` "
                      "(--key). Także pod /api/ledger-key.",
    "trust.copy": "Kopiuj", "trust.copied": "Skopiowano",
    "trust.chain_h": "Księga teraz",
    "trust.count": "Pokwitowania", "trust.head": "Hash najnowszego pokwitowania",
    "trust.head_none": "jeszcze brak pokwitowań",
    "trust.check_h": "Ostatnie samosprawdzenie",
    "trust.check_ok": "sprawdzono o {time}: łańcuch OK, {n} pokwitowań, {m} podpisów, "
                      "P-V1…P-V6 OK",
    "trust.check_bad": "sprawdzono o {time}: łańcuch NIEPRAWIDŁOWY, {n} pokwitowań, "
                       "{m} podpisów, {p} problemów, P-V1…P-V6 {rules}",
    "trust.rules_ok": "OK", "trust.rules_fail": "BŁĄD",
    "trust.check_hint": "Serwer uruchamia te same kontrole co `studio verify` na własnej "
                        "księdze, najwyżej co 10 minut. Nie ufaj mu: uruchom polecenie "
                        "poniżej samodzielnie.",
    "trust.tests_h": "Testy",
    "trust.tests": "{total} testów, w tym {attack} testów ataku (podrobione eksporty, "
                   "sfałszowane zatwierdzenia, powtórzone płatności), stan na {date}.",
    "trust.tests_none": "Liczby testów nie są dołączone do tej wersji.",
    "trust.links_h": "Sprawdź sam",
    "trust.cmd_h": "Jedna linijka",
    "trust.link_export": "Eksport całej księgi (receipts.jsonl; wymaga sesji, zobacz pomoc)",
    "trust.link_help": "Jak zweryfikować eksport",
    "trust.link_repo": "Kod źródłowy na GitHubie",
    "trust.link_video": "Film demonstracyjny",
    "trust.link_try": "Wypróbuj bez konta",
    "trust.link_manifest": "Podpisany manifest (publiczny)",
    "verify.title": "Sprawdzenie pokwitowania",
    "verify.found": "To pokwitowanie jest w łańcuchu i podpisał je klucz księgi.",
    "verify.unsigned": "Pokwitowanie o tym hashu istnieje, ale jego podpis lub hash się "
                       "nie zgadza.",
    "verify.unknown": "W łańcuchu nie ma pokwitowania o tym hashu.",
    "verify.bad_hash": "To nie jest hash SHA-256 (64 znaki szesnastkowe).",
    "verify.step": "Krok", "verify.time": "Zapisano (UTC)", "verify.hash": "Hash",
    "verify.back": "Wróć do strony z dowodami",
    "print.title": "Pokwitowanie",
    "print.prev": "Poprzedni hash", "print.sig": "Podpis (Ed25519, hex)",
    "print.hash": "Hash (SHA-256)", "print.qr": "Zeskanuj, żeby sprawdzić to pokwitowanie",
    "print.qr_label": "Kod QR z adresem, który sprawdza to pokwitowanie",
    "print.print": "Drukuj", "print.back": "Wszystkie pokwitowania",
    "print.fields": "Pola",
    "foot.trust": "Dowód",
}
