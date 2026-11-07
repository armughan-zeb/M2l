const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
    minimize: () => ipcRenderer.send('window-minimize'),
    maximize: () => ipcRenderer.send('window-maximize'),
    close: () => ipcRenderer.send('window-close'),
    isMaximized: () => ipcRenderer.invoke('window-is-maximized'),
    openFileDialog: () => ipcRenderer.invoke('open-file-dialog'),
    selectOutputFolder: () => ipcRenderer.invoke('select-output-folder'),
    getBackendState: () => ipcRenderer.invoke('backend-get-state'),
    retryBackendStartup: () => ipcRenderer.invoke('backend-retry-startup'),
    getAuthConfig: () => ipcRenderer.invoke('auth-get-config'),
    startBrowserGoogleSignIn: () => ipcRenderer.invoke('auth-start-browser-signin'),
    checkBrowserGoogleSignIn: (payload) => ipcRenderer.invoke('auth-check-browser-signin', payload),
    completeGoogleSignIn: (payload) => ipcRenderer.invoke('auth-complete-signin', payload),
    getAuthState: () => ipcRenderer.invoke('auth-get-state'),
    getDeviceFingerprint: () => ipcRenderer.invoke('auth-get-device-fingerprint'),
    refreshAuthState: () => ipcRenderer.invoke('auth-refresh'),
    onAuthStateChanged: (callback) => {
        const listener = (_event, payload) => callback?.(payload);
        ipcRenderer.on('auth-state-changed', listener);
        return () => ipcRenderer.removeListener('auth-state-changed', listener);
    },
    onAuthBrowserClosed: (callback) => {
        const listener = (_event, payload) => callback?.(payload || {});
        ipcRenderer.on('auth-browser-closed', listener);
        return () => ipcRenderer.removeListener('auth-browser-closed', listener);
    },
    onAuthBrowserError: (callback) => {
        const listener = (_event, payload) => callback?.(payload || {});
        ipcRenderer.on('auth-browser-error', listener);
        return () => ipcRenderer.removeListener('auth-browser-error', listener);
    },
    logoutToLanding: () => ipcRenderer.invoke('auth-logout'),
    loadSecretSettings: () => ipcRenderer.invoke('secrets-load-settings'),
    saveSecretSettings: (settings) => ipcRenderer.invoke('secrets-save-settings', settings),
    notifyVideoComplete: (payload) => ipcRenderer.invoke('notify-video-complete', payload),
    onOpenProjects: (callback) => {
        const listener = (_event, payload) => callback?.(payload || {});
        ipcRenderer.on('open-projects', listener);
        return () => ipcRenderer.removeListener('open-projects', listener);
    },
    openExternalUrl: (url) => ipcRenderer.invoke('open-external-url', url),
    connectYouTubeBrowser: (browser) => ipcRenderer.invoke('youtube-connect-managed-browser', browser),
    openYouTubeLogin: (browser) => ipcRenderer.invoke('youtube-open-login', browser),
    syncYouTubeCookies: () => ipcRenderer.invoke('youtube-sync-cookies'),
    onYouTubeConnectionStatus: (callback) => {
        const listener = (_event, payload) => callback?.(payload || {});
        ipcRenderer.on('youtube-connection-status', listener);
        return () => ipcRenderer.removeListener('youtube-connection-status', listener);
    },
    openOutputFolder: (folderPath) => ipcRenderer.invoke('open-output-folder', folderPath),
    openTaskManager: () => ipcRenderer.invoke('open-task-manager'),
    selectFileInExplorer: (filePath) => ipcRenderer.invoke('select-file', filePath),
    // Updates are handled in the main process so manifest validation,
    // downloads, and installer launches never depend on renderer privileges.
    getAppUpdateState: () => ipcRenderer.invoke('app-update-state'),
    checkForAppUpdate: () => ipcRenderer.invoke('app-update-check'),
    downloadAppUpdate: () => ipcRenderer.invoke('app-update-download'),
    installAppUpdate: () => ipcRenderer.invoke('app-update-install'),
    onAppUpdateStateChanged: (callback) => {
        const listener = (_event, payload) => callback?.(payload || {});
        ipcRenderer.on('app-update-state-changed', listener);
        return () => ipcRenderer.removeListener('app-update-state-changed', listener);
    },

    onBackendLog: (callback) => ipcRenderer.on('backend-log', (_event, msg) => callback(msg)),
    onBackendStateChanged: (callback) => ipcRenderer.on('backend-state-changed', (_event, state) => callback(state)),
    onWindowStateChanged: (callback) => ipcRenderer.on('window-state-changed', (_event, state) => callback(state))
});
