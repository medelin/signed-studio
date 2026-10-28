"""What the MCP server (``studio mcp``) tells the agent that drives it, in English and
Polish (the bound account's language). Merged into ``i18n.EN`` / ``i18n.PL``; the same key
and placeholder rules apply (``tests/test_i18n.py``)."""

from __future__ import annotations

EN_MCP: dict[str, str] = {
    "mcp.awaiting_signature": (
        "Awaiting human signature. The owner opens {url}, checks the ad and signs it in "
        "the browser; only then does code create the campaign, paused. No MCP tool can "
        "sign or send."),
    "mcp.awaiting_activation": (
        "The campaign exists and is paused. Turning it on is a separate human signature: "
        "the owner opens {url}. No MCP tool can activate it."),
    "mcp.nothing_to_sign": "Nothing in this run is waiting for a signature (status: {status}).",
    "err.unknown_tool": "This MCP server has no such tool; it can only read and draft.",
    "err.invalid_arguments": "The tool's arguments do not match its schema.",
    "err.account_inactive": "The account this MCP server is bound to is not active.",
    "err.path_not_allowed": "Only a .jsonl receipts export of at most 64 MB can be verified.",
    "err.internal_error": "The tool failed; the call is recorded in a receipt.",
}

PL_MCP: dict[str, str] = {
    "mcp.awaiting_signature": (
        "Czeka na podpis człowieka. Właściciel otwiera {url}, sprawdza reklamę i podpisuje "
        "ją w przeglądarce; dopiero wtedy kod tworzy kampanię, wstrzymaną. Żadne narzędzie "
        "MCP nie może podpisać ani wysłać."),
    "mcp.awaiting_activation": (
        "Kampania istnieje i jest wstrzymana. Jej włączenie to osobny podpis człowieka: "
        "właściciel otwiera {url}. Żadne narzędzie MCP nie może jej włączyć."),
    "mcp.nothing_to_sign": "W tym przebiegu nic nie czeka na podpis (status: {status}).",
    "err.unknown_tool": "Ten serwer MCP nie ma takiego narzędzia; umie tylko czytać i "
                        "przygotowywać szkice.",
    "err.invalid_arguments": "Argumenty narzędzia nie pasują do jego schematu.",
    "err.account_inactive": "Konto, z którym związany jest ten serwer MCP, nie jest aktywne.",
    "err.path_not_allowed": "Sprawdzić można tylko eksport pokwitowań .jsonl o rozmiarze do "
                            "64 MB.",
    "err.internal_error": "Narzędzie zawiodło; wywołanie zapisano w pokwitowaniu.",
}
