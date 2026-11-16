import os
import threading
import logging
from flask import Flask, jsonify, request, render_template

import database as db
from scraper import scrape_once

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

# Initialize database
db.init_db()


def start_background_scraper():
    from apscheduler.schedulers.background import BackgroundScheduler

    log.info("Running initial scrape in background...")
    try:
        scrape_once()
    except Exception as e:
        log.error("Initial scrape failed: %s", e)

    scheduler = BackgroundScheduler()
    scheduler.add_job(scrape_once, "interval", minutes=5, id="steam_scrape")
    scheduler.start()
    log.info("Background scraper started (every 5 minutes).")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/top")
def api_top():
    limit = request.args.get("limit", 100, type=int)
    limit = min(limit, 200)
    games = db.get_top_games(limit)

    # Attach trend and sparkline data
    for game in games:
        yesterday = db.get_player_count_yesterday(game["appid"])
        current = game.get("current_players", 0)
        if yesterday and yesterday > 0:
            game["trend"] = round(((current - yesterday) / yesterday) * 100, 1)
        else:
            game["trend"] = None
        game["sparkline"] = db.get_sparkline_data(game["appid"])

    return jsonify({
        "games": games,
        "total_players": db.get_total_players(),
        "tracked_count": db.get_tracked_game_count(),
    })


@app.route("/api/game/<int:appid>")
def api_game(appid):
    game = db.get_game_detail(appid)
    if not game:
        return jsonify({"error": "Game not found"}), 404

    hours = request.args.get("hours", 24, type=int)
    hours = min(hours, 720)  # Max 30 days
    history = db.get_player_history(appid, hours)

    # Get current players from latest record
    if history:
        game["current_players"] = history[-1]["current_players"]
    else:
        game["current_players"] = 0

    return jsonify({
        "game": game,
        "history": history,
    })


@app.route("/api/search")
def api_search():
    query = request.args.get("q", "").strip()
    if len(query) < 2:
        return jsonify({"results": []})
    results = db.search_games(query)
    return jsonify({"results": results})


# Start the scraper in a background thread when the app starts
_scraper_started = False


def ensure_scraper():
    global _scraper_started
    if not _scraper_started:
        _scraper_started = True
        t = threading.Thread(target=start_background_scraper, daemon=True)
        t.start()


# The dashboard runs its own scraper thread. Set SCRAPER_ENABLED=0 to serve the
# dashboard only (tests, or when `python scraper.py` runs as a separate process).
if os.getenv("SCRAPER_ENABLED", "1") != "0":
    ensure_scraper()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
