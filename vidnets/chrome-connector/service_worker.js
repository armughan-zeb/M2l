const APP_ENDPOINTS = [
    'http://127.0.0.1:5000/api/youtube/session/import-extension',
    'http://localhost:5000/api/youtube/session/import-extension',
    'http://127.0.0.1:3210/api/youtube/session/import-extension',
    'http://localhost:3210/api/youtube/session/import-extension'
];
const AUTH_COOKIE_NAMES = new Set([
    'SID', 'HSID', 'SSID', 'APISID', 'SAPISID', 'LOGIN_INFO',
    '__SECURE-1PAPISID', '__SECURE-3PAPISID', '__SECURE-1PSID',
    '__SECURE-3PSID', '__SECURE-1PSIDTS', '__SECURE-3PSIDTS'
]);
const DEBOUNCE_MS = 1500;
let pendingSync = null;

function isRelevantCookie(cookie) {
    const domain = String(cookie?.domain || '').replace(/^\./, '').toLowerCase();
    return domain === 'youtube.com' || domain.endsWith('.youtube.com')
        || domain === 'google.com' || domain.endsWith('.google.com');
}

function isAuthCookie(cookie) {
    return AUTH_COOKIE_NAMES.has(String(cookie?.name || '').toUpperCase());
}

function publicCookieRecord(cookie) {
    return {
        name: cookie.name,
        value: cookie.value,
        domain: cookie.domain,
        path: cookie.path || '/',
        secure: Boolean(cookie.secure),
        httpOnly: Boolean(cookie.httpOnly),
        expirationDate: cookie.expirationDate,
        session: Boolean(cookie.session),
        sameSite: cookie.sameSite || 'unspecified'
    };
}

async function readCookieSnapshot() {
    // Query permitted account domains and URLs across YouTube and Google.
    const queryPromises = [
        chrome.cookies.getAll({ domain: 'youtube.com' }).catch(() => []),
        chrome.cookies.getAll({ domain: '.youtube.com' }).catch(() => []),
        chrome.cookies.getAll({ domain: 'google.com' }).catch(() => []),
        chrome.cookies.getAll({ domain: '.google.com' }).catch(() => []),
        chrome.cookies.getAll({ url: 'https://www.youtube.com/' }).catch(() => []),
        chrome.cookies.getAll({ url: 'https://youtube.com/' }).catch(() => []),
        chrome.cookies.getAll({ url: 'https://accounts.google.com/' }).catch(() => []),
        chrome.cookies.getAll({ url: 'https://myaccount.google.com/' }).catch(() => [])
    ];
    const lists = await Promise.all(queryPromises);
    const unique = new Map();
    for (const list of lists) {
        if (!Array.isArray(list)) continue;
        for (const cookie of list) {
            if (!isRelevantCookie(cookie)) continue;
            unique.set(`${cookie.storeId || ''}:${cookie.domain}:${cookie.path}:${cookie.name}`, cookie);
        }
    }
    const filtered = [...unique.values()].map(publicCookieRecord);
    return {
        cookies: filtered,
        cookieCount: filtered.length,
        authCookieCount: filtered.filter(isAuthCookie).length
    };
}

async function saveStatus(status) {
    await chrome.storage.local.set({
        ...status,
        updatedAt: Date.now()
    });
}

async function syncNow(reason = 'manual') {
    const snapshot = await readCookieSnapshot();
    if (!snapshot.authCookieCount) {
        await saveStatus({
            state: 'signed-out',
            reason,
            cookieCount: snapshot.cookieCount,
            authCookieCount: 0,
            message: 'No signed-in YouTube cookies found in this Chrome profile.'
        });
        return { ok: false, ...snapshot, state: 'signed-out' };
    }

    let lastError = 'Vidzeo backend is unavailable.';
    for (const endpoint of APP_ENDPOINTS) {
        try {
            const response = await fetch(endpoint, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-Vidzeo-Chrome-Connector': '1'
                },
                body: JSON.stringify({
                    cookies: snapshot.cookies,
                    browser: navigator.userAgent.includes('Edg/') ? 'edge' : 'chrome'
                })
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) {
                lastError = payload.error || `Vidzeo rejected the Chrome session (${response.status}).`;
                continue;
            }
            await saveStatus({
                state: 'verified',
                reason,
                cookieCount: snapshot.cookieCount,
                authCookieCount: snapshot.authCookieCount,
                message: 'YouTube account verified by Vidzeo.'
            });
            return { ok: true, ...snapshot, state: 'verified' };
        } catch (error) {
            lastError = error?.message || lastError;
        }
    }

    await saveStatus({
        state: 'offline',
        reason,
        cookieCount: snapshot.cookieCount,
        authCookieCount: snapshot.authCookieCount,
        message: lastError
    });
    return { ok: false, ...snapshot, state: 'offline', message: lastError };
}

function scheduleSync(reason) {
    clearTimeout(pendingSync);
    pendingSync = setTimeout(() => {
        pendingSync = null;
        syncNow(reason).catch((error) => saveStatus({
            state: 'error',
            reason,
            message: error?.message || 'Chrome cookie sync failed.'
        }));
    }, DEBOUNCE_MS);
}

chrome.cookies.onChanged.addListener((changeInfo) => {
    if (isRelevantCookie(changeInfo.cookie) && (isAuthCookie(changeInfo.cookie) || changeInfo.removed)) {
        scheduleSync('cookie-changed');
    }
});

chrome.runtime.onInstalled.addListener(() => {
    chrome.alarms.create('vidzeo-youtube-session-refresh', { periodInMinutes: 5 });
    scheduleSync('installed');
});

chrome.runtime.onStartup.addListener(() => {
    chrome.alarms.create('vidzeo-youtube-session-refresh', { periodInMinutes: 5 });
    scheduleSync('browser-startup');
});

chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === 'vidzeo-youtube-session-refresh') syncNow('periodic-refresh');
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (message?.type !== 'sync-now') return false;
    syncNow('popup').then(sendResponse).catch((error) => sendResponse({
        ok: false,
        state: 'error',
        message: error?.message || 'Chrome cookie sync failed.'
    }));
    return true;
});

// MV3 service workers can be started without onInstalled/onStartup firing
// (for example when Chrome restores the dedicated profile). Always schedule
// one local sync when this worker is evaluated.
scheduleSync('worker-start');
