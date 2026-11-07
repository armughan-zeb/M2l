# Vidzeo YouTube Connector

This first-party Manifest V3 extension connects Vidzeo to the current Chrome
YouTube session without reading Chrome's encrypted SQLite cookie database.

## Install once

1. Open `chrome://extensions` in Chrome.
2. Enable **Developer mode**.
3. Choose **Load unpacked**.
4. Select this `chrome-connector` folder.
5. Keep Chrome signed in to the YouTube account you want Vidzeo to use.

The service worker syncs live YouTube/Google cookies on startup, when
authentication cookies change, and every five minutes. It sends only the
filtered local snapshot to Vidzeo at `127.0.0.1:5000`; it does not upload
cookies to a remote service or write a plaintext cookie file.

The packaged Vidzeo app loads this connector into its own normal Chrome
profile automatically. A small YouTube content hook triggers an immediate
sync after account selection, so no manual cookie export is required.

If Chrome or Vidzeo is not running, the connector records an offline state and
retries automatically. You can also click the extension's **Sync now** button.
