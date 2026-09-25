/**
 * TSCM (counter-surveillance) mode on the main page: sweeps, device lists,
 * correlations, threat scoring, cases, reports, meetings and playbooks.
 *
 * Moved out of templates/index.html unchanged. It is a classic script loaded
 * straight after the page's main inline script, so it shares that script's
 * top-level names (both ways) and runs in the same order as before.
 */
// ============================================
// TSCM (Counter-Surveillance) Functions
// ============================================
let isTscmRunning = false;
let tscmEventSource = null;
let tscmThreats = [];
let tscmWifiDevices = [];
let tscmWifiClients = [];
let tscmBtDevices = [];
// Devices marked as cleared by investigator (reset each sweep)
let tscmClearedDevices = new Set();
// Persistent ignore list — examiner's own devices, excluded from display and reports
let tscmIgnoreList = (() => {
    try { return new Set(JSON.parse(localStorage.getItem('tscmIgnoreList') || '[]')); }
    catch(e) { return new Set(); }
})();
let tscmBaselineComparison = null;
let tscmIdentityClusters = [];
let tscmIdentitySummary = null;
let tscmCaseLinkContext = null;
let tscmLastSweepId = null;
let tscmLastMeetingId = null;
const tscmFilters = {
    protocol: 'all',
    risk: 'all',
    status: 'all',
    known: 'all',
};
let isRecordingBaseline = false;
let tscmSweepStartTime = null;
let tscmSweepEndTime = null;

async function refreshTscmDevices() {
    // Fetch available interfaces for TSCM scanning
    // Check if agent is selected and route accordingly
    try {
        let response;
        if (typeof currentAgent !== 'undefined' && currentAgent !== 'local') {
            // Fetch devices from agent capabilities
            response = await fetch(`/controller/agents/${currentAgent}?refresh=true`);
        } else {
            response = await fetch('/tscm/devices');
        }
        const data = await response.json();

        // Handle both local (/tscm/devices) and agent response formats
        let devices;
        const isAgentResponse = typeof currentAgent !== 'undefined' && currentAgent !== 'local';

        if (isAgentResponse && data.agent) {
            // Agent response format - extract from capabilities/interfaces
            const agentInterfaces = data.agent.interfaces || {};
            const agentCapabilities = data.agent.capabilities || {};
            devices = {
                wifi_interfaces: agentInterfaces.wifi_interfaces || [],
                bt_adapters: agentInterfaces.bt_adapters || [],
                sdr_devices: agentCapabilities.devices || agentInterfaces.sdr_devices || []
            };
        } else {
            devices = data.devices || {};
        }

        // Populate WiFi interfaces
        const wifiSelect = document.getElementById('tscmWifiInterface');
        wifiSelect.innerHTML = '<option value="">Select WiFi interface...</option>';
        if (devices.wifi_interfaces && devices.wifi_interfaces.length > 0) {
            devices.wifi_interfaces.forEach(iface => {
                const opt = document.createElement('option');
                opt.value = iface.name;
                opt.textContent = iface.display_name || iface.name;
                wifiSelect.appendChild(opt);
            });
            // Auto-select first interface
            if (devices.wifi_interfaces.length > 0) {
                wifiSelect.value = devices.wifi_interfaces[0].name;
            }
        } else {
            if (isAgentResponse) {
                wifiSelect.innerHTML = '<option value="">Agent manages WiFi</option>';
            } else {
                wifiSelect.innerHTML = '<option value="">No WiFi interfaces found</option>';
            }
        }

        // Populate Bluetooth adapters
        const btSelect = document.getElementById('tscmBtInterface');
        btSelect.innerHTML = '<option value="">Select Bluetooth adapter...</option>';
        if (devices.bt_adapters && devices.bt_adapters.length > 0) {
            devices.bt_adapters.forEach(adapter => {
                const opt = document.createElement('option');
                opt.value = adapter.name;
                opt.textContent = adapter.display_name || adapter.name;
                btSelect.appendChild(opt);
            });
            // Auto-select first adapter
            if (devices.bt_adapters.length > 0) {
                btSelect.value = devices.bt_adapters[0].name;
            }
        } else {
            if (isAgentResponse) {
                btSelect.innerHTML = '<option value="">Agent manages Bluetooth</option>';
            } else {
                btSelect.innerHTML = '<option value="">No Bluetooth adapters found</option>';
            }
        }

        // Populate SDR devices
        const sdrSelect = document.getElementById('tscmSdrDevice');
        sdrSelect.innerHTML = '<option value="">Select SDR device...</option>';
        if (devices.sdr_devices && devices.sdr_devices.length > 0) {
            devices.sdr_devices.forEach(dev => {
                const opt = document.createElement('option');
                opt.value = dev.index !== undefined ? dev.index : 0;
                opt.textContent = dev.display_name || dev.name || 'SDR Device';
                sdrSelect.appendChild(opt);
            });
            // Auto-select first SDR if available
            if (devices.sdr_devices.length > 0) {
                sdrSelect.value = devices.sdr_devices[0].index !== undefined ? devices.sdr_devices[0].index : 0;
            }
        } else {
            if (isAgentResponse) {
                sdrSelect.innerHTML = '<option value="">Agent manages SDR</option>';
            } else {
                sdrSelect.innerHTML = '<option value="">No SDR devices found</option>';
            }
        }

        // Show warnings (e.g., not running as root)
        const warningsDiv = document.getElementById('tscmDeviceWarnings');
        if (data.warnings && data.warnings.length > 0) {
            warningsDiv.innerHTML = data.warnings.map(w =>
                `<div class="tscm-privilege-warning">
                    <span class="warning-icon">⚠️</span>
                    <div>
                        <strong>${escapeHtml(w.message)}</strong>
                        ${w.action ? `<div class="warning-action">${escapeHtml(w.action)}</div>` : ''}
                    </div>
                </div>`
            ).join('');
            warningsDiv.style.display = 'block';
        } else {
            warningsDiv.style.display = 'none';
        }

    } catch (e) {
        console.error('Failed to refresh TSCM devices:', e);
    }
}

async function loadTscmBaselines() {
    try {
        const response = await fetch('/tscm/baselines');
        const data = await response.json();
        const select = document.getElementById('tscmBaselineSelect');
        select.innerHTML = '<option value="">No Baseline</option>';
        if (data.baselines) {
            data.baselines.forEach(b => {
                const opt = document.createElement('option');
                opt.value = b.id;
                opt.textContent = b.name + (b.is_active ? ' (Active)' : '');
                select.appendChild(opt);
            });
        }
    } catch (e) {
        console.error('Failed to load baselines:', e);
    }
}

async function startTscmSweep() {
    const sweepType = document.getElementById('tscmSweepType').value;
    const baselineId = document.getElementById('tscmBaselineSelect').value || null;
    const customRanges = sweepType === 'custom' ? [{
        start: parseFloat(document.getElementById('tscmCustomStartMhz').value),
        end: parseFloat(document.getElementById('tscmCustomEndMhz').value),
        step: 0.1,
        name: `Custom ${document.getElementById('tscmCustomStartMhz').value}–${document.getElementById('tscmCustomEndMhz').value} MHz`
    }] : null;
    const wifiEnabled = document.getElementById('tscmWifiEnabled').checked;
    const btEnabled = document.getElementById('tscmBtEnabled').checked;
    const rfEnabled = document.getElementById('tscmRfEnabled').checked;
    const wifiInterface = document.getElementById('tscmWifiInterface').value;
    const btInterface = document.getElementById('tscmBtInterface').value;
    const sdrDevice = document.getElementById('tscmSdrDevice').value;
    const verboseResults = document.getElementById('tscmVerboseResults').checked;

    // Clear any previous warnings
    document.getElementById('tscmDeviceWarnings').style.display = 'none';
    document.getElementById('tscmDeviceWarnings').innerHTML = '';

    // Check for agent mode
    const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';

    // Check for conflicts if using agent
    if (isAgentMode && typeof checkAgentModeConflict === 'function') {
        if (!await checkAgentModeConflict('tscm')) {
            return;  // Conflict detected, user cancelled
        }
    }

    try {
        // Route to agent or local based on selection
        const endpoint = isAgentMode
            ? `/controller/agents/${currentAgent}/tscm/start`
            : '/tscm/sweep/start';

        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                sweep_type: sweepType,
                baseline_id: baselineId ? parseInt(baselineId) : null,
                wifi: wifiEnabled,
                bluetooth: btEnabled,
                rf: rfEnabled,
                wifi_interface: wifiInterface,
                bt_interface: btInterface,
                sdr_device: sdrDevice ? parseInt(sdrDevice) : null,
                verbose_results: verboseResults,
                custom_ranges: customRanges
            })
        });

        const data = await response.json();
        // Handle controller proxy response (agent response is nested in 'result')
        const scanResult = isAgentMode && data.result ? data.result : data;
        if (scanResult.status === 'success' || scanResult.status === 'started') {
            if (scanResult.sweep_id) {
                tscmLastSweepId = scanResult.sweep_id;
            }
            isTscmRunning = true;
            tscmSweepStartTime = new Date();
            tscmSweepEndTime = null;
            document.getElementById('startTscmBtn').style.display = 'none';
            document.getElementById('stopTscmBtn').style.display = 'block';
            document.getElementById('tscmProgress').style.display = 'flex';

            // Clear and reset the signal timeline for new sweep
            SignalTimeline.clear();
            document.getElementById('tscmReportBtn').style.display = 'none';
            document.getElementById('tscmReportCategoryFilter').style.display = 'none';

            // Show warnings if any devices unavailable
            if (scanResult.warnings && scanResult.warnings.length > 0) {
                const warningsDiv = document.getElementById('tscmDeviceWarnings');
                warningsDiv.innerHTML = scanResult.warnings.map(w =>
                    `<div style="color: var(--accent-amber); font-size: 10px; margin-bottom: 2px;">⚠ ${w}</div>`
                ).join('');
                warningsDiv.style.display = 'block';
            }

            // Update device indicators
            updateTscmDeviceIndicators(scanResult.devices);

            // Reset displays
            tscmThreats = [];
            tscmWifiDevices = [];
            tscmWifiClients = [];
            tscmBtDevices = [];
            tscmRfSignals = [];
            tscmRfStatusMessage = null;
            tscmCorrelations = [];
            tscmBaselineComparison = null;
            tscmIdentityClusters = [];
            tscmIdentitySummary = null;
            tscmHighInterestDevices = [];
            tscmClearedDevices = new Set();
            updateTscmDisplays();
            updateTscmThreatCounts();

            // Update capabilities bar for this sweep
            updateTscmCapabilitiesBar(wifiInterface, btInterface);
            // Update baseline health indicator if baseline selected
            if (baselineId) {
                updateTscmBaselineHealth(baselineId);
            }

            // Start SSE stream
            startTscmStream();
        } else {
            // Show error with details
            let errorMsg = scanResult.message || 'Failed to start sweep';
            if (scanResult.details && scanResult.details.length > 0) {
                errorMsg += '\n\n' + scanResult.details.join('\n');
            }
            alert(errorMsg);
        }
    } catch (e) {
        console.error('Failed to start TSCM sweep:', e);
        alert('Failed to start sweep: Network error');
    }
}

function updateTscmDeviceIndicators(devices) {
    const wifiIndicator = document.getElementById('tscmWifiIndicator');
    const btIndicator = document.getElementById('tscmBtIndicator');
    const rfIndicator = document.getElementById('tscmRfIndicator');

    // Safety check for agent mode which may not return devices
    if (!devices) {
        // Just mark all as active if we don't have device info
        if (wifiIndicator) wifiIndicator.classList.add('active');
        if (btIndicator) btIndicator.classList.add('active');
        if (rfIndicator) rfIndicator.classList.add('active');
        return;
    }

    if (wifiIndicator) {
        wifiIndicator.classList.toggle('active', devices.wifi);
        wifiIndicator.classList.toggle('inactive', !devices.wifi);
    }
    if (btIndicator) {
        btIndicator.classList.toggle('active', devices.bluetooth);
        btIndicator.classList.toggle('inactive', !devices.bluetooth);
    }
    if (rfIndicator) {
        rfIndicator.classList.toggle('active', devices.rf);
        rfIndicator.classList.toggle('inactive', !devices.rf);
    }
}

async function stopTscmSweep() {
    const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';
    const endpoint = isAgentMode
        ? `/controller/agents/${currentAgent}/tscm/stop`
        : '/tscm/sweep/stop';
    const timeoutMs = isAgentMode ? REMOTE_STOP_TIMEOUT_MS : LOCAL_STOP_TIMEOUT_MS;

    isTscmRunning = false;
    tscmSweepEndTime = new Date();
    if (tscmEventSource) {
        tscmEventSource.close();
        tscmEventSource = null;
    }
    if (typeof tscmAgentPollInterval !== 'undefined' && tscmAgentPollInterval) {
        clearInterval(tscmAgentPollInterval);
        tscmAgentPollInterval = null;
    }

    document.getElementById('startTscmBtn').style.display = 'block';
    document.getElementById('stopTscmBtn').style.display = 'none';
    document.getElementById('tscmProgress').style.display = 'none';

    // Show report button and category filter if we have any data
    const hasData = tscmWifiDevices.length > 0 || tscmBtDevices.length > 0 || tscmRfSignals.length > 0;
    document.getElementById('tscmReportBtn').style.display = hasData ? 'block' : 'none';
    document.getElementById('tscmReportCategoryFilter').style.display = hasData ? 'block' : 'none';

    return postStopRequest(endpoint, timeoutMs);
}

