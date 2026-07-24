/**
 * history.js — Simplified Industrial DataViewer & Executive Reporting Engine
 * Aggregates shift data by Day, Month, or Shift with Pie Chart analytics & PDF download.
 */

let chartPieTime = null;
let chartPieProduction = null;

document.addEventListener('DOMContentLoaded', () => {
    initDataViewer();
});

function initDataViewer() {
    const viewModeSelect = document.getElementById('report-view-mode');
    const monthPickerGroup = document.getElementById('group-month-picker');
    const datePickerGroup = document.getElementById('group-date-picker');
    const shiftPickerGroup = document.getElementById('group-shift-picker');
    const btnGenerate = document.getElementById('btn-generate-report');
    const btnPdf = document.getElementById('btn-download-pdf');

    // Set default month input to current month (YYYY-MM)
    const now = new Date();
    const curMonth = now.toISOString().slice(0, 7);
    const monthInput = document.getElementById('report-month-input');
    if (monthInput) monthInput.value = curMonth;

    const dateInput = document.getElementById('report-date-input');
    if (dateInput) dateInput.value = now.toISOString().slice(0, 10);

    // View mode switch listener
    if (viewModeSelect) {
        viewModeSelect.addEventListener('change', () => {
            const mode = viewModeSelect.value;
            if (mode === 'month') {
                monthPickerGroup.style.display = 'flex';
                datePickerGroup.style.display = 'none';
                shiftPickerGroup.style.display = 'flex';
            } else {
                monthPickerGroup.style.display = 'none';
                datePickerGroup.style.display = 'flex';
                shiftPickerGroup.style.display = 'flex';
            }
        });
    }

    if (btnGenerate) {
        btnGenerate.addEventListener('click', loadHistory);
    }

    if (btnPdf) {
        btnPdf.addEventListener('click', () => {
            window.print();
        });
    }

    const btnClearHistory = document.getElementById('btn-clear-db-history');
    if (btnClearHistory) {
        btnClearHistory.addEventListener('click', async () => {
            if (confirm('⚠️ Clear all stored shift performance history from the database?')) {
                try {
                    const resp = await fetch('/api/shifts/summaries', { method: 'DELETE' });
                    if (resp.ok) {
                        alert('✓ Shift history cleared.');
                        loadHistory();
                    }
                } catch (e) {
                    console.error('Failed to clear shift history:', e);
                }
            }
        });
    }

    // Load initial report
    loadHistory();
}

async function loadHistory() {
    const viewMode = document.getElementById('report-view-mode')?.value || 'month';
    const monthVal = document.getElementById('report-month-input')?.value || new Date().toISOString().slice(0, 7);
    const dateVal = document.getElementById('report-date-input')?.value || new Date().toISOString().slice(0, 10);
    const shiftVal = document.getElementById('report-shift-select')?.value || 'all';

    const titleEl = document.getElementById('report-title-heading');
    const subtitleEl = document.getElementById('report-subtitle-text');
    const generatedEl = document.getElementById('report-generated-date');
    if (generatedEl) generatedEl.textContent = new Date().toLocaleDateString('en-IN');

    if (viewMode === 'month') {
        if (titleEl) titleEl.textContent = `Monthly Shift Performance Report (${monthVal})`;
        if (subtitleEl) subtitleEl.textContent = `Period: ${monthVal} • All Manufacturing Lines & Shifts Aggregated`;

        try {
            const resp = await fetch(`/api/shifts/monthly-report?month=${monthVal}`);
            const data = await resp.json();
            renderMonthlyReportData(data, shiftVal);
        } catch (e) {
            console.error('Failed to load monthly report:', e);
        }
    } else {
        if (titleEl) titleEl.textContent = `Daily Shift Performance Report (${dateVal})`;
        if (subtitleEl) subtitleEl.textContent = `Date: ${dateVal} • Shift: ${shiftVal === 'all' ? 'All Shifts' : shiftVal}`;

        try {
            let url = `/api/shifts/summaries?date=${dateVal}`;
            if (shiftVal !== 'all') url += `&shift_name=${encodeURIComponent(shiftVal)}`;
            const resp = await fetch(url);
            const records = await resp.json();
            renderShiftRecordsData(records, dateVal);
        } catch (e) {
            console.error('Failed to load daily report:', e);
        }
    }
}

function renderMonthlyReportData(data, shiftFilter) {
    let records = data.shift_records || [];
    if (shiftFilter && shiftFilter !== 'all') {
        records = records.filter(r => r.shift_name.includes(shiftFilter));
    }

    const totalTarget = records.reduce((s, r) => s + (r.target || 0), 0);
    const totalProduction = records.reduce((s, r) => s + (r.production || 0), 0);
    const totalRuntimeMin = records.reduce((s, r) => s + (r.runtime_minutes || 0), 0);
    const totalIdleMin = records.reduce((s, r) => s + (r.idle_time_minutes || 0), 0);
    const totalBreakdownMin = records.reduce((s, r) => s + (r.breakdown_time_minutes || 0), 0);
    const overallEff = totalTarget > 0 ? ((totalProduction / totalTarget) * 100).toFixed(1) : '0.0';

    updateKPIs(totalTarget, totalProduction, totalRuntimeMin, totalIdleMin, totalBreakdownMin, overallEff);
    updatePieCharts(totalRuntimeMin, totalIdleMin, totalBreakdownMin, totalProduction, totalTarget);
    updateReportTable(records);
}

