import sqlite3
from datetime import datetime

import pytest

import database as db
import scraper
from scraper import SteamAPIError, SteamRateLimited


class FakeSteam:
    """Stands in for the Steam endpoints so scrape cycles run offline and instantly.

    `chart` is Steam's top chart as [(appid, current players, peak today), ...],
    or an exception to raise. It is empty unless a test fills it.
    `counts` maps appid -> player count, or an exception to raise for that app.
    `details` maps appid -> details dict, None ("the store has no page"), or an
    exception. Apps missing from `counts` answer HTTP 404 like Steam does.
    """

    def __init__(self):
        self.chart = []
        self.counts = {}
        self.details = {}
        self.calls = []
        self.pauses = []

    def fetch_top_chart(self):
        self.calls.append(("chart", None))
        if isinstance(self.chart, Exception):
            raise self.chart
        return [
            {"appid": appid, "current_players": current, "peak_today": peak}
            for appid, current, peak in self.chart
        ]

    def fetch_current_players(self, appid):
        self.calls.append(("count", appid))
        answer = self.counts.get(appid, SteamAPIError("HTTP 404", status=404))
        if isinstance(answer, Exception):
            raise answer
        return answer

    def fetch_game_details(self, appid):
        self.calls.append(("details", appid))
        answer = self.details.get(appid)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def requested(self, kind):
        return [appid for call_kind, appid in self.calls if call_kind == kind]


def details(name):
    return {"name": name, "thumbnail_url": f"{name}-thumb.jpg", "header_image_url": f"{name}-header.jpg"}


@pytest.fixture
def steam(monkeypatch):
    fake = FakeSteam()
    monkeypatch.setattr(scraper, "TRACKED_APPIDS", [10, 20])
    monkeypatch.setattr(scraper, "fetch_top_chart", fake.fetch_top_chart)
    monkeypatch.setattr(scraper, "fetch_current_players", fake.fetch_current_players)
    monkeypatch.setattr(scraper, "fetch_game_details", fake.fetch_game_details)
    monkeypatch.setattr(scraper, "_pause", fake.pauses.append)
    monkeypatch.setattr(scraper, "_details_retry_at", {})
    monkeypatch.setattr(scraper, "_status", dict.fromkeys(scraper._status))
    monkeypatch.setattr(scraper, "_last_prune", None)
    return fake


def test_scrape_records_counts_and_game_details(steam):
    steam.counts = {10: 500, 20: 30}
    steam.details = {10: details("Ten")}

    summary = scraper.scrape_once()

    top = db.get_top_games()
    assert [(g["appid"], g["name"], g["current_players"]) for g in top] == [
        (10, "Ten", 500),
        (20, "App 20", 30),
    ]
    assert top[0]["thumbnail_url"] == "Ten-thumb.jpg"
    assert top[0]["header_image_url"] == "Ten-header.jpg"
    assert summary == {"games_ok": 2, "games_failed": 0, "error": None}


def test_all_samples_of_a_cycle_share_one_timestamp(steam):
    steam.chart = [(30, 900, 950)]
    steam.counts = {10: 500, 20: 30}

    scraper.scrape_once()

    timestamps = {g["timestamp"] for g in db.get_top_games()}
    assert len(db.get_top_games()) == 3
    assert len(timestamps) == 1
    assert db.get_latest_sample_time() in timestamps


def test_games_in_steams_top_chart_are_tracked_without_being_listed(steam):
    steam.chart = [(30, 9000, 12000), (40, 800, 900)]
    steam.counts = {10: 500, 20: 30}
    steam.details = {30: details("Thirty"), 40: details("Forty")}

    summary = scraper.scrape_once()

    top = db.get_top_games()
    assert [(g["appid"], g["name"], g["current_players"]) for g in top] == [
        (30, "Thirty", 9000),
        (40, "Forty", 800),
        (10, "App 10", 500),
        (20, "App 20", 30),
    ]
    assert summary == {"games_ok": 4, "games_failed": 0, "error": None}


def test_chart_games_are_not_requested_individually(steam):
    steam.chart = [(30, 9000, 12000), (10, 600, 700)]
    steam.counts = {10: 1, 20: 30, 30: 1}

    scraper.scrape_once()

    assert steam.requested("count") == [20]
    assert db.get_game_detail(10)["peak_24h"] == 700
    assert [g["current_players"] for g in db.get_top_games()] == [9000, 600, 30]


