import os
import time
import logging
import threading
import requests

import database as db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10
SCRAPE_INTERVAL_MINUTES = 5

TOP_CHART_URL = "https://api.steampowered.com/ISteamChartsService/GetGamesByConcurrentPlayers/v1/"
PLAYER_COUNT_URL = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"

# Steam refreshes its top chart every few minutes. If the copy we receive is
# older than this, it is treated as unavailable rather than stored as current.
CHART_MAX_AGE_SECONDS = 30 * 60

# A game that drops out of Steam's top chart (and is not in TRACKED_APPIDS) is
# still tracked for this long, so a game hovering around rank 100 keeps a
# continuous history. After that it is no longer requested.
CHART_RETENTION_DAYS = 7

# Pauses between requests. Steam publishes no limit for keyless calls; the store
# API is known to block clients that go much above 200 requests per 5 minutes.
PLAYER_COUNT_DELAY = 0.3
DETAILS_DELAY = 1.5

# When the store has no page for an app, do not ask again for this long
# (instead of once per cycle, forever).
DETAILS_RETRY_SECONDS = 60 * 60

# After this many connection-level failures in a row, Steam or the network is
# down: stop the pass instead of waiting out REQUEST_TIMEOUT for every game.
MAX_CONSECUTIVE_FAILURES = 5

# The dashboard's longest chart covers 30 days, so less history than that
# would quietly break it.
MIN_HISTORY_DAYS = 30


def read_history_days():
    """How many days of samples to keep, from the HISTORY_DAYS environment variable.

    Returns None when it is not set: nothing is ever deleted, which is how the
    app has always behaved. Raises ValueError for anything that is not a whole
    number of at least MIN_HISTORY_DAYS days, because guessing here would mean
    deleting history the user wanted to keep.
    """
    raw = os.getenv("HISTORY_DAYS", "").strip()
    if not raw:
        return None
    try:
        days = int(raw)
    except ValueError:
        raise ValueError(f"HISTORY_DAYS must be a whole number of days, not {raw!r}") from None
    if days < MIN_HISTORY_DAYS:
        raise ValueError(
            f"HISTORY_DAYS must be at least {MIN_HISTORY_DAYS} (the 30-day chart needs that much), not {days}"
        )
    return days


# Read once at startup so a bad value stops the app instead of being noticed
# (or not) at the first cleanup.
HISTORY_DAYS = read_history_days()
PRUNE_INTERVAL_SECONDS = 24 * 60 * 60

