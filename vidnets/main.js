const { app, BrowserWindow, ipcMain, dialog, protocol, net, shell, safeStorage, Notification, session } = require('electron');
const path = require('path');
const fs = require('fs');
const http = require('http');
const https = require('https');
const os = require('os');
const crypto = require('crypto');
const { spawn, execFile, execSync, spawnSync } = require('child_process');
const tcp = require('net');

const APP_USER_MODEL_ID = 'com.vidzeo.studio.v1';
const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) app.quit();

// Keep the existing Electron userData directory and auth storage stable while
// presenting the new Vidnets Studio brand to users. The app id and storage
// keys below are compatibility identifiers, not public product names.
app.setName('vidnets-studio-v1');

if (process.platform === 'win32') {
    app.setAppUserModelId(APP_USER_MODEL_ID);
}

// Low-End & High-Performance Hardware Acceleration Switches
app.commandLine.appendSwitch('enable-gpu-rasterization');
app.commandLine.appendSwitch('enable-zero-copy');
app.commandLine.appendSwitch('ignore-gpu-blocklist');
app.commandLine.appendSwitch('enable-hardware-overlays', 'single-fullscreen,single-on-top,underlay');
app.commandLine.appendSwitch('enable-features', 'VaapiVideoDecoder,PlatformHEVCDecoderSupport,CanvasOopRasterization');
app.commandLine.appendSwitch('disable-features', 'UseChromeOSDirectVideoDecoder');

const AUTHENTIC_CHROME_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';

protocol.registerSchemesAsPrivileged([
    { scheme: 'local-media', privileges: { secure: true, standard: true, supportFetchAPI: true, corsEnabled: true } }
]);


let mainWindow = null;
let pendingProtocolUrl = '';
let pythonProcess = null;
let backendMonitor = null;
let backendReady = false;
let appLoaded = false;
let lastLoggedBackendState = null;
let backendStatusMisses = 0;
let backendMonitorInFlight = false;
let lastBackendActivityAt = 0;
let authRequested = false;
let lastAuthPersistFingerprint = '';
let lastAuthPersistLogAt = 0;
let lastAuthSyncWarningAt = 0;
let lastAuthSyncWarningMessage = '';
let lastAuthSyncAttemptAt = 0;
let lastAuthRefreshWarningAt = 0;
let lastAuthRefreshWarningMessage = '';
let lastAuthRefreshAttemptAt = 0;
let lastAuthBackendRejectedAt = 0;
let managedYouTubeProcess = null;
let managedYouTubeEndpoint = null;
let managedYouTubeContext = null;
let managedYouTubeWindow = null;
let managedYouTubeConnectionPromise = null;
let appQuitting = false;
let backendStopRequested = false;
let backendRestartTimer = null;
let backendRestartAttempts = 0;
let rendererRecoveryInFlight = false;

function focusMainWindow() {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
}

function handleVidnetsProtocolUrl(rawUrl) {
    const value = String(rawUrl || '').trim();
    if (!/^vidnets:\/\//i.test(value)) return false;
    pendingProtocolUrl = value;
    focusMainWindow();
    if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('vidnets-protocol-opened', { url: value, source: 'external' });
    }
    return true;
}

// The hosted payment return page uses vidnets://open as a best-effort deep
// link. It never carries a token; it only asks the already-installed app to
// come to the foreground after checkout.
app.on('second-instance', (_event, commandLine) => {
    const protocolUrl = commandLine.find((argument) => /^vidnets:\/\//i.test(argument));
    if (protocolUrl) handleVidnetsProtocolUrl(protocolUrl);
    else focusMainWindow();
});
app.on('open-url', (event, url) => {
    event.preventDefault();
    handleVidnetsProtocolUrl(url);
});

const DEFAULT_BACKEND_PORT = 5000;
// Keep Electron and Flask on the same media-pipeline contract. If a previous
// desktop session left an older Flask process listening, the app starts a new
// isolated backend instead of silently generating with stale retrieval code.
const EXPECTED_PIPELINE_BUILD_ID = '2026-10-09-local-agent-v1';
// The desktop backend normally uses 5000, but another local service (or a
// stale launch) must not make a packaged app look frozen.  The active values
// are selected before Python starts and every renderer/connector URL uses the
// same port.
let BACKEND_PORT = DEFAULT_BACKEND_PORT;
let BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;
// The release repository is intentionally separate from the application
// repository. `latest.json` is a small, reviewable trust root while the large
// installer is uploaded as a GitHub Release asset.
const UPDATE_MANIFEST_URL = String(
    process.env.VIDZEO_UPDATE_MANIFEST_URL
    || 'https://raw.githubusercontent.com/armughan-zeb/Vidnets-Studio-Releases/main/latest.json'
).trim();
const UPDATE_MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024;
const UPDATE_MIN_DOWNLOAD_BYTES = 10 * 1024 * 1024;
const UPDATE_REQUEST_TIMEOUT_MS = 15000;
const UPDATE_DOWNLOAD_TIMEOUT_MS = 60000;
const appUpdateState = {
    phase: 'idle',
    currentVersion: '',
    latestVersion: '',
    releaseNotes: '',
    buildId: '',
    currentBuildId: '',
    progress: 0,
    manifestUrl: UPDATE_MANIFEST_URL,
    installerUrl: '',
    downloaded: false,
    error: '',
};
let appUpdateManifest = null;
let appUpdateDownloadedPath = '';
const BACKEND_SLOW_GRACE_MS = 30000;
const AUTH_WARNING_INTERVAL_MS = 60000;
const AUTH_SYNC_INTERVAL_MS = 60 * 1000;
const AUTH_SYNC_FETCH_TIMEOUT_MS = 8000;
const AUTH_REFRESH_RETRY_INTERVAL_MS = 2 * 60 * 1000;
let lastAuth429At = 0;
let lastAuth429RetryCount = 0;
const AUTH_BACKEND_REJECT_RETRY_MS = 15 * 60 * 1000;
const allowedLocalMediaFiles = new Set();
const DESKTOP_CONNECTOR_TOKEN = crypto.randomBytes(32).toString('hex');
const YOUTUBE_CONNECT_TIMEOUT_MS = 10 * 60 * 1000;
const YOUTUBE_AUTH_COOKIE_NAMES = new Set([
    'SID', 'HSID', 'SSID', 'APISID', 'SAPISID', 'LOGIN_INFO',
    '__SECURE-1PAPISID', '__SECURE-3PAPISID', '__SECURE-1PSID',
    '__SECURE-3PSID', '__SECURE-1PSIDTS', '__SECURE-3PSIDTS'
]);

function pathIsInside(childPath, parentPath) {
    const child = path.resolve(childPath);
    const parent = path.resolve(parentPath);
    const relative = path.relative(parent, child);
    return relative === '' || (!!relative && !relative.startsWith('..') && !path.isAbsolute(relative));
}

function isAllowedLocalMediaPath(filePath) {
    const resolved = path.resolve(filePath);
    if (allowedLocalMediaFiles.has(resolved)) {
        return true;
    }
    return ['output', 'temp', 'cache'].some((dir) => pathIsInside(resolved, path.join(appRoot, dir)));
}

function isAllowedAppNavigation(rawUrl) {
    try {
        const parsed = new URL(rawUrl);
        if (parsed.protocol === 'file:') {
            return true;
        }
        return parsed.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(parsed.hostname) && parsed.port === String(BACKEND_PORT);
    } catch (_error) {
        return false;
    }
}

async function openSafeExternalUrl(rawUrl) {
    const parsed = new URL(String(rawUrl || ''));
    const isLocalHttp = parsed.protocol === 'http:' && ['localhost', '127.0.0.1'].includes(parsed.hostname);
    const isSafeExternal = parsed.protocol === 'https:' || parsed.protocol === 'mailto:';
    if (!isSafeExternal && !isLocalHttp) {
        throw new Error('Blocked unsafe external URL.');
    }
    await shell.openExternal(parsed.toString());
}

function compareAppVersions(left, right) {
    const parse = (value) => String(value || '').trim().replace(/^v/i, '').split(/[+\-]/, 1)[0]
        .split('.').map((part) => Number.parseInt(part, 10) || 0).slice(0, 3);
    const a = parse(left);
    const b = parse(right);
    for (let index = 0; index < 3; index += 1) {
        if (a[index] !== b[index]) return a[index] > b[index] ? 1 : -1;
    }
    return 0;
}

// A release can contain a small but important fix without changing the
// marketing version.  The build script writes a content fingerprint into the
// packaged resources so the updater can still distinguish that installer from
// an older 1.0.0 build.
function getPackagedBuildId() {
    const candidates = [
        getResourcePath('public', 'update-build.json'),
        path.join(__dirname, 'public', 'update-build.json'),
    ];
    for (const candidate of candidates) {
        try {
            const payload = JSON.parse(fs.readFileSync(candidate, 'utf8'));
            const value = String(payload?.buildId || '').trim().toLowerCase();
            if (/^[a-f0-9]{16,64}$/.test(value)) return value;
        } catch (_error) { /* older packages do not have a build stamp */ }
    }
    return '';
}

function isLocalUpdateUrl(parsed) {
    return parsed.protocol === 'http:'
        && ['localhost', '127.0.0.1'].includes(parsed.hostname)
        && parsed.port !== '';
}

const TRUSTED_UPDATE_HOSTS = new Set([
    'github.com',
    'raw.githubusercontent.com',
    'objects.githubusercontent.com',
    'github-releases.githubusercontent.com',
    'release-assets.githubusercontent.com',
]);

function isTrustedUpdateRedirect(parsed, source) {
    if (isLocalUpdateUrl(source)) {
        return isLocalUpdateUrl(parsed) && parsed.hostname === source.hostname && parsed.port === source.port;
    }
    return parsed.protocol === 'https:'
        && TRUSTED_UPDATE_HOSTS.has(String(parsed.hostname || '').toLowerCase());
}

function isTrustedUpdateArtifactUrl(parsed, manifestParsed) {
    if (parsed.protocol !== 'https:' && !isLocalUpdateUrl(parsed)) return false;
    if (isLocalUpdateUrl(manifestParsed)) {
        return isLocalUpdateUrl(parsed)
            && parsed.hostname === manifestParsed.hostname
            && parsed.port === manifestParsed.port;
    }
    const manifestHost = String(manifestParsed.hostname || '').toLowerCase();
    const artifactHost = String(parsed.hostname || '').toLowerCase();
    if (manifestHost === 'raw.githubusercontent.com') {
        return TRUSTED_UPDATE_HOSTS.has(artifactHost) && artifactHost !== 'raw.githubusercontent.com';
    }
    return artifactHost === manifestHost;
}

function normalizeUpdateManifest(payload) {
    if (!payload || typeof payload !== 'object') throw new Error('Update manifest is not valid JSON.');
    const version = String(payload.version || '').trim();
    if (!/^v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/.test(version)) {
        throw new Error('Update manifest has an invalid version.');
    }
    if (payload.platform && String(payload.platform).toLowerCase() !== process.platform) {
        throw new Error('Update is not for this platform.');
    }
    if (payload.arch && String(payload.arch).toLowerCase() !== process.arch) {
        throw new Error('Update is not for this processor architecture.');
    }
    const manifestParsed = new URL(UPDATE_MANIFEST_URL);
    const artifact = payload.artifact && typeof payload.artifact === 'object' ? payload.artifact : payload;
    const rawUrl = String(artifact.url || artifact.downloadUrl || artifact.path || '').trim();
    if (!rawUrl) throw new Error('Update manifest does not include an installer URL.');
    const installerUrl = new URL(rawUrl, manifestParsed).toString();
    const artifactParsed = new URL(installerUrl);
    if (!isTrustedUpdateArtifactUrl(artifactParsed, manifestParsed)) {
        throw new Error('Update installer URL is not trusted.');
    }
    const name = path.basename(decodeURIComponent(artifactParsed.pathname));
    if (!/^Vidzeo[-_A-Za-z0-9. ]+\.(?:exe|msi)$/i.test(name) || name.includes('..')) {
        throw new Error('Update installer filename is invalid.');
    }
    const sha256 = String(artifact.sha256 || payload.sha256 || '').trim().toLowerCase();
    if (!/^[a-f0-9]{64}$/.test(sha256)) {
        throw new Error('Update manifest is missing a valid SHA-256 checksum.');
    }
    const size = Number(artifact.size || payload.size || 0);
    if (!Number.isSafeInteger(size) || size < UPDATE_MIN_DOWNLOAD_BYTES || size > UPDATE_MAX_DOWNLOAD_BYTES) {
        throw new Error('Update installer size is outside the safe range.');
    }
    return {
        version: version.replace(/^v/i, ''),
        installerUrl,
        name,
        sha256,
        size,
        releaseNotes: String(payload.releaseNotes || payload.notes || '').trim().slice(0, 4000),
        releasePage: String(payload.releasePage || '').trim().slice(0, 500),
        buildId: String(payload.buildId || artifact.buildId || '').trim().toLowerCase(),
    };
}

function publishAppUpdateState(patch = {}) {
    Object.assign(appUpdateState, patch, { currentVersion: app.getVersion(), manifestUrl: UPDATE_MANIFEST_URL });
    if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('app-update-state-changed', { ...appUpdateState });
    }
    return { ...appUpdateState };
}

function fetchUpdateManifest() {
    return new Promise((resolve, reject) => {
        let parsed;
        try { parsed = new URL(UPDATE_MANIFEST_URL); } catch (_error) { reject(new Error('Update manifest URL is invalid.')); return; }
        if (parsed.protocol !== 'https:' && !isLocalUpdateUrl(parsed)) {
            reject(new Error('Update manifest must use HTTPS.'));
            return;
        }
        const client = parsed.protocol === 'https:' ? https : http;
        const request = client.get(parsed, {
            headers: { 'User-Agent': `Vidnets-Studio/${app.getVersion()}`, Accept: 'application/json' },
        }, (response) => {
            const status = Number(response.statusCode || 0);
            if (status >= 300 && status < 400 && response.headers.location) {
                response.resume();
                // Redirects are only followed to HTTPS; the manifest itself
                // remains pinned to the configured endpoint.
                const redirect = new URL(response.headers.location, parsed);
                if (!isTrustedUpdateRedirect(redirect, parsed)) {
                    reject(new Error('Update manifest redirect is not secure.'));
                    return;
                }
                fetchJsonUrl(redirect, 1).then(resolve, reject);
                return;
            }
            if (status < 200 || status >= 300) {
                response.resume();
                reject(new Error(`Update server returned HTTP ${status}.`));
                return;
            }
            let body = '';
            response.setEncoding('utf8');
            response.on('data', (chunk) => { body += chunk; if (body.length > 256 * 1024) request.destroy(new Error('Update manifest is too large.')); });
            response.on('end', () => {
                try { resolve(JSON.parse(body)); } catch (_error) { reject(new Error('Update server returned malformed JSON.')); }
            });
        });
        request.setTimeout(UPDATE_REQUEST_TIMEOUT_MS, () => request.destroy(new Error('Update check timed out.')));
        request.on('error', reject);
    });
}

