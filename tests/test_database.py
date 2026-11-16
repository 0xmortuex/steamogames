import sqlite3

import pytest

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


def test_top_games_lists_each_game_once_when_sample_timestamps_tie(ago):
    same_moment = ago(minutes=5)
    add_game(10, "Game", [(100, same_moment), (120, same_moment)])

    top = db.get_top_games()

    assert [(g["appid"], g["current_players"]) for g in top] == [(10, 120)]
    assert db.get_total_players() == 120


def test_top_games_with_equal_counts_are_ordered_by_appid(ago):
    add_game(30, "C", [(5, ago(minutes=1))])
    add_game(10, "A", [(5, ago(minutes=1))])
    add_game(20, "B", [(5, ago(minutes=1))])

    assert [g["appid"] for g in db.get_top_games()] == [10, 20, 30]


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


def peaks(appid):
    game = db.get_game_detail(appid)
    return game["peak_24h"], game["all_time_peak"]


def test_record_samples_stores_the_count_and_the_highest_sample_of_24_hours(ago):
    add_game(10, "Game", [(900, ago(hours=30)), (300, ago(hours=20))])
    db.upsert_peaks(10, 300, 900)

    now = ago()
    db.record_samples([(10, 200, None)], now)

    assert db.get_top_games()[0]["current_players"] == 200
    assert db.get_top_games()[0]["timestamp"] == now
    assert peaks(10) == (300, 900)  # 900 was sampled more than 24h ago


def test_record_samples_uses_the_reported_peak_when_it_is_higher(ago):
    db.upsert_game(10, "New to us")
    db.upsert_game(20, "Tracked for a while")
    db.insert_player_count(20, 5000, ago(hours=3))

    db.record_samples([(10, 700, 1300), (20, 4000, 4500)], ago())

    assert peaks(10) == (1300, 1300)
    assert peaks(20) == (5000, 5000)  # our own sample from 3h ago beats Steam's figure


def test_record_samples_is_all_or_nothing(ago):
    db.upsert_game(10, "Game")

    with pytest.raises(sqlite3.IntegrityError):
        # 999 is not in the games table: the foreign key rejects it.
        db.record_samples([(10, 100, None), (999, 5, None)], ago())

    assert db.get_top_games() == []
    assert peaks(10) == (None, None)


def test_record_chart_adds_new_games_and_remembers_when_they_were_charted(ago):
    db.upsert_game(10, "Known Game", "thumb.jpg", "header.jpg")

    db.record_chart(
        [
            {"appid": 10, "current_players": 700, "peak_today": 1300},
            {"appid": 20, "current_players": 90, "peak_today": 150},
        ],
        ago(),
    )

    top = db.get_top_games()
    assert [(g["appid"], g["name"], g["current_players"], g["peak_24h"]) for g in top] == [
        (10, "Known Game", 700, 1300),
        (20, "App 20", 90, 150),
    ]
    assert top[0]["thumbnail_url"] == "thumb.jpg"
    assert db.get_recently_charted(days=7) == [10, 20]


def test_recently_charted_forgets_games_after_the_retention_period(ago):
    db.record_chart([{"appid": 10, "current_players": 5, "peak_today": 5}], ago(days=8))
    db.record_chart([{"appid": 20, "current_players": 5, "peak_today": 5}], ago(days=6))
    db.record_chart([{"appid": 30, "current_players": 5, "peak_today": 5}], ago(minutes=5))

    assert db.get_recently_charted(days=7) == [20, 30]

    db.record_chart([{"appid": 10, "current_players": 6, "peak_today": 6}], ago())
    assert db.get_recently_charted(days=7) == [10, 20, 30]


def test_ensure_games_creates_placeholders_without_touching_known_games():
    db.upsert_game(10, "Known Game", "thumb.jpg", "header.jpg")

    db.ensure_games([10, 20])

    assert db.get_game_detail(10)["name"] == "Known Game"
    assert db.get_game_detail(10)["thumbnail_url"] == "thumb.jpg"
    assert db.get_game_detail(20)["name"] == db.placeholder_name(20) == "App 20"


def test_games_that_stopped_being_sampled_are_not_live(ago):
    add_game(10, "Live", [(100, ago(minutes=40)), (110, ago(minutes=5))])
    add_game(20, "Missed two cycles", [(50, ago(minutes=14))])
    add_game(30, "No longer tracked", [(9000, ago(minutes=40))])
    add_game(40, "Scraped just now", [(7, ago())])

    assert [g["appid"] for g in db.get_top_games()] == [10, 20, 40]
    assert db.get_total_players() == 167
    assert db.get_tracked_game_count() == 3
    assert [g["appid"] for g in db.search_games("tracked")] == []
    assert [g["appid"] for g in db.search_games("missed")] == [20]


def test_last_snapshot_stays_live_while_the_scraper_is_down(ago):
    # Nothing has been sampled for two days: "live" is measured from the newest
    # sample, so the last complete snapshot is still served.
    add_game(10, "A", [(100, ago(days=2, minutes=5))])
    add_game(20, "B", [(50, ago(days=2))])
    add_game(30, "Dropped before the outage", [(70, ago(days=2, minutes=30))])

    assert [g["appid"] for g in db.get_top_games()] == [10, 20]
    assert db.get_total_players() == 150
    assert db.get_tracked_game_count() == 2
    assert db.get_latest_sample_time() == db.get_top_games()[1]["timestamp"]


