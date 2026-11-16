// ===== STATE =====
let allGames = [];
let currentSort = { key: "current_players", dir: "desc" };
let playerChart = null;
let currentModalAppId = null;

let lastData = null;      // the last /api/top answer that loaded
let lastLoadedAt = null;  // Date.now() when it loaded
let loadError = null;     // why the latest refresh failed, or null
let refreshTimer = null;

// Each counter identifies the newest request of its kind, so that an answer
// arriving late (after the user moved on) is dropped instead of rendered.
let modalRequest = 0;
let chartRequest = 0;
let searchRequest = 0;

const REFRESH_MS = 5 * 60 * 1000;       // the scraper stores new samples every 5 minutes
const SETTLING_REFRESH_MS = 20 * 1000;  // while the first counts and names are still arriving
const RETRY_MS = 30 * 1000;             // after a refresh that failed
const STALE_AFTER_MS = 15 * 60 * 1000;  // three scrape cycles without a new sample

// ===== INIT =====
document.addEventListener("DOMContentLoaded", () => {
    setupSearch();
    setupModal();
    setupSorting();
    loadTopGames();
});

// ===== DATA LOADING =====
// Fetch JSON from the API. Every failure becomes an Error whose message can be
// shown to the user as it is.
async function fetchJson(url) {
    let resp;
    try {
        resp = await fetch(url);
    } catch (err) {
        throw new Error("the SteamOgames server cannot be reached");
    }
    if (!resp.ok) {
        const isJson = (resp.headers.get("content-type") || "").includes("application/json");
        const body = isJson ? await resp.json() : null;
        throw new Error(body && body.error ? body.error : `the server answered HTTP ${resp.status}`);
    }
    return resp.json();
}

async function loadTopGames() {
    clearTimeout(refreshTimer);
    let nextRefresh = REFRESH_MS;

    try {
        const data = await fetchJson("/api/top");
        lastData = data;
        lastLoadedAt = Date.now();
        loadError = null;
        allGames = data.games || [];
        renderStats(data);
        renderCurrentView();
        if (isSettling(data)) nextRefresh = SETTLING_REFRESH_MS;
    } catch (err) {
        console.error("Failed to load games:", err);
        loadError = err.message;
        nextRefresh = RETRY_MS;
        // Without earlier data there is nothing to keep on screen: end the spinner.
        if (!lastData) renderTable([], "No data loaded.");
    }

    renderStatus();
    refreshTimer = setTimeout(loadTopGames, nextRefresh);
}

// Right after the first start the scraper is still collecting counts and
// looking up names ("App 123" placeholders). Refresh faster until that is done.
function isSettling(data) {
    const scraper = data.scraper || {};
    if (!scraper.enabled) return false;
    if (allGames.length === 0) return true;
    return Boolean(scraper.running) && allGames.some(g => g.name === `App ${g.appid}`);
}

// ===== STATS BAR =====
function renderStats(data) {
    animateNumber("totalPlayers", data.total_players || 0);
    animateNumber("trackedGames", data.tracked_count || 0);

    // When the data was last refreshed by the scraper, not when this page asked for it.
    const el = document.getElementById("lastUpdated");
    const updated = parseUtc(data.updated_at);
    if (!updated) {
        el.textContent = "—";
        el.removeAttribute("title");
        return;
    }
    const sameDay = updated.toDateString() === new Date().toDateString();
    el.textContent = sameDay
        ? updated.toLocaleTimeString()
        : updated.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    el.title = `Newest sample: ${updated.toLocaleString()}`;
}

