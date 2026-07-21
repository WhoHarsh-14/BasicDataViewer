/**
 * shift.js — Shift Control Panel
 *
 * Polls /api/shift/status every 5 seconds and updates the UI.
 * Handles the Force End Shift button with a confirmation dialog.
 * Fetches recent shift history on load.
 */

(function () {
  'use strict';

  // ── Helpers ───────────────────────────────────────────────────

  function fmt(isoStr) {
    if (!isoStr) return '—';
    const d = new Date(isoStr);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
      + ' · ' + d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  function fmtSeconds(secs) {
    if (secs == null || secs < 0) return '—';
    const h = Math.floor(secs / 3600);
    const m = Math.floor((secs % 3600) / 60);
    const s = Math.floor(secs % 60);
    if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m ${String(s).padStart(2, '0')}s`;
    if (m > 0) return `${m}m ${String(s).padStart(2, '0')}s`;
    return `${s}s`;
  }

  function fmtRows(n) {
    if (n == null) return '—';
    return Number(n).toLocaleString();
  }

  // ── DOM refs ──────────────────────────────────────────────────

  const elStart     = document.getElementById('shift-start-time');
  const elEnd       = document.getElementById('shift-end-time');
  const elRows      = document.getElementById('shift-rows-buffered');
  const elRemaining = document.getElementById('shift-time-remaining');
  const elBar       = document.getElementById('shift-progress-bar');
  const elBadge     = document.getElementById('shift-status-badge');
  const elHistory   = document.getElementById('shift-history-list');
  const btnForce    = document.getElementById('btn-force-end-shift');

  // ── Fetch shift status ────────────────────────────────────────

  async function fetchStatus() {
    try {
      const res = await fetch('/api/shift/status');
      if (!res.ok) return;
      const data = await res.json();

      if (!data.initialized) {
        if (elBadge) { elBadge.textContent = 'STARTING'; elBadge.style.color = '#f0a500'; }
        return;
      }

      // Update cells
      if (elStart) elStart.textContent = fmt(data.shift_start);
      if (elEnd)   elEnd.textContent   = fmt(data.shift_end);
      if (elRows)  elRows.textContent  = fmtRows(data.rows_buffered);
      if (elRemaining) elRemaining.textContent = fmtSeconds(data.seconds_remaining);

      // Progress bar
      if (elBar && data.shift_duration_hours && data.seconds_remaining != null) {
        const totalSecs = data.shift_duration_hours * 3600;
        const elapsed   = totalSecs - data.seconds_remaining;
        const pct       = Math.min(100, Math.max(0, (elapsed / totalSecs) * 100));
        elBar.style.width = pct.toFixed(1) + '%';

        // Colour shifts from green→amber→red as shift fills
        if (pct < 60) {
          elBar.style.background = 'linear-gradient(90deg,#50c878,#50c878)';
        } else if (pct < 85) {
          elBar.style.background = 'linear-gradient(90deg,#50c878,#f0a500)';
        } else {
          elBar.style.background = 'linear-gradient(90deg,#f0a500,#e74c3c)';
        }
      }

      // Badge
      if (elBadge) {
        elBadge.textContent = 'ACTIVE';
        elBadge.style.color = '#50c878';
        elBadge.style.borderColor = 'rgba(80,200,120,0.3)';
        elBadge.style.background  = 'rgba(80,200,120,0.15)';
      }

    } catch (err) {
      console.warn('[shift] Status fetch error:', err);
    }
  }

  // ── Fetch shift history ───────────────────────────────────────

  async function fetchHistory() {
    if (!elHistory) return;
    try {
      const res = await fetch('/api/shift/history?limit=5');
      if (!res.ok) return;
      const records = await res.json();

      if (!records.length) {
        elHistory.innerHTML = '<div style="font-size:12px;color:var(--text-secondary);">No completed shifts yet this session.</div>';
        return;
      }

      elHistory.innerHTML = records.map(r => `
        <div style="display:flex; align-items:center; gap:12px; padding:8px 12px;
                    background:rgba(255,255,255,0.03); border-radius:8px;
                    border:1px solid var(--border); font-size:12px;">
          <svg viewBox="0 0 24 24" fill="none" stroke="#50c878" stroke-width="2" width="14" height="14">
            <polyline points="20,6 9,17 4,12"/>
          </svg>
          <div style="flex:1;">
            <span style="color:var(--text-primary); font-weight:600;">
              ${fmt(r.shift_start)} → ${r.shift_end ? new Date(r.shift_end).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) : '?'}
            </span>
          </div>
          <span style="color:#50c878; font-weight:700;">${fmtRows(r.row_count)} rows</span>
          <span style="color:var(--text-secondary);">${r.uploaded_at ? fmt(r.uploaded_at) : ''}</span>
        </div>
      `).join('');
    } catch (err) {
      console.warn('[shift] History fetch error:', err);
    }
  }

  // ── Force End Shift ───────────────────────────────────────────

  let _forcing = false;

  if (btnForce) {
    btnForce.addEventListener('click', async () => {
      if (_forcing) return;

      // Confirmation dialog
      const confirmed = window.confirm(
        '⚠️  Force End Current Shift?\n\n' +
        'This will immediately:\n' +
        '  • Flush all buffered readings to the database\n' +
        '  • Write & archive the shift CSV file\n' +
        '  • Start a fresh shift window\n\n' +
        'Proceed?'
      );
      if (!confirmed) return;

      _forcing = true;
      btnForce.disabled = true;
      btnForce.textContent = 'Committing…';
      if (elBadge) {
        elBadge.textContent = 'COMMITTING';
        elBadge.style.color = '#f0a500';
        elBadge.style.background = 'rgba(240,165,0,0.15)';
        elBadge.style.borderColor = 'rgba(240,165,0,0.3)';
      }

      try {
        const res  = await fetch('/api/shift/force-end', { method: 'POST' });
        const data = await res.json();

        if (data.status === 'success') {
          // Flash success state
          btnForce.textContent = `✓ Committed ${fmtRows(data.rows_committed)} rows`;
          btnForce.style.background = 'linear-gradient(135deg,#27ae60,#2ecc71)';
          if (elBadge) {
            elBadge.textContent = 'NEW SHIFT';
            elBadge.style.color = '#50c878';
          }

          // Refresh history
          await fetchHistory();
          await fetchStatus();

          // Restore button after 2.5s
          setTimeout(() => {
            btnForce.innerHTML = `
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" width="14" height="14">
                <rect x="3" y="3" width="18" height="18" rx="2"/>
              </svg>
              Force End Shift`;
            btnForce.style.background = 'linear-gradient(135deg, #c0392b, #e74c3c)';
            btnForce.disabled = false;
            _forcing = false;
          }, 2500);

        } else {
          alert('Force-end failed: ' + (data.detail || JSON.stringify(data)));
          _resetButton();
        }

      } catch (err) {
        console.error('[shift] Force-end error:', err);
        alert('Network error during force-end. Check server logs.');
        _resetButton();
      }
    });
  }

  function _resetButton() {
    btnForce.innerHTML = `
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" width="14" height="14">
        <rect x="3" y="3" width="18" height="18" rx="2"/>
      </svg>
      Force End Shift`;
    btnForce.style.background = 'linear-gradient(135deg, #c0392b, #e74c3c)';
    btnForce.disabled = false;
    _forcing = false;
    if (elBadge) {
      elBadge.textContent = 'ACTIVE';
      elBadge.style.color = '#50c878';
      elBadge.style.background  = 'rgba(80,200,120,0.15)';
      elBadge.style.borderColor = 'rgba(80,200,120,0.3)';
    }
  }

  // ── Shift Config ──────────────────────────────────────────────

  const btnSaveShiftCfg = document.getElementById('btn-save-shift-config');
  const inputDuration = document.getElementById('input-shift-duration');
  const inputStartHour = document.getElementById('input-shift-start-hour');

  async function loadSystemConfig() {
    try {
      const res = await fetch('/api/system/config');
      if (res.ok) {
        const data = await res.json();
        if (inputDuration && data.shift_duration_hours) inputDuration.value = data.shift_duration_hours;
        if (inputStartHour && data.shift_start_hour != null) inputStartHour.value = data.shift_start_hour;
      }
    } catch (e) {
      console.warn('[shift] Config fetch error:', e);
    }
  }

  if (btnSaveShiftCfg) {
    btnSaveShiftCfg.addEventListener('click', async () => {
      const dur = parseInt(inputDuration?.value, 10);
      const start = parseInt(inputStartHour?.value, 10);
      if (!dur || isNaN(start)) return alert('Please enter valid shift duration and start hour.');

      try {
        btnSaveShiftCfg.textContent = 'Saving…';
        btnSaveShiftCfg.disabled = true;
        const res = await fetch('/api/system/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ shift_duration_hours: dur, shift_start_hour: start })
        });
        if (res.ok) {
          alert('✓ Shift configuration saved and applied live!');
          fetchStatus();
        } else {
          alert('Failed to save shift configuration');
        }
      } catch (e) {
        console.error('Error saving shift config:', e);
      } finally {
        btnSaveShiftCfg.textContent = 'Save Shift Config';
        btnSaveShiftCfg.disabled = false;
      }
    });
  }

  // ── Init ──────────────────────────────────────────────────────

  // Initial load
  fetchStatus();
  fetchHistory();
  loadSystemConfig();

  // Poll status every 5 seconds
  setInterval(fetchStatus, 5000);

  // Refresh history after every forced shift end (already done in handler)
  // and every 60s passively
  setInterval(fetchHistory, 60000);

})();