function fetchJsonUrl(url, redirectCount = 0) {
    if (redirectCount > 3) return Promise.reject(new Error('Too many update redirects.'));
    return new Promise((resolve, reject) => {
        const parsed = url instanceof URL ? url : new URL(url);
        const client = parsed.protocol === 'https:' ? https : http;
        const request = client.get(parsed, { headers: { 'User-Agent': `Vidnets-Studio/${app.getVersion()}` } }, (response) => {
            const status = Number(response.statusCode || 0);
            if (status >= 300 && status < 400 && response.headers.location) {
                response.resume();
                const redirect = new URL(response.headers.location, parsed);
                if (!isTrustedUpdateRedirect(redirect, parsed)) {
                    reject(new Error('Update manifest redirect is not trusted.'));
                    return;
                }
                fetchJsonUrl(redirect, redirectCount + 1).then(resolve, reject);
                return;
            }
            if (status < 200 || status >= 300) { response.resume(); reject(new Error(`Update server returned HTTP ${status}.`)); return; }
            let body = '';
            response.setEncoding('utf8');
            response.on('data', (chunk) => { body += chunk; });
            response.on('end', () => { try { resolve(JSON.parse(body)); } catch (_error) { reject(new Error('Update server returned malformed JSON.')); } });
        });
        request.setTimeout(UPDATE_REQUEST_TIMEOUT_MS, () => request.destroy(new Error('Update check timed out.')));
        request.on('error', reject);
    });
}

function downloadUpdateFile(installerUrl, destination, onProgress, redirectCount = 0) {
    return new Promise((resolve, reject) => {
        if (redirectCount > 5) {
            reject(new Error('Installer download followed too many redirects.'));
            return;
        }
    const parsed = new URL(installerUrl);
        if (parsed.protocol !== 'https:' && !isLocalUpdateUrl(parsed)) {
            reject(new Error('Installer download must use HTTPS.'));
            return;
        }
        const client = parsed.protocol === 'https:' ? https : http;
        const request = client.get(parsed, {
            headers: { 'User-Agent': `Vidnets-Studio/${app.getVersion()}`, Accept: 'application/octet-stream' },
        }, (response) => {
            const status = Number(response.statusCode || 0);
            if (status >= 300 && status < 400 && response.headers.location) {
                response.resume();
                const redirect = new URL(response.headers.location, parsed);
                if (!isTrustedUpdateRedirect(redirect, parsed)) {
                    reject(new Error('Installer download redirect is not trusted.'));
                    return;
                }
                downloadUpdateFile(redirect.toString(), destination, onProgress, redirectCount + 1).then(resolve, reject);
                return;
            }
            if (status < 200 || status >= 300) { response.resume(); reject(new Error(`Installer download returned HTTP ${status}.`)); return; }
            const total = Number(response.headers['content-length'] || 0);
            if (total && total > UPDATE_MAX_DOWNLOAD_BYTES) { response.resume(); reject(new Error('Installer exceeds the safe download size.')); return; }
            const output = fs.createWriteStream(destination, { flags: 'wx' });
            let received = 0;
            response.on('data', (chunk) => {
                received += chunk.length;
                if (received > UPDATE_MAX_DOWNLOAD_BYTES) request.destroy(new Error('Installer exceeds the safe download size.'));
                if (total) onProgress(Math.min(99, Math.round(received / total * 100)));
            });
            response.on('error', (error) => output.destroy(error));
            output.on('error', reject);
            output.on('finish', () => { output.close(() => resolve(received)); });
            response.pipe(output);
        });
        request.setTimeout(UPDATE_DOWNLOAD_TIMEOUT_MS, () => request.destroy(new Error('Installer download timed out.')));
        request.on('error', reject);
    });
}

async function sha256File(filePath) {
    return new Promise((resolve, reject) => {
        const hash = crypto.createHash('sha256');
        const input = fs.createReadStream(filePath);
        input.on('data', (chunk) => hash.update(chunk));
        input.on('error', reject);
        input.on('end', () => resolve(hash.digest('hex')));
    });
}

async function checkForAppUpdate() {
    publishAppUpdateState({ phase: 'checking', progress: 0, error: '', downloaded: false });
    try {
        const manifest = normalizeUpdateManifest(await fetchUpdateManifest());
        appUpdateManifest = manifest;
        const currentBuildId = getPackagedBuildId();
        const isNewer = compareAppVersions(manifest.version, app.getVersion()) > 0;
        const isDifferentBuild = Boolean(manifest.buildId && currentBuildId && manifest.buildId !== currentBuildId);
        // If an older package predates build stamps, a same-version manifest is
        // treated as an update once.  New packages carry the stamp and will not
        // repeatedly download their own installer.
        const isUnstampedPackage = !currentBuildId && Boolean(manifest.buildId);
        if (!isNewer && !isDifferentBuild && !isUnstampedPackage) {
            appUpdateDownloadedPath = '';
            return publishAppUpdateState({ phase: 'up_to_date', latestVersion: manifest.version, installerUrl: manifest.installerUrl, releaseNotes: manifest.releaseNotes, buildId: manifest.buildId, currentBuildId, currentVersion: app.getVersion(), progress: 100, downloaded: false });
        }
        return publishAppUpdateState({ phase: 'available', latestVersion: manifest.version, installerUrl: manifest.installerUrl, releaseNotes: manifest.releaseNotes, buildId: manifest.buildId, currentBuildId, progress: 0, downloaded: false });
    } catch (error) {
        appUpdateManifest = null;
        return publishAppUpdateState({ phase: 'error', progress: 0, error: error.message || 'Update check failed.', downloaded: false });
    }
}

async function downloadAppUpdate() {
    if (!appUpdateManifest) await checkForAppUpdate();
    if (!appUpdateManifest) return { ...appUpdateState };
    const currentBuildId = getPackagedBuildId();
    const isEligible = compareAppVersions(appUpdateManifest.version, app.getVersion()) > 0
        || !appUpdateManifest.buildId
        || !currentBuildId
        || appUpdateManifest.buildId !== currentBuildId;
    if (!isEligible) return { ...appUpdateState };
    const updatesDir = path.join(app.getPath('userData'), 'updates');
    fs.mkdirSync(updatesDir, { recursive: true });
    const destination = path.join(updatesDir, `${appUpdateManifest.version}-${appUpdateManifest.name}`);
    publishAppUpdateState({ phase: 'downloading', progress: 0, error: '' });
    try {
        if (!fs.existsSync(destination)) {
            await downloadUpdateFile(appUpdateManifest.installerUrl, destination, (progress) => publishAppUpdateState({ progress }));
        }
        const actualSize = fs.statSync(destination).size;
        const actualHash = await sha256File(destination);
        if (actualSize !== appUpdateManifest.size || actualHash !== appUpdateManifest.sha256) {
            fs.rmSync(destination, { force: true });
            throw new Error('Downloaded installer failed its integrity check.');
        }
        appUpdateDownloadedPath = destination;
        return publishAppUpdateState({ phase: 'ready', progress: 100, downloaded: true, error: '' });
    } catch (error) {
        fs.rmSync(destination, { force: true });
        appUpdateDownloadedPath = '';
        return publishAppUpdateState({ phase: 'error', progress: 0, error: error.message || 'Update download failed.', downloaded: false });
    }
}

async function installAppUpdate() {
    const updatesDir = path.join(app.getPath('userData'), 'updates');
    if (!appUpdateDownloadedPath || !pathIsInside(appUpdateDownloadedPath, updatesDir) || !fs.existsSync(appUpdateDownloadedPath)) {
        throw new Error('Download the verified update before installing it.');
    }
    const stat = fs.statSync(appUpdateDownloadedPath);
    if (!stat.isFile()) throw new Error('Verified update is not a file.');
    if (!appUpdateManifest?.version || !appUpdateManifest?.sha256) {
        throw new Error('Verified update metadata is missing. Check for updates again.');
    }
    // Keep the installer fingerprint across restarts so a same-version build
    // does not keep presenting itself as new when the package is updated in
    // place on the release channel.
    const markerPath = path.join(app.getPath('userData'), 'updates', 'installed-build.json');
    fs.writeFileSync(markerPath, JSON.stringify({
        version: appUpdateManifest.version,
        buildId: appUpdateManifest.buildId || '',
        sha256: appUpdateManifest.sha256,
        installedAt: new Date().toISOString(),
    }, null, 2));
    spawn(appUpdateDownloadedPath, [], { detached: true, stdio: 'ignore', windowsHide: false }).unref();
    publishAppUpdateState({ phase: 'installing', progress: 100 });
    setTimeout(() => app.quit(), 250);
    return { ...appUpdateState };
}

async function openExternalAuthBrowser(authUrl) {
    const parsedAuthUrl = new URL(String(authUrl || ''));
    const isLegacyLocalAuthUrl = parsedAuthUrl.protocol === 'http:'
        && ['localhost', '127.0.0.1'].includes(parsedAuthUrl.hostname)
        && parsedAuthUrl.port === String(BACKEND_PORT);
    const isConfiguredSupabaseAuthUrl = parsedAuthUrl.protocol === 'https:'
        && parsedAuthUrl.origin === publicSupabaseUrl
        && parsedAuthUrl.pathname === '/auth/v1/authorize';
    if (!isLegacyLocalAuthUrl && !isConfiguredSupabaseAuthUrl) {
        throw new Error('Vidzeo received an invalid Google sign-in URL.');
    }

    // Use the user's default browser, but only for the configured Supabase
    // authorize endpoint. Google receives prompt=select_account, so this is
    // an explicit account choice rather than a silent website-session reuse.
    await shell.openExternal(parsedAuthUrl.toString());
    return true;
}

const backendState = {
    phase: 'idle',
    ready: false,
    message: 'Waiting to start backend…',
    error: '',
    code: null,
    details: null,
    usedExistingBackend: false
};

function normalizeBackendLogLine(value) {
    // Python modules in older packaged builds contain a few mojibake emoji
    // literals. Keep the desktop log readable and deterministic by emitting
    // plain UTF-8-safe text to both the console and renderer.
    return String(value || '')
        .replace(/[^\x09\x20-\x7E]/g, '')
        .replace(/\s{2,}/g, ' ')
        .trim();
}

function getResourcePath(...parts) {
    if (app.isPackaged) {
        return path.join(process.resourcesPath, ...parts);
    }
    return path.join(__dirname, ...parts);
}

function getWindowIconPath() {
    return firstExistingPath([
        getResourcePath('public', 'icon.ico'),
        getResourcePath('public', 'icon.png'),
        getResourcePath('src', 'assets', 'vidnets-icon-glow.png'),
    ]);
}

function getAppRoot() {
    if (app.isPackaged) {
        return path.join(app.getPath('userData'), 'VidzeoData');
    }
    return __dirname;
}

const appRoot = getAppRoot();
for (const dir of ['output', 'temp', 'cache', 'logs', 'secure', 'youtube-browser', path.join('template-library', 'custom')]) {
    const dirPath = path.join(appRoot, dir);
    if (!fs.existsSync(dirPath)) {
        fs.mkdirSync(dirPath, { recursive: true });
    }
}

// A packaged app may outlive the terminal that launched it.  Node emits an
// error when that terminal's stdout/stderr pipe closes; without a listener it
// becomes an uncaught exception and can terminate Electron while a job runs.
for (const stream of [process.stdout, process.stderr]) {
    if (stream && typeof stream.on === 'function') {
        stream.on('error', () => {
            // Runtime diagnostics are persisted below; a closed console pipe
            // must never be allowed to crash the desktop app.
        });
    }
}

function recordRuntimeLog(level, message, details = '') {
    const rendered = [message, details].filter(Boolean).join(' ')
        .replace(/Bearer\s+[A-Za-z0-9._~-]+/gi, 'Bearer [redacted]')
        .replace(/(OPENROUTER_API_KEY|SUPABASE_ANON_KEY|GOOGLE_CLIENT_ID)\s*[=:]\s*\S+/gi, '$1=[redacted]');
    const line = `${new Date().toISOString()} [${String(level || 'INFO').toUpperCase()}] ${rendered}`;
    try {
        const logPath = path.join(appRoot, 'logs', 'electron-runtime.log');
        if (fs.existsSync(logPath) && fs.statSync(logPath).size > 2 * 1024 * 1024) {
            fs.renameSync(logPath, `${logPath}.previous`);
        }
        fs.appendFileSync(logPath, `${line}\n`, 'utf8');
    } catch (_error) {
        // Diagnostics must never become a second crash source.
    }
    try {
        const stream = level === 'ERROR' ? process.stderr : process.stdout;
        if (stream && !stream.destroyed && stream.writable !== false) {
            stream.write(`[Electron] ${rendered}\n`);
        }
    } catch (_error) {
        // A closed parent console is expected for detached desktop launches.
    }
}

process.on('uncaughtException', (error) => {
    if (error?.code === 'EPIPE') return;
    recordRuntimeLog('ERROR', 'Uncaught Electron exception.', error?.stack || error?.message || error);
    broadcastBackendState({
        phase: 'error',
        ready: backendReady,
        code: 'ELECTRON_UNCAUGHT_EXCEPTION',
        error: 'Vidnets recovered from an internal desktop error. Your queue is preserved; check Queue diagnostics and retry.',
        message: 'Desktop error recovered. Your work was preserved.'
    });
});

process.on('unhandledRejection', (reason) => {
    recordRuntimeLog('ERROR', 'Unhandled Electron promise rejection.', reason?.stack || reason?.message || reason);
    broadcastBackendState({
        phase: backendReady ? 'ready' : 'error',
        ready: backendReady,
        code: 'ELECTRON_UNHANDLED_REJECTION',
        error: 'A background desktop operation failed. Check Queue diagnostics for details.',
        message: 'Background operation recovered; the app is still running.'
    });
});

function scheduleBackendRestart(reason = 'backend stopped unexpectedly') {
    if (appQuitting || backendStopRequested || backendRestartTimer || pythonProcess) return;
    backendRestartAttempts += 1;
    const delayMs = Math.min(15000, 1000 * (2 ** Math.min(backendRestartAttempts - 1, 4)));
    const message = `Backend stopped unexpectedly (${reason}). Retrying in ${Math.ceil(delayMs / 1000)}s; queue state is preserved.`;
    recordRuntimeLog('ERROR', message);
    broadcastBackendState({
        phase: 'error',
        ready: false,
        code: 'BACKEND_CRASHED',
        error: message,
        message: 'Backend recovered mode: retrying without losing your queue.'
    });
    backendRestartTimer = setTimeout(() => {
        backendRestartTimer = null;
        if (appQuitting || backendStopRequested || pythonProcess) return;
        selectBackendPort()
            .then(() => startPythonBackend())
            .catch((error) => scheduleBackendRestart(error?.message || 'backend restart failed'));
    }, delayMs);
}

function emitManagedYouTubeStatus(phase, message, details = {}) {
    const payload = {
        phase: String(phase || 'working'),
        message: String(message || ''),
        browser: String(details.browser || ''),
        cookieCount: Number(details.cookieCount || 0),
    };
    if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('youtube-connection-status', payload);
    }
}

function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchJsonWithTimeout(url, options = {}, timeoutMs = 4000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const response = await fetch(url, { ...options, signal: controller.signal });
        let payload = null;
        try {
            payload = await response.json();
        } catch (_error) {
            payload = null;
        }
        return { response, payload };
    } finally {
        clearTimeout(timer);
    }
}

