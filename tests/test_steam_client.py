"""The functions that talk to Steam, exercised against canned HTTP responses."""
import time

import pytest
import requests

import scraper
from scraper import SteamAPIError, SteamRateLimited


class FakeResponse:
    def __init__(self, status_code=200, payload=None, invalid_json=False):
        self.status_code = status_code
        self._payload = payload
        self._invalid_json = invalid_json

    def json(self):
        if self._invalid_json:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


class FakeSession:
    """Answers every GET with `answer` (a FakeResponse, or an exception to raise)."""

    def __init__(self, answer):
        self.answer = answer
        self.requests = []

    def get(self, url, params=None, timeout=None):
        self.requests.append({"url": url, "params": params, "timeout": timeout})
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def steam_answers(monkeypatch):
    def install(answer):
        session = FakeSession(answer)
        monkeypatch.setattr(scraper, "_session", session)
        return session

    return install


def chart_payload(ranks=None, age_seconds=240, **overrides):
    """A GetGamesByConcurrentPlayers answer; the default ranks are copied from a real one."""
    if ranks is None:
        ranks = [
            {"rank": 1, "appid": 730, "concurrent_in_game": 704464, "peak_in_game": 1299141},
            {"rank": 2, "appid": 570, "concurrent_in_game": 567006, "peak_in_game": 874632},
            {"rank": 5, "appid": 3419430, "concurrent_in_game": 196305, "peak_in_game": 196305},
        ]
    response = {"last_update": int(time.time()) - age_seconds, "ranks": ranks}
    response.update(overrides)
    return {"response": response}


def test_fetch_top_chart_returns_counts_and_peaks_in_rank_order(steam_answers):
    session = steam_answers(FakeResponse(payload=chart_payload()))

    assert scraper.fetch_top_chart() == [
        {"appid": 730, "current_players": 704464, "peak_today": 1299141},
        {"appid": 570, "current_players": 567006, "peak_today": 874632},
        {"appid": 3419430, "current_players": 196305, "peak_today": 196305},
    ]
    assert session.requests == [
        {"url": scraper.TOP_CHART_URL, "params": None, "timeout": scraper.REQUEST_TIMEOUT}
    ]


def test_fetch_top_chart_rejects_a_stale_chart(steam_answers):
    steam_answers(FakeResponse(payload=chart_payload(age_seconds=scraper.CHART_MAX_AGE_SECONDS + 90)))

    with pytest.raises(SteamAPIError) as error:
        scraper.fetch_top_chart()

    assert str(error.value) == "the chart is 31 minutes old"


def test_fetch_top_chart_lists_an_app_only_once(steam_answers):
    steam_answers(FakeResponse(payload=chart_payload(ranks=[
        {"rank": 1, "appid": 730, "concurrent_in_game": 700, "peak_in_game": 900},
        {"rank": 2, "appid": 730, "concurrent_in_game": 600, "peak_in_game": 800},
    ])))

    assert scraper.fetch_top_chart() == [{"appid": 730, "current_players": 700, "peak_today": 900}]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"response": {}},
        {"response": {"last_update": 1794827246, "ranks": []}},
        chart_payload(last_update=None),
        chart_payload(ranks="unavailable"),
        chart_payload(ranks=[None]),
        chart_payload(ranks=[{"rank": 1, "appid": 730, "peak_in_game": 5}]),
        chart_payload(ranks=[{"rank": 1, "appid": 730, "concurrent_in_game": 5}]),
        chart_payload(ranks=[{"rank": 1, "appid": 730, "concurrent_in_game": -1, "peak_in_game": 5}]),
        chart_payload(ranks=[{"rank": 1, "appid": "730", "concurrent_in_game": 5, "peak_in_game": 5}]),
    ],
)
def test_unusable_chart_answers_are_errors(steam_answers, payload):
    steam_answers(FakeResponse(payload=payload))

    with pytest.raises(SteamAPIError):
        scraper.fetch_top_chart()


def test_fetch_top_chart_reports_http_errors(steam_answers):
    steam_answers(FakeResponse(status_code=503))

    with pytest.raises(SteamAPIError) as error:
        scraper.fetch_top_chart()

    assert str(error.value) == "HTTP 503"


