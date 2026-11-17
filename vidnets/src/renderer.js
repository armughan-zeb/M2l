// =============================================================================
// VIDZEO - AI Video Studio
// Premium App Shell Architecture with OOP Design
// =============================================================================

// =============================================================================
// GLOBAL STATE MANAGEMENT (Singleton Pattern)
// =============================================================================

const ACCOUNT_STORAGE_KEY = 'vidzeo_account_state';
const ENTITLEMENT_SYNC_INTERVAL_MS = 15000;
// Billing is intentionally hosted on the Vidnets website. The desktop app
// never collects card details or embeds a second checkout flow.
const VIDNETS_PRICING_URL = 'https://vidnets.com/#pricing';

async function openPricingPage(preferredPlan = 'Pro') {
    try {
        // Keep the desktop CTA on the canonical public pricing route. Do not
        // append account identifiers or plan data to the URL: the website
        // owns checkout and resolves the signed-in account there.
        void preferredPlan;
        const pricingUrl = VIDNETS_PRICING_URL;
        if (window.electronAPI?.openExternalUrl) {
            await window.electronAPI.openExternalUrl(pricingUrl);
        } else {
            window.open(pricingUrl, '_blank', 'noopener,noreferrer');
        }
    } catch (error) {
        showToast(error.message || 'Could not open the Vidnets pricing page.', 'error');
    }
}
window.openPricingPage = openPricingPage;

const VIDNETS_GUIDE_LINKS = new Set([
    'https://openrouter.ai/',
    'https://openrouter.ai/keys',
    'https://openrouter.ai/credits',
    'https://www.pexels.com/api/',
    'https://pixabay.com/api/docs/'
]);

async function openGuideLink(url) {
    const target = String(url || '').trim();
    if (!VIDNETS_GUIDE_LINKS.has(target)) {
        showToast('That help link is not available.', 'warning');
        return;
    }
    try {
        if (window.electronAPI?.openExternalUrl) {
            await window.electronAPI.openExternalUrl(target);
        } else {
            window.open(target, '_blank', 'noopener,noreferrer');
        }
    } catch (error) {
        showToast(error.message || 'Could not open the help link.', 'error');
    }
}
window.openGuideLink = openGuideLink;

function formatOpenRouterStatusMessage(status) {
    return String(status?.message || '').trim();
}

function renderOpenRouterAccessStatus(status) {
    const element = $('openrouterStatus');
    if (!element || !status) return;
    const key = String(AppState.apiKeys.openrouter || '').trim();
    if (status.status === 'ready') {
        const remaining = status.limit_remaining;
        const amount = remaining !== null && remaining !== undefined && Number.isFinite(Number(remaining))
            ? ` Remaining key limit: $${Number(remaining).toFixed(2)}.`
            : '';
        element.textContent = `✓ OpenRouter connected.${amount}`;
        element.className = 'api-status set';
    } else if (!key || status.status === 'missing_key') {
        element.textContent = 'OpenRouter key missing. Scene AI and vision will use safe local fallbacks.';
        element.className = 'api-status warning';
    } else {
        element.textContent = `${formatOpenRouterStatusMessage(status) || 'OpenRouter needs attention.'} Use the links below.`;
        element.className = 'api-status warning';
    }
}

async function checkOpenRouterAccess({ required = false, notify = true, force = false, keyOverride = null } = {}) {
    let key = keyOverride !== null ? String(keyOverride).trim() : String(AppState.apiKeys.openrouter || '').trim();
    if (!key) {
        try {
            const serverStatus = await api('/api/openrouter/status');
            if (serverStatus?.ok && serverStatus?.status === 'ready') {
                AppState.openrouterStatus = { checkedAt: Date.now(), payload: serverStatus };
                renderOpenRouterAccessStatus(serverStatus);
                return { ...serverStatus, allowed: true };
            }
        } catch (e) {}
        const status = {
            ok: false,
            configured: false,
            status: 'missing_key',
            message: 'OpenRouter key missing. Local fallbacks remain available.'
        };
        AppState.openrouterStatus = { checkedAt: Date.now(), payload: status };
        renderOpenRouterAccessStatus(status);
        if (notify) {
            showToast(
                required
                    ? 'This voice needs an OpenRouter key. Add one in Settings.'
                    : 'OpenRouter is not configured; local fallbacks will be used. Add a key in Settings for scene AI and vision.',
                required ? 'error' : 'warning'
            );
            if (required) navigateTo('settings');
        }
        return { ...status, allowed: !required };
    }

    const cached = AppState.openrouterStatus?.payload;
    if (!force && cached && Date.now() - Number(AppState.openrouterStatus.checkedAt || 0) < 60000) {
        renderOpenRouterAccessStatus(cached);
        const allowed = Boolean(cached.ok || !required);
        if (notify && required && !cached.ok) {
            showToast(`${cached.message || 'OpenRouter needs attention.'} Update it in Settings.`, 'error');
            navigateTo('settings');
        }
        return { ...cached, allowed };
    }

    try {
        const status = await api('/api/openrouter/status', {
            method: 'POST',
            body: JSON.stringify({ api_key: key })
        });
        AppState.openrouterStatus = { checkedAt: Date.now(), payload: status };
        renderOpenRouterAccessStatus(status);
        const allowed = Boolean(status.ok || !required);
        if (notify && !status.ok) {
            showToast(
                `${status.message || 'OpenRouter needs attention.'} ${required ? 'Update it in Settings.' : 'Local fallbacks remain available.'}`,
                required ? 'error' : 'warning'
            );
            if (required) navigateTo('settings');
        }
        return { ...status, allowed };
    } catch (error) {
        const status = {
            ok: false,
            configured: Boolean(key),
            status: 'unavailable',
            message: error.message || 'Could not check OpenRouter.'
        };
        AppState.openrouterStatus = { checkedAt: Date.now(), payload: status };
        renderOpenRouterAccessStatus(status);
        if (notify) showToast(`${status.message} Local fallbacks remain available.`, required ? 'error' : 'warning');
        if (required) navigateTo('settings');
        return { ...status, allowed: !required };
    }
}
window.checkOpenRouterAccess = checkOpenRouterAccess;

function providerDefaultModel() { return 'qwen/qwen3.5-flash-02-23'; }
function renderProviderPanels() {
    $('openrouterAccountPanel')?.classList.remove('hidden');
    if ($('llmProviderSelect')) $('llmProviderSelect').value = 'openrouter';
}
async function checkSelectedProviderAccess(options = {}) { return checkOpenRouterAccess(options); }

const LocalAgent = {
    async refresh() {
        try {
            const state = await api('/api/agent/access');
            if ($('agentAccessStatus')) $('agentAccessStatus').textContent = state.enabled
                ? 'Paired. Codex can use VIDNETS_AGENT.cmd without opening Vidnets. Your agent supplies text and vision; no OpenRouter calls are made for its jobs.'
                : 'Sign in, then pair once to allow an agent to run the local service.';
            if ($('agentRevokeBtn')) $('agentRevokeBtn').disabled = !state.enabled;
        } catch (_) {
            if ($('agentAccessStatus')) $('agentAccessStatus').textContent = 'Sign in to an active Vidnets account to enable agent access.';
        }
    },
    async pair() {
        const button = $('agentPairBtn');
        if (button) button.disabled = true;
        try {
            await api('/api/agent/pair', {method: 'POST', body: JSON.stringify({refresh_token: AppState.account.refreshToken || ''})});
            await this.refresh();
            showToast('Local agent paired. See AGENT_ACCESS.md in the app folder.', 'success');
        } catch (error) { showToast(error.message, 'error'); }
        finally { if (button) button.disabled = false; }
    },
    async revoke() {
        try {
            await api('/api/agent/revoke', {method: 'POST', body: '{}'});
            await this.refresh();
            showToast('Local agent access revoked.', 'success');
        } catch (error) { showToast(error.message, 'error'); }
    }
};
window.LocalAgent = LocalAgent;

function updateYouTubeCookieNotice(session = {}, unavailable = false) {
    const notice = $('youtubeCookieNotice');
    const title = $('youtubeCookieNoticeTitle');
    const copy = $('youtubeCookieNoticeCopy');
    if (!notice) return;

    const connected = Boolean(session?.ready && session?.verified_authenticated);
    notice.classList.toggle('hidden', connected || document.getElementById('section-settings')?.classList.contains('active'));
    if (connected) return;

    if (session?.cookie_update_required) {
        if (title) title.textContent = 'Your YouTube cookies need to be refreshed';
        if (copy) copy.textContent = session.cookie_update_reason || 'Reconnect your signed-in account before creating footage-heavy videos.';
    } else if (unavailable) {
        if (title) title.textContent = 'YouTube cookies could not be checked';
        if (copy) copy.textContent = 'Open Settings and connect your signed-in account before relying on YouTube footage.';
    } else {
        if (title) title.textContent = 'YouTube cookies are not connected';
        if (copy) copy.textContent = 'Connect your signed-in account for reliable clip extraction and the best available quality up to 1080p.';
    }
}

async function connectYouTubeFromNotice() {
    if (typeof SettingsController === 'undefined' || typeof SettingsController.connectYouTubeBrowser !== 'function') {
        navigateTo('settings');
        showToast('Open Settings to connect your YouTube account.', 'info');
        return;
    }
    await SettingsController.connectYouTubeBrowser('chrome');
}
window.connectYouTubeFromNotice = connectYouTubeFromNotice;

let upgradeReturnFocus = null;
function maybeShowFreeUpgrade(occasion) {
    if (AppState.account.planKey !== 'free_trial') return;
    const key = `vidnets:upgrade:${AppState.account.userId || AppState.account.email}:${occasion}`;
    try { if (sessionStorage.getItem(key)) return; sessionStorage.setItem(key, '1'); } catch (_) {}
    openUpgradeModal('Your administrator-granted trial includes five exports per UTC calendar month, up to one minute each. Purchase Lifetime Pro for full access. Provider usage is separate.');
}

function openUpgradeModal(reasonMessage = 'Monthly export or duration limit reached.', preferredPlan = 'Pro') {
    const modal = $('upgradePlanModal');
    if (!modal) return;
    const reasonTextEl = $('upgradeModalReasonText');
    if (reasonTextEl) {
        reasonTextEl.textContent = reasonMessage;
    }
    upgradeReturnFocus = document.activeElement;
    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
    $('upgradeModalCloseBtn')?.focus();
}

function closeUpgradeModal() {
    const modal = $('upgradePlanModal');
    if (!modal) return;
    modal.classList.remove('open');
    modal.setAttribute('aria-hidden', 'true');
    upgradeReturnFocus?.focus?.();
}

function handleUpgradeModalSelect(planName = 'Pro') {
    closeUpgradeModal();
    openPricingPage(planName);
}

async function handleUpgradeRefreshAccount() {
    try {
        showToast('Checking Supabase entitlement...', 'info');
        await AccountController.refreshEntitlement({ silent: false });
        AccountController.render();
        if (isStudioAccessActive()) {
            showToast('Account refreshed! Studio access active.', 'success');
            closeUpgradeModal();
        } else {
            showToast('Account synced with Supabase.', 'info');
        }
    } catch (err) {
        showToast(err.message || 'Could not refresh account.', 'error');
    }
}

window.openUpgradeModal = openUpgradeModal;
window.closeUpgradeModal = closeUpgradeModal;
window.handleUpgradeModalSelect = handleUpgradeModalSelect;
window.handleUpgradeRefreshAccount = handleUpgradeRefreshAccount;

function accountEntitlementSignature(account = {}) {
    return [
        account.signedIn,
        account.email,
        account.planKey,
        account.planStatus,
        account.accessState,
        account.accountStatus,
        account.accessUpdatedAt,
        account.deviceStatus,
        account.deviceCount
    ].join('|');
}

function getDefaultAccountState() {
    return {
        signedIn: true,
        signed_in: true,
        userId: 'local-user',
        authUserId: 'local-user',
        email: 'local@vidzeo.app',
        name: 'Local Studio',
        status: 'Studio unlocked',
        plan: 'Pro Lifetime',
        planKey: 'pro',
        pendingPlanKey: null,
        requestedPlanName: '',
        planStatus: 'active',
        accessState: 'active',
        accessCopy: 'Studio unlocked (sign-in free).',
        sessionLabel: 'Local Studio',
        sessionCopy: 'Sign-in free local mode active.',
        accessToken: 'local-dev-token',
        refreshToken: '',
        billingPeriod: 'lifetime',
        maxVideoMinutes: null,
        maxScriptWords: null,
        priceMonthlyDisplay: null,
        monthlyExportLimit: null,
        monthlyExportsUsed: 0,
        monthlyExportsRemaining: null,
        isUnlimitedExports: true,
        deviceFingerprint: 'local-device',
        deviceId: 'local-device',
        deviceStatus: 'active',
        deviceCount: 1,
        deviceLimit: 999,
        accountStatus: 'active',
        accessUpdatedAt: new Date().toISOString()
    };
}

function normalizeAccountState(next = {}) {
    const defaults = getDefaultAccountState();
    const merged = { ...defaults, ...(next || {}) };
    const hasPendingPlanKey = Object.prototype.hasOwnProperty.call(next, 'pending_plan_key')
        || Object.prototype.hasOwnProperty.call(next, 'requested_plan_key')
        || Object.prototype.hasOwnProperty.call(next, 'pendingPlanKey');
    const hasPendingPlanName = Object.prototype.hasOwnProperty.call(next, 'pending_plan_name')
        || Object.prototype.hasOwnProperty.call(next, 'requested_plan_name')
        || Object.prototype.hasOwnProperty.call(next, 'requestedPlanName');
    merged.userId = merged.userId || next.user_id || '';
    merged.authUserId = merged.authUserId || next.auth_user_id || '';
    merged.accessToken = merged.accessToken || next.access_token || '';
    merged.refreshToken = merged.refreshToken || next.refresh_token || '';
    const hasVerifiedIdentity = Boolean(merged.accessToken && (merged.authUserId || merged.userId || merged.email));
    merged.signedIn = Boolean((merged.signedIn || merged.signed_in || merged.accessToken) && hasVerifiedIdentity);
    merged.signed_in = merged.signedIn;
    merged.planKey = next.plan_key ?? merged.planKey ?? null;
    merged.pendingPlanKey = hasPendingPlanKey
        ? (next.pending_plan_key ?? next.requested_plan_key ?? next.pendingPlanKey ?? null)
        : (merged.pendingPlanKey ?? null);
    merged.requestedPlanName = hasPendingPlanName
        ? (next.pending_plan_name || next.requested_plan_name || next.requestedPlanName || '')
        : (merged.requestedPlanName || '');
    merged.planStatus = next.plan_status || merged.planStatus || 'pending';
    merged.accessState = next.access_state || merged.accessState || 'pending';
    merged.accountStatus = next.account_status || merged.accountStatus || 'pending';
    if (!merged.signedIn) {
        merged.accessState = 'pending';
        merged.planStatus = 'pending';
        merged.planKey = null;
        merged.pendingPlanKey = null;
        merged.requestedPlanName = '';
    }
    merged.accessCopy = next.access_copy || merged.accessCopy || defaults.accessCopy;
    merged.plan = next.plan_name || merged.plan || defaults.plan;
    merged.status = next.status || merged.status || merged.accessState;
    merged.billingPeriod = next.billing_period || merged.billingPeriod || 'monthly';
    merged.maxVideoMinutes = next.max_video_minutes ?? merged.maxVideoMinutes ?? null;
    merged.maxScriptWords = next.max_script_words ?? merged.maxScriptWords ?? null;
    merged.priceMonthlyDisplay = next.price_monthly_display ?? merged.priceMonthlyDisplay ?? null;
    merged.monthlyExportLimit = next.monthly_export_limit ?? next.monthlyExportLimit ?? defaults.monthlyExportLimit;
    merged.monthlyExportsUsed = next.monthly_exports_used ?? next.monthlyExportsUsed ?? defaults.monthlyExportsUsed;
    merged.monthlyExportsRemaining = next.monthly_exports_remaining ?? next.monthlyExportsRemaining ?? defaults.monthlyExportsRemaining;
    merged.isUnlimitedExports = Boolean(next.is_unlimited_exports ?? next.isUnlimitedExports ?? defaults.isUnlimitedExports);
    merged.deviceId = next.device_id || merged.deviceId || '';
    merged.deviceFingerprint = next.device_fingerprint || merged.deviceFingerprint || '';
    merged.deviceStatus = next.device_status || merged.deviceStatus || '';
    merged.deviceCount = next.device_count ?? merged.deviceCount ?? null;
    merged.deviceLimit = next.device_limit ?? merged.deviceLimit ?? 2;
    merged.accessUpdatedAt = next.access_updated_at || next.accessUpdatedAt || merged.accessUpdatedAt || '';
    merged.access_updated_at = merged.accessUpdatedAt;
    return merged;
}

async function syncDesktopAuthState() {
    try {
        const auth = await window.electronAPI?.getAuthState?.();
        if (!auth) return false;
        const result = applyDesktopAuthPayload(auth);
        if (auth.signed_in) {
            SettingsController.autoConnectYouTubeBrowserIfAvailable();
        }
        return result;
    } catch (error) {
        console.warn('Could not sync desktop auth state', error);
        return false;
    }
}


function loadAccountState() {
    if (!window.electronAPI?.getAuthState) {
        return getDefaultAccountState();
    }
    try {
        const saved = JSON.parse(localStorage.getItem(ACCOUNT_STORAGE_KEY) || 'null');
        const cached = normalizeAccountState(saved || {});
        if (!cached.signedIn) {
            return cached;
        }
        return normalizeAccountState({
            ...cached,
            accessState: 'pending',
            planStatus: 'pending',
            status: 'Checking access',
            accessCopy: 'Checking your plan and device access...',
            accessToken: '',
            refreshToken: ''
        });
    } catch (_error) {
        return getDefaultAccountState();
    }
}

function applyDesktopAuthPayload(auth = {}) {
    const previousSignature = accountEntitlementSignature(AppState.account);
    const hasPlanName = Object.prototype.hasOwnProperty.call(auth, 'plan_name') || Object.prototype.hasOwnProperty.call(auth, 'planName');
    const hasPlanKey = Object.prototype.hasOwnProperty.call(auth, 'plan_key') || Object.prototype.hasOwnProperty.call(auth, 'planKey');
    const hasPlanStatus = Object.prototype.hasOwnProperty.call(auth, 'plan_status') || Object.prototype.hasOwnProperty.call(auth, 'planStatus');
    AppState.account = normalizeAccountState({
        ...AppState.account,
        ...auth,
        userId: auth.user_id || AppState.account.userId,
        authUserId: auth.auth_user_id || AppState.account.authUserId,
        email: auth.email || AppState.account.email,
        name: auth.name || AppState.account.name || 'Google Creator',
        status: auth.access_state === 'active'
            ? 'Studio unlocked'
            : auth.access_state === 'suspended'
                ? 'Suspended'
                : 'Pricing locked',
        plan: hasPlanName ? (auth.plan_name || auth.planName || 'No active plan') : AppState.account.plan,
        planKey: hasPlanKey ? (auth.plan_key ?? auth.planKey ?? null) : AppState.account.planKey,
        pending_plan_key: auth.pending_plan_key ?? auth.requested_plan_key ?? auth.pendingPlanKey ?? null,
        pending_plan_name: auth.pending_plan_name ?? auth.requested_plan_name ?? auth.pendingPlanName ?? '',
        planStatus: hasPlanStatus ? (auth.plan_status || auth.planStatus || 'pending') : AppState.account.planStatus,
        accessState: auth.access_state || AppState.account.accessState,
        accessCopy: auth.access_copy || AppState.account.accessCopy,
        accountStatus: auth.account_status || AppState.account.accountStatus,
        sessionLabel: auth.signed_in ? 'Signed in with Google' : 'Signed out',
        sessionCopy: auth.email
            ? `Connected as ${auth.email}.`
            : (auth.signed_in ? 'Connected through the desktop sign-in screen.' : 'Use the front page to sign in again.'),
        accessToken: auth.access_token || AppState.account.accessToken,
        refreshToken: auth.refresh_token || AppState.account.refreshToken,
        billingPeriod: auth.billing_period || AppState.account.billingPeriod,
        maxVideoMinutes: auth.max_video_minutes || AppState.account.maxVideoMinutes,
        maxScriptWords: auth.max_script_words || AppState.account.maxScriptWords,
        priceMonthlyDisplay: auth.price_monthly_display || AppState.account.priceMonthlyDisplay,
        monthlyExportLimit: auth.monthly_export_limit ?? AppState.account.monthlyExportLimit,
        monthlyExportsUsed: auth.monthly_exports_used ?? AppState.account.monthlyExportsUsed,
        monthlyExportsRemaining: auth.monthly_exports_remaining ?? AppState.account.monthlyExportsRemaining,
        isUnlimitedExports: auth.is_unlimited_exports ?? AppState.account.isUnlimitedExports,
        deviceId: auth.device_id || AppState.account.deviceId,
        deviceFingerprint: auth.device_fingerprint || AppState.account.deviceFingerprint,
        deviceStatus: auth.device_status || AppState.account.deviceStatus,
        deviceCount: auth.device_count ?? AppState.account.deviceCount,
        deviceLimit: auth.device_limit ?? AppState.account.deviceLimit,
        accessUpdatedAt: auth.access_updated_at || AppState.account.accessUpdatedAt
    });
    AccountController.save();
    return previousSignature !== accountEntitlementSignature(AppState.account);
}

const AppState = {
    currentSection: 'long-video',
    isGenerating: false,
    theme: localStorage.getItem('vidzeo_theme') || 'dark',
    currentVideo: null,
    projects: [],
    selectedProjectId: null,
    account: loadAccountState(),
    availablePlans: [],
    pricingCycle: 'monthly',
    runtimeLogs: [],
    openrouterStatus: { checkedAt: 0, payload: null },

    // API Keys
    apiKeys: {
        openrouter: '',
        pollinations: '',
        pexels: '',
        pixabay: '',
        together: '',
        openai: ''
    },

    // LLM Model selection
    llmProvider: 'openrouter',
    llmModel: 'qwen/qwen3.5-flash-02-23',
    generationCostProfile: 'efficient',

    // Queues
    longQueue: [],
    shortQueue: [],

    // Input modes
    longInputMode: 'script',

    // Generated videos
    generatedVideos: [],

    // Dropdowns data
    dropdownData: {
        voices: [],
        fonts: [],
        styles: [],
        effects: [],
        animations: [],
        resolutions: [],
        sceneModes: [],
        edgeVoices: [],
        edgeDefaults: { rate: 0, pitch: 0, volume: 0 },
        ttsModels: [],
        ttsVoices: {},
        llmProviders: {},
        llmModels: { default_provider: 'openrouter', default_model: 'qwen/qwen3.5-flash-02-23', providers: {} },
        imageProviders: {},
        imageModels: {}
    }
};


// =============================================================================
// UTILITY FUNCTIONS (Global)
// =============================================================================

const $ = (id) => document.getElementById(id);
window.$ = $;

function setElementVisibility(target, visible, displayValue = 'block') {
    const element = typeof target === 'string' ? $(target) : target;
    if (!element) return;
    element.classList.toggle('hidden', !visible);
    element.style.display = visible ? displayValue : 'none';
}

function showToast(message, type = 'info') {
    const container = $('toastContainer');
    if (!container) return;
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}

function appendRuntimeLog(message, level = 'info') {
    const text = String(message || '').trim();
    if (!text) return;
    const normalizedLevel = String(level || 'info').toLowerCase();
    const now = new Date();
    const timeStr = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    AppState.runtimeLogs = [
        ...(AppState.runtimeLogs || []),
        { text, level: normalizedLevel, at: timeStr, ts: now.getTime() }
    ].slice(-200);
    const list = $('runtimeLogList');
    const count = $('runtimeLogCount');
    if (count) {
        count.textContent = String(AppState.runtimeLogs.length);
        // Pulse the count badge to draw attention to new entries
        count.classList.remove('pulse');
        void count.offsetWidth; // force reflow
        count.classList.add('pulse');
    }
    if (!list) return;
    const levelBadge = (lvl) => {
        if (lvl === 'error') return '<span class="log-badge log-badge-error">ERR</span>';
        if (lvl === 'warn' || lvl === 'warning') return '<span class="log-badge log-badge-warn">WARN</span>';
        return '<span class="log-badge log-badge-info">INFO</span>';
    };
    list.innerHTML = AppState.runtimeLogs.map((entry) => (
        `<div class="runtime-log-entry ${escapeHtml(entry.level)}" title="${escapeHtml(entry.text)}">${levelBadge(entry.level)}<span class="log-time">${escapeHtml(entry.at)}</span> ${escapeHtml(entry.text)}</div>`
    )).join('');
    list.scrollTop = list.scrollHeight;
}
window.appendRuntimeLog = appendRuntimeLog;

async function loadRuntimeLogHistory(limit = 80) {
    try {
        const payload = await api(`/api/runtime/logs?limit=${Math.max(1, Math.min(200, Number(limit) || 80))}`);
        const lines = Array.isArray(payload?.lines) ? payload.lines : [];
        lines.forEach((line) => {
            const rendered = String(line || '').trim();
            if (!rendered) return;
            appendRuntimeLog(`[server history] ${rendered}`, /\[(?:ERROR|CRITICAL)\]|error|failed|exception|crash/i.test(rendered) ? 'error' : 'info');
        });
    } catch (error) {
        // The live Electron stream remains available when the history route
        // is unavailable (for example during backend startup).
        console.warn('Runtime log history unavailable:', error?.message || error);
    }
}
window.loadRuntimeLogHistory = loadRuntimeLogHistory;

function withUiTimeout(promise, timeoutMs = 12000, label = 'This operation') {
    let timer;
    return Promise.race([promise, new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(`${label} took too long. Please retry.`)), timeoutMs);
    })]).finally(() => clearTimeout(timer));
}

async function fetchJsonWithDeadline(endpoint, options = {}) {
    const {timeoutMs: requestedTimeout, ...requestOptions} = options;
    const timeoutMs = requestedTimeout ?? (/preview-voice/.test(endpoint) ? 75000
        : /\/(?:generate|render|export)(?:\/|$)/.test(endpoint) ? 0 : 20000);
    const controller = new AbortController();
    const timer = timeoutMs > 0 ? setTimeout(() => controller.abort(), timeoutMs) : null;
    const signal = options.signal ? AbortSignal.any([options.signal, controller.signal]) : controller.signal;
    try {
        const response = await fetch(endpoint, {...requestOptions, signal});
        const payload = await response.json().catch(error => {
            if (controller.signal.aborted) throw error;
            if (!response.ok) return {error: `Server request failed (${response.status}).`};
            throw new Error('The server returned an unreadable response. Please retry.');
        });
        return {response, payload};
    } catch (error) {
        if (controller.signal.aborted && !options.signal?.aborted) {
            const timeout = new Error('The server is taking too long to respond. Running jobs continue; refresh status before submitting again.');
            timeout.code = 'REQUEST_TIMEOUT'; throw timeout;
        }
        throw error;
    } finally { if (timer) clearTimeout(timer); }
}

async function api(endpoint, options = {}) {
    const makeRequest = async () => {
        const headers = {
            'Content-Type': 'application/json',
            ...(options.headers || {})
        };
        if (AppState.account.accessToken) {
            headers.Authorization = `Bearer ${AppState.account.accessToken}`;
        }
        if (AppState.account.deviceFingerprint) {
            headers['X-Vidzeo-Device-Fingerprint'] = AppState.account.deviceFingerprint;
        }

        const {response, payload} = await fetchJsonWithDeadline(endpoint, {...options, headers});

        if (!response.ok) {
            const error = payload || {error: 'Request failed'};
            const message = error.error || 'Request failed';
            const wrapped = new Error(message);
            wrapped.status = response.status;
            wrapped.code = error.code;
            wrapped.requestId = error.request_id;
            wrapped.limit_reached = Boolean(error.limit_reached || message.includes('PLAN_LIMIT_REACHED'));
            wrapped.plan_key = error.plan_key;
            if (wrapped.limit_reached) {
                const cleanMsg = message.replace('PLAN_LIMIT_REACHED:', '').trim();
                openUpgradeModal(cleanMsg, 'Pro');
            }
            throw wrapped;
        }

        return payload;
    };

    try {
        return await makeRequest();
    } catch (error) {
        if (error.status === 401 && window.electronAPI?.refreshAuthState) {
            try {
                const refreshed = await window.electronAPI.refreshAuthState();
                AppState.account = normalizeAccountState({
                    ...AppState.account,
                    ...refreshed,
                    accessToken: refreshed.access_token || AppState.account.accessToken,
                    refreshToken: refreshed.refresh_token || AppState.account.refreshToken
                });
                AccountController.save();
                return await makeRequest();
            } catch (_refreshError) {
                // Let the original error fall through.
            }
        }
        console.error('API Error:', error);
        appendRuntimeLog(`[api] ${endpoint}: ${error.message || error}`, 'error');
        throw error;
    }
}

function formatProjectDate(value) {
    if (!value) return 'Just now';
    const millis = value > 1e12 ? value : value * 1000;
    return new Date(millis).toLocaleDateString();
}