function animateNumber(elementId, target) {
    const el = document.getElementById(elementId);
    // The shown text is formatted for the user's locale, so remember the number itself.
    const start = Number(el.dataset.value) || 0;
    el.dataset.value = target;
    const diff = target - start;
    const duration = 800;
    const startTime = performance.now();

    function tick(now) {
        const elapsed = now - startTime;
        const progress = Math.min(elapsed / duration, 1);
        // Ease out cubic
        const eased = 1 - Math.pow(1 - progress, 3);
        const current = Math.round(start + diff * eased);
        el.textContent = current.toLocaleString();
        if (progress < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
}

// ===== STATUS BANNER =====
// Everything the user should know about the data being wrong, old or
// incomplete is listed here. With nothing to report the banner is hidden.
function renderStatus() {
    const messages = [];

    if (loadError && lastData) {
        const loaded = new Date(lastLoadedAt).toLocaleTimeString();
        messages.push(`Refresh failed: ${loadError}. Still showing what was loaded at ${loaded}; retrying every 30 seconds.`);
    } else if (loadError) {
        messages.push(`Could not load games: ${loadError}. Retrying every 30 seconds.`);
    }

    let stale = false;
    if (lastData) {
        const age = dataAgeMs();
        const scraper = lastData.scraper || {};
        if (age !== null && age > STALE_AFTER_MS) {
            stale = true;
            const since = parseUtc(lastData.updated_at).toLocaleString();
            messages.push(scraper.enabled === false
                ? `The newest data is ${formatAge(age)} old (stored ${since}). This server runs without its scraper (SCRAPER_ENABLED=0), so check that "python scraper.py" is running.`
                : `The newest data is ${formatAge(age)} old (stored ${since}). The scraper has not stored anything since; check the server log.`);
        }
        if (scraper.error) {
            messages.push(`Last scrape: ${scraper.error}`);
        }
    }

    document.getElementById("lastUpdated").classList.toggle("stat-stale", stale);

    const banner = document.getElementById("statusBanner");
    banner.replaceChildren(...messages.map(text => {
        const p = document.createElement("p");
        p.textContent = text;
        return p;
    }));
    banner.hidden = messages.length === 0;
}

// Age of the newest sample. The server reports both its own clock and the
// sample time, so a wrong clock on this device cannot fake fresh or stale data;
// time that passed since that answer is added from this device's clock.
function dataAgeMs() {
    const updated = parseUtc(lastData.updated_at);
    const serverNow = parseUtc(lastData.server_time);
    if (!updated || !serverNow) return null;
    return (serverNow - updated) + (Date.now() - lastLoadedAt);
}

function formatAge(ms) {
    const minutes = Math.round(ms / 60000);
    if (minutes < 60) return `${minutes} minutes`;
    const hours = Math.round(minutes / 60);
    if (hours < 48) return hours === 1 ? "1 hour" : `${hours} hours`;
    return `${Math.round(hours / 24)} days`;
}

// The API sends UTC timestamps without an offset ("2026-11-16T11:43:31.446602").
function parseUtc(timestamp) {
    if (!timestamp) return null;
    const date = new Date(timestamp + "Z");
    return Number.isNaN(date.getTime()) ? null : date;
}

// ===== TABLE RENDERING =====
function searchQuery() {
    return document.getElementById("searchInput").value.trim().toLowerCase();
}

// Render the table for the current search text and sort order. Every refresh,
// sort click and keystroke goes through here, so none of them undoes another.
function renderCurrentView() {
    const query = searchQuery();
    if (query.length >= 2) {
        const matches = allGames.filter(g => (g.name || "").toLowerCase().includes(query));
        renderTable(matches, `No game in the list matches "${query}".`);
    } else if (lastData) {
        renderTable(allGames, "No games loaded yet. Data will appear after the first scrape cycle.");
    }
}

function renderTable(games, emptyMessage) {
    const tbody = document.getElementById("gameTableBody");

    if (!games.length) {
        tbody.innerHTML = `
            <tr class="loading-row">
                <td colspan="7">${escapeHtml(emptyMessage)}</td>
            </tr>`;
        return;
    }

    // Sort
    const sorted = [...games].sort((a, b) => {
        let va = a[currentSort.key] ?? -Infinity;
        let vb = b[currentSort.key] ?? -Infinity;
        return currentSort.dir === "desc" ? vb - va : va - vb;
    });

    tbody.innerHTML = sorted.map((game, i) => {
        const rank = i + 1;
        const thumb = escapeHtml(game.thumbnail_url || "");
        const name = escapeHtml(game.name || `App ${game.appid}`);
        const current = (game.current_players || 0).toLocaleString();
        const peak24 = (game.peak_24h || 0).toLocaleString();
        const allTime = (game.all_time_peak || 0).toLocaleString();

        let trendHtml = '<span class="trend-neutral">—</span>';
        if (game.trend !== null && game.trend !== undefined) {
            const sign = game.trend >= 0 ? "+" : "";
            const cls = game.trend > 0 ? "trend-up" : game.trend < 0 ? "trend-down" : "trend-neutral";
            const arrow = game.trend > 0 ? "↑" : game.trend < 0 ? "↓" : "";
            trendHtml = `<span class="trend-badge ${cls}">${arrow} ${sign}${game.trend}%</span>`;
        }

        const sparkId = `spark-${game.appid}`;

        return `
        <tr data-appid="${game.appid}">
            <td class="col-rank">${rank}</td>
            <td class="col-game">
                <div class="game-cell">
                    <img class="game-thumb" src="${thumb}" alt="" loading="lazy"
                         onerror="this.style.display='none'">
                    <span class="game-name">${name}</span>
                </div>
            </td>
            <td class="col-players">${current}</td>
            <td class="col-peak">${peak24}</td>
            <td class="col-alltime">${allTime}</td>
            <td class="col-trend">${trendHtml}</td>
            <td class="col-sparkline"><canvas class="sparkline-canvas" id="${sparkId}" width="100" height="32"></canvas></td>
        </tr>`;
    }).join("");

    // Draw sparklines
    sorted.forEach(game => {
        if (game.sparkline && game.sparkline.length > 1) {
            drawSparkline(`spark-${game.appid}`, game.sparkline);
        }
    });
}

function drawSparkline(canvasId, data) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    const min = Math.min(...data);
    const max = Math.max(...data);
    const range = max - min || 1;
    const pad = 2;

    ctx.clearRect(0, 0, w, h);

    const isUp = data[data.length - 1] >= data[0];
    const color = isUp ? "#00ff88" : "#ff4444";

    // Line
    ctx.beginPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    ctx.lineJoin = "round";
    data.forEach((v, i) => {
        const x = (i / (data.length - 1)) * w;
        const y = h - pad - ((v - min) / range) * (h - 2 * pad);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
    });
    ctx.stroke();

    // Fill gradient
    const lastX = w;
    const lastY = h - pad - ((data[data.length - 1] - min) / range) * (h - 2 * pad);
    ctx.lineTo(lastX, h);
    ctx.lineTo(0, h);
    ctx.closePath();
    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, isUp ? "rgba(0,255,136,0.15)" : "rgba(255,68,68,0.15)");
    grad.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = grad;
    ctx.fill();
}