function generateTscmReport() {
    // Calculate sweep duration
    const startTime = tscmSweepStartTime || new Date();
    const endTime = tscmSweepEndTime || new Date();
    const durationMs = endTime - startTime;
    const durationMin = Math.floor(durationMs / 60000);
    const durationSec = Math.floor((durationMs % 60000) / 1000);

    // Read sweep metadata
    const siteName = document.getElementById('tscmSiteName')?.value?.trim() || '';
    const examinerName = document.getElementById('tscmExaminerName')?.value?.trim() || '';

    // Categorize devices by classification — exclude cleared and ignored devices from reports
    const allDevicesRaw = [
        ...tscmWifiDevices.map(d => ({ ...d, protocol: 'WiFi', _key: _tscmDeviceKey(d, 'wifi') })),
        ...tscmBtDevices.map(d => ({ ...d, protocol: 'Bluetooth', _key: _tscmDeviceKey(d, 'bluetooth') })),
        ...tscmRfSignals.map(d => ({ ...d, protocol: 'RF', _key: _tscmDeviceKey(d, 'rf') }))
    ];
    const clearedCount = allDevicesRaw.filter(d => tscmClearedDevices.has(d._key)).length;
    const allDevices = allDevicesRaw.filter(d => !tscmClearedDevices.has(d._key));

    const allHighInterest = allDevices.filter(d => d.classification === 'high_interest' || d.score >= 6);
    const allNeedsReview = allDevices.filter(d => d.classification === 'review' || (d.score >= 3 && d.score < 6));
    const allInformational = allDevices.filter(d => d.classification === 'informational' || d.score < 3);

    // Apply category filter from sidebar checkboxes
    const incHighInterest = document.getElementById('tscmCatHighInterest')?.checked ?? true;
    const incNeedsReview = document.getElementById('tscmCatNeedsReview')?.checked ?? true;
    const incInformational = document.getElementById('tscmCatInformational')?.checked ?? false;
    const highInterest = incHighInterest ? allHighInterest : [];
    const needsReview = incNeedsReview ? allNeedsReview : [];
    const informational = incInformational ? allInformational : [];

    const categoryNote = (!incHighInterest || !incNeedsReview || !incInformational)
        ? `<div style="font-size:11px; color:var(--text-dim); margin-top:8px;">Filtered: showing ${[incHighInterest && 'High Interest', incNeedsReview && 'Needs Review', incInformational && 'Informational'].filter(Boolean).join(', ') || 'none'} only</div>`
        : '';

    // Determine overall assessment (based on all data, not filter)
    let assessment = 'LOW CONCERN';
    let assessmentClass = 'informational';
    if (allHighInterest.length > 0) {
        assessment = `ELEVATED CONCERN: ${allHighInterest.length} high-interest item(s) detected requiring immediate attention`;
        assessmentClass = 'high-interest';
    } else if (allNeedsReview.length > 0) {
        assessment = `MODERATE CONCERN: ${allNeedsReview.length} item(s) requiring further review`;
        assessmentClass = 'needs-review';
    } else {
        assessment = 'LOW CONCERN: No anomalies flagged by automated scan. Manual inspection recommended for comprehensive assessment.';
    }

    // Helper function to render device row
    const renderDevice = (device) => {
        const scoreClass = device.score >= 6 ? 'high' : (device.score >= 3 ? 'medium' : 'low');
        const indicators = (device.indicators || []).map(i =>
            `<span class="indicator">${i.type}: ${i.desc}</span>`
        ).join('');
        const reasons = (device.reasons || []).map(r => `<li>${r}</li>`).join('');

        let identifier = device.bssid || device.mac || (device.frequency ? `${device.frequency} MHz` : 'Unknown');
        let name = device.essid || device.name || device.band || 'Unknown';

        return `
            <tr class="device-row ${device.classification || ''}">
                <td><span class="protocol-badge ${device.protocol.toLowerCase()}">${device.protocol}</span></td>
                <td><strong>${name}</strong><br><small class="identifier">${identifier}</small></td>
                <td><span class="score-badge ${scoreClass}">${device.score || 0}</span></td>
                <td>${device.classification || 'unknown'}</td>
                <td>${device.signal || device.rssi || device.power || 'N/A'} dBm</td>
                <td>
                    ${indicators ? `<div class="indicators">${indicators}</div>` : ''}
                    ${reasons ? `<ul class="reasons">${reasons}</ul>` : ''}
                </td>
                <td>${device.recommended_action || 'monitor'}</td>
            </tr>
        `;
    };

    // Generate HTML report
    const reportHtml = `
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TSCM Sweep Report - ${startTime.toLocaleDateString()}</title>
    <style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
    background: #1a1a2e;
    color: #e8eaed;
    padding: 40px;
    line-height: 1.6;
}
.report-container {
    max-width: 1200px;
    margin: 0 auto;
    background: #0f1218;
    border-radius: 12px;
    overflow: hidden;
    box-shadow: 0 20px 60px rgba(0,0,0,0.5);
}
.report-header {
    background: linear-gradient(135deg, #1a1a2e 0%, #0f1218 100%);
    padding: 40px;
    border-bottom: 1px solid #2a2a4a;
}
.report-title {
    font-size: 28px;
    font-weight: 700;
    color: #4a9eff;
    margin-bottom: 8px;
}
.report-subtitle {
    font-size: 14px;
    color: #9ca3af;
    letter-spacing: 1px;
    text-transform: uppercase;
}
.report-meta {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 20px;
    margin-top: 30px;
    padding: 20px;
    background: rgba(0,0,0,0.3);
    border-radius: 8px;
}
.meta-item {
    text-align: center;
}
.meta-value {
    font-size: 24px;
    font-weight: 700;
    color: #fff;
}
.meta-label {
    font-size: 11px;
    color: #6b7280;
    text-transform: uppercase;
    letter-spacing: 1px;
}
.section {
    padding: 30px 40px;
    border-bottom: 1px solid #2a2a4a;
}
.section:last-child {
    border-bottom: none;
}
.section-title {
    font-size: 18px;
    font-weight: 600;
    color: #4a9eff;
    margin-bottom: 20px;
    display: flex;
    align-items: center;
    gap: 10px;
}
.assessment {
    padding: 20px;
    border-radius: 8px;
    font-size: 14px;
    font-weight: 500;
}
.assessment.high-interest {
    background: rgba(255, 51, 51, 0.15);
    border: 1px solid #ff3333;
    color: #ff6666;
}
.assessment.needs-review {
    background: rgba(255, 204, 0, 0.15);
    border: 1px solid #ffcc00;
    color: #ffdd44;
}
.assessment.informational {
    background: rgba(0, 204, 0, 0.15);
    border: 1px solid #00cc00;
    color: #44dd44;
}
.summary-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 16px;
    margin-bottom: 20px;
}
.summary-card {
    background: rgba(0,0,0,0.3);
    padding: 20px;
    border-radius: 8px;
    text-align: center;
    border: 1px solid #2a2a4a;
}
.summary-card.high-interest { border-color: #ff3333; }
.summary-card.needs-review { border-color: #ffcc00; }
.summary-card.informational { border-color: #00cc00; }
.summary-card .count {
    font-size: 32px;
    font-weight: 700;
}
.summary-card.high-interest .count { color: #ff3333; }
.summary-card.needs-review .count { color: #ffcc00; }
.summary-card.informational .count { color: #00cc00; }
.summary-card .label {
    font-size: 11px;
    color: #6b7280;
    text-transform: uppercase;
    margin-top: 4px;
}
table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}
th {
    text-align: left;
    padding: 12px;
    background: rgba(0,0,0,0.4);
    color: #9ca3af;
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    border-bottom: 1px solid #2a2a4a;
}
td {
    padding: 12px;
    border-bottom: 1px solid #2a2a4a;
    vertical-align: top;
}
.device-row.high_interest { background: rgba(255, 51, 51, 0.08); }
.device-row.review { background: rgba(255, 204, 0, 0.08); }
.protocol-badge {
    display: inline-block;
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
}
.protocol-badge.wifi { background: #4a9eff; color: #000; }
.protocol-badge.bluetooth { background: #8b5cf6; color: #fff; }
.protocol-badge.rf { background: #f59e0b; color: #000; }
.score-badge {
    display: inline-block;
    padding: 4px 10px;
    border-radius: 12px;
    font-weight: 600;
    font-size: 12px;
}
.score-badge.high { background: rgba(255,51,51,0.2); color: #ff3333; }
.score-badge.medium { background: rgba(255,204,0,0.2); color: #ffcc00; }
.score-badge.low { background: rgba(0,204,0,0.2); color: #00cc00; }
.identifier {
    color: #6b7280;
    font-family: monospace;
    font-size: 11px;
}
.indicators {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-bottom: 6px;
}
.indicator {
    display: inline-block;
    padding: 2px 6px;
    background: rgba(255,153,51,0.2);
    color: #ff9933;
    border-radius: 3px;
    font-size: 10px;
}
.reasons {
    margin: 0;
    padding-left: 16px;
    font-size: 11px;
    color: #9ca3af;
}
.reasons li {
    margin-bottom: 2px;
}
.category-section {
    margin-bottom: 30px;
}
.category-title {
    font-size: 14px;
    font-weight: 600;
    margin-bottom: 12px;
    padding: 8px 12px;
    border-radius: 6px;
}
.category-title.high-interest { background: rgba(255,51,51,0.15); color: #ff6666; }
.category-title.needs-review { background: rgba(255,204,0,0.15); color: #ffdd44; }
.category-title.informational { background: rgba(0,204,0,0.15); color: #44dd44; }
.empty-state {
    text-align: center;
    padding: 40px;
    color: #6b7280;
}
.disclaimer {
    padding: 20px;
    background: rgba(74, 158, 255, 0.1);
    border-radius: 8px;
    font-size: 12px;
    color: #9ca3af;
}
.disclaimer h4 {
    color: #4a9eff;
    margin-bottom: 10px;
    font-size: 13px;
}
.recommendations {
    margin-top: 20px;
}
.recommendations ul {
    padding-left: 20px;
}
.recommendations li {
    margin-bottom: 8px;
}
.report-actions {
    position: fixed;
    top: 20px;
    right: 20px;
    display: flex;
    gap: 10px;
    z-index: 1000;
}
.report-btn {
    padding: 12px 24px;
    background: #4a9eff;
    color: #000;
    border: none;
    border-radius: 6px;
    font-weight: 600;
    cursor: pointer;
    font-size: 14px;
}
.report-btn:hover {
    background: #6bb3ff;
}
.report-btn.save {
    background: #22c55e;
}
.report-btn.save:hover {
    background: #2ecc71;
}
@media print {
    body { background: #fff; color: #000; padding: 20px; }
    .report-container { box-shadow: none; }
    .report-header { background: #f8f9fa; }
    .report-title { color: #1a1a2e; }
    .section { border-color: #ddd; }
    .section-title { color: #1a1a2e; }
    th { background: #f0f0f0; color: #333; }
    td { border-color: #ddd; }
    .report-actions { display: none; }
    .device-row.high_interest { background: rgba(255, 51, 51, 0.1); }
    .device-row.review { background: rgba(255, 204, 0, 0.1); }
}
    </style>
</head>
<body>
    <div class="report-actions">
<button class="report-btn save" onclick="saveReport()">Save Report</button>
<button class="report-btn" onclick="window.print()">Print Report</button>
    </div>

    <div class="report-container">
<div class="report-header">
    <div class="report-title">TSCM Sweep Report</div>
    <div class="report-subtitle">Technical Surveillance Counter-Measures Analysis</div>

    <div class="report-meta">
        <div class="meta-item">
            <div class="meta-value">${startTime.toLocaleDateString()}</div>
            <div class="meta-label">Date</div>
        </div>
        <div class="meta-item">
            <div class="meta-value">${startTime.toLocaleTimeString()} - ${endTime.toLocaleTimeString()}</div>
            <div class="meta-label">Time Range</div>
        </div>
        <div class="meta-item">
            <div class="meta-value">${durationMin}m ${durationSec}s</div>
            <div class="meta-label">Duration</div>
        </div>
        <div class="meta-item">
            <div class="meta-value">${allDevices.length}</div>
            <div class="meta-label">Total Devices</div>
        </div>
        ${siteName ? `<div class="meta-item"><div class="meta-value" style="font-size:15px;">${siteName.replace(/</g,'&lt;').replace(/>/g,'&gt;')}</div><div class="meta-label">Site / Location</div></div>` : ''}
        ${examinerName ? `<div class="meta-item"><div class="meta-value" style="font-size:15px;">${examinerName.replace(/</g,'&lt;').replace(/>/g,'&gt;')}</div><div class="meta-label">Examiner</div></div>` : ''}
    </div>
</div>

<div class="section">
    <div class="section-title">Executive Summary</div>
    <div class="summary-grid">
        <div class="summary-card high-interest">
            <div class="count">${highInterest.length}</div>
            <div class="label">High Interest</div>
        </div>
        <div class="summary-card needs-review">
            <div class="count">${needsReview.length}</div>
            <div class="label">Needs Review</div>
        </div>
        <div class="summary-card informational">
            <div class="count">${informational.length}</div>
            <div class="label">Informational</div>
        </div>
        <div class="summary-card">
            <div class="count" style="color: var(--accent-cyan);">${tscmWifiDevices.length}/${tscmBtDevices.length}/${tscmRfSignals.length}</div>
            <div class="label">WiFi/BT/RF</div>
        </div>
    </div>
    <div class="assessment ${assessmentClass}">
        <strong>Assessment:</strong> ${assessment}
    </div>
    ${categoryNote}
    ${clearedCount > 0 ? `<div style="font-size:11px;color:var(--text-dim);margin-top:8px;">${clearedCount} device(s) marked as cleared by investigator — excluded from this report.</div>` : ''}
</div>

${highInterest.length > 0 ? `
<div class="section">
    <div class="section-title">High Interest Items</div>
    <div class="category-section">
        <table>
            <thead>
                <tr>
                    <th>Type</th>
                    <th>Device</th>
                    <th>Score</th>
                    <th>Class</th>
                    <th>Signal</th>
                    <th>Indicators / Reasons</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>
                ${highInterest.map(renderDevice).join('')}
            </tbody>
        </table>
    </div>
</div>
` : ''}

${needsReview.length > 0 ? `
<div class="section">
    <div class="section-title">Items Requiring Review</div>
    <div class="category-section">
        <table>
            <thead>
                <tr>
                    <th>Type</th>
                    <th>Device</th>
                    <th>Score</th>
                    <th>Class</th>
                    <th>Signal</th>
                    <th>Indicators / Reasons</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>
                ${needsReview.map(renderDevice).join('')}
            </tbody>
        </table>
    </div>
</div>
` : ''}

${informational.length > 0 ? `
<div class="section">
    <div class="section-title">Informational Items</div>
    <div class="category-section">
        <table>
            <thead>
                <tr>
                    <th>Type</th>
                    <th>Device</th>
                    <th>Score</th>
                    <th>Class</th>
                    <th>Signal</th>
                    <th>Indicators / Reasons</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>
                ${informational.map(renderDevice).join('')}
            </tbody>
        </table>
    </div>
</div>
` : ''}

${allDevices.length === 0 ? `
<div class="section">
    <div class="empty-state">
        <p>No devices were detected during this sweep.</p>
    </div>
</div>
` : ''}

<div class="section">
    <div class="section-title">Recommendations</div>
    <div class="recommendations">
        <ul>
            ${highInterest.length > 0 ? `
            <li><strong>Immediate Action Required:</strong> ${highInterest.length} high-interest item(s) detected. These devices exhibit characteristics commonly associated with surveillance equipment and should be physically located and investigated.</li>
            ` : ''}
            ${needsReview.length > 0 ? `
            <li><strong>Further Investigation Recommended:</strong> ${needsReview.length} item(s) require additional review to determine their purpose and legitimacy.</li>
            ` : ''}
            ${allDevices.filter(d => d.is_new).length > 0 ? `
            <li><strong>New Devices Detected:</strong> ${allDevices.filter(d => d.is_new).length} device(s) were not present in the baseline. Verify these are authorized.</li>
            ` : ''}
            <li><strong>Regular Monitoring:</strong> Consider establishing a baseline of normal wireless activity and conducting periodic sweeps to detect changes.</li>
            <li><strong>Physical Inspection:</strong> For any high-interest items, conduct a thorough physical inspection of the area to locate potential surveillance devices.</li>
        </ul>
    </div>
</div>

<div class="section">
    <div class="section-title">Disclaimer</div>
    <div class="disclaimer">
        <h4>Important Notice</h4>
        <p>This report is generated by automated wireless spectrum analysis software. The findings presented are <strong>indicators only</strong> and do not constitute confirmation of surveillance activity. Many legitimate devices may trigger alerts due to their wireless characteristics.</p>
        <p style="margin-top: 10px;">Professional TSCM services involve specialized equipment and expertise beyond wireless spectrum analysis, including: non-linear junction detection, thermal imaging, physical inspection, and RF spectrum analysis with calibrated equipment.</p>
        <p style="margin-top: 10px;"><strong>No content was intercepted or decoded during this analysis.</strong> This tool only detects the presence and characteristics of wireless transmissions.</p>
    </div>
</div>
    </div>

    <scr` + `ipt>
function saveReport() {
    const html = document.documentElement.outerHTML;
    const blob = new Blob([html], { type: 'text/html' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'TSCM_Report_${startTime.toISOString().split('T')[0]}.html';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}
    </scr` + `ipt>
</body>
</html>
    `;

    // Open report in new window
    const reportWindow = window.open('', '_blank');
    reportWindow.document.write(reportHtml);
    reportWindow.document.close();
}

let tscmAgentPollInterval = null;

function startTscmStream() {
    if (tscmEventSource) {
        tscmEventSource.close();
        tscmEventSource = null;
    }
    if (tscmAgentPollInterval) {
        clearInterval(tscmAgentPollInterval);
        tscmAgentPollInterval = null;
    }

    // Check if using agent
    const isAgentMode = typeof currentAgent !== 'undefined' && currentAgent !== 'local';

    if (isAgentMode) {
        // For agent mode, poll the agent for TSCM data since push may not be enabled
        console.log('[TSCM] Starting agent polling mode');
        pollAgentTscmData();  // Initial poll
        tscmAgentPollInterval = setInterval(pollAgentTscmData, 2000);  // Poll every 2 seconds
    } else {
        // For local mode, use SSE stream
        const streamUrl = '/tscm/sweep/stream';
        tscmEventSource = new EventSource(streamUrl);

        tscmEventSource.onmessage = function (event) {
            try {
                const data = JSON.parse(event.data);
                handleTscmEvent(data);
            } catch (e) {
                console.error('TSCM SSE parse error:', e);
            }
        };

        tscmEventSource.onerror = function () {
            console.warn('TSCM SSE connection error');
        };
    }
}

async function pollAgentTscmData() {
    if (!isTscmRunning) {
        if (tscmAgentPollInterval) {
            clearInterval(tscmAgentPollInterval);
            tscmAgentPollInterval = null;
        }
        return;
    }

    try {
        const response = await fetch(`/controller/agents/${currentAgent}/tscm/data`);
        const result = await response.json();

        if (result.status === 'success' && result.data) {
            // Agent data is nested: result.data.data (controller wraps agent response)
            const data = result.data.data || result.data;

            // Process WiFi devices
            if (data.wifi_devices) {
                data.wifi_devices.forEach(device => {
                    if (!tscmWifiDevices.find(d => d.bssid === device.bssid)) {
                        handleTscmEvent({ type: 'wifi_device', ...device });
                    }
                });
            }
            if (data.wifi_clients) {
                data.wifi_clients.forEach(client => {
                    const clientMac = client.mac || client.address;
                    if (!tscmWifiClients.find(d => (d.mac || d.address) === clientMac)) {
                        handleTscmEvent({ type: 'wifi_client', ...client });
                    }
                });
            }

            // Process Bluetooth devices
            if (data.bt_devices) {
                data.bt_devices.forEach(device => {
                    const deviceMac = device.mac || device.address;
                    if (!tscmBtDevices.find(d => (d.mac || d.address) === deviceMac)) {
                        handleTscmEvent({ type: 'bt_device', ...device });
                    }
                });
            }

            // Process anomalies/threats
            // Agent now uses same ThreatDetector as local mode, so format matches:
            // threat_type, severity, source, identifier, name, signal_strength
            if (data.anomalies) {
                data.anomalies.forEach(threat => {
                    handleTscmEvent({
                        type: 'threat_detected',
                        ...threat
                    });
                });
            }

            // Process RF signals
            if (data.rf_signals) {
                data.rf_signals.forEach(signal => {
                    handleTscmEvent({ type: 'rf_signal', ...signal });
                });
            }

            // Update progress (simple time-based estimate)
            if (tscmSweepStartTime) {
                const elapsed = (Date.now() - tscmSweepStartTime) / 1000;
                const sweepType = document.getElementById('tscmSweepType')?.value || 'standard';
                const durations = { quick: 120, standard: 300, full: 900, custom: 300 };
                const maxDuration = durations[sweepType] || 300;
                const progress = Math.min(95, (elapsed / maxDuration) * 100);
                updateTscmProgress({ progress: Math.round(progress), phase: 'Scanning' });
            }
        }
    } catch (e) {
        console.error('[TSCM] Agent poll error:', e);
    }
}

let tscmCorrelations = [];

function handleTscmEvent(data) {
    switch (data.type) {
        case 'sweep_progress':
            updateTscmProgress(data);
            break;
        case 'wifi_device':
            addTscmWifiDevice(data);
            break;
        case 'wifi_client':
            addTscmWifiClient(data);
            break;
        case 'bt_device':
            addTscmBtDevice(data);
            break;
        case 'rf_signal':
            addTscmRfSignal(data);
            break;
        case 'rf_status':
            handleRfStatus(data);
            break;
        case 'threat_detected':
            addTscmThreat(data);
            break;
        case 'correlation_findings':
            handleCorrelationFindings(data);
            break;
        case 'baseline_comparison':
            handleBaselineComparison(data);
            break;
        case 'identity_clusters':
            handleIdentityClusters(data);
            break;
        case 'sweep_completed':
            completeTscmSweep(data);
            break;
        case 'sweep_stopped':
        case 'sweep_error':
            stopTscmSweep();
            break;
    }
}

function handleCorrelationFindings(data) {
    tscmCorrelations = data.correlations || [];
    updateCorrelationsDisplay();
    updateTscmThreatCounts();
}

function handleBaselineComparison(data) {
    tscmBaselineComparison = data || null;
}

function handleIdentityClusters(data) {
    tscmIdentitySummary = {
        total: data.total_clusters || 0,
        high: data.high_risk_count || 0,
        medium: data.medium_risk_count || 0,
        unique_fingerprints: data.unique_fingerprints || 0,
    };
    tscmIdentityClusters = data.clusters || [];
    updateCorrelationsDisplay();
    updateTscmThreatCounts();
}

async function tscmRefreshIdentityClusters() {
    try {
        const [clusterRes, summaryRes] = await Promise.all([
            fetch('/tscm/identity/clusters'),
            fetch('/tscm/identity/summary')
        ]);

        const clusterData = await clusterRes.json();
        if (clusterData.status === 'success') {
            tscmIdentityClusters = clusterData.clusters || [];
        }

        const summaryData = await summaryRes.json();
        if (summaryData.status === 'success' && summaryData.summary) {
            const stats = summaryData.summary.statistics || {};
            tscmIdentitySummary = {
                total: stats.total_clusters || tscmIdentityClusters.length,
                high: stats.high_risk_count || 0,
                medium: stats.medium_risk_count || 0,
                unique_fingerprints: stats.unique_fingerprints || 0,
            };
        }

        updateCorrelationsDisplay();
        updateTscmThreatCounts();
    } catch (e) {
        console.error('Failed to refresh identity clusters:', e);
    }
}

function addTscmWifiDevice(device) {
    // Check ignore list
    if (tscmIgnoreList.has(`wifi:${device.bssid}`)) return;
    // Check if already exists
    const exists = tscmWifiDevices.some(d => d.bssid === device.bssid);
    if (!exists) {
        tscmWifiDevices.push(device);
        debouncedUpdateTscmDisplays();
        updateTscmThreatCounts();
        // Add to findings panel if score >= 3 (review level or higher)
        if (device.score >= 3) {
            addHighInterestDevice(device, 'wifi');
        }
        // Feed to baseline recorder if recording
        if (isRecordingBaseline) {
            fetch('/tscm/feed/wifi', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(device)
            }).catch(e => console.error('Baseline feed error:', e));
        }
        // Add to signal timeline
        const freq = device.channel <= 14 ? '2400' : '5000';
        const strength = Math.min(5, Math.max(1, Math.ceil((device.signal + 100) / 20)));
        SignalTimeline.addEvent(freq, strength, 2000, device.ssid || 'Hidden WiFi');
    }
}

function addTscmWifiClient(client) {
    const mac = client.mac || client.address || '';
    if (!mac) return;
    if (tscmIgnoreList.has(`wifi:${mac}`)) return;
    const exists = tscmWifiClients.some(d => (d.mac || d.address) === mac);
    if (!exists) {
        if (!client.mac) client.mac = mac;
        client.is_client = true;
        tscmWifiClients.push(client);
        debouncedUpdateTscmDisplays();
        updateTscmThreatCounts();
        if (client.score >= 3) {
            addHighInterestDevice(client, 'wifi');
        }
        if (isRecordingBaseline) {
            fetch('/tscm/feed/wifi', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(client)
            }).catch(e => console.error('Baseline feed error:', e));
        }
    }
}

function addTscmBtDevice(device) {
    const mac = device.mac || device.address || '';
    if (tscmIgnoreList.has(`bt:${mac}`)) return;
    // Check if already exists
    const exists = tscmBtDevices.some(d => (d.mac || d.address) === mac);
    if (!exists) {
        if (!device.mac && mac) device.mac = mac;
        tscmBtDevices.push(device);
        debouncedUpdateTscmDisplays();
        updateTscmThreatCounts();
        // Add to threats panel if score >= 3 (review level or higher)
        if (device.score >= 3) {
            addHighInterestDevice(device, 'bluetooth');
        }
        // Feed to baseline recorder if recording
        if (isRecordingBaseline) {
            fetch('/tscm/feed/bluetooth', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(device)
            }).catch(e => console.error('Baseline feed error:', e));
        }
        // Add to signal timeline
        const strength = device.rssi ? Math.min(5, Math.max(1, Math.ceil((device.rssi + 100) / 20))) : 3;
        SignalTimeline.addEvent('2450', strength, 1500, device.name || 'Bluetooth Device');
    }
}

let tscmRfSignals = [];
let tscmRfStatusMessage = null;

function addTscmRfSignal(signal) {
    // Clear any error message since we're receiving signals
    tscmRfStatusMessage = null;
    if (tscmIgnoreList.has(`rf:${(signal.frequency || 0).toFixed(3)}`)) return;
    // Check if already exists (within 0.1 MHz)
    const exists = tscmRfSignals.some(s => Math.abs(s.frequency - signal.frequency) < 0.1);
    const powerDbm = signal.power_dbm ?? signal.power ?? signal.level;
    const strength = powerDbm !== undefined && powerDbm !== null
        ? Math.min(5, Math.max(1, Math.ceil((powerDbm + 60) / 15)))
        : 3;
    if (!exists) {
        tscmRfSignals.push(signal);
        debouncedUpdateTscmDisplays();
        updateTscmThreatCounts();
        // Add to findings panel if score >= 3 (review level or higher)
        if (signal.score >= 3) {
            addHighInterestDevice(signal, 'rf');
        }
        // Feed to baseline recorder if recording
        if (isRecordingBaseline) {
            fetch('/tscm/feed/rf', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(signal)
            }).catch(e => console.error('Baseline feed error:', e));
        }
        // Add to signal timeline
        SignalTimeline.addEvent(String(signal.frequency), strength, 1000, signal.classification || 'RF Signal');
    } else {
        // Update existing signal on timeline (show recurring transmission)
        SignalTimeline.addEvent(String(signal.frequency), strength, 500, signal.classification || 'RF Signal');
    }
}

function handleRfStatus(data) {
    // Store status message to display in RF panel
    tscmRfStatusMessage = data.message;
    updateRfDisplay();
}

function updateRfDisplay() {
    const rfList = document.getElementById('tscmRfList');
    if (!rfList) return;

    if (tscmRfSignals.length === 0) {
        if (tscmRfStatusMessage) {
            rfList.innerHTML = `<div class="tscm-status-message">${escapeHtml(tscmRfStatusMessage)}</div>`;
        } else {
            rfList.innerHTML = '<div class="tscm-empty">No RF signals detected</div>';
        }
    }
    // If there are signals, updateTscmDisplays() will handle the display
}

// Debounced versions of expensive display updates to batch rapid-fire device additions
let _tscmDisplayTimer = null;
function debouncedUpdateTscmDisplays() {
    if (_tscmDisplayTimer) clearTimeout(_tscmDisplayTimer);
    _tscmDisplayTimer = setTimeout(() => { _tscmDisplayTimer = null; updateTscmDisplays(); }, 250);
}
let _tscmHighInterestTimer = null;
function debouncedUpdateHighInterestPanel() {
    if (_tscmHighInterestTimer) clearTimeout(_tscmHighInterestTimer);
    _tscmHighInterestTimer = setTimeout(() => { _tscmHighInterestTimer = null; updateHighInterestPanel(); }, 250);
}

// Track high-interest devices for the threats panel
let tscmHighInterestDevices = [];
function addHighInterestDevice(device, protocol) {
    const id = device.mac || device.bssid || device.frequency;
    const exists = tscmHighInterestDevices.some(d => d.id === id);
    if (!exists) {
        tscmHighInterestDevices.push({
            id: id,
            protocol: protocol,
            name: device.name || device.essid || device.ssid || (device.frequency ? `${device.frequency.toFixed(3)} MHz` : 'Unknown Device'),
            score: device.score,
            classification: device.classification,
            indicators: device.indicators || [],
            recommended_action: device.recommended_action
        });
        debouncedUpdateHighInterestPanel();
    }
}