function formatFileSize(bytes = 0) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function escapeHtml(value = '') {
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function normalizeProject(project = {}) {
    const name = project.name || project.filename || 'Untitled Video';
    const filename = project.filename || name;
    const url = project.url || (filename ? `/output/${encodeURIComponent(filename).replace(/%2F/gi, '/')}` : '');
    return {
        id: project.id || name,
        name,
        filename,
        url,
        thumbnail_url: project.thumbnail_url || project.thumbnailUrl || '',
        size: Number(project.size || 0),
        created_at: project.created_at || project.created || project.modified_at || Date.now() / 1000,
        modified_at: project.modified_at || project.created_at || Date.now() / 1000,
        extension: project.extension || '',
        path: project.path || '',
        root: project.root || '',
        duration: Number(project.duration || 0)
    };
}

function projectIdentity(project = {}) {
    return String(project.path || project.url || project.id || project.filename || project.name || '');
}

function isStudioAccessActive() {
    return true; // Local sign-in free mode
}

function ensureStudioAccess(message = 'An active plan is required to use the studio.') {
    if (isStudioAccessActive()) {
        return true;
    }
    openPricingPage();
    showToast(AppState.account.accessCopy || message, AppState.account.accessState === 'suspended' ? 'error' : 'warning');
    return false;
}

function countScriptWords(text = '') {
    if (!text || typeof text !== 'string') return 0;
    const trimmed = text.trim();
    if (!trimmed) return 0;
    const tokens = trimmed.split(/\s+/).filter(Boolean);
    return tokens.length;
}


function estimateScriptDurationSeconds(script = '') {
    const words = countScriptWords(script);
    if (words <= 0) return 8;
    return Math.max(8, Math.round((words / 2.6) * 1000) / 1000);
}

function parseDurationToSeconds(value) {
    if (value == null || value === '') return 0;
    if (typeof value === 'number') return Math.max(value, 0);
    const match = String(value).trim().toLowerCase().match(/^(\d+(?:\.\d+)?)\s*(second|seconds|minute|minutes|hour|hours)$/);
    if (!match) return 0;
    const amount = Number(match[1]);
    const unit = match[2];
    if (unit.startsWith('second')) return amount;
    if (unit.startsWith('minute')) return amount * 60;
    if (unit.startsWith('hour')) return amount * 3600;
    return 0;
}

function formatPlanMinuteLimit(minutes) {
    const value = Number(minutes || 0);
    if (!value) return 'your plan limit';
    if (value >= 60 && value % 60 === 0) {
        return value === 60 ? '1 hour' : `${value / 60} hours`;
    }
    return value === 1 ? '1 minute' : `${value} minutes`;
}

function getCurrentPlanLimits() {
    const plan = AppState.availablePlans.find((item) => item.plan_key === AppState.account.planKey) || {};
    const maxVideoMinutes = Number(
        AppState.account.maxVideoMinutes
        ?? plan.video_limit_minutes
        ?? 0
    ) || null;
    const maxScriptWords = Number(
        AppState.account.maxScriptWords
        ?? plan.script_word_limit
        ?? (maxVideoMinutes ? Math.floor(maxVideoMinutes * 156) : 0)
    ) || null;

    return {
        planName: AppState.account.plan || plan.display_name || 'your plan',
        maxVideoMinutes,
        maxScriptWords
    };
}

function getPlanLimitSummary() {
    const limits = getCurrentPlanLimits();
    const exportLimit = AppState.account.isUnlimitedExports
        ? 'Unlimited exports'
        : (AppState.account.monthlyExportLimit ? `${AppState.account.monthlyExportsUsed || 0}/${AppState.account.monthlyExportLimit} exports this month` : '');
    const durationLimit = limits.maxVideoMinutes ? `up to ${formatPlanMinuteLimit(limits.maxVideoMinutes)}` : '';
    const parts = [exportLimit, durationLimit].filter(Boolean);
    return parts.length ? `(${parts.join(', ')})` : '';
}

function renderProfileUsage(account = AppState.account) {
    const exportLimit = account.isUnlimitedExports
        ? 'Unlimited'
        : (Number.isFinite(Number(account.monthlyExportLimit)) && Number(account.monthlyExportLimit) > 0
            ? `${Number(account.monthlyExportsUsed || 0)} / ${Number(account.monthlyExportLimit)}`
            : '—');
    const remaining = account.isUnlimitedExports
        ? 'No monthly cap on this plan.'
        : (account.monthlyExportsRemaining == null
            ? 'Usage will appear after your account syncs.'
            : `${Math.max(0, Number(account.monthlyExportsRemaining))} export${Number(account.monthlyExportsRemaining) === 1 ? '' : 's'} remaining this month.`);
    const duration = account.maxVideoMinutes ? `Up to ${formatPlanMinuteLimit(account.maxVideoMinutes)}` : 'Plan limit pending';
    const words = account.maxScriptWords ? `${Number(account.maxScriptWords).toLocaleString()} script words` : 'Script limit follows your plan.';
    const devices = account.deviceCount == null ? '—' : `${account.deviceCount} / ${account.deviceLimit || 2}`;
    const usageHeadline = account.plan && account.plan !== 'No active plan'
        ? (account.billingPeriod === 'lifetime'
            ? `${account.plan} · lifetime access`
            : account.planKey === 'free_trial' ? `${account.plan} · administrator granted` : `${account.plan} · ${account.billingPeriod || 'monthly'} billing`)
        : 'Purchase Lifetime Pro to unlock the studio';
    $('profileUsageHeadline') && ($('profileUsageHeadline').textContent = usageHeadline);
    $('profileExportsValue') && ($('profileExportsValue').textContent = exportLimit);
    $('profileExportsCopy') && ($('profileExportsCopy').textContent = remaining);
    $('profileVideoLimitValue') && ($('profileVideoLimitValue').textContent = duration);
    $('profileWordsLimitCopy') && ($('profileWordsLimitCopy').textContent = words);
    $('profileDevicesValue') && ($('profileDevicesValue').textContent = devices);
    $('profileDevicesCopy') && ($('profileDevicesCopy').textContent = account.deviceCount == null
        ? 'Sign in to sync device access.'
        : `Device limit: ${account.deviceLimit || 2}.`);
}

function syncPlanDurationSelect(selectId) {
    const select = $(selectId);
    if (!select) return;

    if (!select.dataset.originalOptions) {
        select.dataset.originalOptions = JSON.stringify(
            Array.from(select.options).map((option) => ({
                value: option.value,
                text: option.textContent
            }))
        );
    }

    const rawOptions = JSON.parse(select.dataset.originalOptions || '[]');
    const { maxVideoMinutes } = getCurrentPlanLimits();
    const filteredOptions = maxVideoMinutes
        ? rawOptions.filter((option) => (parseDurationToSeconds(option.value) / 60) <= maxVideoMinutes)
        : rawOptions;

    const nextOptions = filteredOptions.length ? filteredOptions : rawOptions;
    const nextValues = nextOptions.map((option) => option.value).join('\u0001');
    // Skip the DOM rebuild entirely if nothing changed. This ran on every
    // queue poll tick (2s) and every account render, replacing children each
    // time, which is the main cause of the laggy UI during generation.
    if (select.dataset.lastValues === nextValues) {
        return;
    }
    select.dataset.lastValues = nextValues;

    const previousValue = select.value;
    select.innerHTML = '';
    nextOptions.forEach((option) => {
        const nextOption = document.createElement('option');
        nextOption.value = option.value;
        nextOption.textContent = option.text;
        select.appendChild(nextOption);
    });

    const values = nextOptions.map((option) => option.value);
    if (values.includes(previousValue)) {
        select.value = previousValue;
    } else if (values.length) {
        select.value = values[values.length - 1];
    }
}

function syncPlanDurationOptions() {
    ['bulkDuration', 'shortDuration', 'bulkShortDuration'].forEach(syncPlanDurationSelect);
}

function validateGenerationAgainstPlan({ inputMode = '', script = '', duration = '', contextLabel = 'This export' } = {}) {
    const limits = getCurrentPlanLimits();

    // 1. Monthly export limit check
    if (AppState.account.monthlyExportsRemaining !== undefined && AppState.account.monthlyExportsRemaining !== null && !AppState.account.isUnlimitedExports) {
        if (Number(AppState.account.monthlyExportsRemaining) <= 0) {
            const msg = `You have reached your monthly export limit (${AppState.account.monthlyExportsUsed || AppState.account.monthlyExportLimit || 10}/${AppState.account.monthlyExportLimit || 10}) on the ${limits.planName} plan. Purchase Lifetime Pro to continue creating.`;
            openUpgradeModal(msg, 'Pro');
            return msg;
        }
    }

    if (!limits.maxVideoMinutes) return null;

    const maxDurationSeconds = limits.maxVideoMinutes * 60;
    if (script) {
        const scriptWords = countScriptWords(script);
        const estimatedDurationSeconds = estimateScriptDurationSeconds(script);
        if (limits.maxScriptWords && scriptWords > limits.maxScriptWords) {
            const msg = `${contextLabel} is too long for ${limits.planName}. Keep the script under about ${limits.maxScriptWords.toLocaleString()} words, or upgrade to Pro for 30+ minute exports.`;
            openUpgradeModal(msg, 'Pro');
            return msg;
        }
        if (estimatedDurationSeconds > maxDurationSeconds) {
            const msg = `${contextLabel} is too long for ${limits.planName}. It estimates to about ${Math.ceil(estimatedDurationSeconds / 60)} minutes, but your plan allows ${formatPlanMinuteLimit(limits.maxVideoMinutes)} per export. Upgrade to Pro for 30+ minute exports!`;
            openUpgradeModal(msg, 'Pro');
            return msg;
        }
    }

    if (duration) {
        const requestedSeconds = parseDurationToSeconds(duration);
        if (requestedSeconds > maxDurationSeconds) {
            const msg = `${contextLabel} duration of ${duration} exceeds your plan limit of ${formatPlanMinuteLimit(limits.maxVideoMinutes)}. Upgrade to Pro for 30+ minute exports!`;
            openUpgradeModal(msg, 'Pro');
            return msg;
        }
    }

    return null;
}

function trimTextToWordLimit(text = '', maxWords = null) {
    if (!maxWords || maxWords <= 0) {
        return { text: String(text || ''), clipped: false, words: countScriptWords(text) };
    }

    const source = String(text || '');
    const tokens = source.match(/\S+\s*/g) || [];
    if (tokens.length <= maxWords) {
        return { text: source, clipped: false, words: tokens.length };
    }

    const trimmed = tokens.slice(0, maxWords).join('').trimEnd();
    return { text: trimmed, clipped: true, words: maxWords };
}

const PlanWordLimitController = {
    _blockedClassDurationMs: 1400,

    isScriptMode(inputId) {
        return true;
    },

    exceedsWordLimit(currentText = '', insertText = '', input) {
        const { maxScriptWords } = getCurrentPlanLimits();
        if (!maxScriptWords) return false;

        const source = String(currentText || '');
        const selectionStart = Number.isInteger(input?.selectionStart) ? input.selectionStart : source.length;
        const selectionEnd = Number.isInteger(input?.selectionEnd) ? input.selectionEnd : source.length;
        const nextValue = `${source.slice(0, selectionStart)}${insertText}${source.slice(selectionEnd)}`;
        return countScriptWords(nextValue) > maxScriptWords;
    },

    flashBlockedState(input, helper, planName, maxScriptWords) {
        if (!input || !helper) return;

        input.classList.add('is-word-limit-blocked');
        helper.classList.add('is-blocked', 'is-at-limit');

        const now = Date.now();
        const lastToast = Number(input.dataset.wordLimitToastAt || 0);
        if (now - lastToast > 1200) {
            input.dataset.wordLimitToastAt = String(now);
            showToast(`${planName} allows up to ${maxScriptWords.toLocaleString()} words per video.`, 'error');
        }

        window.clearTimeout(Number(input.dataset.wordLimitFlashTimer || 0));
        const timer = window.setTimeout(() => {
            input.classList.remove('is-word-limit-blocked');
            helper.classList.remove('is-blocked');
            input.dataset.wordLimitFlashTimer = '';
        }, this._blockedClassDurationMs);
        input.dataset.wordLimitFlashTimer = String(timer);
    },

    syncInputValue(inputId, helperId, { silent = false } = {}) {
        const input = $(inputId);
        const helper = $(helperId);
        if (!input || !helper) return;

        const val = String(input.value || '');
        const currentWords = countScriptWords(val);
        const currentChars = val.length;

        const { planName, maxScriptWords } = getCurrentPlanLimits();

        if (maxScriptWords && currentWords > maxScriptWords) {
            const next = trimTextToWordLimit(val, maxScriptWords);
            if (next.clipped && input.value !== next.text) {
                input.value = next.text;
                if (!silent) {
                    this.flashBlockedState(input, helper, planName, maxScriptWords);
                }
            }
        }

        const finalWords = countScriptWords(input.value);
        const finalChars = String(input.value || '').length;
        const usageRatio = maxScriptWords ? (finalWords / maxScriptWords) : 0;

        helper.classList.remove('is-hidden');
        helper.classList.toggle('is-near-limit', Boolean(maxScriptWords && usageRatio >= 0.85 && usageRatio < 1));
        helper.classList.toggle('is-at-limit', Boolean(maxScriptWords && usageRatio >= 1));
        if (!maxScriptWords || usageRatio < 1) {
            helper.classList.remove('is-blocked');
            input.classList.remove('is-word-limit-blocked');
        }

        const wordsCopy = maxScriptWords
            ? `<strong>${finalWords.toLocaleString()} / ${maxScriptWords.toLocaleString()} words</strong>`
            : `<strong>${finalWords.toLocaleString()} words</strong>`;
        const planLimitBadge = maxScriptWords ? ` • ${planName || 'Max'} limit` : '';

        helper.innerHTML = `<span><strong>${finalChars.toLocaleString()} characters</strong></span><span>${wordsCopy}${planLimitBadge}</span>`;
    },

    refreshAll(options = {}) {
        this.syncInputValue('quickCreateInput', 'quickCreateWordLimitHelper', options);
        this.syncInputValue('videoScript', 'videoScriptWordLimitHelper', options);
    },

    bind() {
        [
            ['quickCreateInput', 'quickCreateWordLimitHelper'],
            ['videoScript', 'videoScriptWordLimitHelper']
        ].forEach(([inputId, helperId]) => {
            const input = $(inputId);
            if (!input) return;

            const handleUpdate = () => {
                this.syncInputValue(inputId, helperId);
                if (inputId === 'quickCreateInput' && $('videoScript') && $('videoScript').value !== input.value) {
                    $('videoScript').value = input.value;
                    this.syncInputValue('videoScript', 'videoScriptWordLimitHelper', { silent: true });
                } else if (inputId === 'videoScript' && $('quickCreateInput') && $('quickCreateInput').value !== input.value) {
                    $('quickCreateInput').value = input.value;
                    this.syncInputValue('quickCreateInput', 'quickCreateWordLimitHelper', { silent: true });
                }
            };

            input.addEventListener('input', handleUpdate);
            input.addEventListener('keyup', handleUpdate);
            input.addEventListener('change', handleUpdate);
            input.addEventListener('paste', () => setTimeout(handleUpdate, 10));
            input.addEventListener('cut', () => setTimeout(handleUpdate, 10));
        });

        this.refreshAll({ silent: true });
    }
};

function updateScriptWordAndCharCount(inputElement) {

    const input = inputElement || $('quickCreateInput') || $('videoScript');
    if (!input) return;

    const val = String(input.value || '');
    const charCount = val.length;
    const wordCount = countScriptWords(val);

    const isQuick = input.id === 'quickCreateInput';
    const helperId = isQuick ? 'quickCreateWordLimitHelper' : 'videoScriptWordLimitHelper';
    const helper = $(helperId);

    const mirrorId = isQuick ? 'videoScript' : 'quickCreateInput';
    const mirrorInput = $(mirrorId);
    if (mirrorInput && mirrorInput.value !== val) {
        mirrorInput.value = val;
    }
    const mirrorHelper = $(isQuick ? 'videoScriptWordLimitHelper' : 'quickCreateWordLimitHelper');

    const { planName, maxScriptWords } = getCurrentPlanLimits();

    const wordsFormatted = maxScriptWords
        ? `<strong>${wordCount.toLocaleString()} / ${maxScriptWords.toLocaleString()} words</strong>`
        : `<strong>${wordCount.toLocaleString()} words</strong>`;
    const planBadge = maxScriptWords ? ` • ${planName || 'Max'} limit` : '';
    const charsFormatted = `<span><strong>${charCount.toLocaleString()} characters</strong></span>`;
    const fullHtml = `${charsFormatted}<span>${wordsFormatted}${planBadge}</span>`;

    if (helper) {
        helper.classList.remove('is-hidden');
        helper.innerHTML = fullHtml;
    }
    if (mirrorHelper) {
        mirrorHelper.classList.remove('is-hidden');
        mirrorHelper.innerHTML = fullHtml;
    }
}

window.updateScriptWordAndCharCount = updateScriptWordAndCharCount;



function formatPlanName(planKey, fallback = 'No active plan') {
    const plan = AppState.availablePlans.find((item) => item.plan_key === planKey);
    return plan?.display_name || fallback;
}

function getPlansForCycle(cycle = AppState.pricingCycle) {
    return AppState.availablePlans.filter((plan) => plan.plan_key === 'lifetime_pro');
}

function getPlanFeatures(plan = {}) {
    const features = Array.isArray(plan.feature_summary)
        ? plan.feature_summary
        : Array.isArray(plan.features)
            ? plan.features
            : [];
    return features.length ? features : ['Unlimited videos, voiceovers, and media tools.'];
}

function getPlanShortName(plan = {}) {
    return plan.name || String(plan.display_name || '').replace(/\s+Yearly$/i, '') || 'Plan';
}

function getPlanPriceLabel(plan = {}) {
    return plan.price_label || plan.price_monthly_display || plan.price_display || '';
}

function populateDropdown(elementId, options, defaultValue = null) {
    const element = $(elementId);
    if (!element) return;

    element.innerHTML = '';

    if (Array.isArray(options)) {
        options.forEach(opt => {
            const option = document.createElement('option');
            option.value = opt;
            option.textContent = opt;
            if (defaultValue && opt === defaultValue) option.selected = true;
            element.appendChild(option);
        });
    } else if (typeof options === 'object') {
        Object.entries(options).forEach(([key, value]) => {
            const option = document.createElement('option');
            option.value = key;
            option.textContent = key;
            if (defaultValue && key === defaultValue) option.selected = true;
            element.appendChild(option);
        });
    }
}

function populateSelectFromMap(elementId, map, defaultValue = null) {
    const element = $(elementId);
    if (!element) return;

    element.innerHTML = '';
    Object.entries(map || {}).forEach(([value, meta]) => {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = typeof meta === 'string' ? meta : (meta?.label || value);
        if (defaultValue && value === defaultValue) option.selected = true;
        element.appendChild(option);
    });
}

function normalizeAudiogramVoices(voices = []) {
    return (voices || [])
        .map((voice) => {
            if (typeof voice === 'string') {
                return { id: voice, label: voice, short_name: voice };
            }
            const id = voice?.id || voice?.short_name || voice?.ShortName || voice?.label || '';
            if (!id) return null;
            return {
                ...voice,
                id,
                label: voice?.label || voice?.friendly_name || voice?.FriendlyName || id,
                short_name: voice?.short_name || id
            };
        })
        .filter(Boolean);
}

function getCurrentLlmModels() {
    return AppState.dropdownData.llmModels?.providers?.[AppState.llmProvider] || {};
}

function normalizeLlmModelForProvider() { return providerDefaultModel(); }

function updateGenerationCostProfile(profile) {
    AppState.generationCostProfile = 'efficient';
    if ($('generationCostProfile')) $('generationCostProfile').value = AppState.generationCostProfile;
    if (AppState.generationCostProfile === 'efficient') AppState.llmModel = providerDefaultModel();
    syncLlmModelSelects(AppState.llmModel);
    ['llmModelSelect', 'shortLlmModel', 'bulkLongLlmModel', 'bulkShortLlmModel'].forEach(id => {
        if ($(id)) $(id).disabled = AppState.generationCostProfile === 'efficient';
    });
}

function applyBrainEngineDefaults() {
    AppState.llmProvider = 'openrouter';
    AppState.llmModel = normalizeLlmModelForProvider(AppState.llmProvider, AppState.llmModel);
    syncLlmModelSelects(AppState.llmModel);
}

function syncLlmModelSelects(preferredModel = null) {
    const configuredModels = getCurrentLlmModels();
    const modelOptions = Object.keys(configuredModels).length ? configuredModels
        : { 'Qwen 3.5 Flash (recommended)': 'qwen/qwen3.5-flash-02-23' };
    const availableModels = Object.values(modelOptions);
    if (!availableModels.length) {
        AppState.llmModel = preferredModel || AppState.llmModel || '';
        return;
    }
    const requestedModel = preferredModel
        || AppState.llmModel
        || AppState.dropdownData.llmModels?.default_model
        || availableModels[0]
        || '';
    const defaultModel = availableModels.includes(requestedModel)
        ? requestedModel
        : (availableModels[0] || '');

    ['llmModelSelect', 'shortLlmModel', 'bulkLongLlmModel', 'bulkShortLlmModel'].forEach((id) => {
        const element = $(id);
        if (!element) return;
        element.innerHTML = '';
        Object.entries(modelOptions).forEach(([label, value]) => {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = label;
            if (value === defaultModel) option.selected = true;
            element.appendChild(option);
        });
    });

    AppState.llmModel = defaultModel;
}

function togglePassword(id) {
    const input = $(id);
    input.type = input.type === 'password' ? 'text' : 'password';
}

// Toggle highlight settings panel visibility based on caption mode
function toggleHighlightPanel(modeSelectId, hlPanelId) {
    const mode = $(modeSelectId)?.value;
    const panel = $(hlPanelId);
    if (panel) {
        panel.style.display = mode === 'combined' ? 'block' : 'none';
    }
}


// =============================================================================
// NAVIGATION CONTROLLER
// =============================================================================

const NavigationController = {
    pageTitles: {
        'long-video': 'Create',
        'gallery': 'Projects',
        'queue': 'Queue',
        'editor': 'Remotion Edit',
        'profile': 'Profile',
        'guide': 'User Guide',
        'settings': 'Settings',
        'preferences': 'Preferences'
    },
    
    history: [],
    _isNavigatingBack: false,

    navigateTo(sectionId) {
        if (sectionId === 'pricing') {
            openPricingPage();
            return;
        }
        const allowedSections = new Set(['long-video', 'gallery', 'queue', 'profile', 'guide', 'settings', 'preferences', 'editor']);
        if (!allowedSections.has(sectionId)) {
            console.warn(`Unknown navigation section: ${sectionId}`);
            sectionId = 'long-video';
        }

        const protectedSections = new Set(['long-video', 'gallery', 'queue', 'editor']);
        if (protectedSections.has(sectionId) && !isStudioAccessActive()) {
            showToast(AppState.account.accessState === 'suspended'
                ? 'Your account is suspended. Contact an administrator.'
                : 'Purchase Lifetime Pro, then sign in with your purchase account to unlock the studio.', 'warning');
            if (AppState.account.accessState !== 'suspended') openUpgradeModal('Purchase Lifetime Pro to unlock video generation. Private trials are granted by the administrator.');
            return;
        }

        // Add to history
        if (AppState.currentSection && AppState.currentSection !== sectionId && !this._isNavigatingBack) {
            this.history.push(AppState.currentSection);
        }
        this._isNavigatingBack = false;

        // Update nav items
        document.querySelectorAll('.nav-item').forEach(item => {
            item.classList.toggle('active', item.dataset.section === sectionId);
        });

        // Update sections
        const targetSection = document.getElementById(`section-${sectionId}`);
        if (!targetSection) {
            console.warn(`Navigation target section not found: section-${sectionId}`);
        }
        document.querySelectorAll('.section').forEach(section => {
            section.classList.toggle('active', section.id === `section-${sectionId}`);
        });

        const contentBody = document.getElementById('contentBody');
        if (contentBody) {
            contentBody.scrollTo({ top: 0, left: 0, behavior: 'auto' });
        }

        // Update page title
        const pageTitleEl = document.getElementById('pageTitle');
        if (pageTitleEl) pageTitleEl.textContent = this.pageTitles[sectionId] || 'Vidnets Studio v1';


        // Update Back button visibility
        const backBtn = document.getElementById('headerBackBtn');
        if (backBtn) {
            if (this.history.length > 0 && sectionId !== 'long-video') {
                backBtn.classList.remove('hidden');
            } else {
                backBtn.classList.add('hidden');
            }
        }

        // Update state
        AppState.currentSection = sectionId;

        if (sectionId === "gallery") {
            GalleryController.load();
        }

        if (sectionId === 'queue') {
            QueueController.renderQueue('all');
        }

        if (sectionId === 'editor') {
            document.body.classList.add('editor-mode');
            if (window.EditorController?.init) {
                window.EditorController.init();
            }
        } else {
            document.body.classList.remove('editor-mode');
        }

        if (sectionId === 'profile') {
            AccountController.render();
        }

        if (sectionId === 'settings') {
            SettingsController.loadYouTubeSessionStatus();
        }


        if (sectionId !== 'long-video') {
            document.body.classList.remove('long-video-settings-open');
            const settingsModal = document.getElementById('videoSettingsModal');
            if (settingsModal) settingsModal.classList.add('hidden');
        }

        // Close mobile sidebar
        const sidebar = document.getElementById('sidebar');
        if (sidebar) sidebar.classList.remove('open');
    },

    goBack() {
        if (this.history.length > 0) {
            const previousSection = this.history.pop();
            this._isNavigatingBack = true;
            this.navigateTo(previousSection);
        } else if (AppState.currentSection === 'editor') {
            this.navigateTo('gallery');
        } else {
            this.navigateTo('long-video');
        }
    },

    toggleSidebar() {
        const sidebar = document.getElementById('sidebar');
        if (sidebar) sidebar.classList.toggle('open');
    }
};
window.NavigationController = NavigationController;


// =============================================================================
// ACCOUNT + PROJECTS
// =============================================================================

const AccountController = {
    entitlementPollTimer: null,

    async save() {
        localStorage.setItem(ACCOUNT_STORAGE_KEY, JSON.stringify(AppState.account));
    },

    getPlanStatus() {
        if (AppState.account.accessState === 'active') {
            return 'Studio unlocked';
        }
        if (AppState.account.accessState === 'suspended') {
            return 'Suspended';
        }
        return AppState.account.signedIn ? 'Pricing locked' : 'Signed out';
    },

    async requestPlan(planKey) {
        if (!AppState.account.signedIn) {
            showToast('Sign in with Google first.', 'warning');
            return;
        }
        try {
            const nextState = await api('/api/auth/request-plan', {
                method: 'POST',
                body: JSON.stringify({ plan_key: planKey })
            });
            AppState.account = normalizeAccountState({
                ...AppState.account,
                ...nextState,
                status: nextState.access_state === 'active' ? 'Studio unlocked' : 'Pricing locked',
                plan: nextState.plan_name || formatPlanName(planKey, AppState.account.plan),
                planKey: nextState.plan_key || planKey,
                pendingPlanKey: nextState.pending_plan_key || nextState.requested_plan_key || planKey,
                requestedPlanName: nextState.pending_plan_name || nextState.requested_plan_name || formatPlanName(planKey, planKey),
                planStatus: nextState.plan_status || 'pending',
                accessState: nextState.access_state || 'pending',
                accessCopy: nextState.access_copy || AppState.account.accessCopy,
                sessionCopy: nextState.access_copy || AppState.account.sessionCopy,
                deviceId: nextState.device_id || AppState.account.deviceId,
                deviceStatus: nextState.device_status || AppState.account.deviceStatus,
                deviceCount: nextState.device_count ?? AppState.account.deviceCount,
                deviceLimit: nextState.device_limit ?? AppState.account.deviceLimit
            });
            this.save();
            this.render();
            showToast('Plan request sent. An admin will need to activate it.', 'success');
        } catch (error) {
            showToast(error.message || 'Could not request plan.', 'error');
        }
    },

    async logout() {
        this.stopEntitlementPolling();
        AppState.account = normalizeAccountState({
            ...getDefaultAccountState(),
            signedIn: false,
            name: 'Guest Creator',
            status: 'Signed out',
            plan: 'No active plan',
            sessionLabel: 'Signed out of cloud session',
            sessionCopy: 'Use the front page to sign in again.'
        });
        this.save();
        this.render();
        showToast('Signed out', 'info');
        if (window.electronAPI?.logoutToLanding) {
            await window.electronAPI.logoutToLanding();
        } else {
            navigateTo('long-video');
        }
    },

    async refreshEntitlement({ silent = true } = {}) {
        if (!AppState.account.signedIn || !window.electronAPI?.getAuthState) {
            return false;
        }
        const wasActive = isStudioAccessActive();
        if (!silent && window.electronAPI?.refreshAuthState) {
            await window.electronAPI.refreshAuthState();
        }
        const changed = await syncDesktopAuthState();
        if (!changed) {
            return false;
        }
        this.render();
        const isActive = isStudioAccessActive();
        if (!silent) {
            showToast(AppState.account.accessCopy || 'Account access refreshed.', isActive ? 'success' : 'info');
        } else if (wasActive !== isActive) {
            showToast(isActive ? 'Plan activated. Studio is unlocked.' : (AppState.account.accessCopy || 'Plan access changed.'), isActive ? 'success' : 'warning');
        }
        if (!isActive && wasActive && AppState.account.accessState !== 'suspended' && AppState.currentSection !== 'profile') {
            openPricingPage();
        }
        return true;
    },

    startEntitlementPolling() {
        if (this.entitlementPollTimer || !window.electronAPI?.getAuthState) {
            return;
        }
        this.entitlementPollTimer = setInterval(() => {
            if (document.hidden) return;
            this.refreshEntitlement({ silent: true }).catch((error) => {
                console.warn('Entitlement refresh failed', error);
            });
        }, ENTITLEMENT_SYNC_INTERVAL_MS);

        window.addEventListener('focus', () => {
            this.refreshEntitlement({ silent: true }).catch((error) => {
                console.warn('Entitlement focus refresh failed', error);
            });
        });
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden) {
                this.refreshEntitlement({ silent: true }).catch((error) => {
                    console.warn('Entitlement visibility refresh failed', error);
                });
            }
        });
    },

    stopEntitlementPolling() {
        if (this.entitlementPollTimer) {
            clearInterval(this.entitlementPollTimer);
            this.entitlementPollTimer = null;
        }
    },

    render() {
        const planLimitSummary = getPlanLimitSummary();
        const deviceSummary = Number.isFinite(Number(AppState.account.deviceCount))
            ? `Device ${AppState.account.deviceCount} of ${AppState.account.deviceLimit || 2}.`
            : '';
        const sessionCopy = [AppState.account.sessionCopy, deviceSummary].filter(Boolean).join(' ');
        const requestCopy = AppState.account.pendingPlanKey
            ? ` Requested: ${AppState.account.requestedPlanName || formatPlanName(AppState.account.pendingPlanKey, AppState.account.pendingPlanKey)}.`
            : '';
        const planCopy = planLimitSummary
            ? `${AppState.account.accessCopy || AppState.account.sessionCopy} ${planLimitSummary}${requestCopy}`.trim()
            : `${AppState.account.accessCopy || AppState.account.sessionCopy}${requestCopy}`.trim();
        $('sidebarProfileName') && ($('sidebarProfileName').textContent = AppState.account.name);
        $('sidebarProfileStatus') && ($('sidebarProfileStatus').textContent = this.getPlanStatus());
        $('sidebarPlanBadge') && ($('sidebarPlanBadge').textContent = AppState.account.plan);
        $('pricingCurrentPlan') && ($('pricingCurrentPlan').textContent = AppState.account.plan);
        $('pricingCurrentCopy') && ($('pricingCurrentCopy').textContent = planCopy);
        $('profileNameValue') && ($('profileNameValue').textContent = AppState.account.name);
        $('profileStatusValue') && ($('profileStatusValue').textContent = this.getPlanStatus());
        $('profilePlanValue') && ($('profilePlanValue').textContent = AppState.account.plan);
        $('profilePlanCopy') && ($('profilePlanCopy').textContent = planCopy);
        $('profileThemeValue') && ($('profileThemeValue').textContent = AppState.theme === 'light' ? 'Light' : 'Dark');
        $('profileQueueValue') && ($('profileQueueValue').textContent = `${AppState.longQueue.length} item${AppState.longQueue.length === 1 ? '' : 's'}`);
        $('profileSessionValue') && ($('profileSessionValue').textContent = AppState.account.sessionLabel);
        $('profileSessionCopy') && ($('profileSessionCopy').textContent = sessionCopy);
        renderProfileUsage(AppState.account);
        $('accessBannerTitle') && ($('accessBannerTitle').textContent = AppState.account.accessState === 'suspended'
            ? 'Your account is suspended'
            : 'Lifetime Pro required');
        $('accessBannerCopy') && ($('accessBannerCopy').textContent = AppState.account.accessState === 'suspended' ? AppState.account.accessCopy : 'Purchase Lifetime Pro, then sign in with the same account and refresh access. Private trials are available only through the administrator.');
        $('accessBanner')?.classList.toggle('hidden', isStudioAccessActive());

        document.querySelectorAll('[data-plan-card]').forEach((card) => {
            card.classList.toggle('active', card.dataset.planCard === AppState.account.planKey);
        });

        document.querySelectorAll('[data-plan-action]').forEach((button) => {
            const isCurrentPlan = button.dataset.planAction === AppState.account.planKey && AppState.account.planStatus === 'active';
            const isPendingPlan = button.dataset.planAction === AppState.account.pendingPlanKey
                || (button.dataset.planAction === AppState.account.planKey && AppState.account.planStatus === 'pending');
            button.textContent = isCurrentPlan ? 'Current Plan' : isPendingPlan ? 'Requested' : 'Request Plan';
            button.disabled = isCurrentPlan || isPendingPlan;
        });

        syncPlanDurationOptions();
        PlanWordLimitController.refreshAll({ silent: true });
    }
};