// ===== SORTING =====
function setupSorting() {
    document.querySelectorAll(".sortable").forEach(th => {
        th.addEventListener("click", () => {
            const key = th.dataset.sort;
            if (currentSort.key === key) {
                currentSort.dir = currentSort.dir === "desc" ? "asc" : "desc";
            } else {
                currentSort = { key, dir: "desc" };
            }

            renderSortState();
            renderCurrentView();
        });
    });
    renderSortState();
}

// Highlight the sorted column and show the arrow there (and only there).
function renderSortState() {
    document.querySelectorAll(".sortable").forEach(th => {
        const active = th.dataset.sort === currentSort.key;
        th.classList.toggle("active", active);
        th.querySelector(".sort-arrow").textContent =
            active ? (currentSort.dir === "desc" ? "▼" : "▲") : "";
    });
}

// ===== SEARCH =====
function setupSearch() {
    const input = document.getElementById("searchInput");
    const results = document.getElementById("searchResults");
    let debounce = null;

    input.addEventListener("input", () => {
        clearTimeout(debounce);
        const q = input.value.trim();

        // Instant local filter on main table
        renderCurrentView();

        if (q.length < 2) {
            searchRequest++;  // drop the answer of a search that is still under way
            results.classList.remove("active");
            return;
        }

        // Debounced API search for games not in top list
        debounce = setTimeout(() => runSearch(q), 300);
    });

    input.addEventListener("focus", () => {
        if (results.children.length > 0 && input.value.trim().length >= 2) {
            results.classList.add("active");
        }
    });

    document.addEventListener("click", (e) => {
        if (!e.target.closest(".search-box")) {
            results.classList.remove("active");
        }
    });

    input.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
            clearTimeout(debounce);
            searchRequest++;
            input.value = "";
            results.classList.remove("active");
            renderCurrentView();
        }
    });
}