def test_chart_peak_is_stored_as_24h_and_all_time_peak(steam):
    steam.chart = [(30, 9000, 12000)]
    steam.counts = {10: 500, 20: 30}

    scraper.scrape_once()

    charted = db.get_game_detail(30)
    listed = db.get_game_detail(10)
    assert (charted["peak_24h"], charted["all_time_peak"]) == (12000, 12000)
    assert (listed["peak_24h"], listed["all_time_peak"]) == (500, 500)


def test_game_that_left_the_chart_is_still_tracked_one_by_one(steam):
    steam.chart = [(30, 9000, 12000)]
    steam.counts = {10: 500, 20: 30}
    scraper.scrape_once()

    steam.chart = [(40, 9500, 9900)]
    steam.counts = {10: 510, 20: 31, 30: 8000}
    summary = scraper.scrape_once()

    assert steam.requested("count") == [10, 20, 10, 20, 30]
    assert [h["current_players"] for h in db.get_player_history(30)] == [9000, 8000]
    assert db.get_game_detail(30)["peak_24h"] == 9000  # Steam's 12000 no longer applies; ours does
    assert db.get_game_detail(30)["all_time_peak"] == 12000
    assert summary == {"games_ok": 4, "games_failed": 0, "error": None}


def test_game_that_left_the_chart_long_ago_is_dropped(steam, ago):
    db.record_chart([{"appid": 30, "current_players": 9000, "peak_today": 9000}],
                    ago(days=scraper.CHART_RETENTION_DAYS, minutes=1))
    steam.counts = {10: 500, 20: 30, 30: 8000}

    summary = scraper.scrape_once()

    assert steam.requested("count") == [10, 20]
    assert [g["appid"] for g in db.get_top_games()] == [10, 20]
    assert summary == {"games_ok": 2, "games_failed": 0, "error": None}


def test_everything_is_fetched_one_by_one_when_the_chart_is_unavailable(steam):
    steam.chart = [(30, 9000, 12000)]
    steam.counts = {10: 500, 20: 30}
    scraper.scrape_once()

    steam.chart = SteamAPIError("HTTP 503", status=503)
    steam.counts = {10: 510, 20: 31, 30: 8800}
    summary = scraper.scrape_once()

    assert steam.requested("count") == [10, 20, 10, 20, 30]
    assert [g["current_players"] for g in db.get_top_games()] == [8800, 510, 31]
    assert summary["games_ok"] == 3
    assert summary["games_failed"] == 0
    assert summary["error"].startswith("Steam's top-100 chart could not be read (HTTP 503).")


def test_chart_and_listed_games_failing_are_counted(steam):
    steam.chart = [(30, 9000, 12000)]
    steam.counts = {10: 500}  # 20 answers HTTP 404

    summary = scraper.scrape_once()

    assert summary == {"games_ok": 2, "games_failed": 1, "error": None}


def test_peak_24h_is_the_highest_sample_not_the_latest(steam):
    steam.counts = {10: 500, 20: 30}
    scraper.scrape_once()
    steam.counts = {10: 400, 20: 45}
    scraper.scrape_once()

    falling = db.get_game_detail(10)
    rising = db.get_game_detail(20)

    assert (falling["peak_24h"], falling["all_time_peak"]) == (500, 500)
    assert (rising["peak_24h"], rising["all_time_peak"]) == (45, 45)


def test_peak_24h_ignores_samples_older_than_24_hours(steam, ago):
    db.upsert_game(10, "Ten")
    db.insert_player_count(10, 900, ago(hours=30))
    db.upsert_peaks(10, 900, 900)
    steam.counts = {10: 400}

    scraper.scrape_once()

    game = db.get_game_detail(10)
    assert game["peak_24h"] == 400
    assert game["all_time_peak"] == 900


def test_one_failing_game_does_not_affect_the_others(steam):
    steam.counts = {10: 500, 20: 30}
    scraper.scrape_once()
    steam.counts = {10: SteamAPIError("HTTP 404", status=404), 20: 40}

    summary = scraper.scrape_once()

    assert len(db.get_player_history(10)) == 1
    assert db.get_game_detail(10)["peak_24h"] == 500
    assert [h["current_players"] for h in db.get_player_history(20)] == [30, 40]
    assert summary == {"games_ok": 1, "games_failed": 1, "error": None}