const GalleryController = {
    pendingAutoOpen: null,
    selectedProjectId: null,
    loadGeneration: 0,

    queueAutoOpen(videoUrl, name, path = '') {
        if (!videoUrl) return;
        this.pendingAutoOpen = {
            url: videoUrl,
            name: name || deriveFilenameFromUrl(videoUrl),
            filename: deriveFilenameFromUrl(videoUrl),
            path: path || ''
        };
    },

    playProject(project) {
        this.selectProject(project, { openPlayer: true });
    },

    selectProject(project, { openPlayer = false } = {}) {
        const normalized = normalizeProject(project);
        if (!normalized?.url) return;
        this.selectedProjectId = projectIdentity(normalized);
        AppState.selectedProjectId = this.selectedProjectId;

        const gallery = $('generatedVideosGallery');
        gallery?.querySelectorAll('.gallery-item').forEach((item) => {
            item.classList.toggle('active', item.dataset.projectId === this.selectedProjectId);
            item.setAttribute('aria-selected', item.dataset.projectId === this.selectedProjectId ? 'true' : 'false');
        });

        const panel = $('projectPreviewPanel');
        const player = $('projectPreviewPlayer');
        const title = $('projectPreviewTitle');
        const meta = $('projectPreviewMeta');
        const openButton = $('projectPreviewOpenButton');
        const downloadButton = $('projectPreviewDownloadButton');
        if (panel && player) {
            panel.classList.remove('hidden');
            panel.setAttribute('aria-hidden', 'false');
            if (title) title.textContent = normalized.name;
            if (meta) meta.textContent = `${formatProjectDate(normalized.created_at)} · ${formatFileSize(normalized.size)}`;
            player.poster = normalized.thumbnail_url || '';
            player.onerror = () => showToast(`Preview could not be opened (${normalized.name}).`, 'error');
            if (player.src !== new URL(normalized.url, window.location.href).href) {
                player.src = normalized.url;
                player.load();
            }
            if (openButton) openButton.onclick = () => playVideo(normalized);
            if (downloadButton) {
                downloadButton.href = normalized.url;
                downloadButton.download = normalized.filename || normalized.name;
            }
        }

        if (openPlayer) playVideo(normalized);
    },

    clearPreview() {
        this.selectedProjectId = null;
        AppState.selectedProjectId = null;
        const panel = $('projectPreviewPanel');
        const player = $('projectPreviewPlayer');
        if (panel) {
            panel.classList.add('hidden');
            panel.setAttribute('aria-hidden', 'true');
        }
        if (player) {
            player.pause();
            player.removeAttribute('src');
            player.removeAttribute('poster');
            player.load();
        }
    },

    async load() {
        const requestGeneration = ++this.loadGeneration;
        try {
            const gallery = $('generatedVideosGallery');
            if (!gallery) return;
            
            gallery.innerHTML = '<div class="loading-state">Loading projects...</div>';
            
            const rawProjects = await api('/api/projects');
            if (requestGeneration !== this.loadGeneration) return;
            // Keep every distinct export. A stable backend id is preferred,
            // while the path/url fallback protects older servers that do not
            // yet return ids.
            const seen = new Set();
            const projects = (Array.isArray(rawProjects) ? rawProjects : [])
                .map(normalizeProject)
                .filter((project) => {
                    // Prefer the canonical file identity. Older packaged
                    // servers returned the same inode/id for every Windows
                    // file, and using that id alone collapsed the gallery to
                    // one card.
                    const key = String(project.path || project.url || project.id || '');
                    if (!key || seen.has(key)) return false;
                    seen.add(key);
                    return Boolean(project.url);
                });
            AppState.projects = projects;
            if ($('projectsCount')) $('projectsCount').textContent = String(projects.length);
            gallery.innerHTML = '';
            
            if (projects.length === 0) {
                this.clearPreview();
                gallery.innerHTML = '<div class="empty-state">No exported videos yet. Start creating!</div>';
                return;
            }
            
            projects.forEach(project => {
                const item = document.createElement('article');
                item.className = 'gallery-item';
                item.dataset.projectId = projectIdentity(project);
                item.tabIndex = 0;
                item.setAttribute('role', 'option');
                item.setAttribute('aria-selected', this.selectedProjectId === projectIdentity(project) ? 'true' : 'false');
                item.onclick = () => this.selectProject(project);
                item.onkeydown = (event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        this.selectProject(project);
                    }
                };
                if (this.selectedProjectId === projectIdentity(project) || this.pendingAutoOpen?.url === project.url) {
                    item.classList.add('active');
                }
                
                const thumb = document.createElement('div');
                thumb.className = 'gallery-thumb';
                const thumbUrl = project.thumbnail_url || '';
                if (thumbUrl) {
                    const image = document.createElement('img');
                    image.src = thumbUrl;
                    image.className = 'gallery-thumb-img';
                    image.alt = `${project.name} thumbnail`;
                    image.loading = 'lazy';
                    image.addEventListener('error', () => {
                        image.remove();
                        thumb.classList.add('has-thumb-error');
                        const fallback = document.createElement('div');
                        fallback.className = 'gallery-thumb-placeholder';
                        fallback.innerHTML = '<span class="gallery-thumb-placeholder-icon">▣</span><span>Preview unavailable</span>';
                        thumb.insertBefore(fallback, thumb.firstChild);
                    }, { once: true });
                    thumb.appendChild(image);
                } else {
                    const fallback = document.createElement('div');
                    fallback.className = 'gallery-thumb-placeholder';
                    fallback.innerHTML = '<span class="gallery-thumb-placeholder-icon">▣</span><span>Generating preview…</span>';
                    thumb.appendChild(fallback);
                }
                const overlay = document.createElement('div');
                overlay.className = 'gallery-thumb-overlay';
                const playIcon = document.createElement('span');
                playIcon.className = 'gallery-thumb-play-icon';
                playIcon.textContent = '▶';
                overlay.appendChild(playIcon);
                thumb.appendChild(overlay);


                
                const info = document.createElement('div');
                info.className = 'gallery-info';
                
                const name = document.createElement('div');
                name.className = 'gallery-name';
                name.textContent = project.name;
                
                const meta = document.createElement('div');
                meta.className = 'gallery-meta';
                meta.innerHTML = `<span>${formatProjectDate(project.created_at)}</span><span>${formatFileSize(project.size)}</span>`;

                const statusRow = document.createElement('div');
                statusRow.className = 'gallery-status-row';

                const chip = document.createElement('span');
                chip.className = 'gallery-chip';
                chip.textContent = 'Playable';

                const action = document.createElement('button');
                action.className = 'btn-secondary btn-small gallery-action';
                action.textContent = 'Open Player';
                action.onclick = (event) => {
                    event.stopPropagation();
                    this.playProject(project);
                };
                
                info.appendChild(name);
                info.appendChild(meta);
                statusRow.appendChild(chip);
                statusRow.appendChild(action);
                info.appendChild(statusRow);
                item.appendChild(thumb);
                item.appendChild(info);
                gallery.appendChild(item);
            });

            if (this.pendingAutoOpen) {
                const pendingProject = projects.find((project) =>
                    project.url === this.pendingAutoOpen.url
                    || (this.pendingAutoOpen.path && project.path === this.pendingAutoOpen.path)
                );
                const pending = pendingProject || this.pendingAutoOpen;
                this.pendingAutoOpen = null;
                this.selectProject(pending, { openPlayer: true });
            } else if (this.selectedProjectId) {
                const selected = projects.find((project) => projectIdentity(project) === this.selectedProjectId);
                if (selected) this.selectProject(selected);
            }
        } catch (error) {
            if (requestGeneration !== this.loadGeneration) return;
            console.error('Error loading gallery:', error);
            const gallery = $('generatedVideosGallery');
            if ($('projectsCount')) $('projectsCount').textContent = '—';
            if (gallery) {
                gallery.innerHTML = `<div class="empty-state">Could not load projects: ${escapeHtml(error.message || 'Unknown error')}</div>`;
            }
            showToast('Failed to load gallery', 'error');
        }
    }
};

// =============================================================================
// VIDEO PLAYER ACTIONS
// =============================================================================

function deriveFilenameFromUrl(url = '') {
    return decodeURIComponent((url.split('/').pop() || '').split('?')[0] || 'Video.mp4');
}

function resolveVideoRecord(video) {
    if (typeof video === 'string') {
        return {
            name: video,
            filename: video,
            url: `/output/${encodeURIComponent(video)}`
        };
    }

    if (!video || typeof video !== 'object') {
        return null;
    }

    const url = video.url || video.video_url || video.video || '';
    const filename = video.filename || deriveFilenameFromUrl(url) || video.name || 'Video.mp4';
    return {
        name: video.name || filename,
        filename,
        url: url || `/output/${encodeURIComponent(filename)}`,
        path: video.path || video.video_path || '',
        root: video.root || ''
    };
}

function playVideo(video) {
    const record = resolveVideoRecord(video);
    if (!record?.url) return;

    AppState.currentVideo = record;
    const modal = $('videoPlayerModal');
    const player = $('mainVideoPlayer');
    const title = $('videoPlayerTitle');
    
    if (!modal || !player) return;
    
    player.onerror = null;
    player.dataset.playFallbackTried = '0';
    player.onerror = () => {
        try {
            if (player.dataset.playFallbackTried === '0' && record.path) {
                player.dataset.playFallbackTried = '1';
                const localUrl = `local-media://${String(record.path).replace(/\\/g, '/')}`;
                player.src = localUrl;
                player.load();
                player.play().catch(() => {});
                showToast('Falling back to local playback for this file.', 'info');
                return;
            }
        } catch (_error) {
            // Fall through to the error toast.
        }
        showToast(`Video could not be opened (${record.name}).`, 'error');
    };

    title.textContent = record.name;
    player.src = record.url;
    modal.classList.remove('hidden');
    
    // Auto-play when video loads
    player.load();
    player.play().catch(() => {});
    
    // Setup controls
    setupVideoControls(player);
}

function closeVideoPlayer() {
    const modal = $('videoPlayerModal');
    const player = $('mainVideoPlayer');
    if (modal) modal.classList.add('hidden');
    if (player) {
        player.pause();
        player.removeAttribute('src');
        player.load(); // Immediately flushes Chromium GPU media pipeline
    }
    // Remove controls listener
    if (AppState._videoControlsCleanup) {
        AppState._videoControlsCleanup();
        AppState._videoControlsCleanup = null;
    }
}


// ── Video Player Controls ──────────────────────────────────────────
function setupVideoControls(player) {
    // Cleanup previous
    if (AppState._videoControlsCleanup) {
        AppState._videoControlsCleanup();
    }

    const playIcon = $('playIcon');
    const pauseIcon = $('pauseIcon');
    const progressFill = $('videoProgressFill');
    const progressContainer = $('videoProgressContainer');
    const timeDisplay = $('videoTimeDisplay');
    const volumeOnIcon = $('volumeOnIcon');
    const volumeOffIcon = $('volumeOffIcon');
    const overlay = $('videoControlsOverlay');

    function updatePlayPauseIcon() {
        if (player.paused) {
            playIcon.style.display = 'block';
            pauseIcon.style.display = 'none';
        } else {
            playIcon.style.display = 'none';
            pauseIcon.style.display = 'block';
        }
    }

    function updateVolumeIcon() {
        if (player.muted || player.volume === 0) {
            volumeOnIcon.style.display = 'none';
            volumeOffIcon.style.display = 'block';
        } else {
            volumeOnIcon.style.display = 'block';
            volumeOffIcon.style.display = 'none';
        }
    }

    function formatTime(seconds) {
        if (isNaN(seconds)) return '0:00';
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    }

    function updateTimeDisplay() {
        const current = player.currentTime || 0;
        const duration = player.duration || 0;
        if (timeDisplay) {
            timeDisplay.textContent = `${formatTime(current)} / ${formatTime(duration)}`;
        }
    }

    function updateProgress() {
        const duration = player.duration;
        if (duration && progressFill) {
            progressFill.style.width = `${(player.currentTime / duration) * 100}%`;
        }
        updateTimeDisplay();
    }

    // Event listeners
    const onPlayPause = () => updatePlayPauseIcon();
    const onTimeUpdate = () => updateProgress();
    const onLoadedMetadata = () => {
        updatePlayPauseIcon();
        updateTimeDisplay();
        // Start muted for autoplay
        player.muted = false;
        updateVolumeIcon();
    };
    const onVolumeChange = () => updateVolumeIcon();

    player.addEventListener('play', onPlayPause);
    player.addEventListener('pause', onPlayPause);
    player.addEventListener('timeupdate', onTimeUpdate);
    player.addEventListener('loadedmetadata', onLoadedMetadata);
    player.addEventListener('volumechange', onVolumeChange);

    // Click on video to toggle play/pause
    const onVideoClick = (e) => {
        if (e.target === player) {
            if (player.paused) {
                player.play().catch(() => {});
            } else {
                player.pause();
            }
        }
    };
    player.addEventListener('click', onVideoClick);

    // Progress bar click to seek
    const onProgressClick = (e) => {
        if (!player.duration) return;
        const rect = progressContainer.getBoundingClientRect();
        const pos = (e.clientX - rect.left) / rect.width;
        player.currentTime = pos * player.duration;
    };
    if (progressContainer) {
        progressContainer.addEventListener('click', onProgressClick);
    }

    // Show controls on mouse move, hide after 3s
    let hideTimeout;
    const onMouseMove = () => {
        if (overlay) {
            overlay.classList.add('visible');
        }
        clearTimeout(hideTimeout);
        hideTimeout = setTimeout(() => {
            if (!player.paused && overlay) {
                overlay.classList.remove('visible');
            }
        }, 3000);
    };
    const container = $('videoContainer');
    if (container) {
        container.addEventListener('mousemove', onMouseMove);
    }

    // Keyboard shortcuts
    const onKeyDown = (e) => {
        const modal = $('videoPlayerModal');
        if (!modal || modal.classList.contains('hidden')) return;
        switch (e.key) {
            case ' ':
            case 'k':
                e.preventDefault();
                if (player.paused) player.play().catch(() => {});
                else player.pause();
                break;
            case 'ArrowLeft':
                e.preventDefault();
                player.currentTime = Math.max(0, player.currentTime - 10);
                break;
            case 'ArrowRight':
                e.preventDefault();
                player.currentTime = Math.min(player.duration || 0, player.currentTime + 10);
                break;
            case 'm':
            case 'M':
                player.muted = !player.muted;
                break;
            case 'Escape':
                closeVideoPlayer();
                break;
        }
    };

    document.addEventListener('keydown', onKeyDown);

    // Cleanup function
    AppState._videoControlsCleanup = () => {
        player.removeEventListener('play', onPlayPause);
        player.removeEventListener('pause', onPlayPause);
        player.removeEventListener('timeupdate', onTimeUpdate);
        player.removeEventListener('loadedmetadata', onLoadedMetadata);
        player.removeEventListener('volumechange', onVolumeChange);
        player.removeEventListener('click', onVideoClick);
        if (progressContainer) {
            progressContainer.removeEventListener('click', onProgressClick);
        }
        if (container) {
            container.removeEventListener('mousemove', onMouseMove);
        }
        document.removeEventListener('keydown', onKeyDown);
        clearTimeout(hideTimeout);
    };
}

function toggleVideoPlayPause() {
    const player = $('mainVideoPlayer');
    if (!player) return;
    if (player.paused) {
        player.play().catch(() => {});
    } else {
        player.pause();
    }
}

function videoSeek(seconds) {
    const player = $('mainVideoPlayer');
    if (!player || !player.duration) return;
    player.currentTime = Math.max(0, Math.min(player.duration, player.currentTime + seconds));
}

function toggleVideoMute() {
    const player = $('mainVideoPlayer');
    if (!player) return;
    player.muted = !player.muted;
}

async function deleteCurrentVideo() {
    if (!AppState.currentVideo?.filename) return;
    
    if (!confirm(`Are you sure you want to delete ${AppState.currentVideo.filename}?`)) return;
    
    try {
        await api('/api/projects/delete', {
            method: 'POST',
            body: JSON.stringify({
                filename: AppState.currentVideo.filename,
                root: AppState.currentVideo.root || ''
            })
        });
        
        showToast('Video deleted');
        closeVideoPlayer();
        GalleryController.load();
    } catch (error) {
        showToast('Delete failed', 'error');
    }
}

function downloadCurrentVideo() {
    if (!AppState.currentVideo?.filename) return;
    if (window.electronAPI?.selectFileInExplorer) {
        window.electronAPI.selectFileInExplorer(AppState.currentVideo.path || AppState.currentVideo.filename);
    } else if (window.electronAPI?.openOutputFolder) {
        window.electronAPI.openOutputFolder();
    } else {
        showToast(`File saved as ${AppState.currentVideo.filename} in the output folder`, 'info');
    }
}

// Global navigation functions
function navigateTo(section) {
    NavigationController.navigateTo(section);
}

function toggleSidebar() {
    NavigationController.toggleSidebar();
}

// =============================================================================
// SETTINGS CONTROLLER
// =============================================================================

const SettingsController = {
    async load() {
        const legacySaved = localStorage.getItem('vidzeo_settings');
        let secureSettings = null;
        try {
            secureSettings = await withUiTimeout(window.electronAPI?.loadSecretSettings?.(), 8000, 'Loading saved settings');
        } catch (error) {
            console.error('Failed to load secure settings:', error);
        }
        const saved = secureSettings && Object.keys(secureSettings).length
            ? JSON.stringify(secureSettings)
            : legacySaved;
        if (saved) {
            try {
                const settings = JSON.parse(saved);
                console.log('Loading secure settings');

                const keys = settings.apiKeys || settings;
                AppState.apiKeys = {
                    openrouter: keys.openrouter || settings.openrouterApiKey || '',
                    pollinations: keys.pollinations || settings.pollinationsApiKey || '',
                    pexels: keys.pexels || settings.pexelsApiKey || '',
                    pixabay: keys.pixabay || settings.pixabayApiKey || '',
                    together: keys.together || settings.togetherApiKey || '',
                    openai: keys.openai || settings.openaiApiKey || ''
                };
                AppState.llmProvider = 'openrouter';
                AppState.llmModel = normalizeLlmModelForProvider(AppState.llmProvider, settings.llmModel);
                AppState.generationCostProfile = settings.generationCostProfile === 'custom' ? 'custom' : 'efficient';
                updateGenerationCostProfile(AppState.generationCostProfile);

                // Populate fields
                if ($('openrouterKey')) $('openrouterKey').value = AppState.apiKeys.openrouter || '';

                if ($('pexelsKey')) $('pexelsKey').value = AppState.apiKeys.pexels || '';
                if ($('pixabayKey')) $('pixabayKey').value = AppState.apiKeys.pixabay || '';
                applyBrainEngineDefaults();
                if ($('llmProviderSelect')) $('llmProviderSelect').value = AppState.llmProvider;
                syncLlmModelSelects(AppState.llmModel || providerDefaultModel());

                this.updateApiStatus();
                if (legacySaved && window.electronAPI?.saveSecretSettings) {
                    await withUiTimeout(window.electronAPI.saveSecretSettings(settings), 8000, 'Migrating saved settings');
                    localStorage.removeItem('vidzeo_settings');
                }
            } catch (e) {
                console.error('Failed to load settings:', e);
            }
        } else {
            console.log('📦 No saved settings found in localStorage');
        }

        applyBrainEngineDefaults();

        const orInput = $('openrouterKey');
        if (orInput && !orInput._boundORListener) {
            orInput._boundORListener = true;
            orInput.addEventListener('change', () => {
                const val = orInput.value.trim();
                AppState.apiKeys.openrouter = val;
                AppState.openrouterStatus = { checkedAt: 0, payload: null };
                if (val) {
                    void checkOpenRouterAccess({ force: true, notify: false, keyOverride: val });
                } else {
                    this.updateApiStatus();
                }
            });
        }

        if (AppState.llmProvider === 'openrouter') void checkOpenRouterAccess({ notify: false });
        this.loadYouTubeSessionStatus();
        renderProviderPanels();
        void LocalAgent.refresh();
    },

    _appUpdateListenerReady: false,

    renderAppUpdateState(state = {}) {
        const version = $('appUpdateVersion');
        const status = $('appUpdateStatus');
        const checkButton = $('appUpdateCheckBtn');
        const downloadButton = $('appUpdateDownloadBtn');
        const installButton = $('appUpdateInstallBtn');
        const phase = String(state.phase || 'idle').toLowerCase();
        const current = String(state.currentVersion || '').trim();
        const latest = String(state.latestVersion || '').trim();
        if (version) version.textContent = current ? `v${current}` : 'Current version';
        if (checkButton) checkButton.disabled = phase === 'checking' || phase === 'downloading' || phase === 'installing';
        if (downloadButton) downloadButton.classList.toggle('hidden', phase !== 'available');
        if (installButton) installButton.classList.toggle('hidden', !state.downloaded || phase === 'installing');
        if (!status) return;
        if (phase === 'checking') {
            status.textContent = 'Checking the official Vidzeo release channel…';
        } else if (phase === 'available') {
            status.textContent = latest ? `Vidnets Studio v${latest} is ready to download.` : 'A verified Vidnets update is available.';
        } else if (phase === 'downloading') {
            status.textContent = `Downloading verified update… ${Number(state.progress || 0)}%`;
        } else if (phase === 'ready') {
            status.textContent = 'Update downloaded and integrity-checked. Install it when you are ready; Vidzeo will restart.';
        } else if (phase === 'installing') {
            status.textContent = 'Restarting Vidnets Studio to finish the update…';
        } else if (phase === 'up_to_date') {
            status.textContent = latest ? `You are up to date (v${latest}).` : 'You are up to date.';
        } else if (phase === 'error') {
            status.textContent = state.error || 'Update check failed. You can keep using the current version.';
        } else {
            status.textContent = 'Check the official Vidzeo release channel for a verified update.';
        }
    },

    async loadAppUpdateState() {
        try {
            const state = await window.electronAPI?.getAppUpdateState?.();
            if (state) this.renderAppUpdateState(state);
        } catch (error) {
            console.warn('Could not load app update state:', error);
        }
        if (!this._appUpdateListenerReady) {
            this._appUpdateListenerReady = true;
            window.electronAPI?.onAppUpdateStateChanged?.((state) => this.renderAppUpdateState(state));
        }
    },

    async checkForAppUpdate() {
        const button = $('appUpdateCheckBtn');
        if (button) button.disabled = true;
        try {
            const state = await window.electronAPI?.checkForAppUpdate?.();
            this.renderAppUpdateState(state || { phase: 'error', error: 'Update service is unavailable.' });
            if (state?.phase === 'available') showToast(`Vidnets Studio v${state.latestVersion} is available.`, 'info');
            else if (state?.phase === 'up_to_date') showToast('Vidnets Studio is up to date.', 'success');
        } catch (error) {
            this.renderAppUpdateState({ phase: 'error', error: error.message || 'Update check failed.' });
        } finally {
            if (button) button.disabled = false;
        }
    },

    async downloadAppUpdate() {
        try {
            const state = await window.electronAPI?.downloadAppUpdate?.();
            this.renderAppUpdateState(state || {});
            if (state?.phase === 'ready') showToast('Verified update downloaded.', 'success');
            else if (state?.phase === 'error') showToast(state.error || 'Update download failed.', 'error');
        } catch (error) {
            this.renderAppUpdateState({ phase: 'error', error: error.message || 'Update download failed.' });
        }
    },

    async installAppUpdate() {
        try {
            const state = await window.electronAPI?.installAppUpdate?.();
            this.renderAppUpdateState(state || {});
        } catch (error) {
            this.renderAppUpdateState({ phase: 'error', error: error.message || 'Update install failed.' });
        }
    },

    async save() {
        const rawKey = String($('openrouterKey')?.value || '').trim();
        AppState.apiKeys.openrouter = rawKey;

        AppState.apiKeys.pexels = $('pexelsKey')?.value || '';
        AppState.apiKeys.pixabay = $('pixabayKey')?.value || '';
        AppState.llmProvider = 'openrouter';
        AppState.generationCostProfile = $('generationCostProfile')?.value || 'efficient';
        AppState.llmModel = $('llmModelSelect')?.value || 'qwen/qwen3.5-flash-02-23';
        applyBrainEngineDefaults();

        const settings = {
            apiKeys: { ...AppState.apiKeys },
            llmProvider: AppState.llmProvider,
            llmModel: AppState.llmModel,
            generationCostProfile: AppState.generationCostProfile
        };

        try {
            if (!window.electronAPI?.saveSecretSettings) throw new Error('Open the Vidnets desktop app to save API keys securely.');
            await withUiTimeout(window.electronAPI.saveSecretSettings(settings), 8000, 'Saving API access');
            localStorage.removeItem('vidzeo_settings');
        } catch (error) {
            console.error('Failed to save secure settings:', error);
            showToast('Settings could not be saved securely', 'error');
            return;
        }

        AppState.openrouterStatus = { checkedAt: 0, payload: null };
        this.updateApiStatus();
        if (rawKey) {
            const check = await checkOpenRouterAccess({ force: true, notify: false, keyOverride: rawKey });
            if (check?.ok) {
                showToast('Settings saved & OpenRouter connected!', 'success');
            } else {
                showToast(`Settings saved. ${check?.message || 'Check OpenRouter key.'}`, 'warning');
            }
        } else {
            showToast('Settings saved successfully!', 'success');
        }
    },

    updateApiStatus() {
        const openrouterStatus = $('openrouterStatus');
        const pollinationsStatus = $('pollinationsStatus');
        const pexelsStatus = $('pexelsStatus');
        const pixabayStatus = $('pixabayStatus');

        if (openrouterStatus) {
            if (AppState.openrouterStatus?.payload) {
                renderOpenRouterAccessStatus(AppState.openrouterStatus.payload);
            } else if (AppState.apiKeys.openrouter) {
                openrouterStatus.textContent = 'API key configured. Text and vision use Qwen 3.5 Flash.';
                openrouterStatus.className = 'api-status set';
            } else {
                openrouterStatus.textContent = 'Qwen 3.5 Flash for scripts, planners, directors, and vision.';
                openrouterStatus.className = 'api-status';
            }
        }


        if (pollinationsStatus) {
            if (AppState.apiKeys.pollinations) {
                pollinationsStatus.textContent = '✓ API Key configured';
                pollinationsStatus.className = 'api-status set';
            } else {
                pollinationsStatus.textContent = 'Optional - enhances image generation speed';
                pollinationsStatus.className = 'api-status';
            }
        }

        if (pexelsStatus) {
            if (AppState.apiKeys.pexels) {
                pexelsStatus.textContent = '✓ Pexels stock API configured';
                pexelsStatus.className = 'api-status set';
            } else {
                pexelsStatus.textContent = 'Optional - enables Pexels stock video search';
                pexelsStatus.className = 'api-status';
            }
        }

        if (pixabayStatus) {
            if (AppState.apiKeys.pixabay) {
                pixabayStatus.textContent = '✓ Pixabay stock API configured';
                pixabayStatus.className = 'api-status set';
            } else {
                pixabayStatus.textContent = 'Optional - enables Pixabay stock video search';
                pixabayStatus.className = 'api-status';
            }
        }
    },

    async testConnection() {
        try {
            const result = await api('/api/status');
            const ttsReady = result.tts_ready ?? result.tts ?? false;
            const ffmpegReady = result.ffmpeg_ready ?? result.ffmpeg ?? false;

            // Update status indicators
            const ttsStatus = $('statusTTS');
            const ffmpegStatus = $('statusFFmpeg');

            if (ttsStatus) {
                const dot = ttsStatus.querySelector('.status-dot');
                if (dot) dot.className = `status-dot ${ttsReady ? 'green' : 'yellow'}`;
            }

            if (ffmpegStatus) {
                const dot = ffmpegStatus.querySelector('.status-dot');
                if (dot) dot.className = `status-dot ${ffmpegReady ? 'green' : 'red'}`;
            }

            showToast('Connection successful!', 'success');
        } catch (error) {
            showToast('Connection failed: ' + error.message, 'error');
        }
    },

    async loadYouTubeSessionStatus() {
        const dot = $('ytStatusDot');
        const title = $('ytStatusTitle');
        const subtitle = $('ytStatusSubtitle');
        const disconnectBtn = $('ytDisconnectBtn');
        const chromeBtn = $('ytConnectChromeBtn');
        const edgeBtn = $('ytConnectEdgeBtn');

        try {
            const data = await api('/api/youtube/session/status');
            const session = data.session || {};
            updateYouTubeCookieNotice(session);
            if (session.ready && session.verified_authenticated) {
                if (dot) dot.className = 'yt-status-dot connected';
                if (title) title.textContent = `Connected (${session.source || 'YouTube Session'})`;
                if (subtitle) {
                    const cookieCount = session.cookie_count ? `${session.cookie_count} cookies` : 'Active';
                    const browser = (session.browser || 'Browser').toUpperCase();
                    subtitle.textContent = `✓ 1080p clip extraction active (${browser}, ${cookieCount})`;
                }
                if (disconnectBtn) disconnectBtn.classList.remove('hidden');
                if (chromeBtn) chromeBtn.textContent = '🔄 Reconnect Chrome';
                if (edgeBtn) edgeBtn.textContent = '🔄 Reconnect Edge';
            } else if (session.cookie_update_required) {
                if (dot) dot.className = 'yt-status-dot warning';
                if (title) title.textContent = 'Session Expired / Needs Renewal';
                if (subtitle) subtitle.textContent = session.cookie_update_reason || 'Click Reconnect or re-import cookies to refresh.';
                if (disconnectBtn) disconnectBtn.classList.remove('hidden');
                if (chromeBtn) chromeBtn.textContent = '🌐 Reconnect Chrome';
                if (edgeBtn) edgeBtn.textContent = '🌐 Reconnect Edge';
            } else {
                if (dot) dot.className = 'yt-status-dot disconnected';
                if (title) title.textContent = 'Not Connected (Public Search Mode)';
                if (subtitle) subtitle.textContent = 'Connect Chrome/Edge or import cookies to enable guaranteed 1080p downloads and prevent 403 blocks.';
                if (disconnectBtn) disconnectBtn.classList.add('hidden');
                if (chromeBtn) chromeBtn.textContent = '🌐 Connect Chrome';
                if (edgeBtn) edgeBtn.textContent = '🌐 Connect Edge';
            }
        } catch (e) {
            console.warn('Failed to load YouTube session status:', e);
            updateYouTubeCookieNotice({}, true);
            if (dot) dot.className = 'yt-status-dot disconnected';
            if (title) title.textContent = 'Public Search Mode';
            if (subtitle) subtitle.textContent = 'Standard search active.';
            if (disconnectBtn) disconnectBtn.classList.add('hidden');
        }
    },

    async connectYouTubeBrowser(browser = 'chrome') {
        const title = $('ytStatusTitle');
        const subtitle = $('ytStatusSubtitle');
        const chromeBtn = $('ytConnectChromeBtn');
        const edgeBtn = $('ytConnectEdgeBtn');
        if (title) title.textContent = `Connecting to ${browser.toUpperCase()}...`;
        if (subtitle) subtitle.textContent = 'Opening a dedicated Vidzeo browser profile. Sign in there once; the verified session will be imported and the window will close automatically.';
        if (chromeBtn) chromeBtn.disabled = true;
        if (edgeBtn) edgeBtn.disabled = true;
        showToast(`Opening ${browser.toUpperCase()} — choose your Google account in the dedicated Vidzeo window.`, 'info');

        const removeStatusListener = window.electronAPI?.onYouTubeConnectionStatus?.((status) => {
            if (title) {
                const labels = {
                    opening: 'Opening YouTube Sign-in...',
                    waiting: 'Waiting for YouTube Sign-in...',
                    verifying: 'Verifying YouTube Account...',
                    connected: 'YouTube Connected',
                    error: 'YouTube Connection Failed',
                };
                title.textContent = labels[status.phase] || 'Connecting YouTube...';
            }
            if (subtitle && status.message) subtitle.textContent = status.message;
        });

        try {
            if (window.electronAPI?.connectYouTubeBrowser) {
                await window.electronAPI.connectYouTubeBrowser(browser);
            } else {
                // Browser-only developer fallback; production distributions
                // always use the managed Electron main-process connector.
                await api('/api/youtube/session/connect-browser', {
                    method: 'POST',
                    body: JSON.stringify({ browser })
                });
            }
            await this.loadYouTubeSessionStatus();
            showToast('YouTube account connected, verified, and saved securely!', 'success');
        } catch (error) {
            console.error('YouTube connection error:', error);
            showToast(error.message || 'Could not connect YouTube session from browser', 'error');
            await this.loadYouTubeSessionStatus();
        } finally {
            if (typeof removeStatusListener === 'function') removeStatusListener();
            if (chromeBtn) chromeBtn.disabled = false;
            if (edgeBtn) edgeBtn.disabled = false;
        }
    },

    async disconnectYouTube() {
        showToast('Disconnecting YouTube session...', 'info');
        try {
            await api('/api/youtube/session/disconnect', { method: 'POST' });
            await this.loadYouTubeSessionStatus();
            showToast('YouTube session disconnected', 'info');
        } catch (error) {
            showToast(error.message || 'Failed to disconnect', 'error');
        }
    },

    async importCookies(cookieData) {
        if (!cookieData || !cookieData.trim()) {
            showToast('Please paste cookie data or upload a file', 'error');
            return;
        }
        showToast('Verifying and importing cookies...', 'info');
        try {
            await api('/api/youtube/session/import-cookies', {
                method: 'POST',
                body: JSON.stringify({ cookie_data: cookieData.trim() })
            });
            closeImportCookieModal();
            await this.loadYouTubeSessionStatus();
            showToast('YouTube cookies imported and verified successfully!', 'success');
        } catch (error) {
            showToast(error.message || 'Failed to import cookies', 'error');
        }
    },

    async autoConnectYouTubeBrowserIfAvailable() {
        try {
            const data = await api('/api/youtube/session/status');
            if (data?.session?.ready && data?.session?.verified_authenticated) {
                return; // Already connected and verified in secure DPAPI storage
            }
            // Passive legacy recovery only. Never open an interactive browser
            // window automatically; the user explicitly starts the managed
            // sign-in flow from Settings.
            const connectRes = await api('/api/youtube/session/connect-browser', {
                method: 'POST',
                body: JSON.stringify({ browser: 'chrome' })
            });
            if (connectRes?.session?.ready && connectRes?.session?.verified_authenticated) {
                console.log('✓ Auto-connected YouTube session from Chrome profile');
                await this.loadYouTubeSessionStatus();
            }
        } catch (err) {
            // Non-blocking: falls back cleanly to public search if not logged in in browser
            console.log('Auto YouTube cookie fetch bypassed (not logged in or not available)');
        }
    }
};

// Global functions
function saveSettings() { SettingsController.save(); }
function testConnection() { SettingsController.testConnection(); }
function connectYouTubeBrowser(browser) { SettingsController.connectYouTubeBrowser(browser); }
function disconnectYouTubeSession() { SettingsController.disconnectYouTube(); }
function checkForAppUpdate() { return SettingsController.checkForAppUpdate(); }
function downloadAppUpdate() { return SettingsController.downloadAppUpdate(); }
function installAppUpdate() { return SettingsController.installAppUpdate(); }

let lastAppUpdateNotificationKey = '';

function renderAppUpdateState(state = {}) {
    const status = $('appUpdateStatus');
    const version = $('appVersionLabel');
    const check = $('checkAppUpdateBtn');
    const download = $('downloadAppUpdateBtn');
    const install = $('installAppUpdateBtn');
    const current = state.currentVersion || 'unknown';
    if (version) version.textContent = `v${current}`;
    if (check) check.disabled = state.phase === 'checking' || state.phase === 'downloading' || state.phase === 'installing';
    if (download) download.classList.toggle('hidden', state.phase !== 'available');
    if (install) install.classList.toggle('hidden', state.phase !== 'ready' || !state.downloaded);
    if (!status) return;
    const messages = {
        idle: 'Check for the latest trusted Windows release.',
        checking: 'Checking for a trusted update…',
        downloading: `Downloading update… ${Math.max(0, Number(state.progress || 0))}%`,
        installing: 'The installer is starting. Vidnets Studio will close and update now.',
        up_to_date: `You are up to date${state.latestVersion ? ` (v${state.latestVersion})` : ''}.`,
        available: `Vidnets Studio v${state.latestVersion || current} has an update ready to download.`,
        ready: 'Update downloaded and verified. Restart to install it.',
        error: state.error || 'Update check failed. Try again later.',
    };
    status.textContent = messages[state.phase] || messages.idle;
    if (state.phase === 'available') {
        const notificationKey = `${state.latestVersion || current}:${state.buildId || state.installerUrl || ''}`;
        if (notificationKey && notificationKey !== lastAppUpdateNotificationKey) {
            lastAppUpdateNotificationKey = notificationKey;
            showToast(`Vidnets Studio update ${state.latestVersion ? `v${state.latestVersion}` : ''} is available. Open Preferences to download it.`, 'info');
        }
    }
}

async function checkForAppUpdateFromUi() {
    try {
        renderAppUpdateState({ phase: 'checking', currentVersion: $('appVersionLabel')?.textContent?.replace(/^v/, '') || '' });
        await window.electronAPI?.checkForAppUpdate?.();
    } catch (error) {
        renderAppUpdateState({ phase: 'error', error: error.message });
    }
}

