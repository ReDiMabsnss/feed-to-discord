import json
import time

import summarize


def _rss_bytes(prefix: str, n: int) -> bytes:
    """Baut einen minimalen, von feedparser lesbaren RSS-Feed mit n Eintraegen,
    alle mit demselben (aktuellen) Datum, fuer main()-Integrationstests."""
    now = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())
    items = "\n".join(
        f"<item><title>{prefix} Artikel {i}</title>"
        f"<link>https://example.com/{prefix}/{i}</link>"
        f"<guid>https://example.com/{prefix}/{i}</guid>"
        f"<description>Testinhalt {i}.</description>"
        f"<pubDate>{now}</pubDate></item>"
        for i in range(n)
    )
    return (
        f"<?xml version='1.0'?><rss version='2.0'>"
        f"<channel><title>{prefix}</title>{items}</channel></rss>"
    ).encode("utf-8")


class _FakeFeedResp:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


class _FakePostResp:
    status_code = 200

    def raise_for_status(self):
        pass


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


def test_main_bootstraps_new_feed_without_touching_existing_feed(tmp_path, monkeypatch):
    """Regression fuer die per-Feed (statt globale) Erstlauf-Erkennung: ein
    Feed mit bereits vorhandenem Stand muss seine Kandidaten wie gewohnt
    posten, waehrend ein im selben Lauf neu hinzugekommener Feed nur seinen
    Bestand markiert und nichts postet - genau das in feeds.yaml dokumentierte
    Szenario (Anthropic-Feed loeschen und spaeter wieder einfuegen)."""
    feeds_file = tmp_path / "feeds.yaml"
    state_file = tmp_path / "state" / "seen.json"
    url_a = "https://example.com/a/rss.xml"
    url_b = "https://example.com/b/rss.xml"
    feeds_file.write_text(
        "feeds:\n"
        "  - name: Feed A (bestehend)\n"
        f"    url: {url_a}\n"
        "    webhook: TEST_WEBHOOK_A\n"
        "  - name: Feed B (neu)\n"
        f"    url: {url_b}\n"
        "    webhook: TEST_WEBHOOK_B\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(summarize, "FEEDS_FILE", feeds_file)
    monkeypatch.setattr(summarize, "STATE_FILE", state_file)
    monkeypatch.setattr(summarize, "ARCHIVE_DIR", tmp_path / "archive")
    monkeypatch.setenv("TEST_WEBHOOK_A", "https://discord.com/api/webhooks/1/fake-a")
    monkeypatch.setenv("TEST_WEBHOOK_B", "https://discord.com/api/webhooks/1/fake-b")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(summarize, "API_KEY", "")

    # Feed A hat schon einmal gelaufen (Stand vorhanden, aber leer -> alle
    # Eintraege gelten als frisch). Feed B taucht zum ersten Mal auf.
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({url_a: []}), encoding="utf-8")

    def fake_get(url, timeout=None, headers=None):
        return _FakeFeedResp(_rss_bytes("a" if url == url_a else "b", 2))

    post_calls = []

    def fake_post(url, json=None, timeout=None):
        post_calls.append(url)
        return _FakePostResp()

    monkeypatch.setattr(summarize.requests, "get", fake_get)
    monkeypatch.setattr(summarize.requests, "post", fake_post)
    monkeypatch.setattr(summarize.time, "sleep", lambda _: None)

    rc = summarize.main()

    assert rc == 0
    assert post_calls.count("https://discord.com/api/webhooks/1/fake-a") == 2, \
        "Feed A's 2 fresh entries should have been posted"
    assert post_calls.count("https://discord.com/api/webhooks/1/fake-b") == 0, \
        "Feed B is new and must only be baselined, not posted"

    final_state = json.loads(state_file.read_text(encoding="utf-8"))
    assert set(final_state[url_a]) == {"https://example.com/a/0", "https://example.com/a/1"}
    assert set(final_state[url_b]) == {"https://example.com/b/0", "https://example.com/b/1"}


def test_main_gives_up_on_entry_after_max_post_retries(tmp_path, monkeypatch):
    """Regression fuer die Aufgeben-Logik: ein Eintrag, der jeden Lauf am
    Posten scheitert, bleibt bis MAX_POST_RETRIES retrybar und wird danach
    als gesehen markiert statt fuer immer erneut versucht zu werden."""
    feeds_file = tmp_path / "feeds.yaml"
    state_file = tmp_path / "state" / "seen.json"
    url = "https://example.com/only/rss.xml"
    entry_url = "https://example.com/only/0"
    feeds_file.write_text(
        "feeds:\n"
        "  - name: Only Feed\n"
        f"    url: {url}\n"
        "    webhook: TEST_WEBHOOK\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(summarize, "FEEDS_FILE", feeds_file)
    monkeypatch.setattr(summarize, "STATE_FILE", state_file)
    monkeypatch.setenv("TEST_WEBHOOK", "https://discord.com/api/webhooks/1/fake")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(summarize, "API_KEY", "")

    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({url: []}), encoding="utf-8")

    monkeypatch.setattr(summarize.requests, "get", lambda *a, **k: _FakeFeedResp(_rss_bytes("only", 1)))

    def always_failing_post(post_url, json=None, timeout=None):
        raise ConnectionError("simulated failure")

    monkeypatch.setattr(summarize.requests, "post", always_failing_post)
    monkeypatch.setattr(summarize.time, "sleep", lambda _: None)

    fkey = summarize._failure_key(url, entry_url)

    for i in range(1, summarize.MAX_POST_RETRIES):
        summarize.main()
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert entry_url not in state[url], f"run {i}: should still be retried, not given up on yet"
        assert state["_failures"][fkey] == i

    # Letzter Lauf: MAX_POST_RETRIES erreicht -> aufgeben.
    summarize.main()
    final_state = json.loads(state_file.read_text(encoding="utf-8"))
    assert entry_url in final_state[url], "should be given up on and marked seen"
    assert fkey not in final_state["_failures"], "failure counter should be cleared once given up"