def test_rate_limit_stops_the_player_count_requests(steam, monkeypatch):
    monkeypatch.setattr(scraper, "TRACKED_APPIDS", [10, 20, 30, 40])
    steam.counts = {10: 500, 20: SteamRateLimited("rate limited (HTTP 429)", status=429), 30: 5, 40: 6}

    summary = scraper.scrape_once()

    assert steam.requested("count") == [10, 20]
    assert [g["appid"] for g in db.get_top_games()] == [10]
    assert summary["games_ok"] == 1
    assert summary["games_failed"] == 3
    assert "HTTP 429" in summary["error"]
    assert "3 of the 4 games that are fetched one by one were not updated" in summary["error"]


def test_consecutive_connection_failures_stop_the_pass(steam, monkeypatch):
    appids = list(range(1, 11))
    monkeypatch.setattr(scraper, "TRACKED_APPIDS", appids)
    steam.counts = {appid: SteamAPIError("could not connect") for appid in appids}
    steam.counts[1] = 99

    summary = scraper.scrape_once()

    assert steam.requested("count") == [1, 2, 3, 4, 5, 6]
    assert summary["games_ok"] == 1
    assert summary["games_failed"] == 9
    assert "5 player count requests in a row failed (could not connect)" in summary["error"]
    assert "9 of the 10 games that are fetched one by one were not updated" in summary["error"]


def test_per_game_errors_do_not_count_as_an_outage(steam, monkeypatch):
    appids = list(range(1, 11))
    monkeypatch.setattr(scraper, "TRACKED_APPIDS", appids)
    steam.counts = {10: 7}  # every other app answers HTTP 404

    summary = scraper.scrape_once()

    assert steam.requested("count") == appids
    assert summary == {"games_ok": 1, "games_failed": 9, "error": None}


def test_requests_are_paced(steam, monkeypatch):
    monkeypatch.setattr(scraper, "TRACKED_APPIDS", [10, 20, 30])
    steam.counts = {10: 1, 20: SteamAPIError("HTTP 404", status=404), 30: 3}
    steam.details = {10: details("Ten"), 30: details("Thirty")}

    scraper.scrape_once()

    assert steam.pauses == [
        scraper.PLAYER_COUNT_DELAY,
        scraper.PLAYER_COUNT_DELAY,
        scraper.DETAILS_DELAY,
    ]


def test_details_are_fetched_for_the_most_played_games_first(steam, monkeypatch):
    monkeypatch.setattr(scraper, "TRACKED_APPIDS", [10, 20, 30])
    steam.counts = {10: 5, 20: 900, 30: 40}
    steam.details = {10: details("Ten"), 20: details("Twenty"), 30: details("Thirty")}

    scraper.scrape_once()

    assert steam.requested("details") == [20, 30, 10]


def test_named_games_are_not_looked_up_again(steam):
    steam.counts = {10: 500, 20: 30}
    steam.details = {10: details("Ten"), 20: details("Twenty")}
    scraper.scrape_once()

    scraper.scrape_once()

    assert steam.requested("details") == [10, 20]


def test_game_without_store_page_is_only_retried_after_the_backoff(steam):
    steam.counts = {10: 500, 20: 30}
    steam.details = {10: details("Ten"), 20: None}
    scraper.scrape_once()
    scraper.scrape_once()
    assert steam.requested("details") == [10, 20]

    scraper._details_retry_at[20] = 0  # the backoff has expired
    steam.details[20] = details("Twenty")
    scraper.scrape_once()

    assert steam.requested("details") == [10, 20, 20]
    assert db.get_game_detail(20)["name"] == "Twenty"


def test_failed_detail_lookup_is_retried_next_cycle(steam):
    steam.counts = {10: 500}
    steam.details = {10: SteamAPIError("no response within 10s")}
    summary = scraper.scrape_once()
    assert summary["error"] is None
    assert db.get_game_detail(10)["name"] == "App 10"

    steam.details = {10: details("Ten")}
    scraper.scrape_once()

    assert db.get_game_detail(10)["name"] == "Ten"