async function runSearch(q) {
    const request = ++searchRequest;
    const container = document.getElementById("searchResults");
    try {
        const data = await fetchJson(`/api/search?q=${encodeURIComponent(q)}`);
        if (request !== searchRequest) return;
        renderSearchResults(data.results || []);
    } catch (err) {
        console.error("Search error:", err);
        if (request !== searchRequest) return;
        container.innerHTML = `<div class="search-result-message">Search failed: ${escapeHtml(err.message)}.</div>`;
        container.classList.add("active");
    }
}

function renderSearchResults(items) {
    const container = document.getElementById("searchResults");
    if (!items.length) {
        container.innerHTML = "";
        container.classList.remove("active");
        return;
    }

    container.innerHTML = items.map(item => `
        <div class="search-result-item" data-appid="${item.appid}">
            <img src="${escapeHtml(item.thumbnail_url || "")}" alt=""
                 onerror="this.style.display='none'">
            <span class="sr-name">${escapeHtml(item.name || `App ${item.appid}`)}</span>
            <span class="sr-players">${(item.current_players || 0).toLocaleString()}</span>
        </div>
    `).join("");
    container.classList.add("active");

    container.querySelectorAll(".search-result-item").forEach(el => {
        el.addEventListener("click", () => {
            const appid = parseInt(el.dataset.appid);
            openModal(appid, items.find(item => item.appid === appid));
            container.classList.remove("active");
        });
    });
}

// ===== MODAL =====
function setupModal() {
    const overlay = document.getElementById("modalOverlay");
    const closeBtn = document.getElementById("modalClose");

    // Click on table row
    document.getElementById("gameTableBody").addEventListener("click", (e) => {
        const row = e.target.closest("tr[data-appid]");
        if (row) openModal(parseInt(row.dataset.appid));
    });

    closeBtn.addEventListener("click", closeModal);
    overlay.addEventListener("click", (e) => {
        if (e.target === overlay) closeModal();
    });

    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") closeModal();
    });

    // Chart time range buttons
    document.querySelectorAll(".chart-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".chart-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            if (currentModalAppId) {
                loadGameChart(currentModalAppId, parseInt(btn.dataset.hours));
            }
        });
    });
}

// `known` is what the caller already has about the game (a search result).
async function openModal(appid, known) {
    currentModalAppId = appid;
    const request = ++modalRequest;
    const chartLoad = ++chartRequest;
    const overlay = document.getElementById("modalOverlay");
    overlay.classList.add("active");
    document.body.style.overflow = "hidden";

    // Reset chart buttons to 24h
    document.querySelectorAll(".chart-btn").forEach(b => b.classList.remove("active"));
    document.querySelector('.chart-btn[data-hours="24"]').classList.add("active");

    // Until the answer arrives show what is already known about this game,
    // never what is left over from the game opened before.
    renderModalGame(appid, allGames.find(g => g.appid === appid) || known || {});
    showChartMessage("Loading…");

    try {
        const data = await fetchJson(`/api/game/${appid}?hours=24`);
        if (request !== modalRequest) return;
        renderModalGame(appid, data.game);
        if (chartLoad === chartRequest) renderChart(data.history || []);
    } catch (err) {
        console.error("Failed to load game detail:", err);
        if (request !== modalRequest) return;
        showChartMessage(`Could not load this game: ${err.message}.`);
    }
}

