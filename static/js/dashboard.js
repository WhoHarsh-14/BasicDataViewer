/**
 * dashboard.js — Dynamic live monitoring page
 */

let liveChart = null;
const MAX_CHART_POINTS = 150;

// ══════════════════════════════════════
// INIT
// ══════════════════════════════════════
function initDashboard() {
    // Build I/O grids
    buildIOGrid('x-inputs-grid', 'X', 18);
    buildIOGrid('y-outputs-grid', 'Y', 18);

    // Init chart
    initLiveChart();

    // Clear feed button
    document.getElementById('btn-clear-feed').addEventListener('click', () => {
        document.getElementById('activity-feed').innerHTML = '';
        APP.feedItems = [];
    });
}

// Global hook to clear dashboard when config changes
window.initDynamicDashboard = function() {
    const statsGrid = document.getElementById('dashboard-stats-grid');
    if (statsGrid) {
        const cards = statsGrid.querySelectorAll('.stat-card');
        cards.forEach(card => {
            if (card.id !== 'stat-status') card.remove();
        });
    }
    if (liveChart) {
        liveChart.data.datasets = [];
        liveChart.update();
    }
};

// ══════════════════════════════════════
// I/O GRID
// ══════════════════════════════════════
function buildIOGrid(containerId, prefix, count) {
    const grid = document.getElementById(containerId);
    grid.innerHTML = '';
    for (let i = 0; i < count; i++) {
        const bit = document.createElement('div');
        bit.className = 'io-bit';
        bit.id = `bit-${prefix.toLowerCase()}${i}`;
        bit.innerHTML = `
            <span class="bit-label">${prefix}${i}</span>
            <span class="bit-dot"></span>
        `;
        grid.appendChild(bit);
    }
}


// ══════════════════════════════════════
// LIVE CHART
// ══════════════════════════════════════
function initLiveChart() {
    const ctx = document.getElementById('live-chart');
    if (!ctx) return;

    liveChart = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: [], // Datasets populated dynamically
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 0 }, // disabled for 20ms performance
            interaction: {
                intersect: false,
                mode: 'index',
            },
            scales: {
                x: {
                    type: 'time',
                    time: {
                        unit: 'second',
                        displayFormats: { second: 'HH:mm:ss' },
                    },
                    grid: {
                        color: 'rgba(255,255,255,0.04)',
                    },
                    ticks: {
                        color: '#64748b',
                        font: { family: 'JetBrains Mono', size: 10 },
                        maxTicksLimit: 8,
                    },
                },
                y: {
                    grid: {
                        color: 'rgba(255,255,255,0.04)',
                    },
                    ticks: {
                        color: '#64748b',
                        font: { family: 'JetBrains Mono', size: 10 },
                    },
                },
            },
            plugins: {
                legend: {
                    labels: {
                        color: '#94a3b8',
                        font: { family: 'Inter', size: 11 },
                        usePointStyle: true,
                        pointStyle: 'circle',
                    },
                },
                tooltip: {
                    backgroundColor: 'rgba(10, 14, 26, 0.9)',
                    borderColor: 'rgba(255,255,255,0.1)',
                    borderWidth: 1,
                    titleFont: { family: 'JetBrains Mono', size: 11 },
                    bodyFont: { family: 'JetBrains Mono', size: 11 },
                    padding: 10,
                    cornerRadius: 8,
                },
            },
        },
    });
}


