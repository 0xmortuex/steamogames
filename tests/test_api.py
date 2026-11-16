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
