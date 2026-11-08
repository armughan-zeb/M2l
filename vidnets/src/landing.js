const stateEls = {
    phase: document.getElementById('backendPhase'),
    message: document.getElementById('backendMessage'),
    error: document.getElementById('backendError'),
    retry: document.getElementById('retryBtn'),
    launchBtn: document.getElementById('autoLaunchBtn'),
    continueBtn: document.getElementById('continueWithGoogleBtn'),
    logList: document.getElementById('logList'),
    logCount: document.getElementById('logCount'),
    authNote: document.getElementById('authNote'),
    progressWrap: document.getElementById('progressWrap'),
    progressFill: document.getElementById('progressFill')
};

let logCount = 0;
let signInRequested = false;
let pendingAutoSignin = false;
let currentBrowserFlow = null;
let browserPollTimer = null;
let browserFlowStartedAt = 0;
const BROWSER_FLOW_MAX_MS = 15 * 60 * 1000;

function appendLog(message, muted = false) {
    if (!stateEls.logList || !message) return;
    if (logCount === 0) {
        stateEls.logList.innerHTML = '';
    }

    const entry = document.createElement('div');
    entry.className = `log-entry${muted ? ' muted' : ''}`;
    entry.textContent = message;
    stateEls.logList.prepend(entry);
    logCount += 1;
    stateEls.logCount.textContent = String(logCount);
}

function setAuthNote(message, type = 'info') {
    if (!stateEls.authNote) return;
    stateEls.authNote.textContent = message;
    stateEls.authNote.dataset.state = type;
}

function setContinueButtonState(mode = 'idle') {
    if (!stateEls.continueBtn) return;

    stateEls.continueBtn.disabled = mode === 'pending' || mode === 'disabled';
    stateEls.continueBtn.classList.toggle('is-pending', mode === 'pending');

    if (mode === 'pending') {
        stateEls.continueBtn.textContent = 'Opening Google sign-in';
        return;
    }

    if (mode === 'disabled') {
        stateEls.continueBtn.textContent = 'Backend still starting';
        return;
    }

    stateEls.continueBtn.textContent = 'Continue with Google';
}

function setBackendState(state) {
    if (!state) return;

    const phaseText = state.phase === 'ready'
        ? 'Ready'
        : state.phase === 'error'
            ? 'Error'
            : state.phase === 'stopped'
                ? 'Stopped'
                : 'Starting';

    stateEls.phase.textContent = phaseText;
    stateEls.message.textContent = state.message || 'Preparing backend...';

    if (state.error) {
        stateEls.error.textContent = state.error;
        stateEls.error.classList.remove('hidden');
    } else {
        stateEls.error.textContent = '';
        stateEls.error.classList.add('hidden');
    }

    stateEls.retry.classList.toggle('hidden', state.phase !== 'error' && state.phase !== 'stopped');

    // Show progress bar during dependency installation
    if (state.phase === 'starting' && state.message && !state.message.startsWith('Starting')) {
        stateEls.progressWrap?.classList.remove('hidden');
    } else {
        stateEls.progressWrap?.classList.add('hidden');
    }

    if (stateEls.launchBtn) {
        stateEls.launchBtn.classList.remove('is-pending', 'is-error', 'is-ready');
        if (state.phase === 'ready') {
            stateEls.launchBtn.textContent = signInRequested ? 'Waiting for Google sign-in...' : 'Backend ready';
            stateEls.launchBtn.classList.add(signInRequested ? 'is-pending' : 'is-ready');
        } else if (state.phase === 'error' || state.phase === 'stopped') {
            stateEls.launchBtn.textContent = 'Studio paused';
            stateEls.launchBtn.classList.add('is-error');
        } else {
            stateEls.launchBtn.textContent = signInRequested ? 'Google sign-in in progress' : 'Waiting for Google sign in';
        }
    }

    if (state.phase === 'ready' && pendingAutoSignin && !currentBrowserFlow) {
        pendingAutoSignin = false;
        window.setTimeout(() => {
            startBrowserSignin().catch((error) => {
                setAuthNote(error.message || 'Could not start Google sign-in.', 'error');
                setContinueButtonState('idle');
            });
        }, 150);
    } else if (state.phase === 'ready' && !currentBrowserFlow) {
        setContinueButtonState('idle');
    } else if (state.phase !== 'ready' && !currentBrowserFlow) {
        setContinueButtonState('disabled');
    }
}

function stopBrowserPoller() {
    if (browserPollTimer) {
        window.clearTimeout(browserPollTimer);
        browserPollTimer = null;
    }
}

