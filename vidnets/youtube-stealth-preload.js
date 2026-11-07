'use strict';
/**
 * Stealth Preload Script for Google & YouTube Authentication
 * Masks Electron indicators so Google detects an authentic Google Chrome browser.
 */

const AUTHENTIC_CHROME_VERSION = '131.0.6778.86';
const AUTHENTIC_CHROME_MAJOR = '131';
const AUTHENTIC_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';

// 1. Remove automation / navigator.webdriver marker
try {
  const navProto = Object.getPrototypeOf(navigator);
  if (navProto && 'webdriver' in navProto) {
    delete navProto.webdriver;
  }
} catch (_) {}

try {
  Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
    set: () => {},
    enumerable: false,
    configurable: true
  });
} catch (_) {}

// 2. Spoof navigator identity to authentic Chrome on Windows
try {
  Object.defineProperty(navigator, 'userAgent', {
    get: () => AUTHENTIC_USER_AGENT,
    configurable: true
  });
  Object.defineProperty(navigator, 'appVersion', {
    get: () => AUTHENTIC_USER_AGENT.replace('Mozilla/', ''),
    configurable: true
  });
  Object.defineProperty(navigator, 'vendor', {
    get: () => 'Google Inc.',
    configurable: true
  });
  Object.defineProperty(navigator, 'platform', {
    get: () => 'Win32',
    configurable: true
  });
} catch (_) {}

// 3. Spoof navigator.userAgentData Client Hints
try {
  const brands = [
    { brand: 'Google Chrome', version: AUTHENTIC_CHROME_MAJOR },
    { brand: 'Chromium', version: AUTHENTIC_CHROME_MAJOR },
    { brand: 'Not_A Brand', version: '24' }
  ];
  const fullVersionList = [
    { brand: 'Google Chrome', version: AUTHENTIC_CHROME_VERSION },
    { brand: 'Chromium', version: AUTHENTIC_CHROME_VERSION },
    { brand: 'Not_A Brand', version: '24.0.0.0' }
  ];

  const userAgentDataObj = {
    brands,
    mobile: false,
    platform: 'Windows',
    getHighEntropyValues: async (hints = []) => {
      const result = {
        brands,
        mobile: false,
        platform: 'Windows'
      };
      if (hints.includes('architecture')) result.architecture = 'x86';
      if (hints.includes('bitness')) result.bitness = '64';
      if (hints.includes('fullVersionList')) result.fullVersionList = fullVersionList;
      if (hints.includes('model')) result.model = '';
      if (hints.includes('platformVersion')) result.platformVersion = '15.0.0';
      if (hints.includes('uaFullVersion')) result.uaFullVersion = AUTHENTIC_CHROME_VERSION;
      return result;
    },
    toJSON: () => ({ brands, mobile: false, platform: 'Windows' })
  };

  Object.defineProperty(navigator, 'userAgentData', {
    get: () => userAgentDataObj,
    configurable: true
  });
} catch (_) {}

// 4. Authentic window.chrome object
try {
  window.chrome = {
    app: {
      isInstalled: false,
      InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
      RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' },
      getDetails: () => null,
      getIsInstalled: () => false,
      installState: (cb) => cb && cb('not_installed'),
      runningState: () => 'cannot_run'
    },
    runtime: {
      OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
      OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
      PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
      PlatformNaclArch: { ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
      PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
      RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' },
      connect: () => {},
      sendMessage: () => {}
    },
    loadTimes: function() {
      const now = Date.now() / 1000;
      return {
        commitLoadTime: now - 0.4,
        connectionInfo: 'http/1.1',
        finishDocumentLoadTime: now - 0.1,
        finishLoadTime: now - 0.05,
        firstPaintAfterLoadTime: 0,
        firstPaintTime: now - 0.2,
        navigationType: 'Other',
        npnNegotiatedProtocol: 'http/1.1',
        requestTime: now - 0.6,
        startLoadTime: now - 0.5,
        wasAlternateProtocolAvailable: false,
        wasFetchedViaSpdy: false,
        wasNpnNegotiated: false
      };
    },
    csi: function() {
      return {
        onloadT: Date.now() - 200,
        pageT: 450,
        startE: Date.now() - 650,
        tran: 15
      };
    }
  };
} catch (_) {}

// 5. Authentic PDF plugins emulation
try {
  if (navigator.plugins && navigator.plugins.length === 0) {
    const mockPlugins = [
      { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' }
    ];
    Object.defineProperty(navigator, 'plugins', {
      get: () => mockPlugins,
      configurable: true
    });
  }
} catch (_) {}
