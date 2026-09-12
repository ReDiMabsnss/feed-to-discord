---
slug: gaming-news
companions: []
sources: []
---

# SPEC: Gaming-News-Integration

## Why

Der Betreiber will Gaming-News automatisiert in einen zweiten, separaten
Discord-Server posten - mit derselben Automatisierung (Feed lesen, KI-
Zusammenfassung, Posten, Deduplizieren, Archivieren), die der bestehende
Bot bereits fuer AI/SAP/Security-News im ersten Discord leistet - statt
Gaming-News manuell zu verfolgen.

## Capabilities

- **CAP-1**: Gaming-Feeds lesen und zusammenfassen.
  Intent: Neue Eintraege aus kuratierten, breiten Gaming-News-Feeds (Releases,
  Updates, trending Games - bewusst ohne Esports-Quellen) erkennen und wie bei
  den bestehenden Kategorien per Gemini auf Deutsch zusammenfassen.
  Success: Neue Feed-Eintraege werden erkannt, zusammengefasst (oder bei
  Fehlschlag unzusammengefasst mit Titel+Link gepostet) und dedupliziert im
  bestehenden `state/seen.json`-Mechanismus gemerkt.

- **CAP-2**: In den zweiten Discord posten.
  Intent: Alle Gaming-Meldungen landen ueber einen eigenen Webhook
  (`DISCORD_WEBHOOK_GAMING`) im Kanal "AI-Gaming News" auf dem zweiten
  Discord-Server.
  Success: Neue Gaming-Meldungen erscheinen im Kanal "AI-Gaming News", nie im
  bestehenden AI/SAP/Security-Discord.

- **CAP-3**: Ueber feeds.yaml integrieren, ohne strukturelle Codeaenderung.
  Intent: Gaming-Feeds werden als neue Eintraege unter dem bestehenden
  `feeds:`-Schema in `feeds.yaml` ergaenzt (Quellen: IGN, PC Gamer, Eurogamer,
  GameSpot - siehe `feeds.yaml` fuer die aktuelle, gepflegte Liste); die
  Feed-Verarbeitung selbst braucht keine Codeaenderung.
  Success: Die neuen Feeds laufen im selben geplanten Workflow-Lauf mit wie
  die bestehenden Kategorien, ohne zweites Skript oder zweiten Workflow.

- **CAP-4**: Eigene Wissenssammlung fuer Gaming.
  Intent: Gaming-Meldungen landen in einem eigenen Archiv
  (`archive/gaming/<monat>.md`, Ueberschrift "AI-Gaming News-Archiv"), nicht
  in der gemeinsamen KI/SAP/Security-Sammlung - andere Domaene, andere
  NotebookLM-Quelle.
  Success: `archive/gaming/<monat>.md` enthaelt nur Gaming-Eintraege; das
  bestehende, gemeinsame `archive/<monat>.md` bleibt unveraendert und erhaelt
  keine Gaming-Eintraege mehr.

## Constraints

- Feed-Auswahl und Routing laufen ueber das bestehende `feeds.yaml`-Schema
  (name / url / webhook / max_per_run / max_age_days) - keine Codeaenderung
  fuer die Feed-Verarbeitung selbst noetig.
  Kein Keyword-Filter fuer Gaming - breite Abdeckung gewuenscht, anders als
  beim Security-Kanal; Esports-Ausschluss laeuft ueber Quellenauswahl, nicht
  ueber einen Filter.
- Separates Archiv erfordert eine kleine, abwaertskompatible Erweiterung von
  `archive_entry()`: zwei neue optionale Parameter (`archive_dir`,
  `archive_title`), aus den neuen `feeds.yaml`-Feldern `archive` /
  `archive_title` gespeist. Bestehende Feeds ohne diese Felder sind
  unveraendert (gemeinsames Archiv, Ueberschrift "KI-News").
- Laeuft im selben GitHub-Actions-Workflow (`news.yml`) auf demselben
  Zeitplan wie die bestehenden Kategorien - kein separater Workflow. Der
  Workflow muss `DISCORD_WEBHOOK_GAMING` zusaetzlich als Env-Var an
  `summarize.py` durchreichen.
- `DISCORD_WEBHOOK_GAMING` muss als GitHub-Secret hinterlegt sein, bevor ein
  Gaming-Feed live gepostet werden kann; ohne Secret wird der Feed wie bei
  den bestehenden Kategorien uebersprungen, nicht gepostet (bestehendes
  Verhalten von `summarize.py`).
- Zusammenfassungen bleiben Deutsch, konsistent mit den bestehenden
  Kategorien - kein Umschalten der Sprache pro Kategorie.

## Non-goals

- Keine Aufteilung in mehrere Gaming-Kanaele (Plattform/Genre) in dieser
  ersten Version - bewusst ein einzelner Kanal; eine Aufteilung ist eine
  spaetere, separate Erweiterung.
- Keine Esports-Quellen/-Inhalte - bewusster Ausschluss.
- Keine Aenderung an der bestehenden AI/SAP/Security-Struktur, deren
  Webhooks oder deren gemeinsamem Archiv.
- Kein zweiter/neuer GitHub-Actions-Workflow.

## Success Signal

Nach Merge und gesetztem `DISCORD_WEBHOOK_GAMING`-Secret erscheinen bei
einem geplanten oder manuell ausgeloesten Workflow-Lauf neue Meldungen aus
den konfigurierten Gaming-Feeds im Kanal "AI-Gaming News", mit deutscher
KI-Zusammenfassung (oder Titel+Link als Rueckfall bei fehlender
Zusammenfassung), werden im bestehenden `state/seen.json` dedupliziert
gemerkt und landen in `archive/gaming/<monat>.md` statt im gemeinsamen
Archiv.
