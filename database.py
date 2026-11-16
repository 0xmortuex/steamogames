import sqlite3
import os
from contextlib import closing
from datetime import datetime, timedelta

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "steam.db")


def get_connection():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS games (
            appid INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            thumbnail_url TEXT,
            header_image_url TEXT
        );

        CREATE TABLE IF NOT EXISTS player_counts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            appid INTEGER NOT NULL,
            current_players INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (appid) REFERENCES games(appid)
        );

        CREATE TABLE IF NOT EXISTS peaks (
            appid INTEGER PRIMARY KEY,
            peak_24h INTEGER DEFAULT 0,
            all_time_peak INTEGER DEFAULT 0,
            last_updated TEXT,
            FOREIGN KEY (appid) REFERENCES games(appid)
        );

        CREATE INDEX IF NOT EXISTS idx_player_counts_appid_ts
            ON player_counts(appid, timestamp);
    """)
    conn.commit()
    conn.close()


def upsert_game(appid, name, thumbnail_url=None, header_image_url=None):
    conn = get_connection()
    conn.execute("""
        INSERT INTO games (appid, name, thumbnail_url, header_image_url)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(appid) DO UPDATE SET
            name = COALESCE(excluded.name, games.name),
            thumbnail_url = COALESCE(excluded.thumbnail_url, games.thumbnail_url),
            header_image_url = COALESCE(excluded.header_image_url, games.header_image_url)
    """, (appid, name, thumbnail_url, header_image_url))
    conn.commit()
    conn.close()


def insert_player_count(appid, current_players, timestamp=None):
    if timestamp is None:
        timestamp = datetime.utcnow().isoformat()
    conn = get_connection()
    conn.execute(
        "INSERT INTO player_counts (appid, current_players, timestamp) VALUES (?, ?, ?)",
        (appid, current_players, timestamp),
    )
    conn.commit()
    conn.close()


def upsert_peaks(appid, peak_24h, all_time_peak):
    now = datetime.utcnow().isoformat()
    conn = get_connection()
    conn.execute("""
        INSERT INTO peaks (appid, peak_24h, all_time_peak, last_updated)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(appid) DO UPDATE SET
            peak_24h = excluded.peak_24h,
            all_time_peak = CASE
                WHEN excluded.all_time_peak > peaks.all_time_peak
                THEN excluded.all_time_peak
                ELSE peaks.all_time_peak
            END,
            last_updated = excluded.last_updated
    """, (appid, peak_24h, all_time_peak, now))
    conn.commit()
    conn.close()


# Correlated subquery selecting the id of a game's newest sample. It is a single
# seek in idx_player_counts_appid_ts per game, so "latest count per game" costs
# the same whether the history holds a day or three months of samples. (Ranking
# every row with ROW_NUMBER() instead reads the whole player_counts table.)
_LATEST_SAMPLE_ID = """(
    SELECT id FROM player_counts
    WHERE appid = g.appid
    ORDER BY timestamp DESC, id DESC
    LIMIT 1
)"""


def get_top_games(limit=100):
    conn = get_connection()
    rows = conn.execute(f"""
        SELECT
            g.appid,
            g.name,
            g.thumbnail_url,
            g.header_image_url,
            p.peak_24h,
            p.all_time_peak,
            pc.current_players,
            pc.timestamp
        FROM games g
        JOIN player_counts pc ON pc.id = {_LATEST_SAMPLE_ID}
        LEFT JOIN peaks p ON g.appid = p.appid
        ORDER BY pc.current_players DESC, g.appid
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_game_detail(appid):
    conn = get_connection()
    row = conn.execute("""
        SELECT g.appid, g.name, g.thumbnail_url, g.header_image_url,
               p.peak_24h, p.all_time_peak
        FROM games g
        LEFT JOIN peaks p ON g.appid = p.appid
        WHERE g.appid = ?
    """, (appid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_player_history(appid, hours=24):
    since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    conn = get_connection()
    rows = conn.execute("""
        SELECT current_players, timestamp
        FROM player_counts
        WHERE appid = ? AND timestamp >= ?
        ORDER BY timestamp ASC
    """, (appid, since)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_peak_players(appid, hours=24):
    """Highest sampled player count in the last `hours` hours, or None if there are no samples."""
    since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
    conn = get_connection()
    row = conn.execute("""
        SELECT MAX(current_players) AS peak
        FROM player_counts
        WHERE appid = ? AND timestamp >= ?
    """, (appid, since)).fetchone()
    conn.close()
    return row["peak"]


def _sparkline(conn, appid, points):
    since = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    rows = conn.execute("""
        SELECT current_players
        FROM player_counts
        WHERE appid = ? AND timestamp >= ?
        ORDER BY timestamp ASC
    """, (appid, since)).fetchall()
    values = [r["current_players"] for r in rows]
    if len(values) <= points:
        return values
    step = len(values) / points
    return [values[int(i * step)] for i in range(points)]


def _player_count_yesterday(conn, appid):
    t_start = (datetime.utcnow() - timedelta(hours=28)).isoformat()
    t_end = (datetime.utcnow() - timedelta(hours=20)).isoformat()
    row = conn.execute("""
        SELECT AVG(current_players) as avg_players
        FROM player_counts
        WHERE appid = ? AND timestamp BETWEEN ? AND ?
    """, (appid, t_start, t_end)).fetchone()
    return row["avg_players"] if row and row["avg_players"] else None


def get_sparkline_data(appid, points=24):
    with closing(get_connection()) as conn:
        return _sparkline(conn, appid, points)


def get_player_count_yesterday(appid):
    with closing(get_connection()) as conn:
        return _player_count_yesterday(conn, appid)


def get_trend_data(appids, points=24):
    """Yesterday's average and the 24h sparkline for many games at once.

    Returns {appid: {"yesterday": float | None, "sparkline": [int, ...]}}. It
    answers the same questions as get_player_count_yesterday() and
    get_sparkline_data() but over a single connection: opening one per game
    made /api/top spend ~0.5s on connection setup alone for 100 games.
    """
    with closing(get_connection()) as conn:
        return {
            appid: {
                "yesterday": _player_count_yesterday(conn, appid),
                "sparkline": _sparkline(conn, appid, points),
            }
            for appid in appids
        }


def search_games(query, limit=20):
    conn = get_connection()
    rows = conn.execute(f"""
        SELECT g.appid, g.name, g.thumbnail_url,
               pc.current_players
        FROM games g
        LEFT JOIN player_counts pc ON pc.id = {_LATEST_SAMPLE_ID}
        WHERE g.name LIKE ?
        ORDER BY pc.current_players DESC, g.appid
        LIMIT ?
    """, (f"%{query}%", limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_total_players():
    conn = get_connection()
    row = conn.execute(f"""
        SELECT SUM(pc.current_players) as total
        FROM games g
        JOIN player_counts pc ON pc.id = {_LATEST_SAMPLE_ID}
    """).fetchone()
    conn.close()
    return row["total"] if row and row["total"] else 0


def get_tracked_game_count():
    conn = get_connection()
    row = conn.execute("SELECT COUNT(*) as cnt FROM games").fetchone()
    conn.close()
    return row["cnt"]


def cleanup_old_data(days=90):
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    conn = get_connection()
    conn.execute("DELETE FROM player_counts WHERE timestamp < ?", (cutoff,))
    conn.commit()
    conn.close()