async function downloadAppUpdateFromUi() {
    try {
        await window.electronAPI?.downloadAppUpdate?.();
    } catch (error) {
        renderAppUpdateState({ phase: 'error', error: error.message });
    }
}

async function installAppUpdateFromUi() {
    try {
        await window.electronAPI?.installAppUpdate?.();
    } catch (error) {
        renderAppUpdateState({ phase: 'error', error: error.message });
    }
}

function initAppUpdateControls() {
    const api = window.electronAPI;
    if (!api) return;
    api.onAppUpdateStateChanged?.(renderAppUpdateState);
    api.getAppUpdateState?.().then(renderAppUpdateState).catch(() => {});
    // Check once after the backend/app shell has settled. The manual button
    // remains available, and the guard prevents duplicate checks on every
    // navigation or account refresh.
    window.setTimeout(async () => {
        try {
            const current = await api.getAppUpdateState?.();
            if (current && ['available', 'downloading', 'ready', 'installing'].includes(String(current.phase))) return;
            const state = await api.checkForAppUpdate?.();
            renderAppUpdateState(state || {});
        } catch (_error) { /* update checks are non-blocking */ }
    }, 3000);
}

function openImportCookieModal() {
    const modal = $('ytCookieModal');
    if (modal) {
        modal.classList.remove('hidden');
        if ($('ytCookieInput')) $('ytCookieInput').value = '';
        if ($('ytCookieFileName')) $('ytCookieFileName').textContent = '';
    }
}
function closeImportCookieModal() {
    const modal = $('ytCookieModal');
    if (modal) modal.classList.add('hidden');
}
function handleCookieFileSelect(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    if ($('ytCookieFileName')) $('ytCookieFileName').textContent = file.name;
    const reader = new FileReader();
    reader.onload = (e) => {
        if ($('ytCookieInput')) $('ytCookieInput').value = e.target.result || '';
    };
    reader.readAsText(file);
}
function submitImportedCookies() {
    const cookieData = $('ytCookieInput')?.value || '';
    SettingsController.importCookies(cookieData);
}
function savePreferences() {
    showToast('Preferences saved!', 'success');
}

function applyTheme(theme) {
    const resolvedTheme = theme === 'light' ? 'light' : 'dark';
    AppState.theme = resolvedTheme;
    document.body.dataset.theme = resolvedTheme;
    document.querySelectorAll('.theme-btn').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.themeOption === resolvedTheme);
    });
}

function setTheme(theme) {
    applyTheme(theme);
    localStorage.setItem('vidzeo_theme', AppState.theme);
    AccountController.render();
    savePreferences();
}

function renderPricing() {
    const grid = $('pricingPlansGrid');
    const compare = $('pricingCompareGrid');
    if (!grid || !compare) return;

    const plans = getPlansForCycle();
    compare.style.setProperty('--plan-count', Math.max(1, plans.length));
    if (!plans.length) {
        grid.innerHTML = '<div class="empty-state">Pricing plans are still loading. Check the backend connection and try again.</div>';
        compare.innerHTML = '';
        AccountController.render();
        return;
    }

    grid.innerHTML = plans.map((plan) => {
        const features = getPlanFeatures(plan);
        const shortName = getPlanShortName(plan);
        const isFeatured = shortName === 'Plus' || shortName === 'Pro';
        const isCurrent = AppState.account.planKey === plan.plan_key && AppState.account.planStatus === 'active';
        const isPending = AppState.account.planKey === plan.plan_key && AppState.account.planStatus === 'pending';
        const buttonLabel = isCurrent ? 'Current Plan' : isPending ? 'Requested'
            : plan.billing_period === 'lifetime' ? 'Get Lifetime Pro' : 'Upgrade Plan';
        const buttonClass = isCurrent ? 'btn-secondary' : 'btn-generate';
        const subcopy = plan.billing_period === 'lifetime'
            ? 'One payment. Pro access for life. No code needed for the launch price.'
            : plan.billing_period === 'yearly' ? 'Billed yearly, shown as monthly equivalent' : 'Billed monthly';
        return `
            <article class="plan-card${isFeatured ? ' plan-card-featured' : ''}${AppState.account.planKey === plan.plan_key ? ' active' : ''}" data-plan-card="${escapeHtml(plan.plan_key)}" style="position: relative;">
                ${isFeatured ? '<div class="popular-ribbon" style="position: absolute; top: -12px; right: 16px; background: linear-gradient(135deg, #2fe8ff, #0084ff); color: #040910; font-size: 10px; font-weight: 800; padding: 3px 12px; border-radius: 20px; letter-spacing: 0.6px; box-shadow: 0 4px 12px rgba(47, 232, 255, 0.4);">PRO FEATURES</div>' : ''}
                <span class="project-chip">${escapeHtml(shortName)}</span>
                <h4>${escapeHtml(plan.display_name)}</h4>
                <p>${escapeHtml(features[0])}</p>
                <div class="plan-price">${escapeHtml(getPlanPriceLabel(plan))}</div>
                <div class="plan-price-subcopy">${escapeHtml(subcopy)}</div>
                <ul class="plan-feature-list">
                    ${features.map((feature) => `<li>${escapeHtml(feature)}</li>`).join('')}
                </ul>
                <button class="${buttonClass} plan-action" data-plan-action="${escapeHtml(plan.plan_key)}" onclick="selectPlan('${escapeHtml(plan.plan_key)}')">${buttonLabel}</button>
            </article>
        `;
    }).join('');

    compare.innerHTML = `
        <div class="pricing-compare-row pricing-compare-head">
            <span>Capability</span>
            ${plans.map((plan) => `<span>${escapeHtml(getPlanShortName(plan))}</span>`).join('')}
        </div>
        <div class="pricing-compare-row">
            <span>Video length</span>
            ${plans.map((plan) => `<span>${plan.video_limit_minutes} minutes</span>`).join('')}
        </div>
        <div class="pricing-compare-row">
            <span>Monthly Exports</span>
            ${plans.map((plan) => `<span>${(plan.metadata?.monthly_export_limit ?? plan.export_limit) != null ? `${plan.metadata?.monthly_export_limit ?? plan.export_limit} exports/mo` : 'Unlimited'}</span>`).join('')}
        </div>
        <div class="pricing-compare-row">
            <span>Audio & Voiceovers</span>
            ${plans.map(() => '<span>Configure in Settings</span>').join('')}
        </div>
        <div class="pricing-compare-row">
            <span>Devices</span>
            ${plans.map((plan) => `<span>${plan.device_limit || 1} Device${(plan.device_limit || 1) > 1 ? 's' : ''}</span>`).join('')}
        </div>
    `;

    AccountController.render();
}

function setPricingCycle(cycle) {
    AppState.pricingCycle = cycle === 'yearly' ? 'yearly' : 'monthly';
    document.querySelectorAll('[data-pricing-cycle]').forEach((button) => {
        button.classList.toggle('active', button.dataset.pricingCycle === AppState.pricingCycle);
    });
    renderPricing();
}

async function selectPlan(planKey) {
    const plan = AppState.availablePlans.find((item) => item.plan_key === planKey);
    const shortName = plan ? getPlanShortName(plan) : 'Pro';
    openPricingPage(shortName);
}

function logoutProfile() {
    AccountController.logout();
}

async function refreshProfileAccess() {
    try {
        const changed = await AccountController.refreshEntitlement({ silent: false });
        if (!changed) {
            AccountController.render();
            showToast('Access is already up to date.', 'info');
        }
    } catch (error) {
        showToast(error.message || 'Could not refresh account access.', 'error');
    }
}

async function selectOutputFolder(scope = 'main') {
    try {
        if (!window.electronAPI?.selectOutputFolder) {
            showToast('Output folder picker is only available in the desktop app.', 'error');
            return;
        }

        const selectedPath = await window.electronAPI.selectOutputFolder();
        if (!selectedPath) return;

        const primaryId = scope === 'setting' ? 'settingOutputPath' : 'outputPath';
        const mirrorId = scope === 'setting' ? 'outputPath' : 'settingOutputPath';
        if ($(primaryId)) $(primaryId).value = selectedPath;
        if ($(mirrorId)) $(mirrorId).value = selectedPath;
        FormSettingsController.save();
        showToast('Output folder set: ' + selectedPath, 'success');
    } catch (error) {
        showToast(`Could not open output folder picker: ${error.message || error}`, 'error');
    }
}

async function openOutputDirectory(scope = 'main') {
    try {
        const primaryId = scope === 'setting' ? 'settingOutputPath' : 'outputPath';
        const folderPath = $(primaryId)?.value?.trim() || '';
        if (window.electronAPI?.openOutputFolder) {
            await window.electronAPI.openOutputFolder(folderPath);
        } else {
            showToast('Opening folder is only supported in the desktop app.', 'info');
        }
    } catch (error) {
        showToast(`Could not open output folder: ${error.message || error}`, 'error');
    }
}

window.selectOutputFolder = selectOutputFolder;
window.openOutputDirectory = openOutputDirectory;

function bindOutputFolderPickers() {
    [
        ['settingOutputPath', 'setting'],
        ['outputPath', 'main']
    ].forEach(([id, scope]) => {
        const input = $(id);
        if (!input || input.dataset.outputPickerBound === 'true') return;
        input.dataset.outputPickerBound = 'true';
        input.addEventListener('click', () => selectOutputFolder(scope));
        input.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                selectOutputFolder(scope);
            }
        });
    });

    document.querySelectorAll('button[data-output-picker]').forEach((button) => {
        const scope = button.dataset.outputPicker || 'main';
        if (button.dataset.outputPickerBound === 'true') return;
        button.dataset.outputPickerBound = 'true';
        button.addEventListener('click', (event) => {
            event.preventDefault();
            selectOutputFolder(scope);
        });
    });
}


// =============================================================================
// FORM SETTINGS CONTROLLER (Auto-save all form values)
// =============================================================================

const FormSettingsController = {
    STORAGE_KEY: 'vidzeo_form_settings',
    saveTimeout: null,

    // List of form element IDs to persist
    // These are all dropdowns, inputs, and checkboxes that should be remembered
    formElements: [
        // Long Video Settings
        'voiceSelect', 'longTtsModel', 'videoName', 'videoQuality', 'effectSelect', 'imageSource',
        'sceneMode',
        'captionsEnabled', 'fontSelect', 'fontSize', 'textColor', 'captionShadow',
        'captionPosition', 'captionStyle', 'captionFont', 'captionAnimation', 'captionMode', 'musicVolume', 'outputPath',
        'videoFormat',
        'longEdgeRate', 'longEdgePitch', 'longEdgeVolume',
        'hlModeLong', 'hlFontLong', 'hlFontSizeLong', 'hlTextColorLong', 'hlShadowLong', 'hlAnimationLong',
        'topHeadingEnabledLong', 'topHeadingFontLong', 'topHeadingFontSizeLong',

        // Short Video Settings
        'shortVoiceSelect', 'shortVideoName', 'shortResolution',
        'shortSceneMode', 'shortEffect',
        'shortCaptionsEnabled', 'shortFontSelect',
        'shortFontSize', 'shortTextColor', 'shortHighlightColor',
        'shortCaptionPosition', 'shortCaptionMode', 'shortCaptionShadow', 'shortDuration', 'shortScriptStyle', 'shortNiche',
        'shortEdgeRate', 'shortEdgePitch', 'shortEdgeVolume',
        'shortVideoFormat',
        'hlModeShort', 'hlFontShort', 'hlFontSizeShort', 'hlTextColorShort', 'hlShadowShort', 'hlAnimationShort',
        'topHeadingEnabledShort', 'topHeadingFontShort', 'topHeadingFontSizeShort',

        // Bulk Long Settings  
        'bulkLongVoice', 'bulkDuration', 'bulkScriptStyle',
        'bulkLongImageSource',
        'bulkLongSceneMode', 'bulkLongEffectSelect', 'bulkLongCaptionAnimation',
        'bulkLongEdgeRate', 'bulkLongEdgePitch', 'bulkLongEdgeVolume',
        'hlModeBulk', 'hlFontBulk', 'hlFontSizeBulk', 'hlTextColorBulk', 'hlShadowBulk', 'hlAnimationBulk',
        'topHeadingEnabledBulk', 'topHeadingFontBulk', 'topHeadingFontSizeBulk',

        // Bulk Short Settings
        'bulkShortVoice', 'bulkShortDuration', 'bulkShortScriptStyle',
        'bulkShortNiche', 'bulkShortVideoFormat', 'bulkShortResolution',
        'bulkShortSceneMode',
        'bulkShortEdgeRate', 'bulkShortEdgePitch', 'bulkShortEdgeVolume',
        // Explainer Settings
        'explainerBg', 'explainerPlainFrames', 'explainerChapters',
        'bulkLongExplainerBg', 'bulkLongExplainerPlainFrames', 'bulkLongExplainerChapters',

        // Preferences
        'defaultVoice', 'defaultOutputPath'
    ],

    // Save all form values to localStorage (debounced)
    save() {
        // Debounce saves to avoid excessive writes
        if (this.saveTimeout) {
            clearTimeout(this.saveTimeout);
        }

        this.saveTimeout = setTimeout(() => {
            const settings = {};

            this.formElements.forEach(id => {
                const element = $(id);
                if (!element) return;

                if (element.type === 'checkbox') {
                    settings[id] = element.checked;
                } else {
                    settings[id] = element.value;
                }
            });

            localStorage.setItem(this.STORAGE_KEY, JSON.stringify(settings));
            console.log('💾 Form settings auto-saved');
        }, 500); // 500ms debounce
    },

    // Load all form values from localStorage
    load() {
        const saved = localStorage.getItem(this.STORAGE_KEY);
        if (!saved) {
            console.log('📦 No saved form settings found');
            return;
        }

        try {
            const settings = JSON.parse(saved);
            console.log('📦 Loading form settings from localStorage');

            // We need to apply these AFTER dropdowns are populated
            // Store for later application
            this.pendingSettings = settings;

        } catch (e) {
            console.error('Failed to load form settings:', e);
        }
    },

    // Apply pending settings (called after dropdowns are populated)
    applyPendingSettings() {
        if (!this.pendingSettings) return;

        const settings = this.pendingSettings;
        let appliedCount = 0;

        Object.entries(settings).forEach(([id, value]) => {
            const element = $(id);
            if (!element) return;

            if (element.type === 'checkbox') {
                element.checked = value;
            } else {
                // For dropdowns, check if the option exists
                if (element.tagName === 'SELECT') {
                    const optionExists = Array.from(element.options).some(opt => opt.value === value);
                    if (optionExists) {
                        element.value = value;
                        appliedCount++;
                    }
                } else {
                    element.value = value;
                    appliedCount++;
                }
            }
        });

        console.log(`✅ Applied ${appliedCount} saved form settings`);
        this.pendingSettings = null;
    },

    // Initialize auto-save listeners on all form elements
    initAutoSave() {
        this.formElements.forEach(id => {
            const element = $(id);
            if (!element) return;

            // Listen for changes and auto-save
            element.addEventListener('change', () => this.save());

            // For text inputs, also save on input (with debounce)
            if (element.type === 'text' || element.type === 'number' || element.tagName === 'TEXTAREA') {
                element.addEventListener('input', () => this.save());
            }
        });

        console.log('🔄 Auto-save listeners initialized');
    }
};

// =============================================================================
// DROPDOWN CONTROLLER
// =============================================================================

// Mapping: TTS engine dropdown ID to its voice dropdown.
const TTS_MODEL_TO_VOICE_MAP = {
    'longTtsModel': 'voiceSelect',
    'settingLongTtsModel': 'settingVoiceSelect',
    'shortTtsModel': 'shortVoiceSelect',
    'bulkLongTtsModel': 'bulkLongVoice',
    'bulkShortTtsModel': 'bulkShortVoice'
};

const EDGE_TTS_CONTEXTS = {
    'setting-long': {
        name: 'setting-long',
        voiceId: 'settingVoiceSelect',
        helpId: 'settingLongTtsVoiceHelp',
        ttsModelId: 'settingLongTtsModel',
        panelId: null,
        rateId: null,
        rateValueId: null,
        pitchId: null,
        pitchValueId: null,
        volumeId: null,
        volumeValueId: null
    },
    long: {
        name: 'long',
        voiceId: 'voiceSelect',
        helpId: 'longTtsVoiceHelp',
        ttsModelId: 'longTtsModel',
        panelId: 'longEdgeTtsOptions',
        rateId: 'longEdgeRate',
        rateValueId: 'longEdgeRateValue',
        pitchId: 'longEdgePitch',
        pitchValueId: 'longEdgePitchValue',
        volumeId: 'longEdgeVolume',
        volumeValueId: 'longEdgeVolumeValue'
    },
    short: {
        name: 'short',
        voiceId: 'shortVoiceSelect',
        ttsModelId: 'shortTtsModel',
        panelId: 'shortEdgeTtsOptions',
        rateId: 'shortEdgeRate',
        rateValueId: 'shortEdgeRateValue',
        pitchId: 'shortEdgePitch',
        pitchValueId: 'shortEdgePitchValue',
        volumeId: 'shortEdgeVolume',
        volumeValueId: 'shortEdgeVolumeValue'
    },
    'bulk-long': {
        name: 'bulk-long',
        voiceId: 'bulkLongVoice',
        ttsModelId: 'bulkLongTtsModel',
        panelId: 'bulkLongEdgeTtsOptions',
        rateId: 'bulkLongEdgeRate',
        rateValueId: 'bulkLongEdgeRateValue',
        pitchId: 'bulkLongEdgePitch',
        pitchValueId: 'bulkLongEdgePitchValue',
        volumeId: 'bulkLongEdgeVolume',
        volumeValueId: 'bulkLongEdgeVolumeValue'
    },
    'bulk-short': {
        name: 'bulk-short',
        voiceId: 'bulkShortVoice',
        ttsModelId: 'bulkShortTtsModel',
        panelId: 'bulkShortEdgeTtsOptions',
        rateId: 'bulkShortEdgeRate',
        rateValueId: 'bulkShortEdgeRateValue',
        pitchId: 'bulkShortEdgePitch',
        pitchValueId: 'bulkShortEdgePitchValue',
        volumeId: 'bulkShortEdgeVolume',
        volumeValueId: 'bulkShortEdgeVolumeValue'
    },
    default: {
        name: 'default',
        voiceId: 'defaultVoice',
        ttsModelId: null,
        panelId: null,
        rateId: null,
        rateValueId: null,
        pitchId: null,
        pitchValueId: null,
        volumeId: null,
        volumeValueId: null
    }
};

function getEdgeContextByTtsModelId(ttsModelSelectId) {
    return Object.values(EDGE_TTS_CONTEXTS).find(context => context.ttsModelId === ttsModelSelectId);
}

function getPreferredEdgeVoiceId() {
    const voices = AppState.dropdownData.edgeVoices || [];
    return voices.find(voice => voice.id === 'en-US-BrianNeural')?.id || voices[0]?.id || 'en-US-BrianNeural';
}

function isAudiogramTtsModel(ttsModel = 'audiogram') {
    return !ttsModel || ttsModel === 'audiogram';
}

function getTtsModelCatalog() {
    const bundledAudiogram = {
        id: 'audiogram',
        label: 'Edge TTS (Free)',
        provider: 'Microsoft Edge',
        voices: AppState.dropdownData.edgeVoices || [],
        default_voice: getPreferredEdgeVoiceId(),
        voice_required: true,
        catalog_source: 'bundled_edge_catalog'
    };
    const remoteModels = (AppState.dropdownData.ttsModels || []).filter(model => model?.id && model.id === 'fish-audio/s2.1-pro-free:free');
    return [bundledAudiogram, ...remoteModels];
}

function getTtsModelById(ttsModel = 'audiogram') {
    return getTtsModelCatalog().find(model => model.id === ttsModel) || getTtsModelCatalog()[0];
}

function getVoiceCatalogForTtsModel(ttsModel = 'audiogram') {
    if (isAudiogramTtsModel(ttsModel)) return AppState.dropdownData.edgeVoices?.length
        ? AppState.dropdownData.edgeVoices : [
            {id:'en-US-BrianNeural', label:'Brian (Male US)', locale:'en-US'},
            {id:'en-US-JennyNeural', label:'Jenny (Female US)', locale:'en-US'},
            {id:'en-GB-RyanNeural', label:'Ryan (Male UK)', locale:'en-GB'}
        ];
    return AppState.dropdownData.ttsVoices?.[ttsModel]
        || getTtsModelById(ttsModel)?.voices
        || [];
}

function getPreferredVoiceForTtsModel(ttsModel = 'audiogram') {
    const model = getTtsModelById(ttsModel);
    const voices = getVoiceCatalogForTtsModel(ttsModel);
    if (model?.default_voice !== undefined) return model.default_voice;
    return voices[0]?.id ?? '';
}

function populateTtsModelDropdown(elementId, preferredValue = 'audiogram') {
    const element = $(elementId);
    if (!element) return;
    const currentValue = element.value;
    const models = getTtsModelCatalog();
    element.innerHTML = '';
    models.forEach((model) => {
        const option = document.createElement('option');
        option.value = model.id;
        option.textContent = model.label || model.name || model.id;
        element.appendChild(option);
    });
    const target = models.some(model => model.id === preferredValue)
        ? preferredValue
        : (models.some(model => model.id === currentValue) ? currentValue : 'audiogram');
    element.value = target;
}

function populateVoiceDropdown(elementId, voices, preferredValue = null) {
    const element = $(elementId);
    if (!element) return;

    // Filter to English-only voices
    const voiceList = (voices || []).filter(Boolean).filter(voice => {
        if (voice.locale) {
            return voice.locale.toLowerCase().startsWith('en-');
        }
        return true;
    });

    const preferredWasProvided = preferredValue !== null && preferredValue !== undefined;
    const normalizedPreferred = voiceList.find((voice) =>
        voice.id === preferredValue || voice.label === preferredValue
    )?.id || preferredValue;

    element.innerHTML = '';

    const maleVoices = [];
    const femaleVoices = [];
    const otherVoices = [];

    // Some providers (notably Fish S2.1) expose delivery/character profiles
    // rather than gendered voices. Keep those categories explicit in the
    // selector so users can tell a provider default from an app-level style.
    const categorizedVoices = {
        provider: [],
        delivery: [],
        character: [],
    };
    const hasExplicitCategories = voiceList.some((voice) =>
        ['provider', 'delivery', 'character'].includes(String(voice.category || '').toLowerCase())
    );

    voiceList.forEach(voice => {
        const category = String(voice.category || '').toLowerCase();
        if (hasExplicitCategories && categorizedVoices[category]) {
            categorizedVoices[category].push(voice);
            return;
        }

        const gender = (voice.gender || '').toLowerCase();
        const label = (voice.label || voice.name || voice.friendly_name || voice.id || '').toLowerCase();
        if (gender === 'male' || label.includes('♂') || (label.includes('male') && !label.includes('female'))) {
            maleVoices.push(voice);
        } else if (gender === 'female' || label.includes('♀') || label.includes('female')) {
            femaleVoices.push(voice);
        } else {
            // Provider-default voices do not promise a particular gender.
            otherVoices.push(voice);
        }
    });

    let hasSelection = false;

    const appendVoiceOption = (container, voice) => {
        const option = document.createElement('option');
        option.value = voice.id ?? '';
        option.textContent = voice.label || voice.name || voice.id || 'Voice';
        if (voice.description) option.title = voice.description;
        if (preferredWasProvided && String(voice.id ?? '') === String(normalizedPreferred ?? '')) {
            option.selected = true;
            hasSelection = true;
        }
        container.appendChild(option);
    };

    if (hasExplicitCategories) {
        const categoryGroups = [
            ['provider', 'Provider default'],
            ['delivery', 'Delivery styles'],
            ['character', 'Character styles'],
        ];
        categoryGroups.forEach(([category, label]) => {
            const groupVoices = categorizedVoices[category];
            if (!groupVoices.length) return;
            const group = document.createElement('optgroup');
            group.label = label;
            groupVoices.forEach((voice) => appendVoiceOption(group, voice));
            element.appendChild(group);
        });
        // Keep provider catalog entries visible if they do not have a known
        // app grouping. This can happen when a provider adds voice metadata
        // before the UI learns its new category.
        const ungroupedVoices = [...maleVoices, ...femaleVoices, ...otherVoices];
        if (ungroupedVoices.length) {
            const group = document.createElement('optgroup');
            group.label = 'Other available voices';
            ungroupedVoices.forEach((voice) => appendVoiceOption(group, voice));
            element.appendChild(group);
        }
    } else if (maleVoices.length > 0 || femaleVoices.length > 0) {
        if (maleVoices.length > 0) {
            const maleGroup = document.createElement('optgroup');
            maleGroup.label = '♂ Male Voices (English)';
            maleVoices.forEach(v => appendVoiceOption(maleGroup, v));
            element.appendChild(maleGroup);
        }
        if (femaleVoices.length > 0) {
            const femaleGroup = document.createElement('optgroup');
            femaleGroup.label = '♀ Female Voices (English)';
            femaleVoices.forEach(v => appendVoiceOption(femaleGroup, v));
            element.appendChild(femaleGroup);
        }
        if (otherVoices.length > 0) {
            const otherGroup = document.createElement('optgroup');
            otherGroup.label = 'Other English Voices';
            otherVoices.forEach(v => appendVoiceOption(otherGroup, v));
            element.appendChild(otherGroup);
        }
    } else {
        voiceList.forEach((voice, index) => {
            const option = document.createElement('option');
            option.value = voice.id ?? '';
            option.textContent = voice.label || voice.id || 'Provider default voice';
            if (preferredWasProvided && String(voice.id ?? '') === String(normalizedPreferred ?? '')) {
                option.selected = true;
                hasSelection = true;
            } else if (!preferredWasProvided && index === 0) {
                option.selected = true;
                hasSelection = true;
            }
            element.appendChild(option);
        });
    }

    if (!hasSelection && element.options.length) {
        element.options[0].selected = true;
    }
}

function populateEdgeVoiceDropdown(elementId, voices, preferredValue = null) {
    populateVoiceDropdown(elementId, voices, preferredValue);
}

function getVoModeForTtsModel(ttsModel = 'audiogram') {
    return isAudiogramTtsModel(ttsModel) ? 'Use Audiogram' : 'Use OpenRouter TTS';
}

function isVideoSettingsModalOpen() {
    const modal = $('videoSettingsModal');
    return !!modal && !modal.classList.contains('hidden');
}

function getEffectiveLongImageSource() {
    if (isVideoSettingsModalOpen() && $('settingImageSource')?.value) {
        return $('settingImageSource').value;
    }
    return $('imageSource')?.value || 'Web Images';
}

