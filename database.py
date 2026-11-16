import sqlite3
import os
from contextlib import closing
from datetime import datetime, timedelta, timezone

DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "steam.db")


def utcnow():
    """Current UTC time as a naive datetime.

    Every timestamp in the database is a naive-UTC ISO 8601 string (the format
    datetime.utcnow().isoformat() produced), so they sort and compare as text.
    datetime.utcnow() itself is deprecated since Python 3.12.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


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

        -- Games seen in Steam's "most played" chart and when they were last in it.
        CREATE TABLE IF NOT EXISTS top_chart (
            appid INTEGER PRIMARY KEY,
            last_seen TEXT NOT NULL,
            FOREIGN KEY (appid) REFERENCES games(appid)
        );
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
        timestamp = utcnow().isoformat()
    conn = get_connection()
    conn.execute(
        "INSERT INTO player_counts (appid, current_players, timestamp) VALUES (?, ?, ?)",
        (appid, current_players, timestamp),
    )
    conn.commit()
    conn.close()


_UPSERT_PEAKS = """
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
"""


def upsert_peaks(appid, peak_24h, all_time_peak):
    now = utcnow().isoformat()
    conn = get_connection()
    conn.execute(_UPSERT_PEAKS, (appid, peak_24h, all_time_peak, now))
    conn.commit()
    conn.close()


def placeholder_name(appid):
    """Name a game carries until the store has told us its real one."""
    return f"App {appid}"


def _ensure_games(conn, appids):
    conn.executemany(
        "INSERT OR IGNORE INTO games (appid, name) VALUES (?, ?)",
        [(appid, placeholder_name(appid)) for appid in appids],
    )


def ensure_games(appids):
    """Create a row with a placeholder name for every game that has none yet."""
    with closing(get_connection()) as conn, conn:
        _ensure_games(conn, appids)


def _record_samples(conn, samples, timestamp):
    day_before = (datetime.fromisoformat(timestamp) - timedelta(hours=24)).isoformat()
    for appid, current_players, reported_peak in samples:
        conn.execute(
            "INSERT INTO player_counts (appid, current_players, timestamp) VALUES (?, ?, ?)",
            (appid, current_players, timestamp),
        )
        # The 24h peak is the highest of our own samples from the last 24 hours
        # (the one just inserted included), never simply the latest count...
        sampled_peak = conn.execute("""
            SELECT MAX(current_players) FROM player_counts
            WHERE appid = ? AND timestamp >= ?
        """, (appid, day_before)).fetchone()[0]
        # ...or Steam's own figure when it is higher: we only sample every few
        # minutes and may have been tracking the game for less than a day.
        peak_24h = max(sampled_peak, reported_peak or 0)
        # The upsert only ever raises all_time_peak, so the 24h peak is passed for both.
        conn.execute(_UPSERT_PEAKS, (appid, peak_24h, peak_24h, timestamp))


def record_samples(samples, timestamp):
    """Store one scrape result per game and bring its peaks up to date.

    `samples` is an iterable of (appid, current_players, reported_peak), where
    reported_peak is Steam's own peak for the day if it is known, else None.
    Everything is written in one transaction.
    """
    with closing(get_connection()) as conn, conn:
        _record_samples(conn, samples, timestamp)


def record_chart(entries, timestamp):
    """Store a snapshot of Steam's top chart (see scraper.fetch_top_chart).

    Creates rows for games seen for the first time, notes that every listed
    game was in the chart at `timestamp`, and records the samples. It is one
    transaction, so the dashboard moves from the previous snapshot to this one
    in a single step.
    """
    with closing(get_connection()) as conn, conn:
        _ensure_games(conn, [entry["appid"] for entry in entries])
        conn.executemany("""
            INSERT INTO top_chart (appid, last_seen) VALUES (?, ?)
            ON CONFLICT(appid) DO UPDATE SET last_seen = excluded.last_seen
        """, [(entry["appid"], timestamp) for entry in entries])
        _record_samples(
            conn,
            [(entry["appid"], entry["current_players"], entry["peak_today"]) for entry in entries],
            timestamp,
        )


def get_recently_charted(days):
    """Appids of the games that were in Steam's top chart during the last `days` days."""
    since = (utcnow() - timedelta(days=days)).isoformat()
    with closing(get_connection()) as conn:
        rows = conn.execute(
            "SELECT appid FROM top_chart WHERE last_seen >= ? ORDER BY appid", (since,)
        ).fetchall()
    return [r["appid"] for r in rows]