function updateHighInterestPanel() {
    const panel = document.getElementById('tscmThreatList');
    if (tscmHighInterestDevices.length === 0) {
        panel.innerHTML = '<div class="tscm-empty"><div class="tscm-empty-primary">Monitoring active — nothing flagged</div><div class="tscm-empty-secondary">Signals are being analyzed against baseline thresholds. This does not rule out passive or dormant devices.</div></div>';
    } else {
        // Sort by score (highest first)
        const sorted = [...tscmHighInterestDevices].sort((a, b) => b.score - a.score);
        panel.innerHTML = '<div class="tscm-threat-list">' + sorted.map(d => {
            const severityClass = d.score >= 6 ? 'critical' : d.score >= 4 ? 'high' : 'medium';
            return `
            <div class="tscm-threat-item ${severityClass}" onclick="showDeviceDetails('${d.id}', '${d.protocol}')">
                <div class="tscm-threat-header">
                    <span class="tscm-threat-type">${d.protocol.toUpperCase()}</span>
                    <span class="tscm-threat-severity">Score: ${d.score}</span>
                </div>
                <div class="tscm-threat-details">
                    <strong>${escapeHtml(d.name)}</strong><br>
                    <span style="font-size: 10px; color: var(--text-muted);">
                    ${d.indicators && d.indicators.length > 0 ? d.indicators.slice(0, 2).map(i => i.desc || i.type).join(' | ') : 'Review recommended'}
                    </span>
                </div>
                <div class="tscm-threat-action">${d.recommended_action || 'review'}</div>
            </div>
            `;
        }).join('') + '</div>';
    }
}

function updateTscmProgress(data) {
    // Update percentage text
    document.getElementById('tscmProgressPercent').textContent = data.progress + '%';

    // Update SVG circle progress (circumference = 2 * PI * 45 = ~283)
    const circumference = 283;
    const offset = circumference - (data.progress / 100) * circumference;
    const circle = document.getElementById('tscmScannerCircle');
    if (circle) {
        circle.style.strokeDashoffset = offset;
    }

    // Update status text
    let statusText = 'SCANNING...';
    if (data.threats_found > 0) {
        statusText = `THREATS: ${data.threats_found}`;
    } else if (data.status) {
        statusText = data.status;
    } else {
        const parts = [];
        if (data.wifi_count > 0) parts.push(`${data.wifi_count} WiFi`);
        if (data.bt_count > 0) parts.push(`${data.bt_count} BT`);
        if (data.rf_count > 0) parts.push(`${data.rf_count} RF`);
        statusText = parts.length > 0 ? parts.join(' | ') : 'SCANNING...';
    }
    document.getElementById('tscmProgressLabel').textContent = statusText;
}

function addTscmThreat(threat) {
    tscmThreats.unshift(threat);

    // Update dashboard counts
    updateTscmThreatCounts();
    debouncedUpdateTscmDisplays();
}

function readTscmFilters() {
    const protocolSelect = document.getElementById('tscmFilterProtocol');
    const riskSelect = document.getElementById('tscmFilterRisk');
    const statusSelect = document.getElementById('tscmFilterStatus');
    const knownSelect = document.getElementById('tscmFilterKnown');

    tscmFilters.protocol = protocolSelect ? protocolSelect.value : 'all';
    tscmFilters.risk = riskSelect ? riskSelect.value : 'all';
    tscmFilters.status = statusSelect ? statusSelect.value : 'all';
    tscmFilters.known = knownSelect ? knownSelect.value : 'all';
}

function updateTscmFilterStatus() {
    const statusEl = document.getElementById('tscmFilterStatusText');
    if (!statusEl) return;

    const parts = [];
    if (tscmFilters.protocol !== 'all') parts.push(tscmFilters.protocol.toUpperCase());
    if (tscmFilters.risk !== 'all') parts.push(tscmFilters.risk.replace(/_/g, ' ').toUpperCase());
    if (tscmFilters.status !== 'all') parts.push(tscmFilters.status.toUpperCase());
    if (tscmFilters.known !== 'all') parts.push(tscmFilters.known.toUpperCase());

    statusEl.textContent = parts.length > 0 ? `Filters: ${parts.join(' • ')}` : 'Filters: none';
}

function updateTscmPanelVisibility() {
    const protocol = tscmFilters.protocol;
    const showWifi = protocol === 'all' || protocol === 'wifi';
    const showBt = protocol === 'all' || protocol === 'bluetooth';
    const showRf = protocol === 'all' || protocol === 'rf';

    const wifiPanel = document.getElementById('tscmWifiPanel');
    const wifiClientPanel = document.getElementById('tscmWifiClientPanel');
    const btPanel = document.getElementById('tscmBtPanel');
    const rfPanel = document.getElementById('tscmRfPanel');

    if (wifiPanel) wifiPanel.style.display = showWifi ? '' : 'none';
    if (wifiClientPanel) wifiClientPanel.style.display = showWifi ? '' : 'none';
    if (btPanel) btPanel.style.display = showBt ? '' : 'none';
    if (rfPanel) rfPanel.style.display = showRf ? '' : 'none';
}

// --- Ignore list + cleared device helpers ---

function _tscmDeviceKey(device, protocol) {
    if (protocol === 'wifi') return `wifi:${device.bssid || device.mac || 'unknown'}`;
    if (protocol === 'bluetooth') return `bt:${device.mac || device.address || 'unknown'}`;
    if (protocol === 'rf') return `rf:${(device.frequency || 0).toFixed(3)}`;
    return `unknown:${protocol}`;
}

function tscmMarkCleared(key) {
    if (tscmClearedDevices.has(key)) {
        tscmClearedDevices.delete(key);
    } else {
        tscmClearedDevices.add(key);
    }
    debouncedUpdateTscmDisplays();
}

function _saveTscmIgnoreList() {
    try { localStorage.setItem('tscmIgnoreList', JSON.stringify([...tscmIgnoreList])); } catch(e) {}
}

function updateTscmIgnoreListUI() {
    const el = document.getElementById('tscmIgnoreListItems');
    if (!el) return;
    if (tscmIgnoreList.size === 0) {
        el.innerHTML = '<div style="font-size:10px;color:var(--text-muted);text-align:center;padding:6px;">No devices ignored</div>';
        return;
    }
    el.innerHTML = [...tscmIgnoreList].map(k => {
        const safe = escapeHtml(k);
        return `<div style="display:flex;justify-content:space-between;align-items:center;padding:3px 0;border-bottom:1px solid var(--border-color);">
            <span style="font-family:monospace;font-size:9px;color:var(--text-secondary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;">${safe}</span>
            <button onclick="tscmRemoveFromIgnoreList('${safe}')" style="background:none;border:none;color:var(--accent-red);cursor:pointer;font-size:12px;padding:0 4px;flex-shrink:0;" title="Remove">✕</button>
        </div>`;
    }).join('');
}

function tscmAddToIgnoreList(key) {
    if (!key || key.endsWith(':unknown')) return;
    tscmIgnoreList.add(key);
    _saveTscmIgnoreList();
    updateTscmIgnoreListUI();
    // Remove from current sweep arrays and re-render
    const colon = key.indexOf(':');
    const proto = key.slice(0, colon);
    const id = key.slice(colon + 1);
    if (proto === 'wifi') {
        tscmWifiDevices = tscmWifiDevices.filter(d => d.bssid !== id);
        if (typeof tscmWifiClients !== 'undefined')
            tscmWifiClients = tscmWifiClients.filter(d => (d.mac || d.address) !== id);
    } else if (proto === 'bt') {
        tscmBtDevices = tscmBtDevices.filter(d => (d.mac || d.address) !== id);
    } else if (proto === 'rf') {
        const freq = parseFloat(id);
        tscmRfSignals = tscmRfSignals.filter(s => Math.abs(s.frequency - freq) >= 0.001);
    }
    updateTscmDisplays();
    updateTscmThreatCounts();
}

function tscmRemoveFromIgnoreList(key) {
    tscmIgnoreList.delete(key);
    _saveTscmIgnoreList();
    updateTscmIgnoreListUI();
}

function tscmClearIgnoreList() {
    if (tscmIgnoreList.size === 0) return;
    if (!confirm('Clear all devices from the ignore list?')) return;
    tscmIgnoreList.clear();
    _saveTscmIgnoreList();
    updateTscmIgnoreListUI();
}

// --- End ignore list helpers ---

function matchesTscmFilters(device, protocol, options = {}) {
    if (tscmFilters.protocol !== 'all' && protocol !== tscmFilters.protocol) return false;

    if (!options.ignoreRisk && tscmFilters.risk !== 'all') {
        if ((device.classification || 'review') !== tscmFilters.risk) return false;
    }

    if (tscmFilters.status === 'new' && device.is_new !== true) return false;
    if (tscmFilters.status === 'baseline' && device.is_new !== false) return false;

    if (tscmFilters.known === 'known' && !device.known_device) return false;
    if (tscmFilters.known === 'unknown' && device.known_device) return false;

    return true;
}

function getFilteredDevices(options = {}) {
    const wifi = tscmWifiDevices.filter(d => matchesTscmFilters(d, 'wifi', options));
    const wifi_clients = tscmWifiClients.filter(d => matchesTscmFilters(d, 'wifi', options));
    const bt = tscmBtDevices.filter(d => matchesTscmFilters(d, 'bluetooth', options));
    const rf = tscmRfSignals.filter(d => matchesTscmFilters(d, 'rf', options));
    return {
        wifi,
        wifi_clients,
        bt,
        rf,
        all: [...wifi, ...wifi_clients, ...bt, ...rf],
    };
}

function applyTscmFilters() {
    readTscmFilters();
    updateTscmFilterStatus();
    updateTscmPanelVisibility();
    updateTscmDisplays();
    updateTscmThreatCounts();
}

// A half-dial for the sweep's overall level, from the classification counts:
// any high-interest device reads HIGH, any needing review ELEVATED.
function renderTscmThreatGauge(counts) {
    const el = document.getElementById('tscmThreatGauge');
    if (!el) return;
    const total = counts.high_interest + counts.review + counts.informational;
    const level = counts.high_interest ? { name: 'High', cls: 'high', at: 0.75 + Math.min(counts.high_interest, 5) * 0.045 }
        : counts.review ? { name: 'Elevated', cls: 'elevated', at: 0.4 + Math.min(counts.review, 10) * 0.022 }
        : total ? { name: 'Low', cls: 'low', at: 0.16 }
        : { name: 'No data', cls: 'none', at: 0 };
    const r = 46, cx = 60, cy = 58;
    const pt = (t) => [cx - r * Math.cos(Math.PI * t), cy - r * Math.sin(Math.PI * t)];
    const arc = (a, b, cls) => {
        const [x1, y1] = pt(a), [x2, y2] = pt(b);
        return `<path class="${cls}" d="M${x1.toFixed(1)} ${y1.toFixed(1)} A${r} ${r} 0 0 1 ${x2.toFixed(1)} ${y2.toFixed(1)}"/>`;
    };
    const [nx, ny] = pt(level.at);
    el.className = 'tscm-gauge ' + level.cls;
    el.setAttribute('aria-label', 'Threat level: ' + level.name);
    el.innerHTML = `<svg viewBox="0 0 120 64" aria-hidden="true">
            ${arc(0, 0.33, 'zone low')}${arc(0.34, 0.66, 'zone elevated')}${arc(0.67, 1, 'zone high')}
            ${level.at ? arc(0, level.at, 'fill') : ''}
            <line class="needle" x1="${cx}" y1="${cy}" x2="${nx.toFixed(1)}" y2="${ny.toFixed(1)}"/>
            <circle class="hub" cx="${cx}" cy="${cy}" r="3.5"/>
        </svg>
        <div class="tscm-gauge-level">${level.name}</div>
        <div class="tscm-gauge-sub">${total ? total + ' device' + (total === 1 ? '' : 's') + ' assessed' : 'Run a sweep to assess'}</div>`;
}
renderTscmThreatGauge({ high_interest: 0, review: 0, informational: 0 });

function updateTscmThreatCounts() {
    // Count devices by new scoring model classification
    const counts = { high_interest: 0, review: 0, informational: 0 };

    // Count from all device lists
    const filtered = getFilteredDevices();
    filtered.all.forEach(d => {
        const classification = d.classification || 'review';
        if (classification === 'high_interest') counts.high_interest++;
        else if (classification === 'review') counts.review++;
        else counts.informational++;
    });

    document.getElementById('tscmHighInterestCount').textContent = counts.high_interest;
    document.getElementById('tscmNeedsReviewCount').textContent = counts.review;
    document.getElementById('tscmInformationalCount').textContent = counts.informational;
    document.getElementById('tscmCorrelationsCount').textContent = tscmCorrelations.length;
    document.getElementById('tscmIdentityCount').textContent = tscmIdentityClusters.length;

    document.getElementById('tscmHighInterestCard').classList.toggle('active', counts.high_interest > 0);
    document.getElementById('tscmNeedsReviewCard').classList.toggle('active', counts.review > 0);
    document.getElementById('tscmInformationalCard').classList.toggle('active', counts.informational > 0);
    document.getElementById('tscmCorrelationsCard').classList.toggle('active', tscmCorrelations.length > 0);
    document.getElementById('tscmIdentityCard').classList.toggle('active', tscmIdentityClusters.length > 0);
    renderTscmThreatGauge(counts);

    // Update threat panel count (shows high interest items only)
    document.getElementById('tscmThreatCount').textContent = counts.high_interest;
}

function getClassificationClass(classification) {
    // Map classification to CSS class
    switch (classification) {
        case 'high_interest': return 'classification-red';
        case 'review': return 'classification-yellow';
        case 'informational': return 'classification-green';
        default: return 'classification-yellow';
    }
}

function getClassificationIcon(classification) {
    // Returns CSS class name for colored dot styling instead of emojis
    switch (classification) {
        case 'high_interest': return '<span class="classification-dot high"></span>';
        case 'review': return '<span class="classification-dot review"></span>';
        case 'informational': return '<span class="classification-dot info"></span>';
        default: return '<span class="classification-dot review"></span>';
    }
}

function formatIndicators(indicators) {
    if (!indicators || indicators.length === 0) return '';
    return indicators.map(i => `<span class="indicator-tag">${escapeHtml(i.desc || i.type)}</span>`).join(' ');
}

function getTrackerLabel(device) {
    if (!device) return null;
    return (device.tracker && (device.tracker.name || device.tracker.type)) ||
        device.tracker_type || device.tracker_name || null;
}

function formatTrackerBadge(device) {
    const label = getTrackerLabel(device);
    if (!label) return '';
    return `<span class="tracker-badge" title="Tracker">${escapeHtml(label)}</span>`;
}

function getScoreBadge(score) {
    if (score === undefined || score === null) return '';
    let scoreClass = 'score-low';
    if (score >= 6) scoreClass = 'score-high';
    else if (score >= 3) scoreClass = 'score-medium';
    return `<span class="score-badge ${scoreClass}">Score: ${score}</span>`;
}

// Store all devices for lookup
function getAllTscmDevices() {
    const devices = {};
    tscmWifiDevices.forEach(d => { devices[`wifi:${d.bssid}`] = { ...d, protocol: 'wifi' }; });
    tscmWifiClients.forEach(d => { devices[`wifi:${d.mac}`] = { ...d, protocol: 'wifi' }; });
    tscmBtDevices.forEach(d => { devices[`bluetooth:${d.mac}`] = { ...d, protocol: 'bluetooth' }; });
    tscmRfSignals.forEach(d => { devices[`rf:${d.frequency}`] = { ...d, protocol: 'rf' }; });
    return devices;
}

function showDeviceDetails(id, protocol) {
    const devices = getAllTscmDevices();
    const key = `${protocol}:${id}`;
    const device = devices[key];

    if (!device) {
        console.warn('Device not found:', key);
        return;
    }

    const modal = document.getElementById('tscmDeviceModal');
    const content = document.getElementById('tscmDeviceModalContent');

    // Build detailed view
    let html = `
        <div class="device-detail-header ${getClassificationClass(device.classification)}">
            <h3>${getClassificationIcon(device.classification)} ${escapeHtml(device.name || device.essid || device.ssid || device.mac || device.bssid || (device.frequency ? device.frequency.toFixed(3) + ' MHz' : 'Unknown'))}</h3>
            <span class="device-detail-protocol">${protocol.toUpperCase()}</span>
        </div>

        <div class="device-detail-score">
            <div class="score-circle ${device.score >= 6 ? 'high' : device.score >= 3 ? 'medium' : 'low'}">
                <span class="score-value">${device.score || 0}</span>
                <span class="score-label">SCORE</span>
            </div>
            <div class="score-breakdown">
                <strong>Risk Level:</strong> ${device.classification === 'high_interest' ? 'HIGH INTEREST' : device.classification === 'review' ? 'NEEDS REVIEW' : 'INFORMATIONAL'}<br>
                <strong>Recommended Action:</strong> ${device.recommended_action || 'Monitor'}
            </div>
        </div>

        <div class="device-detail-section">
            <h4>Device Information</h4>
            <table class="device-detail-table">
    `;

    // Add device-specific fields
    if (protocol === 'wifi') {
        if (device.is_client) {
            html += `
                <tr><td>Client MAC</td><td>${device.mac || 'Unknown'}</td></tr>
                <tr><td>Vendor</td><td>${escapeHtml(device.vendor || 'Unknown')}</td></tr>
                <tr><td>RSSI</td><td>${device.rssi || '--'} dBm</td></tr>
                <tr><td>Associated BSSID</td><td>${device.associated_bssid || 'Unassociated'}</td></tr>
                <tr><td>Probed SSIDs</td><td>${device.probe_count || (device.probed_ssids ? device.probed_ssids.length : 0)}</td></tr>
            `;
        } else {
            html += `
                <tr><td>BSSID</td><td>${device.bssid || 'Unknown'}</td></tr>
                <tr><td>SSID</td><td>${escapeHtml(device.ssid || '[Hidden]')}</td></tr>
                <tr><td>Vendor</td><td>${escapeHtml(device.vendor || 'Unknown')}</td></tr>
                <tr><td>Channel</td><td>${device.channel || 'Unknown'}</td></tr>
                <tr><td>Signal</td><td>${device.signal || '--'} dBm</td></tr>
                <tr><td>Security</td><td>${device.security || 'Unknown'}</td></tr>
            `;
        }
    } else if (protocol === 'bluetooth') {
        const trackerLabel = getTrackerLabel(device);
        html += `
            <tr><td>MAC Address</td><td>${device.mac || 'Unknown'}</td></tr>
            <tr><td>Name</td><td>${escapeHtml(device.name || 'Unknown')}</td></tr>
            <tr><td>Type</td><td>${device.device_type || 'Unknown'}</td></tr>
            <tr><td>Manufacturer</td><td>${escapeHtml(device.manufacturer || 'Unknown')}</td></tr>
            <tr><td>Tracker</td><td>${trackerLabel ? escapeHtml(trackerLabel) : 'No'}</td></tr>
            <tr><td>RSSI</td><td>${device.rssi || '--'} dBm</td></tr>
            <tr><td>Audio Capable</td><td>${device.is_audio_capable ? 'Yes' : 'No'}</td></tr>
        `;
    } else if (protocol === 'rf') {
        html += `
            <tr><td>Frequency</td><td>${device.frequency?.toFixed(3) || 'Unknown'} MHz</td></tr>
            <tr><td>Band</td><td>${device.band || 'Unknown'}</td></tr>
            <tr><td>Power</td><td>${device.power?.toFixed(1) || '--'} dBm</td></tr>
            <tr><td>Signal Strength</td><td>+${(device.signal_strength || 0).toFixed(1)} dB above noise</td></tr>
        `;
    }

    if (device.known_device) {
        const knownLabel = device.known_device_name ? `Yes (${escapeHtml(device.known_device_name)})` : 'Yes';
        html += `<tr><td>Known Device</td><td>${knownLabel}</td></tr>`;
    }
    if (device.score_modifier && device.score_modifier !== 0) {
        const modLabel = `${device.score_modifier > 0 ? '+' : ''}${device.score_modifier}`;
        html += `<tr><td>Score Modifier</td><td>${modLabel}</td></tr>`;
    }
    html += `</table></div>`;

    // Actions section - always show with "Add to Known Devices"
    const deviceIdentifier = device.bssid || device.mac || (device.frequency ? device.frequency.toString() : id);
    const deviceName = device.name || device.ssid || device.essid || (device.frequency ? device.frequency + ' MHz' : 'Unknown');
    html += `
        <div class="device-detail-section">
            <h4>Actions</h4>
            <div style="display: flex; gap: 8px; flex-wrap: wrap;">
    `;

    // Add "Listen" and "Decode (OOK)" buttons for RF signals
    if (protocol === 'rf' && device.frequency) {
        const freq = device.frequency;
        html += `
                <button class="tscm-action-btn" onclick="listenToRfSignal(${freq}, 'fm')">
                    Listen (FM)
                </button>
                <button class="tscm-action-btn" onclick="listenToRfSignal(${freq}, 'am')">
                    Listen (AM)
                </button>
                <button class="tscm-action-btn" onclick="decodeWithOok(${freq})"
                        title="Open OOK decoder tuned to this frequency">
                    Decode (OOK)
                </button>
        `;
    }

    // Add "Add to Known Devices" button for all device types
    html += `
                <button class="tscm-action-btn" style="background: var(--accent-cyan);" onclick="tscmAddToKnownDevices('${escapeHtml(deviceIdentifier)}', '${escapeHtml(deviceName)}', '${protocol}')">
                    Add to Known Devices
                </button>
                <button class="tscm-action-btn" style="background: var(--accent-orange);" onclick="tscmShowInvestigateById('${escapeHtml(deviceIdentifier)}', '${protocol}')">
                    Investigate
                </button>
            </div>
            <div style="font-size: 10px; color: var(--text-secondary); margin-top: 8px;">
                ${protocol === 'rf' ? 'Listen opens Spectrum Waterfall. Decode (OOK) opens the OOK decoder tuned to this frequency. ' : ''}Known devices are excluded from threat scoring in future sweeps.
            </div>
        </div>
    `;

    // Timeline section (loaded async)
    html += `
        <div class="device-detail-section" id="tscmTimelineSection">
            <h4>Timeline</h4>
            <div class="tscm-empty ui-loading">Loading timeline…</div>
        </div>
    `;

    if (protocol === 'wifi') {
        html += `
            <div class="device-detail-section" id="tscmWifiAdvancedSection">
                <h4>WiFi Advanced Indicators</h4>
                <div class="tscm-empty">Analyzing network...</div>
            </div>
        `;
    } else if (protocol === 'bluetooth') {
        html += `
            <div class="device-detail-section" id="tscmBleExplainSection">
                <h4>Bluetooth Risk Explanation</h4>
                <div class="tscm-empty">Analyzing device...</div>
            </div>
        `;
    }

    // Add indicators section
    if (device.indicators && device.indicators.length > 0) {
        html += `
            <div class="device-detail-section">
                <h4>Risk Indicators (Why This Score)</h4>
                <div class="indicator-list">
                    ${device.indicators.map(i => `
                        <div class="indicator-item">
                            <span class="indicator-type">${i.type}</span>
                            <span class="indicator-desc">${escapeHtml(i.desc || '')}</span>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    }

    // Add reasons section
    if (device.reasons && device.reasons.length > 0) {
        html += `
            <div class="device-detail-section">
                <h4>Detection Notes</h4>
                <ul class="device-reasons-list">
                    ${device.reasons.map(r => `<li>${escapeHtml(r)}</li>`).join('')}
                </ul>
            </div>
        `;
    }

    // Signal Timeline Chart
    html += `
        <div class="device-detail-section">
            <h4>Signal Timeline</h4>
            <canvas id="deviceTimelineChart" width="600" height="180" style="width: 100%; max-height: 180px;"></canvas>
            <div id="deviceTimelineMetrics" style="display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px;"></div>
        </div>
    `;

    // Playbook section
    html += `
        <div class="device-detail-section" id="devicePlaybookSection" style="display: none;">
            <h4>Recommended Playbook</h4>
            <div id="devicePlaybookContent"></div>
        </div>
    `;

    // Add disclaimer
    html += `
        <div class="device-detail-disclaimer">
            <strong>Disclaimer:</strong> This analysis identifies indicators and anomalies.
            It does NOT confirm surveillance activity. Professional verification required.
        </div>
    `;

    content.innerHTML = html;
    modal.style.display = 'flex';

    const timelineIdentifier = tscmNormalizeIdentifier(id, protocol, device);
    loadTscmTimeline(timelineIdentifier, protocol);
    loadTscmAdvancedAnalysis(device, protocol);

    // Load timeline chart
    fetchDeviceTimelineChart(timelineIdentifier, protocol);

    // Load playbook for this device
    fetchDevicePlaybook(timelineIdentifier).then(playbook => {
        if (playbook) {
            const section = document.getElementById('devicePlaybookSection');
            const pbContent = document.getElementById('devicePlaybookContent');
            if (section && pbContent) {
                section.style.display = 'block';
                pbContent.innerHTML = renderPlaybook(playbook);
            }
        }
    });
}