function deriveWorkingTitleFromScript(script, fallback = 'Video') {
    const raw = String(script || '').replace(/\s+/g, ' ').trim();
    if (!raw) return fallback;

    // Check for explicit ship/vessel entity pattern (e.g. RMS Titanic, HMS Victory, USS Enterprise)
    const shipMatch = raw.match(/\b(?:RMS|SS|HMS|USS|MV|MS)\s+[A-Z][A-Za-z0-9'-]{2,}(?:\s+[A-Z][A-Za-z0-9'-]+)?\b/);
    if (shipMatch) return shipMatch[0].trim();

    // Strip leading storytelling / date preambles
    const withoutDatePreamble = raw
        .replace(/^(?:(?:on|in|during|back in)\s+(?:the\s+)?(?:early\s+|late\s+)?(?:cold\s+|dark\s+)?(?:morning|evening|night|summer|winter|spring|autumn|fall|year\s+\d{3,4})?(?:\s+of)?\s*(?:[A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s*\d{0,4}|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+,?\s*\d{0,4}|\d{4})[\s,:-]*)+/i, '')
        .trim();

    const withoutPreamble = (withoutDatePreamble || raw)
        .replace(/\b(?:in this video|this video|today|welcome back|let's talk about)\b[\s,:-]*/i, '')
        .trim();

    const sentences = (withoutPreamble || raw)
        .split(/(?<=[.!?])\s+/)
        .map((part) => part.trim())
        .filter(Boolean);
    const firstSentence = sentences[0] || withoutPreamble || raw;
    let seed = '';

    // If the sentence introduces the subject after a colon (e.g. "...blockbuster: Grand Theft Auto VI.")
    if (firstSentence.includes(':')) {
        const colonParts = firstSentence.split(':');
        const afterColon = (colonParts[1] || '').trim().replace(/[.!?]+$/, '').trim();
        const afterWords = afterColon.split(/\s+/).filter(Boolean);
        if (afterWords.length >= 1 && afterWords.length <= 5) {
            seed = afterColon.replace(/^["'`“”‘’]+|["'`“”‘’]+$/g, '').trim();
        }
    }
    if (!seed) {
        // Split on title-subtitle dash ("Title - Subtitle") or semicolons
        const primary = (firstSentence.split(/\s+[-–—]\s+|[;:]/)[0] || '').trim();
        seed = primary.replace(/^["'`“”‘’]+|["'`“”‘’]+$/g, '').trim();
    }
    if (!seed) return fallback;

    // Split before auxiliary / common linking verbs
    const verbSplit = seed.split(/\s+(?:is|are|was|were|has|have|had|will|would|can|could|still|remains?|became|becomes?|seems?|offers?|provides?|changed?|transformed?|started?|began|landed|struck)\b/i);
    if (verbSplit[0] && verbSplit[0].trim().split(/\s+/).filter(Boolean).length >= 1 && verbSplit[0].trim().length >= 3) {
        seed = verbSplit[0].trim();
    }

    let cleaned = seed
        .replace(/^[^A-Za-z0-9]+/, '')
        .replace(/[.,:;!?\-–—\s]+$/, '')
        .replace(/\s+/g, ' ')
        .trim();

    const TRAILING_STOPWORDS = new Set([
        'still', 'is', 'are', 'was', 'were', 'has', 'have', 'had', 'will', 'would',
        'can', 'could', 'and', 'or', 'of', 'to', 'in', 'for', 'with', 'on', 'at',
        'from', 'by', 'a', 'the', 'an', 'that', 'this', 'which', 'as', 'about',
        'into', 'its', 'their', 'our', 'your', 'his', 'her', 'not', 'but', 'so',
        'quietly', 'slowly', 'quickly', 'one', 'two', 'three', 'first', 'second', 'third', 'last', 'part',
    ]);

    let words = cleaned.split(' ').filter(Boolean).slice(0, 6);
    while (words.length > 1 && TRAILING_STOPWORDS.has(words[words.length - 1].toLowerCase().replace(/[^a-z]/g, ''))) {
        words.pop();
    }

    return words.join(' ').replace(/[.,:;!?\-–—\s]+$/, '').trim() || fallback;
}

function updateEdgeSliderValue(contextName, optionKey) {
    const context = EDGE_TTS_CONTEXTS[contextName];
    if (!context) return;

    const input = $(context[`${optionKey}Id`]);
    const valueEl = $(context[`${optionKey}ValueId`]);
    if (!input || !valueEl) return;

    const suffix = optionKey === 'pitch' ? 'Hz' : '%';
    const value = parseInt(input.value || '0', 10);
    valueEl.textContent = `${value >= 0 ? '+' : ''}${value}${suffix}`;
}

function applyEdgeDefaultsToContext(contextName, defaults = {}) {
    const context = EDGE_TTS_CONTEXTS[contextName];
    if (!context) return;

    ['rate', 'pitch', 'volume'].forEach((key) => {
        if (!context[`${key}Id`]) return;
        const input = $(context[`${key}Id`]);
        if (!input) return;
        if (!input.dataset.initialized) {
            input.value = defaults[key] ?? 0;
            input.dataset.initialized = 'true';
        }
        updateEdgeSliderValue(contextName, key);
    });
}

function applyEdgeOptionsToContext(contextName, options = {}) {
    const context = EDGE_TTS_CONTEXTS[contextName];
    if (!context) return;

    ['rate', 'pitch', 'volume'].forEach((key) => {
        if (!context[`${key}Id`]) return;
        const input = $(context[`${key}Id`]);
        if (!input) return;
        input.value = options[key] ?? 0;
        input.dataset.initialized = 'true';
        updateEdgeSliderValue(contextName, key);
    });
}

function toggleEdgeOptionsForContext(contextName) {
    const context = EDGE_TTS_CONTEXTS[contextName];
    if (!context) return;
    if (!context.panelId) return;

    const panel = $(context.panelId);
    if (panel) {
        const ttsModel = context.ttsModelId ? ($(context.ttsModelId)?.value || 'audiogram') : 'audiogram';
        panel.classList.toggle('hidden', !isAudiogramTtsModel(ttsModel));
    }
}

function syncVoiceDropdownForContext(contextName, preferredVoice = null) {
    const context = EDGE_TTS_CONTEXTS[contextName];
    if (!context) return;

    const ttsModel = context.ttsModelId ? ($(context.ttsModelId)?.value || 'audiogram') : 'audiogram';
    const currentVoice = preferredVoice !== null && preferredVoice !== undefined
        ? preferredVoice
        : $(context.voiceId)?.value;
    const catalog = getVoiceCatalogForTtsModel(ttsModel);
    const preferred = catalog.some(voice => String(voice.id ?? '') === String(currentVoice ?? '')
        || voice.label === currentVoice)
        ? currentVoice
        : getPreferredVoiceForTtsModel(ttsModel);

    populateVoiceDropdown(context.voiceId, catalog, preferred);

    const help = context.helpId ? $(context.helpId) : null;
    const model = getTtsModelById(ttsModel);
    if (help) {
        if (ttsModel === 'fish-audio/s2.1-pro-free:free') {
            help.textContent = model?.voice_catalog_note
                || 'Fish Audio uses a provider-selected base voice. The profiles below guide delivery style.';
        } else {
            help.textContent = '';
        }
        help.classList.toggle('hidden', !help.textContent);
    }

    toggleEdgeOptionsForContext(contextName);
}

function updateVoicesForTtsModel(ttsModelSelectId) {
    const context = getEdgeContextByTtsModelId(ttsModelSelectId);
    if (!context) return;
    syncVoiceDropdownForContext(context.name);
}

function getTtsModelForContext(contextName) {
    const context = EDGE_TTS_CONTEXTS[contextName];
    return context?.ttsModelId ? ($(context.ttsModelId)?.value || 'audiogram') : 'audiogram';
}

function getVoiceForContext(contextName) {
    const context = EDGE_TTS_CONTEXTS[contextName];
    const ttsModel = getTtsModelForContext(contextName);
    const selectedVoice = context ? ($(context.voiceId)?.value ?? '') : '';
    if (!isAudiogramTtsModel(ttsModel)) return selectedVoice;
    return selectedVoice || 'Adam (Male US)';
}

function getEdgeTtsOptions(contextName) {
    const context = EDGE_TTS_CONTEXTS[contextName];
    if (!context) return { rate: 0, pitch: 0, volume: 0 };

    return {
        rate: parseInt($(context.rateId)?.value || '0', 10),
        pitch: parseInt($(context.pitchId)?.value || '0', 10),
        volume: parseInt($(context.volumeId)?.value || '0', 10)
    };
}

// =============================================================================
// EDGE VOICE BROWSER — Full-featured voice selection modal
// =============================================================================

const EVB_FAV_KEY = 'vidzeo-edge-fav-voices';

const EVB_GENDER_FALLBACK = {
    'Aria': 'Female', 'Jenny': 'Female', 'Guy': 'Male', 'Davis': 'Male',
    'Brian': 'Male', 'Emma': 'Female', 'Ana': 'Female', 'Andrew': 'Male',
    'Ava': 'Female', 'Brandon': 'Male', 'Christopher': 'Male', 'Cora': 'Female',
    'Elizabeth': 'Female', 'Eric': 'Male', 'Jacob': 'Male', 'Jane': 'Female',
    'Jason': 'Male', 'Michelle': 'Female', 'Monica': 'Female', 'Nancy': 'Female',
    'Roger': 'Male', 'Sara': 'Female', 'Steffan': 'Male', 'Tony': 'Male',
    'Natasha': 'Female', 'William': 'Male', 'Clara': 'Female', 'Liam': 'Male',
    'Neerja': 'Female', 'Prabhat': 'Male', 'Swara': 'Female', 'Madhur': 'Male',
    'Denise': 'Female', 'Henri': 'Male', 'Amala': 'Female', 'Ravi': 'Male',
    'Conner': 'Male', 'Emily': 'Female', 'Ryan': 'Male', 'Sonia': 'Female',
    'Libby': 'Female', 'Valentina': 'Female', 'Diego': 'Male', 'Dalia': 'Female',
    'Jorge': 'Male', 'Elena': 'Female', 'Elvira': 'Female', 'Alvaro': 'Male',
    'Xiaoxiao': 'Female', 'Yunxi': 'Male', 'Yunjian': 'Male', 'Xiaoyi': 'Female'
};

function evbGuessGender(name) {
    if (!name) return 'Neutral';
    if (EVB_GENDER_FALLBACK[name]) return EVB_GENDER_FALLBACK[name];
    const lower = name.toLowerCase();
    if (lower.endsWith('a') || lower.endsWith('ia') || lower.endsWith('na') || lower.endsWith('e')) return 'Female';
    if (lower.endsWith('o') || lower.endsWith('us') || lower.endsWith('an') || lower.endsWith('rt')) return 'Male';
    return 'Neutral';
}

const evbLangNames = (() => {
    try { return new Intl.DisplayNames(['en'], { type: 'language' }); }
    catch { return null; }
})();

function evbGetBaseLang(locale) {
    if (!locale) return 'Unknown';
    try { return evbLangNames?.of(locale.split('-')[0]) || locale; }
    catch { return locale; }
}

function evbGetDialect(locale) {
    if (!locale) return 'Unknown';
    try { return evbLangNames?.of(locale) || locale; }
    catch { return locale; }
}

function evbExtractSimpleName(voice) {
    const raw = voice.friendly_name || voice.label || voice.id || '';
    // Try to extract human name from pattern like "en-US-BrianNeural"
    const parts = raw.split('-');
    let name = parts.length >= 3 ? parts.slice(2).join('-') : raw;
    name = name.replace(/Neural$/i, '').replace(/Multilingual$/i, '').trim();
    if (name.length === 0) name = raw;
    return name;
}

function evbDeriveGender(voice) {
    if (voice.gender && voice.gender !== 'Neutral' && voice.gender !== 'Unknown') return voice.gender;
    const simpleName = evbExtractSimpleName(voice);
    return evbGuessGender(simpleName);
}

const EdgeVoiceBrowser = {
    _activeContext: null,
    _selectedVoiceId: null,
    _favourites: [],
    _filter: 'all',
    _searchText: '',
    _langFilter: 'all',
    _previewAudio: null,
    _initialized: false,

    _loadFavourites() {
        try {
            this._favourites = JSON.parse(localStorage.getItem(EVB_FAV_KEY)) || [];
        } catch { this._favourites = []; }
    },

    _saveFavourites() {
        localStorage.setItem(EVB_FAV_KEY, JSON.stringify(this._favourites));
    },

    _getVoices() {
        return (AppState.dropdownData?.edgeVoices || []).filter(v => (v.locale || '').toLowerCase().startsWith('en-'));
    },

    _initListeners() {
        if (this._initialized) return;
        this._initialized = true;

        // Close button
        $('evbCloseBtn')?.addEventListener('click', () => this.close());

        // Backdrop click
        $('evbOverlay')?.addEventListener('click', (e) => {
            if (e.target === $('evbOverlay')) this.close();
        });

        // Escape key
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && $('evbOverlay')?.classList.contains('open')) {
                this.close();
            }
        });

        // Search input
        $('evbSearchInput')?.addEventListener('input', (e) => {
            this._searchText = e.target.value;
            this._render();
        });

        // Language filter
        $('evbLangSelect')?.addEventListener('change', (e) => {
            this._langFilter = e.target.value;
            this._render();
        });

        // Gender pills
        $('evbFilterPills')?.addEventListener('click', (e) => {
            const pill = e.target.closest('.evb-pill');
            if (!pill) return;
            $('evbFilterPills').querySelectorAll('.evb-pill').forEach(p => p.classList.remove('active'));
            pill.classList.add('active');
            this._filter = pill.dataset.filter;
            this._render();
        });

        // Confirm button
        $('evbConfirmBtn')?.addEventListener('click', () => this._confirmSelection());
    },

    _populateLanguages() {
        const langSelect = $('evbLangSelect');
        if (!langSelect) return;
        const voices = this._getVoices();
        const locales = [...new Set(voices.map(v => v.locale).filter(Boolean))].sort();
        langSelect.innerHTML = '<option value="all">🌍 All English Accents</option>';
        locales.forEach(loc => {
            const opt = document.createElement('option');
            opt.value = loc;
            opt.textContent = `${loc} (${evbGetDialect(loc)})`;
            langSelect.appendChild(opt);
        });
    },

    open(contextName) {
        this._loadFavourites();
        this._initListeners();
        this._activeContext = contextName;

        // Pre-select current voice from context
        const context = EDGE_TTS_CONTEXTS[contextName];
        if (context) {
            this._selectedVoiceId = $(context.voiceId)?.value || null;
        }

        this._searchText = '';
        this._langFilter = 'all';
        this._filter = 'all';

        // Reset UI state
        const searchInput = $('evbSearchInput');
        if (searchInput) searchInput.value = '';
        const langSelect = $('evbLangSelect');
        if (langSelect) langSelect.value = 'all';
        $('evbFilterPills')?.querySelectorAll('.evb-pill').forEach(p => {
            p.classList.toggle('active', p.dataset.filter === 'all');
        });

        this._populateLanguages();
        this._render();

        $('evbOverlay')?.classList.add('open');
    },

    close() {
        $('evbOverlay')?.classList.remove('open');
        this._stopPreview();
        this._activeContext = null;
    },

    _getFilteredVoices() {
        const voices = this._getVoices();
        const search = this._searchText.toLowerCase();
        const langFilter = this._langFilter;
        const genderFilter = this._filter;

        return voices.filter(voice => {
            // Language / accent filter
            if (langFilter !== 'all') {
                if (voice.locale !== langFilter && evbGetBaseLang(voice.locale) !== langFilter) return false;
            }

            // Gender filter
            if (genderFilter === 'male' || genderFilter === 'female') {
                const gender = evbDeriveGender(voice).toLowerCase();
                if (gender !== genderFilter) return false;
            }

            // Favourites filter
            if (genderFilter === 'favourites') {
                if (!this._favourites.includes(voice.id)) return false;
            }

            // Search filter
            if (search) {
                const searchStr = `${voice.id} ${voice.label || ''} ${voice.friendly_name || ''} ${evbGetDialect(voice.locale)} ${evbGetBaseLang(voice.locale)}`.toLowerCase();
                if (!searchStr.includes(search)) return false;
            }

            return true;
        }).sort((a, b) => {
            const dialA = evbGetDialect(a.locale);
            const dialB = evbGetDialect(b.locale);
            if (dialA !== dialB) return dialA.localeCompare(dialB);
            return evbExtractSimpleName(a).localeCompare(evbExtractSimpleName(b));
        });
    },

    _render() {
        const grid = $('evbGrid');
        if (!grid) return;

        const filtered = this._getFilteredVoices();
        const total = this._getVoices().length;

        // Update count
        const countEl = $('evbVoiceCount');
        if (countEl) countEl.textContent = `${filtered.length} of ${total} voices`;

        if (filtered.length === 0) {
            grid.innerHTML = '<div class="evb-empty">No voices match your filters.</div>';
            this._updateFooter();
            return;
        }

        grid.innerHTML = '';
        const fragment = document.createDocumentFragment();

        filtered.forEach(voice => {
            const simpleName = evbExtractSimpleName(voice);
            const gender = evbDeriveGender(voice);
            const dialect = evbGetDialect(voice.locale);
            const initials = simpleName.charAt(0).toUpperCase();
            const isFav = this._favourites.includes(voice.id);
            const isSelected = this._selectedVoiceId === voice.id;

            const card = document.createElement('div');
            card.className = `evb-card${isSelected ? ' selected' : ''}`;
            card.dataset.voiceId = voice.id;

            const genderClass = gender.toLowerCase() === 'female' ? 'female' :
                                gender.toLowerCase() === 'male' ? 'male' : 'neutral';

            card.innerHTML = `
                <div class="evb-avatar ${genderClass}">${initials}</div>
                <div class="evb-info">
                    <div class="evb-name">${simpleName}</div>
                    <div class="evb-meta">
                        <span>🌍 ${dialect}</span>
                        <span>•</span>
                        <span>${gender}</span>
                    </div>
                </div>
                <div class="evb-actions">
                    <button class="evb-fav-btn${isFav ? ' active' : ''}" title="Favourite" data-voice-id="${voice.id}">
                        ${isFav ? '❤️' : '🤍'}
                    </button>
                    <button class="evb-preview-btn" title="Preview" data-voice-id="${voice.id}">
                        ▶
                    </button>
                </div>
            `;

            // Card click = select
            card.addEventListener('click', (e) => {
                if (e.target.closest('.evb-fav-btn') || e.target.closest('.evb-preview-btn')) return;
                this._selectInBrowser(voice.id, simpleName);
            });

            // Fav button
            card.querySelector('.evb-fav-btn').addEventListener('click', (e) => {
                e.stopPropagation();
                this._toggleFavourite(voice.id);
            });

            // Preview button
            card.querySelector('.evb-preview-btn').addEventListener('click', (e) => {
                e.stopPropagation();
                this._previewVoice(voice.id, e.currentTarget);
            });

            fragment.appendChild(card);
        });

        grid.appendChild(fragment);
        this._updateFooter();
    },

    _selectInBrowser(voiceId, displayName) {
        this._selectedVoiceId = voiceId;
        // Update card visual
        $('evbGrid')?.querySelectorAll('.evb-card').forEach(c => {
            c.classList.toggle('selected', c.dataset.voiceId === voiceId);
        });
        this._updateFooter();
    },

    _updateFooter() {
        const nameEl = $('evbSelectedName');
        const confirmBtn = $('evbConfirmBtn');
        if (this._selectedVoiceId) {
            const voice = this._getVoices().find(v => v.id === this._selectedVoiceId);
            const name = voice ? evbExtractSimpleName(voice) : this._selectedVoiceId;
            if (nameEl) nameEl.textContent = name;
            if (confirmBtn) confirmBtn.disabled = false;
        } else {
            if (nameEl) nameEl.textContent = 'None';
            if (confirmBtn) confirmBtn.disabled = true;
        }
    },

    _toggleFavourite(voiceId) {
        const idx = this._favourites.indexOf(voiceId);
        if (idx >= 0) {
            this._favourites.splice(idx, 1);
        } else {
            this._favourites.push(voiceId);
        }
        this._saveFavourites();

        // If on favourites filter, re-render full grid
        if (this._filter === 'favourites') {
            this._render();
        } else {
            // Just update the fav button state
            const btn = $('evbGrid')?.querySelector(`.evb-fav-btn[data-voice-id="${voiceId}"]`);
            if (btn) {
                const isFav = this._favourites.includes(voiceId);
                btn.classList.toggle('active', isFav);
                btn.innerHTML = isFav ? '❤️' : '🤍';
            }
        }
    },

    _stopPreview() {
        if (this._previewAudio) {
            this._previewAudio.pause();
            this._previewAudio = null;
        }
    },

    async _previewVoice(voiceId, btnEl) {
        this._stopPreview();

        const originalContent = btnEl.innerHTML;
        btnEl.innerHTML = '⏳';
        btnEl.classList.add('loading');

        try {
            const edgeOpts = this._activeContext ? getEdgeTtsOptions(this._activeContext) : { rate: 0, pitch: 0, volume: 0 };
            const response = await api('/api/preview-voice', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text: 'Hello, this is a voice preview. I hope you like my voice!',
                    voice: voiceId,
                    vo_mode: 'Use Audiogram',
                    edge_tts_options: edgeOpts
                })
            });

            if (response.audio_url || response.audio) {
                const audio = new Audio(response.audio_url || response.audio);
                this._previewAudio = audio;

                audio.onended = () => {
                    btnEl.innerHTML = originalContent;
                    btnEl.classList.remove('loading');
                };
                audio.onerror = () => {
                    btnEl.innerHTML = originalContent;
                    btnEl.classList.remove('loading');
                };

                await audio.play();
            } else {
                throw new Error(response.error || 'Preview failed');
            }
        } catch (err) {
            console.error('Audiogram preview error:', err);
            btnEl.innerHTML = originalContent;
            btnEl.classList.remove('loading');
        }
    },

    _confirmSelection() {
        if (!this._selectedVoiceId || !this._activeContext) return;

        const context = EDGE_TTS_CONTEXTS[this._activeContext];
        if (!context) return;

        // Update the voice dropdown for this context
        const voiceSelect = $(context.voiceId);
        if (voiceSelect) {
            // Check if option exists, if not add it
            let optionExists = false;
            for (const opt of voiceSelect.options) {
                if (opt.value === this._selectedVoiceId) {
                    optionExists = true;
                    break;
                }
            }
            if (!optionExists) {
                // Re-populate with edge voices to ensure it's there
                populateEdgeVoiceDropdown(
                    context.voiceId,
                    this._getVoices(),
                    this._selectedVoiceId
                );
            } else {
                voiceSelect.value = this._selectedVoiceId;
            }
        }

        this.close();
        // Trigger form auto-save
        FormSettingsController.save();
    }
};

const DropdownController = {
    async init() {
        try {
            // Fetch all dropdown data in parallel
            const [voices, fonts, effects, animations, sceneModes, edgeTtsConfig, ttsConfig, llmProviders, llmModels] = await Promise.all([
                api('/api/voices').catch(() => ({})),
                api('/api/fonts').catch(() => ({})),
                api('/api/effects').catch(() => []),
                api('/api/animations').catch(() => []),
                api('/api/scene-modes').catch(() => []),
                api('/api/edge-tts-config').catch(() => ({ voices: [], defaults: { rate: 0, pitch: 0, volume: 0 } })),
                api('/api/tts-models').catch(() => ({ models: [] })),
                api('/api/llm-providers').catch(() => ({})),
                api('/api/llm-models').catch(() => ({ default_provider: 'openrouter', default_model: 'qwen/qwen3.5-flash-02-23', providers: {} }))
            ]);

            // Store in state
            AppState.dropdownData = {
                voices,
                fonts,
                effects,
                animations,
                sceneModes,
                edgeVoices: normalizeAudiogramVoices(edgeTtsConfig.voices || []),
                edgeDefaults: edgeTtsConfig.defaults || { rate: 0, pitch: 0, volume: 0 },
                ttsModels: Array.isArray(ttsConfig.models) ? ttsConfig.models : [],
                ttsVoices: Object.fromEntries(
                    (Array.isArray(ttsConfig.models) ? ttsConfig.models : [])
                        .map(model => [model.id, Array.isArray(model.voices) ? model.voices : []])
                ),
                llmProviders,
                llmModels,
            };

            // Populate all dropdowns
            this.populateAll();

            // Setup TTS engine to voice dropdown swapping.
            this.setupTtsEngineSwitching();

        } catch (error) {
            console.error('Failed to load dropdowns:', error);
        }
    },

    populateAll() {
        const { voices, fonts, effects, animations, sceneModes, llmProviders } = AppState.dropdownData;

        // Keep every TTS selector bound to the same server-advertised model
        // catalog. The backend receives the selected model unchanged.
        Object.keys(TTS_MODEL_TO_VOICE_MAP).forEach(id => populateTtsModelDropdown(id));

        // Voice dropdowns
        ['voiceSelect', 'shortVoiceSelect', 'bulkShortVoice', 'bulkLongVoice', 'defaultVoice'].forEach(id => {
            if (id === 'defaultVoice') {
                populateDropdown(id, voices, 'Adam (Male US)');
            }
        });
        Object.values(EDGE_TTS_CONTEXTS).forEach(context => syncVoiceDropdownForContext(context.name));

        // Font dropdowns
        // Fix: Use 'flat' key from API response, fallback to flattening values safely
        const fontList = fonts.flat || fonts.all || (Array.isArray(fonts) ? fonts : []) || [];
        ['fontSelect', 'shortFontSelect', 'bulkLongFontSelect', 'bulkShortFontSelect',
            'hlFontLong', 'hlFontShort', 'hlFontBulk',
            'topHeadingFontLong', 'topHeadingFontShort', 'topHeadingFontBulk'].forEach(id => {
                populateDropdown(id, fontList, 'Montserrat-Bold');
            });

        // Animation dropdowns
        ['captionAnimation', 'bulkLongCaptionAnimation', 'bulkShortCaptionAnimation',
            'hlAnimationLong', 'hlAnimationShort', 'hlAnimationBulk'].forEach(id => {
                populateDropdown(id, animations, 'Pop');
            });

        // Effect dropdowns
        ['effectSelect', 'settingEffectSelect', 'shortEffect', 'bulkLongEffectSelect', 'bulkShortEffect'].forEach(id => {
            populateDropdown(id, effects, 'Zoom Combo');
        });

        // Scene mode dropdown
        ['sceneMode', 'bulkLongSceneMode', 'shortSceneMode', 'bulkShortSceneMode'].forEach(id => {
            const preferredSceneMode = 'Every Beat';
            populateDropdown(id, sceneModes, preferredSceneMode);
        });

        ['llmProviderSelect'].forEach((id) => {
            populateSelectFromMap(id, Object.keys(llmProviders || {}).length ? llmProviders
                : { openrouter: {label: 'OpenRouter — Qwen 3.5 Flash'} }, AppState.llmProvider);
        });

        syncLlmModelSelects(AppState.llmModel || providerDefaultModel());


        // Attach TTS engine change listeners for dynamic voice swapping.
        Object.keys(TTS_MODEL_TO_VOICE_MAP).forEach(ttsModelId => {
            const el = $(ttsModelId);
            if (el) {
                el.addEventListener('change', () => updateVoicesForTtsModel(ttsModelId));
            }
        });

        $('llmProviderSelect')?.addEventListener('change', () => {
            AppState.llmProvider = 'openrouter';
            AppState.llmModel = providerDefaultModel();
            renderProviderPanels();
            updateGenerationCostProfile(AppState.generationCostProfile);
        });

        Object.keys(EDGE_TTS_CONTEXTS).forEach((contextName) => {
            applyEdgeDefaultsToContext(contextName, AppState.dropdownData.edgeDefaults || {});
            syncVoiceDropdownForContext(contextName);
        });
    },

    // Keep every voice selector synchronized with its selected TTS model.
    setupTtsEngineSwitching() {
        Object.values(EDGE_TTS_CONTEXTS).forEach((context) => {
            if (context.ttsModelId) $(context.ttsModelId)?.addEventListener('change', () => {
                syncVoiceDropdownForContext(context.name);
            });
        });
    }
};

DropdownController.setupTtsEngineSwitching = function () {
    Object.values(EDGE_TTS_CONTEXTS).forEach((context) => {
        if (context.ttsModelId) $(context.ttsModelId)?.addEventListener('change', () => {
            syncVoiceDropdownForContext(context.name);
        });

        ['rate', 'pitch', 'volume'].forEach((key) => {
            $(context[`${key}Id`])?.addEventListener('input', () => {
                updateEdgeSliderValue(context.name, key);
            });
        });
    });
};

// =============================================================================
// INPUT MODE CONTROLLER
// =============================================================================

const InputModeController = {
    setMode(mode, type) {
        if (type === 'long') {
            AppState.longInputMode = 'script';
            $('longTitleMode')?.classList.add('hidden');
            $('longScriptMode')?.classList.remove('hidden');
            QuickCreateController.reflectMode('script');
        } else if (type === 'short') {
            AppState.shortInputMode = 'script';
            $('shortTitleMode')?.classList.add('hidden');
            $('shortScriptMode')?.classList.remove('hidden');
        } else if (type === 'bulk-long') {
            AppState.bulkLongInputMode = 'script';
            $('bulkTitleMode')?.classList.add('hidden');
            $('bulkScriptMode')?.classList.remove('hidden');
        } else if (type === 'bulk-short') {
            AppState.bulkShortInputMode = 'script';
            $('bulkShortTitleMode')?.classList.add('hidden');
            $('bulkShortScriptMode')?.classList.remove('hidden');
        }

        // Update button states
        const activeEvent = typeof event !== 'undefined' ? event : null;
        const container = activeEvent?.target?.closest('.input-mode-toggle');
        if (container) {
            container.querySelectorAll('.mode-btn').forEach(btn => {
                btn.classList.toggle('active', btn.dataset.mode === mode);
            });
        }
    }
};

// Global input mode functions
function setInputMode(mode, type = 'long') { InputModeController.setMode(mode, type); }
function setShortFormInputMode(mode) { InputModeController.setMode(mode, 'short'); }
function setBulkInputMode(mode) { InputModeController.setMode(mode, 'bulk-long'); }
function setBulkShortInputMode(mode) { InputModeController.setMode(mode, 'bulk-short'); }

// =============================================================================
// QUICK CREATE CONTROLLER
// =============================================================================

const QuickCreateController = {
    suppressModeSync: false,
    placeholders: {
        script: 'Enter your script here. Paste the full script and Vidzeo will generate directly from it.'
    },

    init() {
        const input = $('quickCreateInput');
        const notify = $('progressNotify');

        if (input) {
            input.addEventListener('input', () => this.syncToPipeline());
        }

        PlanWordLimitController.bind();

        if (notify) {
            const saved = localStorage.getItem('vidzeo_notify_when_ready');
            // Completion notifications are part of the normal desktop
            // workflow. Users can still turn them off from the progress card.
            notify.checked = saved !== '0';
            notify.addEventListener('change', async () => {
                localStorage.setItem('vidzeo_notify_when_ready', notify.checked ? '1' : '0');

                if (notify.checked && typeof Notification !== 'undefined' && Notification.permission === 'default') {
                    try {
                        await Notification.requestPermission();
                    } catch (error) {
                        console.warn('Notification permission request failed:', error);
                    }
                }
            });
        }

        this.syncFromPipeline();
        this.reflectMode('script');
    },

    reflectMode(mode) {
        const input = $('quickCreateInput');
        if (!input) return;

        AppState.longInputMode = 'script';
        input.placeholder = this.placeholders.script;
        input.rows = 7;
        input.classList.add('is-script');
        if (!this.suppressModeSync) {
            this.syncFromPipeline();
        }
        PlanWordLimitController.refreshAll({ silent: true });
    },

    setMode(mode) {
        const currentValue = $('quickCreateInput')?.value || '';
        AppState.longInputMode = 'script';
        if ($('videoScript')) {
            $('videoScript').value = currentValue;
        }

        this.suppressModeSync = true;
        setInputMode('script', 'long');
        this.suppressModeSync = false;

        if ($('videoScript') && !$('videoScript').value.trim()) {
            $('videoScript').value = currentValue;
        }

        this.syncFromPipeline();
    },

    syncToPipeline() {
        const value = $('quickCreateInput')?.value || '';
        AppState.longInputMode = 'script';
        if ($('videoScript')) {
            $('videoScript').value = value;
        }
        PlanWordLimitController.refreshAll({ silent: true });
    },

    syncFromPipeline() {
        const input = $('quickCreateInput');
        if (!input) return;

        AppState.longInputMode = 'script';
        input.value = $('videoScript')?.value || '';
        PlanWordLimitController.refreshAll({ silent: true });
    },

    openSettings() {
        this.syncToPipeline();
        VideoSettingsModalController.open();
    },

    closeSettings() {
        VideoSettingsModalController.close();
        $('quickCreateInput')?.focus();
    },

    openComposer() {
        this.closeSettings();
        $('quickCreateInput')?.focus();
        $('quickCreateShell')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    },

    generate() {
        this.syncToPipeline();
        generateVideo();
    },

    getPromptLabel() {
        const raw = $('videoScript')?.value || '';

        const normalized = raw.trim().replace(/\s+/g, ' ');
        return normalized.length > 56 ? `${normalized.slice(0, 56)}...` : (normalized || 'Preparing your video');
    }
};

const VideoSettingsModalController = {
    mappings: [
        ['longTtsModel', 'settingLongTtsModel', 'value'],
        ['voiceSelect', 'settingVoiceSelect', 'value'],
        ['whisperMode', 'settingWhisperMode', 'value'],
        ['musicVolume', 'settingMusicVolume', 'value'],
        ['captionsEnabled', 'settingCaptionsEnabled', 'checked'],
        ['captionPosition', 'settingCaptionPosition', 'value'],
        ['captionStyle', 'settingCaptionStyle', 'value'],
        ['captionFont', 'settingCaptionFont', 'value'],
        ['outputPath', 'settingOutputPath', 'value'],
        ['videoQuality', 'settingVideoQuality', 'value']
    ],

    init() {
        this.rebuildOptions();
        this.syncFromPipeline();
        $('settingLongTtsModel')?.addEventListener('change', () => {
            syncVoiceDropdownForContext('setting-long');
        });
    },

    rebuildOptions() {
        this.mappings.forEach(([sourceId, targetId]) => {
            const source = $(sourceId);
            const target = $(targetId);
            if (!source || !target || source.tagName !== 'SELECT') return;
            target.innerHTML = source.innerHTML;
        });
    },

    syncFromPipeline() {
        this.rebuildOptions();
        this.mappings.forEach(([sourceId, targetId, type]) => {
            const source = $(sourceId);
            const target = $(targetId);
            if (!source || !target) return;

            if (type === 'checked') {
                target.checked = !!source.checked;
            } else {
                target.value = source.value;
            }
        });
        syncVoiceDropdownForContext('setting-long', $('voiceSelect')?.value);
    },

    syncToPipeline() {
        this.mappings.forEach(([sourceId, targetId, type]) => {
            const source = $(sourceId);
            const target = $(targetId);
            if (!source || !target) return;

            if (type === 'checked') {
                source.checked = !!target.checked;
                source.dispatchEvent(new Event('change', { bubbles: true }));
            } else {
                source.value = target.value;
                source.dispatchEvent(new Event('change', { bubbles: true }));
                source.dispatchEvent(new Event('input', { bubbles: true }));
            }
        });
        syncVoiceDropdownForContext('long', $('settingVoiceSelect')?.value);
        if ($('voiceSelect') && $('settingVoiceSelect')) {
            $('voiceSelect').value = $('settingVoiceSelect').value;
            $('voiceSelect').dispatchEvent(new Event('change', { bubbles: true }));
        }

        if ($('musicVolumeVal') && $('musicVolume')) {
            $('musicVolumeVal').textContent = `${$('musicVolume').value}%`;
        }
    },

    open() {
        this.syncFromPipeline();
        $('videoSettingsModal')?.classList.remove('hidden');
        document.body.classList.add('long-video-settings-open');
    },

    close() {
        this.syncToPipeline();
        FormSettingsController.save();
        $('videoSettingsModal')?.classList.add('hidden');
        document.body.classList.remove('long-video-settings-open');
    },

    save() {
        this.syncToPipeline();
        FormSettingsController.save();
        this.close();
    }
};

function toggleFormat() {}
function openLongVideoSettings() { QuickCreateController.openSettings(); }
function closeLongVideoSettings() { QuickCreateController.closeSettings(); }
function openLongVideoComposer() { QuickCreateController.openComposer(); }
function generateVideoFromComposer() { QuickCreateController.generate(); }
function saveLongVideoSettings() {
    VideoSettingsModalController.save();
    showToast('Video settings saved', 'success');
}

// =============================================================================
// IMAGE SOURCE CONTROLLER
// =============================================================================

// Toggle Bulk Long Description section
function toggleBulkLongDescription() {
    const body = $('bulkLongDescBody');
    const arrow = $('bulkLongDescArrow');
    if (!body) return;
    body.classList.toggle('hidden');
    arrow?.classList.toggle('open');
}

// Toggle Bulk Short Description section
function toggleBulkShortDescription() {
    const body = $('bulkShortDescBody');
    const arrow = $('bulkShortDescArrow');
    if (!body) return;
    body.classList.toggle('hidden');
    arrow?.classList.toggle('open');
}

function toggleImageSource() {
    const source = $('imageSource')?.value;
    const isCustom = (source === 'Custom Images');
    const isMixedMedia = (source === 'Mixed Media');
    const isStockMedia = (source === 'Stock Media' || source === 'Real Media' || isMixedMedia);
    const isWebImages = (source === 'Web Images');

    // Show Custom Upload only for Custom
    $('customImageUpload')?.classList.toggle('hidden', !isCustom);

    const stockSources = $('stockMediaSourcesV2');
    if (stockSources) {
        stockSources.style.display = isStockMedia ? 'block' : 'none';
    }

    setElementVisibility('webImagesNotice', isWebImages);
    setElementVisibility('mixedMediaNotice', isMixedMedia);
}

function toggleBulkLongImageSource() {
    const source = $('bulkLongImageSource')?.value;
    const isWebImages = (source === 'Web Images');
    const isMixedMedia = (source === 'Mixed Media');
    const isStockMedia = (source === 'Stock Media' || source === 'Real Media' || isMixedMedia);
    setElementVisibility('bulkLongWebImagesNotice', isWebImages);
    setElementVisibility('bulkLongMixedMediaNotice', isMixedMedia);
    const stockSources = $('bulkLongStockMediaSources');
    if (stockSources) {
        stockSources.style.display = isStockMedia ? 'block' : 'none';
    }
}

function getSelectedMediaSources(scope = 'long') {
    const ids = scope === 'bulk-long'
        ? {
            pexels: 'bulkLongSourcePexels',
            pixabay: 'bulkLongSourcePixabay'
        }
        : {
            pexels: 'sourcePexels',
            pixabay: 'sourcePixabay'
        };

    return [
        $(ids.pexels)?.checked ? 'pexels' : null,
        $(ids.pixabay)?.checked ? 'pixabay' : null
    ].filter(Boolean);
}

function validateStockMediaSelection(imageSource, mediaSources) {
    const isStockMedia = imageSource === 'Stock Media' || imageSource === 'Real Media';
    if (!isStockMedia) return null;

    const hasProvider = mediaSources.includes('pexels') || mediaSources.includes('pixabay');
    if (!hasProvider) {
        return 'Select at least one stock provider: Pexels or Pixabay.';
    }

    const hasMatchingKey =
        (mediaSources.includes('pexels') && AppState.apiKeys.pexels) ||
        (mediaSources.includes('pixabay') && AppState.apiKeys.pixabay);
    if (!hasMatchingKey) {
        return 'Add a Pexels or Pixabay API key in Settings before using Stock Media or Mixed Media.';
    }

    return null;
}

function formatMediaContractSummary(map) {
    const entries = Object.entries(map || {})
        .filter(([, value]) => Number(value) > 0);
    if (!entries.length) return '';
    return entries.map(([label, value]) => `${label}: ${value}`).join(' | ');
}

