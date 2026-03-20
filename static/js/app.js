// ===== STATE =====
let allGames = [];
let currentSort = { key: "current_players", dir: "desc" };
let playerChart = null;
let currentModalAppId = null;

// ===== INIT =====
document.addEventListener("DOMContentLoaded", () => {
    loadTopGames();
    setupSearch();
    setupModal();
    setupSorting();

    // Auto-refresh every 5 minutes
    setInterval(loadTopGames, 5 * 60 * 1000);
});

// ===== DATA LOADING =====
async function loadTopGames() {
    try {
        const resp = await fetch("/api/top");
        const data = await resp.json();
        allGames = data.games || [];
        renderStats(data);
        renderTable(allGames);
    } catch (err) {
        console.error("Failed to load games:", err);
    }
}

// ===== STATS BAR =====
function renderStats(data) {
    animateNumber("totalPlayers", data.total_players || 0);
    animateNumber("trackedGames", data.tracked_count || 0);
    document.getElementById("lastUpdated").textContent = new Date().toLocaleTimeString();
}

function animateNumber(elementId, target) {
    const el = document.getElementById(elementId);
    const start = parseInt(el.textContent.replace(/,/g, "")) || 0;
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

// ===== TABLE RENDERING =====
function renderTable(games) {
    const tbody = document.getElementById("gameTableBody");

    if (!games.length) {
        tbody.innerHTML = `
            <tr class="loading-row">
                <td colspan="7">No games loaded yet. Data will appear after the first scrape cycle.</td>
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
        const thumb = game.thumbnail_url || "";
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

            // Update active state
            document.querySelectorAll(".sortable").forEach(el => el.classList.remove("active"));
            th.classList.add("active");
            const arrow = th.querySelector(".sort-arrow");
            if (arrow) arrow.textContent = currentSort.dir === "desc" ? "▼" : "▲";

            renderTable(allGames);
        });
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

        if (q.length < 2) {
            results.classList.remove("active");
            // Also filter main table instantly
            if (q.length === 0) renderTable(allGames);
            return;
        }

        // Instant local filter on main table
        const filtered = allGames.filter(g =>
            (g.name || "").toLowerCase().includes(q.toLowerCase())
        );
        renderTable(filtered);

        // Debounced API search for games not in top list
        debounce = setTimeout(async () => {
            try {
                const resp = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
                const data = await resp.json();
                renderSearchResults(data.results || []);
            } catch (err) {
                console.error("Search error:", err);
            }
        }, 300);
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
            input.value = "";
            results.classList.remove("active");
            renderTable(allGames);
        }
    });
}

function renderSearchResults(items) {
    const container = document.getElementById("searchResults");
    if (!items.length) {
        container.classList.remove("active");
        return;
    }

    container.innerHTML = items.map(item => `
        <div class="search-result-item" data-appid="${item.appid}">
            <img src="${item.thumbnail_url || ""}" alt=""
                 onerror="this.style.display='none'">
            <span class="sr-name">${escapeHtml(item.name || `App ${item.appid}`)}</span>
            <span class="sr-players">${(item.current_players || 0).toLocaleString()}</span>
        </div>
    `).join("");
    container.classList.add("active");

    container.querySelectorAll(".search-result-item").forEach(el => {
        el.addEventListener("click", () => {
            openModal(parseInt(el.dataset.appid));
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

async function openModal(appid) {
    currentModalAppId = appid;
    const overlay = document.getElementById("modalOverlay");
    overlay.classList.add("active");
    document.body.style.overflow = "hidden";

    // Reset chart buttons to 24h
    document.querySelectorAll(".chart-btn").forEach(b => b.classList.remove("active"));
    document.querySelector('.chart-btn[data-hours="24"]').classList.add("active");

    try {
        const resp = await fetch(`/api/game/${appid}?hours=24`);
        const data = await resp.json();
        const game = data.game;

        document.getElementById("modalGameName").textContent = game.name || `App ${appid}`;
        document.getElementById("modalCurrentPlayers").textContent =
            (game.current_players || 0).toLocaleString();
        document.getElementById("modalPeak24h").textContent =
            (game.peak_24h || 0).toLocaleString();
        document.getElementById("modalAllTimePeak").textContent =
            (game.all_time_peak || 0).toLocaleString();

        const headerImg = document.getElementById("modalHeaderImage");
        if (game.header_image_url) {
            headerImg.src = game.header_image_url;
            headerImg.style.display = "block";
        } else {
            headerImg.style.display = "none";
        }

        document.getElementById("modalStoreLink").href =
            `https://store.steampowered.com/app/${appid}`;

        renderChart(data.history || []);
    } catch (err) {
        console.error("Failed to load game detail:", err);
    }
}

function closeModal() {
    document.getElementById("modalOverlay").classList.remove("active");
    document.body.style.overflow = "";
    currentModalAppId = null;
}

async function loadGameChart(appid, hours) {
    try {
        const resp = await fetch(`/api/game/${appid}?hours=${hours}`);
        const data = await resp.json();
        renderChart(data.history || []);
    } catch (err) {
        console.error("Chart load error:", err);
    }
}

function renderChart(history) {
    const canvas = document.getElementById("playerChart");
    const ctx = canvas.getContext("2d");

    if (playerChart) {
        playerChart.destroy();
    }

    if (!history.length) {
        playerChart = null;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = "#555570";
        ctx.font = "14px Inter";
        ctx.textAlign = "center";
        ctx.fillText("No history data yet", canvas.width / 2, canvas.height / 2);
        return;
    }

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
                pointRadius: 0,
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
function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}