function tscmNormalizeIdentifier(identifier, protocol, device) {
    let value = identifier;
    if ((value === undefined || value === null || value === '') && device) {
        value = device.bssid || device.mac || device.frequency || '';
    }

    if (protocol === 'rf') {
        const freq = device && device.frequency !== undefined ? device.frequency : parseFloat(value);
        if (!isNaN(freq)) return freq.toFixed(3);
        return String(value || '');
    }

    if (value === undefined || value === null) return '';
    return String(value).toUpperCase();
}

function tscmShowInvestigateById(id, protocol) {
    const devices = getAllTscmDevices();
    const key = `${protocol}:${id}`;
    let device = devices[key];

    if (!device && protocol === 'rf') {
        const freq = parseFloat(id);
        if (!isNaN(freq)) {
            const all = Object.values(devices);
            device = all.find(d => d.protocol === 'rf' && d.frequency && Math.abs(d.frequency - freq) < 0.01);
        }
    }

    if (!device) {
        const normalized = tscmNormalizeIdentifier(id, protocol);
        const all = Object.values(devices);
        device = all.find(d => tscmNormalizeIdentifier(null, protocol, d) === normalized);
    }

    if (!device) {
        console.warn('Investigate device not found:', key);
        return;
    }

    tscmShowInvestigate(device, protocol);
}

async function tscmShowInvestigate(device, protocol) {
    const modal = document.getElementById('tscmDeviceModal');
    const content = document.getElementById('tscmDeviceModalContent');

    const identifier = tscmNormalizeIdentifier(null, protocol, device);
    const displayName = device.name || device.essid || device.ssid || device.mac || device.bssid ||
        (device.frequency ? `${device.frequency.toFixed(3)} MHz` : 'Unknown Device');

    content.innerHTML = '<div class="ui-loading">Loading triage sheet…</div>';
    modal.style.display = 'flex';

    let cases = [];
    try {
        const response = await fetch('/tscm/cases');
        const data = await response.json();
        cases = data.cases || [];
    } catch (e) {
        console.warn('Failed to load cases for triage:', e);
    }

    const caseOptions = cases.length
        ? `<option value="">Select a case...</option>` +
          cases.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join('')
        : '<option value="">No cases available</option>';

    const noteTemplate = [
        `Device: ${displayName}`,
        `Protocol: ${protocol.toUpperCase()}`,
        `Identifier: ${identifier}`,
        `Score: ${device.score || 0}`,
        `Recommended Action: ${device.recommended_action || 'monitor'}`,
        'Notes:'
    ].join('\n');

    const indicatorList = (device.indicators || []).map(i => `
        <div class="indicator-item">
            <span class="indicator-type">${escapeHtml(i.type || 'indicator')}</span>
            <span class="indicator-desc">${escapeHtml(i.desc || i.description || '')}</span>
        </div>
    `).join('');

    content.innerHTML = `
        <div class="device-detail-header ${getClassificationClass(device.classification)}">
            <h3>${getClassificationIcon(device.classification)} ${escapeHtml(displayName)}</h3>
            <span class="device-detail-protocol">${protocol.toUpperCase()}</span>
        </div>
        <div class="device-detail-score">
            <div class="score-circle ${device.score >= 6 ? 'high' : device.score >= 3 ? 'medium' : 'low'}">
                <span class="score-value">${device.score || 0}</span>
                <span class="score-label">SCORE</span>
            </div>
            <div class="score-breakdown">
                <strong>Risk Level:</strong> ${device.classification === 'high_interest' ? 'HIGH INTEREST' : device.classification === 'review' ? 'NEEDS REVIEW' : 'INFORMATIONAL'}<br>
                <strong>Recommended Action:</strong> ${device.recommended_action || 'Monitor'}
            </div>
        </div>

        <div class="device-detail-section">
            <h4>Device Profile</h4>
            <div id="tscmTriageProfileSection" class="tscm-empty ui-loading">Loading profile…</div>
        </div>

        <div class="device-detail-section" id="tscmTriageTimelineSection">
            <h4>Timeline</h4>
            <div class="tscm-empty ui-loading">Loading timeline…</div>
        </div>

        ${indicatorList ? `
        <div class="device-detail-section">
            <h4>Indicators</h4>
            <div class="indicator-list">${indicatorList}</div>
        </div>
        ` : ''}

        <div class="device-detail-section">
            <h4>Case Actions</h4>
            ${cases.length === 0 ? `
                <div class="tscm-empty">No cases available. Create a case to attach notes.</div>
                <button class="preset-btn" onclick="tscmCreateCase()" style="margin-top: 8px; font-size: 10px;">+ New Case</button>
            ` : `
                <div class="tscm-case-note-form">
                    <label>Case</label>
                    <select id="tscmTriageCaseSelect">${caseOptions}</select>
                    <label>Note Type</label>
                    <select id="tscmTriageNoteType">
                        <option value="general" selected>General</option>
                        <option value="observation">Observation</option>
                        <option value="action">Action</option>
                        <option value="follow_up">Follow-up</option>
                    </select>
                    <label>Case Note</label>
                    <textarea id="tscmTriageNote" rows="5">${escapeHtml(noteTemplate)}</textarea>
                    <div class="tscm-case-note-actions">
                        <button class="preset-btn" onclick="tscmAddTriageNote()" style="font-size: 10px;">Add Note</button>
                        <button class="preset-btn" onclick="tscmOpenSelectedCase()" style="font-size: 10px;">Open Case</button>
                        ${tscmLastSweepId ? `<button class="preset-btn" onclick="tscmPromptLinkSweep(${tscmLastSweepId})" style="font-size: 10px;" title="Link the current sweep to a case">Link to Case</button>` : ''}
                    </div>
                </div>
            `}
        </div>
        <div class="device-detail-disclaimer">
            <strong>Disclaimer:</strong> This triage sheet surfaces indicators only. It does NOT confirm surveillance activity.
        </div>
    `;

    loadTscmTimeline(identifier, protocol, 'tscmTriageTimelineSection');
    tscmLoadTriageProfile(identifier);
}

async function tscmLoadTriageProfile(identifier) {
    const section = document.getElementById('tscmTriageProfileSection');
    if (!section || !identifier) return;

    try {
        const response = await fetch(`/tscm/findings/device/${encodeURIComponent(identifier)}`);
        const data = await response.json();

        if (data.status !== 'success' || !data.profile) {
            section.innerHTML = '<div class="tscm-empty">No profile available.</div>';
            return;
        }

        const profile = data.profile;
        section.innerHTML = `
            <table class="device-detail-table">
                <tr><td>Identifier</td><td>${escapeHtml(profile.identifier || '')}</td></tr>
                <tr><td>Name</td><td>${escapeHtml(profile.name || 'N/A')}</td></tr>
                <tr><td>Manufacturer</td><td>${escapeHtml(profile.manufacturer || 'N/A')}</td></tr>
                <tr><td>Device Type</td><td>${escapeHtml(profile.device_type || 'N/A')}</td></tr>
                ${(profile.tracker_name || profile.tracker_type) ? `<tr><td>Tracker</td><td>${escapeHtml(profile.tracker_name || profile.tracker_type)}</td></tr>` : ''}
                ${profile.tracker_confidence ? `<tr><td>Tracker Confidence</td><td>${escapeHtml(profile.tracker_confidence)}</td></tr>` : ''}
                <tr><td>First Seen</td><td>${profile.first_seen ? new Date(profile.first_seen).toLocaleString() : 'N/A'}</td></tr>
                <tr><td>Last Seen</td><td>${profile.last_seen ? new Date(profile.last_seen).toLocaleString() : 'N/A'}</td></tr>
                <tr><td>Detections</td><td>${profile.detection_count || 0}</td></tr>
                <tr><td>Risk Level</td><td>${escapeHtml(profile.risk_level || 'informational')}</td></tr>
                <tr><td>Score</td><td>${profile.total_score || 0}</td></tr>
                <tr><td>Confidence</td><td>${profile.confidence !== undefined ? Math.round(profile.confidence * 100) + '%' : 'N/A'}</td></tr>
                ${profile.known_device ? `<tr><td>Known Device</td><td>${escapeHtml(profile.known_device_name || 'Yes')}</td></tr>` : ''}
            </table>
            ${profile.indicators && profile.indicators.length > 0 ? `
                <div style="margin-top: 12px;">
                    <h4>Profile Indicators</h4>
                    <div class="indicator-list">
                        ${profile.indicators.map(i => `
                            <div class="indicator-item">
                                <span class="indicator-type">${escapeHtml(i.type || 'indicator')}</span>
                                <span class="indicator-desc">${escapeHtml(i.description || '')}</span>
                            </div>
                        `).join('')}
                    </div>
                </div>
            ` : ''}
        `;
    } catch (e) {
        console.error('Failed to load triage profile:', e);
        section.innerHTML = '<div class="tscm-empty">Failed to load profile.</div>';
    }
}

async function tscmSubmitCaseNote(caseId, content, noteType) {
    if (!caseId) return false;
    if (!content) {
        alert('Note content is required.');
        return false;
    }

    try {
        const response = await fetch(`/tscm/cases/${caseId}/notes`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                content: content,
                note_type: noteType || 'general'
            })
        });
        const data = await response.json();
        if (data.status === 'success') {
            return true;
        }
        alert(data.message || 'Failed to add note');
        return false;
    } catch (e) {
        console.error('Failed to add case note:', e);
        alert('Failed to add note');
        return false;
    }
}

async function tscmAddTriageNote() {
    const caseSelect = document.getElementById('tscmTriageCaseSelect');
    const noteInput = document.getElementById('tscmTriageNote');
    const typeSelect = document.getElementById('tscmTriageNoteType');

    if (!caseSelect || !noteInput || !typeSelect) return;
    const caseId = caseSelect.value;
    const content = noteInput.value.trim();
    const noteType = typeSelect.value;

    if (!caseId) {
        alert('Select a case to attach this note.');
        return;
    }

    const ok = await tscmSubmitCaseNote(caseId, content, noteType);
    if (ok) {
        noteInput.value = '';
        alert('Note added to case.');
    }
}

function tscmOpenSelectedCase() {
    const caseSelect = document.getElementById('tscmTriageCaseSelect');
    if (!caseSelect || !caseSelect.value) {
        alert('Select a case to open.');
        return;
    }
    tscmViewCase(caseSelect.value);
}

function formatTscmTimestamp(ts) {
    if (!ts) return 'N/A';
    try {
        return new Date(ts).toLocaleString();
    } catch (e) {
        return ts;
    }
}

async function loadTscmTimeline(identifier, protocol, targetId = 'tscmTimelineSection') {
    const section = document.getElementById(targetId);
    const normalizedIdentifier = tscmNormalizeIdentifier(identifier, protocol);
    if (!section || !normalizedIdentifier) return;

    try {
        const response = await fetch(`/tscm/device/${encodeURIComponent(normalizedIdentifier)}/timeline?protocol=${encodeURIComponent(protocol)}`);
        const data = await response.json();

        if (data.status !== 'success' || !data.timeline) {
            section.innerHTML = `
                <h4>Timeline</h4>
                <div class="tscm-empty">No timeline data available.</div>
            `;
            return;
        }

        const timeline = data.timeline;
        const metrics = timeline.metrics || {};
        const signal = timeline.signal || {};
        const movement = timeline.movement || {};
        const meeting = timeline.meeting_correlation || {};
        const observations = timeline.observations || [];
        const recent = observations.slice(-5);

        const metricsRows = [];
        if (metrics.first_seen) metricsRows.push(`<tr><td>First Seen</td><td>${formatTscmTimestamp(metrics.first_seen)}</td></tr>`);
        if (metrics.last_seen) metricsRows.push(`<tr><td>Last Seen</td><td>${formatTscmTimestamp(metrics.last_seen)}</td></tr>`);
        if (metrics.total_observations !== undefined) metricsRows.push(`<tr><td>Observations</td><td>${metrics.total_observations}</td></tr>`);
        if (metrics.presence_ratio !== undefined) metricsRows.push(`<tr><td>Presence Ratio</td><td>${Math.round(metrics.presence_ratio * 100)}%</td></tr>`);

        const signalRows = [];
        if (signal.rssi_min !== undefined && signal.rssi_min !== null) signalRows.push(`<tr><td>RSSI Min</td><td>${signal.rssi_min} dBm</td></tr>`);
        if (signal.rssi_max !== undefined && signal.rssi_max !== null) signalRows.push(`<tr><td>RSSI Max</td><td>${signal.rssi_max} dBm</td></tr>`);
        if (signal.rssi_mean !== undefined && signal.rssi_mean !== null) signalRows.push(`<tr><td>RSSI Mean</td><td>${signal.rssi_mean} dBm</td></tr>`);
        if (signal.stability !== undefined && signal.stability !== null) signalRows.push(`<tr><td>Stability</td><td>${Math.round(signal.stability * 100)}%</td></tr>`);

        const movementRows = [];
        if (movement.pattern) movementRows.push(`<tr><td>Movement</td><td>${escapeHtml(movement.pattern)}</td></tr>`);
        if (movement.appears_stationary !== undefined) {
            movementRows.push(`<tr><td>Stationary</td><td>${movement.appears_stationary ? 'Yes' : 'No'}</td></tr>`);
        }
        if (meeting.correlated !== undefined) {
            movementRows.push(`<tr><td>Meeting Correlated</td><td>${meeting.correlated ? 'Yes' : 'No'}</td></tr>`);
        }

        let observationsHtml = '';
        if (recent.length > 0) {
            observationsHtml = `
                <div style="margin-top: 12px;">
                    <h4>Recent Observations</h4>
                    <table class="device-detail-table">
                        ${recent.map(o => `
                            <tr>
                                <td>${formatTscmTimestamp(o.timestamp)}</td>
                                <td>${o.rssi !== null && o.rssi !== undefined ? `${o.rssi} dBm` : '--'}</td>
                                <td>${o.channel || o.frequency || '--'}</td>
                            </tr>
                        `).join('')}
                    </table>
                </div>
            `;
        }

        const noMetrics = metricsRows.length === 0 && signalRows.length === 0 && movementRows.length === 0;

        section.innerHTML = `
            <h4>Timeline</h4>
            ${noMetrics ? '<div class="tscm-empty">No timeline metrics available yet.</div>' : `
                <table class="device-detail-table">
                    ${metricsRows.join('')}
                    ${signalRows.join('')}
                    ${movementRows.join('')}
                </table>
            `}
            ${observationsHtml}
        `;
    } catch (e) {
        section.innerHTML = `
            <h4>Timeline</h4>
            <div class="tscm-empty">Failed to load timeline data.</div>
        `;
    }
}

async function loadDeviceTimelines() {
    const container = document.getElementById('tscmDeviceTimelinesList');
    if (!container) return;
    container.innerHTML = '<div class="tscm-empty ui-loading">Loading timelines…</div>';

    try {
        const response = await fetch('/tscm/timelines');
        const data = await response.json();

        if (data.status !== 'success' || !data.timelines || data.timelines.length === 0) {
            container.innerHTML = '<div class="tscm-empty">No device timelines available</div>';
            return;
        }

        const timelines = data.timelines;
        let html = '';
        timelines.forEach(tl => {
            const identifier = tl.identifier || 'Unknown';
            const protocol = tl.protocol || 'unknown';
            const presencePct = tl.presence_ratio !== undefined ? Math.round(tl.presence_ratio * 100) : 0;
            const pattern = tl.movement_pattern || 'UNKNOWN';
            const patternColors = { 'STATIONARY': '#00e676', 'MOBILE': '#ff3366', 'INTERMITTENT': '#ff9800' };
            const pColor = patternColors[pattern] || '#9e9e9e';

            // Create a compact swim-lane row
            html += `
                <div style="display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-bottom: 1px solid rgba(255,255,255,0.05); cursor: pointer; font-size: 10px;"
                     onclick="tscmShowInvestigateById('${escapeHtml(identifier)}', '${escapeHtml(protocol)}')">
                    <span style="width: 12px; text-transform: uppercase; color: var(--text-muted); font-size: 8px;">${protocol.charAt(0).toUpperCase()}</span>
                    <span style="flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-primary); font-family: var(--font-mono);">${escapeHtml(identifier)}</span>
                    <div style="width: 100px; height: 6px; background: rgba(255,255,255,0.1); border-radius: 3px; overflow: hidden;">
                        <div style="width: ${presencePct}%; height: 100%; background: var(--accent-cyan); border-radius: 3px;"></div>
                    </div>
                    <span style="width: 35px; text-align: right; color: var(--accent-cyan);">${presencePct}%</span>
                    <span style="padding: 1px 6px; background: ${pColor}22; color: ${pColor}; border-radius: 3px; font-size: 8px; font-weight: bold;">${pattern}</span>
                </div>
            `;
        });
        container.innerHTML = html;
    } catch (e) {
        console.error('Failed to load device timelines:', e);
        container.innerHTML = '<div class="tscm-empty">Failed to load timelines</div>';
    }
}

let deviceTimelineChartInstance = null;

