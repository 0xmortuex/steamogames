import database as db


def add_game(appid, name, samples=()):
    """Create a game and its samples; `samples` is [(players, iso_timestamp), ...]."""
    db.upsert_game(appid, name)
    for players, timestamp in samples:
        db.insert_player_count(appid, players, timestamp)


def test_top_games_use_latest_sample_and_sort_by_players(ago):
    add_game(10, "Small", [(900, ago(minutes=10)), (50, ago(minutes=5))])
    add_game(20, "Big", [(100, ago(minutes=10)), (700, ago(minutes=5))])

    top = db.get_top_games()

    assert [(g["appid"], g["current_players"]) for g in top] == [(20, 700), (10, 50)]


def test_top_games_respects_limit(ago):
    for appid in range(1, 6):
        add_game(appid, f"Game {appid}", [(appid * 10, ago(minutes=1))])

    assert [g["appid"] for g in db.get_top_games(limit=2)] == [5, 4]


def test_games_without_samples_are_not_listed():
    db.upsert_game(10, "Never scraped")

    assert db.get_top_games() == []


def test_upsert_game_keeps_images_when_updated_without_them():
    db.upsert_game(10, "Game", "thumb.jpg", "header.jpg")
    db.upsert_game(10, "Game Renamed")

    game = db.get_game_detail(10)

    assert game["name"] == "Game Renamed"
    assert game["thumbnail_url"] == "thumb.jpg"
    assert game["header_image_url"] == "header.jpg"


def test_game_detail_is_none_for_unknown_game():
    assert db.get_game_detail(999) is None


def test_all_time_peak_never_decreases():
    db.upsert_game(10, "Game")
    db.upsert_peaks(10, 500, 500)
    db.upsert_peaks(10, 300, 300)

    game = db.get_game_detail(10)

    assert game["all_time_peak"] == 500
    assert game["peak_24h"] == 300


def test_player_history_only_returns_requested_window_oldest_first(ago):
    add_game(10, "Game", [(1, ago(hours=30)), (2, ago(hours=3)), (3, ago(hours=1))])

    history = db.get_player_history(10, hours=24)

    assert [h["current_players"] for h in history] == [2, 3]
    assert db.get_player_history(10, hours=48)[0]["current_players"] == 1


def test_peak_players_is_the_maximum_within_the_window(ago):
    add_game(10, "Game", [(900, ago(hours=30)), (300, ago(hours=20)), (200, ago(minutes=5))])

    assert db.get_peak_players(10, hours=24) == 300
    assert db.get_peak_players(10, hours=48) == 900
    assert db.get_peak_players(10, hours=1) == 200


def test_peak_players_is_none_without_samples_in_window(ago):
    add_game(10, "Game", [(900, ago(hours=30))])

    assert db.get_peak_players(10, hours=24) is None


def test_sparkline_is_downsampled_to_24_points(ago):
    samples = [(i, ago(minutes=5 * (100 - i))) for i in range(100)]
    add_game(10, "Game", samples)

    sparkline = db.get_sparkline_data(10)

    assert len(sparkline) == 24
    assert sparkline[0] == 0
    assert sparkline == sorted(sparkline)


def test_sparkline_returns_all_points_when_there_are_few(ago):
    add_game(10, "Game", [(5, ago(hours=2)), (7, ago(hours=1)), (99, ago(hours=30))])

    assert db.get_sparkline_data(10) == [5, 7]


def test_yesterday_count_averages_the_20_to_28_hour_window(ago):
    add_game(
        10,
        "Game",
        [(100, ago(hours=27)), (200, ago(hours=21)), (9999, ago(hours=10)), (9999, ago(hours=40))],
    )

    assert db.get_player_count_yesterday(10) == 150


def test_yesterday_count_is_none_without_data_in_window(ago):
    add_game(10, "Game", [(100, ago(hours=1))])

    assert db.get_player_count_yesterday(10) is None


def test_search_matches_substring_case_insensitively(ago):
    add_game(10, "Counter-Strike 2", [(700, ago(minutes=1))])
    add_game(20, "Counter-Strike: Source", [(20, ago(minutes=1))])
    add_game(30, "Dota 2", [(500, ago(minutes=1))])

    results = db.search_games("counter")

    assert [(r["appid"], r["current_players"]) for r in results] == [(10, 700), (20, 20)]


def test_total_players_sums_the_latest_sample_of_each_game(ago):
    add_game(10, "A", [(1000, ago(minutes=10)), (100, ago(minutes=5))])
    add_game(20, "B", [(50, ago(minutes=5))])

    assert db.get_total_players() == 150


def test_total_players_is_zero_for_empty_database():
    assert db.get_total_players() == 0


def test_tracked_game_count(ago):
    add_game(10, "A", [(1, ago(minutes=1))])
    add_game(20, "B", [(1, ago(minutes=1))])

    assert db.get_tracked_game_count() == 2


def test_cleanup_deletes_only_samples_older_than_cutoff(ago):
    add_game(10, "Game", [(1, ago(days=100)), (2, ago(days=80)), (3, ago(hours=1))])

    db.cleanup_old_data(days=90)

    assert [h["current_players"] for h in db.get_player_history(10, hours=24 * 365)] == [2, 3]
