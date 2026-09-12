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