async function fetchDeviceTimelineChart(identifier, protocol) {
    try {
        const response = await fetch(`/tscm/device/${encodeURIComponent(identifier)}/timeline?protocol=${encodeURIComponent(protocol)}&since_hours=24`);
        const data = await response.json();

        if (data.status !== 'success' || !data.timeline) return;

        const timeline = data.timeline;
        const observations = timeline.observations || [];
        const metrics = timeline.metrics || {};
        const signal = timeline.signal || {};
        const movement = timeline.movement || {};

        // Render Chart.js RSSI timeline
        const canvas = document.getElementById('deviceTimelineChart');
        if (canvas && typeof Chart !== 'undefined' && observations.length > 0) {
            if (deviceTimelineChartInstance) {
                deviceTimelineChartInstance.destroy();
            }

            const chartData = observations.map(o => ({
                x: new Date(o.timestamp),
                y: o.rssi !== null && o.rssi !== undefined ? o.rssi : null,
            })).filter(d => d.y !== null);

            const pointColors = chartData.map(d => d.y !== null ? 'rgba(0, 230, 118, 0.8)' : 'rgba(158, 158, 158, 0.5)');

            deviceTimelineChartInstance = new Chart(canvas, {
                type: 'line',
                data: {
                    datasets: [{
                        label: 'RSSI (dBm)',
                        data: chartData,
                        borderColor: 'rgba(0, 212, 255, 0.8)',
                        backgroundColor: 'rgba(0, 212, 255, 0.1)',
                        fill: true,
                        pointBackgroundColor: pointColors,
                        pointRadius: 3,
                        pointHoverRadius: 5,
                        tension: 0.3,
                        borderWidth: 1.5,
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                    },
                    scales: {
                        x: {
                            type: 'time',
                            time: { unit: 'hour', displayFormats: { hour: 'ha', minute: 'h:mm a' } },
                            ticks: { color: 'rgba(255,255,255,0.5)', font: { size: 9 } },
                            grid: { color: 'rgba(255,255,255,0.05)' },
                        },
                        y: {
                            title: { display: true, text: 'dBm', color: 'rgba(255,255,255,0.5)', font: { size: 9 } },
                            ticks: { color: 'rgba(255,255,255,0.5)', font: { size: 9 } },
                            grid: { color: 'rgba(255,255,255,0.05)' },
                        }
                    }
                }
            });
        }

        // Render metrics badges
        renderTimelineMetrics(metrics, signal, movement);
    } catch (e) {
        console.error('Failed to load device timeline chart:', e);
    }
}

function renderTimelineMetrics(metrics, signal, movement) {
    const container = document.getElementById('deviceTimelineMetrics');
    if (!container) return;

    const badges = [];
    if (metrics.total_observations !== undefined) {
        badges.push(`<span style="padding: 4px 8px; background: rgba(0,212,255,0.15); color: var(--accent-cyan); border-radius: 4px; font-size: 10px;">${metrics.total_observations} observations</span>`);
    }
    if (metrics.presence_ratio !== undefined) {
        const pct = Math.round(metrics.presence_ratio * 100);
        badges.push(`<span style="padding: 4px 8px; background: rgba(0,230,118,0.15); color: var(--accent-green); border-radius: 4px; font-size: 10px;">${pct}% presence</span>`);
    }
    if (signal.rssi_min !== undefined && signal.rssi_max !== undefined && signal.rssi_min !== null) {
        badges.push(`<span style="padding: 4px 8px; background: rgba(255,152,0,0.15); color: var(--accent-amber); border-radius: 4px; font-size: 10px;">${signal.rssi_min} to ${signal.rssi_max} dBm</span>`);
    }
    if (signal.stability !== undefined && signal.stability !== null) {
        badges.push(`<span style="padding: 4px 8px; background: rgba(156,39,176,0.15); color: var(--accent-purple); border-radius: 4px; font-size: 10px;">${Math.round(signal.stability * 100)}% stability</span>`);
    }
    if (movement.pattern) {
        const patternColors = { 'STATIONARY': '#00e676', 'MOBILE': '#ff3366', 'INTERMITTENT': '#ff9800' };
        const color = patternColors[movement.pattern] || '#9e9e9e';
        badges.push(`<span style="padding: 4px 8px; background: ${color}22; color: ${color}; border-radius: 4px; font-size: 10px; font-weight: bold;">${movement.pattern}</span>`);
    }
    container.innerHTML = badges.join('');
}

async function loadTscmAdvancedAnalysis(device, protocol) {
    if (protocol === 'wifi') {
        const section = document.getElementById('tscmWifiAdvancedSection');
        if (!section) return;

        if (device && device.is_client) {
            section.innerHTML = `
                <h4>WiFi Advanced Indicators</h4>
                <div class="tscm-empty">Client devices do not have AP indicators.</div>
            `;
            return;
        }

        try {
            const payload = {
                bssid: device.bssid,
                ssid: device.ssid || device.essid,
                channel: device.channel,
                encryption: device.security,
                power: device.signal,
                signal: device.signal,
            };

            const response = await fetch('/tscm/wifi/analyze-network', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
            const data = await response.json();

            if (data.status !== 'success') {
                section.innerHTML = `
                    <h4>WiFi Advanced Indicators</h4>
                    <div class="tscm-empty">No advanced indicators available.</div>
                `;
                return;
            }

            const indicators = data.indicators || [];
            if (indicators.length === 0) {
                section.innerHTML = `
                    <h4>WiFi Advanced Indicators</h4>
                    <div class="tscm-empty">No advanced indicators detected.</div>
                `;
                return;
            }

            section.innerHTML = `
                <h4>WiFi Advanced Indicators</h4>
                <div class="indicator-list">
                    ${indicators.map(i => `
                        <div class="indicator-item">
                            <span class="indicator-type">${escapeHtml((i.type || 'indicator') + (i.severity ? ` • ${i.severity}` : ''))}</span>
                            <span class="indicator-desc">${escapeHtml(i.description || '')}</span>
                        </div>
                    `).join('')}
                </div>
            `;
        } catch (e) {
            section.innerHTML = `
                <h4>WiFi Advanced Indicators</h4>
                <div class="tscm-empty">Failed to analyze network.</div>
            `;
        }
        return;
    }

    if (protocol === 'bluetooth') {
        const section = document.getElementById('tscmBleExplainSection');
        if (!section) return;
        const mac = device.mac || device.address;
        if (!mac) {
            section.innerHTML = `
                <h4>Bluetooth Risk Explanation</h4>
                <div class="tscm-empty">No identifier available for explanation.</div>
            `;
            return;
        }

        try {
            const response = await fetch(`/tscm/bluetooth/${encodeURIComponent(mac)}/explain`);
            const data = await response.json();

            if (data.status !== 'success' || !data.explanation) {
                section.innerHTML = `
                    <h4>Bluetooth Risk Explanation</h4>
                    <div class="tscm-empty">No explanation available.</div>
                `;
                return;
            }

            const exp = data.explanation;
            const risk = exp.risk || {};
            let proximity = exp.proximity || {};
            let proximityNote = proximity.explanation || '';
            let proximityDistance = proximity.estimated_distance || '';
            let proximityRssi = null;
            let proximityDisclaimer = '';
            const tracker = exp.tracker || {};
            const meeting = exp.meeting_correlation || {};
            const action = exp.recommended_action || {};
            const indicators = exp.indicators || [];

            try {
                const proxResponse = await fetch(`/tscm/bluetooth/${encodeURIComponent(mac)}/proximity`);
                const proxData = await proxResponse.json();
                if (proxData.status === 'success' && proxData.proximity) {
                    proximity = proxData.proximity;
                    proximityNote = proxData.proximity.explanation || proximityNote;
                    proximityDistance = proxData.proximity.estimated_distance || proximityDistance;
                    proximityRssi = proxData.proximity.rssi_used;
                    proximityDisclaimer = proxData.disclaimer || '';
                }
            } catch (e) {
                console.warn('BLE proximity lookup failed:', e);
            }

            section.innerHTML = `
                <h4>Bluetooth Risk Explanation</h4>
                <table class="device-detail-table">
                    <tr><td>Risk Level</td><td>${escapeHtml(risk.level || 'unknown').toUpperCase()} (${risk.score || 0})</td></tr>
                    <tr><td>Risk Rationale</td><td>${escapeHtml(risk.explanation || 'N/A')}</td></tr>
                    <tr><td>Proximity</td><td>${escapeHtml(proximity.estimate || 'unknown')} ${proximityDistance ? `(${escapeHtml(proximityDistance)})` : ''}${proximityRssi !== null ? ` — RSSI ${proximityRssi} dBm` : ''}</td></tr>
                    <tr><td>Proximity Note</td><td>${escapeHtml(proximityNote || 'N/A')}</td></tr>
                    <tr><td>Tracker</td><td>${tracker.is_tracker ? `Yes (${escapeHtml(tracker.type || 'unknown')})` : 'No'}</td></tr>
                    <tr><td>Meeting Correlated</td><td>${meeting.correlated ? 'Yes' : 'No'}</td></tr>
                    <tr><td>Recommended Action</td><td>${escapeHtml(action.action || 'monitor')} — ${escapeHtml(action.rationale || '')}</td></tr>
                </table>
                ${indicators.length > 0 ? `
                    <div style="margin-top: 12px;">
                        <h4>Indicators</h4>
                        <div class="indicator-list">
                            ${indicators.map(i => `
                                <div class="indicator-item">
                                    <span class="indicator-type">${escapeHtml(i.type || 'indicator')}</span>
                                    <span class="indicator-desc">${escapeHtml(i.description || i.explanation || '')}</span>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                ` : ''}
                ${proximityDisclaimer ? `
                    <div class="device-detail-disclaimer">
                        <strong>Note:</strong> ${escapeHtml(proximityDisclaimer)}
                    </div>
                ` : ''}
                ${exp.disclaimer ? `
                    <div class="device-detail-disclaimer">
                        <strong>Note:</strong> ${escapeHtml(exp.disclaimer)}
                    </div>
                ` : ''}
            `;
        } catch (e) {
            section.innerHTML = `
                <h4>Bluetooth Risk Explanation</h4>
                <div class="tscm-empty">Failed to load explanation.</div>
            `;
        }
    }
}

function closeTscmDeviceModal() {
    document.getElementById('tscmDeviceModal').style.display = 'none';
    if (tscmCaseLinkContext) tscmCaseLinkContext = null;
}

function listenToRfSignal(frequency, modulation) {
    // Close the modal
    closeTscmDeviceModal();

    // Switch to spectrum waterfall mode
    switchMode('waterfall');

    // Wait a moment for the mode to switch, then tune to the frequency
    setTimeout(() => {
        if (typeof Waterfall !== 'undefined' && typeof Waterfall.quickTune === 'function') {
            Waterfall.quickTune(frequency, modulation);
        } else {
            // Fallback: update Waterfall center control directly
            const freqInput = document.getElementById('wfCenterFreq');
            if (freqInput) {
                freqInput.value = frequency.toFixed(4);
            }
            alert(`Tune to ${frequency.toFixed(3)} MHz (${modulation.toUpperCase()}) to listen`);
        }
    }, 300);
}

function decodeWithOok(frequency) {
    // Close the TSCM modal and switch to OOK decoder with the detected frequency pre-filled
    closeTscmDeviceModal();
    switchMode('ook');
    setTimeout(function () {
        if (typeof OokMode !== 'undefined' && typeof OokMode.setFreq === 'function') {
            OokMode.setFreq(parseFloat(frequency).toFixed(3));
        }
    }, 300);
}

async function showDevicesByCategory(category) {
    const modal = document.getElementById('tscmDeviceModal');
    const content = document.getElementById('tscmDeviceModalContent');

    let devices = [];
    let title = '';
    let titleClass = '';

    if (category === 'correlations') {
        // Show correlations
        title = 'Cross-Protocol Correlations';
        titleClass = 'classification-yellow';

        if (tscmCorrelations.length === 0) {
            content.innerHTML = `
                <div class="device-detail-header ${titleClass}">
                    <h3>${title}</h3>
                </div>
                <div class="device-detail-section">
                    <p style="text-align: center; color: var(--text-muted);">No correlations detected yet.</p>
                </div>
            `;
        } else {
            content.innerHTML = `
                <div class="device-detail-header ${titleClass}">
                    <h3>${title} (${tscmCorrelations.length})</h3>
                </div>
                <div class="device-detail-section">
                    ${tscmCorrelations.map(c => `
                        <div class="correlation-detail-item">
                            <strong>${escapeHtml(c.description || 'Cross-protocol match')}</strong>
                            <div style="font-size: 11px; color: var(--text-muted); margin-top: 4px;">
                                Protocols: ${(c.protocols || []).join(', ')}<br>
                                Devices: ${(c.devices || []).join(', ')}
                            </div>
                        </div>
                    `).join('')}
                </div>
            `;
        }
        modal.style.display = 'flex';
        return;
    }

    if (category === 'identity') {
        title = 'Identity Clusters (MAC-Randomization Resistant)';
        titleClass = 'classification-cyan';

        if (tscmIdentityClusters.length === 0) {
            await tscmRefreshIdentityClusters();
        }

        if (tscmIdentityClusters.length === 0) {
            content.innerHTML = `
                <div class="device-detail-header ${titleClass}">
                    <h3>${title}</h3>
                </div>
                <div class="device-detail-section">
                    <p style="text-align: center; color: var(--text-muted);">No identity clusters detected yet.</p>
                </div>
            `;
        } else {
            content.innerHTML = `
                <div class="device-detail-header ${titleClass}">
                    <h3>${title} (${tscmIdentityClusters.length})</h3>
                    <button class="preset-btn" onclick="tscmRefreshIdentityClusters().then(() => showDevicesByCategory('identity'))" style="font-size: 10px; padding: 6px 8px;">
                        Refresh
                    </button>
                </div>
                <div class="device-detail-section">
                    ${tscmIdentityClusters.map(c => `
                        <div class="correlation-item">
                            <strong>${escapeHtml(c.best_name || c.manufacturer_name || c.cluster_id)}</strong>
                            <div class="correlation-devices">
                                Risk: ${escapeHtml(c.risk_level || 'informational')} (${c.risk_score || 0}) |
                                MACs: ${(c.linked_macs || []).length} |
                                Observations: ${c.total_observations || 0} |
                                Confidence: ${c.confidence !== undefined ? (c.confidence * 100).toFixed(0) + '%' : 'n/a'}
                            </div>
                        </div>
                    `).join('')}
                </div>
                <div class="device-detail-disclaimer">
                    <strong>Note:</strong> Identity clustering is probabilistic. It links observations by passive fingerprints and timing patterns.
                </div>
            `;
        }
        modal.style.display = 'flex';
        return;
    }

    // Filter devices by classification
    const filteredForCategory = getFilteredDevices({ ignoreRisk: true });
    const allDevices = [
        ...filteredForCategory.wifi.map(d => ({ ...d, protocol: 'wifi', id: d.bssid })),
        ...filteredForCategory.bt.map(d => ({ ...d, protocol: 'bluetooth', id: d.mac })),
        ...filteredForCategory.rf.map(d => ({ ...d, protocol: 'rf', id: d.frequency }))
    ];

    if (category === 'high_interest') {
        devices = allDevices.filter(d => d.classification === 'high_interest');
        title = 'High Interest Devices';
        titleClass = 'classification-red';
    } else if (category === 'review') {
        devices = allDevices.filter(d => d.classification === 'review');
        title = 'Devices Needing Review';
        titleClass = 'classification-yellow';
    } else if (category === 'informational') {
        devices = allDevices.filter(d => d.classification === 'informational');
        title = 'Informational Devices';
        titleClass = 'classification-green';
    }

    // Sort by score descending
    devices.sort((a, b) => (b.score || 0) - (a.score || 0));

    if (devices.length === 0) {
        content.innerHTML = `
            <div class="device-detail-header ${titleClass}">
                <h3>${title}</h3>
            </div>
            <div class="device-detail-section">
                <p style="text-align: center; color: var(--text-muted);">No devices in this category.</p>
            </div>
        `;
    } else {
        content.innerHTML = `
            <div class="device-detail-header ${titleClass}">
                <h3>${title} (${devices.length})</h3>
            </div>
            <div class="category-device-list">
                ${devices.map(d => `
                    <div class="category-device-item" onclick="event.stopPropagation(); showDeviceDetails('${d.id}', '${d.protocol}')">
                        <div class="category-device-header">
                            <span class="category-device-name">
                                ${getClassificationIcon(d.classification)}
                                ${escapeHtml(d.name || d.ssid || d.mac || d.bssid || (d.frequency ? d.frequency.toFixed(3) + ' MHz' : 'Unknown'))}
                            </span>
                            <span class="category-device-score">${d.score || 0}</span>
                        </div>
                        <div class="category-device-meta">
                            <span class="protocol-badge">${d.protocol}</span>
                            ${d.indicators ? d.indicators.slice(0, 2).map(i => `<span class="indicator-mini">${i.type}</span>`).join('') : ''}
                        </div>
                    </div>
                `).join('')}
            </div>
        `;
    }
    modal.style.display = 'flex';
}

function showBaselineComparison() {
    const modal = document.getElementById('tscmDeviceModal');
    const content = document.getElementById('tscmDeviceModalContent');

    if (!tscmBaselineComparison) {
        content.innerHTML = `
            <div class="device-detail-header classification-orange">
                <h3>Baseline Comparison</h3>
            </div>
            <div class="device-detail-section">
                <p style="text-align: center; color: var(--text-muted);">No baseline comparison data available.</p>
            </div>
        `;
        modal.style.display = 'flex';
        return;
    }

    const comparison = tscmBaselineComparison;
    const baselineName = comparison.baseline_name || 'Baseline';

    const formatItem = (item, protocol) => {
        if (protocol === 'wifi') {
            const name = item.essid || item.ssid || 'Hidden SSID';
            const id = item.bssid || item.mac || '';
            return `${escapeHtml(name)} ${id ? `<span class="device-detail-id">${escapeHtml(id)}</span>` : ''}`;
        }
        if (protocol === 'wifi_clients') {
            const name = item.vendor || 'WiFi Client';
            const id = item.mac || item.address || '';
            return `${escapeHtml(name)} ${id ? `<span class="device-detail-id">${escapeHtml(id)}</span>` : ''}`;
        }
        if (protocol === 'bluetooth') {
            const name = item.name || 'Unknown';
            const id = item.mac || item.address || '';
            return `${escapeHtml(name)} ${id ? `<span class="device-detail-id">${escapeHtml(id)}</span>` : ''}`;
        }
        if (protocol === 'rf') {
            const freq = item.frequency ? `${item.frequency} MHz` : 'Unknown Frequency';
            const band = item.band || '';
            return `${escapeHtml(freq)} ${band ? `<span class="device-detail-id">${escapeHtml(band)}</span>` : ''}`;
        }
        return escapeHtml(item.name || item.identifier || 'Unknown');
    };

    const renderList = (items, protocol, limit = 10) => {
        if (!items || items.length === 0) {
            return '<div class="tscm-empty">None</div>';
        }
        const listItems = items.slice(0, limit).map(i => `<li>${formatItem(i, protocol)}</li>`).join('');
        const more = items.length > limit
            ? `<div class="tscm-more-hint">+${items.length - limit} more</div>`
            : '';
        return `<ul class="device-reasons-list">${listItems}</ul>${more}`;
    };

    const sections = [
        { key: 'wifi', label: 'WiFi' },
        { key: 'wifi_clients', label: 'WiFi Clients' },
        { key: 'bluetooth', label: 'Bluetooth' },
        { key: 'rf', label: 'RF' },
    ];

    content.innerHTML = `
        <div class="device-detail-header classification-orange">
            <h3>Baseline Comparison — ${escapeHtml(baselineName)}</h3>
        </div>
        <div class="device-detail-section">
            <div style="font-size: 11px; color: var(--text-muted); margin-bottom: 12px;">
                New: ${comparison.total_new || 0} | Missing: ${comparison.total_missing || 0}
            </div>
            ${sections.map(section => {
                const data = comparison[section.key] || {};
                return `
                    <div style="margin-bottom: 16px;">
                        <h4>${section.label}</h4>
                        <div style="font-size: 10px; color: var(--text-muted); margin-bottom: 6px;">
                            New: ${data.new_count || 0} | Missing: ${data.missing_count || 0}
                        </div>
                        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
                            <div>
                                <strong style="font-size: 10px; color: var(--text-secondary);">New</strong>
                                ${renderList(data.new || [], section.key)}
                            </div>
                            <div>
                                <strong style="font-size: 10px; color: var(--text-secondary);">Missing</strong>
                                ${renderList(data.missing || [], section.key)}
                            </div>
                        </div>
                    </div>
                `;
            }).join('')}
        </div>
        <div class="device-detail-disclaimer">
            <strong>Note:</strong> Baseline comparisons indicate environmental changes, not confirmed threats. Validate before action.
        </div>
    `;
    modal.style.display = 'flex';
}

function updateTscmDisplays() {
    const filtered = getFilteredDevices();
    const filtersActive = tscmFilters.protocol !== 'all' || tscmFilters.risk !== 'all' ||
        tscmFilters.status !== 'all' || tscmFilters.known !== 'all';
    // Update WiFi list
    const wifiList = document.getElementById('tscmWifiList');
    if (filtered.wifi.length === 0) {
        wifiList.innerHTML = `<div class="tscm-empty">${filtersActive ? 'No WiFi networks match filters' : 'No WiFi networks detected'}</div>`;
    } else {
        // Sort by score (highest first)
        const sorted = [...filtered.wifi].sort((a, b) => (b.score || 0) - (a.score || 0));
        wifiList.innerHTML = sorted.map(d => {
            const dkey = _tscmDeviceKey(d, 'wifi');
            const cleared = tscmClearedDevices.has(dkey);
            return `
            <div class="tscm-device-item ${getClassificationClass(d.classification)}${cleared ? ' tscm-cleared' : ''}" onclick="showDeviceDetails('${d.bssid}', 'wifi')">
                <div class="tscm-device-header">
                    <div class="tscm-device-name">
                        <span class="classification-indicator">${getClassificationIcon(d.classification)}</span>
                        ${escapeHtml(d.ssid || d.bssid || 'Hidden')}
                        ${cleared ? '<span class="cleared-badge">CLEARED</span>' : ''}
                        ${d.known_device ? '<span class="known-badge" title="Known device">KNOWN</span>' : ''}
                    </div>
                    ${getScoreBadge(d.score)}
                </div>
                <div class="tscm-device-meta">
                    <span>${d.bssid}</span>
                    <span>${d.signal || '--'} dBm</span>
                    <span>${escapeHtml(d.vendor || 'Unknown')} • ${escapeHtml(d.security || 'Open')}</span>
                </div>
                ${d.indicators && d.indicators.length > 0 ? `<div class="tscm-device-indicators">${formatIndicators(d.indicators)}</div>` : ''}
                ${d.recommended_action && d.recommended_action !== 'monitor' ? `<div class="tscm-action">Action: ${d.recommended_action}</div>` : ''}
                <div class="tscm-device-actions" onclick="event.stopPropagation()">
                    <button class="tscm-action-btn${cleared ? ' cleared' : ''}" onclick="tscmMarkCleared('${dkey}')" title="${cleared ? 'Undo cleared mark' : 'Mark as investigated and cleared'}">${cleared ? '✓ Cleared' : 'Mark Cleared'}</button>
                    <button class="tscm-action-btn ignore" onclick="tscmAddToIgnoreList('${dkey}')" title="Add to ignore list (my device)">Ignore</button>
                </div>
            </div>`;
        }).join('');
    }
    document.getElementById('tscmWifiCount').textContent = filtered.wifi.length;

    // Update WiFi clients list
    const wifiClientList = document.getElementById('tscmWifiClientList');
    if (filtered.wifi_clients.length === 0) {
        wifiClientList.innerHTML = `<div class="tscm-empty">${filtersActive ? 'No WiFi clients match filters' : 'No WiFi clients detected'}</div>`;
    } else {
        const sortedClients = [...filtered.wifi_clients].sort((a, b) => (b.score || 0) - (a.score || 0));
        wifiClientList.innerHTML = sortedClients.map(c => {
            const ckey = `wifi:${c.mac || c.address}`;
            const cleared = tscmClearedDevices.has(ckey);
            return `
            <div class="tscm-device-item ${getClassificationClass(c.classification)}${cleared ? ' tscm-cleared' : ''}" onclick="showDeviceDetails('${c.mac}', 'wifi')">
                <div class="tscm-device-header">
                    <div class="tscm-device-name">
                        <span class="classification-indicator">${getClassificationIcon(c.classification)}</span>
                        ${escapeHtml(c.vendor || 'WiFi Client')}
                        <span class="client-badge" title="WiFi client">CLIENT</span>
                        ${cleared ? '<span class="cleared-badge">CLEARED</span>' : ''}
                        ${c.known_device ? '<span class="known-badge" title="Known device">KNOWN</span>' : ''}
                    </div>
                    ${getScoreBadge(c.score)}
                </div>
                <div class="tscm-device-meta">
                    <span>${c.mac}</span>
                    <span>${c.rssi || '--'} dBm</span>
                    <span>${c.associated_bssid ? `Assoc: ${c.associated_bssid}` : `Probes: ${c.probe_count || 0}`}</span>
                </div>
                ${c.indicators && c.indicators.length > 0 ? `<div class="tscm-device-indicators">${formatIndicators(c.indicators)}</div>` : ''}
                ${c.recommended_action && c.recommended_action !== 'monitor' ? `<div class="tscm-action">Action: ${c.recommended_action}</div>` : ''}
                <div class="tscm-device-actions" onclick="event.stopPropagation()">
                    <button class="tscm-action-btn${cleared ? ' cleared' : ''}" onclick="tscmMarkCleared('${ckey}')">${cleared ? '✓ Cleared' : 'Mark Cleared'}</button>
                    <button class="tscm-action-btn ignore" onclick="tscmAddToIgnoreList('${ckey}')">Ignore</button>
                </div>
            </div>`;
        }).join('');
    }
    document.getElementById('tscmWifiClientCount').textContent = filtered.wifi_clients.length;

    // Update BT list
    const btList = document.getElementById('tscmBtList');
    if (filtered.bt.length === 0) {
        btList.innerHTML = `<div class="tscm-empty">${filtersActive ? 'No Bluetooth devices match filters' : 'No Bluetooth devices detected'}</div>`;
    } else {
        // Sort by score (highest first)
        const sorted = [...filtered.bt].sort((a, b) => (b.score || 0) - (a.score || 0));
        btList.innerHTML = sorted.map(d => {
            const dkey = _tscmDeviceKey(d, 'bluetooth');
            const cleared = tscmClearedDevices.has(dkey);
            return `
            <div class="tscm-device-item ${getClassificationClass(d.classification)}${cleared ? ' tscm-cleared' : ''}" onclick="showDeviceDetails('${d.mac}', 'bluetooth')">
                <div class="tscm-device-header">
                    <div class="tscm-device-name">
                        <span class="classification-indicator">${getClassificationIcon(d.classification)}</span>
                        ${escapeHtml(d.name || 'Unknown')}
                        ${d.is_audio_capable ? '<span class="audio-badge" title="Audio-capable device">AUDIO</span>' : ''}
                        ${formatTrackerBadge(d)}
                        ${cleared ? '<span class="cleared-badge">CLEARED</span>' : ''}
                        ${d.known_device ? '<span class="known-badge" title="Known device">KNOWN</span>' : ''}
                    </div>
                    ${getScoreBadge(d.score)}
                </div>
                <div class="tscm-device-meta">
                    <span>${d.mac}</span>
                    <span>${d.rssi || '--'} dBm</span>
                    <span>${escapeHtml([d.device_type, d.manufacturer].filter(Boolean).join(' • ') || 'Unknown')}</span>
                </div>
                ${d.indicators && d.indicators.length > 0 ? `<div class="tscm-device-indicators">${formatIndicators(d.indicators)}</div>` : ''}
                ${d.recommended_action && d.recommended_action !== 'monitor' ? `<div class="tscm-action">Action: ${d.recommended_action}</div>` : ''}
                <div class="tscm-device-actions" onclick="event.stopPropagation()">
                    <button class="tscm-action-btn${cleared ? ' cleared' : ''}" onclick="tscmMarkCleared('${dkey}')">${cleared ? '✓ Cleared' : 'Mark Cleared'}</button>
                    <button class="tscm-action-btn ignore" onclick="tscmAddToIgnoreList('${dkey}')">Ignore</button>
                </div>
            </div>`;
        }).join('');
    }
    document.getElementById('tscmBtCount').textContent = filtered.bt.length;

    // Update RF list
    const rfList = document.getElementById('tscmRfList');
    if (filtered.rf.length === 0) {
        if (tscmRfStatusMessage) {
            rfList.innerHTML = `<div class="tscm-status-message">${escapeHtml(tscmRfStatusMessage)}</div>`;
        } else {
            rfList.innerHTML = `<div class="tscm-empty">${filtersActive ? 'No RF signals match filters' : 'No RF signals detected'}</div>`;
        }
    } else {
        // Sort by score (highest first)
        const sorted = [...filtered.rf].sort((a, b) => (b.score || 0) - (a.score || 0));
        rfList.innerHTML = sorted.map(s => {
            const skey = _tscmDeviceKey(s, 'rf');
            const cleared = tscmClearedDevices.has(skey);
            return `
            <div class="tscm-device-item ${getClassificationClass(s.classification)}${cleared ? ' tscm-cleared' : ''}" onclick="showDeviceDetails('${s.frequency}', 'rf')">
                <div class="tscm-device-header">
                    <div class="tscm-device-name">
                        <span class="classification-indicator">${getClassificationIcon(s.classification)}</span>
                        ${s.frequency.toFixed(3)} MHz
                        ${cleared ? '<span class="cleared-badge">CLEARED</span>' : ''}
                        ${s.known_device ? '<span class="known-badge" title="Known device">KNOWN</span>' : ''}
                    </div>
                    ${getScoreBadge(s.score)}
                </div>
                <div class="tscm-device-meta">
                    <span>${s.band}</span>
                    <span>${s.power.toFixed(1)} dBm</span>
                    <span>+${(s.signal_strength || 0).toFixed(1)} dB above noise</span>
                </div>
                ${s.indicators && s.indicators.length > 0 ? `<div class="tscm-device-indicators">${formatIndicators(s.indicators)}</div>` : ''}
                ${s.recommended_action && s.recommended_action !== 'monitor' ? `<div class="tscm-action">Action: ${s.recommended_action}</div>` : ''}
                <div class="tscm-device-actions" onclick="event.stopPropagation()">
                    <button class="tscm-action-btn${cleared ? ' cleared' : ''}" onclick="tscmMarkCleared('${skey}')">${cleared ? '✓ Cleared' : 'Mark Cleared'}</button>
                    <button class="tscm-action-btn ignore" onclick="tscmAddToIgnoreList('${skey}')">Ignore</button>
                </div>
            </div>`;
        }).join('');
    }
    document.getElementById('tscmRfCount').textContent = filtered.rf.length;

    // Update threats list
    const threatList = document.getElementById('tscmThreatList');
    let threatItems = tscmThreats;
    if (tscmFilters.protocol !== 'all') {
        threatItems = threatItems.filter(t => t.source === tscmFilters.protocol);
    }
    if (threatItems.length === 0) {
        threatList.innerHTML = '<div class="tscm-empty"><div class="tscm-empty-primary">Monitoring active — nothing flagged</div><div class="tscm-empty-secondary">Signals are being analyzed against baseline thresholds. This does not rule out passive or dormant devices.</div></div>';
    } else {
        threatList.innerHTML = '<div class="tscm-threat-list">' + threatItems.map(t => `
            <div class="tscm-threat-item ${t.severity}" onclick="showDeviceDetails('${escapeHtml(t.identifier)}', '${escapeHtml(t.source)}')" style="cursor: pointer;">
                <div class="tscm-threat-header">
                    <span class="tscm-threat-type">${escapeHtml(t.threat_type || 'Unknown')}</span>
                    <span class="tscm-threat-severity">${t.severity}</span>
                    ${t.threat_id ? `
                        <button class="tscm-case-link-btn" onclick="event.stopPropagation(); tscmPromptLinkThreat(${t.threat_id})">
                            Link
                        </button>
                    ` : ''}
                </div>
                <div class="tscm-threat-details">
                    <strong>${escapeHtml(t.name || t.identifier)}</strong><br>
                    Source: ${t.source} | Signal: ${t.signal_strength || '--'} dBm
                </div>
            </div>
        `).join('') + '</div>';
    }
}

function updateCorrelationsDisplay() {
    const container = document.getElementById('tscmCorrelationsContainer');
    if (!container) return;

    const hasCorrelations = tscmCorrelations.length > 0;
    const hasIdentity = tscmIdentityClusters.length > 0;

    if (!hasCorrelations && !hasIdentity) {
        container.innerHTML = '';
        container.style.display = 'none';
        return;
    }

    container.style.display = 'block';
    const sections = [];

    if (hasCorrelations) {
        sections.push(`
            <div class="tscm-correlations">
                <h4>Cross-Protocol Correlations (${tscmCorrelations.length})</h4>
                ${tscmCorrelations.map(c => `
                    <div class="correlation-item">
                        <strong>${escapeHtml(c.description)}</strong>
                        <div class="correlation-devices">
                            Devices: ${c.devices.join(', ')} | Protocols: ${c.protocols.join(', ')}
                        </div>
                    </div>
                `).join('')}
            </div>
        `);
    }

    if (hasIdentity) {
        const sortedClusters = [...tscmIdentityClusters]
            .sort((a, b) => (b.risk_score || 0) - (a.risk_score || 0));
        const topClusters = sortedClusters.slice(0, 10);
        const summaryText = tscmIdentitySummary
            ? `High: ${tscmIdentitySummary.high || 0} | Medium: ${tscmIdentitySummary.medium || 0} | Total: ${tscmIdentitySummary.total || tscmIdentityClusters.length}`
            : `Total: ${tscmIdentityClusters.length}`;

        sections.push(`
            <div class="tscm-correlations">
                <h4>Identity Clusters (MAC-Randomization Resistant) — ${summaryText}</h4>
                ${topClusters.map(c => `
                    <div class="correlation-item">
                        <strong>${escapeHtml(c.best_name || c.manufacturer_name || c.cluster_id)}</strong>
                        <div class="correlation-devices">
                            Risk: ${escapeHtml(c.risk_level || 'informational')} (${c.risk_score || 0}) |
                            MACs: ${(c.linked_macs || []).length} |
                            Observations: ${c.total_observations || 0} |
                            Confidence: ${c.confidence !== undefined ? (c.confidence * 100).toFixed(0) + '%' : 'n/a'}
                        </div>
                    </div>
                `).join('')}
                ${tscmIdentityClusters.length > topClusters.length ? `
                    <div class="tscm-more-hint">Showing top ${topClusters.length} clusters. Use the Identity Clusters card for full list.</div>
                ` : ''}
            </div>
        `);
    }

    container.innerHTML = sections.join('');
}

function completeTscmSweep(data) {
    isTscmRunning = false;
    if (tscmEventSource) {
        tscmEventSource.close();
        tscmEventSource = null;
    }

    document.getElementById('startTscmBtn').style.display = 'block';
    document.getElementById('stopTscmBtn').style.display = 'none';
    document.getElementById('tscmProgress').style.display = 'none';
    document.getElementById('tscmProgressLabel').textContent = 'Sweep Complete';
    document.getElementById('tscmProgressPercent').textContent = '100%';

    // Final update of counts
    updateTscmThreatCounts();

    // Display sweep summary with correlation results
    const summaryContainer = document.getElementById('tscmSweepSummary');
    if (summaryContainer && data) {
        if (data.sweep_id) {
            tscmLastSweepId = data.sweep_id;
        }
        const highInterest = data.high_interest_devices || 0;
        const needsReview = data.needs_review_devices || 0;
        const correlations = data.correlations_found || 0;
        const identityClusters = data.identity_clusters || (tscmIdentitySummary ? tscmIdentitySummary.total : 0);
        const baselineNew = data.baseline_new_devices || 0;
        const baselineMissing = data.baseline_missing_devices || 0;
        const wifiCount = data.wifi_count ?? tscmWifiDevices.length;
        const wifiClientCount = data.wifi_client_count ?? tscmWifiClients.length;
        const btCount = data.bt_count ?? tscmBtDevices.length;
        const rfCount = data.rf_count ?? tscmRfSignals.length;

        let assessment = 'BASELINE ENVIRONMENT';
        let assessmentClass = 'informational';
        if (highInterest > 0 || correlations > 0) {
            assessment = 'ELEVATED CONCERN';
            assessmentClass = 'high-interest';
        } else if (needsReview > 3) {
            assessment = 'MODERATE CONCERN';
            assessmentClass = 'needs-review';
        } else if (needsReview > 0) {
            assessment = 'LOW CONCERN';
            assessmentClass = 'needs-review';
        }

        summaryContainer.innerHTML = `
            <div class="tscm-summary-box">
                <div class="summary-stat high-interest">
                    <div class="count">${highInterest}</div>
                    <div class="label">High Interest</div>
                </div>
                <div class="summary-stat needs-review">
                    <div class="count">${needsReview}</div>
                    <div class="label">Needs Review</div>
                </div>
                <div class="summary-stat">
                    <div class="count">${correlations}</div>
                    <div class="label">Correlations</div>
                </div>
                <div class="summary-stat">
                    <div class="count">${identityClusters}</div>
                    <div class="label">Identity Clusters</div>
                </div>
                ${(baselineNew || baselineMissing) ? `
                <div class="summary-stat">
                    <div class="count">+${baselineNew} / -${baselineMissing}</div>
                    <div class="label">Baseline Delta</div>
                </div>
                ` : ''}
            </div>
            <div class="tscm-summary-meta" style="margin-top: 8px; font-size: 10px; color: var(--text-muted);">
                Devices: ${wifiCount} WiFi AP • ${wifiClientCount} WiFi Clients • ${btCount} BT • ${rfCount} RF
            </div>
            <div class="tscm-assessment ${assessmentClass}">
                <strong>Assessment:</strong> ${assessment}
            </div>
            ${(baselineNew || baselineMissing) && tscmBaselineComparison ? `
            <div style="margin-top: 8px;">
                <button class="preset-btn" onclick="showBaselineComparison()" style="font-size: 10px;">
                    View Baseline Diff
                </button>
            </div>
            ` : ''}
            ${data.sweep_id ? `
            <div style="margin-top: 8px;">
                <button class="preset-btn" onclick="tscmPromptLinkSweep(${data.sweep_id})" style="font-size: 10px;">
                    Link Sweep to Case
                </button>
            </div>
            ` : ''}
            <div class="tscm-disclaimer">
                This screening identifies wireless/RF anomalies, NOT confirmed surveillance devices.
                Findings require professional verification.
            </div>
        `;
        summaryContainer.style.display = 'block';
    }

    // Update correlations display
    updateCorrelationsDisplay();
}

async function tscmRecordBaseline() {
    const name = document.getElementById('tscmBaselineName').value ||
        `Baseline ${new Date().toLocaleString()}`;

    try {
        const response = await fetch('/tscm/baseline/record', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
        });

        const data = await response.json();
        if (data.status === 'success') {
            isRecordingBaseline = true;
            document.getElementById('tscmRecordBaselineBtn').style.display = 'none';
            document.getElementById('tscmStopBaselineBtn').style.display = 'block';
            document.getElementById('tscmBaselineStatus').textContent = 'Recording baseline...';
            document.getElementById('tscmBaselineStatus').style.color = '#ff9933';
        } else {
            alert(data.message || 'Failed to start baseline recording');
        }
    } catch (e) {
        console.error('Failed to start baseline:', e);
        alert('Failed to start baseline recording');
    }
}

async function tscmStopBaseline() {
    try {
        const response = await fetch('/tscm/baseline/stop', { method: 'POST' });
        const data = await response.json();

        isRecordingBaseline = false;
        document.getElementById('tscmRecordBaselineBtn').style.display = 'block';
        document.getElementById('tscmStopBaselineBtn').style.display = 'none';

        if (data.status === 'success') {
            document.getElementById('tscmBaselineStatus').textContent =
                `Baseline saved: ${data.wifi_count} WiFi, ${data.wifi_client_count || 0} Clients, ${data.bt_count} BT, ${data.rf_count} RF`;
            document.getElementById('tscmBaselineStatus').style.color = '#00ff88';
            loadTscmBaselines();
        } else {
            document.getElementById('tscmBaselineStatus').textContent = data.message || 'Recording stopped';
            document.getElementById('tscmBaselineStatus').style.color = 'var(--text-muted)';
        }
    } catch (e) {
        console.error('Failed to stop baseline:', e);
        document.getElementById('tscmBaselineStatus').textContent = 'Error stopping baseline';
    }
}

function escapeHtml(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// ========== TSCM Advanced Features ==========

// Meeting Window Management
let tscmActiveMeetingId = null;
let tscmMeetingStartTime = null;

async function tscmStartMeeting() {
    const meetingName = document.getElementById('tscmMeetingName').value ||
        `Meeting ${new Date().toLocaleString()}`;

    try {
        const response = await fetch('/tscm/meeting/start-tracked', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: meetingName })
        });

        const data = await response.json();
        if (data.status === 'success') {
            tscmActiveMeetingId = data.meeting_id;
            tscmMeetingStartTime = new Date();
            tscmLastMeetingId = null;

            // Update UI
            document.getElementById('tscmStartMeetingBtn').style.display = 'none';
            document.getElementById('tscmEndMeetingBtn').style.display = 'block';
            document.getElementById('tscmMeetingStatus').innerHTML =
                `<span style="color: var(--accent-amber);">Meeting active: ${escapeHtml(meetingName)}</span>`;
            const summaryBtn = document.getElementById('tscmMeetingSummaryBtn');
            if (summaryBtn) summaryBtn.style.display = 'block';

            // Show meeting banner
            const banner = document.getElementById('tscmMeetingBanner');
            if (banner) {
                banner.style.display = 'flex';
                const nameSpan = document.getElementById('tscmMeetingBannerName');
                if (nameSpan) nameSpan.textContent = meetingName;
                const timeSpan = document.getElementById('tscmMeetingBannerTime');
                if (timeSpan) timeSpan.textContent = `Started ${new Date().toLocaleTimeString()}`;
            }
        } else {
            alert(data.message || 'Failed to start meeting window');
        }
    } catch (e) {
        console.error('Failed to start meeting:', e);
        alert('Failed to start meeting window');
    }
}

async function tscmEndMeeting() {
    if (!tscmActiveMeetingId) return;

    try {
        const response = await fetch(`/tscm/meeting/${tscmActiveMeetingId}/end`, {
            method: 'POST'
        });

        const data = await response.json();

        // Update UI
        document.getElementById('tscmStartMeetingBtn').style.display = 'block';
        document.getElementById('tscmEndMeetingBtn').style.display = 'none';

        // Hide meeting banner
        const banner = document.getElementById('tscmMeetingBanner');
        if (banner) banner.style.display = 'none';

        if (data.status === 'success') {
            const duration = tscmMeetingStartTime ?
                Math.round((new Date() - tscmMeetingStartTime) / 60000) : 0;
            document.getElementById('tscmMeetingStatus').innerHTML =
                `<span style="color: var(--accent-green);">Meeting ended (${duration} min) - ${data.devices_flagged || 0} devices flagged</span>`;

            // Show export section if devices were flagged
            if (data.devices_flagged > 0) {
                document.getElementById('tscmExportSection').style.display = 'block';
            }
        } else {
            document.getElementById('tscmMeetingStatus').textContent = 'Meeting ended';
        }

        tscmLastMeetingId = tscmActiveMeetingId;
        tscmActiveMeetingId = null;
        tscmMeetingStartTime = null;

        const summaryBtn = document.getElementById('tscmMeetingSummaryBtn');
        if (summaryBtn) summaryBtn.style.display = tscmLastMeetingId ? 'block' : 'none';
    } catch (e) {
        console.error('Failed to end meeting:', e);
    }
}

async function tscmShowMeetingSummary(meetingId) {
    let id = meetingId || tscmActiveMeetingId || tscmLastMeetingId;

    if (!id) {
        try {
            const activeRes = await fetch('/tscm/meeting/active');
            const activeData = await activeRes.json();
            if (activeData && activeData.meeting) {
                id = activeData.meeting.id;
                tscmActiveMeetingId = id;
            }
        } catch (e) {
            console.warn('Failed to fetch active meeting:', e);
        }
    }

    if (!id) {
        alert('No meeting window available for summary.');
        return;
    }

    const modal = document.getElementById('tscmDeviceModal');
    const content = document.getElementById('tscmDeviceModalContent');
    content.innerHTML = '<div class="ui-loading">Loading meeting summary…</div>';
    modal.style.display = 'flex';

    try {
        const response = await fetch(`/tscm/meeting/${id}/summary`);
        const data = await response.json();

        if (data.status !== 'success' || !data.summary) {
            content.innerHTML = '<div style="padding: 20px; color: var(--accent-red);">Failed to load meeting summary.</div>';
            return;
        }

        const summary = data.summary;
        const metrics = summary.summary || {};
        const firstSeen = summary.devices_first_seen || [];
        const behavior = summary.devices_behavior_change || [];

        content.innerHTML = `
            <div class="device-detail-header classification-cyan">
                <h3>${escapeHtml(summary.name || 'Meeting Summary')}</h3>
            </div>
            <div class="device-detail-section">
                <table class="device-detail-table">
                    <tr><td>Start</td><td>${summary.start_time ? new Date(summary.start_time).toLocaleString() : 'N/A'}</td></tr>
                    <tr><td>End</td><td>${summary.end_time ? new Date(summary.end_time).toLocaleString() : 'In progress'}</td></tr>
                    <tr><td>Duration</td><td>${summary.duration_minutes ? `${summary.duration_minutes} min` : 'N/A'}</td></tr>
                    <tr><td>Total Active Devices</td><td>${metrics.total_devices_active || 0}</td></tr>
                    <tr><td>New During Meeting</td><td>${metrics.new_devices || 0}</td></tr>
                    <tr><td>Behavior Changes</td><td>${metrics.behavior_changes || 0}</td></tr>
                    <tr><td>High Interest</td><td>${metrics.high_interest || 0}</td></tr>
                </table>
            </div>
            <div class="device-detail-section">
                <h4>Devices First Seen During Meeting (${firstSeen.length})</h4>
                ${firstSeen.length === 0
                ? '<div class="tscm-empty">No devices first seen during meeting.</div>'
                : `<div class="tscm-summary-list">
                        ${firstSeen.map(d => `
                            <div class="tscm-summary-item">
                                <strong>${escapeHtml(d.name || d.identifier)}</strong>
                                <div class="tscm-summary-meta">
                                    ${escapeHtml(d.protocol || 'unknown')} • ${escapeHtml(d.description || '')}
                                </div>
                                ${d.risk_modifier ? `<div class="tscm-summary-risk">${escapeHtml(d.risk_modifier)}</div>` : ''}
                            </div>
                        `).join('')}
                    </div>`
                }
            </div>
            <div class="device-detail-section">
                <h4>Behavior Changes (${behavior.length})</h4>
                ${behavior.length === 0
                ? '<div class="tscm-empty">No behavior changes detected.</div>'
                : `<div class="tscm-summary-list">
                        ${behavior.map(d => `
                            <div class="tscm-summary-item">
                                <strong>${escapeHtml(d.name || d.identifier)}</strong>
                                <div class="tscm-summary-meta">
                                    ${escapeHtml(d.protocol || 'unknown')} • ${escapeHtml(d.description || '')}
                                </div>
                            </div>
                        `).join('')}
                    </div>`
                }
            </div>
            ${summary.disclaimer ? `
                <div class="device-detail-disclaimer">
                    <strong>Note:</strong> ${escapeHtml(summary.disclaimer)}
                </div>
            ` : ''}
        `;
    } catch (e) {
        console.error('Failed to load meeting summary:', e);
        content.innerHTML = '<div style="padding: 20px; color: var(--accent-red);">Failed to load meeting summary.</div>';
    }
}

// Capabilities Display
async function tscmShowCapabilities() {
    const modal = document.getElementById('tscmDeviceModal');
    const content = document.getElementById('tscmDeviceModalContent');

    content.innerHTML = '<div class="ui-loading">Loading capabilities…</div>';
    modal.style.display = 'flex';

    try {
        const response = await fetch('/tscm/capabilities');
        const data = await response.json();

        if (data.status === 'success') {
            const caps = data.capabilities;

            // Determine availability from nested structure
            const wifiAvailable = caps.wifi && caps.wifi.mode !== 'unavailable';
            const btAvailable = caps.bluetooth && caps.bluetooth.mode !== 'unavailable';
            const rfAvailable = caps.rf && caps.rf.available;

            // Build can/cannot detect lists based on capabilities
            const canDetect = [];
            const cannotDetect = [];

            if (wifiAvailable) {
                canDetect.push('WiFi access points and networks');
                canDetect.push('Hidden SSIDs (presence only)');
                if (caps.wifi.monitor_capable) {
                    canDetect.push('WiFi client devices (probe requests)');
                    canDetect.push('Deauthentication attacks');
                }
            } else {
                cannotDetect.push('WiFi networks - no adapter available');
            }

            if (btAvailable) {
                canDetect.push('Bluetooth Classic devices');
                canDetect.push('BLE beacons and trackers');
                canDetect.push('Audio-capable Bluetooth devices');
            } else {
                cannotDetect.push('Bluetooth devices - no adapter available');
            }

            if (rfAvailable) {
                const minFreq = caps.rf.frequency_range_mhz?.min || 0;
                const maxFreq = caps.rf.frequency_range_mhz?.max || 0;
                canDetect.push(`RF signals (${minFreq}-${maxFreq} MHz)`);
                canDetect.push('Unknown transmitters in frequency range');
            } else {
                cannotDetect.push('RF signals - no SDR device available');
            }

            // Always cannot detect
            cannotDetect.push('Wired surveillance devices');
            cannotDetect.push('Passive listening devices (no transmitter)');
            cannotDetect.push('Devices that are powered off');
            cannotDetect.push('Burst/store-and-forward transmitters (when idle)');

            content.innerHTML = `
                <div class="device-detail-header classification-cyan">
                    <h3>Sweep Capabilities</h3>
                </div>
                <div class="device-detail-section">
                    <h4>System Information</h4>
                    <div style="font-size: 11px; color: var(--text-muted); margin-bottom: 12px;">
                        OS: ${escapeHtml(caps.system?.os || 'Unknown')} ${escapeHtml(caps.system?.os_version || '')} |
                        Root: ${caps.system?.is_root ? 'Yes' : 'No'}
                    </div>
                    <h4>Available Detection Methods</h4>
                    <div class="capabilities-grid">
                        <div class="cap-detail-item ${wifiAvailable ? 'available' : 'unavailable'}">
                            <span class="cap-icon icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><circle cx="12" cy="20" r="1" fill="currentColor" stroke="none"/></svg></span>
                            <span class="cap-name">WiFi Scanning</span>
                            <span class="cap-status">${wifiAvailable ? caps.wifi.mode : 'Not Available'}</span>
                            ${caps.wifi?.interface ? `<span class="cap-detail">${escapeHtml(caps.wifi.interface)}</span>` : ''}
                        </div>
                        <div class="cap-detail-item ${btAvailable ? 'available' : 'unavailable'}">
                            <span class="cap-icon icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6.5 6.5 17.5 17.5 12 22 12 2 17.5 6.5 6.5 17.5"/></svg></span>
                            <span class="cap-name">Bluetooth Scanning</span>
                            <span class="cap-status">${btAvailable ? caps.bluetooth.mode : 'Not Available'}</span>
                            ${caps.bluetooth?.adapter ? `<span class="cap-detail">${escapeHtml(caps.bluetooth.adapter)}</span>` : ''}
                        </div>
                        <div class="cap-detail-item ${rfAvailable ? 'available' : 'unavailable'}">
                            <span class="cap-icon icon"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M2 12c0-3 2-6 5-6s4 3 5 6c1 3 2 6 5 6s5-3 5-6"/></svg></span>
                            <span class="cap-name">RF/SDR Scanning</span>
                            <span class="cap-status">${rfAvailable ? 'Available' : 'Not Available'}</span>
                            ${caps.rf?.device_type ? `<span class="cap-detail">${escapeHtml(caps.rf.device_type)}</span>` : ''}
                        </div>
                    </div>
                </div>
                <div class="device-detail-section">
                    <h4>What This Sweep CAN Detect</h4>
                    <ul class="cap-can-list">
                        ${canDetect.map(item => `<li>✅ ${escapeHtml(item)}</li>`).join('')}
                    </ul>
                </div>
                <div class="device-detail-section">
                    <h4>What This Sweep CANNOT Detect</h4>
                    <ul class="cap-cannot-list">
                        ${cannotDetect.map(item => `<li>❌ ${escapeHtml(item)}</li>`).join('')}
                    </ul>
                </div>
                ${caps.all_limitations && caps.all_limitations.length > 0 ? `
                <div class="device-detail-section">
                    <h4>Current Limitations</h4>
                    <ul class="cap-cannot-list">
                        ${caps.all_limitations.map(item => `<li>⚠️ ${escapeHtml(item)}</li>`).join('')}
                    </ul>
                </div>
                ` : ''}
                <div class="device-detail-disclaimer">
                    <strong>Important:</strong> ${escapeHtml(caps.disclaimer || 'This tool detects wireless RF emissions only. Professional TSCM requires physical inspection, NLJD, thermal imaging, and spectrum analysis equipment.')}
                </div>
            `;
        } else {
            content.innerHTML = `<div style="padding: 20px; color: var(--accent-red);">Failed to load capabilities: ${data.message || 'Unknown error'}</div>`;
        }
    } catch (e) {
        console.error('Failed to load capabilities:', e);
        content.innerHTML = '<div style="padding: 20px; color: var(--accent-red);">Failed to load capabilities</div>';
    }
}

async function tscmShowWifiIndicators() {
    const modal = document.getElementById('tscmDeviceModal');
    const content = document.getElementById('tscmDeviceModalContent');

    content.innerHTML = '<div class="ui-loading">Loading WiFi indicators…</div>';
    modal.style.display = 'flex';

    try {
        const response = await fetch('/tscm/wifi/advanced-indicators');
        const data = await response.json();

        if (data.status !== 'success') {
            content.innerHTML = `<div style="padding: 20px; color: var(--accent-red);">Failed to load indicators: ${escapeHtml(data.message || 'Unknown error')}</div>`;
            return;
        }

        const indicators = data.indicators || [];
        const unavailable = data.unavailable_features || [];
        const disclaimer = data.disclaimer || 'Indicators are heuristic signals, not confirmations.';

        content.innerHTML = `
            <div class="device-detail-header classification-yellow">
                <h3>WiFi Advanced Indicators (${indicators.length})</h3>
            </div>
            <div class="device-detail-section">
                ${indicators.length === 0 ? `
                    <div class="tscm-empty">No advanced indicators detected.</div>
                ` : `
                    <div class="indicator-list">
                        ${indicators.map(i => `
                            <div class="indicator-item">
                                <span class="indicator-type">${escapeHtml(i.type || 'indicator')}${i.severity ? ` • ${escapeHtml(i.severity)}` : ''}</span>
                                <span class="indicator-desc">${escapeHtml(i.description || '')}</span>
                            </div>
                        `).join('')}
                    </div>
                `}
            </div>
            ${unavailable.length > 0 ? `
                <div class="device-detail-section">
                    <h4>Unavailable Features</h4>
                    <ul class="device-reasons-list">
                        ${unavailable.map(u => `<li>${escapeHtml(u)}</li>`).join('')}
                    </ul>
                </div>
            ` : ''}
            <div class="device-detail-disclaimer">
                <strong>Note:</strong> ${escapeHtml(disclaimer)}
            </div>
        `;
    } catch (e) {
        console.error('Failed to load WiFi indicators:', e);
        content.innerHTML = '<div style="padding: 20px; color: var(--accent-red);">Failed to load WiFi indicators</div>';
    }
}

// Known Devices Management
async function tscmShowKnownDevices() {
    const modal = document.getElementById('tscmDeviceModal');
    const content = document.getElementById('tscmDeviceModalContent');

    content.innerHTML = '<div class="ui-loading">Loading known devices…</div>';
    modal.style.display = 'flex';

    try {
        const response = await fetch('/tscm/known-devices');
        const data = await response.json();

        const devices = data.devices || [];
        content.innerHTML = `
            <div class="device-detail-header classification-green">
                <h3>✅ Known/Approved Devices (${devices.length})</h3>
            </div>
            <div class="device-detail-section">
                <div style="margin-bottom: 12px;">
                    <button class="preset-btn" onclick="tscmAddKnownDevice()" style="font-size: 11px;">
                        + Add Device
                    </button>
                </div>
                ${devices.length === 0 ?
                '<p style="color: var(--text-muted);">No known devices registered. Devices you mark as "known" will be excluded from threat scoring.</p>' :
                `<div class="known-devices-list">
                        ${devices.map(d => `
                            <div class="known-device-item">
                                <div class="known-device-info">
                                    <strong>${escapeHtml(d.name || d.identifier)}</strong>
                                    <span class="known-device-id">${escapeHtml(d.identifier)}</span>
                                    <span class="known-device-type">${d.device_type}</span>
                                </div>
                                <div class="known-device-actions">
                                    <button class="preset-btn" onclick="tscmRemoveKnownDevice('${encodeURIComponent(d.identifier)}')" style="font-size: 10px; background: #ff4444;">
                                        Remove
                                    </button>
                                </div>
                            </div>
                        `).join('')}
                    </div>`
            }
            </div>
        `;
    } catch (e) {
        console.error('Failed to load known devices:', e);
        content.innerHTML = '<div style="padding: 20px; color: var(--accent-red);">Failed to load known devices</div>';
    }
}

async function tscmAddKnownDevice() {
    const identifier = prompt('Enter device identifier (MAC address, BSSID, or frequency):');
    if (!identifier) return;

    const name = prompt('Enter friendly name for this device:');
    const protocol = prompt('Enter protocol type (wifi/bluetooth/rf):') || 'wifi';

    try {
        const response = await fetch('/tscm/known-devices', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                identifier: identifier,
                protocol: protocol,
                name: name || identifier
            })
        });

        const data = await response.json();
        if (data.status === 'success') {
            tscmShowKnownDevices(); // Refresh list
        } else {
            alert(data.message || 'Failed to add device');
        }
    } catch (e) {
        console.error('Failed to add known device:', e);
        alert('Failed to add device');
    }
}

async function tscmRemoveKnownDevice(identifier) {
    const confirmed = await AppFeedback.confirmAction({
        title: 'Remove Known Device',
        message: 'Remove this device from known devices list?',
        confirmLabel: 'Remove',
        confirmClass: 'btn-danger'
    });
    if (!confirmed) return;

    try {
        const response = await fetch(`/tscm/known-devices/${identifier}`, {
            method: 'DELETE'
        });

        const data = await response.json();
        if (data.status === 'success') {
            tscmShowKnownDevices(); // Refresh list
        } else {
            alert(data.message || 'Failed to remove device');
        }
    } catch (e) {
        console.error('Failed to remove known device:', e);
    }
}

async function tscmAddToKnownDevices(identifier, name, protocol) {
    // Ask for optional custom name
    const customName = prompt(`Add "${name}" to known devices.\n\nEnter a friendly name (or leave blank to use default):`, name);
    if (customName === null) return; // User cancelled

    try {
        const response = await fetch('/tscm/known-devices', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                identifier: identifier,
                protocol: protocol,
                name: customName || name
            })
        });

        const data = await response.json();
        if (data.status === 'success') {
            // Show success message
            alert(`"${customName || name}" added to known devices.\n\nThis device will be excluded from threat scoring in future sweeps.`);
            // Close the device modal
            closeTscmDeviceModal();
        } else {
            alert(data.message || 'Failed to add device');
        }
    } catch (e) {
        console.error('Failed to add to known devices:', e);
        alert('Failed to add device to known list');
    }
}

// Case Linking Helpers
function tscmPromptLinkSweep(sweepId) {
    if (!sweepId) return;
    tscmCaseLinkContext = { type: 'sweep', id: sweepId };
    tscmShowCases();
}

function tscmPromptLinkThreat(threatId) {
    if (!threatId) return;
    tscmCaseLinkContext = { type: 'threat', id: threatId };
    tscmShowCases();
}

function tscmCancelCaseLink() {
    tscmCaseLinkContext = null;
    tscmShowCases();
}

async function tscmLinkCase(caseId) {
    if (!tscmCaseLinkContext || !caseId) return;
    const ctx = tscmCaseLinkContext;
    const endpoint = ctx.type === 'sweep'
        ? `/tscm/cases/${caseId}/sweeps/${ctx.id}`
        : `/tscm/cases/${caseId}/threats/${ctx.id}`;

    try {
        const response = await fetch(endpoint, { method: 'POST' });
        const data = await response.json();
        if (data.status === 'success') {
            tscmCaseLinkContext = null;
            tscmViewCase(caseId);
        } else {
            alert(data.message || 'Failed to link case');
        }
    } catch (e) {
        console.error('Failed to link case:', e);
        alert('Failed to link case');
    }
}

// Cases Management
async function tscmShowCases() {
    const modal = document.getElementById('tscmDeviceModal');
    const content = document.getElementById('tscmDeviceModalContent');

    content.innerHTML = '<div class="ui-loading">Loading cases…</div>';
    modal.style.display = 'flex';

    try {
        const response = await fetch('/tscm/cases');
        const data = await response.json();

        const cases = data.cases || [];
        const linkBanner = tscmCaseLinkContext ? `
            <div class="tscm-case-link-banner">
                <span>Linking ${tscmCaseLinkContext.type} ${tscmCaseLinkContext.id}. Select a case.</span>
                <button class="preset-btn" onclick="tscmCancelCaseLink()" style="font-size: 10px; padding: 4px 6px;">Cancel</button>
            </div>
        ` : '';
        content.innerHTML = `
            <div class="device-detail-header classification-cyan">
                <h3>TSCM Cases (${cases.length})</h3>
            </div>
            <div class="device-detail-section">
                ${linkBanner}
                <div style="margin-bottom: 12px;">
                    <button class="preset-btn" onclick="tscmCreateCase()" style="font-size: 11px;">
                        + New Case
                    </button>
                </div>
                ${cases.length === 0 ?
                '<p style="color: var(--text-muted);">No cases created. Cases help you organize sweeps and findings for specific locations or clients.</p>' :
                `<div class="cases-list">
                        ${cases.map(c => `
                            <div class="case-item" onclick="tscmViewCase(${c.id})">
                                <div class="case-header">
                                    <strong>${escapeHtml(c.name)}</strong>
                                    <span class="case-status ${c.status}">${c.status}</span>
                                </div>
                                <div class="case-meta">
                                    ${c.client_name ? `Client: ${escapeHtml(c.client_name)} | ` : ''}
                                    ${c.location ? `Location: ${escapeHtml(c.location)} | ` : ''}
                                    Sweeps: ${c.sweep_count || 0} | Threats: ${c.threat_count || 0}
                                </div>
                                <div class="case-date">
                                    Created: ${new Date(c.created_at).toLocaleDateString()}
                                </div>
                                ${tscmCaseLinkContext ? `
                                    <div class="case-actions">
                                        <button class="preset-btn" onclick="event.stopPropagation(); tscmLinkCase(${c.id})" style="font-size: 10px; padding: 4px 6px;">
                                            Link
                                        </button>
                                    </div>
                                ` : ''}
                            </div>
                        `).join('')}
                    </div>`
            }
            </div>
        `;
    } catch (e) {
        console.error('Failed to load cases:', e);
        content.innerHTML = '<div style="padding: 20px; color: var(--accent-red);">Failed to load cases</div>';
    }
}

async function tscmCreateCase() {
    const name = prompt('Enter case name:');
    if (!name) return;

    const clientName = prompt('Enter client name (optional):');
    const location = prompt('Enter location (optional):');

    try {
        const response = await fetch('/tscm/cases', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: name,
                client_name: clientName || null,
                location: location || null
            })
        });

        const data = await response.json();
        if (data.status === 'success') {
            tscmShowCases(); // Refresh list
        } else {
            alert(data.message || 'Failed to create case');
        }
    } catch (e) {
        console.error('Failed to create case:', e);
        alert('Failed to create case');
    }
}

async function tscmViewCase(caseId) {
    try {
        const response = await fetch(`/tscm/cases/${caseId}`);
        const data = await response.json();

        if (data.status === 'success') {
            const c = data.case;
            const content = document.getElementById('tscmDeviceModalContent');
            content.innerHTML = `
                <div class="device-detail-header classification-cyan">
                    <h3>${escapeHtml(c.name)}</h3>
                    <span class="case-status ${c.status}">${c.status}</span>
                </div>
                <div class="device-detail-section">
                    <h4>Case Details</h4>
                    <table class="device-detail-table">
                        <tr><td>Client</td><td>${escapeHtml(c.client_name || 'N/A')}</td></tr>
                        <tr><td>Location</td><td>${escapeHtml(c.location || 'N/A')}</td></tr>
                        <tr><td>Created</td><td>${new Date(c.created_at).toLocaleString()}</td></tr>
                        <tr><td>Status</td><td>${c.status}</td></tr>
                    </table>
                </div>
                <div class="device-detail-section">
                    <h4>Linked Sweeps (${(c.sweeps || []).length})</h4>
                    ${(c.sweeps || []).length === 0 ?
                    '<p style="color: var(--text-muted);">No sweeps linked to this case yet.</p>' :
                    `<ul>${(c.sweeps || []).map(s => `<li>Sweep ${s.id} - ${new Date(s.timestamp).toLocaleString()}</li>`).join('')}</ul>`
                }
                </div>
                <div class="device-detail-section">
                    <h4>Flagged Threats (${(c.threats || []).length})</h4>
                    ${(c.threats || []).length === 0 ?
                    '<p style="color: var(--text-muted);">No threats flagged in this case.</p>' :
                    `<ul>${(c.threats || []).map(t => `<li>${escapeHtml(t.identifier)} - ${t.threat_type}</li>`).join('')}</ul>`
                }
                </div>
                <div class="device-detail-section">
                    <h4>Case Notes (${(c.case_notes || []).length})</h4>
                    ${(c.case_notes || []).length === 0
                        ? '<div class="tscm-empty">No notes added yet.</div>'
                        : `<div class="tscm-case-notes">
                            ${(c.case_notes || []).map(n => `
                                <div class="tscm-case-note">
                                    <div class="tscm-case-note-meta">
                                        <span class="tscm-case-note-type">${escapeHtml(n.note_type || 'general')}</span>
                                        <span>${n.created_at ? new Date(n.created_at).toLocaleString() : ''}</span>
                                    </div>
                                    <div class="tscm-case-note-content">${escapeHtml(n.content || '')}</div>
                                    ${n.created_by ? `<div class="tscm-case-note-author">By ${escapeHtml(n.created_by)}</div>` : ''}
                                </div>
                            `).join('')}
                        </div>`
                    }
                    <div class="tscm-case-note-form">
                        <label>Note Type</label>
                        <select id="tscmCaseNoteType">
                            <option value="general" selected>General</option>
                            <option value="observation">Observation</option>
                            <option value="action">Action</option>
                            <option value="follow_up">Follow-up</option>
                        </select>
                        <label>Add Note</label>
                        <textarea id="tscmCaseNoteInput" rows="4" placeholder="Add a note to this case..."></textarea>
                        <button class="preset-btn" onclick="tscmAddCaseNote(${c.id})" style="margin-top: 6px; font-size: 10px;">Add Note</button>
                    </div>
                </div>
                <div style="margin-top: 16px;">
                    <button class="preset-btn" onclick="tscmShowCases()">← Back to Cases</button>
                </div>
            `;
        }
    } catch (e) {
        console.error('Failed to view case:', e);
    }
}

async function tscmAddCaseNote(caseId) {
    const noteInput = document.getElementById('tscmCaseNoteInput');
    const typeSelect = document.getElementById('tscmCaseNoteType');
    if (!noteInput || !typeSelect) return;

    const content = noteInput.value.trim();
    const noteType = typeSelect.value;

    const ok = await tscmSubmitCaseNote(caseId, content, noteType);
    if (ok) {
        tscmViewCase(caseId);
    }
}

// Schedules Management
async function tscmShowSchedules() {
    const modal = document.getElementById('tscmDeviceModal');
    const content = document.getElementById('tscmDeviceModalContent');

    content.innerHTML = '<div class="ui-loading">Loading schedules…</div>';
    modal.style.display = 'flex';

    try {
        const [scheduleRes, baselineRes] = await Promise.all([
            fetch('/tscm/schedules'),
            fetch('/tscm/baselines')
        ]);

        const scheduleData = await scheduleRes.json();
        const baselineData = await baselineRes.json();

        const schedules = scheduleData.schedules || [];
        const baselines = baselineData.baselines || [];
        const baselineMap = {};
        baselines.forEach(b => { baselineMap[String(b.id)] = b.name; });

        const baselineOptions = [
            '<option value="">No Baseline</option>',
            ...baselines.map(b => `<option value="${b.id}">${escapeHtml(b.name)}</option>`)
        ].join('');

        content.innerHTML = `
            <div class="device-detail-header classification-cyan">
                <h3>TSCM Schedules (${schedules.length})</h3>
            </div>
            <div class="device-detail-section">
                <div class="tscm-schedule-form">
                    <div class="form-group">
                        <label>Name</label>
                        <input type="text" id="tscmScheduleName" placeholder="Daily sweep">
                    </div>
                    <div class="form-group">
                        <label>Sweep Type</label>
                        <select id="tscmScheduleSweepType">
                            <option value="quick">Quick Scan (2 min)</option>
                            <option value="standard" selected>Standard (5 min)</option>
                            <option value="full">Full Sweep (15 min)</option>
                            <option value="wireless_cameras">Wireless Cameras</option>
                            <option value="body_worn">Body-Worn Devices</option>
                            <option value="gps_trackers">GPS Trackers</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Baseline</label>
                        <select id="tscmScheduleBaseline">
                            ${baselineOptions}
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Cadence</label>
                        <select id="tscmScheduleCadence" onchange="tscmScheduleCadenceChanged()">
                            <option value="daily" selected>Daily</option>
                            <option value="weekly">Weekly</option>
                            <option value="hourly">Every N Hours</option>
                        </select>
                    </div>
                    <div class="form-group" id="tscmScheduleTimeRow">
                        <label>Time</label>
                        <input type="time" id="tscmScheduleTime" value="09:00">
                    </div>
                    <div class="form-group" id="tscmScheduleDayRow" style="display: none;">
                        <label>Day of Week</label>
                        <select id="tscmScheduleDay">
                            <option value="0">Sunday</option>
                            <option value="1">Monday</option>
                            <option value="2">Tuesday</option>
                            <option value="3">Wednesday</option>
                            <option value="4">Thursday</option>
                            <option value="5">Friday</option>
                            <option value="6">Saturday</option>
                        </select>
                    </div>
                    <div class="form-group" id="tscmScheduleIntervalRow" style="display: none;">
                        <label>Interval (hours)</label>
                        <input type="number" id="tscmScheduleInterval" min="1" max="24" value="6">
                    </div>
                    <button class="preset-btn" onclick="tscmCreateScheduleFromForm()" style="margin-top: 6px; font-size: 11px;">
                        Create Schedule
                    </button>
                </div>
            </div>
            <div class="device-detail-section">
                <h4>Existing Schedules</h4>
                ${schedules.length === 0
                ? '<div class="tscm-empty">No schedules created.</div>'
                : `<div class="tscm-schedule-list">
                    ${schedules.map(s => {
                        const isEnabled = !!s.enabled;
                        const baselineName = baselineMap[String(s.baseline_id)] || 'None';
                        const nextRun = s.next_run ? new Date(s.next_run).toLocaleString() : 'Not scheduled';
                        const lastRun = s.last_run ? new Date(s.last_run).toLocaleString() : 'Never';
                        return `
                            <div class="tscm-schedule-item ${isEnabled ? 'enabled' : 'disabled'}">
                                <div class="tscm-schedule-header">
                                    <strong>${escapeHtml(s.name)}</strong>
                                    <span class="tscm-schedule-status">${isEnabled ? 'Enabled' : 'Disabled'}</span>
                                </div>
                                <div class="tscm-schedule-meta">
                                    Sweep: ${escapeHtml(s.sweep_type || 'standard')} | Baseline: ${escapeHtml(baselineName)}
                                </div>
                                <div class="tscm-schedule-meta">
                                    Cron: ${escapeHtml(s.cron_expression || '')}
                                </div>
                                <div class="tscm-schedule-meta">
                                    Next: ${nextRun} | Last: ${lastRun}
                                </div>
                                <div class="tscm-schedule-actions">
                                    <button class="preset-btn" onclick="tscmRunScheduleNow(${s.id})" style="font-size: 10px; padding: 4px 6px;">
                                        Run Now
                                    </button>
                                    <button class="preset-btn" onclick="tscmToggleSchedule(${s.id}, ${isEnabled ? 'false' : 'true'})" style="font-size: 10px; padding: 4px 6px;">
                                        ${isEnabled ? 'Disable' : 'Enable'}
                                    </button>
                                    <button class="preset-btn" onclick="tscmDeleteSchedule(${s.id})" style="font-size: 10px; padding: 4px 6px;">
                                        Delete
                                    </button>
                                </div>
                            </div>
                        `;
                    }).join('')}
                </div>`
                }
            </div>
        `;
        tscmScheduleCadenceChanged();
    } catch (e) {
        console.error('Failed to load schedules:', e);
        content.innerHTML = '<div style="padding: 20px; color: var(--accent-red);">Failed to load schedules</div>';
    }
}

function tscmScheduleCadenceChanged() {
    const cadence = document.getElementById('tscmScheduleCadence')?.value || 'daily';
    const timeRow = document.getElementById('tscmScheduleTimeRow');
    const dayRow = document.getElementById('tscmScheduleDayRow');
    const intervalRow = document.getElementById('tscmScheduleIntervalRow');

    if (timeRow) timeRow.style.display = cadence === 'hourly' ? 'none' : 'block';
    if (dayRow) dayRow.style.display = cadence === 'weekly' ? 'block' : 'none';
    if (intervalRow) intervalRow.style.display = cadence === 'hourly' ? 'block' : 'none';
}

async function tscmCreateScheduleFromForm() {
    const name = document.getElementById('tscmScheduleName')?.value.trim();
    if (!name) {
        alert('Schedule name required');
        return;
    }
    const sweepType = document.getElementById('tscmScheduleSweepType')?.value || 'standard';
    const baselineId = document.getElementById('tscmScheduleBaseline')?.value || null;
    const cadence = document.getElementById('tscmScheduleCadence')?.value || 'daily';

    let cronExpression = '';
    if (cadence === 'hourly') {
        const interval = parseInt(document.getElementById('tscmScheduleInterval')?.value || '6', 10);
        if (isNaN(interval) || interval < 1 || interval > 24) {
            alert('Interval must be between 1 and 24 hours');
            return;
        }
        cronExpression = `0 */${interval} * * *`;
    } else {
        const timeValue = document.getElementById('tscmScheduleTime')?.value || '09:00';
        const [hourStr, minStr] = timeValue.split(':');
        const hour = parseInt(hourStr, 10);
        const minute = parseInt(minStr, 10);
        if (isNaN(hour) || isNaN(minute)) {
            alert('Invalid time');
            return;
        }
        if (cadence === 'weekly') {
            const day = document.getElementById('tscmScheduleDay')?.value || '0';
            cronExpression = `${minute} ${hour} * * ${day}`;
        } else {
            cronExpression = `${minute} ${hour} * * *`;
        }
    }

    const zoneName = Intl.DateTimeFormat().resolvedOptions().timeZone || null;

    try {
        const response = await fetch('/tscm/schedules', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name,
                sweep_type: sweepType,
                baseline_id: baselineId ? Number(baselineId) : null,
                cron_expression: cronExpression,
                zone_name: zoneName
            })
        });

        const data = await response.json();
        if (data.status === 'success') {
            tscmShowSchedules();
        } else {
            alert(data.message || 'Failed to create schedule');
        }
    } catch (e) {
        console.error('Failed to create schedule:', e);
        alert('Failed to create schedule');
    }
}

async function tscmToggleSchedule(scheduleId, enabled) {
    try {
        const response = await fetch(`/tscm/schedules/${scheduleId}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled })
        });
        const data = await response.json();
        if (data.status === 'success') {
            tscmShowSchedules();
        } else {
            alert(data.message || 'Failed to update schedule');
        }
    } catch (e) {
        console.error('Failed to update schedule:', e);
        alert('Failed to update schedule');
    }
}