def test_init_db_upgrades_a_database_created_by_an_older_version(temp_db, ago):
    # The schema as it was before the top_chart table existed.
    temp_db.unlink()
    conn = sqlite3.connect(temp_db)
    conn.executescript("""
        CREATE TABLE games (appid INTEGER PRIMARY KEY, name TEXT NOT NULL,
                            thumbnail_url TEXT, header_image_url TEXT);
        CREATE TABLE player_counts (id INTEGER PRIMARY KEY AUTOINCREMENT, appid INTEGER NOT NULL,
                                    current_players INTEGER NOT NULL, timestamp TEXT NOT NULL,
                                    FOREIGN KEY (appid) REFERENCES games(appid));
        CREATE TABLE peaks (appid INTEGER PRIMARY KEY, peak_24h INTEGER DEFAULT 0,
                            all_time_peak INTEGER DEFAULT 0, last_updated TEXT,
                            FOREIGN KEY (appid) REFERENCES games(appid));
        CREATE INDEX idx_player_counts_appid_ts ON player_counts(appid, timestamp);
    """)
    conn.execute("INSERT INTO games VALUES (10, 'Old Game', 't.jpg', 'h.jpg')")
    conn.execute("INSERT INTO player_counts (appid, current_players, timestamp) VALUES (10, 42, ?)",
                 (ago(minutes=5),))
    conn.execute("INSERT INTO peaks VALUES (10, 42, 99, ?)", (ago(minutes=5),))
    conn.commit()
    conn.close()

    db.init_db()

    top = db.get_top_games()
    assert [(g["name"], g["current_players"], g["all_time_peak"]) for g in top] == [("Old Game", 42, 99)]
    assert db.get_recently_charted(days=7) == []
    db.record_chart([{"appid": 10, "current_players": 50, "peak_today": 60}], ago())
    assert peaks(10) == (60, 99)


def test_sparkline_is_downsampled_to_24_points(ago):
    samples = [(i, ago(minutes=5 * (100 - i))) for i in range(100)]
    add_game(10, "Game", samples)

    sparkline = db.get_sparkline_data(10)

    assert len(sparkline) == 24
    assert sparkline[0] == 0
    assert sparkline == sorted(sparkline)


def test_sparkline_ends_at_the_newest_sample(ago):
    # A full day of 5-minute samples, as the scraper produces.
    samples = [(i, ago(minutes=5 * (287 - i))) for i in range(288)]
    add_game(10, "Game", samples)

    sparkline = db.get_sparkline_data(10)

    assert len(sparkline) == 24
    assert sparkline[-1] == 287
    assert sparkline == sorted(set(sparkline))


def test_latest_sample_of_a_game(ago):
    newest = ago(hours=30)
    add_game(10, "Game", [(5, ago(hours=50)), (7, newest)])
    db.upsert_game(20, "Never scraped")

    assert db.get_latest_sample(10) == {"current_players": 7, "timestamp": newest}
    assert db.get_latest_sample(20) is None


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


def test_trend_data_matches_the_single_game_helpers(ago):
    add_game(10, "A", [(100, ago(hours=23)), (150, ago(minutes=5))])
    add_game(20, "B", [(7, ago(minutes=5))])

    trend_data = db.get_trend_data([10, 20, 999])

    assert trend_data == {
        10: {"yesterday": 100, "sparkline": [100, 150]},
        20: {"yesterday": None, "sparkline": [7]},
        999: {"yesterday": None, "sparkline": []},
    }
    for appid in (10, 20):
        assert trend_data[appid]["yesterday"] == db.get_player_count_yesterday(appid)
        assert trend_data[appid]["sparkline"] == db.get_sparkline_data(appid)


def test_search_only_lists_games_that_have_a_count(ago):
    db.upsert_game(10, "Portal")
    add_game(20, "Portal 2", [(800, ago(minutes=1))])

    results = db.search_games("portal")

    assert [(r["appid"], r["current_players"]) for r in results] == [(20, 800)]


def test_search_is_empty_before_the_first_scrape():
    db.upsert_game(10, "Portal")

    assert db.search_games("portal") == []


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


def test_latest_sample_time_is_the_newest_sample_of_any_game(ago):
    assert db.get_latest_sample_time() is None

    newest = ago(minutes=2)
    add_game(10, "A", [(1, ago(hours=5)), (1, ago(minutes=7))])
    add_game(20, "B", [(1, newest), (1, ago(days=3))])
    db.upsert_game(30, "Never scraped")

    assert db.get_latest_sample_time() == newest


def test_utcnow_is_naive_utc():
    from datetime import datetime, timezone

    now = db.utcnow()

    assert now.tzinfo is None
    assert abs((datetime.now(timezone.utc).replace(tzinfo=None) - now).total_seconds()) < 5


def test_tracked_game_count(ago):
    add_game(10, "A", [(1, ago(minutes=1))])
    add_game(20, "B", [(1, ago(minutes=1))])

    assert db.get_tracked_game_count() == 2


def test_cleanup_deletes_only_samples_older_than_cutoff(ago):
    add_game(10, "Game", [(1, ago(days=100)), (2, ago(days=80)), (3, ago(hours=1))])

    db.cleanup_old_data(days=90)

    assert [h["current_players"] for h in db.get_player_history(10, hours=24 * 365)] == [2, 3]
