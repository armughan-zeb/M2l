const badge = document.getElementById('badge');
const authCountEl = document.getElementById('auth-count');
const totalCountEl = document.getElementById('total-count');
const syncedAtEl = document.getElementById('synced-at');
const messageEl = document.getElementById('message');
const button = document.getElementById('sync');

function renderStatus(status = {}) {
    const state = status.state || 'unknown';
    badge.className = `badge ${state}`;
    
    if (state === 'verified') {
        badge.textContent = 'Active';
    } else if (state === 'signed-out') {
        badge.textContent = 'Signed Out';
    } else if (state === 'offline') {
        badge.textContent = 'Offline';
    } else {
        badge.textContent = state;
    }

    authCountEl.textContent = status.authCookieCount != null ? `${status.authCookieCount}` : '0';
    totalCountEl.textContent = status.cookieCount != null ? `${status.cookieCount}` : '—';
    
    if (status.updatedAt) {
        const d = new Date(status.updatedAt);
        syncedAtEl.textContent = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } else {
        syncedAtEl.textContent = 'Never';
    }

    messageEl.textContent = status.message || (status.authCookieCount > 0 ? 'YouTube session is active and synced.' : 'No authenticated YouTube cookies found.');
}

async function refreshStatus() {
    const data = await chrome.storage.local.get([
        'state', 'message', 'authCookieCount', 'cookieCount', 'updatedAt'
    ]);
    if (data.state) {
        renderStatus(data);
    } else {
        triggerSync();
    }
}

function triggerSync() {
    button.disabled = true;
    badge.className = 'badge syncing';
    badge.textContent = 'Syncing';
    messageEl.textContent = 'Syncing session with Vidzeo…';

    chrome.runtime.sendMessage({ type: 'sync-now' }, (result) => {
        button.disabled = false;
        if (chrome.runtime.lastError) {
            renderStatus({
                state: 'offline',
                message: chrome.runtime.lastError.message || 'Could not communicate with connector worker.'
            });
            return;
        }
        renderStatus(result || { state: 'unknown', message: 'No response from connector.' });
    });
}

button.addEventListener('click', triggerSync);
refreshStatus();