# 120+ popular Steam app IDs (no API key needed to query these).
# These are always tracked. Whatever is in Steam's live top-100 chart is tracked
# in addition to them (see fetch_top_chart), so this list does not have to keep
# up with new releases.
TRACKED_APPIDS = [
    730,      # Counter-Strike 2
    570,      # Dota 2
    578080,   # PUBG: BATTLEGROUNDS
    271590,   # Grand Theft Auto V
    1245620,  # Elden Ring
    1086940,  # Baldur's Gate 3
    1623730,  # Palworld
    1966720,  # Lethal Company
    252490,   # Rust
    440,      # Team Fortress 2
    1172470,  # Apex Legends
    359550,   # Tom Clancy's Rainbow Six Siege
    1091500,  # Cyberpunk 2077
    892970,   # Valheim
    413150,   # Stardew Valley
    105600,   # Terraria
    381210,   # Dead by Daylight
    1174180,  # Red Dead Redemption 2
    548430,   # Deep Rock Galactic
    346110,   # ARK: Survival Evolved
    304930,   # Unturned
    236390,   # War Thunder
    1085660,  # Destiny 2
    218620,   # PAYDAY 2
    1938090,  # Call of Duty
    238960,   # Path of Exile
    2358720,  # Black Myth: Wukong
    431960,   # Wallpaper Engine
    550,      # Left 4 Dead 2
    960090,   # Baldur's Gate 3 (alt)
    1599340,  # Lost Ark
    1203220,  # NARAKA: BLADEPOINT
    1517290,  # Battlefield 2042
    2399830,  # ARK: Survival Ascended
    240,      # Counter-Strike: Source
    4000,     # Garry's Mod
    1222670,  # The Sims 4
    8930,     # Sid Meier's Civilization V
    289070,   # Sid Meier's Civilization VI
    945360,   # Among Us
    275850,   # No Man's Sky
    242760,   # The Forest
    1326470,  # Sons Of The Forest
    755790,   # Ring of Elysium
    1551360,  # Forza Horizon 5
    1145360,  # Hades
    1449850,  # Yu-Gi-Oh! Master Duel
    1229490,  # Cities: Skylines II
    1468810,  # Monster Hunter Rise
    582010,   # Monster Hunter: World
    526870,   # Satisfactory
    322170,   # Geometry Dash
    236850,   # Europa Universalis IV
    394360,   # Hearts of Iron IV
    281990,   # Stellaris
    203160,   # Tomb Raider (2013)
    814380,   # Sekiro: Shadows Die Twice
    292030,   # The Witcher 3: Wild Hunt
    374320,   # Dark Souls III
    1888160,  # The Finals
    230410,   # Warframe
    1158310,  # Crusader Kings III
    227300,   # Euro Truck Simulator 2
    1817070,  # Marvel's Spider-Man Remastered
    976730,   # Halo Infinite
    1687950,  # Persona 5 Royal
    312530,   # Duck Game
    42700,    # Call of Duty: Black Ops
    264710,   # Subnautica
    427520,   # Factorio
    892970,   # Valheim (confirm)
    1794680,  # Vampire Survivors
    728880,   # Overcooked! 2
    250900,   # The Binding of Isaac: Rebirth
    255710,   # Cities: Skylines
    1250410,  # Microsoft Flight Simulator
    620,      # Portal 2
    377160,   # Fallout 4
    489830,   # The Elder Scrolls V: Skyrim Special Edition
    1817190,  # Marvel's Spider-Man: Miles Morales
    606150,   # Phasmophobia
    1332010,  # Stray
    960090,   # Bloons TD 6 (alt)
    251570,   # 7 Days to Die
    1238810,  # Battlefield V
    1238860,  # Battlefield 1
    552520,   # Far Cry 5
    813780,   # Age of Empires II: Definitive Edition
    1551360,  # Forza Horizon 5
    594650,   # Hunt: Showdown
    774361,   # Halo: The Master Chief Collection
    108600,   # Project Zomboid
    49520,    # Borderlands 2
    72850,    # The Elder Scrolls V: Skyrim
    1778820,  # Hogwarts Legacy
    306130,   # The Elder Scrolls Online
    1286830,  # STAR WARS Jedi: Fallen Order
    1328670,  # Mass Effect Legendary Edition
    2215430,  # Ghost of Tsushima
    211820,   # Starbound
    236850,   # Europa Universalis IV
    1263950,  # V Rising
    1938090,  # Call of Duty: HQ
    2567870,  # Chained Together
    2694490,  # Banana
    48700,    # Mount & Blade: Warband
    261550,   # Mount & Blade II: Bannerlord
    435150,   # Divinity: Original Sin 2
    239140,   # Dying Light
    312520,   # Rain World
    990080,   # Hogwarts Legacy (alt)
    1817230,  # Marvel Rivals
    2379780,  # Balatro
    1811260,  # EA SPORTS FC 24
    2669320,  # EA SPORTS FC 25
    1627720,  # Lies of P
    1716740,  # Warhammer 40,000: Darktide
    22380,    # Fallout: New Vegas
    2050650,  # Resident Evil 4
    1293830,  # Forza Horizon 4
    1229380,  # Marvel's Avengers
    1332010,  # Stray
    601150,   # Devil May Cry 5
    1144200,  # Ready or Not
    1364780,  # Nioh 2
    1222680,  # Need for Speed Heat
    1816570,  # Papers, Please
    2420510,  # Content Warning
]

# Deduplicate
TRACKED_APPIDS = list(dict.fromkeys(TRACKED_APPIDS))


class SteamAPIError(Exception):
    """A Steam endpoint could not be reached or did not return a usable answer.

    `status` is the HTTP status code, or None when there was no HTTP response at
    all (DNS failure, refused connection, timeout).
    """

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status

    @property
    def is_outage(self):
        """True if the failure is about Steam or the network as a whole, not about one app."""
        return self.status is None or self.status >= 500