def test_rate_limit_stops_the_detail_lookups(steam, monkeypatch):
    monkeypatch.setattr(scraper, "TRACKED_APPIDS", [10, 20, 30])
    steam.counts = {10: 300, 20: 200, 30: 100}
    steam.details = {10: details("Ten"), 20: SteamRateLimited("rate limited (HTTP 429)", status=429)}

    summary = scraper.scrape_once()

    assert steam.requested("details") == [10, 20]
    assert summary["games_ok"] == 3
    assert "2 games keep a placeholder name" in summary["error"]


def test_status_reports_the_last_cycle(steam):
    assert scraper.get_status()["last_finished"] is None
    steam.counts = {10: 500, 20: SteamRateLimited("rate limited (HTTP 429)", status=429)}

    scraper.scrape_once()

    status = scraper.get_status()
    assert status["running"] is False
    assert status["last_started"] <= status["last_finished"]
    assert status["games_ok"] == 1
    assert status["games_failed"] == 1
    assert "HTTP 429" in status["error"]

    steam.counts = {10: 500, 20: 30}
    scraper.scrape_once()
    assert scraper.get_status()["error"] is None


def test_status_is_running_during_a_cycle(steam, monkeypatch):
    seen = []
    steam.counts = {10: 500, 20: 30}

    def fetch_and_peek(appid):
        seen.append(scraper.get_status()["running"])
        return steam.fetch_current_players(appid)

    monkeypatch.setattr(scraper, "fetch_current_players", fetch_and_peek)

    scraper.scrape_once()

    assert seen == [True, True]


def test_crash_is_recorded_and_raised(steam, monkeypatch):
    steam.counts = {10: 500, 20: 30}

    def broken_insert(*args):
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(db, "record_samples", broken_insert)

    with pytest.raises(sqlite3.OperationalError):
        scraper.scrape_once()

    status = scraper.get_status()
    assert status["running"] is False
    assert status["last_finished"] is not None
    assert status["error"] == "The scrape cycle crashed (OperationalError: database or disk is full)."


def sample_ages_in_days(appid):
    now = db.utcnow()
    history = db.get_player_history(appid, hours=24 * 3650)
    return [round((now - datetime.fromisoformat(h["timestamp"])).total_seconds() / 86400) for h in history]


def test_history_is_never_deleted_unless_a_limit_is_set(steam, ago):
    db.upsert_game(10, "Ten")
    db.insert_player_count(10, 1, ago(days=400))
    steam.counts = {10: 500, 20: 30}

    assert scraper.HISTORY_DAYS is None
    scraper.scrape_once()

    assert sample_ages_in_days(10) == [400, 0]


def test_history_older_than_the_limit_is_deleted_once_a_day(steam, ago, monkeypatch):
    monkeypatch.setattr(scraper, "HISTORY_DAYS", 90)
    db.upsert_game(10, "Ten")
    db.insert_player_count(10, 900, ago(days=400))
    db.insert_player_count(10, 2, ago(days=80))
    db.upsert_peaks(10, 900, 900)
    steam.counts = {10: 500, 20: 30}

    scraper.scrape_once()

    assert sample_ages_in_days(10) == [80, 0]
    assert db.get_game_detail(10)["all_time_peak"] == 900  # peaks outlive the samples

    # A second cycle on the same day does not scan for old samples again...
    db.insert_player_count(10, 3, ago(days=300))
    scraper.scrape_once()
    assert sample_ages_in_days(10) == [300, 80, 0, 0]

    # ...the first cycle after a day has passed does.
    monkeypatch.setattr(scraper, "_last_prune", scraper.time.monotonic() - scraper.PRUNE_INTERVAL_SECONDS - 1)
    scraper.scrape_once()
    assert sample_ages_in_days(10) == [80, 0, 0, 0]


@pytest.mark.parametrize("value, expected", [(None, None), ("", None), ("  ", None), ("90", 90), (" 30 ", 30), ("365", 365)])
def test_history_days_setting(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("HISTORY_DAYS", raising=False)
    else:
        monkeypatch.setenv("HISTORY_DAYS", value)

    assert scraper.read_history_days() == expected


@pytest.mark.parametrize("value", ["abc", "90 days", "12.5", "0", "-1", "29"])
def test_unusable_history_days_setting_is_an_error(monkeypatch, value):
    monkeypatch.setenv("HISTORY_DAYS", value)

    with pytest.raises(ValueError, match="HISTORY_DAYS must be"):
        scraper.read_history_days()
