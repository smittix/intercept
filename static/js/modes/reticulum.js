/**
 * Reticulum Mode
 * LXMF messaging over Reticulum: start/stop, announce, nodes heard,
 * conversations and a live message feed (SSE from /reticulum/stream).
 */
const Reticulum = (function () {

    // ── State ──────────────────────────────────────────────────────────────
    let _mode = 'shared';
    let _eventSource = null;
    let _statusTimer = null;
    let _running = false;
    let _address = '';
    let _nodes = {};          // dest_hash -> node
    let _nodeFilter = '';
    let _peer = null;         // selected conversation (dest hash) or null for all
    let _msgCount = 0;

    // ── Init / Destroy ─────────────────────────────────────────────────────
    async function init() {
        await _loadConfig();
        await _checkStatus();
        _loadNodes();
        _loadConversations();
        _loadMessages();
        _startSSE();
        _statusTimer = setInterval(_checkStatus, 5000);
        const header = document.getElementById('rnsConversationHeader');
        if (header) header.addEventListener('click', () => selectPeer(null));
    }

    function destroy() {
        if (_eventSource) { _eventSource.close(); _eventSource = null; }
        if (_statusTimer) { clearInterval(_statusTimer); _statusTimer = null; }
    }

    async function _json(url, opts) {
        const r = await fetch(url, opts);
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.message || d.error || ('HTTP ' + r.status));
        return d;
    }

    function _post(url, body) {
        return _json(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
    }

    // ── Config / status ────────────────────────────────────────────────────
    async function _loadConfig() {
        try {
            const c = await _json('/reticulum/config');
            selectMode(c.mode || 'shared');
            _val('rnsTcpHost', c.host || '');
            _val('rnsTcpPort', c.port || 4242);
            _val('rnsDisplayName', c.display_name || '');
        } catch (e) { /* defaults stay */ }
    }

    async function _checkStatus() {
        try {
            _updateStatusUI(await _json('/reticulum/status'));
        } catch (e) { /* ignore transient */ }
    }

    function _updateStatusUI(s) {
        const dot = document.getElementById('rnsStatusDot');
        const txt = document.getElementById('rnsStatusText');
        if (!dot) return;
        const state = s.state || 'stopped';
        dot.className = 'rns-status-dot ' + state;
        const labels = { online: 'Online', waiting: 'Waiting for link', starting: 'Starting…', stopped: 'Stopped', error: 'Error', unavailable: 'Not installed' };
        txt.textContent = s.message || labels[state] || state;
        txt.title = txt.textContent;

        _running = !!s.running;
        const busy = _running || state === 'starting' || state === 'waiting';
        _setDisabled('rnsStartBtn', busy || state === 'unavailable');
        _setDisabled('rnsStopBtn', !busy);
        _setDisabled('rnsAnnounceBtn', !_running);
        document.querySelectorAll('.rns-transport-tab').forEach(el => { el.disabled = busy; });

        if (s.address) {
            _address = s.address;
            const a = document.getElementById('rnsAddress');
            if (a) a.textContent = s.address;
        }
    }

    function selectMode(mode) {
        _mode = mode === 'tcp' ? 'tcp' : 'shared';
        document.querySelectorAll('.rns-transport-tab').forEach(el => {
            el.classList.toggle('active', el.dataset.mode === _mode);
        });
        const tcp = document.getElementById('rnsTcpConfig');
        if (tcp) tcp.style.display = _mode === 'tcp' ? '' : 'none';
    }

    // ── Lifecycle ──────────────────────────────────────────────────────────
    async function start() {
        const body = {
            mode: _mode,
            display_name: _get('rnsDisplayName') || 'INTERCEPT',
        };
        if (_mode === 'tcp') {
            body.host = _get('rnsTcpHost');
            body.port = parseInt(_get('rnsTcpPort') || '4242', 10);
        }
        try {
            _updateStatusUI({ state: 'starting' });
            await _post('/reticulum/start', body);
        } catch (e) {
            _updateStatusUI({ state: 'error', message: e.message });
            return;
        }
        _checkStatus();
    }

    async function stop() {
        try { await _post('/reticulum/stop'); } catch (e) { console.error('Stop failed:', e); }
        _checkStatus();
    }

    async function announce() {
        try {
            await _post('/reticulum/announce');
            _flash('rnsAnnounceBtn', 'Announced');
        } catch (e) { alert(e.message); }
    }

    function copyAddress() {
        if (!_address || !navigator.clipboard) return;
        navigator.clipboard.writeText(_address).then(() => _flash('rnsAddress', 'copied'), () => {});
    }

    // ── SSE ────────────────────────────────────────────────────────────────
    function _startSSE() {
        if (_eventSource) _eventSource.close();
        _eventSource = new EventSource('/reticulum/stream');
        _eventSource.onmessage = (e) => {
            let event;
            try { event = JSON.parse(e.data); } catch (err) { return; }
            switch (event.type) {
                case 'status': _updateStatusUI(event.data); break;
                case 'node': _upsertNode(event.data); break;
                case 'message': _onMessage(event.data); break;
                case 'message_state': _setMessageState(event.data.msg_hash, event.data.state); break;
            }
        };
    }

    // ── Nodes ──────────────────────────────────────────────────────────────
    async function _loadNodes() {
        try {
            const d = await _json('/reticulum/nodes');
            (d.nodes || []).slice().reverse().forEach(_upsertNode);
        } catch (e) { /* ignore */ }
    }

    function _nodeLabel(hash) {
        const n = _nodes[hash];
        return (n && n.name) || ('<' + hash.slice(0, 10) + '…>');
    }

    function _upsertNode(node) {
        const list = document.getElementById('rnsNodeList');
        if (!list) return;
        _nodes[node.dest_hash] = Object.assign({}, _nodes[node.dest_hash], node, { name: node.name || (_nodes[node.dest_hash] || {}).name });
        let el = document.getElementById('rns-node-' + node.dest_hash);
        if (!el) {
            el = document.createElement('div');
            el.className = 'rns-item';
            el.id = 'rns-node-' + node.dest_hash;
            el.title = 'Message this node';
            el.addEventListener('click', () => selectPeer(node.dest_hash));
            const empty = list.querySelector('.rns-empty');
            if (empty) empty.remove();
        }
        list.prepend(el);  // most recently heard first
        const n = _nodes[node.dest_hash];
        const hops = n.hops === null || n.hops === undefined ? '?' : n.hops;
        const heard = n.last_heard ? new Date(n.last_heard * 1000).toLocaleTimeString() : '';
        el.innerHTML = `
            <div class="rns-item-name">${_esc(n.name || '(no name)')}</div>
            <div class="rns-item-meta">${_esc(n.dest_hash)}</div>
            <div class="rns-item-meta">${hops} hops · ${_esc(heard)}</div>`;
        el.hidden = !_nodeMatches(n);
        _setText('rnsNodeCount', Object.keys(_nodes).length);
    }

    function _nodeMatches(n) {
        return !_nodeFilter || `${n.name || ''} ${n.dest_hash}`.toLowerCase().includes(_nodeFilter);
    }

    function filterNodes(query) {
        _nodeFilter = String(query || '').trim().toLowerCase();
        Object.values(_nodes).forEach(n => {
            const el = document.getElementById('rns-node-' + n.dest_hash);
            if (el) el.hidden = !_nodeMatches(n);
        });
    }

    // ── Conversations ──────────────────────────────────────────────────────
    async function _loadConversations() {
        const list = document.getElementById('rnsConversationList');
        if (!list) return;
        let convs = [];
        try { convs = (await _json('/reticulum/conversations')).conversations || []; } catch (e) { return; }
        if (!convs.length) { list.innerHTML = '<div class="rns-empty">No conversations</div>'; return; }
        list.innerHTML = '';
        convs.forEach(c => {
            const el = document.createElement('div');
            el.className = 'rns-item' + (c.peer_hash === _peer ? ' active' : '');
            el.addEventListener('click', () => selectPeer(c.peer_hash));
            el.innerHTML = `
                <div class="rns-item-name">${_esc(c.name || _nodeLabel(c.peer_hash))} <span class="rns-item-meta">(${c.count})</span></div>
                <div class="rns-item-meta">${_esc(c.last_content || '')}</div>`;
            list.appendChild(el);
        });
    }

    function selectPeer(hash) {
        _peer = hash;
        _setText('rnsConversationHeader', hash ? `${_nodeLabel(hash)} · ${hash}  (click for all)` : 'All messages');
        if (hash) _val('rnsDestination', hash);
        _loadMessages();
        _loadConversations();
        const input = document.getElementById('rnsComposeInput');
        if (hash && input) input.focus();
    }

    // ── Messages ───────────────────────────────────────────────────────────
    async function _loadMessages() {
        const feed = document.getElementById('rnsMessageFeed');
        if (!feed) return;
        let msgs = [];
        try {
            const url = '/reticulum/messages' + (_peer ? '?peer=' + encodeURIComponent(_peer) : '');
            msgs = (await _json(url)).messages || [];
        } catch (e) { return; }
        feed.innerHTML = '';
        if (!msgs.length) feed.innerHTML = '<div class="rns-empty rns-feed-placeholder">No messages yet</div>';
        msgs.forEach(_appendMessage);
        if (!_peer) { _msgCount = msgs.length; _setText('rnsMsgCount', _msgCount); }
    }

    function _onMessage(msg) {
        _msgCount++;
        _setText('rnsMsgCount', _msgCount);
        if (!_peer || msg.peer_hash === _peer) _appendMessage(msg);
        _loadConversations();
    }

    function _appendMessage(msg) {
        const feed = document.getElementById('rnsMessageFeed');
        if (!feed) return;
        const placeholder = feed.querySelector('.rns-feed-placeholder');
        if (placeholder) placeholder.remove();
        if (msg.msg_hash && document.getElementById('rns-msg-' + msg.msg_hash)) return;
        const out = msg.direction === 'out';
        const el = document.createElement('div');
        el.className = 'rns-message' + (out ? ' out' : '') + (msg.state === 'failed' ? ' failed' : '');
        if (msg.msg_hash) el.id = 'rns-msg-' + msg.msg_hash;
        const ts = msg.timestamp ? new Date(msg.timestamp * 1000).toLocaleString() : '';
        const who = out ? `Me → ${_nodeLabel(msg.peer_hash)}` : _nodeLabel(msg.peer_hash);
        el.innerHTML = `
            <div class="rns-message-header">
                <span class="rns-message-sender" title="${_esc(msg.peer_hash)}">${_esc(who)}</span>
                <span>${_esc(ts)}${out ? ' · <span class="rns-message-state">' + _esc(msg.state || '') + '</span>' : ''}</span>
            </div>
            ${msg.title ? `<div class="rns-message-text"><strong>${_esc(msg.title)}</strong></div>` : ''}
            <div class="rns-message-text">${_esc(msg.content)}</div>`;
        feed.appendChild(el);
        feed.scrollTop = feed.scrollHeight;
    }

    function _setMessageState(hash, state) {
        const el = document.getElementById('rns-msg-' + hash);
        if (!el) return;
        el.classList.toggle('failed', state === 'failed');
        const s = el.querySelector('.rns-message-state');
        if (s) s.textContent = state;
    }

    async function sendMessage() {
        const input = document.getElementById('rnsComposeInput');
        const text = input ? input.value.trim() : '';
        const destination = _get('rnsDestination');
        if (!text) return;
        if (!destination) { alert('Pick a node or enter a destination address'); return; }
        try {
            await _post('/reticulum/send', { destination, text });
            if (input) input.value = '';
        } catch (e) {
            alert(e.message);
        }
    }

    // ── Helpers ────────────────────────────────────────────────────────────
    function _esc(s) {
        return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }
    function _get(id) { const el = document.getElementById(id); return el ? el.value.trim() : ''; }
    function _val(id, v) { const el = document.getElementById(id); if (el) el.value = v; }
    function _setText(id, v) { const el = document.getElementById(id); if (el) el.textContent = v; }
    function _setDisabled(id, v) { const el = document.getElementById(id); if (el) el.disabled = v; }
    function _flash(id, text) {
        const el = document.getElementById(id);
        if (!el) return;
        const prev = el.textContent;
        el.textContent = text;
        setTimeout(() => { el.textContent = prev; }, 1200);
    }

    return { init, destroy, selectMode, start, stop, announce, copyAddress, filterNodes, selectPeer, sendMessage };
})();