function resolveManagedBrowserExecutable(browser) {
    if (process.platform !== 'win32') {
        throw new Error('The managed YouTube connector currently supports Windows distributions.');
    }
    const local = process.env.LOCALAPPDATA || '';
    const programFiles = process.env.ProgramFiles || 'C:\\Program Files';
    const programFilesX86 = process.env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)';
    const candidates = browser === 'edge'
        ? [
            path.join(programFiles, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
            path.join(programFilesX86, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
            local && path.join(local, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
        ]
        : [
            local && path.join(local, 'Google', 'Chrome', 'Application', 'chrome.exe'),
            path.join(programFiles, 'Google', 'Chrome', 'Application', 'chrome.exe'),
            path.join(programFilesX86, 'Google', 'Chrome', 'Application', 'chrome.exe'),
        ];
    const executable = firstExistingPath(candidates);
    if (!executable) {
        throw new Error(`${browser === 'edge' ? 'Microsoft Edge' : 'Google Chrome'} is not installed for this Windows user.`);
    }
    return executable;
}

async function readManagedDevToolsEndpoint(profileDir) {
    const activePortPath = path.join(profileDir, 'DevToolsActivePort');
    try {
        const lines = fs.readFileSync(activePortPath, 'utf8').trim().split(/\r?\n/);
        const port = Number.parseInt(lines[0], 10);
        if (!Number.isInteger(port) || port < 1024 || port > 65535) return null;
        const { response, payload } = await fetchJsonWithTimeout(
            `http://127.0.0.1:${port}/json/version`,
            { method: 'GET' },
            1200
        );
        if (!response.ok) return null;
        let webSocketDebuggerUrl = '';
        if (String(lines[1] || '').startsWith('/devtools/browser/')) {
            webSocketDebuggerUrl = `ws://127.0.0.1:${port}${lines[1]}`;
        } else if (payload?.webSocketDebuggerUrl) {
            const parsedSocketUrl = new URL(String(payload.webSocketDebuggerUrl));
            if (
                parsedSocketUrl.protocol !== 'ws:'
                || !['127.0.0.1', 'localhost', '[::1]'].includes(parsedSocketUrl.hostname)
                || Number(parsedSocketUrl.port) !== port
            ) return null;
            // Always connect via explicit IPv4 loopback even when Chromium
            // reports the debugger URL as localhost or IPv6.
            webSocketDebuggerUrl = `ws://127.0.0.1:${port}${parsedSocketUrl.pathname}`;
        }
        if (!webSocketDebuggerUrl) return null;
        return { port, webSocketDebuggerUrl };
    } catch (_error) {
        return null;
    }
}

async function executeManagedCdpCommand(endpoint, method, params = {}, timeoutMs = 8000) {
    const bridge = new BrowserWindow({
        show: false,
        webPreferences: {
            contextIsolation: true,
            nodeIntegration: false,
            sandbox: true,
            devTools: false,
        },
    });
    try {
        // Give the WebSocket a stable localhost Origin accepted by the
        // dedicated browser's --remote-allow-origins setting.
        await bridge.loadURL(`${BACKEND_URL}/api/status?connector_bridge=1`);
        const script = `new Promise((resolve, reject) => {
            const ws = new WebSocket(${JSON.stringify(endpoint.webSocketDebuggerUrl)});
            const commandId = 1;
            let settled = false;
            const finish = (fn, value) => {
                if (settled) return;
                settled = true;
                clearTimeout(timer);
                try { ws.close(); } catch (_) {}
                fn(value);
            };
            const timer = setTimeout(() => finish(reject, new Error('Browser connection timed out.')), ${Math.max(1000, timeoutMs)});
            ws.onerror = () => finish(reject, new Error('Could not communicate with the managed browser.'));
            ws.onopen = () => ws.send(JSON.stringify({ id: commandId, method: ${JSON.stringify(method)}, params: ${JSON.stringify(params)} }));
            ws.onmessage = (event) => {
                try {
                    const message = JSON.parse(event.data);
                    if (message.id !== commandId) return;
                    if (message.error) return finish(reject, new Error(message.error.message || 'Browser command failed.'));
                    finish(resolve, message.result || {});
                } catch (error) {
                    finish(reject, error);
                }
            };
        })`;
        return await bridge.webContents.executeJavaScript(script, true);
    } finally {
        if (!bridge.isDestroyed()) bridge.destroy();
    }
}

async function startOrReuseManagedYouTubeBrowser(browser) {
    const executable = resolveManagedBrowserExecutable(browser);
    const profileDir = path.join(appRoot, 'youtube-browser', browser);
    fs.mkdirSync(profileDir, { recursive: true });

    const existing = await readManagedDevToolsEndpoint(profileDir);
    if (existing) {
        managedYouTubeEndpoint = existing;
        try {
            await executeManagedCdpCommand(existing, 'Target.createTarget', {
                url: 'https://www.youtube.com/feed/you?persist_app=1&app=desktop',
                newWindow: false,
            });
        } catch (_error) {
            // The existing window is still usable even if tab creation races.
        }
        return existing;
    }

    const activePortPath = path.join(profileDir, 'DevToolsActivePort');
    if (pathIsInside(activePortPath, path.join(appRoot, 'youtube-browser'))) {
        try { fs.unlinkSync(activePortPath); } catch (_error) {}
    }

    const loginUrl = 'https://accounts.google.com/ServiceLogin?service=youtube&continue=https%3A%2F%2Fwww.youtube.com%2Ffeed%2Fyou';
    const args = [
        `--user-data-dir=${profileDir}`,
        '--profile-directory=Default',
        '--remote-debugging-address=127.0.0.1',
        '--remote-debugging-port=0',
        `--remote-allow-origins=${BACKEND_URL}`,
        '--disable-blink-features=AutomationControlled',
        `--user-agent=${AUTHENTIC_CHROME_UA}`,
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-background-mode',
        '--start-maximized',
        loginUrl,
    ];
    const child = spawn(executable, args, {
        windowsHide: false,
        stdio: 'ignore',
    });
    managedYouTubeProcess = child;
    let exitCode = null;
    child.once('exit', (code) => { exitCode = code; });
    child.once('error', () => { exitCode = -1; });

    const deadline = Date.now() + 25000;
    while (Date.now() < deadline) {
        const endpoint = await readManagedDevToolsEndpoint(profileDir);
        if (endpoint) {
            managedYouTubeEndpoint = endpoint;
            return endpoint;
        }
        if (exitCode !== null) {
            throw new Error(
                'The Vidzeo YouTube browser profile could not start. Close any older Vidzeo YouTube window and retry.'
            );
        }
        await delay(250);
    }
    try { child.kill(); } catch (_error) {}
    throw new Error('Chrome/Edge started but its secure connection channel did not become ready.');
}

function relevantManagedCookies(cookies) {
    return (Array.isArray(cookies) ? cookies : []).filter((cookie) => {
        const domain = String(cookie?.domain || '').replace(/^\./, '').toLowerCase();
        return domain === 'youtube.com' || domain.endsWith('.youtube.com')
            || domain === 'google.com' || domain.endsWith('.google.com');
    }).slice(0, 2048);
}

async function importManagedYouTubeCookies(browser, cookies) {
    const { response, payload } = await fetchJsonWithTimeout(
        `${BACKEND_URL}/api/youtube/session/import-managed-browser`,
        {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Vidzeo-Desktop-Connector': DESKTOP_CONNECTOR_TOKEN,
            },
            body: JSON.stringify({ browser, cookies }),
        },
        15000
    );
    if (!response.ok) {
        throw new Error(payload?.error || `YouTube session verification failed (${response.status}).`);
    }
    return payload;
}

async function importExistingYouTubeBrowserSession(browser) {
    // If the user is already signed in to their normal Chrome/Edge profile,
    // prefer that verified session as soon as the browser releases its cookie
    // database.  The backend performs the DPAPI-safe extraction and the same
    // positive YouTube verification used by the managed connector.  A locked
    // profile is expected while the user's browser is open, so this helper is
    // deliberately best-effort and never turns a connector attempt into a
    // hard failure.
    try {
        const { response, payload } = await fetchJsonWithTimeout(
            `${BACKEND_URL}/api/youtube/session/connect-browser`,
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ browser }),
            },
            18000
        );
        if (!response.ok) return null;
        const session = payload?.session || {};
        return session.ready && session.verified_authenticated ? payload : null;
    } catch (_error) {
        return null;
    }
}

async function closeManagedYouTubeBrowser(endpoint = managedYouTubeEndpoint) {
    closeYouTubeSignInWindow();
    if (endpoint) {
        try {
            await executeManagedCdpCommand(endpoint, 'Browser.close', {}, 3000);
        } catch (_error) {
            // Browser.close normally tears down the socket before responding.
        }
    }
    if (managedYouTubeProcess && managedYouTubeProcess.exitCode === null) {
        try { managedYouTubeProcess.kill(); } catch (_error) {}
    }
    managedYouTubeProcess = null;
    managedYouTubeEndpoint = null;
}

async function connectManagedYouTubeBrowser(browserValue) {
    const browser = String(browserValue || 'chrome').toLowerCase() === 'edge' ? 'edge' : 'chrome';
    if (!backendReady) throw new Error('Vidzeo is still starting. Try again in a moment.');
    emitManagedYouTubeStatus('opening', `Opening Vidzeo's ${browser === 'edge' ? 'Edge' : 'Chrome'} YouTube profile...`, { browser });
    const endpoint = await startOrReuseManagedYouTubeBrowser(browser);
    emitManagedYouTubeStatus('waiting', 'Choose your Google account and finish YouTube sign-in in the opened browser.', { browser });

    const deadline = Date.now() + YOUTUBE_CONNECT_TIMEOUT_MS;
    let lastAttemptSignature = '';
    let lastAttemptAt = 0;
    let lastImportError = '';
    try {
        while (Date.now() < deadline) {
            let result;
            try {
                result = await executeManagedCdpCommand(endpoint, 'Storage.getCookies', {}, 6000);
            } catch (error) {
                if (managedYouTubeProcess && managedYouTubeProcess.exitCode !== null) {
                    throw new Error('The YouTube sign-in window was closed before the account was connected.');
                }
                await delay(1000);
                continue;
            }
            const cookies = relevantManagedCookies(result?.cookies);
            const hasAuthCookie = cookies.some((cookie) => YOUTUBE_AUTH_COOKIE_NAMES.has(String(cookie.name || '').toUpperCase()));
            emitManagedYouTubeStatus(
                hasAuthCookie ? 'verifying' : 'waiting',
                hasAuthCookie ? 'YouTube account detected. Verifying the session...' : 'Waiting for YouTube sign-in to finish...',
                { browser, cookieCount: cookies.length }
            );

            if (hasAuthCookie && Date.now() - lastAttemptAt >= 2500) {
                const signature = crypto.createHash('sha256').update(JSON.stringify(
                    cookies.map((cookie) => [cookie.name, cookie.domain, cookie.expires, cookie.value])
                )).digest('hex');
                if (signature !== lastAttemptSignature || Date.now() - lastAttemptAt >= 15000) {
                    lastAttemptSignature = signature;
                    lastAttemptAt = Date.now();
                    try {
                        const payload = await importManagedYouTubeCookies(browser, cookies);
                        emitManagedYouTubeStatus('connected', 'YouTube connected, verified, and saved securely.', {
                            browser,
                            cookieCount: payload?.session?.cookie_count || cookies.length,
                        });
                        await closeManagedYouTubeBrowser(endpoint);
                        return payload;
                    } catch (error) {
                        lastImportError = error.message || 'YouTube has not confirmed the account yet.';
                    }
                }
            }
            await delay(1200);
        }
        throw new Error(lastImportError || 'YouTube sign-in timed out. Reopen Connect YouTube and finish account selection.');
    } catch (error) {
        emitManagedYouTubeStatus('error', error.message || 'YouTube connection failed.', { browser });
        await closeManagedYouTubeBrowser(endpoint);
        throw error;
    }
}

function resolveTrustedYouTubeConnectorPath() {
    const connectorDir = firstExistingPath([
        getResourcePath('chrome-connector'),
        path.join(__dirname, 'chrome-connector'),
    ]);
    if (!connectorDir) {
        throw new Error('The trusted Vidzeo YouTube connector is missing from this package. Rebuild the distribution.');
    }
    for (const requiredFile of ['manifest.json', 'service_worker.js']) {
        if (!fs.existsSync(path.join(connectorDir, requiredFile))) {
            throw new Error(`The trusted YouTube connector is incomplete (${requiredFile} missing).`);
        }
    }
    return connectorDir;
}

/**
 * Electron-native YouTube sign-in window.
 *
 * Uses Electron's own BrowserWindow with a dedicated persist: partition.
 * Uses the runtime browser identity without injected sign-in scripts.
 * Google may still require account verification or refuse embedded sign-in.
 *
 * Cookies are extracted via Electron's session.cookies.get() API, which
 * reads directly from memory — no file locks, no App-Bound Encryption,
 * no extensions needed.
 */
function closeYouTubeSignInWindow() {
    if (managedYouTubeWindow && !managedYouTubeWindow.isDestroyed()) {
        try { managedYouTubeWindow.close(); } catch (_error) {}
    }
    managedYouTubeWindow = null;
}

async function getYouTubeSessionCookies(partitionName) {
    const ses = session.fromPartition(partitionName, { cache: true });
    const allCookies = await ses.cookies.get({});
    return relevantManagedCookies(allCookies);
}

async function readYouTubeSessionStatus() {
    const { response, payload } = await fetchJsonWithTimeout(
        `${BACKEND_URL}/api/youtube/session/status`,
        { method: 'GET' },
        5000
    );
    if (!response.ok) throw new Error(payload?.error || 'Could not read YouTube connection status.');
    return payload?.session || {};
}

async function connectTrustedYouTubeBrowser(browserValue) {
    const browser = String(browserValue || 'chrome').toLowerCase() === 'edge' ? 'edge' : 'chrome';
    if (!backendReady) throw new Error('Vidzeo is still starting. Try again in a moment.');

    // Use a persistent partition so the user stays signed in across app restarts.
    // persist: prefix makes Electron store it on disk under userData.
    const partitionName = `persist:youtube-signin`;
    emitManagedYouTubeStatus(
        'opening',
        `Opening YouTube sign-in — use this secure window to connect your Google account.`,
        { browser }
    );

    // Close any existing sign-in window
    closeYouTubeSignInWindow();

    const ytWindow = new BrowserWindow({
        width: 1100,
        height: 750,
        title: 'Vidzeo — Sign in to YouTube',
        autoHideMenuBar: true,
        webPreferences: {
            partition: partitionName,
            nodeIntegration: false,
            contextIsolation: true,
            sandbox: true,
            // No preload: use the same plain sign-in window as Vidzeo App.
        },
    });
    managedYouTubeWindow = ytWindow;

    // Navigate directly to Google sign-in for YouTube
    await ytWindow.loadURL('https://accounts.google.com/ServiceLogin?service=youtube&continue=https%3A%2F%2Fwww.youtube.com%2Ffeed%2Fyou&hl=en');

    emitManagedYouTubeStatus(
        'waiting',
        'Sign in to your Google account in this window. Vidzeo will detect and save your session automatically.',
        { browser }
    );

    const deadline = Date.now() + YOUTUBE_CONNECT_TIMEOUT_MS;
    let lastImportError = '';
    let lastAttemptSignature = '';
    let windowClosed = false;

    ytWindow.on('closed', () => {
        windowClosed = true;
        managedYouTubeWindow = null;
    });

    try {
        while (Date.now() < deadline) {
            if (windowClosed) {
                throw new Error('The YouTube sign-in window was closed before the account was connected.');
            }

            let cookies = [];
            try {
                cookies = await getYouTubeSessionCookies(partitionName);
            } catch (_error) {
                await delay(1000);
                continue;
            }

            const authCookies = cookies.filter((cookie) =>
                YOUTUBE_AUTH_COOKIE_NAMES.has(String(cookie?.name || '').toUpperCase())
            );

            if (authCookies.length >= 2) {
                emitManagedYouTubeStatus('verifying', 'YouTube account detected. Verifying the session...', {
                    browser, cookieCount: cookies.length,
                });

                const signature = crypto.createHash('sha256').update(JSON.stringify(
                    cookies.map((c) => [c.name, c.domain, c.expirationDate, c.value])
                )).digest('hex');

                if (signature !== lastAttemptSignature) {
                    lastAttemptSignature = signature;
                    try {
                        const payload = await importManagedYouTubeCookies(browser, cookies);
                        emitManagedYouTubeStatus('connected', 'YouTube connected, verified, and saved securely!', {
                            browser,
                            cookieCount: payload?.session?.cookie_count || cookies.length,
                        });
                        closeYouTubeSignInWindow();
                        return payload;
                    } catch (error) {
                        lastImportError = error.message || 'YouTube has not confirmed the account yet.';
                        // Auth cookies appeared but verification failed — user may
                        // still be mid-redirect. Wait and retry.
                    }
                }
            }

            await delay(800);
        }
        throw new Error(lastImportError || 'YouTube sign-in timed out. Click Connect YouTube and finish account selection.');
    } catch (error) {
        emitManagedYouTubeStatus('error', error.message || 'YouTube connection failed.', { browser });
        closeYouTubeSignInWindow();
        throw error;
    }
}


