"""EN/PL strings for signed mandates (``studio.mandates``): the "Mandates" block on
``/account``, the limits on the studio's signing screen, the API note and the errors."""

from __future__ import annotations

EN_MANDATES: dict[str, str] = {
    "mandate.title": "Mandates",
    "mandate.lead": "A mandate lets your assistant create campaigns without asking you each "
                    "time, inside limits you sign once on this device. Campaigns made this "
                    "way are always created paused: turning one on, changing a budget and "
                    "replying to people still need your own signature.",
    "mandate.objectives": "Goals it may use",
    "mandate.countries": "Countries (ISO codes, e.g. PL, DE)",
    "mandate.countries_hint": "Every country a campaign targets must be on this list.",
    "mandate.hosts": "Websites ads may link to",
    "mandate.hosts_hint": "Host names only, e.g. shop.example.com. Every link in the ad must "
                          "point to one of them.",
    "mandate.budget": "Highest daily budget ({currency})",
    "mandate.budget_hint": "At most {ceiling} {currency}, the studio's ceiling.",
    "mandate.max_campaigns": "Most campaigns it may create",
    "mandate.days": "Valid for (days, 1 to {days})",
    "mandate.tokens": "Assistants (tokens) that may use it",
    "mandate.no_tokens": "Make an assistant token above first.",
    "mandate.prepare": "Prepare mandate",
    "mandate.prepare_hint": "Next you see every limit again and sign it on the studio's "
                            "signing screen. Nothing is active before you sign.",
    "mandate.caption": "Your mandates",
    "mandate.col.limits": "Limits",
    "mandate.col.used": "Used",
    "mandate.col.until": "Valid until (UTC)",
    "mandate.col.status": "Status",
    "mandate.col.action": "Action",
    "mandate.limits": "Goals: {objectives}. Countries: {countries}. Links to: {hosts}. "
                      "Up to {budget} {currency} a day. Tokens: {tokens}.",
    "mandate.used": "{used} of {max}",
    "mandate.status.active": "active",
    "mandate.status.revoked": "revoked",
    "mandate.status.expired": "expired",
    "mandate.status.used_up": "used up",
    "mandate.revoke_button": "Revoke",
    "mandate.revoke_label": "Revoke mandate {id}",
    "mandate.revoked": "The mandate is revoked. Runs that start from now on wait for your "
                       "signature again.",
    "mandate.none": "No mandates yet.",
    "mandate.problems": "Check these fields: {fields}.",
    "mandate.field.objectives": "goals",
    "mandate.field.countries": "countries",
    "mandate.field.landing_hosts": "websites",
    "mandate.field.max_daily_budget": "daily budget",
    "mandate.field.max_campaigns": "number of campaigns",
    "mandate.field.valid_until": "days valid",
    "mandate.field.token_ids": "assistants",
    "mandate.field.currency": "currency",
    "mandate.field.account_id": "account",
    "mandate.field.fields": "the form",
    "mandate.field.scope": "scope",
    "mandate.api_note": "Within a mandate that lists this token, a run you start creates its "
                        "campaign paused, without a new signature. Turning it on, budgets "
                        "and replies always wait for the owner. Only the owner makes or "
                        "revokes a mandate, on /account.",
    # the studio's signing screen (scripts)
    "js.mandate_title": "You are signing a mandate for your assistant:",
    "js.mandate_scope": "It may only create campaigns, always paused. Turning one on, "
                        "budget changes and replies still need your signature.",
    "js.mandate_objectives": "Goals: {list}",
    "js.mandate_countries": "Countries: {list}",
    "js.mandate_hosts": "Ads may only link to: {list}",
    "js.mandate_budget": "Daily budget per campaign: at most {amount} {currency}",
    "js.mandate_count": "At most {n} campaigns",
    "js.mandate_until": "Valid until {date}",
    "js.mandate_tokens": "For these assistant tokens: {list}",
    "js.mandate_signed": "Signed. The mandate is active; see it under Account, "
                         "Assistants & API.",
    # errors
    "err.mandate_not_allowed": "Mandates are off for this account here: on the live ad "
                               "account only full accounts may let an assistant create "
                               "campaigns.",
    "err.mandate_invalid": "The mandate's limits are not valid.",
    "err.mandate_unknown": "No such mandate on your account.",
    "err.mandate_not_yours": "Only the account the mandate is for can sign it.",
    "err.mandate_unsigned": "The mandate was not signed by its owner.",
    "err.mandate_expired": "The mandate has expired.",
    "err.mandate_revoked": "The mandate was revoked.",
    "err.mandate_tampered": "The stored mandate is not the one its owner signed.",
    "err.mandate_wrong_action": "A mandate can only create paused campaigns.",
    "err.mandate_unused": "No use of the mandate was recorded for this approval.",
    "err.mandate_limits": "The campaign is outside the mandate's limits.",
    "vault.title": "Saved PayPal",
    "vault.lead": "Save your PayPal once (you approve it in PayPal) so that an assistant "
                  "working under a mandate you sign can pay for campaigns without you. "
                  "Signed Studio keeps only PayPal's reference to it, never your PayPal "
                  "details. Removing it stops every charge at once.",
    "vault.save": "Save my PayPal",
    "vault.saved_line": "Saved PayPal {ref} (since {date} UTC)",
    "vault.delete": "Remove",
    "vault.delete_label": "Remove saved PayPal {ref}",
    "vault.status_saved": "Your PayPal is saved. You can now let a mandate pay from it.",
    "vault.status_deleted": "The saved PayPal is removed. No mandate can charge it any more.",
    "vault.status_cancelled": "Saving PayPal was cancelled. Nothing was saved.",
    "mandate.pay": "Paying for campaigns",
    "mandate.pay_hint": "Optional. Without it, every campaign still waits for you to pay "
                        "with PayPal and sign. With it, the assistant may pay from your "
                        "saved PayPal, only within these caps. The money is held, not taken: "
                        "it is taken only when you sign to turn a campaign on.",
    "mandate.pay_check": "Let the assistant pay from my saved PayPal",
    "mandate.vault": "Saved PayPal",
    "mandate.max_charge": "Most per campaign, fee included ({currency})",
    "mandate.funding_cap": "Most in each period ({currency})",
    "mandate.period_days": "Period (days, 1 to {days})",
    "mandate.kinds": "Campaign kinds it may pay for",
    "mandate.kind.link": "ads that link to a website",
    "mandate.kind.message": "ads that open a chat",
    "mandate.kind.lead_form": "ads with a lead form",
    "mandate.limits_pay": "Pays from the saved PayPal: up to {charge} {currency} per campaign, "
                          "{cap} {currency} every {days} days, for: {kinds}.",
    "mandate.field.vault": "saved PayPal",
    "mandate.field.funding": "payment limits",
    "mandate.field.max_charge_minor": "most per campaign",
    "mandate.field.funding_cap_minor": "most per period",
    "mandate.field.period_days": "period",
    "mandate.field.period_start": "period start",
    "mandate.field.kinds": "campaign kinds",
    "js.mandate_pay": "Pays from your saved PayPal: at most {charge} {currency} per campaign "
                      "and {cap} {currency} every {days} days, for these kinds: {kinds}. The "
                      "money is only held until you sign to turn a campaign on.",
    "js.mandate_no_pay": "It cannot pay: each campaign still waits for you to pay with "
                         "PayPal and sign.",
    "err.paypal_vault_failed": "PayPal did not answer as expected. Nothing was saved; try "
                               "again in a minute.",
    "err.paypal_vault_not_found": "No such saved PayPal on your account.",
    "err.paypal_vault_wrong_state": "This saved PayPal is no longer being set up.",
    "err.paypal_vault_not_approved_yet": "PayPal has not confirmed the approval yet.",
    "err.paypal_vault_conflict": "The saved PayPal changed meanwhile; reload the page.",
}