class SteamRateLimited(SteamAPIError):
    """Steam answered HTTP 429: stop sending this kind of request for the rest of the cycle."""


# One session for the whole process so the hundred-odd requests of a cycle reuse
# a connection instead of each doing a new TLS handshake. Only the scraper
# thread uses it.
_session = requests.Session()


def _get_json(url, params):
    """GET a Steam endpoint and return its JSON object, or raise SteamAPIError."""
    try:
        resp = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)
    except requests.Timeout as e:
        raise SteamAPIError(f"no response within {REQUEST_TIMEOUT}s") from e
    except requests.ConnectionError as e:
        raise SteamAPIError("could not connect") from e
    except requests.RequestException as e:
        raise SteamAPIError(f"request failed ({type(e).__name__})") from e

    if resp.status_code == 429:
        raise SteamRateLimited("rate limited (HTTP 429)", status=429)
    if resp.status_code != 200:
        raise SteamAPIError(f"HTTP {resp.status_code}", status=resp.status_code)
    try:
        data = resp.json()
    except ValueError as e:
        raise SteamAPIError("response is not JSON", status=200) from e
    if not isinstance(data, dict):
        raise SteamAPIError("response is not a JSON object", status=200)
    return data


def _is_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def fetch_top_chart():
    """Steam's live "most played" chart: the 100 games with the most players right now.

    Returns a list in rank order of
        {"appid": int, "current_players": int, "peak_today": int}
    where peak_today is the game's peak for the day as Steam reports it. This is
    the data behind store.steampowered.com/charts/mostplayed. It needs no API
    key but is not part of Steam's documented Web API, so anything unexpected
    in the answer raises SteamAPIError instead of being guessed around.
    """
    data = _get_json(TOP_CHART_URL, None)
    response = data.get("response")
    ranks = response.get("ranks") if isinstance(response, dict) else None
    if not isinstance(ranks, list) or not ranks:
        raise SteamAPIError("response has no ranks", status=200)

    last_update = response.get("last_update")
    if not _is_count(last_update):
        raise SteamAPIError("response does not say when the chart was updated", status=200)
    age = time.time() - last_update
    if age > CHART_MAX_AGE_SECONDS:
        raise SteamAPIError(f"the chart is {int(age // 60)} minutes old", status=200)

    chart = {}
    for rank in ranks:
        if not isinstance(rank, dict):
            raise SteamAPIError("response has a malformed chart entry", status=200)
        appid = rank.get("appid")
        current_players = rank.get("concurrent_in_game")
        peak_today = rank.get("peak_in_game")
        if not (_is_count(appid) and _is_count(current_players) and _is_count(peak_today)):
            raise SteamAPIError("response has a malformed chart entry", status=200)
        # Keyed by appid so an app listed twice cannot produce two samples.
        chart.setdefault(appid, {"appid": appid, "current_players": current_players, "peak_today": peak_today})
    return list(chart.values())


def fetch_current_players(appid):
    """Current player count of one app. Raises SteamAPIError if Steam gives none."""
    data = _get_json(PLAYER_COUNT_URL, {"appid": appid})
    response = data.get("response")
    count = response.get("player_count") if isinstance(response, dict) else None
    if not _is_count(count):
        raise SteamAPIError("response has no player_count", status=200)
    return count


def fetch_game_details(appid):
    """Name and artwork of one app from the store.

    Returns None when the store answers that it has no page for the app, and
    raises SteamAPIError when it gives no usable answer at all.
    """
    data = _get_json(APP_DETAILS_URL, {"appids": appid})
    entry = data.get(str(appid))
    if not isinstance(entry, dict):
        raise SteamAPIError("response has no entry for the app", status=200)
    if not entry.get("success"):
        return None
    info = entry.get("data")
    if not isinstance(info, dict):
        raise SteamAPIError("response reports success but has no data", status=200)
    return {
        "name": info.get("name", f"App {appid}"),
        "thumbnail_url": info.get("capsule_image", ""),
        "header_image_url": info.get("header_image", ""),
    }


