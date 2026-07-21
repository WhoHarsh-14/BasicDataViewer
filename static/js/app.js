/**
 * app.js — Core application logic: page routing, WebSocket, clock
 */

// ── Globals ──
window.APP = {
    ws: null,
    wsConnected: false,
    currentPage: 'lines',
    feedItems: [],
    lineCharts: null,
    activeLineId: null,
    activeMachineId: null,
    machineHistoryChart: null,
    activePresetId: 'plc1',
};

// ══════════════════════════════════════
// MULTI-PLC PRESET SWITCHING
// ══════════════════════════════════════
window.switchPLCPreset = async function(presetId) {
    try {
        const resp = await fetch('/api/plc/switch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ preset_id: presetId })
        });
        if (resp.ok) {
            APP.activePresetId = presetId;
            document.querySelectorAll('.plc-preset-btn').forEach(btn => {
                if (btn.dataset.preset === presetId) {
                    btn.classList.add('active', 'btn-primary');
                    btn.classList.remove('btn-ghost');
                } else {
                    btn.classList.remove('active', 'btn-primary');
                    btn.classList.add('btn-ghost');
                }
            });
            console.log(`✓ Switched to PLC preset: ${presetId}`);
            if (typeof fetchStatus === 'function') fetchStatus();
        }
    } catch (e) {
        console.error('Failed to switch PLC preset:', e);
    }
};

// ── Mode Detection ──
const urlParams = new URLSearchParams(window.location.search);
window.APP_MODE = (window.electronAPI && window.electronAPI.isElectron) || urlParams.get('mode') === 'desktop' ? 'desktop' : 'web';

// ══════════════════════════════════════
// PAGE ROUTING
// ══════════════════════════════════════
function navigateTo(pageName) {
    // In web mode, only reports/history is allowed
    if (window.APP_MODE === 'web') {
        pageName = 'history';
    }

    // Hide all pages
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));

    // Show target page
    const page = document.getElementById(`page-${pageName}`);
    const nav = document.querySelector(`.nav-item[data-page="${pageName}"]`);
    if (page) page.classList.add('active');
    if (nav) nav.classList.add('active');

    APP.currentPage = pageName;

    // Trigger page-specific init
    if (pageName === 'history' && typeof loadHistory === 'function') loadHistory();
    if (pageName === 'lines' && typeof loadProductionLinesPage === 'function') loadProductionLinesPage();
}

// Bind nav clicks
document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', e => {
        e.preventDefault();
        navigateTo(item.dataset.page);
    });
});

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.plc-preset-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            const presetId = btn.dataset.preset;
            if (presetId) switchPLCPreset(presetId);
        });
    });
});


// ══════════════════════════════════════
// CLOCK
// ══════════════════════════════════════
function updateClock() {
    const el = document.getElementById('clock');
    if (el) {
        const now = new Date();
        el.textContent = now.toLocaleString('en-IN', {
            year: 'numeric', month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit', second: '2-digit',
            hour12: false,
        });
    }
}
setInterval(updateClock, 1000);
updateClock();


