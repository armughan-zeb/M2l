const {app, BrowserWindow} = require('electron');
const fs = require('fs');
const path = require('path');
const output = path.resolve(__dirname, '../artifacts/release_validation_20261006');
app.whenReady().then(async () => {
  fs.mkdirSync(output, {recursive: true});
  const win = new BrowserWindow({width: 1440, height: 960, show: false,
    webPreferences: {contextIsolation: true, nodeIntegration: false, backgroundThrottling: false}});
  await win.loadFile(path.resolve(__dirname, '../src/index.html'));
  await new Promise(resolve => setTimeout(resolve, 1800));
  await win.webContents.executeJavaScript(`
    AppState.account.accessState = 'active';
    AccountController.render();
    navigateTo('settings');
  `);
  await new Promise(resolve => setTimeout(resolve, 1400));
  const results = [];
  for (const [width, height] of [[1440,960],[1024,768],[760,720]]) {
    win.setSize(width, height);
    await new Promise(resolve => setTimeout(resolve, 800));
    const result = await win.webContents.executeJavaScript(`(() => {
      const section = document.querySelector('#section-settings');
      const panels = [...section.querySelectorAll('.panel-card')].map(el => {
        const r=el.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};
      });
      const fields = ['openrouterKey','pexelsKey','pixabayKey','llmModelSelect'].map(id => {
        const el=document.getElementById(id),r=el.getBoundingClientRect();
        return {id,x:r.x,width:r.width,display:getComputedStyle(el).display,color:getComputedStyle(el).color,background:getComputedStyle(el).backgroundColor,
          options:el.tagName === 'SELECT' ? el.options.length : null};
      });
      return {width:innerWidth,panels,fields,active:section.classList.contains('active'),opacity:getComputedStyle(section).opacity,cookieNotice:getComputedStyle(document.getElementById('youtubeCookieNotice')).display,
        overflow:document.documentElement.scrollWidth > innerWidth,
        columns:getComputedStyle(section.querySelector('.settings-workspace')).gridTemplateColumns};
    })()`);
    results.push(result);
    fs.writeFileSync(path.join(output, `settings-${width}.png`), (await win.webContents.capturePage()).toPNG());
  }
  fs.writeFileSync(path.join(output, 'settings-layout.json'), JSON.stringify(results,null,2));
  if (results.some(row => !row.active || Number(row.opacity) !== 1 || row.overflow || row.cookieNotice !== 'none' || row.panels.length !== 2 || row.fields.some(field => field.width < 100 || field.x + field.width > row.width || field.options === 0)))
    throw new Error('Settings layout overflow or inaccessible fields');
  console.log('Settings layout passed at 1440, 1024 and 760 pixels');
  win.destroy(); app.quit();
}).catch(error => {console.error(error);app.exit(1);});
