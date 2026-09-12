#!/usr/bin/env python3
"""Liest RSS-Feeds, fasst neue Eintraege per Gemini zusammen und postet sie nach Discord.

Faellt die Zusammenfassung aus (Quota, Fehler, kein Key), wird trotzdem
Titel + Link gepostet. Nachrichten gehen nie verloren, nur die Zusammenfassung.
"""

import html
import json
import os
import pathlib
import re
import sys
import time

import feedparser
import requests
import yaml

ROOT = pathlib.Path(__file__).parent
STATE_FILE = ROOT / "state" / "seen.json"
FEEDS_FILE = ROOT / "feeds.yaml"
ARCHIVE_DIR = ROOT / "archive"

# Modellnamen aendern sich. Aktuellen Namen in Google AI Studio pruefen und
# hier oder als Repository-Variable GEMINI_MODEL setzen.
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
DEFAULT_MAX_PER_RUN = int(os.getenv("MAX_PER_RUN", "4"))
KEEP_IDS_PER_FEED = 2000          # muss groesser sein als der laengste Feed
MAX_AGE_DAYS = int(os.getenv("MAX_AGE_DAYS", "14"))

FOOTER = "KI-generierte Zusammenfassung, nicht redaktionell geprueft"

PROMPT = """Fasse die folgende Meldung in hoechstens drei kurzen deutschen Saetzen zusammen.

Regeln:
- Uebernimm keine woertlichen Passagen aus dem Text.
- Keine Einleitung, keine Floskeln, kein "Der Artikel beschreibt".
- Wenn der Text zu duenn fuer eine Zusammenfassung ist, antworte exakt: KEINE

Titel: {title}

Text: {body}"""


def strip_html(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("WARN: seen.json unlesbar, starte mit leerem Stand", file=sys.stderr)
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )


def entry_body(entry) -> str:
    """Holt den Textkoerper. Feeds nutzen summary, description oder content."""
    for key in ("summary", "description"):
        if entry.get(key):
            return strip_html(entry[key])
    content = entry.get("content")
    if content:
        return strip_html(content[0].get("value", ""))
    return ""


def is_recent(entry, max_age_days: int) -> bool:
    """True, wenn der Eintrag jung genug ist. Ohne Datum: True (nicht verwerfen)."""
    stamp = entry.get("published_parsed") or entry.get("updated_parsed")
    if not stamp:
        return True
    age_days = (time.time() - time.mktime(stamp)) / 86400
    return age_days <= max_age_days


def fetch_article_text(url: str, limit: int = 4000) -> str:
    """Laedt die Artikelseite und zieht groben Text heraus. Nur als Rueckfall,
    wenn der Feed keinen Textkoerper mitliefert."""
    try:
        resp = requests.get(
            url,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (compatible; feed-to-discord/1.0)"},
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"  Artikel nicht abrufbar: {exc}", file=sys.stderr)
        return ""

    body = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", resp.text)
    return strip_html(body)[:limit]


def entry_id(entry) -> str:
    return entry.get("id") or entry.get("link") or entry.get("title", "")


def matches_keywords(entry, keywords) -> bool:
    """True, wenn eines der Stichworte als eigenstaendiges Wort vorkommt.

    Auf Wortgrenzen pruefen, nicht auf Teilstrings: sonst trifft "KI" auch
    Tracking, Hacking und Marketing, und "AI" auch email und domain. Ein
    angehaengtes s wird geduldet, damit LLMs auf LLM passt.
    """
    if not keywords:
        return True
    haystack = (entry.get("title", "") + " " + entry_body(entry)).lower()
    return any(
        re.search(rf"(?<![a-z0-9]){re.escape(k.lower())}s?(?![a-z0-9])", haystack)
        for k in keywords
    )