# A game is "live" while its newest sample is at most this much older than the
# newest sample of any game, i.e. while it took part in one of the last three
# scrape cycles. Games that are no longer tracked (they left Steam's top chart
# long ago) or whose requests keep failing drop out of the dashboard instead of
# showing an old count as if it were current.
LIVE_WINDOW_MINUTES = 15


def _latest_sample_time(conn):
    # One index seek per game; MAX(timestamp) over player_counts would read every row.
    row = conn.execute("""
        SELECT MAX(newest) AS newest
        FROM (
            SELECT (SELECT MAX(timestamp) FROM player_counts WHERE appid = g.appid) AS newest
            FROM games g
        )
    """).fetchone()
    return row["newest"]


def _live_since(conn):
    """Oldest timestamp a game's newest sample may have for the game to be live.

    It is measured from the newest sample in the database, not from the clock:
    when the scraper has been down for a while the dashboard keeps showing the
    last complete snapshot (and says how old it is) instead of going blank.
    Returns None while there are no samples at all.
    """
    newest = _latest_sample_time(conn)
    if newest is None:
        return None
    return (datetime.fromisoformat(newest) - timedelta(minutes=LIVE_WINDOW_MINUTES)).isoformat()


# Correlated subquery selecting the id of a live game's newest sample. It is a
# single seek in idx_player_counts_appid_ts per game, so "latest count per game"
# costs the same whether the history holds a day or three months of samples.
# (Ranking every row with ROW_NUMBER() instead reads the whole player_counts table.)
_LIVE_SAMPLE_ID = """(
    SELECT id FROM player_counts
    WHERE appid = g.appid AND timestamp >= :live_since
    ORDER BY timestamp DESC, id DESC
    LIMIT 1
)"""


def get_top_games(limit=100):
    """The live games with the most players right now, most played first."""
    with closing(get_connection()) as conn:
        live_since = _live_since(conn)
        if live_since is None:
            return []
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
            JOIN player_counts pc ON pc.id = {_LIVE_SAMPLE_ID}
            LEFT JOIN peaks p ON g.appid = p.appid
            ORDER BY pc.current_players DESC, g.appid
            LIMIT :limit
        """, {"live_since": live_since, "limit": limit}).fetchall()
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
    since = (utcnow() - timedelta(hours=hours)).isoformat()
    conn = get_connection()
    rows = conn.execute("""
        SELECT current_players, timestamp
        FROM player_counts
        WHERE appid = ? AND timestamp >= ?
        ORDER BY timestamp ASC
    """, (appid, since)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _sparkline(conn, appid, points):
    since = (utcnow() - timedelta(hours=24)).isoformat()
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
    t_start = (utcnow() - timedelta(hours=28)).isoformat()
    t_end = (utcnow() - timedelta(hours=20)).isoformat()
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
    """Live games whose name contains `query`, most played first."""
    with closing(get_connection()) as conn:
        live_since = _live_since(conn)
        if live_since is None:
            return []
        rows = conn.execute(f"""
            SELECT g.appid, g.name, g.thumbnail_url,
                   pc.current_players
            FROM games g
            JOIN player_counts pc ON pc.id = {_LIVE_SAMPLE_ID}
            WHERE g.name LIKE :pattern
            ORDER BY pc.current_players DESC, g.appid
            LIMIT :limit
        """, {"live_since": live_since, "pattern": f"%{query}%", "limit": limit}).fetchall()
    return [dict(r) for r in rows]


def _live_totals(conn):
    live_since = _live_since(conn)
    if live_since is None:
        return 0, 0
    row = conn.execute(f"""
        SELECT COUNT(*) AS games, COALESCE(SUM(pc.current_players), 0) AS players
        FROM games g
        JOIN player_counts pc ON pc.id = {_LIVE_SAMPLE_ID}
    """, {"live_since": live_since}).fetchone()
    return row["games"], row["players"]


def get_total_players():
    """Players currently in all live games together."""
    with closing(get_connection()) as conn:
        return _live_totals(conn)[1]


def get_tracked_game_count():
    """Number of live games, i.e. games the dashboard currently has a count for."""
    with closing(get_connection()) as conn:
        return _live_totals(conn)[0]


def get_latest_sample_time():
    """Timestamp of the newest sample of any game, or None before the first scrape.

    This is when the dashboard's data was last refreshed.
    """
    with closing(get_connection()) as conn:
        return _latest_sample_time(conn)


def cleanup_old_data(days=90):
    cutoff = (utcnow() - timedelta(days=days)).isoformat()
    conn = get_connection()
    conn.execute("DELETE FROM player_counts WHERE timestamp < ?", (cutoff,))
    conn.commit()
    conn.close()
