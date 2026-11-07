const fs = require('fs');
const { chromium } = require('playwright-core');

const chromeCandidates = [
  process.env.VIDZEO_CHROME_PATH,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
].filter(Boolean);

(async () => {
  const query = process.argv.slice(2).join(' ').trim();
  if (!query) process.exit(2);
  // Prefer an installed system browser.  Do not pass a missing path to
  // Playwright: some machines have Edge only, and an invalid executable path
  // would make the fallback fail before Yandex is even contacted.
  const executablePath = chromeCandidates.find((candidate) => fs.existsSync(candidate));
  const browser = await chromium.launch({
    headless: true,
    ...(executablePath ? { executablePath } : {}),
    args: ['--disable-blink-features=AutomationControlled', '--lang=en-US'],
  });
  try {
    const page = await browser.newPage({
      viewport: { width: 1365, height: 900 },
      locale: 'en-US',
      userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    });
    await page.addInitScript(() => Object.defineProperty(navigator, 'webdriver', { get: () => undefined }));
    await page.goto(`https://yandex.com/images/search?text=${encodeURIComponent(query)}`, {
      waitUntil: 'domcontentloaded',
      timeout: 30000,
    });
    await page.waitForTimeout(3000);
    const title = await page.title();
    const bodyText = await page.locator('body').innerText().catch(() => '');
    const rows = await page.locator('[data-state],[data-bem],a[href*="img_url"],a[href*="url="]').evaluateAll((elements) => elements.map((element) => ({
      state: element.getAttribute('data-state'),
      bem: element.getAttribute('data-bem'),
      href: element.getAttribute('href'),
    })).filter((item) => item.state || item.bem || item.href));
    process.stdout.write(JSON.stringify({ title, challenge: /not a robot|are you not a robot|captcha/i.test(`${title}\n${bodyText}`), rows }));
  } finally {
    await browser.close();
  }
})().catch((error) => {
  process.stderr.write(String(error && error.stack || error));
  process.exit(1);
});
