"""UI strings for open-weights mode, in English and Polish. Merged into ``i18n.EN`` /
``i18n.PL``; the same key and placeholder rules apply (``tests/test_i18n.py``)."""

from __future__ import annotations

EN_OPEN: dict[str, str] = {
    "brief.f.open_weights": "Run on open weights (Muse Spark Open)",
    "brief.h.open_weights": "The copywriter runs on Meta's open-weight Muse Spark Open "
                            "instead of the hosted model, with the same schema and checks. "
                            "Receipts name the model family of every step.",
    "brief.h.open_weights_forced": "Open-weights mode is on for this studio "
                                   "(STUDIO_OPEN_WEIGHTS=1): comments and leads are also "
                                   "processed only on open weights.",
    "err.open_weights_unavailable": "Open-weights mode needs Muse Spark Open "
                                    "(BASETEN_API_KEY), which is not configured here.",
    "err.invalid_open_weights": "open_weights must be true or false.",
}

PL_OPEN: dict[str, str] = {
    "brief.f.open_weights": "Uruchom na otwartych wagach (Muse Spark Open)",
    "brief.h.open_weights": "Copywriter działa na otwartym modelu Meta Muse Spark Open "
                            "zamiast modelu hostowanego, z tym samym schematem i "
                            "kontrolami. Pokwitowania podają rodzinę modelu każdego kroku.",
    "brief.h.open_weights_forced": "Tryb otwartych wag jest włączony dla tego studia "
                                   "(STUDIO_OPEN_WEIGHTS=1): komentarze i leady też są "
                                   "przetwarzane wyłącznie na otwartych wagach.",
    "err.open_weights_unavailable": "Tryb otwartych wag wymaga Muse Spark Open "
                                    "(BASETEN_API_KEY), który nie jest tu skonfigurowany.",
    "err.invalid_open_weights": "open_weights musi mieć wartość true albo false.",
}