def test_fetch_current_players_returns_the_count(steam_answers):
    session = steam_answers(FakeResponse(payload={"response": {"player_count": 685270, "result": 1}}))

    assert scraper.fetch_current_players(730) == 685270
    assert session.requests == [
        {"url": scraper.PLAYER_COUNT_URL, "params": {"appid": 730}, "timeout": scraper.REQUEST_TIMEOUT}
    ]


def test_zero_players_is_a_valid_count(steam_answers):
    steam_answers(FakeResponse(payload={"response": {"player_count": 0, "result": 1}}))

    assert scraper.fetch_current_players(1263950) == 0


def test_unknown_app_is_an_error_about_that_app_only(steam_answers):
    # What Steam really answers for an app id without player statistics.
    steam_answers(FakeResponse(status_code=404, payload={"response": {"result": 42}}))

    with pytest.raises(SteamAPIError) as error:
        scraper.fetch_current_players(999999999)

    assert str(error.value) == "HTTP 404"
    assert error.value.status == 404
    assert not error.value.is_outage


def test_http_429_is_reported_as_rate_limiting(steam_answers):
    steam_answers(FakeResponse(status_code=429))

    with pytest.raises(SteamRateLimited) as error:
        scraper.fetch_current_players(730)

    assert error.value.status == 429


@pytest.mark.parametrize("status_code", [500, 502, 503])
def test_server_errors_count_as_an_outage(steam_answers, status_code):
    steam_answers(FakeResponse(status_code=status_code))

    with pytest.raises(SteamAPIError) as error:
        scraper.fetch_current_players(730)

    assert error.value.is_outage


@pytest.mark.parametrize(
    "failure, message",
    [
        (requests.ConnectTimeout("timed out"), "no response within 10s"),
        (requests.ReadTimeout("timed out"), "no response within 10s"),
        (requests.ConnectionError("getaddrinfo failed"), "could not connect"),
        (requests.TooManyRedirects("too many"), "request failed (TooManyRedirects)"),
    ],
)
def test_transport_failures_become_outage_errors(steam_answers, failure, message):
    steam_answers(failure)

    with pytest.raises(SteamAPIError) as error:
        scraper.fetch_current_players(730)

    assert str(error.value) == message
    assert error.value.status is None
    assert error.value.is_outage
    assert error.value.__cause__ is failure


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(invalid_json=True),
        FakeResponse(payload=[]),
        FakeResponse(payload={}),
        FakeResponse(payload={"response": {"result": 1}}),
        FakeResponse(payload={"response": {"player_count": "many"}}),
        FakeResponse(payload={"response": {"player_count": None}}),
    ],
)
def test_unusable_player_count_answers_are_errors(steam_answers, response):
    steam_answers(response)

    with pytest.raises(SteamAPIError):
        scraper.fetch_current_players(730)


def test_fetch_game_details_extracts_name_and_artwork(steam_answers):
    session = steam_answers(FakeResponse(payload={
        "730": {
            "success": True,
            "data": {
                "type": "game",
                "name": "Counter-Strike 2",
                "capsule_image": "https://cdn.example/730/capsule_231x87.jpg",
                "header_image": "https://cdn.example/730/header.jpg",
            },
        }
    }))

    assert scraper.fetch_game_details(730) == {
        "name": "Counter-Strike 2",
        "thumbnail_url": "https://cdn.example/730/capsule_231x87.jpg",
        "header_image_url": "https://cdn.example/730/header.jpg",
    }
    assert session.requests == [
        {"url": scraper.APP_DETAILS_URL, "params": {"appids": 730}, "timeout": scraper.REQUEST_TIMEOUT}
    ]


def test_fetch_game_details_is_none_when_the_store_has_no_page(steam_answers):
    # What the store really answers for app 1263950.
    steam_answers(FakeResponse(payload={"1263950": {"success": False}}))

    assert scraper.fetch_game_details(1263950) is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"730": None},
        {"730": {"success": True}},
        {"570": {"success": True, "data": {"name": "Dota 2"}}},
    ],
)
def test_unusable_detail_answers_are_errors(steam_answers, payload):
    steam_answers(FakeResponse(payload=payload))

    with pytest.raises(SteamAPIError):
        scraper.fetch_game_details(730)


def test_rate_limited_store_is_reported_as_rate_limiting(steam_answers):
    steam_answers(FakeResponse(status_code=429))

    with pytest.raises(SteamRateLimited):
        scraper.fetch_game_details(730)