function firstExistingPath(paths) {
    return paths.find((candidate) => candidate && fs.existsSync(candidate)) || '';
}

function getToolPath(...parts) {
    return path.join(appRoot, 'tools', ...parts);
}

function resolveToolDirectory(toolName) {
    const packagedToolPath = getResourcePath('tools', toolName);
    return firstExistingPath(app.isPackaged
        ? [packagedToolPath, getToolPath(toolName), path.join(__dirname, 'tools', toolName)]
        : [getToolPath(toolName), packagedToolPath, path.join(__dirname, 'tools', toolName)]);
}

function resolvePythonExecutable() {
    const packagedPythonPaths = [
        getResourcePath('tools', 'python', 'python.exe'),
        getResourcePath('tools', 'python', 'Scripts', 'python.exe')
    ];
    const userDataPythonPaths = [
        getToolPath('python', 'python.exe'),
        getToolPath('python', 'Scripts', 'python.exe')
    ];
    const developmentPythonPaths = [
        path.join(__dirname, 'tools', 'python', 'python.exe'),
        path.join(__dirname, 'tools', 'python', 'Scripts', 'python.exe')
    ];
    return firstExistingPath(app.isPackaged
        ? [...packagedPythonPaths, ...userDataPythonPaths, ...developmentPythonPaths]
        : [...userDataPythonPaths, ...packagedPythonPaths, ...developmentPythonPaths]);
}

function resolveBundledNodeExecutable() {
    const nodeDirectory = resolveToolDirectory('node');
    const bundledNode = nodeDirectory ? path.join(nodeDirectory, 'node.exe') : '';
    if (bundledNode && fs.existsSync(bundledNode)) return bundledNode;
    if (app.isPackaged) return '';
    return '';
}

function resolveFfmpegDirectory() {
    const ffmpegDir = resolveToolDirectory('ffmpeg');
    if (ffmpegDir && fs.existsSync(path.join(ffmpegDir, 'ffmpeg.exe'))) {
        return ffmpegDir;
    }
    return '';
}

function runtimeStampIsCurrent() {
    // Accept any deps-vN marker. Hardcoding one version here would make an
    // upgraded bootstrap (e.g. deps-v3) trigger a full re-install on every
    // launch because the stamp would never match again. In packaged builds
    // the writable appRoot is separate from read-only resources, so also
    // trust the validated marker shipped beside the bundled runtime.
    // Packaged builds must validate the marker beside the runtime they will
    // actually launch. A stale user-data marker must not bless an incomplete
    // older Python copy or suppress repair of the bundled runtime.
    const stampPaths = app.isPackaged
        ? [getResourcePath('tools', '.runtime-ready')]
        : [
            path.join(appRoot, 'tools', '.runtime-ready'),
            getResourcePath('tools', '.runtime-ready'),
            path.join(__dirname, 'tools', '.runtime-ready')
        ];
    return stampPaths.some((stampPath) => {
        try {
            return fs.existsSync(stampPath) && /deps-v\d+/.test(fs.readFileSync(stampPath, 'utf8'));
        } catch (_error) {
            return false;
        }
    });
}

function resolvePowerShellExecutable() {
    const systemRoot = process.env.SystemRoot || process.env.windir || 'C:\\Windows';
    return firstExistingPath([
        path.join(systemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'),
        path.join(systemRoot, 'SysWOW64', 'WindowsPowerShell', 'v1.0', 'powershell.exe'),
        'powershell.exe'
    ]) || 'powershell.exe';
}

// A stale per-user runtime can contain python.exe and a readiness marker while
// its site-packages directory is incomplete (the most visible symptom is
// ``Flask not installed`` on every automatic backend restart).  Existence
// checks alone therefore cannot decide whether the backend is runnable.  Keep
// this probe small and deterministic; the full dependency validation remains
// in bootstrap-runtime.ps1 when the probe fails.
async function runtimePythonImportsAreReady(pythonExe) {
    if (!pythonExe || !fs.existsSync(pythonExe)) return false;
    // Validate importable essentials without loading several inference runtimes
    // into a second Python process on every launch. The release build validates
    // those native dependencies separately; feature workers load them on demand.
    const probe = [
        'import importlib, importlib.util',
        "essential=('flask','flask_cors','requests','PIL','edge_tts')",
        "optional=('numpy','soundfile','rembg','faster_whisper','onnxruntime','cv2','rapidocr_onnxruntime','pyclipper','shapely')",
        'missing=[]',
        'for name in essential:',
        '    try: importlib.import_module(name)',
        '    except Exception as exc: missing.append(f"{name}: {exc}")',
        'for name in optional:',
        '    if importlib.util.find_spec(name) is None: missing.append(name)',
        'if missing:',
        '    print("; ".join(missing), flush=True)',
        '    raise SystemExit(1)',
    ].join('\n');
    return new Promise(resolve => {
        execFile(pythonExe, ['-I', '-c', probe], {
            windowsHide:true, timeout:20000, encoding:'utf8', maxBuffer:1024*1024,
            env:{...process.env,PYTHONNOUSERSITE:'1',PYTHONUNBUFFERED:'1'}
        }, (error, stdout, stderr) => {
            if (error) recordRuntimeLog('WARN', 'Python readiness check did not pass; checking runtime installation.',
                String(stderr || stdout || error.message).replace(/\s+/g,' ').slice(0,500));
            resolve(!error);
        });
    });
}

async function ensureRuntimeDependencies() {
    const pythonExecutable = resolvePythonExecutable();
    const pythonReady = await runtimePythonImportsAreReady(pythonExecutable);
    const ffmpegReady = Boolean(resolveFfmpegDirectory());
    const runtimeReady = runtimeStampIsCurrent();
    if (pythonReady && ffmpegReady && runtimeReady) {
        return null;
    }

    const bootstrapScript = firstExistingPath([
        getResourcePath('scripts', 'bootstrap-runtime.ps1'),
        path.join(__dirname, 'scripts', 'bootstrap-runtime.ps1')
    ]);

    if (!bootstrapScript) {
        console.warn('[Electron] Runtime bootstrap script is missing; falling back to system PATH.');
        return null;
    }

    broadcastBackendState({
        phase: 'starting',
        ready: false,
        message: runtimeReady
            ? 'Checking Vidnets runtime dependencies...'
            : 'Installing first-run runtime dependencies. This can take several minutes...',
        error: '',
        code: null
    });

    return new Promise((resolve, reject) => {
        const proc = spawn(
            resolvePowerShellExecutable(),
            [
                '-NoProfile',
                '-ExecutionPolicy', 'Bypass',
                '-File', bootstrapScript,
                '-InstallRoot', appRoot,
                '-SourceRoot', app.isPackaged ? process.resourcesPath : __dirname
            ],
            {
                cwd: app.isPackaged ? process.resourcesPath : __dirname,
                windowsHide: true,
                stdio: ['ignore', 'pipe', 'pipe'],
                timeout: 1000 * 60 * 45
            }
        );

        let stdoutBuf = '';
        let stderrBuf = '';

        proc.stdout.on('data', (data) => {
            const text = data.toString();
            stdoutBuf += text;
            text.split('\n').filter(Boolean).forEach((line) => {
                const cleanLine = normalizeBackendLogLine(line);
                if (!cleanLine) return;
                console.log(`[Runtime] ${cleanLine}`);
                broadcastBackendState({
                    phase: 'starting',
                    ready: false,
                    message: cleanLine.replace(/^\[(?:Vidnets|Vidzeo) Runtime\] /, ''),
                    error: '',
                    code: null
                });
                if (mainWindow && !mainWindow.isDestroyed()) {
                    mainWindow.webContents.send('backend-log', cleanLine);
                }
            });
        });

        proc.stderr.on('data', (data) => {
            const text = data.toString();
            stderrBuf += text;
            text.split('\n').filter(Boolean).forEach((line) => {
                console.warn(`[Runtime] ${line.trim()}`);
            });
        });

        proc.on('error', (err) => {
            reject(new Error(err.message || 'Runtime bootstrap process failed'));
        });

        proc.on('close', (code) => {
            if (code !== 0) {
                const detail = stderrBuf.trim() || stdoutBuf.trim() || `exit code ${code}`;
                reject(new Error(`Runtime bootstrap failed: ${detail}`));
            } else {
                resolve();
            }
        });
    });
}

const SECURE_STORE_DIR = app.getPath('userData');
const AUTH_SESSION_PATH = path.join(SECURE_STORE_DIR, 'auth-session.enc.json');
const LEGACY_AUTH_SESSION_PATH = path.join(appRoot, 'auth-session.json');
const AUTH_SESSION_MIRROR_PATH = path.join(appRoot, 'auth-session.enc.json');
const SECRET_SETTINGS_PATH = path.join(SECURE_STORE_DIR, 'secret-settings.enc.json');
const AUTH_REFRESH_BUFFER_SECONDS = 120;
const ephemeralProtectedStores = new Map();

function encodeProtectedJson(payload) {
    const text = JSON.stringify(payload || {});
    try {
        if (safeStorage.isEncryptionAvailable()) {
            return {
                version: 1,
                protected: true,
                data: safeStorage.encryptString(text).toString('base64')
            };
        }
    } catch (error) {
        console.warn('[Electron] Secure auth encryption unavailable; credentials will remain in memory for this session.');
    }
    const sessionKey = crypto.randomBytes(24).toString('hex');
    ephemeralProtectedStores.set(sessionKey, payload || {});
    return { version: 2, protected: true, ephemeral: true, sessionKey };
}

function decodeProtectedJson(container) {
    if (!container || typeof container !== 'object') {
        return {};
    }
    if (container.ephemeral) return ephemeralProtectedStores.get(container.sessionKey) || {};
    if (container.protected && container.data) {
        if (!app.isReady()) {
            return {};
        }
        try {
            const decrypted = safeStorage.decryptString(Buffer.from(container.data, 'base64'));
            return JSON.parse(decrypted || '{}');
        } catch (error) {
            console.warn('[Electron] Failed to decrypt protected desktop store:', error.message);
            return {};
        }
    }
    return container.data || {};
}

function readProtectedJson(filePath) {
    const parsed = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    if (parsed && Object.prototype.hasOwnProperty.call(parsed, 'protected')) {
        const value = decodeProtectedJson(parsed);
        if (parsed.protected === false) writeProtectedJson(filePath, value);
        return value;
    }
    writeProtectedJson(filePath, parsed || {});
    return parsed || {};
}

function writeProtectedJson(filePath, payload) {
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    let previousSessionKey;
    try { previousSessionKey = JSON.parse(fs.readFileSync(filePath, 'utf8')).sessionKey; } catch (_error) {}
    const encoded = encodeProtectedJson(payload);
    const temporary = `${filePath}.${crypto.randomBytes(6).toString('hex')}.part`;
    try {
        fs.writeFileSync(temporary, JSON.stringify(encoded, null, 2), { encoding: 'utf8', mode: 0o600 });
        fs.renameSync(temporary, filePath);
        if (previousSessionKey) ephemeralProtectedStores.delete(previousSessionKey);
    } finally {
        try { fs.unlinkSync(temporary); } catch (_error) {}
    }
}

function writeFallbackJson(filePath, payload) {
    writeProtectedJson(filePath, payload);
}

function sha256Hex(value) {
    return crypto.createHash('sha256').update(String(value || ''), 'utf8').digest('hex');
}

function readWindowsMachineGuid() {
    if (process.platform !== 'win32') {
        return '';
    }
    try {
        const output = execSync(
            'reg query "HKLM\\SOFTWARE\\Microsoft\\Cryptography" /v MachineGuid',
            { windowsHide: true, encoding: 'utf8', timeout: 2500 }
        );
        const match = output.match(/MachineGuid\s+REG_SZ\s+([^\r\n]+)/i);
        return match ? match[1].trim() : '';
    } catch (_error) {
        return '';
    }
}

function getDeviceFingerprint() {
    const cpuModel = os.cpus()?.[0]?.model || '';
    const raw = [
        'vidzeo-studio-device-v1',
        process.platform,
        os.arch(),
        os.hostname(),
        readWindowsMachineGuid(),
        cpuModel,
        Math.round(os.totalmem() / (1024 * 1024 * 512))
    ].join('|');
    return sha256Hex(raw);
}

function getDeviceInfo() {
    return {
        platform: process.platform,
        arch: os.arch(),
        hostname_hash: sha256Hex(os.hostname()).slice(0, 16),
        cpu_model_hash: sha256Hex(os.cpus()?.[0]?.model || '').slice(0, 16),
        total_memory_mb: Math.round(os.totalmem() / (1024 * 1024)),
        app_version: app.getVersion(),
        packaged: app.isPackaged
    };
}

const deviceFingerprint = getDeviceFingerprint();
const encodedDeviceInfo = encodeURIComponent(JSON.stringify(getDeviceInfo()));

function withDeviceHeaders(headers = {}) {
    return {
        ...headers,
        'X-Vidzeo-Device-Fingerprint': deviceFingerprint,
        'X-Vidzeo-Device-Info': encodedDeviceInfo
    };
}

function readLocalEnv() {
    // A distributed build must use its embedded public Supabase config. Do
    // not let an unrelated `.env` in the user's working directory (or an old
    // developer session under VidzeoData) silently override Google auth.
    // Developers can still opt in while debugging a packaged build.
    const allowPrivateEnvInPackaged = String(
        process.env.VIDZEO_ALLOW_PACKAGED_PRIVATE_ENV || ''
    ).trim().toLowerCase() === '1';
    const shouldReadPrivateEnv = !app.isPackaged || allowPrivateEnvInPackaged;
    const baseDirs = [
        __dirname,
        process.resourcesPath || '',
        app.isPackaged ? path.dirname(process.execPath) : '',
        appRoot,
        process.cwd()
    ].filter(Boolean);
    const candidatePaths = [];
    for (const baseDir of baseDirs) {
        candidatePaths.push(path.join(baseDir, '.env.public'));
        // Non-secret runtime tuning is shipped with the app. Private .env is
        // still preferred when a local developer explicitly supplies one.
        candidatePaths.push(path.join(baseDir, '.env.defaults'));
        if (shouldReadPrivateEnv) {
            candidatePaths.push(path.join(baseDir, '.env'));
        }
    }

    const entries = {};

    for (const envPath of candidatePaths) {
        if (!fs.existsSync(envPath)) {
            continue;
        }

        const lines = fs.readFileSync(envPath, 'utf8').split(/\r?\n/);
        for (const line of lines) {
            if (!line || line.trim().startsWith('#')) continue;
            const index = line.indexOf('=');
            if (index <= 0) continue;
            const key = line.slice(0, index).trim();
            let value = line.slice(index + 1).trim();
            if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
                value = value.slice(1, -1);
            }
            entries[key] = value;
        }
    }

    return entries;
}