async function tscmRunScheduleNow(scheduleId) {
    try {
        const response = await fetch(`/tscm/schedules/${scheduleId}/run`, { method: 'POST' });
        const data = await response.json();
        if (data.status === 'success') {
            alert('Scheduled sweep started');
            tscmShowSchedules();
        } else {
            alert(data.message || 'Failed to run schedule');
        }
    } catch (e) {
        console.error('Failed to run schedule:', e);
        alert('Failed to run schedule');
    }
}

async function tscmDeleteSchedule(scheduleId) {
    const confirmed = await AppFeedback.confirmAction({
        title: 'Delete Schedule',
        message: 'Delete this schedule?',
        confirmLabel: 'Delete',
        confirmClass: 'btn-danger'
    });
    if (!confirmed) return;
    try {
        const response = await fetch(`/tscm/schedules/${scheduleId}`, { method: 'DELETE' });
        const data = await response.json();
        if (data.status === 'success') {
            tscmShowSchedules();
        } else {
            alert(data.message || 'Failed to delete schedule');
        }
    } catch (e) {
        console.error('Failed to delete schedule:', e);
        alert('Failed to delete schedule');
    }
}

// Playbooks Display
async function tscmShowPlaybooks() {
    const modal = document.getElementById('tscmDeviceModal');
    const content = document.getElementById('tscmDeviceModalContent');

    content.innerHTML = '<div class="ui-loading">Loading playbooks…</div>';
    modal.style.display = 'flex';

    try {
        const response = await fetch('/tscm/playbooks');
        const data = await response.json();

        const playbooks = data.playbooks || [];
        content.innerHTML = `
            <div class="device-detail-header classification-orange">
                <h3>Operator Playbooks</h3>
            </div>
            <div class="device-detail-section">
                <p style="color: var(--text-muted); margin-bottom: 16px;">
                    Playbooks provide step-by-step guidance for investigating specific types of findings.
                </p>
                <div class="playbooks-list">
                    ${playbooks.map(p => `
                        <div class="playbook-item" onclick="tscmViewPlaybook('${p.id}')">
                            <div class="playbook-header">
                                <strong>${escapeHtml(p.name || p.title)}</strong>
                                <span class="playbook-category">${escapeHtml(p.risk_level || p.category || 'General')}</span>
                            </div>
                            <div class="playbook-desc">
                                ${escapeHtml(p.description || 'No description')}
                            </div>
                            <div class="playbook-meta">
                                ${p.steps?.length || 0} steps
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    } catch (e) {
        console.error('Failed to load playbooks:', e);
        content.innerHTML = '<div style="padding: 20px; color: var(--accent-red);">Failed to load playbooks</div>';
    }
}