PL_MANDATES: dict[str, str] = {
    "mandate.title": "Pełnomocnictwa",
    "mandate.lead": "Pełnomocnictwo pozwala asystentowi tworzyć kampanie bez pytania Cię za "
                    "każdym razem, w granicach, które podpisujesz raz na tym urządzeniu. "
                    "Tak utworzone kampanie są zawsze wstrzymane: włączenie, zmiana budżetu "
                    "i odpowiedzi do ludzi nadal wymagają Twojego podpisu.",
    "mandate.objectives": "Cele, których może użyć",
    "mandate.countries": "Kraje (kody ISO, np. PL, DE)",
    "mandate.countries_hint": "Każdy kraj, na który celuje kampania, musi być na tej liście.",
    "mandate.hosts": "Strony, do których mogą prowadzić reklamy",
    "mandate.hosts_hint": "Same nazwy hostów, np. sklep.example.com. Każdy link w reklamie "
                          "musi prowadzić do jednej z nich.",
    "mandate.budget": "Najwyższy budżet dzienny ({currency})",
    "mandate.budget_hint": "Najwyżej {ceiling} {currency}, limit studia.",
    "mandate.max_campaigns": "Najwięcej kampanii do utworzenia",
    "mandate.days": "Ważne przez (dni, od 1 do {days})",
    "mandate.tokens": "Asystenci (tokeny), którzy mogą z niego korzystać",
    "mandate.no_tokens": "Najpierw utwórz token asystenta powyżej.",
    "mandate.prepare": "Przygotuj pełnomocnictwo",
    "mandate.prepare_hint": "Za chwilę zobaczysz jeszcze raz każdy limit i podpiszesz go na "
                            "ekranie podpisu w studiu. Przed podpisem nic nie działa.",
    "mandate.caption": "Twoje pełnomocnictwa",
    "mandate.col.limits": "Limity",
    "mandate.col.used": "Wykorzystano",
    "mandate.col.until": "Ważne do (UTC)",
    "mandate.col.status": "Stan",
    "mandate.col.action": "Działanie",
    "mandate.limits": "Cele: {objectives}. Kraje: {countries}. Linki do: {hosts}. "
                      "Do {budget} {currency} dziennie. Tokeny: {tokens}.",
    "mandate.used": "{used} z {max}",
    "mandate.status.active": "aktywne",
    "mandate.status.revoked": "odwołane",
    "mandate.status.expired": "wygasło",
    "mandate.status.used_up": "wykorzystane",
    "mandate.revoke_button": "Odwołaj",
    "mandate.revoke_label": "Odwołaj pełnomocnictwo {id}",
    "mandate.revoked": "Pełnomocnictwo odwołane. Przebiegi uruchomione od teraz znów czekają "
                       "na Twój podpis.",
    "mandate.none": "Nie ma jeszcze pełnomocnictw.",
    "mandate.problems": "Sprawdź te pola: {fields}.",
    "mandate.field.objectives": "cele",
    "mandate.field.countries": "kraje",
    "mandate.field.landing_hosts": "strony",
    "mandate.field.max_daily_budget": "budżet dzienny",
    "mandate.field.max_campaigns": "liczba kampanii",
    "mandate.field.valid_until": "dni ważności",
    "mandate.field.token_ids": "asystenci",
    "mandate.field.currency": "waluta",
    "mandate.field.account_id": "konto",
    "mandate.field.fields": "formularz",
    "mandate.field.scope": "zakres",
    "mandate.api_note": "W ramach pełnomocnictwa, które wymienia ten token, uruchomiony przez "
                        "Ciebie przebieg tworzy kampanię wstrzymaną, bez nowego podpisu. "
                        "Włączenie, budżety i odpowiedzi zawsze czekają na właściciela. Tylko "
                        "właściciel tworzy i odwołuje pełnomocnictwo, na /account.",
    "js.mandate_title": "Podpisujesz pełnomocnictwo dla swojego asystenta:",
    "js.mandate_scope": "Może tylko tworzyć kampanie, zawsze wstrzymane. Włączenie, zmiany "
                        "budżetu i odpowiedzi nadal wymagają Twojego podpisu.",
    "js.mandate_objectives": "Cele: {list}",
    "js.mandate_countries": "Kraje: {list}",
    "js.mandate_hosts": "Reklamy mogą prowadzić tylko do: {list}",
    "js.mandate_budget": "Budżet dzienny kampanii: najwyżej {amount} {currency}",
    "js.mandate_count": "Najwyżej {n} kampanii",
    "js.mandate_until": "Ważne do {date}",
    "js.mandate_tokens": "Dla tych tokenów asystenta: {list}",
    "js.mandate_signed": "Podpisano. Pełnomocnictwo działa; znajdziesz je w Koncie, "
                         "Asystenci i API.",
    "err.mandate_not_allowed": "Pełnomocnictwa są tu wyłączone dla tego konta: na "
                               "prawdziwym koncie reklamowym tylko pełne konta mogą pozwolić "
                               "asystentowi tworzyć kampanie.",
    "err.mandate_invalid": "Limity pełnomocnictwa są nieprawidłowe.",
    "err.mandate_unknown": "Na Twoim koncie nie ma takiego pełnomocnictwa.",
    "err.mandate_not_yours": "Pełnomocnictwo może podpisać tylko konto, którego dotyczy.",
    "err.mandate_unsigned": "Właściciel nie podpisał tego pełnomocnictwa.",
    "err.mandate_expired": "Pełnomocnictwo wygasło.",
    "err.mandate_revoked": "Pełnomocnictwo zostało odwołane.",
    "err.mandate_tampered": "Zapisane pełnomocnictwo nie jest tym, które podpisał "
                            "właściciel.",
    "err.mandate_wrong_action": "Pełnomocnictwo pozwala tylko tworzyć wstrzymane kampanie.",
    "err.mandate_unused": "Dla tej zgody nie zapisano użycia pełnomocnictwa.",
    "err.mandate_limits": "Kampania wykracza poza limity pełnomocnictwa.",
    "vault.title": "Zapisany PayPal",
    "vault.lead": "Zapisz swój PayPal raz (zatwierdzasz to w PayPal), aby asystent działający "
                  "w ramach podpisanego przez Ciebie pełnomocnictwa mógł płacić za kampanie "
                  "bez Ciebie. Signed Studio przechowuje tylko odnośnik PayPal, nigdy Twoich "
                  "danych z PayPal. Usunięcie go od razu wstrzymuje wszystkie obciążenia.",
    "vault.save": "Zapisz mój PayPal",
    "vault.saved_line": "Zapisany PayPal {ref} (od {date} UTC)",
    "vault.delete": "Usuń",
    "vault.delete_label": "Usuń zapisany PayPal {ref}",
    "vault.status_saved": "Twój PayPal jest zapisany. Pełnomocnictwo może teraz z niego płacić.",
    "vault.status_deleted": "Zapisany PayPal usunięty. Żadne pełnomocnictwo nie może go już "
                            "obciążyć.",
    "vault.status_cancelled": "Zapisywanie PayPal anulowane. Nic nie zapisano.",
    "mandate.pay": "Płacenie za kampanie",
    "mandate.pay_hint": "Opcjonalne. Bez tego każda kampania nadal czeka, aż zapłacisz "
                        "PayPal i podpiszesz. Z tym asystent może płacić z zapisanego PayPal, "
                        "tylko w tych limitach. Pieniądze są zablokowane, nie pobrane: "
                        "pobranie następuje dopiero, gdy podpiszesz włączenie kampanii.",
    "mandate.pay_check": "Pozwól asystentowi płacić z mojego zapisanego PayPal",
    "mandate.vault": "Zapisany PayPal",
    "mandate.max_charge": "Najwyżej za kampanię, z opłatą ({currency})",
    "mandate.funding_cap": "Najwyżej w każdym okresie ({currency})",
    "mandate.period_days": "Okres (dni, od 1 do {days})",
    "mandate.kinds": "Rodzaje kampanii, za które może płacić",
    "mandate.kind.link": "reklamy z linkiem do strony",
    "mandate.kind.message": "reklamy otwierające czat",
    "mandate.kind.lead_form": "reklamy z formularzem",
    "mandate.limits_pay": "Płaci z zapisanego PayPal: do {charge} {currency} za kampanię, "
                          "{cap} {currency} co {days} dni, za: {kinds}.",
    "mandate.field.vault": "zapisany PayPal",
    "mandate.field.funding": "limity płatności",
    "mandate.field.max_charge_minor": "najwyżej za kampanię",
    "mandate.field.funding_cap_minor": "najwyżej na okres",
    "mandate.field.period_days": "okres",
    "mandate.field.period_start": "początek okresu",
    "mandate.field.kinds": "rodzaje kampanii",
    "js.mandate_pay": "Płaci z zapisanego PayPal: najwyżej {charge} {currency} za kampanię "
                      "i {cap} {currency} co {days} dni, za te rodzaje: {kinds}. Pieniądze są "
                      "tylko zablokowane, dopóki nie podpiszesz włączenia kampanii.",
    "js.mandate_no_pay": "Nie może płacić: każda kampania nadal czeka, aż zapłacisz PayPal i "
                         "podpiszesz.",
    "err.paypal_vault_failed": "PayPal nie odpowiedział zgodnie z oczekiwaniem. Nic nie "
                               "zapisano; spróbuj za minutę.",
    "err.paypal_vault_not_found": "Na Twoim koncie nie ma takiego zapisanego PayPal.",
    "err.paypal_vault_wrong_state": "Ten zapisany PayPal nie jest już w trakcie zapisywania.",
    "err.paypal_vault_not_approved_yet": "PayPal jeszcze nie potwierdził zgody.",
    "err.paypal_vault_conflict": "Zapisany PayPal zmienił się w międzyczasie; odśwież stronę.",
}