const localEnv = readLocalEnv();
const requestedAuthMode = String(process.env.VIDZEO_AUTH_MODE || localEnv.VIDZEO_AUTH_MODE || 'local').trim().toLowerCase();
const publicSupabaseUrl = (process.env.SUPABASE_URL || localEnv.SUPABASE_URL || '').trim().replace(/\/$/, '');
const publicSupabaseAnonKey = (process.env.SUPABASE_ANON_KEY || localEnv.SUPABASE_ANON_KEY || '').trim();
const publicGoogleClientId = (process.env.GOOGLE_CLIENT_ID || localEnv.GOOGLE_CLIENT_ID || '').trim();
const allowPackagedLocalAuth = String(process.env.VIDZEO_ALLOW_PACKAGED_LOCAL_AUTH || localEnv.VIDZEO_ALLOW_PACKAGED_LOCAL_AUTH || '1').trim().toLowerCase() === '1';
const isLocalDistributionMode = ['local', 'offline', 'byok'].includes(requestedAuthMode) || !requestedAuthMode;
if (app.isPackaged && !isLocalDistributionMode && (!publicSupabaseUrl || !publicSupabaseAnonKey || !publicGoogleClientId)) {
    console.error('[Electron] Packaged distribution is missing Supabase public auth config. Google sign-in is required for distribution builds.');
}
const authConfig = {
    enabled: !isLocalDistributionMode && Boolean(publicSupabaseUrl && publicSupabaseAnonKey && publicGoogleClientId),
    mode: isLocalDistributionMode ? 'local' : 'supabase',
    supabaseUrl: isLocalDistributionMode ? '' : publicSupabaseUrl,
    supabaseAnonKey: isLocalDistributionMode ? '' : publicSupabaseAnonKey,
    googleClientId: isLocalDistributionMode ? '' : publicGoogleClientId
};

function getDefaultAuthState() {
    if (isLocalDistributionMode) {
        return {
            signed_in: true,
            signedIn: true,
            provider: 'local',
            user_id: 'local-user',
            auth_user_id: 'local-user',
            email: '',
            name: 'Local Creator',
            avatar_url: '',
        plan_key: 'local_byok',
        plan_name: 'Local BYOK',
        plan_status: 'active',
        pending_plan_key: null,
        pending_plan_name: null,
        access_state: 'active',
        access_copy: 'Local BYOK mode is active. Add your own API keys in Settings.',
        account_status: 'active',
        access_updated_at: '',
            billing_period: null,
            max_video_minutes: null,
            max_script_words: null,
            price_monthly_display: null,
            device_fingerprint: deviceFingerprint,
            access_token: 'local-bypass',
            refresh_token: '',
            expires_at: 0
        };
    }
    return {
        signed_in: false,
        signedIn: false,
        provider: '',
        user_id: '',
        auth_user_id: '',
        email: '',
        name: 'Guest Creator',
        avatar_url: '',
        plan_key: null,
        plan_name: 'No active plan',
        plan_status: 'pending',
        pending_plan_key: null,
        pending_plan_name: null,
        access_state: 'pending',
        access_copy: 'Sign in with Google to continue.',
        account_status: 'pending',
        access_updated_at: '',
        billing_period: null,
        max_video_minutes: null,
        max_script_words: null,
        price_monthly_display: null,
        device_fingerprint: deviceFingerprint,
        access_token: '',
        refresh_token: '',
        expires_at: 0
    };
}

function normalizeAuthState(next = {}) {
    const aliases = next || {};
    const hasPendingPlanKey = Object.prototype.hasOwnProperty.call(aliases, 'pending_plan_key')
        || Object.prototype.hasOwnProperty.call(aliases, 'requested_plan_key')
        || Object.prototype.hasOwnProperty.call(aliases, 'pendingPlanKey');
    const hasPendingPlanName = Object.prototype.hasOwnProperty.call(aliases, 'pending_plan_name')
        || Object.prototype.hasOwnProperty.call(aliases, 'requested_plan_name')
        || Object.prototype.hasOwnProperty.call(aliases, 'pendingPlanName');
    const merged = {
        ...getDefaultAuthState(),
        ...aliases,
        user_id: aliases.user_id || aliases.userId || aliases.userID || '',
        auth_user_id: aliases.auth_user_id || aliases.authUserId || aliases.authUserID || '',
        access_token: aliases.access_token || aliases.accessToken || '',
        refresh_token: aliases.refresh_token || aliases.refreshToken || '',
        expires_at: aliases.expires_at || aliases.expiresAt || 0,
    };
    const hasToken = Boolean(merged.access_token);
    merged.signed_in = Boolean((merged.signed_in || merged.signedIn || hasToken) && hasToken);
    merged.signedIn = merged.signed_in;
    merged.userId = merged.user_id;
    merged.authUserId = merged.auth_user_id;
    merged.accessToken = merged.access_token;
    merged.refreshToken = merged.refresh_token;
    merged.expiresAt = merged.expires_at;
    merged.pending_plan_key = hasPendingPlanKey
        ? (aliases.pending_plan_key ?? aliases.requested_plan_key ?? aliases.pendingPlanKey ?? null)
        : (merged.pending_plan_key || null);
    merged.pending_plan_name = hasPendingPlanName
        ? (aliases.pending_plan_name || aliases.requested_plan_name || aliases.pendingPlanName || null)
        : (merged.pending_plan_name || null);
    merged.access_updated_at = aliases.access_updated_at || aliases.accessUpdatedAt || merged.access_updated_at || '';
    merged.pendingPlanKey = merged.pending_plan_key;
    merged.pendingPlanName = merged.pending_plan_name;
    merged.requested_plan_key = merged.pending_plan_key;
    merged.requested_plan_name = merged.pending_plan_name;
    merged.accessUpdatedAt = merged.access_updated_at;
    merged.name = merged.name || merged.email || 'Vidzeo Creator';
    merged.device_fingerprint = merged.device_fingerprint || deviceFingerprint;
    return merged;
}

function isLocalBypassAuthState(state = {}) {
    const provider = String(state.provider || '').trim().toLowerCase();
    const accessToken = String(state.access_token || state.accessToken || '').trim();
    const planKey = String(state.plan_key || state.planKey || '').trim().toLowerCase();
    return provider === 'local' || accessToken === 'local-bypass' || planKey === 'local_byok';
}

function isReusableAuthState(state = {}) {
    if (isLocalDistributionMode) {
        return true;
    }
    return Boolean(state?.signed_in && state?.access_token && !isLocalBypassAuthState(state));
}

function removeAuthSessionFile(sessionPath) {
    try {
        if (sessionPath && fs.existsSync(sessionPath)) {
            fs.unlinkSync(sessionPath);
        }
    } catch (error) {
        console.warn('[Electron] Could not remove stale auth session:', error.message);
    }
}

function loadStoredAuthState() {
    if (isLocalDistributionMode) {
        return getDefaultAuthState();
    }
    try {
        const sessionCandidates = [
            AUTH_SESSION_MIRROR_PATH,
            LEGACY_AUTH_SESSION_PATH,
            AUTH_SESSION_PATH,
        ];
        for (const sessionPath of sessionCandidates) {
            if (!sessionPath || !fs.existsSync(sessionPath)) {
                continue;
            }
            const parsed = readProtectedJson(sessionPath);
            const normalized = normalizeAuthState(parsed);
            if (!isReusableAuthState(normalized)) {
                if (isLocalBypassAuthState(normalized)) {
                    console.warn(`[Electron] Removed stale local/BYOK auth session from ${path.basename(sessionPath)}.`);
                    removeAuthSessionFile(sessionPath);
                }
                continue;
            }
            try {
                writeProtectedJson(AUTH_SESSION_PATH, normalized);
                writeFallbackJson(AUTH_SESSION_MIRROR_PATH, normalized);
            } catch (error) {
                console.warn('[Electron] Loaded auth session but could not refresh mirrored stores:', error.message);
            }
            console.log(`[Electron] Restored desktop auth session from ${path.basename(sessionPath)}.`);
            lastAuthPersistFingerprint = authStateFingerprint(normalized);
            return normalized;
        }
        console.log('[Electron] No reusable desktop auth session found.');
        return getDefaultAuthState();
    } catch (error) {
        console.error('[Electron] Failed to load stored auth session:', error);
        return getDefaultAuthState();
    }
}

function authStateFingerprint(nextState) {
    return sha256Hex(JSON.stringify(normalizeAuthState(nextState)));
}

function persistAuthState(nextState, options = {}) {
    authState = normalizeAuthState(nextState);
    authRequested = authState.signed_in;
    const fingerprint = authStateFingerprint(authState);
    const changed = fingerprint !== lastAuthPersistFingerprint;
    if (!changed && !options.force) {
        return authState;
    }
    try {
        writeProtectedJson(AUTH_SESSION_PATH, authState);
        writeFallbackJson(AUTH_SESSION_MIRROR_PATH, authState);
        lastAuthPersistFingerprint = fingerprint;
        const now = Date.now();
        if (options.force || now - lastAuthPersistLogAt > 60000) {
            console.log(`[Electron] Auth session saved (${authState.signed_in ? 'signed in' : 'signed out'}).`);
            lastAuthPersistLogAt = now;
        }
        if (changed && mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send('auth-state-changed', { ...authState, backendReady });
        }
    } catch (error) {
        console.error('[Electron] Failed to persist auth session:', error);
    }
    return authState;
}

function clearStoredAuthState() {
    authState = getDefaultAuthState();
    authRequested = isLocalDistributionMode;
    lastAuthPersistFingerprint = '';
    lastAuthPersistLogAt = 0;
    try {
        if (fs.existsSync(AUTH_SESSION_PATH)) {
            fs.unlinkSync(AUTH_SESSION_PATH);
        }
        if (fs.existsSync(AUTH_SESSION_MIRROR_PATH)) {
            fs.unlinkSync(AUTH_SESSION_MIRROR_PATH);
        }
        if (fs.existsSync(LEGACY_AUTH_SESSION_PATH)) {
            fs.unlinkSync(LEGACY_AUTH_SESSION_PATH);
        }
    } catch (error) {
        console.error('[Electron] Failed to clear auth session:', error);
    }
}

function markAuthSessionNeedsSignin(reason = '') {
    const previousEmail = authState?.email || '';
    clearStoredAuthState();
    const message = reason || 'Your saved sign-in expired. Please sign in with Google again.';
    authState = normalizeAuthState({
        ...getDefaultAuthState(),
        email: previousEmail,
        access_copy: message,
    });
    authRequested = false;
    lastAuthBackendRejectedAt = Date.now();
    lastAuthRefreshAttemptAt = Date.now();
    lastAuthSyncWarningAt = 0;
    lastAuthSyncWarningMessage = '';
    lastAuthRefreshWarningAt = 0;
    lastAuthRefreshWarningMessage = '';
    console.warn('[Electron] Auth session reset:', message);
    if (mainWindow && !mainWindow.isDestroyed() && !appLoaded) {
        loadLandingPage();
    }
    return authState;
}

function firstEnvValue(names) {
    for (const name of names) {
        const value = (process.env[name] || localEnv[name] || '').trim();
        if (value) {
            return value;
        }
    }
    return '';
}


function compactObject(obj = {}) {
    return Object.fromEntries(
        Object.entries(obj).filter(([, value]) => value !== undefined && value !== null && String(value).trim() !== '')
    );
}

function getEnvSecretSettings() {
    const apiKeys = compactObject({
        pollinations: firstEnvValue(['POLLINATIONS_API_KEY', 'POLLINATIONS_TOKEN', 'VIDZEO_POLLINATIONS_API_KEY']),
        pexels: firstEnvValue(['PEXELS_API_KEY', 'VIDZEO_PEXELS_API_KEY']),
        pixabay: firstEnvValue(['PIXABAY_API_KEY', 'VIDZEO_PIXABAY_API_KEY']),
        together: firstEnvValue(['TOGETHER_API_KEY', 'VIDZEO_TOGETHER_API_KEY']),
        openai: firstEnvValue(['OPENAI_API_KEY', 'VIDZEO_OPENAI_API_KEY']),
        openrouter: firstEnvValue(['OPENROUTER_API_KEY', 'VIDZEO_OPENROUTER_API_KEY']),
    });
    return compactObject({
        apiKeys,
        pollinationsApiKey: apiKeys.pollinations,
        pexelsApiKey: apiKeys.pexels,
        pixabayApiKey: apiKeys.pixabay,
        togetherApiKey: apiKeys.together,
        openaiApiKey: apiKeys.openai,
        openrouterApiKey: apiKeys.openrouter,
        llmProvider: 'openrouter',
        llmModel: 'qwen/qwen3.5-flash-02-23',
        generationCostProfile: 'efficient',
    });
}

function mergeSecretSettings(envSettings = {}, savedSettings = {}) {
    const savedApiKeys = savedSettings.apiKeys || {};
    const envApiKeys = envSettings.apiKeys || {};
    const apiKeys = compactObject({
        pollinations: savedApiKeys.pollinations || savedSettings.pollinationsApiKey || envApiKeys.pollinations,
        pexels: savedApiKeys.pexels || savedSettings.pexelsApiKey || envApiKeys.pexels,
        pixabay: savedApiKeys.pixabay || savedSettings.pixabayApiKey || envApiKeys.pixabay,
        together: savedApiKeys.together || savedSettings.togetherApiKey || envApiKeys.together,
        openai: savedApiKeys.openai || savedSettings.openaiApiKey || envApiKeys.openai,
        openrouter: savedApiKeys.openrouter || savedSettings.openrouterApiKey || envApiKeys.openrouter,
    });
    const costProfile = savedSettings.generationCostProfile === 'custom' ? 'custom' : 'efficient';
    let rawModel = costProfile === 'custom' ? (savedSettings.llmModel || envSettings.llmModel) : 'qwen/qwen3.5-flash-02-23';
    if (!rawModel || rawModel === 'llama-3.3-70b-versatile' || rawModel === 'llama-3.1-8b-instant') {
        rawModel = 'qwen/qwen3.5-flash-02-23';
    }
    return {
        apiKeys,
        pollinationsApiKey: savedSettings.pollinationsApiKey || apiKeys.pollinations || '',
        pexelsApiKey: savedSettings.pexelsApiKey || apiKeys.pexels || '',
        pixabayApiKey: savedSettings.pixabayApiKey || apiKeys.pixabay || '',
        togetherApiKey: savedSettings.togetherApiKey || apiKeys.together || '',
        openaiApiKey: savedSettings.openaiApiKey || apiKeys.openai || '',
        openrouterApiKey: savedSettings.openrouterApiKey || apiKeys.openrouter || '',
        llmProvider: 'openrouter',
        llmModel: rawModel,
        generationCostProfile: costProfile,
    };
}

