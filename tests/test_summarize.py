import json
import time

import summarize


def test_matches_keywords_respects_word_boundaries():
    """Regression fuer fa19293: Substring-Treffer wie 'KI' in 'Tracking'
    duerfen nicht mehr durchrutschen, ein angehaengtes 's' bleibt erlaubt."""
    entry_tracking = {"title": "Ad Tracking im Browser", "summary": "Details zum Tracking."}
    entry_llms = {"title": "Neue LLMs im Vergleich", "summary": ""}

    assert summarize.matches_keywords(entry_tracking, ["KI"]) is False
    assert summarize.matches_keywords(entry_llms, ["LLM"]) is True


def test_post_to_discord_never_logs_webhook_secret(monkeypatch, capsys):
    """Regression fuer 08a4578: Ein fehlgeschlagener Post darf die
    Webhook-URL nicht ins (oeffentliche) Action-Log schreiben."""
    webhook_url = "https://discord.com/api/webhooks/123/topsecret-token"

    def fake_post(url, json=None, timeout=None):
        raise ConnectionError(f"could not reach {url}")

    monkeypatch.setattr(summarize.requests, "post", fake_post)

    ok = summarize.post_to_discord(webhook_url, "Quelle", "Titel", "https://example.com", "Zusammenfassung")

    assert ok is False
    captured = capsys.readouterr()
    assert webhook_url not in captured.err
    assert "topsecret-token" not in captured.err


def test_merge_seen_no_loss_and_dedup_across_runs():
    """Regression fuer 7e492f9: Ueber dem Limit zurueckgestellte Eintraege
    duerfen nicht verloren gehen, und der Stand darf keine Duplikate ansammeln."""
    keep = 800
    all_ids = ["a", "b", "c", "d", "e"]

    # Lauf 1: Limit 2 -> "a", "b" gepostet, "c"-"e" zurueckgestellt.
    state = summarize.merge_seen(all_ids, set(), {"c", "d", "e"}, keep)
    assert state == ["a", "b"]

    # Lauf 2: "c", "d" folgen, "e" bleibt weiter zurueckgestellt.
    state = summarize.merge_seen(all_ids, set(state), {"e"}, keep)
    assert set(state) == {"a", "b", "c", "d"}
    assert len(state) == len(set(state))

    # Lauf 3: "e" folgt zuletzt - nichts ist unterwegs verloren gegangen.
    state = summarize.merge_seen(all_ids, set(state), set(), keep)
    assert set(state) == {"a", "b", "c", "d", "e"}
    assert len(state) == len(set(state))


def test_entry_id_disambiguates_same_title_by_date():
    """Zwei Eintraege ohne id/link (z. B. wiederkehrende 'Weekly Roundup'-
    Posts) duerfen nicht auf dieselbe ID kollidieren, wenn sie an
    verschiedenen Tagen erschienen sind."""
    day1 = {"title": "Weekly Roundup", "published_parsed": time.gmtime(1_700_000_000)}
    day2 = {"title": "Weekly Roundup", "published_parsed": time.gmtime(1_700_000_000 + 86400 * 30)}

    assert summarize.entry_id(day1) != summarize.entry_id(day2)


def test_is_recent_rejects_far_future_dates():
    """Nur eine obere Altersgrenze zu pruefen liesse ein falsch weit in der
    Zukunft liegendes Datum immer als aktuell durchgehen."""
    far_future = {"published_parsed": time.gmtime(time.time() + 86400 * 365)}

    assert summarize.is_recent(far_future, max_age_days=14) is False


def test_matches_keywords_none_vs_explicit_empty_list():
    """keywords: [] (bewusst gesetzt) muss 'nichts durchlassen' heissen,
    ein fehlender keywords-Schluessel (None) weiterhin 'alles durchlassen'."""
    entry = {"title": "Irgendein Artikel", "summary": ""}

    assert summarize.matches_keywords(entry, None) is True
    assert summarize.matches_keywords(entry, []) is False


def test_load_state_resets_on_malformed_shape(tmp_path, monkeypatch):
    """Eine strukturell falsche (aber valide JSON-) seen.json darf nicht
    still falsch verwendet werden, sondern soll als leerer Stand gelten."""
    state_file = tmp_path / "seen.json"
    state_file.write_text('{"https://example.com/feed": "not-a-list"}', encoding="utf-8")
    monkeypatch.setattr(summarize, "STATE_FILE", state_file)

    assert summarize.load_state() == {}


def test_archive_entry_escapes_injected_markdown_structure(tmp_path, monkeypatch):
    """Titel/Zusammenfassung aus fremden Feeds duerfen keine eigenen
    Ueberschriften/Listeneintraege ins Archiv-Record-Format einschleusen."""
    monkeypatch.setattr(summarize, "ARCHIVE_DIR", tmp_path)

    entry = {"published_parsed": time.gmtime(1_700_000_000)}
    summarize.archive_entry(
        entry,
        "Quelle",
        "Harmloser Titel",
        "https://example.com",
        "Zusammenfassung\n\n## Gefaelschte Ueberschrift\n\n- Quelle: Fake",
    )

    month = time.strftime("%Y-%m", time.gmtime(1_700_000_000))
    written = (tmp_path / f"{month}.md").read_text(encoding="utf-8")

    assert "\n## Gefaelschte Ueberschrift" not in written
    assert "\n- Quelle: Fake" not in written


def test_fetch_article_text_caps_response_size(monkeypatch):
    """Eine riesige oder endlose Artikelseite darf den Lauf nicht haengen
    lassen oder unbegrenzt Speicher belegen - die Leseschleife muss frueh
    abbrechen statt die komplette Antwort zu konsumieren."""
    consumed = {"bytes": 0}
    chunk = b"<p>" + b"x" * 8192 + b"</p>"

    class FakeResp:
        encoding = "utf-8"

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=8192, decode_unicode=False):
            for _ in range(1000):  # weit mehr als der 500_000-Byte-Deckel erlaubt
                consumed["bytes"] += len(chunk)
                yield chunk

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(summarize.requests, "get", lambda *a, **k: FakeResp())

    text = summarize.fetch_article_text("https://example.com/article", limit=4000)

    assert len(text) <= 4000
    assert consumed["bytes"] < 1000 * len(chunk)


def test_main_survives_feed_missing_required_keys(tmp_path, monkeypatch):
    """Ein Feed ohne 'url' (z. B. ein kaputter Edit in feeds.yaml) darf
    weder den per-Feed-Schleifenkoerper noch die Altlasten-Aufraeumung nach
    der Schleife crashen - beides indiziert feed_cfg['url'] direkt."""
    feeds_file = tmp_path / "feeds.yaml"
    state_file = tmp_path / "state" / "seen.json"
    feeds_file.write_text(
        "feeds:\n"
        "  - name: Kaputter Feed\n"
        "    webhook: TEST_WEBHOOK\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(summarize, "FEEDS_FILE", feeds_file)
    monkeypatch.setattr(summarize, "STATE_FILE", state_file)
    monkeypatch.setenv("TEST_WEBHOOK", "https://discord.com/api/webhooks/1/fake")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(summarize, "API_KEY", "")

    rc = summarize.main()

    assert rc == 0
    assert json.loads(state_file.read_text(encoding="utf-8")) == {"_failures": {}}
