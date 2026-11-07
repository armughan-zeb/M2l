const fs = require('fs');
const path = require('path');
const dns = require('dns').promises;
const net = require('net');
const { ensureBrowser } = require('@remotion/renderer');
const { chromium } = require('playwright-core');

const requestPath = path.resolve(process.argv[2]);

function publicAddress(value) {
  const version = net.isIP(value);
  if (version === 4) {
    const parts = value.split('.').map(Number);
    const [a, b, c] = parts;
    if (a === 0 || a === 10 || a === 127 || a >= 224) return false;
    if (a === 169 && b === 254) return false;
    if (a === 172 && b >= 16 && b <= 31) return false;
    if (a === 100 && b >= 64 && b <= 127) return false;
    if (a === 192 && (b === 0 || b === 168 || (b === 88 && c === 99))) return false;
    if (a === 198 && (b === 18 || b === 19 || (b === 51 && c === 100))) return false;
    if (a === 203 && b === 0 && c === 113) return false;
    return true;
  }
  if (version === 6) {
    const lower = value.toLowerCase().replace(/^0+/, '');
    // Restrict to global unicast (2000::/3), excluding documentation space.
    return (lower.startsWith('2') || lower.startsWith('3'))
      && !lower.startsWith('2001:db8:')
      && !lower.startsWith('3fff:');
  }
  return false;
}

async function validateRequestUrl(raw) {
  const url = new URL(raw);
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) throw new Error('Blocked non-public source URL');
  if ((url.protocol === 'https:' && url.port && url.port !== '443') || (url.protocol === 'http:' && url.port && url.port !== '80')) throw new Error('Blocked non-standard source port');
  const records = net.isIP(url.hostname) ? [{ address: url.hostname }] : await dns.lookup(url.hostname, { all: true, verbatim: true });
  if (!records.length || records.some((record) => !publicAddress(record.address))) throw new Error('Blocked source URL resolving to a private network');
  return url.href;
}

function normalize(value) { return String(value || '').replace(/\s+/g, ' ').trim().toLocaleLowerCase(); }

async function main() {
  const request = JSON.parse(fs.readFileSync(requestPath, 'utf8'));
  const url = await validateRequestUrl(request.url);
  const status = await ensureBrowser({ logLevel: 'error' });
  if (!status || !status.path) throw new Error('Remotion browser is not available');
  const browser = await chromium.launch({ executablePath: status.path, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
    const page = await context.newPage();
    await page.route('**/*', async (route) => {
      try { await validateRequestUrl(route.request().url()); await route.continue(); }
      catch { await route.abort('blockedbyclient'); }
    });
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 15000 });
    const result = await page.evaluate((quote) => {
      const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim().toLocaleLowerCase();
      const bodyText = document.body?.innerText || '';
      const exact = normalize(quote);
      if (!exact || !normalize(bodyText).includes(exact)) return { found: false, title: document.title || '' };
      // Select precisely the supplied passage in the browser before capture.
      const selected = window.find(quote, false, false, true, false, false, false);
      if (!selected) return { found: true, highlighted: false, title: document.title || '' };
      const range = window.getSelection()?.getRangeAt(0);
      if (!range) return { found: true, highlighted: false, title: document.title || '' };
      const marker = document.createElement('mark');
      marker.style.cssText = 'background:#ffe36a!important;color:#151515!important;box-shadow:0 0 0 2px #ffe36a!important;border-radius:2px!important';
      try {
        marker.appendChild(range.extractContents());
        range.insertNode(marker);
        marker.scrollIntoView({ block: 'center', behavior: 'instant' });
      } catch (_) { return { found: true, highlighted: false, title: document.title || '' }; }
      const canonical = document.querySelector('link[rel="canonical"]')?.href || location.href;
      const publication = document.querySelector('meta[property="og:site_name"]')?.content || location.hostname;
      return { found: true, highlighted: true, title: document.title || '', canonical, publication };
    }, request.excerpt);
    if (!result.found || !result.highlighted) throw new Error(result.found ? 'Passage could not be highlighted exactly' : 'The supplied passage was not found on this page');
    await page.screenshot({ path: request.output_path, type: 'png', fullPage: false, animations: 'disabled' });
    process.stdout.write(JSON.stringify({ success: true, ...result, final_url: await page.url() }));
    await context.close();
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(String(error?.message || error));
  process.exitCode = 1;
});
