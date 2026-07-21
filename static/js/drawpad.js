/**
 * drawpad.js — Drawing pad page with HTML5 Canvas
 */

let drawCanvas, drawCtx;
let isDrawing = false;
let lastPoint = null;
let drawCount = 0;
let drawThrottle = 0;

document.addEventListener('DOMContentLoaded', () => {
    drawCanvas = document.getElementById('draw-canvas');
    if (!drawCanvas) return;

    drawCtx = drawCanvas.getContext('2d');
    drawCtx.lineCap = 'round';
    drawCtx.lineJoin = 'round';
    drawCtx.lineWidth = 3;
    drawCtx.strokeStyle = '#22d3ee';

    // ── Mouse Events ──
    drawCanvas.addEventListener('mousedown', onDrawStart);
    drawCanvas.addEventListener('mousemove', onDrawMove);
    drawCanvas.addEventListener('mouseup', onDrawEnd);
    drawCanvas.addEventListener('mouseleave', onDrawEnd);

    // ── Touch Events ──
    drawCanvas.addEventListener('touchstart', e => {
        e.preventDefault();
        const touch = e.touches[0];
        onDrawStart(touchToMouse(touch));
    });
    drawCanvas.addEventListener('touchmove', e => {
        e.preventDefault();
        const touch = e.touches[0];
        onDrawMove(touchToMouse(touch));
    });
    drawCanvas.addEventListener('touchend', onDrawEnd);

    // ── Clear Button ──
    document.getElementById('btn-clear-canvas').addEventListener('click', clearCanvas);
});


function touchToMouse(touch) {
    const rect = drawCanvas.getBoundingClientRect();
    return {
        offsetX: touch.clientX - rect.left,
        offsetY: touch.clientY - rect.top,
    };
}


// ══════════════════════════════════════
// DRAWING
// ══════════════════════════════════════
function onDrawStart(e) {
    isDrawing = true;
    const x = Math.round(e.offsetX);
    const y = Math.round(e.offsetY);
    lastPoint = { x, y };

    drawCtx.beginPath();
    drawCtx.moveTo(x, y);

    updateCoords(x, y);
    sendCoordinate(x, y);
}

function onDrawMove(e) {
    if (!isDrawing) return;

    const x = Math.round(e.offsetX);
    const y = Math.round(e.offsetY);

    drawCtx.lineTo(x, y);
    drawCtx.stroke();
    drawCtx.beginPath();
    drawCtx.moveTo(x, y);

    updateCoords(x, y);

    // Throttle PLC writes — send every 3rd point
    drawThrottle++;
    if (drawThrottle % 3 === 0) {
        sendCoordinate(x, y);
    }
}

function onDrawEnd() {
    isDrawing = false;
    lastPoint = null;
    drawCtx.beginPath();
}


// ══════════════════════════════════════
// SEND TO BACKEND → PLC
// ══════════════════════════════════════
async function sendCoordinate(x, y) {
    const activePLC = window.APP.activePLC;
    if (!activePLC || !activePLC.ip || !activePLC.port) {
        console.warn("No active PLC connected. Cannot send sketch coordinates.");
        return;
    }

    const regInput = document.getElementById('input-draw-register');
    const register = regInput ? regInput.value.trim() : 'D200';

    try {
        const resp = await fetch('/api/draw', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                x,
                y,
                plc_ip: activePLC.ip,
                plc_port: activePLC.port,
                register: register || 'D200'
            }),
        });

        if (resp.ok) {
            const data = await resp.json();
            drawCount++;
            document.getElementById('draw-count').textContent = `${drawCount} sent`;
            addDrawLogEntry(data);
        }
    } catch (e) {
        console.error('Draw send error:', e);
    }
}


// ══════════════════════════════════════
// COORDINATE LOG TABLE
// ══════════════════════════════════════
function addDrawLogEntry(data) {
    const tbody = document.getElementById('draw-tbody');
    if (!tbody) return;

    const tr = document.createElement('tr');
    const ts = new Date(data.timestamp);
    tr.innerHTML = `
        <td>${ts.toLocaleTimeString('en-IN', { hour12: false })}</td>
        <td class="mono" style="font-size: 12.5px; color: var(--cyan);">${data.register || 'D200'}</td>
        <td>${data.x}</td>
        <td>${data.y}</td>
    `;

    // Prepend (newest first)
    tbody.insertBefore(tr, tbody.firstChild);

    // Limit to 100 rows
    while (tbody.children.length > 100) {
        tbody.removeChild(tbody.lastChild);
    }
}


// ══════════════════════════════════════
// UTILS
// ══════════════════════════════════════
function updateCoords(x, y) {
    document.getElementById('coord-x').textContent = x;
    document.getElementById('coord-y').textContent = y;
}

function clearCanvas() {
    drawCtx.clearRect(0, 0, drawCanvas.width, drawCanvas.height);
    drawCount = 0;
    document.getElementById('draw-count').textContent = '0 sent';
    document.getElementById('draw-tbody').innerHTML = '';
}
