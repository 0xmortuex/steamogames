import time
import logging
import requests
from datetime import datetime

import database as db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10

# 120+ popular Steam app IDs (no API key needed to query these)
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


def fetch_current_players(appid):
    url = f"https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid={appid}"
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        count = data.get("response", {}).get("player_count")
        if count is not None:
            return count
    except Exception as e:
        log.warning("Failed to fetch player count for appid %s: %s", appid, e)
    return None


def fetch_game_details(appid):
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}"
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        app_data = data.get(str(appid), {})
        if app_data.get("success"):
            info = app_data["data"]
            return {
                "name": info.get("name", f"App {appid}"),
                "thumbnail_url": info.get("capsule_image", ""),
                "header_image_url": info.get("header_image", ""),
            }
    except Exception as e:
        log.warning("Failed to fetch details for appid %s: %s", appid, e)
    return None


def scrape_once():
    log.info("Starting scrape cycle for %d tracked games...", len(TRACKED_APPIDS))
    now = datetime.utcnow().isoformat()
    success_count = 0
    games_needing_details = []

    # First pass: ensure all tracked games have a row in the games table
    for appid in TRACKED_APPIDS:
        existing = db.get_game_detail(appid)
        if not existing:
            db.upsert_game(appid, f"App {appid}")

    # Second pass: fetch player counts
    for appid in TRACKED_APPIDS:
        current = fetch_current_players(appid)
        if current is None:
            continue

        success_count += 1
        db.insert_player_count(appid, current, now)
        # The 24h peak is the highest sample of the last 24 hours (which includes
        # the one just inserted), not the latest count. upsert_peaks() only ever
        # raises the all-time peak, so passing the 24h peak for both is correct.
        peak_24h = db.get_peak_players(appid, hours=24)
        db.upsert_peaks(appid, peak_24h, peak_24h)

        existing = db.get_game_detail(appid)
        if not existing or not existing.get("name") or existing["name"] == f"App {appid}":
            games_needing_details.append(appid)

        # Small delay to respect rate limits (~200 req / 5 min)
        time.sleep(0.3)

    # Fetch game details for unknown games (store API is slower, 1 req/sec)
    log.info("Fetching details for %d new games...", len(games_needing_details))
    for i, appid in enumerate(games_needing_details):
        details = fetch_game_details(appid)
        if details:
            db.upsert_game(
                appid,
                details["name"],
                details.get("thumbnail_url"),
                details.get("header_image_url"),
            )

        if i < len(games_needing_details) - 1:
            time.sleep(1.5)

    log.info("Scrape cycle complete. Got player counts for %d / %d games.", success_count, len(TRACKED_APPIDS))


def run_scheduler():
    from apscheduler.schedulers.blocking import BlockingScheduler

    db.init_db()
    log.info("Running initial scrape...")
    scrape_once()

    scheduler = BlockingScheduler()
    scheduler.add_job(scrape_once, "interval", minutes=5, id="steam_scrape")
    log.info("Scheduler started. Scraping every 5 minutes.")
    scheduler.start()


if __name__ == "__main__":
    run_scheduler()