function renderModalGame(appid, game) {
    const count = (value) => (value === null || value === undefined) ? "—" : value.toLocaleString();

    document.getElementById("modalGameName").textContent = game.name || `App ${appid}`;
    document.getElementById("modalCurrentPlayers").textContent = count(game.current_players);
    document.getElementById("modalPeak24h").textContent = count(game.peak_24h);
    document.getElementById("modalAllTimePeak").textContent = count(game.all_time_peak);

    const headerImg = document.getElementById("modalHeaderImage");
    if (game.header_image_url) {
        headerImg.src = game.header_image_url;
        headerImg.style.display = "block";
    } else {
        headerImg.removeAttribute("src");
        headerImg.style.display = "none";
    }

    document.getElementById("modalStoreLink").href =
        `https://store.steampowered.com/app/${appid}`;
}

function closeModal() {
    document.getElementById("modalOverlay").classList.remove("active");
    document.body.style.overflow = "";
    currentModalAppId = null;
    // Answers still on their way belong to a modal that is gone.
    modalRequest++;
    chartRequest++;
}

async function loadGameChart(appid, hours) {
    const request = ++chartRequest;
    try {
        const data = await fetchJson(`/api/game/${appid}?hours=${hours}`);
        if (request !== chartRequest) return;
        renderChart(data.history || []);
    } catch (err) {
        console.error("Chart load error:", err);
        if (request !== chartRequest) return;
        showChartMessage(`Could not load the chart: ${err.message}.`);
    }
}

// Replace the chart with a line of text (loading, empty or error state).
function showChartMessage(text) {
    if (playerChart) {
        playerChart.destroy();
        playerChart = null;
    }
    const message = document.getElementById("chartMessage");
    message.textContent = text;
    message.hidden = false;
}

function renderChart(history) {
    if (!history.length) {
        showChartMessage("No history data yet");
        return;
    }
    if (typeof Chart === "undefined") {
        showChartMessage("The chart library (Chart.js from cdn.jsdelivr.net) could not be loaded.");
        return;
    }

    const canvas = document.getElementById("playerChart");
    const ctx = canvas.getContext("2d");

    if (playerChart) {
        playerChart.destroy();
    }
    document.getElementById("chartMessage").hidden = true;

    const labels = history.map(h => {
        const d = new Date(h.timestamp + "Z");
        return d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    });
    const values = history.map(h => h.current_players);

    const gradient = ctx.createLinearGradient(0, 0, 0, 260);
    gradient.addColorStop(0, "rgba(102, 192, 244, 0.25)");
    gradient.addColorStop(1, "rgba(102, 192, 244, 0)");

    playerChart = new Chart(ctx, {
        type: "line",
        data: {
            labels,
            datasets: [{
                label: "Players",
                data: values,
                borderColor: "#66c0f4",
                backgroundColor: gradient,
                borderWidth: 2,
                // A line needs two points; draw the points themselves while there are few.
                pointRadius: values.length < 3 ? 3 : 0,
                pointBackgroundColor: "#66c0f4",
                pointHitRadius: 10,
                fill: true,
                tension: 0.3,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                intersect: false,
                mode: "index",
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: "#1a1a28",
                    borderColor: "#2a2a40",
                    borderWidth: 1,
                    titleFont: { family: "Inter" },
                    bodyFont: { family: "JetBrains Mono" },
                    callbacks: {
                        label: (ctx) => `${ctx.parsed.y.toLocaleString()} players`,
                    },
                },
            },
            scales: {
                x: {
                    grid: { color: "rgba(30,30,48,0.5)" },
                    ticks: {
                        color: "#555570",
                        font: { size: 10 },
                        maxTicksLimit: 8,
                    },
                },
                y: {
                    grid: { color: "rgba(30,30,48,0.5)" },
                    ticks: {
                        color: "#555570",
                        font: { family: "JetBrains Mono", size: 11 },
                        callback: (v) => v >= 1000000 ? (v / 1000000).toFixed(1) + "M"
                                       : v >= 1000 ? (v / 1000).toFixed(0) + "K"
                                       : v,
                    },
                },
            },
        },
    });
}

// ===== UTIL =====
// Escape text for use in HTML, both between tags and inside a quoted attribute.
function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML.replace(/"/g, "&quot;");
}