_status_lock = threading.Lock()
_status = {
    "running": False,
    "last_started": None,
    "last_finished": None,
    "games_ok": None,
    "games_failed": None,
    "error": None,
}

# appid -> time.monotonic() value before which the store is not asked about it again.
_details_retry_at = {}

# time.monotonic() of the last history cleanup in this process, or None.
_last_prune = None


def get_status():
    """State of the scraper in this process, for the dashboard.

    running        a cycle is in progress
    last_started   when the latest cycle started (naive-UTC ISO string), or None
    last_finished  when the latest cycle ended, or None
    games_ok       games that got a new sample in the last finished cycle
    games_failed   tracked games that did not
    error          what cut the last finished cycle short, or None if nothing did
    """
    with _status_lock:
        return dict(_status)


def _pause(seconds):
    time.sleep(seconds)


def scrape_once():
    """Run one scrape cycle and return its summary (get_status() reports it too).

    A failed request is logged and counted but never raised: one unreachable
    game must not cost the others their sample. Anything else (a bug, a
    database error) is recorded in the status and then propagates.
    """
    with _status_lock:
        _status["running"] = True
        _status["last_started"] = db.utcnow().isoformat()

    summary = {"games_ok": None, "games_failed": None, "error": None}
    try:
        summary = _run_cycle()
        return summary
    except Exception as e:
        summary["error"] = f"The scrape cycle crashed ({type(e).__name__}: {e})."
        raise
    finally:
        with _status_lock:
            _status.update(summary, running=False, last_finished=db.utcnow().isoformat())


def _run_cycle():
    log.info("Starting scrape cycle...")
    now = db.utcnow().isoformat()
    problems = []
    counts = {}

    # 1. Steam's live top chart: one request yields the current count and the
    #    day's peak of the 100 most played games, whatever they are today.
    try:
        chart = fetch_top_chart()
    except SteamAPIError as e:
        chart = []
        log.error("Steam's top chart is unavailable (%s); newly popular games will not be picked up.", e)
        problems.append(
            f"Steam's top-100 chart could not be read ({e}). Games that are already tracked are "
            f"still updated one by one, but newly popular games are not being picked up."
        )
    if chart:
        db.record_chart(chart, now)
        counts.update((entry["appid"], entry["current_players"]) for entry in chart)
        log.info("Top chart: recorded %d games.", len(chart))

    # 2. Every other tracked game, one request each: the fixed list plus games
    #    that were in the chart recently (all of them when the chart is down).
    tracked = list(dict.fromkeys(TRACKED_APPIDS + db.get_recently_charted(CHART_RETENTION_DAYS)))
    others = [appid for appid in tracked if appid not in counts]
    db.ensure_games(others)
    log.info("Fetching player counts for %d other tracked games...", len(others))
    counts.update(_scrape_player_counts(others, now, problems))

    # 3. Names and artwork for games seen for the first time.
    _scrape_missing_details(counts, problems)

    # 4. Old samples, if the user asked for a limit (HISTORY_DAYS).
    _prune_history_if_due()

    total = len(counts.keys() | set(tracked))
    log.info("Scrape cycle complete. Got player counts for %d / %d games.", len(counts), total)
    return {
        "games_ok": len(counts),
        "games_failed": total - len(counts),
        "error": " ".join(problems) or None,
    }


def _prune_history_if_due():
    """Once a day, delete samples older than HISTORY_DAYS. Does nothing unless it is set.

    It runs as part of a cycle rather than as its own scheduled job so that it
    never writes to the database at the same time as the scraper.
    """
    global _last_prune
    if HISTORY_DAYS is None:
        return
    if _last_prune is not None and time.monotonic() - _last_prune < PRUNE_INTERVAL_SECONDS:
        return
    deleted = db.cleanup_old_data(HISTORY_DAYS)
    _last_prune = time.monotonic()
    log.info("Deleted %d samples older than %d days (HISTORY_DAYS).", deleted, HISTORY_DAYS)