async function tscmViewPlaybook(playbookId) {
    try {
        const response = await fetch(`/tscm/playbooks/${playbookId}`);
        const data = await response.json();

        if (data.status === 'success') {
            const p = data.playbook;
            const content = document.getElementById('tscmDeviceModalContent');
            content.innerHTML = renderPlaybook(p);
        }
    } catch (e) {
        console.error('Failed to view playbook:', e);
    }
}

function renderPlaybook(p) {
    const riskColors = { 'critical': '#ff3366', 'high': '#ff6633', 'medium': '#ff9800', 'low': '#4caf50' };
    const riskColor = riskColors[(p.risk_level || '').toLowerCase()] || '#ff9800';
    return `
        <div class="device-detail-header" style="border-left: 4px solid ${riskColor};">
            <h3>${escapeHtml(p.title || p.name || 'Playbook')}</h3>
            <span style="font-size: 10px; background: ${riskColor}; color: #000; padding: 2px 8px; border-radius: 3px; font-weight: bold; text-transform: uppercase;">${escapeHtml(p.risk_level || 'MEDIUM')}</span>
        </div>
        <div class="device-detail-section">
            <p style="color: var(--text-muted);">${escapeHtml(p.description || '')}</p>
        </div>
        ${p.when_to_escalate ? `
        <div style="margin: 12px 0; padding: 10px 14px; background: rgba(255,51,102,0.1); border: 1px solid rgba(255,51,102,0.4); border-radius: 6px;">
            <strong style="color: var(--accent-red); font-size: 11px; text-transform: uppercase;">Escalation Trigger</strong>
            <p style="margin: 4px 0 0; font-size: 12px; color: var(--accent-red);">${escapeHtml(p.when_to_escalate)}</p>
        </div>
        ` : ''}
        <div class="device-detail-section">
            <h4 style="margin-bottom: 10px;">Investigation Steps</h4>
            <div class="playbook-checklist">
                ${(p.steps || []).map((step, i) => {
                    const stepNum = step.step_number || step.step || (i + 1);
                    return `
                    <div class="playbook-check-step" id="pbStep${i}" style="display: flex; gap: 10px; padding: 10px; margin-bottom: 8px; background: var(--surface-sunken-soft); border: 1px solid var(--border-color); border-radius: 6px; cursor: pointer; transition: border-color 0.3s;" onclick="togglePlaybookStep(${i})">
                        <div style="flex-shrink: 0; display: flex; align-items: flex-start; padding-top: 2px;">
                            <input type="checkbox" id="pbCheck${i}" style="width: 16px; height: 16px; accent-color: #00e676; cursor: pointer;" onclick="event.stopPropagation(); togglePlaybookStep(${i})">
                        </div>
                        <div style="flex: 1;">
                            <div style="display: flex; align-items: center; gap: 6px; margin-bottom: 4px;">
                                <span style="font-size: 10px; color: var(--accent-cyan); font-weight: bold;">STEP ${stepNum}</span>
                                <strong style="font-size: 12px;">${escapeHtml(step.action || step.title || '')}</strong>
                            </div>
                            <p style="font-size: 11px; color: var(--text-muted); margin: 0;">${escapeHtml(step.details || step.description || '')}</p>
                            ${step.safety_note ? `<div style="margin-top: 6px; padding: 6px 8px; background: rgba(255,152,0,0.1); border-left: 3px solid #ff9800; border-radius: 3px; font-size: 10px; color: var(--accent-amber);"><strong>Safety:</strong> ${escapeHtml(step.safety_note)}</div>` : ''}
                            ${step.evidence_needed && step.evidence_needed.length > 0 ? `<div style="margin-top: 6px; font-size: 10px; color: var(--text-muted);"><strong>Evidence needed:</strong> ${step.evidence_needed.map(e => escapeHtml(e)).join(', ')}</div>` : ''}
                        </div>
                    </div>`;
                }).join('')}
            </div>
        </div>
        ${p.documentation_required && p.documentation_required.length > 0 ? `
        <div class="device-detail-section">
            <h4>Documentation Required</h4>
            <ul style="list-style: none; padding: 0;">
                ${p.documentation_required.map(d => `<li style="padding: 4px 0; font-size: 11px; color: var(--text-secondary);">&#9744; ${escapeHtml(d)}</li>`).join('')}
            </ul>
        </div>
        ` : ''}
        ${p.disclaimer ? `
        <div class="device-detail-disclaimer">
            <strong>Disclaimer:</strong> ${escapeHtml(p.disclaimer)}
        </div>
        ` : ''}
        <div style="margin-top: 16px;">
            <button class="preset-btn" onclick="tscmShowPlaybooks()">&#8592; Back to Playbooks</button>
        </div>
    `;
}