// ══════════════════════════════════════
// HANDLE LIVE DATA (called from WebSocket)
// ══════════════════════════════════════
function handleLiveData(data) {
    const ts = new Date(data.timestamp);

    // ── Status card ──
    const statusCard = document.getElementById('stat-status');
    const statusText = document.getElementById('plc-status-text');
    if (statusText) statusText.textContent = data.status;
    if (statusCard) statusCard.className = 'stat-card ' + (data.status === 'OK' ? 'status-ok' : 'status-dc');

    if (data.status !== 'OK') {
        // Clear inputs on disconnect
        for (let i = 0; i < 18; i++) {
            const xi = document.getElementById(`bit-x${i}`);
            const yi = document.getElementById(`bit-y${i}`);
            if (xi) xi.className = 'io-bit off';
            if (yi) yi.className = 'io-bit off';
        }
        return;
    }

    // ── Dynamic Stat Cards ──
    const statsGrid = document.getElementById('dashboard-stats-grid');
    const activeAddresses = [];

    (data.d_registers || []).forEach(r => {
        activeAddresses.push(r.address);
        let valEl = document.getElementById(`val-${r.address}`);
        if (!valEl && statsGrid) {
            // Create new stat card
            const card = document.createElement('div');
            card.className = 'stat-card';
            card.id = `card-${r.address}`;
            card.innerHTML = `
                <div class="stat-icon icon-cyan" style="color: var(--cyan); background: var(--cyan-glow);">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18">
                        <rect x="2" y="3" width="20" height="14" rx="2"/>
                        <line x1="8" y1="21" x2="16" y2="21"/>
                        <line x1="12" y1="17" x2="12" y2="21"/>
                    </svg>
                </div>
                <div class="stat-info">
                    <span class="stat-label">${r.name || r.address}</span>
                    <span class="stat-value mono" id="val-${r.address}">—</span>
                </div>
            `;
            statsGrid.appendChild(card);
            valEl = document.getElementById(`val-${r.address}`);
        }
        if (valEl) {
            valEl.textContent = r.value !== null ? r.value : '—';
        }
    });

    // Remove obsolete stat cards
    if (statsGrid) {
        const cards = statsGrid.querySelectorAll('.stat-card');
        cards.forEach(card => {
            if (card.id !== 'stat-status' && !activeAddresses.some(addr => `card-${addr}` === card.id)) {
                card.remove();
            }
        });
    }

    // ── X Inputs ──
    (data.x_inputs || []).forEach(bit => {
        const el = document.getElementById(`bit-x${bit.address.replace('X', '')}`);
        if (el) el.className = 'io-bit ' + (bit.value ? 'on' : 'off');
    });

    // ── Y Outputs ──
    (data.y_outputs || []).forEach(bit => {
        const el = document.getElementById(`bit-y${bit.address.replace('Y', '')}`);
        if (el) el.className = 'io-bit ' + (bit.value ? 'on' : 'off');
    });

    // ── Live Chart ──
    if (liveChart) {
        (data.d_registers || []).forEach(r => {
            if (r.data_type === 'string') return; // Skip non-numeric values
            
            // Find or create chart dataset
            let dataset = liveChart.data.datasets.find(ds => ds.label === r.address);
            if (!dataset) {
                const colors = ['#f87171', '#34d399', '#60a5fa', '#a78bfa', '#fbbf24', '#22d3ee'];
                const color = colors[liveChart.data.datasets.length % colors.length];
                dataset = {
                    label: r.address,
                    data: [],
                    borderColor: color,
                    backgroundColor: color + '0f', // 10% opacity glow
                    borderWidth: 2,
                    pointRadius: 0,
                    tension: 0.3,
                    fill: true,
                };
                liveChart.data.datasets.push(dataset);
            }

            dataset.data.push({ x: ts, y: Number(r.value) });
        });

        // Trim old points
        liveChart.data.datasets.forEach(ds => {
            if (ds.data.length > MAX_CHART_POINTS) {
                ds.data = ds.data.slice(-MAX_CHART_POINTS);
            }
        });

        liveChart.update('none'); // Update without animation for 20ms performance
    }

    // ── Activity Feed ──
    addFeedItem(data);

    // ── Production Lines Live Render ──
    if (window.APP && window.APP.currentPage === 'lines' && window.renderLinesLiveData) {
        window.renderLinesLiveData(data);
    }
}


// ══════════════════════════════════════
// ACTIVITY FEED
// ══════════════════════════════════════
function addFeedItem(data) {
    const feed = document.getElementById('activity-feed');
    if (!feed) return;

    const ts = new Date(data.timestamp);
    const timeStr = ts.toLocaleTimeString('en-IN', { hour12: false });

    let valuesStr = '';
    if (data.status === 'OK' && data.d_registers) {
        valuesStr = data.d_registers.map(r => `${r.address}:${r.value ?? '-'}`).slice(0, 5).join(' ');
        if (data.d_registers.length > 5) valuesStr += ' ...';
    }

    const item = document.createElement('div');
    item.className = 'feed-item';
    item.innerHTML = `
        <span class="feed-dot ${data.status === 'OK' ? 'ok' : 'err'}"></span>
        <span class="feed-time">${timeStr}</span>
        <span class="feed-text">${data.status === 'OK' ? 'Poll cycle complete' : 'Connection lost'}</span>
        ${data.status === 'OK'
            ? `<span class="feed-values">${valuesStr}</span>`
            : ''
        }
    `;

    feed.insertBefore(item, feed.firstChild);

    APP.feedItems.push(item);
    if (APP.feedItems.length > 30) { // Slower DOM footprint
        const old = APP.feedItems.shift();
        old.remove();
    }
}