function renderMediaContractError(item) {
    const contract = item?.error_detail?.media_contract;
    if (!contract) return '';

    const targetSummary = formatMediaContractSummary(contract.target_counts);
    const resolvedSummary = formatMediaContractSummary(contract.resolved_counts);
    const unresolvedScenes = Array.isArray(contract.unresolved_scene_indices) && contract.unresolved_scene_indices.length
        ? contract.unresolved_scene_indices.join(', ')
        : '';
    const failureHtml = Array.isArray(contract.source_failures)
        ? contract.source_failures.map((failure) => {
            const source = escapeHtml(failure.source || 'Media Source');
            const resolved = Number(failure.resolved_count || 0);
            const requested = Number(failure.requested_count || 0);
            const scenes = Array.isArray(failure.unresolved_scene_indices) && failure.unresolved_scene_indices.length
                ? failure.unresolved_scene_indices.join(', ')
                : '';
            const message = escapeHtml(failure.error || 'Unable to satisfy the requested scene contract.');
            const auditPath = failure.audit_path
                ? `<div style="margin-top:4px; color:#fecaca;">Audit: ${escapeHtml(failure.audit_path)}</div>`
                : '';
            return `
                <div style="margin-top:8px; padding:8px; border-radius:8px; background:rgba(127,29,29,0.28); border:1px solid rgba(248,113,113,0.2); text-align:left;">
                    <div style="font-weight:700; color:#fecaca;">${source}: ${resolved}/${requested} scenes resolved</div>
                    <div style="margin-top:4px; color:#fca5a5;">${message}</div>
                    ${scenes ? `<div style="margin-top:4px; color:#fecaca;">Unresolved scenes: ${escapeHtml(scenes)}</div>` : ''}
                    ${auditPath}
                </div>
            `;
        }).join('')
        : '';

    return `
        <div style="margin-top:10px; padding:10px; border-radius:10px; background:rgba(127,29,29,0.2); border:1px solid rgba(248,113,113,0.24); text-align:left;">
            <div style="font-weight:700; color:#fecaca;">Media contract failed before render</div>
            ${targetSummary ? `<div style="margin-top:4px; color:#fecaca;">Target: ${escapeHtml(targetSummary)}</div>` : ''}
            ${resolvedSummary ? `<div style="margin-top:4px; color:#fecaca;">Resolved: ${escapeHtml(resolvedSummary)}</div>` : ''}
            ${unresolvedScenes ? `<div style="margin-top:4px; color:#fca5a5;">Unresolved scenes: ${escapeHtml(unresolvedScenes)}</div>` : ''}
            ${failureHtml}
        </div>
    `;
}

function validateSelectedLlmProvider() {
    return null;
}

// =============================================================================
// PROGRESS CONTROLLER & USER STATUS FORMATTER
// =============================================================================

function formatUserProgressStatusBase(rawText = '', percent = 0) {
    const text = String(rawText || '').trim();
    if (!text) {
        if (percent >= 100) return 'Video Ready';
        if (percent >= 75) return 'Rendering video';
        if (percent >= 40) return 'Preparing media';
        if (percent >= 25) return 'Searching for media';
        if (percent >= 10) return 'Generating narration';
        return 'Analyzing your script';
    }

    const lower = text.toLowerCase();
    const countMatch = text.match(/(\d+)\s*(?:\/|of)\s*(\d+)/i);

    // Explicit pause, retry, or failure states must NEVER be swallowed into generic stage labels
    if (/no verified media|click retry|paused|waiting for verified media|waiting for openrouter|waiting for free ram|review remotion edit|retry remotion render/i.test(text)) {
        return text;
    }
    if (/generation failed|failed|error:/i.test(text)) {
        return text;
    }
    const isFinalMessage = /^(?:complete!?|completed|video ready|finished|done)$/i.test(text);
    if (percent >= 100 || isFinalMessage) {
        return 'Video Ready';
    }
    if (/\b(?:queued|waiting in queue)\b/.test(lower)) return 'Waiting in queue';
    if (lower.includes('submitting')) return 'Submitting video';
    if (/^starting(?:\.\.\.)?$/i.test(text)) return 'Starting video generation';
    if (/^(?:downloading youtube footage|searching youtube|checking youtube footage|checking media relevance)/i.test(text)) {
        return text.replace(/\s*[—–�-]?\s*still working.*$/i, '').slice(0, 120);
    }
    // Visual verification and quality checks
    if (lower.includes('visual verif') || lower.includes('vision check') || lower.includes('qwen vision') || lower.includes('visual gate')) {
        if (countMatch) return `Verifying media quality ${countMatch[1]}/${countMatch[2]}`;
        return 'Verifying media quality';
    }
    if (lower.includes('deferred visual') || lower.includes('batch verif')) return 'Running visual quality checks';
    // Editorial planning phases
    if (lower.includes('editorial director') || lower.includes('edit plan') || lower.includes('scene composition')) return 'Planning editorial layout';
    if (lower.includes('semantic edit') || lower.includes('visual grammar') || lower.includes('template assign')) return 'Composing visual grammar';
    if (lower.includes('storyboard') || lower.includes('scene brain') || lower.includes('shot plan')) return 'Building storyboard';
    // Provider details belong to one neutral per-scene phase. Check this
    // before the render threshold because parallel recovery branches can
    // report media work after another branch has advanced the fraction.
    if (lower.includes('youtube') || lower.includes('yt-v2') || lower.includes('yt clips')) {
        if (countMatch) return `Finding scene ${countMatch[1]}/${countMatch[2]}`;
        return 'Finding YouTube footage';
    }
    if (lower.includes('web') || lower.includes('image') || lower.includes('stock')) {
        if (countMatch) return `Finding scene ${countMatch[1]}/${countMatch[2]}`;
        return lower.includes('stock') ? 'Finding stock media' : 'Finding web images';
    }
    if (lower.includes('waiting for previous video render') || lower.includes('waiting for previous render') || lower.includes('waiting for active render') || lower.includes('waiting for the active render')) {
        return 'Waiting for active render to finish';
    }
    if (lower.includes('preparing the video renderer')) return 'Preparing video renderer';
    if (lower.includes('loading the documentary edit')) return 'Loading documentary edit';
    if (lower.includes('starting render workers')) return 'Starting render workers';
    if (lower.includes('remotion frame progress')) return 'Rendering scenes';
    if (lower.includes('finalizing the remotion render')) return 'Finishing render';
    // Audio processing stages
    if (lower.includes('sound effect') || lower.includes('sfx') || lower.includes('sound cue')) return 'Adding sound effects';
    if (lower.includes('background music') || lower.includes('music track')) return 'Adding background music';
    if (lower.includes('audio mix') || lower.includes('loudness') || lower.includes('normalize')) return 'Mixing audio';
    if (percent >= 70 || lower.includes('rendering video') || lower.includes('ffmpeg assembly') || lower.includes('final mux') || lower.includes('encoding final') || lower.includes('finaliz')) {
        if (percent >= 94 || lower.includes('final')) return 'Finalizing video';
        if (countMatch && lower.includes('rendering')) {
            return `Rendering video ${countMatch[1]}/${countMatch[2]}`;
        }
        return 'Rendering video';
    }
    if (lower.includes('caption') || lower.includes('subtitle')) return 'Generating captions';
    if (['query plan', 'query planning', 'source-query', 'scene plan', 'visual plan'].some((word) => lower.includes(word))) return 'Planning visual searches';
    if (countMatch && ['media', 'scene', 'asset', 'rescue'].some((word) => lower.includes(word))) return `Preparing media ${countMatch[1]}/${countMatch[2]}`;
    if (['media', 'footage', 'pool', 'candidate', 'rescue'].some((word) => lower.includes(word))) return percent >= 40 ? 'Preparing media' : 'Searching for media';
    if (lower.includes('transcrib') || lower.includes('whisper') || lower.includes('align')) return 'Aligning narration';
    if (lower.includes('tts') || lower.includes('voice') || lower.includes('audio')) return 'Generating narration';
    if (lower.includes('script') || lower.includes('analyz') || lower.includes('beat') || lower.includes('llm') || percent < 15) {
        return 'Analyzing your script';
    }
    if (percent < 30) {
        return 'Generating narration';
    }
    if (percent < 50) {
        return 'Searching for media';
    }
    if (percent < 75) {
        return 'Preparing media';
    }
    return 'Rendering video';
}

function formatUserProgressStatus(rawText = '', percent = 0) {
    const clean = formatUserProgressStatusBase(rawText, percent);
    const lower = String(rawText || '').toLowerCase();
    const numericPercent = Number(percent) || 0;
    if (numericPercent >= 100 || clean === 'Video Ready') return clean;
    if ((lower.includes('still working') || lower.includes('please wait')) && ![
        'Waiting in queue', 'Submitting video', 'Starting video generation'
    ].includes(clean)) {
        // Keep the overlay's primary status short. The detailed heartbeat,
        // including elapsed seconds, remains available in Runtime logs.
        return clean;
    }
    return clean;
}

window.formatUserProgressStatus = formatUserProgressStatus;

const ProgressController = {
    activeQueueId: null,
    activeTitle: '',
    hideTimer: null,
    dismissed: false,
    completionHandledFor: null,
    lastProgressSeq: 0,
    lastProgressPercent: 0,
    lastServerUpdateAt: 0,
    lastObservedServerSeq: 0,
    lastObservedServerProgress: 0,
    lastObservedServerText: '',
    activeStartedAt: 0,
    liveWaitTimer: null,
    // Queue progress is also the backend's durable activity stream. Keep the
    // last sequence number per job so polling can surface each server event in
    // the Runtime diagnostics panel exactly once, even when the overlay is
    // dismissed or the queue view is rebuilt.
    runtimeEventSeqByJob: {},

    resetSession({ hideOverlay = false } = {}) {
        if (this.hideTimer) {
            clearTimeout(this.hideTimer);
            this.hideTimer = null;
        }
        if (this.liveWaitTimer) {
            clearInterval(this.liveWaitTimer);
            this.liveWaitTimer = null;
        }
        this.activeQueueId = null;
        this.activeTitle = '';
        this.dismissed = false;
        this.completionHandledFor = null;
        this.lastProgressSeq = 0;
        this.lastProgressPercent = 0;
        this.lastServerUpdateAt = 0;
        this.lastObservedServerSeq = 0;
        this.lastObservedServerProgress = 0;
        this.lastObservedServerText = '';
        this.activeStartedAt = 0;
        this.clearPausedState();
        const steps = $('progressSteps');
        if (steps) steps.replaceChildren();
        const overlay = $('progressOverlay');
        if (overlay) {
            overlay.style.cursor = '';
            overlay.onclick = null;
            if (hideOverlay) overlay.classList.add('hidden');
        }
    },

    beginQueueItem(itemOrId, fallbackTitle = '') {
        const item = itemOrId && typeof itemOrId === 'object' ? itemOrId : null;
        const id = item?.id || itemOrId || null;
        const config = item?.config || item || {};
        this.resetSession();
        this.activeQueueId = id;
        this.activeTitle = config.title || fallbackTitle || 'Generating Video...';
        if (item) this.trackQueueItem(item, this.activeTitle);
    },

    ensureLiveWaitTimer() {
        if (this.liveWaitTimer) return;
        this.liveWaitTimer = window.setInterval(() => {
            if (!this.activeQueueId || !this.lastServerUpdateAt) return;
            const items = [...(AppState.longQueue || []), ...(AppState.shortQueue || [])];
            const item = items.find((row) => row?.id === this.activeQueueId);
            if (!item || item.status !== 'processing') return;
            const staleFor = Date.now() - this.lastServerUpdateAt;
            if (staleFor < 6000 || this.dismissed) return;
            const elapsed = this.activeStartedAt
                ? Math.max(1, Math.round((Date.now() - this.activeStartedAt) / 1000))
                : Math.max(1, Math.round(staleFor / 1000));
            const base = String(item.progress_text || 'Processing')
                .replace(/\s*[—-]\s*still working.*$/i, '')
                .trim();
            this.update(this.lastProgressPercent, `${base} — still working (${elapsed}s); please wait`);
        }, 1000);
    },

    recordQueueRuntimeEvents(item, title, events = []) {
        const jobId = String(item?.id || '').trim();
        if (!jobId || !Array.isArray(events) || !events.length) return;
        const previous = Number(this.runtimeEventSeqByJob[jobId] || 0);
        const ordered = events
            .filter((event) => event && Number.isFinite(Number(event.seq)))
            .map((event) => ({
                ...event,
                seq: Number(event.seq),
                progress: Math.max(0, Math.min(100, Number(event.progress) || 0)),
            }))
            .sort((a, b) => a.seq - b.seq);
        const unseen = ordered.filter((event) => event.seq > previous);
        unseen.forEach((event) => {
            const message = formatUserProgressStatus(event.text || '', event.progress);
            if (!message) return;
            const level = /error|failed|exception|stopped/i.test(String(event.text || ''))
                ? 'error'
                : 'info';
            appendRuntimeLog(`[${title || 'video'}] ${message}`, level);
        });
        if (ordered.length) {
            this.runtimeEventSeqByJob[jobId] = Math.max(previous, ordered[ordered.length - 1].seq);
        }
        // A long-running desktop session should not retain an unbounded map.
        const ids = Object.keys(this.runtimeEventSeqByJob);
        if (ids.length > 64) {
            ids.slice(0, ids.length - 64).forEach((id) => delete this.runtimeEventSeqByJob[id]);
        }
    },

    beginSubmission(title = '') {
        this.resetSession();
        this.activeTitle = title || 'Generating Video...';
        this.show(this.activeTitle, 'Submitting your request to the backend...', 6);
    },

    getMetaText(percent, text = '') {
        const cleanStatus = formatUserProgressStatus(text, percent);
        // This is the small line above the title. Keep it stable and short so
        // long titles and stage labels never create a crowded/wrapped header.
        if (Number(percent) >= 100 || cleanStatus === 'Video Ready') return 'Ready';
        if (/error|failed|exception|stopped|paused|retry|no verified media/i.test(String(text || ''))) return 'Action needed';
        if (cleanStatus === 'Waiting in queue') return 'Queued';
        if (cleanStatus === 'Waiting for active render to finish') return 'Queued for render';
        if (cleanStatus === 'Submitting video' || cleanStatus === 'Starting video generation') return 'Starting';
        return 'Please wait';
    },

    show(title, text, percent = 0) {
        if (this.hideTimer) {
            clearTimeout(this.hideTimer);
            this.hideTimer = null;
        }
        this.dismissed = false;
        $('progressOverlay')?.classList.remove('hidden');
        const cleanStatus = formatUserProgressStatus(text, percent);
        const progressTitle = $('progressTitle');
        const progressText = $('progressText');
        const progressMeta = $('progressMeta');
        if (progressTitle) progressTitle.textContent = title;
        if (progressText) progressText.textContent = cleanStatus;
        if (progressMeta) {
            progressMeta.textContent = this.getMetaText(percent, cleanStatus);
        }
        this.update(percent, cleanStatus);
    },

    update(percent, text = null) {
        const cleanStatus = text ? formatUserProgressStatus(text, percent) : null;
        const progressFill = $('progressFill');
        const progressPercent = $('progressPercent');
        const progressText = $('progressText');
        const progressMeta = $('progressMeta');
        if (progressFill) progressFill.style.width = `${percent}%`;
        if (progressFill) progressFill.classList.toggle('is-waiting', /\bstill working\b/i.test(String(text || cleanStatus || '')));
        if (progressPercent) progressPercent.textContent = `${Math.round(percent)}%`;
        if (cleanStatus && progressText) progressText.textContent = cleanStatus;
        if (progressMeta) {
            progressMeta.textContent = this.getMetaText(percent, cleanStatus || progressText?.textContent || '');
        }
    },

    updateSteps(events = []) {
        const container = $('progressSteps');
        if (!container) return;
        const ordered = (Array.isArray(events) ? events : [])
            .filter((event) => event && Number.isFinite(Number(event.seq)))
            .sort((a, b) => Number(a.seq) - Number(b.seq))
            .map((event) => ({
                ...event,
                progress: Math.max(0, Math.min(100, Number(event.progress) || 0)),
                displayText: formatUserProgressStatus(event.text || '', Number(event.progress) || 0)
            }))
            .reduce((rows, event) => {
                const seq = Number(event.seq);
                const previous = rows[rows.length - 1];
                if (previous && (Number(previous.seq) === seq || previous.displayText === event.displayText)) rows[rows.length - 1] = event;
                else rows.push(event);
                return rows;
            }, [])
            .slice(-6);
        container.replaceChildren();
        ordered.forEach((event) => {
            const row = document.createElement('div');
            row.className = 'progress-step';
            row.textContent = `${Math.round(Number(event.progress) || 0)}%  ${event.displayText}`;
            container.appendChild(row);
        });
    },

    trackQueueItem(item, fallbackTitle = '') {
        if (!item) return;

        const config = item.config || item;
        const title = config.title || fallbackTitle || this.activeTitle || 'Generating Video...';
        const rawProgress = Number(item.progress || 0);
        const status = item.status || 'queued';
        const rawStatusText = item.progress_text
            || (status === 'processing' ? 'Processing' : status === 'queued' ? 'Queued' : 'Finalizing video');
        const cleanStatusText = formatUserProgressStatus(rawStatusText, rawProgress);

        const events = Array.isArray(item.progress_events) ? item.progress_events : [];
        this.recordQueueRuntimeEvents(item, title, events);
        const latestEventSeq = events.reduce((max, event) => Math.max(max, Number(event?.seq) || 0), 0);
        const incomingSeq = Math.max(Number(item.progress_seq || 0), latestEventSeq);
        if (this.activeQueueId === item.id && incomingSeq && incomingSeq < this.lastProgressSeq) {
            return;
        }
        if (this.activeQueueId !== item.id) {
            this.lastProgressSeq = 0;
            this.lastProgressPercent = 0;
            this.lastObservedServerSeq = 0;
            this.lastObservedServerProgress = 0;
            this.lastObservedServerText = '';
        }
        const serverChanged = this.activeQueueId !== item.id
            || incomingSeq > this.lastObservedServerSeq
            || rawProgress !== this.lastObservedServerProgress
            || rawStatusText !== this.lastObservedServerText;
        if (serverChanged) this.lastServerUpdateAt = Date.now();
        this.lastObservedServerSeq = Math.max(this.lastObservedServerSeq, incomingSeq);
        this.lastObservedServerProgress = rawProgress;
        this.lastObservedServerText = rawStatusText;
        if (item.started_at) {
            const started = Date.parse(item.started_at);
            if (Number.isFinite(started)) this.activeStartedAt = started;
        }
        this.lastProgressSeq = Math.max(this.lastProgressSeq, incomingSeq);
        const progress = status === 'completed'
            ? 100
            : Math.max(this.lastProgressPercent, Math.max(0, Math.min(100, rawProgress)));
        this.lastProgressPercent = progress;
        this.updateSteps(events);

        this.activeQueueId = item.id;
        this.activeTitle = title;
        if (status === 'processing') this.ensureLiveWaitTimer();
        else if (this.liveWaitTimer) {
            clearInterval(this.liveWaitTimer);
            this.liveWaitTimer = null;
        }

        if (status === 'completed') {
            if (this.completionHandledFor !== item.id) {
                this.completionHandledFor = item.id;
                this.dismissed = false;
                this.show(title, item.result?.ready_to_publish === false ? 'Export ready — review needed' : 'Video Ready', 100);
                const completedVideoUrl = item.result?.video_url || item.result?.video || '';
                this.notifyReady(title, completedVideoUrl);
                maybeShowFreeUpgrade(`export:${item.id}`);
                GalleryController.queueAutoOpen(completedVideoUrl, title, item.result?.video_path || '');
                // Refresh the library immediately so a user who is already on
                // Projects sees the finished export without a manual reload.
                if (AppState.currentSection === 'gallery') {
                    GalleryController.load().catch((error) => {
                        console.warn('Project gallery refresh after completion failed:', error);
                    });
                }
                const overlay = $('progressOverlay');
                if (overlay) {
                    overlay.style.cursor = 'pointer';
                    overlay.onclick = () => {
                        overlay.style.cursor = '';
                        overlay.onclick = null;
                        this.dismissOverlay();
                        navigateTo('gallery');
                    };
                }
            } else {
                this.show(title, 'Video Ready', 100);
            }
            this.hideTimer = window.setTimeout(() => this.hide(), 5000);
        } else if (status === 'error') {
            this.dismissed = false;
            this.clearPausedState();
            this.show(title, item.error || 'Generation failed', progress);
        } else if (status === 'paused') {
            this.dismissed = false;
            const pauseMsg = item.progress_text || item.error || 'Generation paused — action needed';
            this.show(title, pauseMsg, progress);
            this.setPausedState(item);
        } else if (!this.dismissed) {
            this.clearPausedState();
            this.show(title, cleanStatusText, progress);
        }
    },


    syncWithQueue(items = []) {
        if (!items.length) {
            this.hide();
            return;
        }

        let activeItem = null;
        if (this.activeQueueId) {
            activeItem = items.find((item) => item.id === this.activeQueueId) || null;
        }

        // Auto-advance: if current item is completed, error, or not found, check if another item is processing
        if (!activeItem || ['completed', 'cancelled', 'error', 'shutdown_timeout'].includes(activeItem.status)) {
            const nextProcessing = items.find((item) => item.status === 'processing');
            if (nextProcessing && (!activeItem || activeItem.id !== nextProcessing.id)) {
                // If previous was already handled/dismissed or not found, switch to the active processing job
                if (!activeItem || this.dismissed || this.completionHandledFor === activeItem.id) {
                    this.activeQueueId = nextProcessing.id;
                    this.activeTitle = nextProcessing.config?.title || nextProcessing.title || 'Generating Video...';
                    activeItem = nextProcessing;
                }
            }
        }

        if (activeItem) {
            this.trackQueueItem(activeItem, this.activeTitle);
        } else {
            const anyActive = items.find((item) => item.status === 'processing' || item.status === 'queued');
            if (anyActive && !this.dismissed) {
                this.activeQueueId = anyActive.id;
                this.activeTitle = anyActive.config?.title || anyActive.title || 'Generating Video...';
                this.trackQueueItem(anyActive, this.activeTitle);
            } else {
                this.hide();
            }
        }
    },

    dismissOverlay() {
        this.clearPausedState();
        this.dismissed = true;
        const overlay = $('progressOverlay');
        if (overlay) {
            overlay.classList.add('hidden');
            overlay.style.cursor = '';
            overlay.onclick = null;
        }
    },

    notifyReady(title, videoUrl = '') {
        const notify = $('progressNotify');
        // Always show an in-app notification. It remains visible even when the
        // user has dismissed the progress overlay or disabled OS notifications.
        showToast(`Video ready: ${title}`, 'success');
        if (!notify?.checked) return;

        // Native Electron notifications work in the packaged app even when the
        // renderer has no browser Notification permission.
        if (window.electronAPI?.notifyVideoComplete) {
            window.electronAPI.notifyVideoComplete({
                title,
                videoUrl,
                body: `${title} finished rendering. Click to open Projects.`
            }).catch((error) => console.warn('Native notification failed:', error));
            return;
        }

        if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return;

        try {
            new Notification('Vidzeo video ready', {
                body: `${title} finished rendering.`
            });
        } catch (error) {
            console.warn('Notification failed:', error);
        }
    },

    setPausedState(item) {
        const actions = document.querySelector('.progress-actions');
        if (!actions) return;
        let retryBtn = document.getElementById('progressRetryBtn');
        if (!retryBtn) {
            retryBtn = document.createElement('button');
            retryBtn.id = 'progressRetryBtn';
            retryBtn.className = 'btn-generate';
            retryBtn.textContent = 'Retry Video';
            actions.prepend(retryBtn);
        }
        retryBtn.onclick = (e) => {
            if (e?.stopPropagation) e.stopPropagation();
            if (e?.preventDefault) e.preventDefault();
            this.clearPausedState();
            resumeQueueItem(item.type, item.id);
        };
        const fill = $('progressFill');
        if (fill) fill.classList.remove('is-waiting');
        const progressMeta = $('progressMeta');
        if (progressMeta) progressMeta.textContent = 'Action needed';
    },

    clearPausedState() {
        const retryBtn = document.getElementById('progressRetryBtn');
        if (retryBtn) retryBtn.remove();
    },

    hide() {
        this.resetSession({ hideOverlay: true });
    }
};
window.EdgeVoiceBrowser = EdgeVoiceBrowser;

// =============================================================================
// GLOBAL NAVIGATION - Create New
// =============================================================================