function renderShiftRecordsData(records, label) {
    const totalTarget = records.reduce((s, r) => s + (r.target || 0), 0);
    const totalProduction = records.reduce((s, r) => s + (r.production || 0), 0);
    const totalRuntimeMin = records.reduce((s, r) => s + (r.runtime_minutes || 0), 0);
    const totalIdleMin = records.reduce((s, r) => s + (r.idle_time_minutes || 0), 0);
    const totalBreakdownMin = records.reduce((s, r) => s + (r.breakdown_time_minutes || 0), 0);
    const overallEff = totalTarget > 0 ? ((totalProduction / totalTarget) * 100).toFixed(1) : '0.0';

    updateKPIs(totalTarget, totalProduction, totalRuntimeMin, totalIdleMin, totalBreakdownMin, overallEff);
    updatePieCharts(totalRuntimeMin, totalIdleMin, totalBreakdownMin, totalProduction, totalTarget);
    updateReportTable(records);
}

function updateKPIs(target, prod, runMin, idleMin, bdMin, eff) {
    document.getElementById('kpi-total-target').textContent = target.toLocaleString();
    document.getElementById('kpi-total-production').textContent = prod.toLocaleString();
    document.getElementById('kpi-total-runtime').textContent = `${(runMin / 60).toFixed(1)} hrs`;
    document.getElementById('kpi-total-idle').textContent = `${(idleMin / 60).toFixed(1)} hrs`;
    document.getElementById('kpi-total-breakdown').textContent = `${(bdMin / 60).toFixed(1)} hrs`;
    document.getElementById('kpi-overall-efficiency').textContent = `${eff}%`;
}

function updatePieCharts(runMin, idleMin, bdMin, prod, target) {
    // 1. Time breakdown pie chart
    const runHrs = Math.max(0, parseFloat((runMin / 60).toFixed(1)));
    const idleHrs = Math.max(0, parseFloat((idleMin / 60).toFixed(1)));
    const bdHrs = Math.max(0, parseFloat((bdMin / 60).toFixed(1)));

    const ctxTime = document.getElementById('chart-pie-time')?.getContext('2d');
    if (ctxTime) {
        if (chartPieTime) chartPieTime.destroy();
        chartPieTime = new Chart(ctxTime, {
            type: 'doughnut',
            data: {
                labels: ['Runtime (hrs)', 'Idle Time (hrs)', 'Breakdown (hrs)'],
                datasets: [{
                    data: [runHrs, idleHrs, bdHrs],
                    backgroundColor: ['#22c55e', '#f59e0b', '#ef4444'],
                    borderWidth: 2,
                    borderColor: '#0f1520'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: { color: '#94a3b8', font: { family: 'Inter', size: 11 } }
                    }
                }
            }
        });
    }

    // 2. Target vs Production pie chart
    const shortfall = Math.max(0, target - prod);
    const ctxProd = document.getElementById('chart-pie-production')?.getContext('2d');
    if (ctxProd) {
        if (chartPieProduction) chartPieProduction.destroy();
        chartPieProduction = new Chart(ctxProd, {
            type: 'doughnut',
            data: {
                labels: ['Achieved Production', 'Target Shortfall'],
                datasets: [{
                    data: [prod, shortfall],
                    backgroundColor: ['#00d4ff', 'rgba(255,255,255,0.12)'],
                    borderWidth: 2,
                    borderColor: '#0f1520'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: { color: '#94a3b8', font: { family: 'Inter', size: 11 } }
                    }
                }
            }
        });
    }
}

function updateReportTable(records) {
    const tbody = document.getElementById('report-tbody');
    const countEl = document.getElementById('report-record-count');
    if (!tbody) return;

    tbody.innerHTML = '';
    if (countEl) countEl.textContent = `${records.length} shifts`;

    if (records.length === 0) {
        tbody.innerHTML = `<tr><td colspan="9" style="text-align: center; color: var(--text-muted); padding: 20px;">No shift records found for selected period.</td></tr>`;
        return;
    }

    records.forEach(r => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td style="font-family:'JetBrains Mono',monospace; font-weight:600;">${r.date}</td>
            <td>${r.shift_name}</td>
            <td><strong style="color:var(--cyan);">${r.line_name}</strong></td>
            <td style="font-family:'JetBrains Mono',monospace;">${r.target}</td>
            <td style="font-family:'JetBrains Mono',monospace; color:#50c878; font-weight:700;">${r.production}</td>
            <td style="font-family:'JetBrains Mono',monospace;">${r.runtime_minutes}</td>
            <td style="font-family:'JetBrains Mono',monospace; color:#f0a500;">${r.idle_time_minutes}</td>
            <td style="font-family:'JetBrains Mono',monospace; color:#e74c3c;">${r.breakdown_time_minutes}</td>
            <td>
                <span style="font-family:'JetBrains Mono',monospace; font-weight:700; color: ${r.efficiency >= 90 ? '#50c878' : r.efficiency >= 75 ? '#f0a500' : '#e74c3c'};">
                    ${r.efficiency}%
                </span>
            </td>
        `;
        tbody.appendChild(tr);
    });
}