function loadSecretSettings() {
    const envSettings = getEnvSecretSettings();
    try {
        if (!fs.existsSync(SECRET_SETTINGS_PATH)) {
            return envSettings;
        }
        return mergeSecretSettings(envSettings, readProtectedJson(SECRET_SETTINGS_PATH));
    } catch (error) {
        console.error('[Electron] Failed to load secret settings:', error);
        return envSettings;
    }
}

function persistSecretSettings(settings = {}) {
    const incomingApiKeys = settings.apiKeys || {};
    const apiKeys = compactObject({
        pollinations: incomingApiKeys.pollinations || settings.pollinationsApiKey || '',
        pexels: incomingApiKeys.pexels || settings.pexelsApiKey || '',
        pixabay: incomingApiKeys.pixabay || settings.pixabayApiKey || '',
        together: incomingApiKeys.together || settings.togetherApiKey || '',
        openai: incomingApiKeys.openai || settings.openaiApiKey || '',
        openrouter: incomingApiKeys.openrouter || settings.openrouterApiKey || '',
    });
    const costProfile = settings.generationCostProfile === 'custom' ? 'custom' : 'efficient';
    let rawModel = costProfile === 'custom' ? settings.llmModel : 'qwen/qwen3.5-flash-02-23';
    if (!rawModel || rawModel === 'llama-3.3-70b-versatile' || rawModel === 'llama-3.1-8b-instant') {
        rawModel = 'qwen/qwen3.5-flash-02-23';
    }
    const allowed = {
        apiKeys,
        llmProvider: 'openrouter',
        llmModel: rawModel,
        generationCostProfile: costProfile
    };
    writeProtectedJson(SECRET_SETTINGS_PATH, allowed);
    return mergeSecretSettings(getEnvSecretSettings(), allowed);
}


let authState = getDefaultAuthState();
authRequested = isLocalDistributionMode;

function hasVerifiedAuthState(state = authState) {
    return isReusableAuthState(state);
}

function mapSessionPayloadToAuthState(payload = {}) {
    const session = payload.session || payload.authSession || {};
    const user = payload.user || session.user || {};
    const metadata = user.user_metadata || {};
    const hasPendingPlanKey = Object.prototype.hasOwnProperty.call(payload, 'pending_plan_key')
        || Object.prototype.hasOwnProperty.call(payload, 'requested_plan_key');
    const hasPendingPlanName = Object.prototype.hasOwnProperty.call(payload, 'pending_plan_name')
        || Object.prototype.hasOwnProperty.call(payload, 'requested_plan_name');
    return normalizeAuthState({
        signed_in: true,
        provider: 'google',
        user_id: payload.user_id || payload.userId || user.id || authState.user_id,
        auth_user_id: user.id || payload.auth_user_id || payload.authUserId || payload.user_id || authState.auth_user_id,
        email: user.email || payload.email || authState.email,
        name: user.name || user.full_name || metadata.full_name || metadata.name || payload.name || authState.name,
        avatar_url: user.avatar_url || metadata.avatar_url || metadata.picture || payload.avatar_url || authState.avatar_url,
        access_token: payload.accessToken || payload.access_token || session.access_token || authState.access_token,
        refresh_token: payload.refreshToken || payload.refresh_token || session.refresh_token || authState.refresh_token,
        expires_at: Number(payload.expiresAt || payload.expires_at || session.expires_at || 0),
        plan_key: payload.plan_key || authState.plan_key,
        plan_name: payload.plan_name || authState.plan_name,
        plan_status: payload.plan_status || authState.plan_status,
        pending_plan_key: hasPendingPlanKey ? (payload.pending_plan_key ?? payload.requested_plan_key ?? null) : authState.pending_plan_key,
        pending_plan_name: hasPendingPlanName ? (payload.pending_plan_name || payload.requested_plan_name || null) : authState.pending_plan_name,
        access_state: payload.access_state || authState.access_state,
        access_copy: payload.access_copy || authState.access_copy,
        account_status: payload.account_status || authState.account_status,
        billing_period: payload.billing_period || authState.billing_period,
        max_video_minutes: payload.max_video_minutes || authState.max_video_minutes,
        max_script_words: payload.max_script_words || authState.max_script_words,
        price_monthly_display: payload.price_monthly_display || authState.price_monthly_display,
        access_updated_at: payload.access_updated_at || payload.accessUpdatedAt || authState.access_updated_at,
        device_fingerprint: deviceFingerprint
    });
}

function broadcastBackendState(extra = {}) {
    const prevPhase = backendState.phase;
    Object.assign(backendState, extra);
    const phaseChanged = prevPhase !== backendState.phase;
    const isError = Boolean(backendState.error);

    // Only log high-level phase transitions or errors to the console,
    // avoiding duplicate logs for every stdout line forwarded from Python.
    if (phaseChanged || isError) {
        const logKey = `${backendState.phase}|${backendState.ready}|${backendState.error || backendState.message}`;
        if (lastLoggedBackendState !== logKey) {
            lastLoggedBackendState = logKey;
            const text = backendState.error || backendState.message || backendState.phase;
            if (isError) {
                console.error(`[Electron] Backend state: ${backendState.phase} - ${text}`);
            } else {
                console.log(`[Electron] Backend state: ${backendState.phase} - ${text}`);
            }
        }
    }

    if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('backend-state-changed', { ...backendState });
    }
}

function isVidzeoStatus(payload) {
    return Boolean(payload) && typeof payload === 'object' && Object.prototype.hasOwnProperty.call(payload, 'ready');
}

function isCurrentPipelineBackend(payload) {
    const expectedBuild = getPackagedBuildId();
    const sameResources = payload?.resource_root
        && path.resolve(payload.resource_root).toLowerCase() === path.resolve(getResourcePath()).toLowerCase();
    return String(payload?.pipeline_build_id || '').trim() === EXPECTED_PIPELINE_BUILD_ID
        && Boolean(sameResources)
        && (!expectedBuild || payload?.runtime_build?.release_build_id === expectedBuild)
        && payload?.runtime_build?.restart_required !== true;
}

function fetchBackendStatus(timeoutMs = 5000) {
    return new Promise((resolve, reject) => {
        const req = http.get(`${BACKEND_URL}/api/status`, (res) => {
            let body = '';
            res.setEncoding('utf8');
            res.on('data', (chunk) => {
                body += chunk;
            });
            res.on('end', () => {
                if (res.statusCode !== 200) {
                    reject(new Error(`HTTP ${res.statusCode}`));
                    return;
                }
                try {
                    resolve(JSON.parse(body));
                } catch (error) {
                    reject(error);
                }
            });
        });

        req.on('error', reject);
        req.setTimeout(timeoutMs, () => {
            req.destroy(new Error('Timed out'));
        });
    });
}

function canBindLocalPort(port) {
    return new Promise((resolve) => {
        const server = tcp.createServer();
        let settled = false;
        const finish = (available) => {
            if (settled) return;
            settled = true;
            try { server.close(); } catch (_error) {}
            resolve(Boolean(available));
        };
        server.once('error', () => finish(false));
        server.listen({ host: '127.0.0.1', port }, () => finish(true));
    });
}

async function selectBackendPort() {
    const configured = Number.parseInt(
        process.env.VIDZEO_PORT || localEnv.VIDZEO_PORT || String(DEFAULT_BACKEND_PORT),
        10,
    );
    const preferred = Number.isInteger(configured) && configured >= 1024 && configured <= 65535
        ? configured
        : DEFAULT_BACKEND_PORT;
    // Keep the fallback range narrow and predictable for firewall/security
    // rules while avoiding an unrelated service occupying the preferred port.
    for (let offset = 0; offset <= 20; offset += 1) {
        const candidate = preferred + offset;
        if (candidate > 65535) break;
        if (await canBindLocalPort(candidate)) {
            BACKEND_PORT = candidate;
            BACKEND_URL = `http://127.0.0.1:${BACKEND_PORT}`;
            console.log(`[Electron] Backend port selected: ${BACKEND_PORT}`);
            return BACKEND_PORT;
        }
    }
    throw new Error(`No available localhost port in ${preferred}-${preferred + 20}.`);
}

async function probeExistingBackend() {
    try {
        const status = await fetchBackendStatus(1500);
        if (!isVidzeoStatus(status)) return null;
        if (!isCurrentPipelineBackend(status)) {
            const actual = String(status?.pipeline_build_id || 'missing').trim();
            recordRuntimeLog(
                'WARN',
                'Ignoring stale backend build; starting a fresh media pipeline instance.',
                `expected=${EXPECTED_PIPELINE_BUILD_ID} actual=${actual}`,
            );
            return {
                ...status,
                ready: false,
                stale_backend: true,
                boot_stage: 'Backend build mismatch; starting a fresh instance.',
            };
        }
        return status;
    } catch (_error) {
        return null;
    }
}

async function fetchWithTimeout(url, options = {}, timeoutMs = AUTH_SYNC_FETCH_TIMEOUT_MS) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutMs);
    try {
        return await fetch(url, {
            ...options,
            signal: controller.signal
        });
    } finally {
        clearTimeout(timeout);
    }
}

async function syncAuthStateWithBackend(force = false) {
    if (!authState.access_token || !backendReady) {
        return authState;
    }

    const now = Date.now();
    if (lastAuthBackendRejectedAt && now - lastAuthBackendRejectedAt < AUTH_BACKEND_REJECT_RETRY_MS) {
        return authState;
    }
    // Exponential backoff after 429: 120s, 240s, 480s, capped at 600s.
    // Use the counter instead of recalculating from lastAuth429At
    // because lastAuth429At gets updated by each new 429 hit.
    if (lastAuth429At) {
        const baseSeconds = 120;  // 2 minutes base
        const maxSeconds = 600;   // 10 minutes cap
        const backoff = Math.min(
            maxSeconds * 1000,
            baseSeconds * 1000 * Math.pow(2, lastAuth429RetryCount)
        );
        if (now - lastAuth429At < backoff) {
            return authState;
        }
        lastAuth429At = 0;
    }
    if (!force && now - lastAuthSyncAttemptAt < AUTH_SYNC_INTERVAL_MS) {
        return authState;
    }
    lastAuthSyncAttemptAt = now;

    try {
        const response = await fetchWithTimeout(`${BACKEND_URL}/api/auth/session`, {
            method: 'POST',
            headers: withDeviceHeaders({
                Authorization: `Bearer ${authState.access_token}`
            })
        });

        if (!response.ok) {
            let message = `Backend auth sync failed (${response.status})`;
            try {
                const payload = await response.json();
                message = payload.error || payload.message || message;
            } catch (_error) {
                // Keep the status-based message if the backend returned no JSON body.
            }

            if (response.status === 401) {
                lastAuthBackendRejectedAt = now;
                warnAuthSyncOnce('Saved auth session needs refresh; Vidzeo is retrying automatically.');
                return authState;
            }

            if (response.status === 429) {
                lastAuth429At = now;
                lastAuth429RetryCount += 1;
                warnAuthSyncOnce('Supabase rate-limited; backing off auth checks.');
                return authState;
            }

            warnAuthSyncOnce(
                response.status === 503
                    ? 'Supabase auth is temporarily unreachable; keeping the desktop session until it reconnects.'
                    : message
            );
            return authState;
        }

        const payload = await response.json();
        lastAuthBackendRejectedAt = 0;
        lastAuthSyncWarningAt = 0;
        lastAuthSyncWarningMessage = '';
        lastAuth429At = 0;
        lastAuth429RetryCount = 0;
        persistAuthState({
            ...authState,
            ...payload
        });
    } catch (error) {
        warnAuthSyncOnce(error.message || 'Could not sync auth state with backend.');
    }

    return authState;
}

async function verifyAndPersistAuthPayload(payload = {}) {
    const candidate = mapSessionPayloadToAuthState(payload);
    if (!candidate.access_token) {
        clearStoredAuthState();
        throw new Error('Google sign-in did not return a Supabase access token.');
    }

    let response;
    try {
        response = await fetchWithTimeout(`${BACKEND_URL}/api/auth/session`, {
            method: 'POST',
            headers: withDeviceHeaders({
                Authorization: `Bearer ${candidate.access_token}`
            })
        });
    } catch (error) {
        persistAuthState(candidate);
        warnAuthSyncOnce(error.message || 'Supabase auth verification is temporarily unreachable; saved the desktop session and will retry.');
        return authState;
    }

    if (!response.ok) {
        let message = `Backend auth verification failed (${response.status})`;
        try {
            const errorPayload = await response.json();
            message = errorPayload.error || errorPayload.message || message;
        } catch (_error) {
            // Keep status fallback.
        }
        if (response.status === 503) {
            persistAuthState(candidate);
            warnAuthSyncOnce(message || 'Supabase auth is temporarily unreachable; saved the desktop session and will retry.');
            return authState;
        }
        clearStoredAuthState();
        throw new Error(message);
    }

    const verifiedState = await response.json();
    persistAuthState({
        ...candidate,
        ...verifiedState,
    });
    return authState;
}

function warnAuthSyncOnce(message) {
    const normalizedMessage = String(message || '');
    if (/aborted|aborterror/i.test(normalizedMessage)) {
        return;
    }
    const now = Date.now();
    if (normalizedMessage !== lastAuthSyncWarningMessage || now - lastAuthSyncWarningAt > AUTH_WARNING_INTERVAL_MS) {
        console.warn('[Electron] Auth sync paused:', normalizedMessage);
        lastAuthSyncWarningMessage = normalizedMessage;
        lastAuthSyncWarningAt = now;
    }
}

function warnAuthRefreshOnce(message) {
    const now = Date.now();
    if (message !== lastAuthRefreshWarningMessage || now - lastAuthRefreshWarningAt > AUTH_WARNING_INTERVAL_MS) {
        console.warn('[Electron] Auth refresh paused:', message);
        lastAuthRefreshWarningMessage = message;
        lastAuthRefreshWarningAt = now;
    }
}

async function refreshSupabaseSession(force = false) {
    if (!authConfig.enabled || !authState.refresh_token) {
        return authState;
    }

    const attemptNow = Date.now();
    if (!force && lastAuthRefreshAttemptAt && attemptNow - lastAuthRefreshAttemptAt < AUTH_REFRESH_RETRY_INTERVAL_MS) {
        return authState;
    }
    lastAuthRefreshAttemptAt = attemptNow;

    const now = Math.floor(Date.now() / 1000);
    if (!force && authState.expires_at && authState.expires_at > now + AUTH_REFRESH_BUFFER_SECONDS) {
        return authState;
    }

    try {
        const response = await fetch(`${authConfig.supabaseUrl}/auth/v1/token?grant_type=refresh_token`, {
            method: 'POST',
            headers: {
                apikey: authConfig.supabaseAnonKey,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                refresh_token: authState.refresh_token
            })
        });

        if (!response.ok) {
            if (response.status === 400 || response.status === 401) {
                return markAuthSessionNeedsSignin('Your saved Supabase session expired. Please sign in with Google again.');
            }
            if (response.status === 429) {
                lastAuth429At = Date.now();
                lastAuth429RetryCount += 1;
                warnAuthRefreshOnce('Supabase rate-limited on token refresh. Backing off.');
                return authState;
            }
            throw new Error(`Supabase refresh failed (${response.status})`);
        }

        const session = await response.json();
        const nextState = mapSessionPayloadToAuthState({
            accessToken: session.access_token,
            refreshToken: session.refresh_token || authState.refresh_token,
            expiresAt: session.expires_at || (Math.floor(Date.now() / 1000) + Number(session.expires_in || 0)),
            user: {
                id: session.user?.id,
                email: session.user?.email,
                name: session.user?.user_metadata?.full_name || session.user?.user_metadata?.name,
                avatar_url: session.user?.user_metadata?.avatar_url || session.user?.user_metadata?.picture
            }
        });
        persistAuthState(nextState);
        lastAuthBackendRejectedAt = 0;
        lastAuthRefreshAttemptAt = 0;
        lastAuthRefreshWarningAt = 0;
        lastAuthRefreshWarningMessage = '';
        lastAuth429At = 0;
        lastAuth429RetryCount = 0;
        await syncAuthStateWithBackend(true);
    } catch (error) {
        warnAuthRefreshOnce(
            /fetch failed|getaddrinfo|network|enotfound|eai_again|timed out|timeout/i.test(error.message || '')
                ? 'Supabase is temporarily unreachable. Check internet/DNS; Vidzeo will retry without deleting the saved session.'
                : (error.message || 'Failed to refresh auth session.')
        );
    }

    return authState;
}