function createNewVideo() {
    if (!ensureStudioAccess()) {
        return;
    }

    // A composer is a new UI session. Background queue history remains on the
    // queue page, but its completed status/log state must not leak here.
    ProgressController.resetSession({ hideOverlay: true });

    // Clear Long Video inputs
    if ($('videoScript')) $('videoScript').value = '';
    if ($('videoName')) $('videoName').value = '';
    
    // Clear Short Video inputs
    if ($('shortVideoScript')) $('shortVideoScript').value = '';
    if ($('shortVideoName')) $('shortVideoName').value = '';
    
    // Script-first creation flow
    setInputMode('script', 'long');
    
    // Go to Long Video creation form
    navigateTo('long-video');
    closeLongVideoSettings();
    AccountController.render();
    if ($('quickCreateInput')) $('quickCreateInput').value = '';
    updateScriptWordAndCharCount($('quickCreateInput'));
    QuickCreateController.syncFromPipeline();

    window.setTimeout(() => {
        $('quickCreateInput')?.focus();
        $('quickCreateShell')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 80);
}
window.createNewVideo = createNewVideo;

function closeProgressOverlay(event = null) {
    if (event?.stopPropagation) event.stopPropagation();
    if (event?.preventDefault) event.preventDefault();
    ProgressController.dismissOverlay();
}
window.closeProgressOverlay = closeProgressOverlay;

function createNewWhileProcessing(event = null) {
    if (event?.stopPropagation) event.stopPropagation();
    if (event?.preventDefault) event.preventDefault();
    ProgressController.dismissOverlay();
    createNewVideo();
    showToast('Queue processing continues in the background', 'info');
}
window.createNewWhileProcessing = createNewWhileProcessing;

// =============================================================================
// GENERATION CARD CONTROLLER — InVideo-style inline preview
// =============================================================================

const GenCardController = {
    _cards: {},

    /** Create the generating card inside a container element */
    create(containerId, title = 'Generating your video...') {
        const container = $(containerId);
        if (!container) return null;

        const id = `gc_${Date.now()}`;
        const wrap = document.createElement('div');
        wrap.className = 'gen-card-wrap';
        wrap.id = id;
        wrap.innerHTML = `
            <div class="gen-card generating" id="${id}_card">
                <div class="gen-card-generating-body">
                    <div class="gen-spinner">
                        <div class="gen-spinner-icon">🎬</div>
                    </div>
                    <div class="gen-status-title">${title}</div>
                    <div class="gen-status-step" id="${id}_step">Initializing...</div>
                    <div class="gen-status-msg">This may take a few minutes depending on video length and settings.</div>
                </div>
            </div>`;
        container.prepend(wrap);
        wrap.scrollIntoView({ behavior: 'smooth', block: 'start' });
        this._cards[id] = { wrap, containerId };
        return id;
    },

    /** Update the status step label */
    update(id, stepText) {
        const stepEl = $(`${id}_step`);
        if (stepEl) stepEl.textContent = stepText;
    },

    /** Transform card into success preview */
    showDone(id, { videoUrl, name, duration, manifest }) {
        const card = $(`${id}_card`);
        if (!card) return;
        const mins = Math.floor(duration / 60);
        const secs = Math.round(duration % 60);
        const durStr = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
        
        window._manifestStore = window._manifestStore || {};
        window._manifestStore[id] = manifest;

        card.className = 'gen-card done';
        card.innerHTML = `
            <div class="gen-preview-thumb" onclick="playVideo('${videoUrl}')" title="Click to play video">
                <div class="gen-preview-thumb-inner">
                    <span class="gen-preview-play-icon">▶</span>
                    <span class="gen-preview-play-text">Play Video</span>
                </div>
            </div>
            <div class="gen-card-done-body">
                <div class="gen-card-top-row">
                    <span class="gen-done-badge">DONE</span>
                    <span class="gen-card-name">${name}</span>
                    <span class="gen-card-duration">${durStr}</span>
                </div>
                <div class="gen-card-actions">
                    <button class="gen-btn gallery" onclick="playVideo('${videoUrl}')">▶ Play</button>
                    <a class="gen-btn download" href="${videoUrl}" download="${name}.mp4">⬇ Download</a>
                    <button class="gen-btn gallery" onclick="navigateTo('gallery')">🎥 Gallery</button>
                </div>
            </div>`;

    },

    /** Transform card into error state */
    showError(id, errorMsg) {
        const card = $(`${id}_card`);
        if (!card) return;
        card.className = 'gen-card';
        card.style.borderColor = 'rgba(239,68,68,0.4)';
        card.innerHTML = `
            <div class="gen-card-generating-body">
                <div style="font-size:36px">⚠️</div>
                <div class="gen-status-title">Generation Failed</div>
                <div class="gen-status-msg" style="color:var(--error)">${errorMsg}</div>
                <button class="gen-btn download" onclick="this.closest('.gen-card-wrap').remove()" style="margin-top:8px">Dismiss</button>
            </div>`;
    }
};

// =============================================================================
// VIDEO GENERATION CONTROLLER
// =============================================================================

const VideoGenerationController = {

    async generateLongVideo() {
        if (!ensureStudioAccess()) {
            return;
        }
        // No longer blocking — background queue allows multiple videos
        if (isVideoSettingsModalOpen()) {
            VideoSettingsModalController.syncToPipeline();
        }

        // Validate inputs
        const inputMode = 'script';
        AppState.longInputMode = 'script';
        const script = $('videoScript')?.value?.trim();
        if (!script || script.length < 50) {
            showToast('Paste a script with at least 50 characters before creating a video.', 'error');
            return;
        }


        ProgressController.beginSubmission(QuickCreateController.getPromptLabel());

        try {
            const selectedImageSource = getEffectiveLongImageSource();
            const imageSource = selectedImageSource;
            const derivedScriptTitle = deriveWorkingTitleFromScript(script);
            const contentTitle = ($('contentTitle')?.value || '').trim() || derivedScriptTitle;
            const requestName = ($('videoName')?.value || '').trim() || derivedScriptTitle;
            const selectedTtsModel = getTtsModelForContext('long');
            const openrouterCheck = await checkSelectedProviderAccess({
                required: !isAudiogramTtsModel(selectedTtsModel),
                notify: true
            });
            if (!openrouterCheck.allowed) {
                ProgressController.hide();
                return;
            }
            const needsStockMedia = imageSource === 'Stock Media' || imageSource === 'Mixed Media';
            const mediaSources = needsStockMedia ? getSelectedMediaSources('long') : [];
            const request = {
                type: 'long',
                openrouter_api_key: AppState.llmProvider === 'openrouter' ? AppState.apiKeys.openrouter : '',
                llm_provider: AppState.llmProvider,
            llm_model: AppState.llmModel || providerDefaultModel(),

                animation_mode: 'None',
                voice: getVoiceForContext('long'),
                vo_mode: getVoModeForTtsModel(selectedTtsModel),
                tts_model: selectedTtsModel,
                edge_tts_options: getEdgeTtsOptions('long'),
                name: requestName,
                output_name: requestName,
                content_title: contentTitle,
                format_hint: $('scriptFormatHint')?.value || 'auto',
                language: 'auto',
                effect: $('effectSelect')?.value || 'Zoom Combo',
                image_source: imageSource,

                scene_mode: $('sceneMode')?.value || 'Every Beat',
                media_mode: imageSource === 'Mixed Media' ? 'automatic' : 'source_locked',
                pipeline_v2_mode: 'on',
                youtube_rights_mode: 'all_public',
                allow_relevant_stock: true,
                allow_stock_last_resort: true,
                stock_last_resort_no_vision: true,
                visual_understanding: true,
                visual_required: true,
                qwen_vision_model: 'qwen/qwen3.5-flash-02-23',
                qwen_vision_fallback_model: 'qwen/qwen3.5-flash-02-23',
                // Keep the desktop request aligned with the validated
                // OpenRouter chain. Older installs persisted Nemotron here;
                // that provider frequently times out before media planning.
                scene_brain_model: 'qwen/qwen3.5-flash-02-23',
                scene_brain_fallback_models: ['qwen/qwen3.5-flash-02-23'],
                captions_enabled: $('captionsEnabled')?.checked ?? true,
                font: $('fontSelect')?.value || 'Montserrat-Bold',
                fontsize: parseInt($('fontSize')?.value) || 48,
                text_color: $('textColor')?.value || '#FFFFFF',
                highlight_color: $('textColor')?.value || '#FFFFFF',
                shadow_on: true,
                outline_size: 3,
                position: $('captionPosition')?.value || 'auto',
                caption_style: $('captionStyle')?.value || 'documentary',
                caption_font: $('captionFont')?.value || 'DM Sans',
                animation: $('captionAnimation')?.value || 'Pop',
                caption_mode: $('captionMode')?.value || 'word',
                music_volume: parseInt($('musicVolume')?.value) || 20,
                output_dir: $('outputPath')?.value || '',
                quality: $('videoQuality')?.value || 'standard',
                whisper_mode: $('whisperMode')?.value || 'local'
            };

            request.strict_retrieval_accuracy = true;
            // Stock remains the terminal emergency lane, but a slot that is
            // still unresolved after it must surface as an actionable error
            // instead of silently producing a stock-heavy render.
            request.fail_on_unresolved_media = true;
            request.yt_lenient_acceptance = false;
            request.youtube_fair_use_mode = false;
            request.youtube_recall_mode = true;
            request.youtube_relaxed_visual_gate = true;
            request.youtube_fail_open_on_visual_unavailable = true;
            request.youtube_prefer_whisper_timing = true;
            // Keep the desktop request on the same exhaustive multi-source
            // lane as the validated Flask pipeline.  Disabling this flag
            // here made manual Electron renders use only the narrow primary
            // provider wave, which is why they could look dramatically worse
            // than the same script submitted through the server.
            request.web_collect_all_sources = true;
            // Release the web, YouTube, and stock lanes together. The backend
            // still enforces per-provider transport limits and the shared
            // visual/media contracts, but a slow provider cannot hold the
            // whole job after another lane has produced usable candidates.
            request.web_wait_until_complete = false;
            request.allow_grounded_graphic_fallback = true;
            // Keep the three retrieval lanes bounded as a group. Individual
            // provider requests retain their own retry/transport safeguards.
            request.media_deadline_seconds = 240;
            request.visual_deadline_seconds = 240;
            request.visual_shared_deadline_seconds = 240;
            request.yt_branch_wall_timeout_seconds = 150;
            request.mixed_yt_wall_cap_seconds = 150;

            // Let the OpenRouter profile and scene directors select the
            // project's visual grammar instead of pinning every export to
            // one manually selected template.
            const selectedDensity = $('settingDocDensity')?.value || 'dynamic';

            // Quality changes render detail and scale; all documentary modes
            // keep the same Remotion scene system so previews and masters match.
            request.render_backend = 'remotion';
            request.semantic_render_required = true;
            request.director_mode = true;
            request.semantic_edit_enable = true;
            request.semantic_render_enabled = true;
            request.editorial_cue_v2_enabled = true;
            request.animate_all_media = true;
            request.enable_sound_library = true;
            request.enable_editorial_cues = true;
            request.enable_motion_templates = true;
            request.documentary_density = selectedDensity;

            if (imageSource === 'Mixed Media') {
                request.media_sources = mediaSources;
                request.pexels_api_key = AppState.apiKeys.pexels || '';
                request.pixabay_api_key = AppState.apiKeys.pixabay || '';
            } else if (imageSource === 'Stock Media') {
                request.media_sources = mediaSources;
                request.pexels_api_key = AppState.apiKeys.pexels || '';
                request.pixabay_api_key = AppState.apiKeys.pixabay || '';
            }

            if (imageSource === 'Stock Media') {
                const stockValidationError = validateStockMediaSelection(request.image_source, mediaSources);
                if (stockValidationError) {
                    showToast(stockValidationError, 'error');
                    navigateTo('settings');
                    return;
                }
            }

            if (request.caption_mode === 'combined') {
                request.hl_font = $('hlFontLong')?.value || 'Montserrat-Bold';
                request.hl_fontsize = parseInt($('hlFontSizeLong')?.value) || 72;
                request.hl_text_color = $('hlTextColorLong')?.value || '#000000';
                request.hl_shadow_on = $('hlShadowLong')?.checked ?? false;
                request.hl_animation = $('hlAnimationLong')?.value || 'Pop';
                request.hl_caption_mode = $('hlModeLong')?.value || 'word';
            }

            request.top_heading_enabled = false;


            request.script = script;
            request.input_mode = 'script';
            request.title = contentTitle;

            const planValidationError = validateGenerationAgainstPlan({
                inputMode: request.input_mode,
                script: request.script,
                duration: request.duration,
                contextLabel: 'This video'
            });
            if (planValidationError) {
                showToast(planValidationError, 'error');
                return;
            }

            // ADD TO BACKGROUND QUEUE
            const queueResult = await api('/api/generate', {
                method: 'POST',
                body: JSON.stringify({...request, execution_mode: 'queue', cost_profile: AppState.generationCostProfile,
                    request_id: crypto.randomUUID()})
            });

            showToast('Video added to queue! Generating in the background...', 'success');
            
            // SYNC, RENDER, AND START POLLING
            await QueueController.syncFromServer();
            QueueController.renderQueue('all');
            const queuedItem = AppState.longQueue.find((item) => item.id === queueResult?.item?.id)
                || queueResult?.item
                || null;
            ProgressController.beginQueueItem(queuedItem, request.title);
            ProgressController.syncWithQueue([...AppState.longQueue]);
            QueueController.startPolling({ notifyOnComplete: true });
            closeLongVideoSettings();

        } catch (error) {
            ProgressController.hide();
            const message = error.message || 'Unknown queue error.';
            appendRuntimeLog(`[generation] ${message}`, 'error');
            showToast('Failed to add to queue: ' + message, 'error');
            console.error('Queue add error:', error);
        }
    },

    async generateShortVideo() {
        if (!ensureStudioAccess()) {
            return;
        }
        // No longer blocking — background queue allows multiple videos

        const inputMode = 'script';
        const script = $('shortVideoScript')?.value?.trim();
        if (!script || script.length < 20) {
            showToast('Script must be at least 20 characters', 'error');
            return;
        }

        const llmValidationError = validateSelectedLlmProvider(inputMode);
        if (llmValidationError) {
            showToast(llmValidationError, 'error');
            navigateTo('settings');
            return;
        }

        const selectedTtsModel = getTtsModelForContext('short');


        // (Background queue transition)
        try {
            const openrouterCheck = await checkSelectedProviderAccess({
                required: !isAudiogramTtsModel(selectedTtsModel),
                notify: true
            });
            if (!openrouterCheck.allowed) return;
            const request = {
                input_mode: inputMode,
                openrouter_api_key: AppState.llmProvider === 'openrouter' ? AppState.apiKeys.openrouter : '',
                llm_provider: AppState.llmProvider,
                llm_model: $('shortLlmModel')?.value || providerDefaultModel(),

                voice: getVoiceForContext('short'),
                vo_mode: getVoModeForTtsModel(selectedTtsModel),
                tts_model: selectedTtsModel,
                edge_tts_options: getEdgeTtsOptions('short'),
                name: $('shortVideoName')?.value || 'ShortFormVideo',
                resolution: $('shortResolution')?.value || '1080x1920 (TikTok/Shorts)',
                scene_mode: $('shortSceneMode')?.value || 'Every Beat',
                effect: $('shortEffect')?.value || 'Zoom In',
                animation_mode: 'None',
                captions_enabled: $('shortCaptionsEnabled')?.checked ?? true,
                font: $('shortFontSelect')?.value || 'Montserrat-Bold',
                fontsize: parseInt($('shortFontSize')?.value) || 80,
                text_color: $('shortTextColor')?.value || '#FFFFFF',
                highlight_color: $('shortTextColor')?.value || '#FFFFFF',
                shadow_on: $('shortCaptionShadow')?.checked ?? true,
                outline_size: ($('shortCaptionShadow')?.checked ?? true) ? 4 : 0,
                position: $('shortCaptionPosition')?.value || 'middle',
                caption_style: $('captionStyle')?.value || 'impact',
                caption_font: $('captionFont')?.value || 'Archivo Black',
                caption_mode: $('shortCaptionMode')?.value || 'karaoke',
                format: $('shortVideoFormat')?.value === 'Explainer Format' ? 'explainer' : 'normal',
            };

            if (request.caption_mode === 'combined') {
                request.hl_font = $('hlFontShort')?.value || 'Montserrat-Bold';
                request.hl_fontsize = parseInt($('hlFontSizeShort')?.value) || 100;
                request.hl_text_color = $('hlTextColorShort')?.value || '#000000';
                request.hl_shadow_on = $('hlShadowShort')?.checked ?? false;
                request.hl_animation = $('hlAnimationShort')?.value || 'Pop';
                request.hl_caption_mode = $('hlModeShort')?.value || 'word';
            }

            request.top_heading_enabled = $('topHeadingEnabledShort')?.checked ?? false;
            if (request.top_heading_enabled) {
                request.top_heading_font = $('topHeadingFontShort')?.value || 'Montserrat-Bold';
                request.top_heading_fontsize = parseInt($('topHeadingFontSizeShort')?.value) || 42;
            }

            request.type = 'short'; // Tells queue to use short pipeline

            request.script = script;
            request.input_mode = 'script';
            request.title = deriveWorkingTitleFromScript(script, request.name);

            const planValidationError = validateGenerationAgainstPlan({
                inputMode: request.input_mode,
                script: request.script,
                duration: request.duration,
                contextLabel: 'This short video'
            });
            if (planValidationError) {
                showToast(planValidationError, 'error');
                return;
            }

            // ADD TO BACKGROUND QUEUE
            const queueResult = await api('/api/generate', {
                method: 'POST',
                body: JSON.stringify({...request, execution_mode: 'queue', cost_profile: AppState.generationCostProfile,
                    request_id: crypto.randomUUID()})
            });

            showToast('Short video added to queue! Generating in the background...', 'success');
            
            // SYNC, RENDER, AND START POLLING
            await QueueController.syncFromServer();
            const queuedItem = AppState.shortQueue.find((item) => item.id === queueResult?.item?.id)
                || queueResult?.item
                || null;
            ProgressController.beginQueueItem(queuedItem, request.title);
            navigateTo('queue');
            QueueController.renderQueue('all');
            QueueController.startPolling({ notifyOnComplete: true });

        } catch (error) {
            const message = error.message || 'Unknown queue error.';
            appendRuntimeLog(`[generation] ${message}`, 'error');
            showToast('Failed to add to queue: ' + message, 'error');
            console.error('Queue add error:', error);
        }
    },

    addToGallery(videoUrl, name) {
        const gallery = $('videoGallery');
        if (!gallery) return;

        const emptyMsg = gallery.querySelector('.empty-msg');
        if (emptyMsg) emptyMsg.remove();

        const item = document.createElement('div');
        item.className = 'video-item';
        item.innerHTML = `
            <video controls>
                <source src="${videoUrl}" type="video/mp4">
            </video>
            <div class="video-info">
                <div class="video-title">${name}</div>
                <div class="video-meta">Generated just now</div>
            </div>
        `;
        gallery.insertBefore(item, gallery.firstChild);
        AppState.generatedVideos.unshift({ url: videoUrl, name, date: new Date() });
    }
};


// Global video generation functions  
function generateVideo() { VideoGenerationController.generateLongVideo(); }
function generateShortFormVideo() { VideoGenerationController.generateShortVideo(); }

// =============================================================================
// OPEN IN EDITOR — Load generated video components into the timeline
// =============================================================================

window._manifestStore = {};

async function openInEditor(manifestId) {
    let manifest = window._manifestStore[manifestId];
    // Compact queue polling omits the heavy component arrays. Fetch the
    if (!manifest || (!manifest.video_clips && (!manifest.media_resolution_v2 || !manifest.media_shots))) {
        const jobId = String(manifestId || '').replace(/^queue_/, '');
        try {
            const payload = await api(`/api/queue/item/${encodeURIComponent(jobId)}`);
            manifest = payload?.item?.result?.component_manifest || null;
            if (manifest) window._manifestStore[manifestId] = manifest;
        } catch (error) {
            appendRuntimeLog(`[queue] manifest load failed: ${error?.message || error}`, 'error');
        }
    }
    if (!manifest) {
        showToast('Error: Video manifest could not be loaded', 'error');
        return;
    }

    if (!document.getElementById('section-editor')) {
        showToast('Editor workspace is not available in this build yet.', 'warning');
        return;
    }

    // Navigate to the editor section
    navigateTo('editor');

    // Give the editor a tick to initialize, then load the manifest
    setTimeout(() => {
        if (typeof EditorController !== 'undefined' && EditorController.init && EditorController.loadManifest) {
            EditorController.init();
            EditorController.loadManifest(manifest);
        }
    }, 150);
}



// =============================================================================
// QUEUE CONTROLLER - Server-Synced with Persistence
// =============================================================================

const QueueController = {
    pollingInterval: null,
    isPolling: false,
    queueRunObservedActiveWork: false,
    queueRunNotifyOnComplete: false,
    lastSyncError: '',
    activeJobState: {},
    queueSyncInFlight: false,
    queuePollInFlight: false,

    hasActiveQueueWork() {
        const allItems = [...AppState.longQueue, ...AppState.shortQueue];
        return allItems.some(i => ['processing', 'queued', 'cancellation_requested', 'shutdown_timeout'].includes(i.status))
            || Object.keys(this.activeJobState || {}).length > 0;
    },

    // =========================================================================
    // INITIALIZATION - Load queue from server
    // =========================================================================

    async init() {
        await this.syncFromServer();
        this.renderQueue('all');
        this.updateQueueCount();
        ProgressController.syncWithQueue([...AppState.longQueue, ...AppState.shortQueue]);
        AccountController.render();

        // Auto-start polling while any queue or cancellation work remains.
        if (this.hasActiveQueueWork()) this.startPolling({ notifyOnComplete: false });
    },

    async syncFromServer() {
        if (!AppState.account.accessToken) {
            this.lastSyncError = '';
            return;
        }
        if (this.queueSyncInFlight) return;
        this.queueSyncInFlight = true;
        try {
            // Keep progress polling small even when history contains large
            // component manifests; the editor loads a full manifest lazily.
            const result = await api('/api/queue/status?compact=1');
            const items = result.items || [];
            AppState.longQueue = items.filter(i => (i.type || 'long') === 'long');
            AppState.shortQueue = items.filter(i => i.type === 'short');
            this.activeJobState = result.active_job_state || {};
            this.lastSyncError = '';
            this._lastLoggedSyncError = '';
            this._lastLoggedPollError = '';
            if (this._accountStateChanged()) {
                AccountController.render();
            }

            console.log(`📋 Synced ${items.length} queue items from server`);
        } catch (error) {
            this.lastSyncError = error.message || 'Could not sync queue.';
            console.warn('Failed to sync queue:', error.message);
            // Keep a single visible diagnostic for a repeated outage. The
            // queue poll continues so a recovered backend is picked up
            // automatically, while the user can still see why the card is
            // stale instead of watching a silent progress freeze.
            if (this._lastLoggedSyncError !== this.lastSyncError) {
                this._lastLoggedSyncError = this.lastSyncError;
                appendRuntimeLog(`[queue sync] ${this.lastSyncError}`, 'error');
            }
        } finally {
            this.queueSyncInFlight = false;
        }
    },

    // Cheap fingerprint of the account/plan fields that AccountController.render
    // depends on. Avoids rebuilding selects + plan widgets on every 2s poll.
    _accountStateChanged() {
        const a = AppState.account || {};
        const fingerprint = [
            a.planKey, a.planStatus, a.pendingPlanKey, a.accessState,
            a.maxVideoMinutes, a.maxScriptWords, a.deviceCount, a.deviceLimit, a.name
        ].join('|');
        const changed = fingerprint !== this._lastAccountFingerprint;
        this._lastAccountFingerprint = fingerprint;
        return changed;
    },

    // Lightweight signature of everything renderQueue displays. When a poll
    // returns unchanged data the DOM is left alone instead of being rebuilt.
    _queueViewSignature() {
        const summarize = (items) => items.map((item) => [
            item.id, item.status,
            Math.round(Number(item.progress || 0) * 10) / 10,
            item.progress_text || '', item.error || '', item.paused_reason || ''
        ]);
        const active = Object.keys(this.activeJobState || {})
            .map((jobId) => [jobId, this.activeJobState[jobId]])
            .sort((a, b) => String(a[0]).localeCompare(String(b[0])));
        return [
            AppState.longQueue.length, AppState.shortQueue.length,
            summarize(AppState.longQueue), summarize(AppState.shortQueue),
            active
        ].map((part) => JSON.stringify(part)).join('|');
    },

    insertLocalItem(item) {
        if (!item || !item.id) return;
        const target = item.type === 'short' ? AppState.shortQueue : AppState.longQueue;
        const other = item.type === 'short' ? AppState.longQueue : AppState.shortQueue;
        const withoutExisting = target.filter(existing => String(existing.id) !== String(item.id));
        const withoutCrossType = other.filter(existing => String(existing.id) !== String(item.id));
        target.splice(0, target.length, item, ...withoutExisting);
        other.splice(0, other.length, ...withoutCrossType);
    },

    // =========================================================================
    // ADD TO QUEUE - Sends to server
    // =========================================================================

    async addToQueue(type) {
        let queueResult = null;
        try {
            if (type === 'long') {
                const script = $('bulkVideoScript')?.value?.trim();
                if (!script) {
                    showToast('Please enter a script', 'error');
                    return;
                }
                const config = this.buildLongConfig(null, script);
                const access = await checkSelectedProviderAccess({
                    required: !isAudiogramTtsModel(config.tts_model),
                    notify: true
                });
                if (!access.allowed) return;
                queueResult = await this.addItemToServer(config);
                if ($('bulkVideoScript')) $('bulkVideoScript').value = '';
                showToast('Added to queue', 'success');

            } else if (type === 'short') {
                const script = $('bulkShortVideoScript')?.value?.trim();
                if (!script) {
                    showToast('Please enter a script', 'error');
                    return;
                }
                const config = this.buildShortConfig(null, script);
                const access = await checkSelectedProviderAccess({
                    required: !isAudiogramTtsModel(config.tts_model),
                    notify: true
                });
                if (!access.allowed) return;
                queueResult = await this.addItemToServer(config);
                if ($('bulkShortVideoScript')) $('bulkShortVideoScript').value = '';
                showToast('Added to shorts queue', 'success');
            }
        } catch (error) {
            const message = error.message || 'Failed to add to queue';
            appendRuntimeLog(`[queue] ${message}`, 'error');
            showToast(message, 'error');
            if ((error.message || '').includes('API key')) {
                navigateTo('settings');
            }
            return;
        }

        await this.syncFromServer();
        this.renderQueue('all');
        this.updateQueueCount();
        const queuedItem = [...AppState.longQueue, ...AppState.shortQueue]
            .find((item) => String(item.id) === String(queueResult?.item?.id))
            || queueResult?.item
            || null;
        if (queuedItem) {
            ProgressController.beginQueueItem(queuedItem, queuedItem.config?.title || queuedItem.title || 'Queued video');
            ProgressController.syncWithQueue([...AppState.longQueue, ...AppState.shortQueue]);
        }
        this.startPolling({ notifyOnComplete: true });
    },

    async addItemToServer(config) {
        try {
            const result = await api('/api/generate', {
                method: 'POST',
                body: JSON.stringify({...config, execution_mode: 'queue', cost_profile: config.cost_profile || AppState.generationCostProfile,
                    request_id: config.request_id || crypto.randomUUID()})
            });
            this.insertLocalItem(result?.item);
            return result;
        } catch (error) {
            console.error('Failed to add to queue:', error);
            throw error;
        }
    },

    buildLongConfig(title, script) {
        const selectedImageSource = $('bulkLongImageSource')?.value || 'Mixed Media';
        const imageSource = selectedImageSource;
        const derivedScriptTitle = deriveWorkingTitleFromScript(script, title || 'Video');
        const queueTitle = (title || '').trim() || derivedScriptTitle;
        const needsStockMedia = imageSource === 'Stock Media' || imageSource === 'Mixed Media';
        const mediaSources = needsStockMedia ? getSelectedMediaSources('bulk-long') : [];
        const selectedTtsModel = getTtsModelForContext('bulk-long');
        const config = {
            type: 'long',
            input_mode: 'script',
            title: queueTitle,
            content_title: queueTitle,
            script: script,
            llm_provider: AppState.llmProvider,
            name: queueTitle || `BulkVideo_${Date.now()}`,
            output_name: queueTitle || `BulkVideo_${Date.now()}`,
            format_hint: 'auto',
            language: 'auto',
            duration: $('bulkDuration')?.value || '5 minutes',
            openrouter_api_key: AppState.llmProvider === 'openrouter' ? AppState.apiKeys.openrouter : '',
            llm_model: $('bulkLongLlmModel')?.value || providerDefaultModel(),

            animation_mode: 'None',
            voice: getVoiceForContext('bulk-long'),
            vo_mode: getVoModeForTtsModel(selectedTtsModel),
            tts_model: selectedTtsModel,
            edge_tts_options: getEdgeTtsOptions('bulk-long'),
            format: $('bulkLongVideoFormat')?.value === 'Explainer Format' ? 'explainer' : 'normal',
            explainer_bg: $('bulkLongExplainerBg')?.value || 'White (#FFFFFF)',
            explainer_plain_frames: $('bulkLongExplainerPlainFrames')?.checked ?? true,
            explainer_chapters: $('bulkLongExplainerChapters')?.checked ?? true,
            image_source: imageSource,
            scene_mode: $('bulkLongSceneMode')?.value || 'Every Beat',
            media_mode: imageSource === 'Mixed Media' ? 'automatic' : 'source_locked',
            effect: $('bulkLongEffectSelect')?.value || 'Zoom Combo',
            captions_enabled: $('bulkLongCaptionsEnabled')?.checked ?? true,
            font: $('bulkLongFontSelect')?.value || 'Montserrat-Bold',
            fontsize: parseInt($('bulkLongFontSize')?.value) || 48,
            text_color: $('bulkLongTextColor')?.value || '#FFFFFF',
            highlight_color: $('bulkLongTextColor')?.value || '#FFFFFF',
            position: $('bulkLongCaptionPosition')?.value || 'auto',
            caption_style: $('captionStyle')?.value || 'documentary',
            caption_font: $('captionFont')?.value || 'DM Sans',
            animation: $('bulkLongCaptionAnimation')?.value || 'Pop',
                caption_mode: $('bulkLongCaptionMode')?.value || 'word',
            music_volume: parseInt($('bulkLongMusicVolume')?.value) || 20,
            niche: $('bulkLongNiche')?.value || '',
            quality: $('videoQuality')?.value || 'standard',
            outline_color: '#000000',
            outline_size: ($('bulkLongCaptionShadow')?.checked ?? true) ? 3 : 0,
            shadow_on: $('bulkLongCaptionShadow')?.checked ?? true,
            bg_on: false,
            margin_v: 50,
            script_description: $('bulkLongDescription')?.value?.trim() || ''
        };
        config.strict_retrieval_accuracy = true;
        config.fail_on_unresolved_media = true;
        config.yt_lenient_acceptance = false;
        config.youtube_fair_use_mode = false;
        config.youtube_rights_mode = 'all_public';
        config.youtube_recall_mode = true;
        config.youtube_relaxed_visual_gate = true;
        config.youtube_fail_open_on_visual_unavailable = true;
        config.youtube_prefer_whisper_timing = true;
        // Short-video generation must use the same multi-source web pool as
        // the long-video path; a single-provider lane makes manual runs
        // brittle and produces visibly weaker media choices.
        config.web_collect_all_sources = true;
        // Manual Electron jobs use the same bounded lane contract as the
        // Flask pipeline: release usable web/YouTube/stock candidates
        // together instead of draining every slow web worker.
        config.web_wait_until_complete = false;
        config.allow_grounded_graphic_fallback = true;
        config.media_deadline_seconds = 240;
        config.visual_deadline_seconds = 240;
        config.visual_shared_deadline_seconds = 240;
        config.yt_branch_wall_timeout_seconds = 150;
        config.mixed_yt_wall_cap_seconds = 150;
        config.render_backend = 'remotion';
        config.semantic_render_required = true;
        config.semantic_edit_enable = true;
        config.semantic_render_enabled = true;
        config.director_mode = true;
        config.editorial_cue_v2_enabled = true;
        config.animate_all_media = true;
        config.graphic_density = config.graphic_density ?? 0.48;
        config.sound_density = config.sound_density ?? 0.48;
        config.allow_relevant_stock = true;
        config.allow_stock_last_resort = true;
        config.stock_last_resort_no_vision = true;
        config.visual_understanding = true;
        config.visual_required = true;
        config.qwen_vision_model = 'qwen/qwen3.5-flash-02-23';
        config.qwen_vision_fallback_model = 'qwen/qwen3.5-flash-02-23';
        config.pipeline_v2_mode = 'on';

        if (imageSource === 'Mixed Media') {
            config.media_sources = mediaSources;
            config.pexels_api_key = AppState.apiKeys.pexels || '';
            config.pixabay_api_key = AppState.apiKeys.pixabay || '';
        } else if (imageSource === 'Stock Media') {
            config.media_sources = mediaSources;
            config.pexels_api_key = AppState.apiKeys.pexels || '';
            config.pixabay_api_key = AppState.apiKeys.pixabay || '';
        }

        const llmValidationError = validateSelectedLlmProvider();
        if (llmValidationError) {
            throw new Error(llmValidationError);
        }

        if (selectedImageSource === 'Stock Media') {
            const stockValidationError = validateStockMediaSelection(config.image_source, mediaSources);
            if (stockValidationError) {
                throw new Error(stockValidationError);
            }
        }

        // Add highlight params if combined mode
        if (config.caption_mode === 'combined') {
            config.hl_font = $('hlFontBulk')?.value || 'Montserrat-Bold';
            config.hl_fontsize = parseInt($('hlFontSizeBulk')?.value) || 72;
            config.hl_text_color = $('hlTextColorBulk')?.value || '#000000';
            config.hl_shadow_on = $('hlShadowBulk')?.checked ?? false;
            config.hl_animation = $('hlAnimationBulk')?.value || 'Pop';
            config.hl_caption_mode = $('hlModeBulk')?.value || 'word';
        }

        // Add top heading params
        config.top_heading_enabled = $('topHeadingEnabledBulk')?.checked ?? false;
        if (config.top_heading_enabled) {
            config.top_heading_font = $('topHeadingFontBulk')?.value || 'Montserrat-Bold';
            config.top_heading_fontsize = parseInt($('topHeadingFontSizeBulk')?.value) || 42;
        }

        const planValidationError = validateGenerationAgainstPlan({
            inputMode: config.input_mode,
            script: config.script,
            duration: config.duration,
            contextLabel: 'This queued video'
        });
        if (planValidationError) {
            throw new Error(planValidationError);
        }

        return config;
    },

    buildShortConfig(title, script) {
        const selectedTtsModel = getTtsModelForContext('bulk-short');
        const config = {
            type: 'short',
            input_mode: 'script',
            title: deriveWorkingTitleFromScript(script, title || 'Short video'),
            content_title: deriveWorkingTitleFromScript(script, title || 'Short video'),
            script: script,
            name: deriveWorkingTitleFromScript(script, title || `BulkShort_${Date.now()}`),
            output_name: deriveWorkingTitleFromScript(script, title || `BulkShort_${Date.now()}`),
            format_hint: 'auto',
            language: 'auto',
            pipeline_v2_mode: 'on',
            duration: $('bulkShortDuration')?.value || '30 seconds',
            script_style: $('bulkShortScriptStyle')?.value || 'viral_hook',
            niche: $('bulkShortNiche')?.value || '',
            openrouter_api_key: AppState.llmProvider === 'openrouter' ? AppState.apiKeys.openrouter : '',
            llm_provider: AppState.llmProvider,
            llm_model: $('bulkShortLlmModel')?.value || providerDefaultModel(),

            animation_mode: 'None',
            voice: getVoiceForContext('bulk-short'),
            vo_mode: getVoModeForTtsModel(selectedTtsModel),
            tts_model: selectedTtsModel,
            edge_tts_options: getEdgeTtsOptions('bulk-short'),
            resolution: $('bulkShortResolution')?.value || '1080x1920 (TikTok/Shorts)',
            scene_mode: $('bulkShortSceneMode')?.value || 'Every Beat',
            effect: $('bulkShortEffect')?.value || 'Zoom In',
            captions_enabled: true,
            font: $('shortFontSelect')?.value || 'Montserrat-Bold',
            fontsize: parseInt($('shortFontSize')?.value) || 80,
            text_color: $('shortTextColor')?.value || '#FFFFFF',
            highlight_color: $('shortTextColor')?.value || '#FFFFFF',
            outline_color: '#000000',
            outline_size: ($('shortCaptionShadow')?.checked ?? true) ? 4 : 0,
            shadow_on: $('shortCaptionShadow')?.checked ?? true,
            bg_on: false,
            position: 'middle',
            caption_style: $('captionStyle')?.value || 'impact',
            caption_font: $('captionFont')?.value || 'Archivo Black',
            margin_v: 80,
            animation: 'Pop',
            caption_mode: $('shortCaptionMode')?.value || 'karaoke',
            script_description: $('bulkShortDescription')?.value?.trim() || '',
            format: $('bulkShortVideoFormat')?.value === 'Explainer Format' ? 'explainer' : 'normal'
        };

        const llmValidationError = validateSelectedLlmProvider();
        if (llmValidationError) {
            throw new Error(llmValidationError);
        }

        // Add highlight params if combined mode
        if (config.caption_mode === 'combined') {
            config.hl_font = $('hlFontShort')?.value || 'Montserrat-Bold';
            config.hl_fontsize = parseInt($('hlFontSizeShort')?.value) || 100;
            config.hl_text_color = $('hlTextColorShort')?.value || '#000000';
            config.hl_shadow_on = $('hlShadowShort')?.checked ?? false;
            config.hl_animation = $('hlAnimationShort')?.value || 'Pop';
            config.hl_caption_mode = $('hlModeShort')?.value || 'word';
        }

        // Add top heading params
        config.top_heading_enabled = $('topHeadingEnabledBulk')?.checked ?? false;
        if (config.top_heading_enabled) {
            config.top_heading_font = $('topHeadingFontBulk')?.value || 'Montserrat-Bold';
            config.top_heading_fontsize = parseInt($('topHeadingFontSizeBulk')?.value) || 42;
        }

        const planValidationError = validateGenerationAgainstPlan({
            inputMode: config.input_mode,
            script: config.script,
            duration: config.duration,
            contextLabel: 'This queued short video'
        });
        if (planValidationError) {
            throw new Error(planValidationError);
        }

        return config;
    },

    // =========================================================================
    // DELETE/CANCEL - Stops backend processing
    // =========================================================================

    async removeFromQueue(type, id) {
        try {
            const result = await api('/api/queue/delete', {
                method: 'POST',
                body: JSON.stringify({ id: id })
            });
            showToast(result?.pending ? 'Stopping video…' : 'Item removed', 'info');
        } catch (error) {
            console.error('Failed to delete:', error);
            showToast('Failed to delete: ' + error.message, 'error');
        }

        await this.syncFromServer();
        this.renderQueue('all');
        this.updateQueueCount();
    },

    // =========================================================================
    // EDIT - Load item config back into form
    // =========================================================================

    async editQueueItem(type, id) {
        const queue = type === 'long' ? AppState.longQueue : AppState.shortQueue;
        const item = queue.find(i => i.id === id);

        if (!item) {
            showToast('Item not found', 'error');
            return;
        }

        if (item.status !== 'queued' && item.status !== 'paused') {
            showToast('Can only edit queued or paused items', 'warning');
            return;
        }

        const config = item.config || item;
        AppState.editingItem = { type, id, config };

        if (type === 'long') {
            if (config.script && $('bulkVideoScript')) $('bulkVideoScript').value = config.script;
            if (config.duration && $('bulkDuration')) $('bulkDuration').value = config.duration;
            if ($('bulkLongTtsModel')) $('bulkLongTtsModel').value = config.tts_model || 'audiogram';
            if (config.edge_tts_options) applyEdgeOptionsToContext('bulk-long', config.edge_tts_options);
            syncVoiceDropdownForContext('bulk-long', config.voice);
            if (config.voice && $('bulkLongVoice')) $('bulkLongVoice').value = config.voice;
            if (config.format && $('bulkLongVideoFormat')) $('bulkLongVideoFormat').value = (config.format === 'explainer' ? 'Explainer Format' : 'Normal Format');
        } else {
            if (config.script && $('bulkShortVideoScript')) $('bulkShortVideoScript').value = config.script;
            if (config.duration && $('bulkShortDuration')) $('bulkShortDuration').value = config.duration;
            if ($('bulkShortTtsModel')) $('bulkShortTtsModel').value = config.tts_model || 'audiogram';
            if (config.edge_tts_options) applyEdgeOptionsToContext('bulk-short', config.edge_tts_options);
            syncVoiceDropdownForContext('bulk-short', config.voice);
            if (config.voice && $('bulkShortVoice')) $('bulkShortVoice').value = config.voice;
            if (config.format && $('bulkShortVideoFormat')) $('bulkShortVideoFormat').value = (config.format === 'explainer' ? 'Explainer Format' : 'Normal Format');
        }

        showToast('Editing item. Make changes and click "Save Changes"', 'info');

        // Show save button
        const saveBtn = $(`${type}SaveEditBtn`);
        if (saveBtn) saveBtn.classList.remove('hidden');
    },

    async saveEditedItem(type) {
        if (!AppState.editingItem || AppState.editingItem.type !== type) {
            showToast('No item being edited', 'error');
            return;
        }

        const id = AppState.editingItem.id;
        const newConfig = type === 'long'
            ? this.buildLongConfig(null, $('bulkVideoScript')?.value?.trim())
            : this.buildShortConfig(null, $('bulkShortVideoScript')?.value?.trim());

        try {
            await api('/api/queue/update', {
                method: 'POST',
                body: JSON.stringify({
                    id: id,
                    updates: { config: newConfig }
                })
            });
            showToast('Item updated successfully', 'success');
        } catch (error) {
            showToast('Failed to update: ' + error.message, 'error');
        }

        AppState.editingItem = null;
        const saveBtn = $(`${type}SaveEditBtn`);
        if (saveBtn) saveBtn.classList.add('hidden');

        await this.syncFromServer();
        this.renderQueue('all');
    },

    // =========================================================================
    // RESUME - Restart paused/failed items
    // =========================================================================

    async resumeItem(type, id) {
        try {
            const result = await api('/api/queue/resume', {
                method: 'POST',
                body: JSON.stringify({ id: id })
            });

            // Auto-start worker if needed
            if (result.needs_start) {
                await api('/api/queue/start', { method: 'POST' });
                showToast('Item resumed and processing started', 'success');
            } else {
                showToast('Item resumed and queued for processing', 'success');
            }
        } catch (error) {
            showToast('Failed to resume: ' + error.message, 'error');
        }

        await this.syncFromServer();
        this.renderQueue('all');
        const resumedItem = [...AppState.longQueue, ...AppState.shortQueue]
            .find((item) => String(item.id) === String(id));
        if (resumedItem) {
            ProgressController.beginQueueItem(resumedItem, resumedItem.config?.title || resumedItem.title || 'Queued video');
            ProgressController.syncWithQueue([...AppState.longQueue, ...AppState.shortQueue]);
        }
        this.startPolling({ notifyOnComplete: true });
    },

    // =========================================================================
    // CLEAR COMPLETED
    // =========================================================================

    async clearQueue(type) {
        try {
            const result = await api('/api/queue/clear', { method: 'POST' });
            showToast(
                result?.pending?.length
                    ? `Stopping ${result.pending.length} active video${result.pending.length === 1 ? '' : 's'}…`
                    : 'Queue cleared',
                'info'
            );

            // Clear bulk input textareas for a truly clean slate
            const bulkIds = ['bulkVideoScript', 'bulkShortVideoScript'];
            bulkIds.forEach(id => {
                const el = $(id);
                if (el) el.value = '';
            });

            console.log('🧹 Bulk input fields cleared');
        } catch (error) {
            console.error('Failed to clear:', error);
            showToast('Failed to clear queue', 'error');
        }

        await this.syncFromServer();
        this.renderQueue('all');
        this.updateQueueCount();
    },

    // =========================================================================
    // RENDER QUEUE - Enhanced with GenCards
    // =========================================================================

    renderQueue(type) {
        // We now render both queues into a single container
        const allItems = [...AppState.longQueue, ...AppState.shortQueue]
            .sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));

        console.log(`📋 renderQueue called — ${allItems.length} items (long: ${AppState.longQueue.length}, short: ${AppState.shortQueue.length})`);

        const container = $('queueCardsContainer');
        const countBadge = $('queueCount');

        if (countBadge) countBadge.textContent = allItems.length;
        if (!container) return;

        if (allItems.length === 0) {
            container.innerHTML = `<div class="empty-msg" id="queueEmptyMsg">No videos in the background queue. Click 'Create New' to start one!</div>`;
            return;
        }

        container.innerHTML = allItems.map(item => {
            const config = item.config || item;
            const title = config.title || 'Script-based Video';
            const status = item.status || 'queued';
            const progress = item.progress || 0;
            const progressText = item.progress_text || 'Queued';
            
            // Format ID for DOM bindings
            const cleanId = `queue_${item.id}`;

            if (status === 'completed' && item.result) {
                // Done GenCard
                const manifest = item.result.component_manifest || {};
                const duration = item.result.duration || 0;
                const videoUrl = item.result.video_url || item.result.video;
                const name = manifest.name || title;
                const mins = Math.floor(duration / 60);
                const secs = Math.round(duration % 60);
                const durStr = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
                
                window._manifestStore = window._manifestStore || {};
                window._manifestStore[cleanId] = manifest;

                return `
                <div class="gen-card-wrap" id="${cleanId}_wrap">
                    <div class="gen-card done" id="${cleanId}_card">
                        <div class="gen-preview-thumb" onclick="playVideo('${videoUrl}')" title="Click to play video">
                            <div class="gen-preview-thumb-inner">
                                <span class="gen-preview-play-icon">▶</span>
                                <span class="gen-preview-play-text">Play Video</span>
                            </div>
                        </div>
                        <div class="gen-card-done-body">
                            <div class="gen-card-top-row">
                                <span class="gen-done-badge">DONE</span>
                                <span class="gen-card-name">${name}</span>
                                <span class="gen-card-duration">${durStr}</span>
                            </div>
                            <div class="gen-card-actions" style="margin-top:auto">
                                <button class="gen-btn edit" onclick="openInEditor('${cleanId}')">✂️ Edit in Timeline</button>
                                <button class="gen-btn gallery" onclick="playVideo('${videoUrl}')">▶ Play</button>
                                <a class="gen-btn download" href="${videoUrl}" download="${name}.mp4">⬇ Download</a>
                                <button class="btn-delete" onclick="removeFromQueue('${item.type}', '${item.id}')" title="Remove from queue" style="margin-left:auto; background:rgba(239,68,68,0.2); color:#ef4444; border:none; border-radius:4px; padding:4px 8px; cursor:pointer;">✕</button>
                            </div>
                        </div>
                    </div>
                </div>`;

            } else if (status === 'error') {
                return `
                <div class="gen-card-wrap" id="${cleanId}_wrap">
                    <div class="gen-card" style="border-color: rgba(239,68,68,0.4)">
                        <div class="gen-card-generating-body">
                            <div style="font-size:36px">⚠️</div>
                            <div class="gen-status-title">Generation Failed: ${title}</div>
                            <div class="gen-status-msg" style="color:var(--error)">${item.error || progressText}</div>
                            <div style="display:flex; gap:8px; margin-top:8px">
                                <button class="gen-btn edit" onclick="resumeQueueItem('${item.type}', '${item.id}')">▶️ Retry</button>
                                <button class="gen-btn download" onclick="removeFromQueue('${item.type}', '${item.id}')">Dismiss</button>
                            </div>
                        </div>
                    </div>
                </div>`;
            } else {
                // Generating, stopping, queued, or paused
                const isProcessing = status === 'processing';
                const isStopping = status === 'cancellation_requested';
                const isShutdownTimeout = status === 'shutdown_timeout';
                const isPaused = status === 'paused' || isShutdownTimeout;
                const icon = isStopping ? '🛑' : (isProcessing ? '🎬' : (isPaused ? '⏸️' : '⏳'));
                const titleStr = isStopping
                    ? 'Stopping video…'
                    : (isProcessing ? 'Generating your video...' : (isPaused ? 'Paused — cleanup interrupted' : 'Waiting in Queue...'));

                return `
                <div class="gen-card-wrap" id="${cleanId}_wrap">
                    <div class="gen-card generating">
                        <div class="gen-card-generating-body">
                            <div class="gen-spinner" style="animation: ${isProcessing ? 'spin 2s linear infinite' : 'none'}">
                                <div class="gen-spinner-icon">${icon}</div>
                            </div>
                            <div class="gen-status-title">${titleStr}</div>
                            <div class="gen-status-step"><b>${title}</b></div>
                            <div class="gen-status-step" style="margin-top:4px">${isStopping ? 'Stopping active work…' : progressText} ${isProcessing ? '(' + progress + '%)' : ''}</div>
                            
                            ${isProcessing ? `
                            <div style="width:100%; height:4px; background:rgba(255,255,255,0.1); border-radius:2px; margin-top:12px; overflow:hidden">
                                <div style="height:100%; width:${progress}%; background:var(--primary); transition:width 0.3s ease"></div>
                            </div>` : ''}

                            <div style="display:flex; gap:8px; margin-top:12px; justify-content:center; flex-wrap:wrap">
                                ${isProcessing ? `<button class="gen-btn edit" onclick="viewQueueItemProgress('${item.id}')" style="padding:4px 12px; font-size:12px">📊 View Details</button>` : ''}
                                ${isPaused ? `<button class="gen-btn edit" onclick="resumeQueueItem('${item.type}', '${item.id}')" style="padding:4px 12px; font-size:12px">Retry</button>` : ''}
                                ${isStopping ? `<span class="gen-btn" style="padding:4px 12px; font-size:12px; opacity:.7">Stopping…</span>` : `<button class="gen-btn download" onclick="removeFromQueue('${item.type}', '${item.id}')" style="padding:4px 12px; font-size:12px">✕ Cancel</button>`}
                            </div>
                        </div>
                    </div>
                </div>`;
            }
        }).join('');
    },

    updateQueueCount() {
        const total = AppState.longQueue.length + AppState.shortQueue.length;
        const queueCount = $('queueCount');
        if (queueCount) queueCount.textContent = total;
    },

    // =========================================================================
    // PROCESS QUEUE - Start backend worker + polling
    // =========================================================================

    async processQueue(type) {
        try {
            // Start the backend worker
            await api('/api/queue/start', { method: 'POST' });
            await this.syncFromServer();
            this.renderQueue('all');
            this.updateQueueCount();
            showToast('Queue processing started', 'success');

            // Start polling for status updates
            this.startPolling({ notifyOnComplete: true });
        } catch (error) {
            showToast('Failed to start queue: ' + error.message, 'error');
        }
    },

    startPolling(options = {}) {
        const notifyOnComplete = options.notifyOnComplete !== false;
        if (this.isPolling) {
            this.queueRunNotifyOnComplete = this.queueRunNotifyOnComplete || notifyOnComplete;
            return;
        }

        this.isPolling = true;
        this.queueRunObservedActiveWork = this.hasActiveQueueWork();
        this.queueRunNotifyOnComplete = notifyOnComplete;
        this._lastQueueViewSignature = null;
        this._lastAccountFingerprint = null;
        this.pollingInterval = setInterval(async () => {
            if (document.hidden && !this.queueRunObservedActiveWork) return;
            if (this.queuePollInFlight) return;
            this.queuePollInFlight = true;
            try {
                await this.syncFromServer();

                const signature = this._queueViewSignature();
                if (signature !== this._lastQueueViewSignature) {
                    this._lastQueueViewSignature = signature;
                    this.renderQueue('all');
                    this.updateQueueCount();
                    ProgressController.syncWithQueue([...AppState.longQueue, ...AppState.shortQueue]);
                }

                const hasActiveWork = this.hasActiveQueueWork();
                if (hasActiveWork) {
                    this.queueRunObservedActiveWork = true;
                }

                if (!hasActiveWork) {
                    const shouldNotifyComplete = this.queueRunNotifyOnComplete && this.queueRunObservedActiveWork;
                    this.stopPolling();
                    if (shouldNotifyComplete) {
                        showToast('Queue processing complete!', 'success');
                    }
                }
            } catch (error) {
                const message = error?.message || String(error || 'Queue polling failed.');
                if (this._lastLoggedPollError !== message) {
                    this._lastLoggedPollError = message;
                    appendRuntimeLog(`[queue poll] ${message}`, 'error');
                }
            } finally {
                this.queuePollInFlight = false;
            }
        }, 2000);
    },

    stopPolling() {
        if (this.pollingInterval) {
            clearInterval(this.pollingInterval);
            this.pollingInterval = null;
        }
        this.isPolling = false;
        this.queuePollInFlight = false;
        this.queueRunObservedActiveWork = false;
        this.queueRunNotifyOnComplete = false;
    }
};

