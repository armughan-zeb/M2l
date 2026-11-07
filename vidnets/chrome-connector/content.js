// The page itself never reads cookies. It only wakes the extension worker;
// the privileged chrome.cookies API performs the narrowly scoped sync.
try {
    chrome.runtime.sendMessage({ type: 'sync-now' }).catch(() => {});
} catch (_error) {
    // The service worker may be asleep or the page may be unloading.
}
