const {chromium} = require('playwright-core');
const fs = require('fs');
const path = require('path');
const {pathToFileURL} = require('url');
(async () => {
  const root = path.resolve(__dirname, '..');
  const folder = path.join(root, 'artifacts/release_validation_20261006');
  const browser = await chromium.launch({headless:true, executablePath:path.join(root,
    'node_modules/.remotion/chrome-headless-shell/win64/chrome-headless-shell-win64/chrome-headless-shell.exe')});
  try {
    const page = await browser.newPage();
    await page.goto(pathToFileURL(path.join(root, 'src/index.html')).href);
    await page.waitForTimeout(2000);
    await page.evaluate(() => {AppState.account.accessState='active';AccountController.render();navigateTo('settings');});
    await page.waitForTimeout(400);
    const savedModel = await page.evaluate(() => {
      AppState.dropdownData.llmModels.providers = {};
      AppState.llmModel = 'saved/custom-model';
      syncLlmModelSelects(AppState.llmModel);
      const preserved = document.getElementById('llmModelSelect').value;
      AppState.llmModel = 'qwen/qwen3.5-flash-02-23';
      syncLlmModelSelects(AppState.llmModel);
      return preserved;
    });
    if (savedModel !== 'saved/custom-model') throw new Error('Startup catalog reset the saved model');
    const checks = [];
    for (const theme of ['dark','light']) {
      await page.evaluate(theme => setTheme(theme), theme);
      for (const width of [1440,1024,760]) {
        await page.setViewportSize({width,height:960});
        await page.waitForTimeout(200);
        const state = await page.evaluate(() => {
          const s=document.getElementById('section-settings'),select=document.getElementById('llmModelSelect');
          const fields=[...s.querySelectorAll('input,select')];
          return {width:innerWidth,active:s.classList.contains('active'),opacity:getComputedStyle(s).opacity,
            overflow:document.documentElement.scrollWidth>innerWidth,modelOptions:select.options.length,
            model:select.value,providerOptions:document.getElementById('llmProviderSelect').options.length,noticeHidden:getComputedStyle(document.getElementById('youtubeCookieNotice')).display==='none',
            fields:fields.map(el=>({x:el.getBoundingClientRect().x,width:el.getBoundingClientRect().width,color:getComputedStyle(el).color,background:getComputedStyle(el).backgroundColor}))};
        });
        if (!state.active || state.opacity !== '1' || state.overflow || !state.modelOptions || !state.providerOptions || !state.noticeHidden
          || state.fields.some(field=>field.x<0 || field.x+field.width>width || field.width<100)) throw new Error('Settings layout check failed: '+JSON.stringify(state));
        checks.push({theme,...state});
        await page.screenshot({path:path.join(folder,`settings-${theme}-${width}.png`)});
      }
    }
    fs.writeFileSync(path.join(folder,'settings-browser-checks.json'),JSON.stringify(checks,null,2));
    console.log('Settings browser layout passed: two themes, three window sizes');
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