def _scrape_player_counts(appids, now, problems):
    """Fetch and store the current player count of each app.

    Returns {appid: count} for the apps that got a sample, and appends a
    sentence to `problems` if the pass had to be cut short.
    """
    counts = {}
    consecutive_failures = 0

    for position, appid in enumerate(appids):
        if position:
            _pause(PLAYER_COUNT_DELAY)

        try:
            current = fetch_current_players(appid)
        except SteamRateLimited:
            log.error(
                "Steam rate-limited the player count requests; skipping the remaining %d games this cycle.",
                len(appids) - position,
            )
            problems.append(
                f"Steam rate-limited the player count requests (HTTP 429), so {len(appids) - len(counts)} "
                f"of the {len(appids)} games that are fetched one by one were not updated this cycle."
            )
            break
        except SteamAPIError as e:
            log.warning("No player count for appid %s: %s", appid, e)
            consecutive_failures = consecutive_failures + 1 if e.is_outage else 0
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                log.error(
                    "%d player count requests in a row failed; skipping the remaining %d games this cycle.",
                    consecutive_failures, len(appids) - position - 1,
                )
                problems.append(
                    f"{consecutive_failures} player count requests in a row failed ({e}): Steam or the "
                    f"network looks unreachable. {len(appids) - len(counts)} of the {len(appids)} games "
                    f"that are fetched one by one were not updated this cycle."
                )
                break
            continue

        consecutive_failures = 0
        counts[appid] = current
        db.record_samples([(appid, current, None)], now)

    return counts


def _scrape_missing_details(counts, problems):
    """Look up name and artwork for sampled games that only have a placeholder name.

    `counts` is {appid: current players}; the most played games are looked up
    first because they are at the top of the dashboard. The store API is slow
    (one request every DETAILS_DELAY seconds), so on a fresh database this pass
    takes a few minutes.
    """
    pending = []
    for appid in sorted(counts, key=counts.get, reverse=True):
        existing = db.get_game_detail(appid)
        if existing and existing.get("name") and existing["name"] != db.placeholder_name(appid):
            continue
        if _details_retry_at.get(appid, 0) > time.monotonic():
            continue
        pending.append(appid)

    log.info("Fetching details for %d new games...", len(pending))
    consecutive_failures = 0

    for position, appid in enumerate(pending):
        if position:
            _pause(DETAILS_DELAY)

        try:
            details = fetch_game_details(appid)
        except SteamRateLimited:
            remaining = len(pending) - position
            log.error("The Steam store rate-limited the detail lookups; %d games stay unnamed for now.", remaining)
            problems.append(
                f"The Steam store rate-limited the game detail lookups (HTTP 429), so {remaining} "
                f"games keep a placeholder name until a later cycle."
            )
            break
        except SteamAPIError as e:
            log.warning("No store details for appid %s: %s", appid, e)
            consecutive_failures = consecutive_failures + 1 if e.is_outage else 0
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                remaining = len(pending) - position - 1 + consecutive_failures
                log.error("%d detail lookups in a row failed; %d games stay unnamed for now.",
                          consecutive_failures, remaining)
                problems.append(
                    f"{consecutive_failures} game detail lookups in a row failed ({e}), so {remaining} "
                    f"games keep a placeholder name until a later cycle."
                )
                break
            continue

        consecutive_failures = 0
        if details is None:
            log.warning(
                "The Steam store has no page for appid %s; not asking again for %d minutes.",
                appid, DETAILS_RETRY_SECONDS // 60,
            )
            _details_retry_at[appid] = time.monotonic() + DETAILS_RETRY_SECONDS
            continue

        db.upsert_game(
            appid,
            details["name"],
            details.get("thumbnail_url"),
            details.get("header_image_url"),
        )


def run_scheduler():
    from apscheduler.schedulers.blocking import BlockingScheduler

    db.init_db()
    log.info("Running initial scrape...")
    scrape_once()

    scheduler = BlockingScheduler()
    scheduler.add_job(scrape_once, "interval", minutes=SCRAPE_INTERVAL_MINUTES, id="steam_scrape")
    log.info("Scheduler started. Scraping every %d minutes.", SCRAPE_INTERVAL_MINUTES)
    scheduler.start()


if __name__ == "__main__":
    run_scheduler()
