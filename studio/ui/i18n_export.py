"""EN/PL strings for exports and imports: the leads export/import panel on ``/leads`` and
the monthly statement on ``/report`` (``studio.exports``, ``studio.routes_export``)."""

from __future__ import annotations

EN_EXPORT: dict[str, str] = {
    "xport.leads_title": "Export and import",
    "xport.leads_hint": "Take your leads to a spreadsheet, a CRM or your phone's contacts, "
                        "or bring in the ones you already have.",
    "xport.leads_masked": "This account sees leads without names and contacts, so the "
                          "files are masked the same way: initials, [email] and [phone].",
    "xport.download": "Download:",
    "xport.import_title": "Import leads",
    "xport.import_hint": "A CSV with a header row (name, e-mail, phone, source, message), "
                         "a JSON list or a vCard file; up to 2 MB and 5,000 rows. Leads "
                         "already here (same e-mail or phone) are skipped. Nothing is "
                         "sent to Meta.",
    "xport.file": "File",
    "xport.format": "Format",
    "xport.fmt.csv": "CSV (spreadsheet)",
    "xport.fmt.json": "JSON",
    "xport.fmt.vcf": "vCard (contacts)",
    "xport.import": "Import",
    "xport.imported": "Imported: {added} added, {skipped} skipped as duplicates, "
                      "{rejected} rejected.",
    "xport.line": "Line {line}:",
    "xport.err.invalid_email": "the e-mail address is not valid.",
    "xport.err.invalid_phone": "the phone number is not valid (7 to 15 digits).",
    "xport.err.no_contact": "neither an e-mail nor a phone number.",
    "xport.err.unreadable": "The file could not be read in this format.",
    "xport.err.not_utf8": "The file is not UTF-8 text. Save it as CSV UTF-8 and try again.",
    "xport.err.no_contact_column": "The header row has no e-mail or phone column.",
    "xport.err.empty": "The file is empty.",
    "xport.err.too_large": "The file is larger than 2 MB.",
    "xport.err.too_many_rows": "The file has more than 5,000 rows.",
    "xport.err.unknown_format": "Choose CSV, JSON or vCard.",
    "xport.err.import_not_allowed": "This account cannot import leads.",
    "xport.st_title": "Monthly statement",
    "xport.st_hint": "Every ad expense of the month, day by day, next to the signature "
                     "that allowed it, plus what the models cost. It reconciles with "
                     "Meta's invoice line by line.",
    "xport.month": "Month",
    "xport.show": "Show",
    "xport.st_caption": "Statement for {month}",
    "xport.col.date": "Date",
    "xport.col.campaign": "Campaign",
    "xport.col.spend": "Spend",
    "xport.col.budget": "Approved daily budget",
    "xport.col.approval": "Approval · key · receipt",
    "xport.col.model": "Model cost",
    "xport.models_only": "Model calls only, run",
    "xport.total": "Total",
    "xport.more": "{n} more rows are in the downloads.",
    "xport.st_empty": "Nothing was spent in {month}.",
    "xport.profile.generic": "CSV",
    "xport.profile.xero": "CSV for Xero",
    "xport.profile.quickbooks": "CSV for QuickBooks",
    "xport.profile.wfirma": "CSV for wFirma",
    "xport.traces_html": "Every row traces to a signature: <code>studio verify</code> "
                         "checks the receipts.",
    # API errors of the downloads (studio.routes_export)
    "err.invalid_format": "This format is not offered here.",
    "err.invalid_month": "The month must be written as YYYY-MM.",
    "err.unknown_profile": "Choose a CSV profile: generic, xero, quickbooks or wfirma.",
}

PL_EXPORT: dict[str, str] = {
    "xport.leads_title": "Eksport i import",
    "xport.leads_hint": "Przenieś leady do arkusza, CRM-u albo kontaktów w telefonie albo "
                        "wczytaj te, które już masz.",
    "xport.leads_masked": "To konto widzi leady bez imion i kontaktów, więc pliki są "
                          "zamaskowane tak samo: inicjały, [email] i [phone].",
    "xport.download": "Pobierz:",
    "xport.import_title": "Import leadów",
    "xport.import_hint": "CSV z wierszem nagłówka (imię i nazwisko, e-mail, telefon, "
                         "źródło, wiadomość), lista JSON albo plik vCard; do 2 MB i 5000 "
                         "wierszy. Leady, które już tu są (ten sam e-mail lub telefon), "
                         "zostaną pominięte. Nic nie trafia do Mety.",
    "xport.file": "Plik",
    "xport.format": "Format",
    "xport.fmt.csv": "CSV (arkusz)",
    "xport.fmt.json": "JSON",
    "xport.fmt.vcf": "vCard (kontakty)",
    "xport.import": "Importuj",
    "xport.imported": "Zaimportowano: dodane {added}, pominięte duplikaty {skipped}, "
                      "odrzucone {rejected}.",
    "xport.line": "Wiersz {line}:",
    "xport.err.invalid_email": "adres e-mail jest niepoprawny.",
    "xport.err.invalid_phone": "numer telefonu jest niepoprawny (od 7 do 15 cyfr).",
    "xport.err.no_contact": "brak zarówno e-maila, jak i telefonu.",
    "xport.err.unreadable": "Nie udało się odczytać pliku w tym formacie.",
    "xport.err.not_utf8": "Plik nie jest tekstem UTF-8. Zapisz go jako CSV UTF-8 i spróbuj "
                          "ponownie.",
    "xport.err.no_contact_column": "Wiersz nagłówka nie ma kolumny e-mail ani telefon.",
    "xport.err.empty": "Plik jest pusty.",
    "xport.err.too_large": "Plik jest większy niż 2 MB.",
    "xport.err.too_many_rows": "Plik ma więcej niż 5000 wierszy.",
    "xport.err.unknown_format": "Wybierz CSV, JSON albo vCard.",
    "xport.err.import_not_allowed": "To konto nie może importować leadów.",
    "xport.st_title": "Zestawienie miesięczne",
    "xport.st_hint": "Każdy wydatek na reklamy w miesiącu, dzień po dniu, obok podpisu, "
                     "który go dopuścił, plus koszt modeli. Zgadza się z fakturą Mety "
                     "pozycja po pozycji.",
    "xport.month": "Miesiąc",
    "xport.show": "Pokaż",
    "xport.st_caption": "Zestawienie za {month}",
    "xport.col.date": "Data",
    "xport.col.campaign": "Kampania",
    "xport.col.spend": "Wydatek",
    "xport.col.budget": "Zatwierdzony budżet dzienny",
    "xport.col.approval": "Zatwierdzenie · klucz · paragon",
    "xport.col.model": "Koszt modeli",
    "xport.models_only": "Tylko wywołania modeli, przebieg",
    "xport.total": "Razem",
    "xport.more": "Pozostałe wiersze ({n}) są w plikach do pobrania.",
    "xport.st_empty": "W {month} nic nie wydano.",
    "xport.profile.generic": "CSV",
    "xport.profile.xero": "CSV dla Xero",
    "xport.profile.quickbooks": "CSV dla QuickBooks",
    "xport.profile.wfirma": "CSV dla wFirmy",
    "xport.traces_html": "Każdy wiersz prowadzi do podpisu: <code>studio verify</code> "
                         "sprawdza paragony.",
    "err.invalid_format": "Ten format nie jest tu dostępny.",
    "err.invalid_month": "Miesiąc trzeba zapisać jako RRRR-MM.",
    "err.unknown_profile": "Wybierz profil CSV: generic, xero, quickbooks albo wfirma.",
}