def summarize(title: str, body: str) -> str | None:
    """Gibt die Zusammenfassung zurueck oder None, wenn keine erzeugt werden konnte."""
    if not API_KEY:
        return None
    if not body:
        print("  kein Text im Feed, Zusammenfassung uebersprungen", file=sys.stderr)
        return None

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": PROMPT.format(title=title, body=body[:4000])}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 800,
            # Thinking-Tokens zaehlen gegen maxOutputTokens. Ohne diese Zeile
            # wird die Antwort abgeschnitten, bevor sie ueberhaupt beginnt.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }

    for attempt in range(3):
        try:
            resp = requests.post(
                url,
                headers={"x-goog-api-key": API_KEY, "Content-Type": "application/json"},
                json=payload,
                timeout=60,
            )
            if resp.status_code == 429:
                wait = 20 * (attempt + 1)
                print(f"  Rate Limit, warte {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                print(f"  Gemini HTTP {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
                resp.raise_for_status()
            data = resp.json()
            cand = (data.get("candidates") or [{}])[0]
            parts = cand.get("content", {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts).strip()
            if not text:
                print(f"  leere Antwort, finishReason={cand.get('finishReason')}",
                      file=sys.stderr)
                return None
            return None if text.upper().startswith("KEINE") else text
        except Exception as exc:  # noqa: BLE001 - bewusst breit, Bot darf nie sterben
            print(f"  Zusammenfassung fehlgeschlagen: {exc}", file=sys.stderr)
            time.sleep(5)
    return None


def post_to_discord(webhook_url: str, source: str, title: str, link: str, summary: str | None) -> bool:
    embed = {
        "title": title[:250],
        "url": link,
        "author": {"name": source},
        "color": 0x5865F2 if summary else 0x99AAB5,
    }
    if summary:
        embed["description"] = summary[:1500]
        embed["footer"] = {"text": FOOTER}
    else:
        embed["footer"] = {"text": "ohne Zusammenfassung"}

    try:
        resp = requests.post(webhook_url, json={"embeds": [embed]}, timeout=30)
        if resp.status_code == 429:
            wait = resp.json().get("retry_after", 5)
            time.sleep(float(wait) + 1)
            resp = requests.post(webhook_url, json={"embeds": [embed]}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  Discord-Post fehlgeschlagen: {exc}", file=sys.stderr)
        return False


def archive_entry(entry, source: str, title: str, link: str, summary: str | None) -> None:
    """Haengt die Meldung an die Monatsdatei unter archive/ an.

    Baut eine Wissenssammlung, die sich als einzelne Quelle in NotebookLM
    einlesen laesst. Ein Fehler hier darf den Lauf nicht abbrechen - die
    Meldung ist zu diesem Zeitpunkt schon bei Discord.
    """
    stamp = entry.get("published_parsed") or entry.get("updated_parsed")
    day = time.strftime("%Y-%m-%d", stamp or time.gmtime())
    month = day[:7]

    try:
        ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        path = ARCHIVE_DIR / f"{month}.md"
        header = f"# KI-News-Archiv {month}\n" if not path.exists() else ""
        with path.open("a", encoding="utf-8") as fh:
            fh.write(
                f"{header}\n## {title}\n\n"
                f"- Quelle: {source}\n"
                f"- Datum: {day}\n"
                f"- Link: {link}\n\n"
                f"{summary if summary else '_Keine Zusammenfassung erzeugt._'}\n"
            )
    except OSError as exc:
        print(f"  Archiv-Eintrag fehlgeschlagen: {exc}", file=sys.stderr)


def main() -> int:
    print(f"Modell: {MODEL}")
    if API_KEY:
        print(f"GEMINI_API_KEY gesetzt ({len(API_KEY)} Zeichen)")
    else:
        print("WARNUNG: GEMINI_API_KEY fehlt - es wird ohne Zusammenfassung gepostet",
              file=sys.stderr)

    config = yaml.safe_load(FEEDS_FILE.read_text(encoding="utf-8"))
    state = load_state()
    first_run = not state
    posted = 0

    for feed_cfg in config["feeds"]:
        name = feed_cfg["name"]
        url = feed_cfg["url"]
        webhook = os.getenv(feed_cfg["webhook"], "").strip()
        limit = int(feed_cfg.get("max_per_run", DEFAULT_MAX_PER_RUN))

        if not webhook:
            print(f"{name}: Secret {feed_cfg['webhook']} fehlt, uebersprungen")
            continue

        print(f"{name}: lese {url}")
        parsed = feedparser.parse(url)
        if parsed.bozo and not parsed.entries:
            print(f"  Feed nicht lesbar: {parsed.get('bozo_exception')}", file=sys.stderr)
            continue

        seen = set(state.get(url, []))
        max_age = int(feed_cfg.get("max_age_days", MAX_AGE_DAYS))
        fresh = [
            e for e in parsed.entries
            if entry_id(e) not in seen and is_recent(e, max_age)
        ]
        print(f"  {len(parsed.entries)} Eintraege im Feed")

        if first_run:
            # Erster Lauf: nur Stand merken, nicht 200 Altmeldungen posten.
            state[url] = [entry_id(e) for e in parsed.entries][:KEEP_IDS_PER_FEED]
            print(f"  Erstlauf, {len(parsed.entries)} Eintraege als gesehen markiert")
            continue

        candidates = [e for e in fresh if matches_keywords(e, feed_cfg.get("keywords"))][:limit]
        print(f"  {len(fresh)} neu und aktuell, {len(candidates)} werden gepostet")

        failed = set()
        for entry in candidates:
            title = strip_html(entry.get("title", "ohne Titel"))
            link = entry.get("link", "")
            body = entry_body(entry)
            # Feed liefert nur den Titel (oder nichts): Artikelseite nachladen.
            if feed_cfg.get("fetch_full") and len(body) < 200:
                body = fetch_article_text(link)
            summary = summarize(title, body)
            if post_to_discord(webhook, name, title, link, summary):
                posted += 1
                archive_entry(entry, name, title, link, summary)
            else:
                # Nicht als gesehen markieren, damit der naechste Lauf es erneut versucht.
                failed.add(entry_id(entry))
            time.sleep(2)

        # Alle frischen IDs merken, auch die per Keyword gefilterten - sonst tauchen
        # sie beim naechsten Lauf wieder als neu auf. Ausser den fehlgeschlagenen.
        merged = [entry_id(e) for e in parsed.entries if entry_id(e) not in failed]
        state[url] = (merged + list(seen))[:KEEP_IDS_PER_FEED]

    save_state(state)
    print(f"Fertig. {posted} Meldungen gepostet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
