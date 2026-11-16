import database as db


def add_game(appid, name, samples=()):
    db.upsert_game(appid, name, f"thumb-{appid}.jpg", f"header-{appid}.jpg")
    for players, timestamp in samples:
        db.insert_player_count(appid, players, timestamp)


def test_index_serves_the_dashboard(client):
    resp = client.get("/")

    assert resp.status_code == 200
    assert b"SteamOgames" in resp.data


def test_top_is_empty_before_the_first_scrape(client):
    data = client.get("/api/top").get_json()

    assert data["games"] == []
    assert data["total_players"] == 0
    assert data["tracked_count"] == 0


def test_top_lists_games_with_trend_and_sparkline(client, ago):
    # 23h ago is inside both the sparkline window (24h) and the trend window (20-28h).
    add_game(10, "Rising", [(100, ago(hours=23)), (120, ago(hours=1)), (150, ago(minutes=2))])
    add_game(20, "New", [(900, ago(minutes=2))])

    data = client.get("/api/top").get_json()

    assert [g["appid"] for g in data["games"]] == [20, 10]
    new, rising = data["games"]
    assert new["trend"] is None
    assert new["sparkline"] == [900]
    assert rising["name"] == "Rising"
    assert rising["current_players"] == 150
    assert rising["trend"] == 50.0
    assert rising["sparkline"] == [100, 120, 150]
    assert rising["thumbnail_url"] == "thumb-10.jpg"
    assert data["total_players"] == 1050
    assert data["tracked_count"] == 2


def test_top_reports_when_the_data_was_last_refreshed(client, ago):
    assert client.get("/api/top").get_json()["updated_at"] is None

    newest = ago(minutes=3)
    add_game(10, "A", [(1, ago(hours=2)), (2, newest)])
    add_game(20, "B", [(1, ago(minutes=8))])

    assert client.get("/api/top").get_json()["updated_at"] == newest


def test_top_reports_the_server_clock_in_the_same_format_as_the_data(client, ago):
    from datetime import datetime

    add_game(10, "A", [(1, ago(minutes=20))])

    data = client.get("/api/top").get_json()

    age = datetime.fromisoformat(data["server_time"]) - datetime.fromisoformat(data["updated_at"])
    assert 20 * 60 <= age.total_seconds() < 20 * 60 + 30


def test_top_includes_the_scraper_status(client, monkeypatch):
    import scraper

    monkeypatch.setattr(scraper, "_status", {
        "running": False,
        "last_started": "2026-11-16T11:12:01",
        "last_finished": "2026-11-16T11:13:26",
        "games_ok": 100,
        "games_failed": 22,
        "error": "Steam rate-limited the player count requests (HTTP 429), so 22 of 122 games were not updated this cycle.",
    })

    status = client.get("/api/top").get_json()["scraper"]

    assert status["enabled"] is False  # the test suite runs with SCRAPER_ENABLED=0
    assert status["running"] is False
    assert status["last_finished"] == "2026-11-16T11:13:26"
    assert status["games_ok"] == 100
    assert status["games_failed"] == 22
    assert "HTTP 429" in status["error"]


def test_top_honours_limit(client, ago):
    for appid in range(1, 4):
        add_game(appid, f"Game {appid}", [(appid, ago(minutes=1))])

    data = client.get("/api/top?limit=2").get_json()

    assert [g["appid"] for g in data["games"]] == [3, 2]


def test_game_detail_returns_stats_and_history(client, ago):
    add_game(10, "Game", [(5, ago(hours=30)), (7, ago(hours=2)), (9, ago(minutes=1))])
    db.upsert_peaks(10, 9, 12)

    data = client.get("/api/game/10").get_json()

    assert data["game"]["name"] == "Game"
    assert data["game"]["current_players"] == 9
    assert data["game"]["all_time_peak"] == 12
    assert data["game"]["header_image_url"] == "header-10.jpg"
    assert [h["current_players"] for h in data["history"]] == [7, 9]

    wider = client.get("/api/game/10?hours=168").get_json()
    assert [h["current_players"] for h in wider["history"]] == [5, 7, 9]


def test_game_detail_is_404_for_unknown_game(client):
    resp = client.get("/api/game/999")

    assert resp.status_code == 404
    assert resp.get_json() == {"error": "Game not found"}


def test_search_requires_two_characters(client, ago):
    add_game(10, "Among Us", [(5, ago(minutes=1))])

    assert client.get("/api/search?q=a").get_json() == {"results": []}
    results = client.get("/api/search?q=am").get_json()["results"]
    assert [r["appid"] for r in results] == [10]
