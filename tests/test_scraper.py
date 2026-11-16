import pytest

import database as db
import scraper


class FakeSteam:
    """Stands in for the Steam endpoints so scrape cycles run offline and instantly."""

    def __init__(self):
        self.counts = {}
        self.details = {}

    def fetch_current_players(self, appid):
        return self.counts.get(appid)

    def fetch_game_details(self, appid):
        return self.details.get(appid)


@pytest.fixture
def steam(monkeypatch):
    fake = FakeSteam()
    monkeypatch.setattr(scraper, "TRACKED_APPIDS", [10, 20])
    monkeypatch.setattr(scraper, "fetch_current_players", fake.fetch_current_players)
    monkeypatch.setattr(scraper, "fetch_game_details", fake.fetch_game_details)
    monkeypatch.setattr(scraper.time, "sleep", lambda seconds: None)
    return fake


def test_scrape_records_counts_and_game_details(steam):
    steam.counts = {10: 500, 20: 30}
    steam.details = {10: {"name": "Ten", "thumbnail_url": "t.jpg", "header_image_url": "h.jpg"}}

    scraper.scrape_once()

    top = db.get_top_games()
    assert [(g["appid"], g["name"], g["current_players"]) for g in top] == [
        (10, "Ten", 500),
        (20, "App 20", 30),
    ]
    assert top[0]["thumbnail_url"] == "t.jpg"


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


def test_failed_count_fetch_keeps_previous_data(steam):
    steam.counts = {10: 500, 20: 30}
    scraper.scrape_once()
    steam.counts = {10: None, 20: 40}

    scraper.scrape_once()

    assert len(db.get_player_history(10)) == 1
    assert db.get_game_detail(10)["peak_24h"] == 500
    assert len(db.get_player_history(20)) == 2