QueueController.renderQueue = function(type) {
    const allItems = [...AppState.longQueue, ...AppState.shortQueue]
        .sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));

    console.log(`Queue render: ${allItems.length} items`);

    const container = $('queueCardsContainer');
    const countBadge = $('queueCount');

    if (countBadge) countBadge.textContent = allItems.length;
    if (!container) return;

    if (QueueController.lastSyncError && allItems.length === 0) {
        container.innerHTML = `<div class="empty-msg error">Could not load queue: ${escapeHtml(QueueController.lastSyncError)}</div>`;
        return;
    }

    if (allItems.length === 0) {
        container.innerHTML = `<div class="empty-msg" id="queueEmptyMsg">No videos in the background queue. Click 'Create New' to start one!</div>`;
        return;
    }

    // Remove empty message if it exists
    const emptyMsg = $('queueEmptyMsg');
    if (emptyMsg) container.removeChild(emptyMsg);

    // Keep track of current IDs to remove stale elements
    const currentIds = allItems.map(item => `queue_${item.id}_wrap`);
    Array.from(container.children).forEach(child => {
        if (!currentIds.includes(child.id) && child.id !== 'queueEmptyMsg') {
            container.removeChild(child);
        }
    });

    allItems.forEach((item, index) => {
        const config = item.config || item;
        const title = escapeHtml(config.title || 'Script-based Video');
        const status = item.status || 'queued';
        const progress = Math.max(0, Math.min(100, Number(item.progress || 0)));
        const progressText = escapeHtml(formatUserProgressStatus(item.progress_text || (status === 'processing' ? 'Processing' : 'Queued'), progress));
        const cleanId = `queue_${item.id}`;
        const wrapId = `${cleanId}_wrap`;
        window._queueCheckpointPaths = window._queueCheckpointPaths || {};
        if (item.checkpoint_dir) window._queueCheckpointPaths[cleanId] = item.checkpoint_dir;

        
        let htmlContent = '';

        if (status === 'completed' && item.result) {
            const manifest = item.result.component_manifest || {};
            const duration = Number(item.result.duration || 0);
            const videoUrl = item.result.video_url || item.result.video || '';
            const safeVideoUrl = escapeHtml(videoUrl);
            const name = escapeHtml(manifest.name || config.title || 'Completed Video');
            const mins = Math.floor(duration / 60);
            const secs = Math.round(duration % 60);
            const durStr = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
            const reviewNeeded = item.result.ready_to_publish === false;
            const reviewNotice = reviewNeeded
                ? `<p class="gen-review-notice">${escapeHtml((item.result.warnings || [])[0] || 'Review this export before publishing.')}</p>`
                : '';

            window._manifestStore = window._manifestStore || {};
            window._manifestStore[cleanId] = manifest;

            const thumbUrl = safeVideoUrl ? safeVideoUrl.replace(/\.[^/.]+$/, '') + '.jpg' : '';
            htmlContent = `
                <div class="gen-card done" id="${cleanId}_card">
                    <div class="gen-preview-thumb" onclick="playVideo('${safeVideoUrl}')" title="Click to play video">
                        ${thumbUrl ? `<img src="${thumbUrl}" class="gallery-thumb-img" alt="${name}" loading="lazy" onerror="this.style.display='none';">` : ''}
                        <div class="gallery-thumb-overlay">
                            <div class="gen-preview-thumb-inner">
                                <span class="gen-preview-play-icon">▶</span>
                                <span class="gen-preview-play-text">Play Video</span>
                            </div>
                        </div>
                    </div>

                    <div class="gen-card-done-body">
                        <div class="gen-card-top-row">
                            <span class="gen-done-badge">${reviewNeeded ? 'REVIEW NEEDED' : 'DONE'}</span>
                            <span class="gen-card-name">${name}</span>
                            <span class="gen-card-duration">${durStr}</span>
                        </div>
                        ${reviewNotice}
                        <div class="gen-card-actions" style="margin-top:auto">
                            <button class="gen-btn edit" onclick="openInEditor('${cleanId}')">Edit Timeline</button>
                            <button class="gen-btn gallery" onclick="playVideo('${safeVideoUrl}')">Open Player</button>
                            <button class="gen-btn download" onclick="openCompletedQueueProject('${item.id}')">Projects</button>
                            <button class="btn-delete" onclick="removeFromQueue('${item.type}', '${item.id}')" title="Remove from queue" style="margin-left:auto; background:rgba(239,68,68,0.2); color:#ef4444; border:none; border-radius:4px; padding:4px 8px; cursor:pointer;">X</button>
                        </div>
                    </div>
                </div>`;
        } else if (status === 'error') {
            const errorText = escapeHtml(item.error || item.progress_text || 'Generation failed');
            const contractHtml = renderMediaContractError(item);
            htmlContent = `
                <div class="gen-card" style="border-color: rgba(239,68,68,0.4)">
                    <div class="gen-card-generating-body">
                        <div style="font-size:36px">!</div>
                        <div class="gen-status-title">Generation Failed: ${title}</div>
                        <div class="gen-status-msg" style="color:var(--error)">${errorText}</div>
                        ${contractHtml}
                        <div style="display:flex; gap:8px; margin-top:8px">
                            <button class="gen-btn edit" onclick="resumeQueueItem('${item.type}', '${item.id}')">Retry</button>
                            ${item.checkpoint_dir ? `<button class="gen-btn download" onclick="openQueueCheckpoint('${cleanId}')">Checkpoints</button>` : ''}
                            <button class="gen-btn download" onclick="removeFromQueue('${item.type}', '${item.id}')">Dismiss</button>
                        </div>
                    </div>
                </div>`;
        } else {
            const isProcessing = status === 'processing';
            const isStopping = status === 'cancellation_requested';
            const isPaused = status === 'paused';
            const isLowMemory = isPaused && item.paused_reason === 'low_memory';
            const isOpenRouterWait = isPaused && item.paused_reason === 'openrouter_scene_plan';
            const isEditPlanWait = isPaused && item.paused_reason === 'remotion_edit_plan';
            const isRenderWait = isPaused && item.paused_reason === 'remotion_render';
            const isMediaWait = isPaused && item.paused_reason === 'media_contract';
            const statusClass = isStopping ? 'stopping' : (isProcessing ? 'processing' : (isPaused ? 'paused' : 'queued'));
            const titleStr = isStopping ? 'Stopping video…' : (isProcessing ? 'Generating your video...' : (isPaused ? (isLowMemory ? 'Waiting for free RAM' : (isOpenRouterWait ? 'Waiting for OpenRouter' : (isEditPlanWait ? 'Review Remotion edit' : (isRenderWait ? 'Retry Remotion render' : (isMediaWait ? 'Waiting for verified media' : 'Paused'))))) : 'Waiting in Queue...'));
            const progressWidth = isStopping ? Math.max(8, progress) : (isProcessing ? progress : (isPaused ? 6 : 8));

            htmlContent = `
            <div class="gen-card generating">
                <div class="gen-card-generating-body">
                    <div class="queue-state-pill ${statusClass}">${escapeHtml(status.toUpperCase())}</div>
                    <div class="gen-status-title">${titleStr}</div>
                    <div class="gen-status-step"><b>${title}</b></div>
                    <div class="gen-status-step" style="margin-top:4px">${isStopping ? 'Stopping active work…' : progressText}${isProcessing ? ` (${Math.round(progress)}%)` : ''}</div>
                    <div class="queue-progress-track" aria-hidden="true">
                        <div class="queue-progress-fill" style="width:${progressWidth}%"></div>
                    </div>
                    <div class="queue-actions" style="display:flex; gap:8px; margin-top:12px; justify-content:center; flex-wrap:wrap">
                        ${isProcessing ? `<button class="gen-btn edit" onclick="viewQueueItemProgress('${item.id}')" style="padding:4px 12px; font-size:12px">View Details</button>` : ''}
                        ${isPaused ? `<button class="gen-btn edit" onclick="resumeQueueItem('${item.type}', '${item.id}')" style="padding:4px 12px; font-size:12px">Retry</button>` : ''}
                        ${item.checkpoint_dir ? `<button class="gen-btn download" onclick="openQueueCheckpoint('${cleanId}')" style="padding:4px 12px; font-size:12px">Checkpoints</button>` : ''}
                        ${isStopping ? `<span class="gen-btn" style="padding:4px 12px; font-size:12px; opacity:.7">Stopping…</span>` : `<button class="gen-btn download" onclick="removeFromQueue('${item.type}', '${item.id}')" style="padding:4px 12px; font-size:12px">Cancel</button>`}
                    </div>
                </div>
            </div>`;
        }

        let wrap = $(wrapId);
        if (!wrap) {
            wrap = document.createElement('div');
            wrap.className = 'gen-card-wrap';
            wrap.id = wrapId;
            wrap.innerHTML = htmlContent;
            
            // Append or insert at the correct index
            if (index >= container.children.length) {
                container.appendChild(wrap);
            } else {
                container.insertBefore(wrap, container.children[index]);
            }
        } else {
            // Reorder if it's not at the correct index
            if (container.children[index] !== wrap) {
                container.insertBefore(wrap, container.children[index]);
            }
            
            // Smart update: do not overwrite innerHTML if it's a completed card
            if (status === 'completed' && wrap.querySelector('.gen-preview-thumb')) {
                // It is already rendered as completed, do nothing

            } else if (status === 'error' && wrap.querySelector('.gen-status-msg')) {
                // Already rendered as error, just update text if it changed (rare)
                wrap.innerHTML = htmlContent;
            } else if (status === 'processing' || status === 'queued' || status === 'paused' || status === 'cancellation_requested') {
                // If it was already generating, update inner fields to avoid flicker
                const pill = wrap.querySelector('.queue-state-pill');
                if (pill) {
                    const isProcessing = status === 'processing';
                    const isStopping = status === 'cancellation_requested';
                    const isPaused = status === 'paused';
                    const isLowMemory = isPaused && item.paused_reason === 'low_memory';
                    const isOpenRouterWait = isPaused && item.paused_reason === 'openrouter_scene_plan';
                    const isEditPlanWait = isPaused && item.paused_reason === 'remotion_edit_plan';
                    const isRenderWait = isPaused && item.paused_reason === 'remotion_render';
                    const isMediaWait = isPaused && item.paused_reason === 'media_contract';
                    pill.className = `queue-state-pill ${isStopping ? 'stopping' : (isProcessing ? 'processing' : (isPaused ? 'paused' : 'queued'))}`;
                    pill.textContent = status.toUpperCase();
                    
                    wrap.querySelector('.gen-status-title').textContent = isStopping ? 'Stopping video…' : (isProcessing ? 'Generating your video...' : (isPaused ? (isLowMemory ? 'Waiting for free RAM' : (isOpenRouterWait ? 'Waiting for OpenRouter' : (isEditPlanWait ? 'Review Remotion edit' : (isRenderWait ? 'Retry Remotion render' : (isMediaWait ? 'Waiting for verified media' : 'Paused'))))) : 'Waiting in Queue...'));
                    wrap.querySelectorAll('.gen-status-step')[1].textContent = `${isStopping ? 'Stopping active work…' : progressText}${isProcessing ? ` (${Math.round(progress)}%)` : ''}`;
                    wrap.querySelector('.queue-progress-fill').style.width = `${isStopping ? Math.max(8, progress) : (isProcessing ? progress : (isPaused ? 6 : 8))}%`;
                    const actions = wrap.querySelector('.queue-actions');
                    if (actions) {
                        const template = document.createElement('template');
                        template.innerHTML = htmlContent;
                        const nextActions = template.content.querySelector('.queue-actions');
                        if (nextActions) actions.innerHTML = nextActions.innerHTML;
                    }
                } else {
                    // Type changed (e.g., error -> processing), replace HTML entirely
                    wrap.innerHTML = htmlContent;
                }
            } else {
                wrap.innerHTML = htmlContent;
            }
        }
    });
};

// Keep the cancellation-aware incremental renderer active. The previous
// assignment restored the legacy full-rebuild renderer and caused queue cards
// to flicker/stale-progress under a real run.

// Global queue functions
function addToQueue(type) { QueueController.addToQueue(type); }
function removeFromQueue(type, id) { QueueController.removeFromQueue(type, id); }
function editQueueItem(type, id) { QueueController.editQueueItem(type, id); }
function saveEditedItem(type) { QueueController.saveEditedItem(type); }
function resumeQueueItem(type, id) { QueueController.resumeItem(type, id); }
function viewQueueItemProgress(id) {
    const items = [...(AppState.longQueue || []), ...(AppState.shortQueue || [])];
    const item = items.find(i => String(i.id) === String(id));
    if (!item) return;
    const title = item.config?.title || item.title || 'Video';
    ProgressController.beginQueueItem(item, title);
    ProgressController.syncWithQueue(items);
}
function openQueueCheckpoint(cleanId) {
    const target = window._queueCheckpointPaths?.[cleanId];
    if (!target) {
        showToast('Checkpoint folder is not available yet.', 'info');
        return;
    }
    if (window.electronAPI?.openOutputFolder) {
        window.electronAPI.openOutputFolder(target);
    } else {
        showToast('Checkpoint folder is saved in the video output package.', 'info');
    }
}
function clearQueue(type) { QueueController.clearQueue(type); }
function processQueue(type) { QueueController.processQueue(type); }

function getQueueItemById(id) {
    return [...AppState.longQueue, ...AppState.shortQueue].find(item => String(item.id) === String(id)) || null;
}

function playQueueVideoById(id) {
    const item = getQueueItemById(id);
    const config = item?.config || item || {};
    const videoUrl = item?.result?.video_url || item?.result?.video || '';

    if (!videoUrl) {
        showToast('Video is not ready yet', 'warning');
        return;
    }

    playVideo({
        name: config.title || 'Completed Video',
        filename: deriveFilenameFromUrl(videoUrl),
        url: videoUrl,
        path: item?.result?.video_path || item?.result?.video_path_disk || ''
    });
}

function openCompletedQueueProject(id) {
    const item = getQueueItemById(id);
    const config = item?.config || item || {};
    const videoUrl = item?.result?.video_url || item?.result?.video || '';

    if (!videoUrl) {
        showToast('Project video is not ready yet', 'warning');
        return;
    }

    GalleryController.queueAutoOpen(
        videoUrl,
        config.title || 'Completed Video',
        item?.result?.video_path || item?.result?.video_path_disk || ''
    );
    navigateTo('gallery');
}

async function refreshQueue() {
    await QueueController.syncFromServer();
    QueueController.renderQueue('all');
    QueueController.updateQueueCount();
    showToast('Queue refreshed', 'success');
}

// =============================================================================
// VOICE PREVIEW
// =============================================================================

let activeVoicePreview = null;
let voicePreviewSequence = 0;
async function previewVoice(context = 'long', button = null) {
    if (!ensureStudioAccess('An active plan is required to preview voices.')) {
        return;
    }

    const sequence = ++voicePreviewSequence;
    activeVoicePreview?.pause();
    const originalLabel = button?.textContent;
    if (button) { button.disabled = true; button.textContent = 'Loading preview…'; }
    let voice, voMode, ttsModel;
    let edgeOptionsContext = 'long';

    if (context === 'setting-long') {
        ttsModel = getTtsModelForContext('setting-long');
        voice = getVoiceForContext('setting-long');
        voMode = getVoModeForTtsModel(ttsModel);
        edgeOptionsContext = 'setting-long';
    } else if (context === 'bulk-long') {
        ttsModel = getTtsModelForContext('bulk-long');
        voice = getVoiceForContext('bulk-long');
        voMode = getVoModeForTtsModel(ttsModel);
        edgeOptionsContext = 'bulk-long';
    } else if (context === 'bulk-short') {
        ttsModel = getTtsModelForContext('bulk-short');
        voice = getVoiceForContext('bulk-short');
        voMode = getVoModeForTtsModel(ttsModel);
        edgeOptionsContext = 'bulk-short';
    } else if (context === 'short') {
        ttsModel = getTtsModelForContext('short');
        voice = getVoiceForContext('short');
        voMode = getVoModeForTtsModel(ttsModel);
        edgeOptionsContext = 'short';
    } else {
        ttsModel = getTtsModelForContext('long');
        voice = getVoiceForContext('long');
        voMode = getVoModeForTtsModel(ttsModel);
    }

    try {
        const openrouterCheck = await checkSelectedProviderAccess({
            required: !isAudiogramTtsModel(ttsModel),
            notify: true
        });
        if (!openrouterCheck.allowed) return;
        showToast('Generating preview...', 'info');

        const result = await api('/api/preview-voice', {
            method: 'POST',
            body: JSON.stringify({
                voice: voice,
                vo_mode: voMode,
                tts_model: ttsModel,
                openrouter_api_key: AppState.llmProvider === 'openrouter' ? AppState.apiKeys.openrouter : '',
                edge_tts_options: getEdgeTtsOptions(edgeOptionsContext),
                text: 'Hello! This is a preview of my voice.',
            })
        });

        if (sequence === voicePreviewSequence && (result.audio || result.audio_url)) {
            activeVoicePreview = new Audio(result.audio || result.audio_url);
            await activeVoicePreview.play();
        }
    } catch (error) {
        appendRuntimeLog(`[voice preview] ${error.message || error}`, 'error');
        showToast('Preview failed: ' + (error.message || error), 'error');
    } finally {
        if (button) { button.disabled = false; button.textContent = originalLabel; }
    }
}

// =============================================================================
// GALLERY
// =============================================================================

function refreshGallery() {
    GalleryController.load();
    showToast('Projects refreshed', 'success');
}

function showQueueTab(tab) {
    // Legacy function, replaced entirely by GenCard renders
    console.log("showQueueTab: unused");
}

// =============================================================================
// INITIALIZATION
// =============================================================================

console.log('🎬 Vidzeo - AI Video Studio Initializing...');
window.electronAPI?.onBackendLog?.((message) => {
    const rendered = String(message || '');
    appendRuntimeLog(rendered, /error|failed|crash|exception|stopped/i.test(rendered) ? 'error' : 'info');
});
window.electronAPI?.onBackendStateChanged?.((state = {}) => {
    const rendered = `[backend:${state.phase || 'unknown'}] ${state.message || state.error || ''}`.trim();
    appendRuntimeLog(rendered, state.phase === 'error' || state.phase === 'stopped' ? 'error' : 'info');
    if (state.phase === 'error' && state.error) showToast(state.error, 'error');
});
(async () => {
    applyTheme(AppState.theme);
    // A pricing or account outage must not hold the entire Settings page.
    // Populate local voice choices now, then hydrate independent data together.
    Object.values(EDGE_TTS_CONTEXTS).forEach(context => syncVoiceDropdownForContext(context.name));
    const dropdownsReady = DropdownController.init();
    const settingsReady = SettingsController.load();
    void api('/api/plans', {timeoutMs: 8000}).then(plans => {
        AppState.availablePlans = Array.isArray(plans) ? plans : [];
        renderPricing();
    }).catch(() => renderPricing());
    await Promise.all([
        withUiTimeout(syncDesktopAuthState(), 10000, 'Account sync').catch(error => console.warn(error.message)),
        settingsReady
    ]);
    if (AppState.llmProvider === 'openrouter' && AppState.apiKeys.openrouter) {
        void checkOpenRouterAccess({ notify: false });
    } else {
        renderOpenRouterAccessStatus({ status: 'missing_key', ok: false, message: 'OpenRouter key missing.' });
    }

    // Load form settings (will be applied after dropdowns are populated)
    FormSettingsController.load();

    // Load dropdowns (this populates select options)
    await dropdownsReady;

    // Apply saved form settings AFTER dropdowns are populated
    // This ensures dropdown values can be properly restored
    FormSettingsController.applyPendingSettings();
    toggleImageSource();
    toggleBulkLongImageSource();

    const savedFormSettings = JSON.parse(localStorage.getItem(FormSettingsController.STORAGE_KEY) || '{}');
    Object.values(EDGE_TTS_CONTEXTS).forEach((context) => {
        applyEdgeDefaultsToContext(context.name, AppState.dropdownData.edgeDefaults || {});
        syncVoiceDropdownForContext(context.name, savedFormSettings[context.voiceId]);
    });

    // Re-apply format logic to ensure UI consistency with loaded settings

    // Restore highlight panel visibility based on saved caption mode
    if ($('captionMode') && !$('captionMode').dataset.userSelected) {
        $('captionMode').value = 'word';
    }
    toggleHighlightPanel('captionMode', 'hlSettingsLong');

    // Live-sanitize video name input (strip special chars)
    const videoNameEl = $('videoName');
    if (videoNameEl) {
        videoNameEl.addEventListener('input', function() {
            this.value = this.value.replace(/[<>:"\/\\|?*]/g, '');
        });
    }

    // Initialize auto-save listeners for all form elements
    FormSettingsController.initAutoSave();
    bindOutputFolderPickers();

    // Test connection
    SettingsController.testConnection();
    initAppUpdateControls();

    // Initialize queue counts
    await QueueController.init();
    await loadRuntimeLogHistory();
    QuickCreateController.init();
    VideoSettingsModalController.init();
    // Keep a settings panel the user has already opened during startup open.
    if (AppState.account.planKey === 'free_trial') maybeShowFreeUpgrade('welcome');
    AccountController.render();
    if (!isStudioAccessActive() && AppState.account.accessState !== 'suspended') {
        openUpgradeModal('Purchase Lifetime Pro to create videos. Already purchased? Sign in with your purchase account and refresh access.');
    }
    window.electronAPI?.onAuthStateChanged?.((auth) => {
        const wasActive = isStudioAccessActive();
        const changed = applyDesktopAuthPayload(auth || {});
        if (!changed) return;
        AccountController.render();
        const isActive = isStudioAccessActive();
        if (wasActive !== isActive) {
            showToast(isActive ? 'Plan activated. Studio is unlocked.' : (AppState.account.accessCopy || 'Plan access changed.'), isActive ? 'success' : 'warning');
        }
        if (!isActive && wasActive && AppState.account.accessState !== 'suspended' && AppState.currentSection !== 'profile') {
            openUpgradeModal(AppState.account.accessCopy || 'View your account options.');
        }
        if (AppState.account.planKey === 'free_trial') maybeShowFreeUpgrade('welcome');
    });
    window.electronAPI?.onOpenProjects?.((payload = {}) => {
        GalleryController.queueAutoOpen(payload.videoUrl || '', payload.title || '', payload.path || payload.videoPath || '');
        navigateTo('gallery');
    });
    AccountController.startEntitlementPolling();
})().catch(error => {
    appendRuntimeLog(`[startup] ${error.message || error}`, 'error');
    showToast('Some controls could not finish loading. Open Settings or retry the connection.', 'error');
});

// Upgrade offers remain dismissible with mouse, keyboard, or the visible close button.
document.getElementById('upgradePlanModal')?.addEventListener('click', (event) => {
    if (event.target.id === 'upgradePlanModal') closeUpgradeModal();
});
document.addEventListener('keydown', (event) => {
    const modal = document.getElementById('upgradePlanModal');
    if (!modal?.classList.contains('open')) return;
    if (event.key === 'Escape') closeUpgradeModal();
    if (event.key !== 'Tab') return;
    const buttons = Array.from(modal.querySelectorAll('button:not([disabled]),a[href]'));
    const first = buttons[0], last = buttons[buttons.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
});