function togglePlaybookStep(index) {
    const checkbox = document.getElementById('pbCheck' + index);
    const stepEl = document.getElementById('pbStep' + index);
    if (!checkbox || !stepEl) return;
    // Toggle if triggered from the row (not the checkbox itself)
    if (document.activeElement !== checkbox) {
        checkbox.checked = !checkbox.checked;
    }
    if (checkbox.checked) {
        stepEl.style.borderColor = '#00e676';
        stepEl.style.background = 'rgba(0, 230, 118, 0.05)';
    } else {
        stepEl.style.borderColor = 'var(--border-color)';
        stepEl.style.background = 'rgba(0,0,0,0.2)';
    }
}

async function fetchDevicePlaybook(identifier) {
    try {
        const response = await fetch(`/tscm/findings/${encodeURIComponent(identifier)}/playbook`);
        const data = await response.json();
        if (data.status === 'success' && data.playbook) {
            return data.playbook;
        }
    } catch (e) {
        console.error('Failed to fetch device playbook:', e);
    }
    return null;
}

// Report Downloads
function _tscmCategoryParam() {
    const cats = [];
    if (document.getElementById('tscmCatHighInterest')?.checked) cats.push('high_interest');
    if (document.getElementById('tscmCatNeedsReview')?.checked) cats.push('needs_review');
    if (document.getElementById('tscmCatInformational')?.checked) cats.push('informational');
    return cats.length < 3 ? `categories=${cats.join(',')}` : '';
}

function _tscmMetaParams() {
    const params = [];
    const site = document.getElementById('tscmSiteName')?.value?.trim();
    const examiner = document.getElementById('tscmExaminerName')?.value?.trim();
    if (site) params.push(`site_name=${encodeURIComponent(site)}`);
    if (examiner) params.push(`examiner_name=${encodeURIComponent(examiner)}`);
    return params.join('&');
}

// The client report opens as a printable page; the browser's Print,
// with "Save as PDF", makes the PDF.
function tscmDownloadPdf() {
    const parts = [_tscmCategoryParam(), _tscmMetaParams()].filter(Boolean);
    window.open(`/tscm/report/print${parts.length ? '?' + parts.join('&') : ''}`, '_blank', 'noopener');
}

async function tscmDownloadAnnex(format) {
    try {
        const parts = [_tscmCategoryParam(), _tscmMetaParams()].filter(Boolean);
        const response = await fetch(`/tscm/report/annex?format=${format}${parts.length ? '&' + parts.join('&') : ''}`);
        if (response.ok) {
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `TSCM_Annex_${new Date().toISOString().split('T')[0]}.${format}`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        } else {
            const data = await response.json();
            alert(data.message || 'Failed to generate annex');
        }
    } catch (e) {
        console.error('Failed to download annex:', e);
        alert('Failed to download technical annex');
    }
}

// Update capabilities bar on sweep start
async function updateTscmCapabilitiesBar(wifiInterface = '', btInterface = '') {
    try {
        const params = new URLSearchParams();
        if (wifiInterface) params.append('wifi_interface', wifiInterface);
        if (btInterface) params.append('bt_adapter', btInterface);
        const query = params.toString();
        const response = await fetch(`/tscm/capabilities${query ? `?${query}` : ''}`);
        const data = await response.json();

        if (data.status === 'success') {
            const caps = data.capabilities;
            const bar = document.getElementById('tscmCapabilitiesBar');

            if (bar) {
                const wifiAvailable = caps.wifi && caps.wifi.mode && caps.wifi.mode !== 'unavailable';
                const btAvailable = caps.bluetooth && caps.bluetooth.mode && caps.bluetooth.mode !== 'unavailable';
                const rfAvailable = caps.rf && caps.rf.available;
                const isRoot = caps.system && caps.system.is_root;

                const normalizeMode = (mode) => mode ? mode.replace(/_/g, ' ').toUpperCase() : 'ON';

                document.getElementById('capWifiStatus').textContent = wifiAvailable ? normalizeMode(caps.wifi.mode) : 'OFF';
                document.getElementById('capWifi').classList.toggle('active', wifiAvailable);

                document.getElementById('capBtStatus').textContent = btAvailable ? normalizeMode(caps.bluetooth.mode) : 'OFF';
                document.getElementById('capBt').classList.toggle('active', btAvailable);

                document.getElementById('capRfStatus').textContent = rfAvailable ? 'ON' : 'OFF';
                document.getElementById('capRf').classList.toggle('active', rfAvailable);

                document.getElementById('capRootStatus').textContent = isRoot ? 'ROOT' : 'USER';
                document.getElementById('capRoot').classList.toggle('active', isRoot);

                const limitationCount = (caps.all_limitations || []).length;
                document.getElementById('capLimitationCount').textContent = limitationCount;

                bar.style.display = 'flex';
            }
        }
    } catch (e) {
        console.error('Failed to update capabilities bar:', e);
    }
}

// Update baseline health indicator
async function updateTscmBaselineHealth(baselineId) {
    if (!baselineId) {
        const healthDiv = document.getElementById('tscmBaselineHealth');
        if (healthDiv) healthDiv.style.display = 'none';
        return;
    }

    try {
        const response = await fetch(`/tscm/baseline/${baselineId}/health`);
        const data = await response.json();

        if (data.status === 'success') {
            const healthDiv = document.getElementById('tscmBaselineHealth');
            const badge = document.getElementById('baselineHealthBadge');
            const nameEl = document.getElementById('baselineHealthName');
            const ageEl = document.getElementById('baselineHealthAge');

            if (healthDiv && badge) {
                const health = data.health || {};
                const status = (health.status || 'unknown').toLowerCase();
                const displayStatus = status.replace(/_/g, ' ');

                badge.textContent = displayStatus.toUpperCase();
                badge.className = `health-badge health-${status}`;
                if (health.reasons && health.reasons.length > 0) {
                    badge.title = health.reasons.join(' • ');
                }

                if (nameEl) {
                    const baselineSelect = document.getElementById('tscmBaselineSelect');
                    const selectedOption = baselineSelect ? baselineSelect.options[baselineSelect.selectedIndex] : null;
                    const selectedName = selectedOption ? selectedOption.textContent.replace(' (Active)', '') : 'Baseline';
                    nameEl.textContent = selectedName || 'Baseline';
                }
                if (ageEl) {
                    const ageHours = health.age_hours;
                    if (ageHours !== undefined && ageHours !== null) {
                        const ageLabel = ageHours >= 48
                            ? `${(ageHours / 24).toFixed(1)}d`
                            : `${Math.round(ageHours)}h`;
                        ageEl.textContent = `• ${ageLabel} old`;
                    } else {
                        ageEl.textContent = '';
                    }
                }
                healthDiv.style.display = 'block';
            }
        }
    } catch (e) {
        console.error('Failed to update baseline health:', e);
    }
}

// Listen for baseline selection changes
document.addEventListener('DOMContentLoaded', function () {
    const baselineSelect = document.getElementById('tscmBaselineSelect');
    if (baselineSelect) {
        baselineSelect.addEventListener('change', function () {
            updateTscmBaselineHealth(this.value);
        });
    }

    const filterIds = ['tscmFilterProtocol', 'tscmFilterRisk', 'tscmFilterStatus', 'tscmFilterKnown'];
    filterIds.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('change', applyTscmFilters);
        }
    });
    applyTscmFilters();
});
