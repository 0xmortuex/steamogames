# SteamOgames

Live Steam game player statistics — track concurrent players, peaks, and trends with a modern dark-themed dashboard.

![Screenshot](screenshot.png)

## Features

- **Top 100 most played** Steam games with live concurrent player counts
- **24-hour peak** and **all-time peak** player tracking
- **Player trend** percentage (up/down vs yesterday)
- **Sparkline charts** showing 24h player trends per game
- **Game detail modal** with interactive Chart.js player history (24h / 7d / 30d)
- **Instant search** filtering with API-backed results
- **Auto-refresh** every 5 minutes
- Dark theme with glassmorphism effects

## Tech Stack

- **Backend**: Python / Flask
- **Frontend**: Vanilla HTML / CSS / JS
- **Charts**: Chart.js
- **Database**: SQLite (auto-created)
- **Scheduler**: APScheduler

## Setup

```bash
# Clone the repo
git clone https://github.com/youruser/steamogames.git
cd steamogames

# Create a virtual environment (optional but recommended)
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set your Steam API key
cp .env.example .env
# Edit .env and add your key: STEAM_API_KEY=your_key_here

# Run the app
python app.py
```

Open [http://localhost:5000](http://localhost:5000) in your browser.

The scraper runs automatically on startup and refreshes every 5 minutes.

### Standalone scraper

You can also run the scraper independently:

```bash
python scraper.py
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Main dashboard page |
| `GET` | `/api/top?limit=100` | Top games with current players, peaks, trends, sparklines |
| `GET` | `/api/game/<appid>?hours=24` | Game detail + player count history |
| `GET` | `/api/search?q=term` | Search games by name |

### Example response: `/api/top`

```json
{
  "games": [
    {
      "appid": 730,
      "name": "Counter-Strike 2",
      "current_players": 1200000,
      "peak_24h": 1400000,
      "all_time_peak": 1800000,
      "trend": 5.2,
      "sparkline": [1100000, 1150000, ...]
    }
  ],
  "total_players": 30000000,
  "tracked_count": 100
}
```

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `STEAM_API_KEY` | Your Steam Web API key | (required for some endpoints) |
| `PORT` | Server port | `5000` |

Get a Steam API key at [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey).

## License

MIT