async function startExternalBrowserSignin() {
    if (!backendReady) {
        throw new Error('Backend is still starting. Try again in a moment.');
    }

    const response = await fetch(`${BACKEND_URL}/api/auth/browser/start`, {
        method: 'POST',
        headers: withDeviceHeaders()
    });

    if (!response.ok) {
        let message = `Browser sign-in could not be started (${response.status})`;
        try {
            const payload = await response.json();
            if (payload?.error) {
                message = payload.error;
            }
        } catch (_error) {
            // Keep fallback message.
        }
        throw new Error(message);
    }

    const payload = await response.json();
    await openExternalAuthBrowser(payload.auth_url);
    return payload;
}

async function getExternalBrowserSigninStatus(flowId, flowSecret) {
    const url = `${BACKEND_URL}/api/auth/browser/status/${encodeURIComponent(flowId)}?flow_secret=${encodeURIComponent(flowSecret || '')}`;
    const response = await fetch(url, { method: 'GET' });
    if (!response.ok) {
        let message = `Browser sign-in status check failed (${response.status})`;
        try {
            const payload = await response.json();
            if (payload?.error) {
                message = payload.error;
            }
        } catch (_error) {
            // Keep fallback message.
        }
        const error = new Error(message);
        error.status = response.status;
        throw error;
    }
    return response.json();
}

async function startPythonBackend() {
    if (pythonProcess) return;

    backendStopRequested = false;

    try {
        const bootstrapPromise = ensureRuntimeDependencies();
        if (bootstrapPromise) {
            await bootstrapPromise;
        }
    } catch (error) {
        const detail = error.message || 'Unknown runtime bootstrap error.';
        recordRuntimeLog('ERROR', 'Runtime bootstrap failed.', error?.stack || detail);
        broadcastBackendState({
            phase: 'error',
            ready: false,
            error: detail,
            code: 'RUNTIME_BOOTSTRAP_FAILED',
            message: 'Runtime setup failed. Close and relaunch Vidzeo, or run INSTALL.bat once.'
        });
        scheduleBackendRestart('runtime bootstrap failed');
        return;
    }

    const pythonExe = resolvePythonExecutable();
    const pythonDir = resolveToolDirectory('python');
    const pythonScriptsDir = firstExistingPath([
        getToolPath('python', 'Scripts'),
        getResourcePath('tools', 'python', 'Scripts'),
        path.join(__dirname, 'tools', 'python', 'Scripts')
    ]);
    const ffmpegDir = resolveFfmpegDirectory();
    const mainPy = getResourcePath('main.py');
    const serverDir = getResourcePath('server');
    const resourceRoot = getResourcePath();
    const fontsDir = getResourcePath('fonts');
    const publicDir = getResourcePath('public');
    const srcDir = getResourcePath('src');
    const adminPanelDir = getResourcePath('admin-panel');
    const nodeExe = resolveBundledNodeExecutable();
    const nodeDir = nodeExe ? path.dirname(nodeExe) : '';
    const pathParts = [ffmpegDir, pythonDir, pythonScriptsDir, nodeDir, process.env.PATH].filter(Boolean);

    const env = Object.assign({}, process.env, localEnv, {
        TF_CPP_MIN_LOG_LEVEL: '3',
        TF_ENABLE_ONEDNN_OPTS: '0',
        CUDA_VISIBLE_DEVICES: '-1',
        TRANSFORMERS_VERBOSITY: 'error',
        PYTHONWARNINGS: 'ignore',
        PYTHONUNBUFFERED: '1',
        PYTHONIOENCODING: 'utf-8',
        TOKENIZERS_PARALLELISM: 'false',
        VIDZEO_ROOT: appRoot,
        VIDZEO_NODE_PATH: nodeExe,
        // Writable user data and packaged assets are different roots in a
        // production Electron build. Renderers must never confuse the two.
        VIDZEO_RESOURCE_ROOT: resourceRoot,
        VIDZEO_SESSION_SECRET_FILE: path.join(appRoot, '.vidzeo_session_secret'),
        // Keep the encrypted YouTube cookie snapshot beside the packaged
        // app's writable data, not beside read-only resources or a transient
        // working directory. This makes imported cookies survive restarts and
        // installer launches under the same Windows user.
        VIDZEO_SECURE_DATA_DIR: path.join(appRoot, 'secure'),
        VIDZEO_SERVER_DIR: serverDir,
        VIDZEO_FONTS_DIR: fontsDir,
        VIDZEO_PUBLIC_DIR: publicDir,
        VIDZEO_OUTPUT_DIR: path.join(appRoot, 'output'),
        VIDZEO_TEMP_DIR: path.join(appRoot, 'temp'),
        VIDZEO_QUEUE_FILE: path.join(appRoot, 'server', 'queue.json'),
        VIDZEO_TEMPLATE_LIBRARY_DIR: getResourcePath('resources', 'template-library'),
        VIDZEO_TEMPLATE_CUSTOM_DIR: path.join(appRoot, 'template-library', 'custom'),
        VIDZEO_SRC_DIR: srcDir,
        VIDZEO_ADMIN_PANEL_DIR: adminPanelDir,
        VIDZEO_PACKAGED: app.isPackaged ? '1' : '0',
        VIDZEO_AUTH_MODE: isLocalDistributionMode ? 'local' : 'supabase',
        VIDZEO_PORT: String(BACKEND_PORT),
        // Fast, no-cookie Yandex discovery lane. The Python adapter falls
        // through to requests and then the local browser if this transport
        // is unavailable or challenged.
        VIDZEO_YANDEX_CURL_IMPERSONATE: firstEnvValue([
            'VIDZEO_YANDEX_CURL_IMPERSONATE'
        ]) || '1',
        // Per-launch secret used only between the Electron main process and
        // the localhost cookie broker. It is never written to UI logs.
        VIDZEO_DESKTOP_CONNECTOR_TOKEN: DESKTOP_CONNECTOR_TOKEN,

        PATH: pathParts.join(';')
    });

    const pyExe = pythonExe || 'python';
    lastBackendActivityAt = Date.now();
    broadcastBackendState({
        phase: 'starting',
        message: 'Starting Vidzeo backend…',
        error: '',
        code: null,
        details: null
    });

    // In development, make the backend console visible by default so a
    // failed local run is immediately inspectable. Packaged distributions
    // keep it hidden and retain the same stream in the UI and durable log;
    // VIDZEO_SHOW_BACKEND_CONSOLE=1 is an explicit support override.
    const showBackendConsoleValue = String(process.env.VIDZEO_SHOW_BACKEND_CONSOLE || localEnv.VIDZEO_SHOW_BACKEND_CONSOLE || '').trim().toLowerCase();
    const showBackendConsole = showBackendConsoleValue === '1' || showBackendConsoleValue === 'true';
    pythonProcess = spawn(pyExe, [mainPy], {
        cwd: app.isPackaged ? path.dirname(mainPy) : __dirname,
        env,
        stdio: ['ignore', 'pipe', 'pipe'],
        windowsHide: app.isPackaged && !showBackendConsole
    });

    let stdoutBuffer = '';
    let stderrBuffer = '';
    let backendExitCode = null;
    let backendExitSignal = '';
    const spawnedProcess = pythonProcess;
    const emitPythonLines = (chunk, isError = false) => {
        const buffer = isError ? `${stderrBuffer}${chunk}` : `${stdoutBuffer}${chunk}`;
        const lines = buffer.split(/\r?\n/);
        if (isError) stderrBuffer = lines.pop() || ''; else stdoutBuffer = lines.pop() || '';
        lines.map(normalizeBackendLogLine).filter(Boolean).forEach((msg) => {
            if (/^Deprecated Feature: Support for Python version 3\.10/i.test(msg)) return;
            lastBackendActivityAt = Date.now();
            const isRealError = isError && !/^\s*(\*\s*Running on|Press CTRL\+C|\[INFO\]|\[DEBUG\]|\*\s*Serving Flask app|\*\s*Debug mode)/i.test(msg);
            const prefix = isRealError ? '[Python ERR]' : '[Python]';
            (isRealError ? console.error : console.log)(`${prefix} ${msg}`);
            if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('backend-log', msg);
            broadcastBackendState({ phase: backendReady ? 'ready' : 'starting', message: msg });
        });
    };
    pythonProcess.stdout.on('data', (data) => emitPythonLines(data.toString('utf8'), false));
    pythonProcess.stderr.on('data', (data) => emitPythonLines(data.toString('utf8'), true));

    pythonProcess.once('exit', (code, signal) => {
        backendExitCode = code;
        backendExitSignal = signal || '';
    });

    pythonProcess.on('close', (code) => {
        emitPythonLines('\n', false);
        emitPythonLines('\n', true);
        const exitDescription = `Python backend exited with code ${code}${backendExitSignal ? ` (${backendExitSignal})` : ''}`;
        recordRuntimeLog(code === 0 || backendStopRequested ? 'INFO' : 'ERROR', exitDescription);
        if (pythonProcess === spawnedProcess) pythonProcess = null;
        backendReady = false;
        if (appQuitting || backendStopRequested) {
            broadcastBackendState({
                phase: 'stopped',
                ready: false,
                code,
                message: 'Backend stopped.',
                error: ''
            });
        } else if (!appLoaded) {
            broadcastBackendState({
                phase: 'error',
                ready: false,
                code,
                error: 'Backend stopped before the app finished loading. The app will retry automatically.',
                message: 'Backend stopped during startup; retrying automatically.'
            });
            scheduleBackendRestart(`startup exit ${code}`);
        } else {
            broadcastBackendState({
                phase: 'error',
                ready: false,
                code,
                message: 'Backend stopped unexpectedly; retrying automatically.',
                error: 'The backend stopped during generation. Queue state was saved and will be available after recovery.'
            });
            scheduleBackendRestart(`exit ${code}`);
        }
    });

    pythonProcess.on('error', (err) => {
        recordRuntimeLog('ERROR', 'Failed to start Python backend.', err?.stack || err?.message || err);
        if (pythonProcess === spawnedProcess) pythonProcess = null;
        backendReady = false;
        broadcastBackendState({
            phase: 'error',
            ready: false,
            error: err.message,
            code: 'BACKEND_START_FAILED',
            message: 'Failed to start backend; retrying automatically.'
        });
        scheduleBackendRestart(err.message || 'process start failed');
    });
}

function stopPythonBackend() {
    backendStopRequested = true;
    if (backendRestartTimer) {
        clearTimeout(backendRestartTimer);
        backendRestartTimer = null;
    }
    if (!pythonProcess) return;

    console.log('[Electron] Stopping Python backend...');
    if (process.platform === 'win32') {
        try {
            execSync(`taskkill /PID ${pythonProcess.pid} /T /F`, { stdio: 'ignore' });
        } catch (_error) {
            try {
                pythonProcess.kill();
            } catch (_killError) {
                // Ignore shutdown noise.
            }
        }
    } else {
        pythonProcess.kill('SIGTERM');
    }
    pythonProcess = null;
}

function startBackendMonitor() {
    if (backendMonitor) return;

    backendMonitor = setInterval(async () => {
        if (backendMonitorInFlight) return;
        backendMonitorInFlight = true;
        try {
            const status = await fetchBackendStatus(8000);
            if (!isVidzeoStatus(status)) {
                return;
            }

            lastBackendActivityAt = Date.now();
            backendStatusMisses = 0;
            const newReady = Boolean(status.ready);
            const newPhase = newReady ? 'ready' : 'starting';
            const newMessage = status.boot_stage || (newReady ? 'Backend ready.' : 'Backend starting…');
            if (newReady) backendRestartAttempts = 0;

            // Only broadcast the monitor message if the ready state changed, 
            // or if we are still starting up (so we see the changing boot_stage).
            // This prevents interleaving repetitive "Backend ready" with Python logs.
            if (newReady !== backendState.ready || newPhase !== backendState.phase || !newReady) {
                backendReady = newReady;
                broadcastBackendState({
                    phase: newPhase,
                    ready: newReady,
                    message: newMessage,
                    details: status,
                    error: status.boot_error || ''
                });
            }

            if (newReady) {
                if (authRequested && authState.access_token) {
                    await syncAuthStateWithBackend();
                }
                loadMainApp();
            }
        } catch (_error) {
            backendStatusMisses += 1;
            if (backendReady && backendStatusMisses >= 4) {
                const recentlyActive = (Date.now() - lastBackendActivityAt) < BACKEND_SLOW_GRACE_MS;
                broadcastBackendState({
                    phase: 'ready',
                    ready: true,
                    message: recentlyActive
                        ? 'Generation in progress. Backend heartbeat is delayed.'
                        : 'Backend is busy. Waiting for the next heartbeat…',
                    error: ''
                });
            }
        } finally {
            backendMonitorInFlight = false;
        }
    }, 1200);
}

function stopBackendMonitor() {
    if (backendMonitor) {
        clearInterval(backendMonitor);
        backendMonitor = null;
    }
}

function loadLandingPage() {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    appLoaded = false;
    const landingPath = getResourcePath('src', 'landing.html');
    mainWindow.loadFile(landingPath).catch((error) => {
        console.error('[Electron] Failed to load landing page:', error);
    });
}

function loadMainApp() {
    if (!mainWindow || mainWindow.isDestroyed() || appLoaded || !backendReady) return;
    if (!isLocalDistributionMode && (!authRequested || !hasVerifiedAuthState())) return;

    appLoaded = true;
    mainWindow.loadURL(BACKEND_URL).catch((error) => {
        appLoaded = false;
        broadcastBackendState({
            phase: 'error',
            ready: false,
            error: error.message,
            message: 'Failed to load the main app.'
        });
    });
}

