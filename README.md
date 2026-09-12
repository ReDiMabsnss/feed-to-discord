# feed-to-discord
DE: Holt RSS-Feeds zu KI, SAP AI und Gaming, fasst sie per Gemini zusammen und postet sie nach Discord. Läuft serverlos als GitHub Action. EN: Fetches AI, SAP AI, and gaming news feeds, summarizes them with Gemini, and posts them to Discord. Runs serverless as a GitHub Action.

## Archiv

Jede gepostete Meldung wird zusätzlich in eine Monatsdatei unter `archive/`
geschrieben (`archive/2026-09.md`), mit Titel, Quelle, Datum, Link und
Zusammenfassung. Gruppiert wird nach dem Erscheinungsdatum des Artikels.
Kategorien mit eigener `archive`-Angabe in `feeds.yaml` (aktuell: Gaming)
bekommen eine eigene Unterordner-Sammlung, z. B. `archive/gaming/2026-09.md`,
statt in der gemeinsamen KI/SAP/Security-Sammlung zu landen.

Damit entsteht pro Sammlung eine durchsuchbare Wissenssammlung im Repo. Eine
Monatsdatei lässt sich in NotebookLM als **eine** Quelle einlesen — bei 50
Quellen im Gratis-Tarif reicht das für gut vier Jahre. Die Datei muss dort
gelegentlich neu eingelesen werden, da NotebookLM-Quellen Momentaufnahmen
sind.