// ══════════════════════════════════════
// WEBSOCKET
// ══════════════════════════════════════
function connectWebSocket() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${location.host}/ws/live`;

    APP.ws = new WebSocket(url);

    APP.ws.onopen = () => {
        APP.wsConnected = true;
        updateConnectionBadge(true);
        console.log('WebSocket connected');
    };

    APP.ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleLiveData(data);
        } catch (e) {
            console.error('WS parse error:', e);
        }
    };

    APP.ws.onclose = () => {
        APP.wsConnected = false;
        updateConnectionBadge(false);
        console.log('WebSocket closed, reconnecting in 2s…');
        setTimeout(connectWebSocket, 2000);
    };

    APP.ws.onerror = () => {
        APP.ws.close();
    };
}

function updateConnectionBadge(connected) {
    const badge = document.getElementById('connection-badge');
    if (!badge) return;
    const text = badge.querySelector('.conn-text');
    badge.className = 'connection-badge ' + (connected ? 'connected' : 'disconnected');
    text.textContent = connected ? 'Connected' : 'Disconnected';
}


// ══════════════════════════════════════
// PLC CONNECTION MANAGEMENT
// ══════════════════════════════════════
function showConnectModal(show = true) {
    if (window.APP_MODE === 'web') return; // PLC connection controls live in Desktop app only
    const modal = document.getElementById('plc-connect-modal');
    if (modal) {
        if (show) modal.classList.add('active');
        else modal.classList.remove('active');
    }
}

async function connectPLC(ip, port, simulation) {
    try {
        const resp = await fetch('/api/plc/connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ip, port, simulation }),
        });
        if (resp.ok) {
            const data = await resp.json();
            console.log('Connected target:', data);
            showConnectModal(false);
            await fetchStatus();
        }
    } catch (e) {
        console.error('PLC connection error:', e);
    }
}

async function disconnectPLC() {
    try {
        const resp = await fetch('/api/plc/disconnect', { method: 'POST' });
        if (resp.ok) {
            showConnectModal(true);
            await fetchStatus();
        }
    } catch (e) {
        console.error('PLC disconnect error:', e);
    }
}


// ══════════════════════════════════════
// FETCH STATUS
// ══════════════════════════════════════
async function fetchStatus() {
    try {
        const resp = await fetch('/api/status');
        const data = await resp.json();
        
        window.APP.activePLC = {
            ip: data.plc_ip,
            port: data.plc_port,
            simulation: data.simulation_mode,
            connected: data.plc_connected
        };

        const simBadge = document.getElementById('sim-badge');
        if (simBadge) {
            simBadge.style.display = data.simulation_mode && data.plc_ip ? '' : 'none';
        }

        const hostEl = document.getElementById('sidebar-plc-host');
        const disconnectBtn = document.getElementById('btn-sidebar-disconnect');
        
        if (data.plc_ip && data.plc_port) {
            hostEl.textContent = `${data.plc_ip}:${data.plc_port}`;
            if (data.simulation_mode) {
                hostEl.textContent += ' (Sim)';
            }
            disconnectBtn.style.display = '';
            
            // Sync values to modal form
            document.getElementById('input-plc-ip').value = data.plc_ip;
            document.getElementById('input-plc-port').value = data.plc_port;
            document.getElementById('input-plc-sim').checked = data.simulation_mode;
        } else {
            hostEl.textContent = 'None (Disconnected)';
            disconnectBtn.style.display = 'none';
            showConnectModal(true);
        }

        // Trigger updating elements inside dashboard page if active
        if (window.updateDashboardState) {
            window.updateDashboardState();
        }
    } catch (e) {
        console.error('Status fetch error:', e);
    }
}


// ══════════════════════════════════════
// REGISTERS CONFIGURATION MANAGEMENT
// ══════════════════════════════════════
async function fetchRegistersConfig() {
    try {
        const resp = await fetch('/api/registers/config');
        if (resp.ok) {
            window.APP.registerConfig = await resp.json();
            // Re-initialize dynamic components if present
            if (window.initDynamicDashboard) window.initDynamicDashboard();
        }
    } catch (e) {
        console.error('Failed to fetch registers config:', e);
    }
}

function showRegistersModal(show = true) {
    const modal = document.getElementById('plc-registers-modal');
    if (modal) {
        if (show) {
            populateRegistersConfigModal();
            modal.classList.add('active');
        } else {
            modal.classList.remove('active');
        }
    }
}

function populateRegistersConfigModal() {
    const list = document.getElementById('plc-registers-config-list');
    list.innerHTML = '';
    const config = window.APP.registerConfig || [];
    config.forEach(item => {
        addRegisterConfigRow(item.name, item.address, item.data_type);
    });
}

function addRegisterConfigRow(name = '', address = '', dataType = 'int') {
    const list = document.getElementById('plc-registers-config-list');
    const row = document.createElement('div');
    row.className = 'register-config-row';
    row.style.display = 'flex';
    row.style.gap = '8px';
    row.style.alignItems = 'center';
    row.innerHTML = `
        <input type="text" placeholder="Name (e.g. Temp)" class="input-name" value="${name}" style="flex: 2; background: var(--bg-input); border: 1px solid var(--border); color: var(--text-primary); border-radius: var(--radius-sm); padding: 6px 10px; font-family: inherit; font-size: 13px;">
        <input type="text" placeholder="e.g. D100" class="input-address" value="${address}" style="flex: 1.5; background: var(--bg-input); border: 1px solid var(--border); color: var(--text-primary); border-radius: var(--radius-sm); padding: 6px 10px; font-family: inherit; font-size: 13px;">
        <select class="select-datatype" style="flex: 1.5; background: var(--bg-input); border: 1px solid var(--border); color: var(--text-primary); border-radius: var(--radius-sm); padding: 6px 10px; font-family: inherit; font-size: 13px;">
            <option value="int" ${dataType === 'int' ? 'selected' : ''}>16-bit Int</option>
            <option value="int32" ${dataType === 'int32' ? 'selected' : ''}>32-bit Int</option>
            <option value="float" ${dataType === 'float' ? 'selected' : ''}>32-bit Float</option>
            <option value="string" ${dataType === 'string' ? 'selected' : ''}>ASCII String</option>
        </select>
        <button class="btn btn-ghost btn-delete-row" style="width: 40px; padding: 6px 0; color: var(--red); border: 1px solid rgba(239,68,68,0.2); font-weight: 700;">×</button>
    `;
    row.querySelector('.btn-delete-row').addEventListener('click', () => row.remove());
    list.appendChild(row);
}

async function saveRegistersConfig() {
    const list = document.getElementById('plc-registers-config-list');
    const rows = list.querySelectorAll('.register-config-row');
    const config = [];
    rows.forEach(row => {
        const name = row.querySelector('.input-name').value.trim();
        const address = row.querySelector('.input-address').value.trim();
        const dataType = row.querySelector('.select-datatype').value;
        if (address) {
            config.push({
                name: name || address,
                address: address,
                data_type: dataType
            });
        }
    });

    try {
        const resp = await fetch('/api/registers/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config })
        });
        if (resp.ok) {
            window.APP.registerConfig = config;
            showRegistersModal(false);
            if (window.initDynamicDashboard) window.initDynamicDashboard();
            if (window.loadHistory) window.loadHistory();
        } else {
            alert('Failed to save register configuration');
        }
    } catch (e) {
        console.error('Failed to save registers config:', e);
    }
}


// ══════════════════════════════════════
// INITIALIZE
// ══════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
    if (window.APP_MODE === 'web') {
        document.body.classList.add('mode-web');
        // Web mode: strictly DB Reports only. Hide data communication elements.
        const brandText = document.querySelector('.brand-text');
        if (brandText) brandText.textContent = 'TAG REPORTS';
        
        // Force navigate to history page
        navigateTo('history');
    } else {
        document.body.classList.add('mode-desktop');
        // Desktop mode: full data communication system
        fetchRegistersConfig().then(() => {
            initDashboard();
        });
        connectWebSocket();
        fetchStatus();
    }

    // Event Bindings
    document.getElementById('btn-modal-connect').addEventListener('click', () => {
        const ip = document.getElementById('input-plc-ip').value.trim();
        const port = parseInt(document.getElementById('input-plc-port').value.trim(), 10);
        const sim = document.getElementById('input-plc-sim').checked;
        if (ip && port) {
            connectPLC(ip, port, sim);
        }
    });

    document.getElementById('btn-modal-close').addEventListener('click', () => {
        showConnectModal(false);
    });

    document.getElementById('btn-sidebar-connect').addEventListener('click', (e) => {
        e.preventDefault();
        showConnectModal(true);
    });

    document.getElementById('btn-sidebar-disconnect').addEventListener('click', (e) => {
        e.preventDefault();
        disconnectPLC();
    });

    // Registers Config Bindings
    const btnCfgRegs = document.getElementById('btn-configure-registers');
    if (btnCfgRegs) btnCfgRegs.addEventListener('click', () => showRegistersModal(true));
    const btnRegsClose = document.getElementById('btn-registers-modal-close');
    if (btnRegsClose) btnRegsClose.addEventListener('click', () => showRegistersModal(false));
    const btnAddRegRow = document.getElementById('btn-add-register-row');
    if (btnAddRegRow) btnAddRegRow.addEventListener('click', () => addRegisterConfigRow());
    const btnRegsSave = document.getElementById('btn-registers-modal-save');
    if (btnRegsSave) btnRegsSave.addEventListener('click', () => saveRegistersConfig());

    // Production Lines Bindings
    const btnConfigureLines = document.getElementById('btn-configure-lines');
    if (btnConfigureLines) {
        btnConfigureLines.addEventListener('click', () => {
            showLinesModal(true);
        });
    }
    const btnLinesModalClose = document.getElementById('btn-lines-modal-close');
    if (btnLinesModalClose) {
        btnLinesModalClose.addEventListener('click', () => {
            showLinesModal(false);
        });
    }
    const btnAddLineConfigRow = document.getElementById('btn-add-line-config-row');
    if (btnAddLineConfigRow) {
        btnAddLineConfigRow.addEventListener('click', () => {
            addLineConfigBlock();
        });
    }
    const btnLinesModalSave = document.getElementById('btn-lines-modal-save');
    if (btnLinesModalSave) {
        btnLinesModalSave.addEventListener('click', () => {
            saveLinesConfig();
        });
    }
});


// ══════════════════════════════════════
// PRODUCTION LINES MANAGEMENT
// ══════════════════════════════════════
async function loadProductionLinesPage() {
    window.APP.activeLineId = null;
    window.APP.activeMachineId = null;
    destroyLineDetailCharts();
    destroyMachineHistoryChart();
    await fetchLinesConfig();
    renderLinesLayoutShell();
    updateLinesClock();
}

async function fetchLinesConfig() {
    try {
        const resp = await fetch('/api/lines/config');
        if (resp.ok) {
            window.APP.linesConfig = await resp.json();
        }
    } catch (e) {
        console.error('Failed to fetch lines config:', e);
    }
}

function updateLinesClock() {
    const el = document.getElementById('clock-lines');
    if (el && APP.currentPage === 'lines') {
        const now = new Date();
        el.textContent = now.toLocaleString('en-IN', {
            year: 'numeric', month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit', second: '2-digit',
            hour12: false,
        });
        setTimeout(updateLinesClock, 1000);
    }
}

function renderLinesLayoutShell() {
    if (window.APP.activeLineId) {
        if (window.APP.activeMachineId) {
            showMachineDetails(window.APP.activeLineId, window.APP.activeMachineId);
        } else {
            showLineDetails(window.APP.activeLineId);
        }
    } else {
        renderLinesGrid();
    }
}

function renderLinesGrid() {
    window.APP.activeLineId = null;
    window.APP.activeMachineId = null;
    destroyLineDetailCharts();
    destroyMachineHistoryChart();

    const container = document.getElementById('lines-container');
    if (!container) return;
    container.innerHTML = '';

    const config = window.APP.linesConfig || [];
    if (config.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" width="48" height="48">
                    <rect x="3" y="3" width="18" height="18" rx="2"/>
                    <line x1="9" y1="3" x2="9" y2="21"/>
                </svg>
                <p>No production lines configured. Click "Lines Config" to define them.</p>
            </div>
        `;
        return;
    }

    const grid = document.createElement('div');
    grid.className = 'lines-grid';
    grid.style.display = 'grid';
    grid.style.gridTemplateColumns = 'repeat(auto-fill, minmax(260px, 1fr))';
    grid.style.gap = '20px';

    config.forEach(line => {
        const card = document.createElement('div');
        card.className = 'card line-block-card';
        card.id = `line-block-${line.line_id}`;
        card.style.cursor = 'pointer';
        card.style.padding = '20px';
        card.style.transition = 'all 0.2s ease';
        card.style.display = 'flex';
        card.style.flexDirection = 'column';
        card.style.gap = '12px';
        card.style.background = 'linear-gradient(135deg, rgba(20,27,40,0.8) 0%, rgba(10,15,25,0.9) 100%)';
        card.style.border = '1px solid var(--border)';
        card.style.borderRadius = 'var(--radius)';

        card.addEventListener('mouseenter', () => {
            card.style.transform = 'translateY(-2px)';
            card.style.borderColor = 'var(--cyan)';
            card.style.boxShadow = '0 8px 24px rgba(0, 212, 255, 0.15)';
        });
        card.addEventListener('mouseleave', () => {
            card.style.transform = 'none';
            card.style.borderColor = 'var(--border)';
            card.style.boxShadow = 'none';
        });

        card.addEventListener('click', () => {
            showLineDetails(line.line_id);
        });

        card.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <h3 style="margin: 0; font-size: 15px; color: var(--cyan); font-weight: 700;">${line.line_name}</h3>
                <span class="badge badge-purple" style="font-size: 9px; padding: 2px 6px;">LINE BLOCK</span>
            </div>
            <div style="font-size: 11px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px; margin-top: 4px;">
                Target vs Actual Progress:
            </div>
            <div style="display: flex; align-items: center; justify-content: space-between; gap: 10px;">
                <div style="font-size: 20px; font-weight: 700; font-family: 'JetBrains Mono', monospace; color: var(--text-primary);">
                    <span id="line-grid-actual-${line.line_id}">—</span> / <span id="line-grid-target-${line.line_id}">—</span>
                </div>
                <div style="width: 80px; height: 80px; position: relative;">
                    <canvas id="line-grid-chart-${line.line_id}"></canvas>
                </div>
            </div>
            <div style="font-size: 11px; color: var(--text-muted); display: flex; justify-content: space-between; margin-top: auto; border-top: 1px solid rgba(255,255,255,0.05); padding-top: 8px;">
                <span>Accuracy: <strong id="line-grid-accuracy-${line.line_id}" style="color: var(--cyan);">—</strong></span>
                <span>Time left: <strong id="line-grid-time-${line.line_id}" style="color: var(--amber);">—</strong></span>
            </div>
        `;
        grid.appendChild(card);
    });

    container.appendChild(grid);
}

function showLineDetails(lineId) {
    window.APP.activeLineId = lineId;
    window.APP.activeMachineId = null;
    destroyLineDetailCharts();
    destroyMachineHistoryChart();
    
    const container = document.getElementById('lines-container');
    if (!container) return;
    container.innerHTML = '';

    const line = (window.APP.linesConfig || []).find(l => l.line_id === lineId);
    if (!line) {
        renderLinesGrid();
        return;
    }

    const detailView = document.createElement('div');
    detailView.className = 'line-detail-view';
    detailView.style.display = 'flex';
    detailView.style.flexDirection = 'column';
    detailView.style.gap = '20px';

    let machinesHtml = '';
    (line.machines || []).forEach(m => {
        machinesHtml += `
            <div class="card machine-block-card" id="machine-block-${m.machine_id}" style="cursor: pointer; padding: 20px; display: flex; flex-direction: column; gap: 12px; transition: all 0.2s ease; background: linear-gradient(135deg, rgba(20,27,40,0.8) 0%, rgba(10,15,25,0.9) 100%); border: 1px solid var(--border); border-radius: var(--radius);">
                <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(255,255,255,0.05); padding-bottom: 8px;">
                    <h4 style="margin: 0; font-size: 15px; font-weight: 600; color: var(--text-primary);">${m.machine_name}</h4>
                    <span class="machine-status-badge mono" id="m-selector-status-${m.machine_id}" style="font-size: 9px; padding: 1px 6px; border-radius: 10px; font-weight: 700; border: 1px solid currentColor;">UNKNOWN</span>
                </div>
                <div style="font-size: 11px; color: var(--text-secondary); text-transform: uppercase; letter-spacing: 0.5px;">
                    Completion:
                </div>
                <div style="font-size: 18px; font-weight: 700; font-family: 'JetBrains Mono', monospace; color: var(--text-primary);">
                    <span id="m-selector-actual-${m.machine_id}">—</span> / <span id="m-selector-target-${m.machine_id}">—</span>
                </div>
                <div style="font-size: 10px; color: var(--text-muted); display: flex; justify-content: space-between; margin-top: auto; border-top: 1px solid rgba(255,255,255,0.05); padding-top: 8px;">
                    <span>Accuracy: <strong id="m-selector-accuracy-${m.machine_id}" style="color: var(--cyan);">—</strong></span>
                    <span>Click for details →</span>
                </div>
            </div>
        `;
    });

    detailView.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <button class="btn btn-ghost btn-sm" id="btn-back-to-lines" style="border: 1px solid var(--border); display: flex; align-items: center; gap: 6px;">
                ← Back to Lines
            </button>
            <span style="font-size: 12px; color: var(--text-muted); font-family: 'JetBrains Mono', monospace;">Line: ${line.line_name}</span>
        </div>

        <div class="card" style="padding: 24px; background: linear-gradient(135deg, rgba(20,27,40,0.85) 0%, rgba(10,15,25,0.95) 100%); border: 1px solid var(--border);">
            <div style="display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 16px;">
                <div>
                    <h2 style="margin: 0; font-size: 20px; font-weight: 700; color: var(--cyan);">${line.line_name}</h2>
                    <span style="font-size: 12px; color: var(--text-secondary);">Real-time metrics mapped to PLC registers</span>
                </div>
                <div style="text-align: right;">
                    <div style="font-size: 12px; color: var(--text-secondary);">Line Completion (Summed)</div>
                    <div style="font-size: 22px; font-weight: 700; color: var(--text-primary); font-family: 'JetBrains Mono', monospace;"><span id="line-detail-actual-${line.line_id}">0</span> / <span id="line-detail-target-${line.line_id}">0</span></div>
                </div>
            </div>

            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px;">
                <div style="background: rgba(255,255,255,0.01); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="font-size: 10px; color: var(--text-secondary); text-transform: uppercase;">Line Accuracy</div>
                    <div id="line-detail-accuracy-${line.line_id}" style="font-size: 18px; font-weight: 700; color: var(--cyan); font-family: 'JetBrains Mono', monospace;">—</div>
                </div>
                <div style="background: rgba(255,255,255,0.01); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="font-size: 10px; color: var(--text-secondary); text-transform: uppercase;">Time to Complete (Slowest)</div>
                    <div id="line-detail-time-${line.line_id}" style="font-size: 18px; font-weight: 700; color: var(--amber); font-family: 'JetBrains Mono', monospace;">—</div>
                </div>
            </div>
        </div>

        <div style="display: flex; gap: 10px; margin-top: 10px; border-bottom: 1px solid rgba(255,255,255,0.05); padding-bottom: 8px;">
            <h3 style="font-size:14px; font-weight:600; color:var(--text-secondary); margin:0;">Select Machine to View History:</h3>
        </div>

        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 20px;">
            ${machinesHtml}
        </div>
    `;

    container.appendChild(detailView);

    document.getElementById('btn-back-to-lines').addEventListener('click', () => {
        renderLinesGrid();
    });

    (line.machines || []).forEach(m => {
        const card = document.getElementById(`machine-block-${m.machine_id}`);
        if (card) {
            card.addEventListener('mouseenter', () => {
                card.style.transform = 'translateY(-2px)';
                card.style.borderColor = 'var(--cyan)';
            });
            card.addEventListener('mouseleave', () => {
                card.style.transform = 'none';
                card.style.borderColor = 'var(--border)';
            });
            card.addEventListener('click', () => {
                showMachineDetails(lineId, m.machine_id);
            });
        }
    });
}

function showMachineDetails(lineId, machineId) {
    window.APP.activeMachineId = machineId;
    destroyLineDetailCharts();

    const container = document.getElementById('lines-container');
    if (!container) return;
    container.innerHTML = '';

    const line = (window.APP.linesConfig || []).find(l => l.line_id === lineId);
    if (!line) {
        renderLinesGrid();
        return;
    }

    const m = (line.machines || []).find(mac => mac.machine_id === machineId);
    if (!m) {
        showLineDetails(lineId);
        return;
    }

    const machineView = document.createElement('div');
    machineView.className = 'machine-detail-view';
    machineView.style.display = 'flex';
    machineView.style.flexDirection = 'column';
    machineView.style.gap = '20px';

    machineView.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <button class="btn btn-ghost btn-sm" id="btn-back-to-line" style="border: 1px solid var(--border); display: flex; align-items: center; gap: 6px;">
                ← Back to ${line.line_name}
            </button>
            <span style="font-size: 12px; color: var(--text-muted); font-family: 'JetBrains Mono', monospace;">Machine: ${m.machine_name}</span>
        </div>

        <div class="card" style="padding: 24px; background: linear-gradient(135deg, rgba(20,27,40,0.85) 0%, rgba(10,15,25,0.95) 100%); border: 1px solid var(--border);">
            <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 16px;">
                <div>
                    <h2 style="margin: 0; font-size: 20px; font-weight: 700; color: var(--cyan);">${m.machine_name}</h2>
                    <span style="font-size: 12px; color: var(--text-secondary);">Real-time status mapped to PLC registers</span>
                </div>
                <span class="machine-status-badge mono" id="m-show-status-${m.machine_id}" style="font-size: 13px; padding: 4px 12px; border-radius: 12px; font-weight: 700; border: 1px solid currentColor;">UNKNOWN</span>
            </div>

            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 15px;">
                <div style="background: rgba(255,255,255,0.01); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="font-size: 10px; color: var(--text-secondary); text-transform: uppercase;">Target Count</div>
                    <div id="m-show-target-${m.machine_id}" style="font-size: 18px; font-weight: 700; color: var(--text-primary); font-family: 'JetBrains Mono', monospace;">—</div>
                </div>
                <div style="background: rgba(255,255,255,0.01); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="font-size: 10px; color: var(--text-secondary); text-transform: uppercase;">Actual Count</div>
                    <div id="m-show-actual-${m.machine_id}" style="font-size: 18px; font-weight: 700; color: var(--text-primary); font-family: 'JetBrains Mono', monospace;">—</div>
                </div>
                <div style="background: rgba(255,255,255,0.01); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="font-size: 10px; color: var(--text-secondary); text-transform: uppercase;">Accuracy</div>
                    <div id="m-show-accuracy-${m.machine_id}" style="font-size: 18px; font-weight: 700; color: var(--cyan); font-family: 'JetBrains Mono', monospace;">—</div>
                </div>
                <div style="background: rgba(255,255,255,0.01); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="font-size: 10px; color: var(--text-secondary); text-transform: uppercase;">Yield</div>
                    <div id="m-show-yield-${m.machine_id}" style="font-size: 18px; font-weight: 700; color: var(--green); font-family: 'JetBrains Mono', monospace;">—</div>
                </div>
                <div style="background: rgba(255,255,255,0.01); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="font-size: 10px; color: var(--text-secondary); text-transform: uppercase;">Time left</div>
                    <div id="m-show-time-${m.machine_id}" style="font-size: 18px; font-weight: 700; color: var(--amber); font-family: 'JetBrains Mono', monospace;">—</div>
                </div>
            </div>
        </div>

        <div style="display: grid; grid-template-columns: 280px 1fr; gap: 20px; min-height: 350px;">
            <div class="card" style="padding: 20px; display: flex; flex-direction: column; gap: 16px; background: var(--bg-card); border: 1px solid var(--border);">
                <div class="card-header" style="padding: 0 0 10px; border-bottom: 1px solid rgba(255,255,255,0.05);">
                    <h3 style="font-size: 14px; font-weight: 600; color: var(--text-primary); margin:0;">State Durations</h3>
                </div>
                <div style="flex: 1; display: flex; align-items: center; justify-content: center; position: relative; height: 180px;">
                    <canvas id="m-detail-chart-${m.machine_id}" width="180" height="180"></canvas>
                </div>
                <div style="display: flex; flex-direction: column; gap: 6px; font-size: 11px; color: var(--text-secondary); font-family: 'JetBrains Mono', monospace; border-top: 1px dashed rgba(255,255,255,0.05); padding-top: 10px;">
                    <div style="display:flex; justify-content:space-between;">
                        <span><span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:#22c55e; margin-right:6px;"></span>Runtime:</span>
                        <strong style="color: #22c55e;" id="m-show-val-run-${m.machine_id}">0s</strong>
                    </div>
                    <div style="display:flex; justify-content:space-between;">
                        <span><span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:#f59e0b; margin-right:6px;"></span>Idle Time:</span>
                        <strong style="color: #f59e0b;" id="m-show-val-idle-${m.machine_id}">0s</strong>
                    </div>
                    <div style="display:flex; justify-content:space-between;">
                        <span><span style="display:inline-block; width:8px; height:8px; border-radius:50%; background:#ef4444; margin-right:6px;"></span>Breakdown:</span>
                        <strong style="color: #ef4444;" id="m-show-val-break-${m.machine_id}">0s</strong>
                    </div>
                </div>
            </div>

            <div class="card" style="padding: 20px; display: flex; flex-direction: column; gap: 16px; background: var(--bg-card); border: 1px solid var(--border);">
                <div class="card-header" style="padding: 0 0 10px; border-bottom: 1px solid rgba(255,255,255,0.05); display: flex; justify-content: space-between; align-items: center;">
                    <h3 style="font-size: 14px; font-weight: 600; color: var(--text-primary); margin:0;">Historical Trend (Last 30 Min)</h3>
                    <span class="chip" style="font-size: 9px;">Live Updating</span>
                </div>
                <div id="m-history-chart-container" style="flex: 1; height: 230px; position: relative;">
                </div>
            </div>
        </div>
    `;

    container.appendChild(machineView);

    document.getElementById('btn-back-to-line').addEventListener('click', () => {
        showLineDetails(lineId);
    });

    requestAnimationFrame(() => {
        destroyLineDetailCharts();
        window.APP.lineCharts = {};

        const canvas = document.getElementById(`m-detail-chart-${m.machine_id}`);
        if (canvas) {
            const ctx = canvas.getContext('2d');
            window.APP.lineCharts[m.machine_id] = new Chart(ctx, {
                type: 'pie',
                data: {
                    labels: ['Runtime', 'Idle', 'Breakdown'],
                    datasets: [{
                        data: [1, 0, 0],
                        backgroundColor: ['rgba(255,255,255,0.05)', '#f59e0b', '#ef4444'],
                        borderColor: 'rgba(15, 21, 32, 0.95)',
                        borderWidth: 1.5,
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                label: ctx => ` ${ctx.label}: ${formatDurationSeconds(ctx.raw)}`
                            }
                        }
                    }
                }
            });
        }

        fetchMachineHistory(m);
    });
}

function destroyLineDetailCharts() {
    if (window.APP.lineCharts) {
        Object.keys(window.APP.lineCharts).forEach(key => {
            if (window.APP.lineCharts[key]) {
                window.APP.lineCharts[key].destroy();
            }
        });
        window.APP.lineCharts = null;
    }
}

function destroyMachineHistoryChart() {
    if (window.APP.machineHistoryChart) {
        window.APP.machineHistoryChart.destroy();
        window.APP.machineHistoryChart = null;
    }
}

async function fetchMachineHistory(m) {
    const chartContainer = document.getElementById(`m-history-chart-container`);
    if (!chartContainer) return;
    
    chartContainer.innerHTML = '<div class="spinner"><div class="spinner-ring"></div> Fetching history data…</div>';
    
    const mRegs = m.registers || {};
    const addresses = [mRegs.target, mRegs.actual, mRegs.accuracy, mRegs.runtime, mRegs.idle_time, mRegs.breakdown_time].filter(Boolean);
    if (!addresses.length) {
        chartContainer.innerHTML = '<div class="empty-state"><p>No registers configured for this machine</p></div>';
        return;
    }
    
    try {
        const ip = window.APP.activePLC?.ip || '';
        const port = window.APP.activePLC?.port || '';
        const url = `/api/registers/history?minutes=30&plc_ip=${ip}&plc_port=${port}`;
        const resp = await fetch(url);
        if (!resp.ok) throw new Error('API error');
        
        const historyData = await resp.json();
        
        chartContainer.innerHTML = '<canvas id="m-history-line-chart" style="width: 100%; height: 100%;"></canvas>';
        
        const datasets = [];
        const colors = {
            target: '#60a5fa',
            actual: '#4ade80',
            runtime: '#22c55e',
            idle_time: '#fbbf24',
            breakdown_time: '#f87171'
        };
        const labels = {
            target: 'Target Count',
            actual: 'Actual Count',
            runtime: 'Runtime (s)',
            idle_time: 'Idle Time (s)',
            breakdown_time: 'Breakdown Time (s)'
        };
        
        Object.keys(mRegs).forEach(key => {
            const addr = mRegs[key];
            if (addr && historyData[addr] && ['target', 'actual', 'runtime', 'idle_time', 'breakdown_time'].includes(key)) {
                const pts = historyData[addr] || [];
                const dataPts = pts.map(p => ({ x: new Date(p.timestamp), y: p.value }));
                
                datasets.push({
                    label: labels[key] || key,
                    data: dataPts,
                    borderColor: colors[key] || '#a78bfa',
                    backgroundColor: 'transparent',
                    borderWidth: 2,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    tension: 0.2,
                    fill: false
                });
            }
        });
        
        const ctx = document.getElementById('m-history-line-chart').getContext('2d');
        destroyMachineHistoryChart();
        
        window.APP.machineHistoryChart = new Chart(ctx, {
            type: 'line',
            data: {
                datasets: datasets
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                plugins: {
                    legend: {
                        display: true,
                        position: 'top',
                        labels: { color: 'var(--text-secondary)', font: { size: 10 } }
                    },
                    tooltip: {
                        mode: 'index',
                        intersect: false,
                        bodyFont: { family: 'JetBrains Mono', size: 11 },
                    }
                },
                scales: {
                    x: {
                        type: 'time',
                        time: {
                            tooltipFormat: 'HH:mm:ss',
                            displayFormats: { second: 'HH:mm:ss', minute: 'HH:mm', hour: 'HH:mm' }
                        },
                        grid: { color: 'rgba(255,255,255,0.04)' },
                        ticks: { color: '#64748b', font: { size: 10 } }
                    },
                    y: {
                        grid: { color: 'rgba(255,255,255,0.04)' },
                        ticks: { color: '#64748b', font: { size: 10 } }
                    }
                }
            }
        });
        
    } catch (e) {
        console.error('History fetch error:', e);
        chartContainer.innerHTML = '<div class="empty-state"><p style="color: var(--red)">Failed to load historical trend data</p></div>';
    }
}

window.updateLineGridPieChart = function(lineId, actual, target) {
    const canvas = document.getElementById(`line-grid-chart-${lineId}`);
    if (!canvas) return;

    window.APP.lineGridCharts = window.APP.lineGridCharts || {};

    const actVal = Math.max(0, Number(actual) || 0);
    const tgtVal = Math.max(actVal, Number(target) || 0);
    const remVal = Math.max(0, tgtVal - actVal);

    if (window.APP.lineGridCharts[lineId]) {
        const chart = window.APP.lineGridCharts[lineId];
        chart.data.datasets[0].data = [actVal, remVal];
        chart.update('none');
    } else {
        const ctx = canvas.getContext('2d');
        window.APP.lineGridCharts[lineId] = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: ['Production Achieved', 'Target Shortfall'],
                datasets: [{
                    data: [actVal, remVal],
                    backgroundColor: ['#00d4ff', 'rgba(255,255,255,0.08)'],
                    borderColor: '#0f1520',
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: ctx => ` ${ctx.label}: ${ctx.raw}`
                        }
                    }
                }
            }
        });
    }
};

function renderLinesLiveData(data) {
    if (!window.APP.linesConfig || data.status !== 'OK') return;

    const getRegVal = (addr) => {
        if (!addr) return null;
        const reg = (data.d_registers || []).find(r => r.address === addr);
        return reg ? reg.value : null;
    };

    window.APP.linesConfig.forEach(line => {
        let sumTarget = 0;
        let sumActual = 0;
        let maxTime = 0;
        let hasData = false;

        (line.machines || []).forEach(m => {
            const mRegs = m.registers || {};
            const mTarget = getRegVal(mRegs.target);
            const mActual = getRegVal(mRegs.actual);
            const mTime = getRegVal(mRegs.time_to_complete);

            if (mTarget !== null) { sumTarget += Number(mTarget); hasData = true; }
            if (mActual !== null) { sumActual += Number(mActual); hasData = true; }
            if (mTime !== null) { maxTime = Math.max(maxTime, Number(mTime)); hasData = true; }
        });

        if (!hasData) {
            const lineRegs = line.registers || {};
            const lTarget = getRegVal(lineRegs.target);
            const lActual = getRegVal(lineRegs.actual);
            const lTime = getRegVal(lineRegs.time_to_complete);
            if (lTarget !== null) sumTarget = Number(lTarget);
            if (lActual !== null) sumActual = Number(lActual);
            if (lTime !== null) maxTime = Number(lTime);
        }

        const calculatedAccuracy = sumTarget > 0 ? (sumActual / sumTarget) * 100 : 0;

        const elGridTarget = document.getElementById(`line-grid-target-${line.line_id}`);
        const elGridActual = document.getElementById(`line-grid-actual-${line.line_id}`);
        const elGridAccuracy = document.getElementById(`line-grid-accuracy-${line.line_id}`);
        const elGridTime = document.getElementById(`line-grid-time-${line.line_id}`);

        if (elGridTarget) elGridTarget.textContent = sumTarget;
        if (elGridActual) elGridActual.textContent = sumActual;
        if (elGridAccuracy) elGridAccuracy.textContent = `${calculatedAccuracy.toFixed(1)}%`;
        if (elGridTime) elGridTime.textContent = formatDurationSeconds(maxTime);

        updateLineGridPieChart(line.line_id, sumActual, sumTarget);

        if (window.APP.activeLineId === line.line_id) {
            const elDetailTarget = document.getElementById(`line-detail-target-${line.line_id}`);
            const elDetailActual = document.getElementById(`line-detail-actual-${line.line_id}`);
            const elDetailAccuracy = document.getElementById(`line-detail-accuracy-${line.line_id}`);
            const elDetailTime = document.getElementById(`line-detail-time-${line.line_id}`);

            if (elDetailTarget) elDetailTarget.textContent = sumTarget;
            if (elDetailActual) elDetailActual.textContent = sumActual;
            if (elDetailAccuracy) elDetailAccuracy.textContent = `${calculatedAccuracy.toFixed(1)}%`;
            if (elDetailTime) elDetailTime.textContent = formatDurationSeconds(maxTime);

            (line.machines || []).forEach(m => {
                const mRegs = m.registers || {};
                const mTarget = getRegVal(mRegs.target);
                const mActual = getRegVal(mRegs.actual);
                const mAccuracy = getRegVal(mRegs.accuracy);
                const mTime = getRegVal(mRegs.time_to_complete);
                const mBreak = getRegVal(mRegs.breakdown_time) || 0;
                const mIdle = getRegVal(mRegs.idle_time) || 0;
                const mRun = getRegVal(mRegs.runtime) || 0;
                const mProduct = getRegVal(mRegs.product_type);

                const elMSelectorTarget = document.getElementById(`m-selector-target-${m.machine_id}`);
                const elMSelectorActual = document.getElementById(`m-selector-actual-${m.machine_id}`);
                const elMSelectorAccuracy = document.getElementById(`m-selector-accuracy-${m.machine_id}`);
                const elMSelectorStatus = document.getElementById(`m-selector-status-${m.machine_id}`);

                if (elMSelectorTarget) elMSelectorTarget.textContent = mTarget !== null ? mTarget : '—';
                if (elMSelectorActual) elMSelectorActual.textContent = mActual !== null ? mActual : '—';
                if (elMSelectorAccuracy) elMSelectorAccuracy.textContent = mAccuracy !== null ? `${Number(mAccuracy).toFixed(1)}%` : '—';

                if (elMSelectorStatus) {
                    if (mProduct === 2) {
                        elMSelectorStatus.textContent = 'BREAKDOWN';
                        elMSelectorStatus.style.color = 'var(--red)';
                        elMSelectorStatus.style.background = 'rgba(239, 68, 68, 0.15)';
                    } else {
                        const isIdle = mIdle > 0 && mRun === 0 || (mProduct === 1 && mTime === 0 && mActual === 0);
                        if (isIdle) {
                            elMSelectorStatus.textContent = 'IDLE';
                            elMSelectorStatus.style.color = 'var(--amber)';
                            elMSelectorStatus.style.background = 'rgba(245, 158, 11, 0.15)';
                        } else {
                            elMSelectorStatus.textContent = 'RUNNING';
                            elMSelectorStatus.style.color = 'var(--green)';
                            elMSelectorStatus.style.background = 'rgba(34, 197, 94, 0.15)';
                        }
                    }
                }

                if (window.APP.activeMachineId === m.machine_id) {
                    const elShowTarget = document.getElementById(`m-show-target-${m.machine_id}`);
                    const elShowActual = document.getElementById(`m-show-actual-${m.machine_id}`);
                    const elShowAccuracy = document.getElementById(`m-show-accuracy-${m.machine_id}`);
                    const elShowTime = document.getElementById(`m-show-time-${m.machine_id}`);
                    const elShowYield = document.getElementById(`m-show-yield-${m.machine_id}`);
                    const elShowStatus = document.getElementById(`m-show-status-${m.machine_id}`);

                    if (elShowTarget) elShowTarget.textContent = mTarget !== null ? mTarget : '—';
                    if (elShowActual) elShowActual.textContent = mActual !== null ? mActual : '—';
                    if (elShowAccuracy) elShowAccuracy.textContent = mAccuracy !== null ? `${Number(mAccuracy).toFixed(1)}%` : '—';
                    if (elShowTime) elShowTime.textContent = mTime !== null ? formatDurationSeconds(mTime) : '—';

                    if (elShowStatus) {
                        if (mProduct === 2) {
                            elShowStatus.textContent = 'BREAKDOWN';
                            elShowStatus.style.color = 'var(--red)';
                            elShowStatus.style.background = 'rgba(239, 68, 68, 0.15)';
                            if (elShowYield) {
                                elShowYield.textContent = 'NG';
                                elShowYield.style.color = 'var(--red)';
                            }
                        } else {
                            const isIdle = mIdle > 0 && mRun === 0 || (mProduct === 1 && mTime === 0 && mActual === 0);
                            if (isIdle) {
                                elShowStatus.textContent = 'IDLE';
                                elShowStatus.style.color = 'var(--amber)';
                                elShowStatus.style.background = 'rgba(245, 158, 11, 0.15)';
                            } else {
                                elShowStatus.textContent = 'RUNNING';
                                elShowStatus.style.color = 'var(--green)';
                                elShowStatus.style.background = 'rgba(34, 197, 94, 0.15)';
                            }
                            if (elShowYield) {
                                elShowYield.textContent = 'OK';
                                elShowYield.style.color = 'var(--green)';
                            }
                        }
                    }

                    const elShowValRun = document.getElementById(`m-show-val-run-${m.machine_id}`);
                    const elShowValIdle = document.getElementById(`m-show-val-idle-${m.machine_id}`);
                    const elShowValBreak = document.getElementById(`m-show-val-break-${m.machine_id}`);

                    if (elShowValRun) elShowValRun.textContent = formatDurationSeconds(mRun);
                    if (elShowValIdle) elShowValIdle.textContent = formatDurationSeconds(mIdle);
                    if (elShowValBreak) elShowValBreak.textContent = formatDurationSeconds(mBreak);

                    if (window.APP.lineCharts && window.APP.lineCharts[m.machine_id]) {
                        const chart = window.APP.lineCharts[m.machine_id];
                        const totalSec = mRun + mBreak + mIdle;
                        
                        if (totalSec > 0) {
                            chart.data.datasets[0].data = [mRun, mIdle, mBreak];
                            chart.data.datasets[0].backgroundColor = ['#22c55e', '#f59e0b', '#ef4444'];
                        } else {
                            chart.data.datasets[0].data = [1, 0, 0];
                            chart.data.datasets[0].backgroundColor = ['rgba(255,255,255,0.05)', '#f59e0b', '#ef4444'];
                        }
                        chart.update('none');
                    }

                    if (window.APP.machineHistoryChart) {
                        const lChart = window.APP.machineHistoryChart;
                        const ts = new Date(data.timestamp);
                        
                        const keys = {
                            target: mTarget,
                            actual: mActual,
                            runtime: mRun,
                            idle_time: mIdle,
                            breakdown_time: mBreak
                        };
                        const labels = {
                            target: 'Target Count',
                            actual: 'Actual Count',
                            runtime: 'Runtime (s)',
                            idle_time: 'Idle Time (s)',
                            breakdown_time: 'Breakdown Time (s)'
                        };
                        
                        Object.keys(keys).forEach(k => {
                            const val = keys[k];
                            if (val !== null) {
                                let dataset = lChart.data.datasets.find(ds => ds.label === labels[k]);
                                if (dataset) {
                                    dataset.data.push({ x: ts, y: Number(val) });
                                    if (dataset.data.length > 100) {
                                        dataset.data.shift();
                                    }
                                }
                            }
                        });
                        lChart.update('none');
                    }
                }
            });
        }
    });
}

function formatDurationSeconds(sec) {
    if (sec === null || sec === undefined) return '—';
    const s = parseInt(sec, 10);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const rs = s % 60;
    if (m < 60) return `${m}m ${rs}s`;
    const h = Math.floor(m / 60);
    const rm = m % 60;
    return `${h}h ${rm}m`;
}

function showLinesModal(show = true) {
    const modal = document.getElementById('plc-lines-modal');
    if (modal) {
        if (show) {
            populateLinesConfigModal();
            modal.classList.add('active');
        } else {
            modal.classList.remove('active');
        }
    }
}

function populateLinesConfigModal() {
    const list = document.getElementById('plc-lines-config-list');
    list.innerHTML = '';
    const config = window.APP.linesConfig || [];
    config.forEach(line => {
        addLineConfigBlock(line);
    });
}

function addLineConfigBlock(line = null) {
    const list = document.getElementById('plc-lines-config-list');
    const lineId = line ? line.line_id : `line_${Date.now()}`;
    const name = line ? line.line_name : 'New Line';
    const regs = line ? (line.registers || {}) : { target: '', actual: '', accuracy: '', time_to_complete: '' };

    const block = document.createElement('div');
    block.className = 'line-config-block';
    block.id = `line-config-block-${lineId}`;
    block.style.background = 'rgba(255,255,255,0.02)';
    block.style.border = '1px solid var(--border)';
    block.style.borderRadius = '8px';
    block.style.padding = '16px';
    block.style.display = 'flex';
    block.style.flexDirection = 'column';
    block.style.gap = '12px';
    block.style.marginBottom = '15px';

    block.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(255,255,255,0.05); padding-bottom: 8px;">
            <div style="display: flex; gap: 10px; align-items: center; flex: 1;">
                <label style="font-size: 12px; font-weight: 600; color: var(--cyan);">LINE NAME:</label>
                <input type="text" class="line-name-input" value="${name}" style="background: var(--bg-input); border: 1px solid var(--border); color: var(--text-primary); border-radius: var(--radius-sm); padding: 4px 10px; font-size: 13px; font-weight: 700; flex: 0.5;">
            </div>
            <button class="btn btn-ghost btn-sm btn-delete-line-row" style="color: var(--red); border: 1px solid rgba(239,68,68,0.2);">Delete Line</button>
        </div>

        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; font-size: 11px;">
            <div>
                <label style="color: var(--text-secondary);">Target Reg:</label>
                <input type="text" class="line-reg-target" value="${regs.target || ''}" placeholder="e.g. D300" style="width: 100%; background: var(--bg-input); border: 1px solid var(--border); color: var(--text-primary); border-radius: var(--radius-sm); padding: 4px 8px; margin-top: 4px;">
            </div>
            <div>
                <label style="color: var(--text-secondary);">Actual Reg:</label>
                <input type="text" class="line-reg-actual" value="${regs.actual || ''}" placeholder="e.g. D301" style="width: 100%; background: var(--bg-input); border: 1px solid var(--border); color: var(--text-primary); border-radius: var(--radius-sm); padding: 4px 8px; margin-top: 4px;">
            </div>
            <div>
                <label style="color: var(--text-secondary);">Accuracy Reg:</label>
                <input type="text" class="line-reg-accuracy" value="${regs.accuracy || ''}" placeholder="e.g. D302" style="width: 100%; background: var(--bg-input); border: 1px solid var(--border); color: var(--text-primary); border-radius: var(--radius-sm); padding: 4px 8px; margin-top: 4px;">
            </div>
            <div>
                <label style="color: var(--text-secondary);">Time Reg:</label>
                <input type="text" class="line-reg-time" value="${regs.time_to_complete || ''}" placeholder="e.g. D303" style="width: 100%; background: var(--bg-input); border: 1px solid var(--border); color: var(--text-primary); border-radius: var(--radius-sm); padding: 4px 8px; margin-top: 4px;">
            </div>
        </div>

        <div class="machines-config-list" style="margin-top: 10px; display: flex; flex-direction: column; gap: 10px; padding-left: 15px; border-left: 2px dashed var(--border);">
            <!-- Machines list -->
        </div>

        <button class="btn btn-ghost btn-sidebar-xs btn-add-machine-config" style="align-self: flex-start; margin-top: 5px; border: 1px dashed var(--border);">
            + Add Machine to Line
        </button>
    `;

    block.querySelector('.btn-delete-line-row').addEventListener('click', () => block.remove());
    block.querySelector('.btn-add-machine-config').addEventListener('click', () => {
        addMachineConfigRow(block.querySelector('.machines-config-list'), null);
    });

    list.appendChild(block);

    const machList = block.querySelector('.machines-config-list');
    if (line && line.machines) {
        line.machines.forEach(m => {
            addMachineConfigRow(machList, m);
        });
    }
}

function addMachineConfigRow(container, m = null) {
    const mId = m ? m.machine_id : `m_${Date.now()}_${Math.floor(Math.random()*1000)}`;
    const name = m ? m.machine_name : 'New Machine';
    const regs = m ? (m.registers || {}) : {
        target: '', actual: '', accuracy: '', time_to_complete: '',
        breakdown_time: '', idle_time: '', runtime: '', product_type: ''
    };

    const row = document.createElement('div');
    row.className = 'machine-config-row';
    row.dataset.machineId = mId;
    row.style.background = 'rgba(255,255,255,0.01)';
    row.style.border = '1px solid var(--border)';
    row.style.borderRadius = '6px';
    row.style.padding = '12px';
    row.style.display = 'flex';
    row.style.flexDirection = 'column';
    row.style.gap = '8px';
    row.style.marginTop = '5px';

    row.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid rgba(255,255,255,0.03); padding-bottom: 6px;">
            <div style="display: flex; gap: 8px; align-items: center; flex: 1;">
                <label style="font-size: 11px; font-weight: 600; color: var(--text-secondary);">MACHINE NAME:</label>
                <input type="text" class="machine-name-input" value="${name}" style="background: var(--bg-input); border: 1px solid var(--border); color: var(--text-primary); border-radius: var(--radius-sm); padding: 2px 8px; font-size: 12px; flex: 0.5;">
            </div>
            <button class="btn btn-ghost btn-sidebar-xs btn-delete-machine-row" style="color: var(--red); padding: 2px 6px;">Remove</button>
        </div>

        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 8px; font-size: 10px;">
            <div>
                <label>Target Reg:</label>
                <input type="text" class="m-reg-target" value="${regs.target || ''}" placeholder="D310" style="width: 100%; background: var(--bg-input); border: 1px solid var(--border); color: var(--text-primary); border-radius: var(--radius-sm); padding: 2px 6px; margin-top: 2px;">
            </div>
            <div>
                <label>Actual Reg:</label>
                <input type="text" class="m-reg-actual" value="${regs.actual || ''}" placeholder="D311" style="width: 100%; background: var(--bg-input); border: 1px solid var(--border); color: var(--text-primary); border-radius: var(--radius-sm); padding: 2px 6px; margin-top: 2px;">
            </div>
            <div>
                <label>Accuracy Reg:</label>
                <input type="text" class="m-reg-accuracy" value="${regs.accuracy || ''}" placeholder="D312" style="width: 100%; background: var(--bg-input); border: 1px solid var(--border); color: var(--text-primary); border-radius: var(--radius-sm); padding: 2px 6px; margin-top: 2px;">
            </div>
            <div>
                <label>Time Reg:</label>
                <input type="text" class="m-reg-time" value="${regs.time_to_complete || ''}" placeholder="D313" style="width: 100%; background: var(--bg-input); border: 1px solid var(--border); color: var(--text-primary); border-radius: var(--radius-sm); padding: 2px 6px; margin-top: 2px;">
            </div>
            <div>
                <label>Breakdown Reg:</label>
                <input type="text" class="m-reg-breakdown" value="${regs.breakdown_time || ''}" placeholder="D314" style="width: 100%; background: var(--bg-input); border: 1px solid var(--border); color: var(--text-primary); border-radius: var(--radius-sm); padding: 2px 6px; margin-top: 2px;">
            </div>
            <div>
                <label>Idle Reg:</label>
                <input type="text" class="m-reg-idle" value="${regs.idle_time || ''}" placeholder="D315" style="width: 100%; background: var(--bg-input); border: 1px solid var(--border); color: var(--text-primary); border-radius: var(--radius-sm); padding: 2px 6px; margin-top: 2px;">
            </div>
            <div>
                <label>Runtime Reg:</label>
                <input type="text" class="m-reg-runtime" value="${regs.runtime || ''}" placeholder="D316" style="width: 100%; background: var(--bg-input); border: 1px solid var(--border); color: var(--text-primary); border-radius: var(--radius-sm); padding: 2px 6px; margin-top: 2px;">
            </div>
            <div>
                <label>Prod Type Reg:</label>
                <input type="text" class="m-reg-product" value="${regs.product_type || ''}" placeholder="D317" style="width: 100%; background: var(--bg-input); border: 1px solid var(--border); color: var(--text-primary); border-radius: var(--radius-sm); padding: 2px 6px; margin-top: 2px;">
            </div>
        </div>
    `;

    row.querySelector('.btn-delete-machine-row').addEventListener('click', () => row.remove());
    container.appendChild(row);
}

async function saveLinesConfig() {
    const list = document.getElementById('plc-lines-config-list');
    const blocks = list.querySelectorAll('.line-config-block');
    const config = [];

    blocks.forEach(block => {
        const lineName = block.querySelector('.line-name-input').value.trim();
        const lineId = block.id.replace('line-config-block-', '');
        
        const regs = {
            target: block.querySelector('.line-reg-target').value.trim(),
            actual: block.querySelector('.line-reg-actual').value.trim(),
            accuracy: block.querySelector('.line-reg-accuracy').value.trim(),
            time_to_complete: block.querySelector('.line-reg-time').value.trim()
        };

        const machines = [];
        const mRows = block.querySelectorAll('.machine-config-row');
        mRows.forEach(mRow => {
            const mName = mRow.querySelector('.machine-name-input').value.trim();
            const mId = mRow.dataset.machineId;
            const mRegs = {
                target: mRow.querySelector('.m-reg-target').value.trim(),
                actual: mRow.querySelector('.m-reg-actual').value.trim(),
                accuracy: mRow.querySelector('.m-reg-accuracy').value.trim(),
                time_to_complete: mRow.querySelector('.m-reg-time').value.trim(),
                breakdown_time: mRow.querySelector('.m-reg-breakdown').value.trim(),
                idle_time: mRow.querySelector('.m-reg-idle').value.trim(),
                runtime: mRow.querySelector('.m-reg-runtime').value.trim(),
                product_type: mRow.querySelector('.m-reg-product').value.trim()
            };

            machines.push({
                machine_id: mId,
                machine_name: mName,
                registers: mRegs
            });
        });

        config.push({
            line_id: lineId,
            line_name: lineName,
            registers: regs,
            machines: machines
        });
    });

    try {
        const resp = await fetch('/api/lines/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ config })
        });
        if (resp.ok) {
            window.APP.linesConfig = config;
            showLinesModal(false);
            loadProductionLinesPage();
            await fetchRegistersConfig();
        } else {
            alert('Failed to save lines configuration');
        }
    } catch (e) {
        console.error('Failed to save lines config:', e);
    }
}