async function pollBrowserSignin() {
    if (!currentBrowserFlow) return;

    if (browserFlowStartedAt && Date.now() - browserFlowStartedAt > BROWSER_FLOW_MAX_MS) {
        stopBrowserPoller();
        currentBrowserFlow = null;
        signInRequested = false;
        setContinueButtonState('idle');
        setAuthNote('This sign-in attempt expired. Start Google sign-in again.', 'error');
        appendLog('Google sign-in expired before it completed.', true);
        return;
    }

    try {
        const status = await window.electronAPI?.checkBrowserGoogleSignIn({
            flowId: currentBrowserFlow.flow_id,
            flowSecret: currentBrowserFlow.flow_secret
        });

        if (status?.status === 'completed' && status?.payload) {
            stopBrowserPoller();
            appendLog('In-app Google sign-in completed. Syncing desktop session...');
            let auth = null;
            try {
                auth = await window.electronAPI?.completeGoogleSignIn(status.payload);
            } catch (error) {
                currentBrowserFlow = null;
                browserFlowStartedAt = 0;
                signInRequested = false;
                setContinueButtonState('idle');
                setAuthNote(error.message || 'Google sign-in could not be verified.', 'error');
                appendLog(`Google sign-in rejected: ${error.message || error}`, true);
                return;
            }
            const backendState = await window.electronAPI?.getBackendState();
            setBackendState(backendState);
            setContinueButtonState('idle');
            currentBrowserFlow = null;
            browserFlowStartedAt = 0;
            setAuthNote(auth?.access_state === 'active'
                ? 'Plan active. Opening the studio.'
                : 'Account connected. Purchase Lifetime Pro, then refresh access using the same Google account.', 'success');
            appendLog(auth?.access_state === 'active'
                ? 'Desktop session synced. Active plan detected.'
                : 'Desktop session synced. Pending access will open pricing.');
            return;
        }

        if (status?.status === 'error') {
            stopBrowserPoller();
            currentBrowserFlow = null;
            browserFlowStartedAt = 0;
            signInRequested = false;
            setContinueButtonState('idle');
            setAuthNote(status.error || 'Google sign-in failed.', 'error');
            appendLog(`Google sign-in failed: ${status.error || 'Unknown error'}`, true);
            return;
        }
    } catch (error) {
        if (error?.status === 404) {
            stopBrowserPoller();
            currentBrowserFlow = null;
            browserFlowStartedAt = 0;
            signInRequested = false;
            setContinueButtonState('idle');
            setAuthNote('This sign-in attempt expired. Start Google sign-in again.', 'error');
            appendLog('Google sign-in flow was not found or expired.', true);
            return;
        }
        appendLog(`Google sign-in polling error: ${error.message || error}`, true);
    }

    browserPollTimer = window.setTimeout(pollBrowserSignin, 2000);
}

async function startBrowserSignin() {
    const backendState = await window.electronAPI?.getBackendState();
    if (backendState?.phase !== 'ready') {
        signInRequested = true;
        pendingAutoSignin = true;
        setAuthNote('Preparing Vidnets. Google sign-in will open automatically in a moment.', 'info');
        setContinueButtonState('pending');
        appendLog('Google sign-in queued while the backend finishes starting.', true);
        return;
    }

    signInRequested = true;
    setContinueButtonState('pending');
    setAuthNote('Opening Google sign-in in your default browser...', 'info');
    appendLog('Starting secure browser Google sign-in flow.', true);

    try {
        currentBrowserFlow = await window.electronAPI?.startBrowserGoogleSignIn();
        browserFlowStartedAt = Date.now();
        appendLog('Google sign-in opened in your default browser.', true);
        setAuthNote('Choose your existing Google account in the browser. Vidnets will continue automatically.', 'info');
        stopBrowserPoller();
        browserPollTimer = window.setTimeout(pollBrowserSignin, 1000);
    } catch (error) {
        signInRequested = false;
        currentBrowserFlow = null;
        browserFlowStartedAt = 0;
        setContinueButtonState('idle');
        setAuthNote(error.message || 'Could not start Google sign-in.', 'error');
        appendLog(`Google sign-in could not start: ${error.message || error}`, true);
    }
}

document.getElementById('purchaseLifetimeBtn')?.addEventListener('click', async () => {
    try { await window.electronAPI?.openExternalUrl('https://vidnets.com/#pricing'); }
    catch (error) { setAuthNote(error.message || 'Could not open the purchase page.', 'error'); }
});
document.getElementById('minimizeBtn')?.addEventListener('click', () => window.electronAPI?.minimize());
document.getElementById('maximizeBtn')?.addEventListener('click', () => window.electronAPI?.maximize());
document.getElementById('closeBtn')?.addEventListener('click', () => window.electronAPI?.close());

document.getElementById('retryBtn')?.addEventListener('click', async () => {
    const nextState = await window.electronAPI?.retryBackendStartup();
    setBackendState(nextState);
    appendLog('Retry requested from landing screen.', true);
});

document.getElementById('continueWithGoogleBtn')?.addEventListener('click', () => {
    startBrowserSignin().catch((error) => {
        setAuthNote(error.message || 'Could not start Google sign-in.', 'error');
    });
});

window.electronAPI?.onBackendLog((message) => appendLog(message));
window.electronAPI?.onBackendStateChanged((state) => setBackendState(state));
window.addEventListener('DOMContentLoaded', async () => {
    const state = await window.electronAPI?.getBackendState();
    setBackendState(state);
    appendLog('Landing shell ready.', true);
    setAuthNote('Local engine ready. Opening Vidnets Studio...', 'info');
    setContinueButtonState(state?.phase === 'ready' ? 'idle' : 'disabled');
});