function createWindow() {
    mainWindow = new BrowserWindow({
        width: 1440,
        height: 900,
        minWidth: 1100,
        minHeight: 700,
        backgroundColor: '#071018',
        icon: getWindowIconPath(),
        frame: false,
        titleBarStyle: 'hidden',
        title: 'Vidnets Studio v1',
        show: false,

        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
            webSecurity: true,
            allowRunningInsecureContent: false,
            backgroundThrottling: true,
            // Keep the first paint enabled so ready-to-show fires reliably in
            // packaged builds. With this disabled, the app window can remain
            // invisible and every Settings click appears to do nothing.
            paintWhenInitiallyHidden: true,
            devTools: !app.isPackaged
        }

    });

    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
        openSafeExternalUrl(url).catch((error) => {
            console.warn('[Electron] Blocked external window:', error.message);
        });
        return { action: 'deny' };
    });

    mainWindow.webContents.on('will-navigate', (event, url) => {
        if (isAllowedAppNavigation(url)) {
            return;
        }
        event.preventDefault();
        openSafeExternalUrl(url).catch((error) => {
            console.warn('[Electron] Blocked navigation:', error.message);
        });
    });

    // Keep a renderer crash from taking the whole desktop app down.  The
    // backend owns the queue state, so a renderer reload is safe and lets the
    // user continue without losing an in-flight export.
    mainWindow.webContents.on('render-process-gone', (_event, details = {}) => {
        recordRuntimeLog('ERROR', 'Renderer process exited.', JSON.stringify({
            reason: details.reason,
            exitCode: details.exitCode,
            serviceName: details.serviceName
        }));
        if (rendererRecoveryInFlight || !mainWindow || mainWindow.isDestroyed()) return;
        rendererRecoveryInFlight = true;
        broadcastBackendState({
            phase: backendReady ? 'ready' : 'error',
            ready: backendReady,
            code: 'RENDERER_RECOVERED',
            error: 'The app window recovered after an internal UI error. Your queue is preserved.',
            message: 'Recovering the Vidnets window…'
        });
        setTimeout(() => {
            rendererRecoveryInFlight = false;
            if (!mainWindow || mainWindow.isDestroyed()) return;
            try {
                if (backendReady && appLoaded) mainWindow.webContents.reload();
                else loadLandingPage();
            } catch (error) {
                recordRuntimeLog('ERROR', 'Renderer recovery failed.', error?.stack || error?.message || error);
            }
        }, 750);
    });
    mainWindow.webContents.on('unresponsive', () => {
        recordRuntimeLog('ERROR', 'Renderer became unresponsive.');
        broadcastBackendState({
            phase: backendReady ? 'ready' : 'error',
            ready: backendReady,
            code: 'RENDERER_UNRESPONSIVE',
            error: 'The app is busy; generation continues in the backend. Avoid closing the window.',
            message: 'Vidnets is busy; the queue is still safe.'
        });
    });
    mainWindow.webContents.on('responsive', () => recordRuntimeLog('INFO', 'Renderer responsive again.'));
    mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
        if (errorCode === -3) return; // normal aborted navigation
        recordRuntimeLog('ERROR', 'Renderer failed to load.', `${errorCode} ${errorDescription} ${validatedURL}`);
    });

    loadLandingPage();

    mainWindow.once('ready-to-show', () => {
        if (!mainWindow) return;
        mainWindow.show();
        mainWindow.maximize();
        if (process.env.VIDZEO_OPEN_DEVTOOLS === '1') {
            mainWindow.webContents.openDevTools();
        }
        mainWindow.webContents.send('backend-state-changed', { ...backendState });
    });

    mainWindow.on('closed', () => {
        mainWindow = null;
    });

    mainWindow.on('maximize', () => {
        if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send('window-state-changed', 'maximized');
        }
    });

    mainWindow.on('unmaximize', () => {
        if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.send('window-state-changed', 'normal');
        }
    });
}

ipcMain.on('window-minimize', () => {
    if (mainWindow) mainWindow.minimize();
});

ipcMain.on('window-maximize', () => {
    if (mainWindow) {
        mainWindow.isMaximized() ? mainWindow.unmaximize() : mainWindow.maximize();
    }
});

ipcMain.on('window-close', () => {
    if (mainWindow) mainWindow.close();
});

ipcMain.handle('window-is-maximized', () => {
    return mainWindow ? mainWindow.isMaximized() : false;
});

ipcMain.handle('open-file-dialog', async () => {
    if (!mainWindow) return [];
    const result = await dialog.showOpenDialog(mainWindow, {
        properties: ['openFile', 'multiSelections'],
        filters: [
            { name: 'Media Files', extensions: ['mp4', 'mov', 'avi', 'mkv', 'webm', 'wmv', 'flv', 'mp3', 'wav', 'ogg', 'aac', 'flac', 'm4a', 'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'] },
            { name: 'Video', extensions: ['mp4', 'mov', 'avi', 'mkv', 'webm', 'wmv', 'flv'] },
            { name: 'Audio', extensions: ['mp3', 'wav', 'ogg', 'aac', 'flac', 'm4a'] },
            { name: 'Images', extensions: ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'] },
            { name: 'All Files', extensions: ['*'] }
        ]
    });
    if (!result.canceled) {
        for (const filePath of result.filePaths || []) {
            allowedLocalMediaFiles.add(path.resolve(filePath));
        }
    }
    return result.canceled ? [] : result.filePaths;
});

ipcMain.handle('select-output-folder', async () => {
    if (!mainWindow) return '';
    const result = await dialog.showOpenDialog(mainWindow, {
        title: 'Choose output folder',
        properties: ['openDirectory', 'createDirectory']
    });
    return result.canceled ? '' : (result.filePaths?.[0] || '');
});

ipcMain.handle('app-update-state', () => publishAppUpdateState({}));
ipcMain.handle('app-update-check', async () => checkForAppUpdate());
ipcMain.handle('app-update-download', async () => downloadAppUpdate());
ipcMain.handle('app-update-install', async () => {
    try {
        return await installAppUpdate();
    } catch (error) {
        return publishAppUpdateState({ phase: 'error', error: error.message || 'Update install failed.' });
    }
});

ipcMain.handle('backend-get-state', () => ({ ...backendState }));
ipcMain.handle('backend-retry-startup', async () => {
    if (backendReady || pythonProcess) {
        return { ...backendState };
    }
    broadcastBackendState({
        phase: 'starting',
        ready: false,
        message: 'Retrying backend startup...',
        error: '',
        code: null
    });
    selectBackendPort().then(() => startPythonBackend()).catch((err) => {
        console.error('[Electron] Retry failed:', err.message);
        broadcastBackendState({
            phase: 'error',
            ready: false,
            error: err.message,
            code: 'BACKEND_PORT_UNAVAILABLE',
            message: 'Could not find an available localhost port for Vidnets Studio.',
        });
    });
    return { ...backendState };
});
ipcMain.handle('auth-get-config', () => ({ ...authConfig }));
ipcMain.handle('auth-start-browser-signin', async () => startExternalBrowserSignin());
ipcMain.handle('auth-check-browser-signin', async (_event, payload = {}) => {
    return getExternalBrowserSigninStatus(payload.flowId || '', payload.flowSecret || '');
});
ipcMain.handle('auth-complete-signin', async (_event, payload = {}) => {
    if (!backendReady) {
        clearStoredAuthState();
        throw new Error('Backend is not ready to verify Google sign-in.');
    }
    await verifyAndPersistAuthPayload(payload);
    await syncAuthStateWithBackend(true);
    loadMainApp();
    return { ...authState, backendReady };
});
ipcMain.handle('auth-get-state', async () => {
    await refreshSupabaseSession();
    if (backendReady && authState.access_token) {
        await syncAuthStateWithBackend();
    }
    return { ...authState, backendReady };
});
ipcMain.handle('auth-get-device-fingerprint', async () => deviceFingerprint);
ipcMain.handle('auth-refresh', async () => {
    await refreshSupabaseSession(true);
    if (backendReady && authState.access_token) {
        await syncAuthStateWithBackend(true);
    }
    return { ...authState, backendReady };
});
ipcMain.handle('auth-logout', async () => {
    clearStoredAuthState();
    if (isLocalDistributionMode) {
        loadMainApp();
    } else {
        loadLandingPage();
    }
    return { ...authState, backendReady };
});
ipcMain.handle('secrets-load-settings', async () => loadSecretSettings());
ipcMain.handle('secrets-save-settings', async (_event, settings = {}) => persistSecretSettings(settings));
ipcMain.handle('open-external-url', async (_event, url) => {
    await openSafeExternalUrl(url);
    return true;
});
ipcMain.handle('youtube-connect-managed-browser', async (_event, browser = 'chrome') => {
    if (managedYouTubeConnectionPromise) return managedYouTubeConnectionPromise;
    managedYouTubeConnectionPromise = connectTrustedYouTubeBrowser(browser)
        .finally(() => { managedYouTubeConnectionPromise = null; });
    return managedYouTubeConnectionPromise;
});
ipcMain.handle('youtube-open-login', async (_event, browser = 'chrome') => {
    if (managedYouTubeConnectionPromise) return managedYouTubeConnectionPromise;
    managedYouTubeConnectionPromise = connectTrustedYouTubeBrowser(browser)
        .finally(() => { managedYouTubeConnectionPromise = null; });
    return managedYouTubeConnectionPromise;
});
ipcMain.handle('youtube-sync-cookies', async () => {
    const cookies = await getYouTubeSessionCookies('persist:youtube-signin');
    const authCookies = cookies.filter((cookie) =>
        YOUTUBE_AUTH_COOKIE_NAMES.has(String(cookie?.name || '').toUpperCase())
    );
    if (authCookies.length < 2) {
        return { ok: false, success: false, message: 'No authenticated YouTube cookies found in current session. Please sign in first.' };
    }
    const payload = await importManagedYouTubeCookies('chrome', cookies);
    return { ok: true, success: true, authCount: authCookies.length, payload };
});

ipcMain.handle('notify-video-complete', async (_event, payload = {}) => {
    const title = String(payload.title || 'Your video').trim().slice(0, 160) || 'Your video';
    const body = String(payload.body || `${title} finished rendering.`).trim().slice(0, 240);
    if (!Notification || !Notification.isSupported()) {
        return { shown: false, supported: false };
    }
    try {
        const notification = new Notification({
            title: 'Vidzeo video ready',
            body,
            silent: false,
        });
        notification.on('click', () => {
            if (!mainWindow || mainWindow.isDestroyed()) return;
            if (mainWindow.isMinimized()) mainWindow.restore();
            mainWindow.show();
            mainWindow.focus();
            mainWindow.webContents.send('open-projects', {
                videoUrl: String(payload.videoUrl || ''),
                title,
            });
        });
        notification.show();
        return { shown: true, supported: true };
    } catch (error) {
        console.warn('[Electron] Could not show completion notification:', error.message);
        return { shown: false, supported: true };
    }
});

ipcMain.handle('open-output-folder', async (_event, customFolder) => {
    const targetDir = customFolder && typeof customFolder === 'string' && customFolder.trim() ? customFolder.trim() : path.join(appRoot, 'output');
    if (!fs.existsSync(targetDir)) {
        fs.mkdirSync(targetDir, { recursive: true });
    }
    await shell.openPath(targetDir);
    return true;
});

ipcMain.handle('open-task-manager', async () => {
    if (process.platform !== 'win32') return false;
    try {
        const child = spawn('taskmgr.exe', [], {
            detached: true,
            stdio: 'ignore',
            windowsHide: false,
        });
        child.unref();
        return true;
    } catch (error) {
        console.warn('[Electron] Could not open Task Manager:', error.message);
        return false;
    }
});


ipcMain.handle('select-file', async (_event, filePath) => {
    if (!filePath) return false;
    // Renderer may pass a bare filename from the current video record; resolve
    // it against the output folder so the reveal lands on the real file.
    if (!path.isAbsolute(filePath)) {
        filePath = path.join(appRoot, 'output', path.basename(filePath));
    }
    if (!fs.existsSync(filePath)) return false;
    await shell.showItemInFolder(filePath);
    return true;
});

app.whenReady().then(async () => {
    if (!gotSingleInstanceLock) return;
    if (process.platform === 'win32') {
        try { app.setAsDefaultProtocolClient('vidnets'); } catch (error) { console.warn('[Electron] Could not register vidnets:// protocol:', error.message); }
        const protocolArg = process.argv.find((argument) => /^vidnets:\/\//i.test(argument));
        if (protocolArg) pendingProtocolUrl = protocolArg;
    }
    authState = loadStoredAuthState();
    authRequested = authState.signed_in;

    protocol.handle('local-media', (request) => {
        let filePath = request.url.slice('local-media://'.length);
        filePath = decodeURIComponent(filePath);
        const resolved = path.resolve(filePath);
        if (!isAllowedLocalMediaPath(resolved)) {
            return new Response('Forbidden', { status: 403 });
        }
        return net.fetch(`file:///${resolved.replace(/\\/g, '/')}`);
    });

    createWindow();
    if (pendingProtocolUrl) {
        const protocolUrl = pendingProtocolUrl;
        pendingProtocolUrl = '';
        setTimeout(() => handleVidnetsProtocolUrl(protocolUrl), 250);
    }

    const existingBackend = await probeExistingBackend();
    // Reuse only a backend that is already ready. A stale/half-booted process
    // on the preferred port used to make the new app attach to a permanently
    // "starting" status and never launch its own server. If that happens,
    // select a free fallback port and start a clean backend instance.
    if (existingBackend && existingBackend.ready) {
        backendReady = Boolean(existingBackend.ready);
        broadcastBackendState({
            phase: backendReady ? 'ready' : 'starting',
            ready: backendReady,
            usedExistingBackend: true,
            message: existingBackend.boot_stage || 'Connected to existing Vidzeo backend.',
            details: existingBackend,
            error: existingBackend.boot_error || ''
        });
        if (backendReady) {
            if (authRequested && authState.access_token) {
                await refreshSupabaseSession();
                await syncAuthStateWithBackend(true);
            }
            loadMainApp();
        }
    } else {
        if (existingBackend) {
            console.warn('[Electron] Ignoring stale backend on the preferred port; starting an isolated instance.');
        }
        selectBackendPort().then(() => startPythonBackend()).catch((err) => {
            console.error('[Electron] Backend startup failed:', err.message);
            broadcastBackendState({
                phase: 'error',
                ready: false,
                error: err.message,
                code: 'BACKEND_PORT_UNAVAILABLE',
                message: 'Could not find an available localhost port for Vidnets Studio.',
            });
        });
    }

    startBackendMonitor();

    app.on('activate', () => {
        if (BrowserWindow.getAllWindows().length === 0) {
            appLoaded = false;
            createWindow();
            if (backendReady) {
                loadMainApp();
            }
        }
    });
});

app.on('child-process-gone', (_event, details = {}) => {
    recordRuntimeLog('ERROR', 'Electron child process exited.', JSON.stringify({
        type: details.type,
        reason: details.reason,
        exitCode: details.exitCode
    }));
    if (details.type !== 'GPU' && mainWindow && !mainWindow.isDestroyed()) {
        broadcastBackendState({
            phase: backendReady ? 'ready' : 'error',
            ready: backendReady,
            code: 'ELECTRON_CHILD_PROCESS_GONE',
            error: 'A desktop helper process stopped. Vidnets will continue and retry where possible.',
            message: 'Desktop helper recovered.'
        });
    }
});

app.on('window-all-closed', () => {
    appQuitting = true;
    stopBackendMonitor();
    stopPythonBackend();
    if (managedYouTubeProcess && managedYouTubeProcess.exitCode === null) {
        try { managedYouTubeProcess.kill(); } catch (_error) {}
    }
    closeYouTubeSignInWindow();
    app.quit();
});

app.on('before-quit', () => {
    appQuitting = true;
    stopBackendMonitor();
    stopPythonBackend();
    if (managedYouTubeProcess && managedYouTubeProcess.exitCode === null) {
        try { managedYouTubeProcess.kill(); } catch (_error) {}
    }
    closeYouTubeSignInWindow();
});
