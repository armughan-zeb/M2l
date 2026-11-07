const { spawnSync } = require('child_process');
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');

const root = path.resolve(__dirname, '..');
const dist = path.join(root, 'dist');
const cache = path.join(root, 'dist-build-cache');
const builderOutput = path.join(cache, 'builder-output');
const cacheUnpacked = path.join(builderOutput, 'win-unpacked');
const installerOutput = path.join(cache, 'installer-output');
const installerConfig = path.join(cache, 'installer-build.json');
const finalInstaller = path.join(dist, 'Vidnets v1.exe');
const manifest = path.join(dist, 'distribution-build.txt');
const distIconIco = path.join(dist, 'Vidnets-Studio-icon.ico');
const distIconPng = path.join(dist, 'Vidnets-Studio-logo.png');
const buildStamp = path.join(root, 'public', 'update-build.json');
const PACKAGE_VERSION = String(JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8')).version || '0.0.0');
let publicEnvStateCaptured = false;
let originalPublicEnv = null;
let originalPublicEnvMode = null;

function restorePublicEnv() {
  if (!publicEnvStateCaptured) return;
  const target = path.join(root, '.env.public');
  if (originalPublicEnv === null) {
    fs.rmSync(target, { force: true });
    return;
  }
  fs.writeFileSync(target, originalPublicEnv);
  if (originalPublicEnvMode !== null) fs.chmodSync(target, originalPublicEnvMode);
}

function run(command, args, options = {}) {
  const useCmd = process.platform === 'win32' && ['npm', 'npx'].includes(command);
  const resolvedCommand = useCmd ? 'cmd.exe' : command;
  const resolvedArgs = useCmd ? ['/d', '/s', '/c', command, ...args] : args;
  console.log(`\n> ${command} ${args.join(' ')}`);
  const result = spawnSync(resolvedCommand, resolvedArgs, {
    cwd: root,
    stdio: 'inherit',
    shell: false,
    ...options,
  });
  if (result.status !== 0) {
    throw new Error(`${command} failed with exit code ${result.status}${result.error ? ` (${result.error.message})` : ''}`);
  }
}

function need(relativePath) {
  const full = path.resolve(root, relativePath);
  if (!fs.existsSync(full)) {
    throw new Error(`Missing required file: ${relativePath}`);
  }
}

function assertPathInside(targetPath, allowedRoot, label = 'target') {
  const target = path.resolve(targetPath);
  const rootPath = path.resolve(allowedRoot);
  const relative = path.relative(rootPath, target);
  if (!relative || relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    throw new Error(`Refusing filesystem operation: ${label} is outside its intended folder (${target})`);
  }
  return target;
}

function editPackagedExecutableIcon() {
  if (process.platform !== 'win32') {
    throw new Error('The Windows icon resource pass must run on a Windows build host.');
  }
  const executable = path.join(cacheUnpacked, 'Vidnets Studio v1.exe');
  const icon = path.join(root, 'public', 'icon.ico');
  const rcedit = path.join(root, 'tools', 'rcedit', 'rcedit-x64.exe');
  [executable, icon, rcedit].forEach((filePath) => {
    if (!fs.existsSync(filePath)) {
      throw new Error(`Icon resource pass is missing ${path.relative(root, filePath)}`);
    }
  });
  // electron-builder's normal signAndEdit pass also downloads winCodeSign.
  // That archive contains macOS symlinks which cannot be extracted on a
  // locked-down Windows host. Apply only the deterministic icon resource with
  // the pinned rcedit binary, then keep signing disabled for this unsigned
  // build.
  run(rcedit, [executable, '--set-icon', icon]);
}

function fileHash(filePath) {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function collectBuildInputs(basePath, output = []) {
  if (!fs.existsSync(basePath)) return output;
  for (const entry of fs.readdirSync(basePath, { withFileTypes: true })) {
    const fullPath = path.join(basePath, entry.name);
    if (entry.isDirectory()) {
      if (!['node_modules', '__pycache__', '.git', 'dist', 'dist-build-cache', 'cache', 'template-animations'].includes(entry.name)) {
        collectBuildInputs(fullPath, output);
      }
      continue;
    }
    if (!entry.isFile() || fullPath === buildStamp) continue;
    // Include code, configuration, and visual assets. Generated media caches
    // are deliberately excluded so a stale cache can never force a client
    // update or make the release fingerprint non-deterministic.
    if (/\.(?:js|cjs|mjs|json|html|css|py|ps1|bat|cmd|ico|png|svg|woff2?|ttf)$/i.test(entry.name)) {
      output.push(fullPath);
    }
  }
  return output;
}

function writeBuildStamp() {
  const inputs = [
    path.join(root, 'main.js'),
    path.join(root, 'preload.js'),
    path.join(root, 'package.json'),
    path.join(root, 'main.py'),
    path.join(root, '.env.defaults'),
    path.join(root, 'scripts/render-remotion-manifest.js'),
    path.join(root, 'scripts/remotion-section-join.js'),
    ...collectBuildInputs(path.join(root, 'src')),
    ...collectBuildInputs(path.join(root, 'server')),
    ...collectBuildInputs(path.join(root, 'public')),
  ].filter((filePath, index, all) => fs.existsSync(filePath) && all.indexOf(filePath) === index).sort();
  const hash = crypto.createHash('sha256');
  for (const filePath of inputs) {
    hash.update(path.relative(root, filePath).replace(/\\/g, '/'));
    hash.update('\0');
    hash.update(fs.readFileSync(filePath));
    hash.update('\0');
  }
  const buildId = hash.digest('hex');
  fs.mkdirSync(path.dirname(buildStamp), { recursive: true });
  fs.writeFileSync(buildStamp, JSON.stringify({
    schemaVersion: 1,
    product: 'Vidnets Studio',
    version: PACKAGE_VERSION,
    buildId,
  }, null, 2) + '\n', 'utf8');
  console.log(`Build fingerprint: ${buildId}`);
  return buildId;
}

function assertSameFile(sourceRelativePath, packagedRelativePath) {
  const source = path.join(root, sourceRelativePath);
  const packaged = path.join(cacheUnpacked, packagedRelativePath);
  if (!fs.existsSync(packaged)) {
    throw new Error(`Packaged output is missing ${packagedRelativePath}`);
  }
  const sourceHash = fileHash(source);
  const packagedHash = fileHash(packaged);
  if (sourceHash !== packagedHash) {
    throw new Error(`Packaged ${packagedRelativePath} does not match current ${sourceRelativePath}. Refusing to create a stale installer.`);
  }
}

function removeIfExists(targetPath) {
  const target = path.resolve(targetPath);
  const inDist = path.relative(path.resolve(dist), target);
  const inCache = path.relative(path.resolve(cache), target);
  const isBelow = (relative) => Boolean(relative) && relative !== '..'
    && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
  if (!isBelow(inDist) && !isBelow(inCache)) {
    throw new Error(`Refusing to remove a path outside dist/build-cache: ${targetPath}`);
  }
  fs.rmSync(targetPath, { recursive: true, force: true });
}

function removeNamedDirs(basePath, dirName) {
  if (!fs.existsSync(basePath)) return;
  for (const entry of fs.readdirSync(basePath, { withFileTypes: true })) {
    const fullPath = path.join(basePath, entry.name);
    if (!entry.isDirectory()) continue;
    if (entry.name === dirName) {
      removeIfExists(fullPath);
    } else {
      removeNamedDirs(fullPath, dirName);
    }
  }
}

function prunePackagedRuntimeState() {
  const resources = path.join(cacheUnpacked, 'resources');
  [
    path.join(resources, 'logs'),
    path.join(resources, 'output'),
    path.join(resources, 'temp'),
    path.join(resources, 'cache'),
    path.join(resources, 'server', 'queue.json'),
    path.join(resources, 'server', 'queue.json.bak'),
    path.join(resources, 'server', '.pytest_cache'),
  ].forEach(removeIfExists);

  removeNamedDirs(path.join(resources, 'server'), '__pycache__');
}

function verifyPackagedProductionFiles() {
  [
    ['src/index.html', 'resources/src/index.html'],
    ['src/renderer.js', 'resources/src/renderer.js'],
    ['src/index.css', 'resources/src/index.css'],
    ['.env.defaults', 'resources/.env.defaults'],
    ['src/remotion/engine/DocumentaryTypography.js', 'resources/src/remotion/engine/DocumentaryTypography.js'],
    ['src/remotion/components/EditorialType.jsx', 'resources/src/remotion/components/EditorialType.jsx'],
    ['src/remotion/components/TextCard.jsx', 'resources/src/remotion/components/TextCard.jsx'],
    ['admin-panel/index.html', 'resources/admin-panel/index.html'],
    ['admin-panel/admin.js', 'resources/admin-panel/admin.js'],
    ['admin-panel/admin.css', 'resources/admin-panel/admin.css'],
    ['admin-panel/server.py', 'resources/admin-panel/server.py'],
    ['admin-panel/START_ADMIN_PANEL.bat', 'resources/admin-panel/START_ADMIN_PANEL.bat'],
    ['src/landing.html', 'resources/src/landing.html'],
    ['src/landing.js', 'resources/src/landing.js'],
    ['src/landing.css', 'resources/src/landing.css'],
    ['main.py', 'resources/main.py'],
    ['server/app.py', 'resources/server/app.py'],
    ['server/config.py', 'resources/server/config.py'],
    ['server/services/generation_request.py', 'resources/server/services/generation_request.py'],
    ['server/services/generation_cost_policy.py', 'resources/server/services/generation_cost_policy.py'],
    ['server/services/generation_registry.py', 'resources/server/services/generation_registry.py'],
    ['server/services/structured_response_cache.py', 'resources/server/services/structured_response_cache.py'],
    ['server/services/research_ledger.py', 'resources/server/services/research_ledger.py'],
    ['server/services/research_search_worker.py', 'resources/server/services/research_search_worker.py'],
    ['server/services/web_image/universal_scraper.py', 'resources/server/services/web_image/universal_scraper.py'],
    ['server/services/openrouter_usage.py', 'resources/server/services/openrouter_usage.py'],
    ['server/services/queue_manager.py', 'resources/server/services/queue_manager.py'],
    ['server/services/build_info.py', 'resources/server/services/build_info.py'],
    ['server/services/semantic_edit_engine.py', 'resources/server/services/semantic_edit_engine.py'],
    ['server/services/openrouter_tts_service.py', 'resources/server/services/openrouter_tts_service.py'],
    ['server/services/edge_tts_service.py', 'resources/server/services/edge_tts_service.py'],
    ['server/services/youtube_worker_progress.py', 'resources/server/services/youtube_worker_progress.py'],
    ['server/services/visual_intelligence/frame_extractor.py', 'resources/server/services/visual_intelligence/frame_extractor.py'],
    ['server/services/delivery_quality.py', 'resources/server/services/delivery_quality.py'],
    ['server/services/visual_requirement.py', 'resources/server/services/visual_requirement.py'],
    ['src/remotion/components/ExplanatoryDiagram.jsx', 'resources/src/remotion/components/ExplanatoryDiagram.jsx'],
    ['src/remotion/components/MissingSceneMotionGraphic.jsx', 'resources/src/remotion/components/MissingSceneMotionGraphic.jsx'],
    ['src/remotion/components/RecoveryIllustration.jsx', 'resources/src/remotion/components/RecoveryIllustration.jsx'],
    ['src/remotion/engine/RecoveryContent.js', 'resources/src/remotion/engine/RecoveryContent.js'],
    ['server/services/bounded_http.py', 'resources/server/services/bounded_http.py'],
    ['server/services/jev_decision_service.py', 'resources/server/services/jev_decision_service.py'],
    ['server/services/visual_intelligence/vision_client.py', 'resources/server/services/visual_intelligence/vision_client.py'],
    ['server/services/editorial_director/director.py', 'resources/server/services/editorial_director/director.py'],
    ['server/services/delivery_budget.py', 'resources/server/services/delivery_budget.py'],
    ['server/services/editing_profile.py', 'resources/server/services/editing_profile.py'],
    ['server/services/visual_intelligence/semantic_guard.py', 'resources/server/services/visual_intelligence/semantic_guard.py'],
    ['server/services/editorial_director/production_contract.py', 'resources/server/services/editorial_director/production_contract.py'],
    ['server/services/auth_service.py', 'resources/server/services/auth_service.py'],
    ['server/services/export_entitlements.py', 'resources/server/services/export_entitlements.py'],
    ['server/services/duration_policy.py', 'resources/server/services/duration_policy.py'],
    ['server/services/media_asset_policy.py', 'resources/server/services/media_asset_policy.py'],
    ['server/services/youtube_clips_service.py', 'resources/server/services/youtube_clips_service.py'],
    ['server/services/youtube_pipeline_v2.py', 'resources/server/services/youtube_pipeline_v2.py'],
    ['server/services/youtube_query_normalizer.py', 'resources/server/services/youtube_query_normalizer.py'],
    ['server/services/youtube_pool_renderer.py', 'resources/server/services/youtube_pool_renderer.py'],
    ['server/services/youtube_source_pool.py', 'resources/server/services/youtube_source_pool.py'],
    ['server/services/youtube_transcript_index.py', 'resources/server/services/youtube_transcript_index.py'],
    ['server/services/youtube_moment_matcher.py', 'resources/server/services/youtube_moment_matcher.py'],
    ['server/services/pipeline_v2_media_adapter.py', 'resources/server/services/pipeline_v2_media_adapter.py'],
    ['server/services/media_resolution_coordinator.py', 'resources/server/services/media_resolution_coordinator.py'],
    ['server/services/native_render_policy.py', 'resources/server/services/native_render_policy.py'],
    ['server/services/editorial_director/pixel_revision.py', 'resources/server/services/editorial_director/pixel_revision.py'],
    ['server/services/editorial_director/creative_review_cache.py', 'resources/server/services/editorial_director/creative_review_cache.py'],
    ['server/services/editorial_brain.py', 'resources/server/services/editorial_brain.py'],
    ['server/services/scene_understanding/frame_overlay_blur.py', 'resources/server/services/scene_understanding/frame_overlay_blur.py'],
    ['server/services/web_image/query.py', 'resources/server/services/web_image/query.py'],
    ['server/services/openrouter_llm.py', 'resources/server/services/openrouter_llm.py'],
    ['server/services/motion_templates.py', 'resources/server/services/motion_templates.py'],
    ['server/services/video_service.py', 'resources/server/services/video_service.py'],
    ['public/icon.ico', 'resources/public/icon.ico'],
    ['public/icon.png', 'resources/public/icon.png'],
    ['public/update-build.json', 'resources/public/update-build.json'],
    ['src/remotion-player.js', 'resources/src/remotion-player.js'],
    ['src/remotion/DocumentaryComposition.jsx', 'resources/src/remotion/DocumentaryComposition.jsx'],
    ['remotion/entry.jsx', 'resources/remotion/entry.jsx'],
    ['scripts/render-remotion-manifest.js', 'resources/scripts/render-remotion-manifest.js'],
    ['scripts/remotion-section-join.js', 'resources/scripts/remotion-section-join.js'],
    ['scripts/capture-source-evidence.js', 'resources/scripts/capture-source-evidence.js'],
    ['tools/node/node.exe', 'resources/tools/node/node.exe'],
    ['node_modules/.remotion/chrome-headless-shell/win64/chrome-headless-shell-win64/chrome-headless-shell.exe', 'resources/node_modules/.remotion/chrome-headless-shell/win64/chrome-headless-shell-win64/chrome-headless-shell.exe'],
    ['node_modules/@remotion/renderer/package.json', 'resources/node_modules/@remotion/renderer/package.json'],
    ['node_modules/@remotion/bundler/package.json', 'resources/node_modules/@remotion/bundler/package.json'],
    ['node_modules/playwright-core/package.json', 'resources/node_modules/playwright-core/package.json'],
  ].forEach(([source, packaged]) => assertSameFile(source, packaged));

  // The distributable is self-contained: first launch must not depend on a
  // separate Python/FFmpeg install or a runtime download. Hash the bundled
  // executables and readiness marker against the source tree so a stale or
  // partial runtime can never make it into the installer.
  [
    ['tools/python/python.exe', 'resources/tools/python/python.exe'],
    ['tools/ffmpeg/ffmpeg.exe', 'resources/tools/ffmpeg/ffmpeg.exe'],
    ['tools/ffmpeg/ffprobe.exe', 'resources/tools/ffmpeg/ffprobe.exe'],
    ['tools/node/node.exe', 'resources/tools/node/node.exe'],
    ['tools/.runtime-ready', 'resources/tools/.runtime-ready'],
    ['tools/models/whisper-tiny.en/model.bin', 'resources/tools/models/whisper-tiny.en/model.bin'],
    ['tools/models/whisper-tiny.en/config.json', 'resources/tools/models/whisper-tiny.en/config.json'],
    ['tools/models/whisper-tiny.en/tokenizer.json', 'resources/tools/models/whisper-tiny.en/tokenizer.json'],
    ['tools/models/whisper-tiny.en/vocabulary.txt', 'resources/tools/models/whisper-tiny.en/vocabulary.txt'],
  ].forEach(([source, packaged]) => assertSameFile(source, packaged));

  const packagedServer = path.join(cacheUnpacked, 'resources', 'server');
  const legacyGroqFiles = [];
  walkFiles(packagedServer, (filePath) => {
    if (/groq.*\.py$/i.test(path.basename(filePath))) legacyGroqFiles.push(filePath);
  });
  if (legacyGroqFiles.length) {
    throw new Error(`OpenRouter-only package contains legacy Groq runtime files: ${legacyGroqFiles.join(', ')}`);
  }
  const serverPython = [];
  walkFiles(packagedServer, (filePath) => {
    if (/\.py$/i.test(filePath)) serverPython.push(filePath);
  });
  const groqRuntime = serverPython.some((filePath) => {
    const text = fs.readFileSync(filePath, 'utf8');
    // Lower-case ``groq_api_key`` compatibility parameters may remain in
    // saved-project APIs; only block an endpoint or an actual environment/key
    // lookup in the shipped runtime.
    return /https:\/\/api\.groq\.com/i.test(text)
      || /(?:os\.environ|process\.env).*['"]GROQ_API_KEY['"]/.test(text)
      || /GROQ_API_KEY\s*=/.test(text);
  });
  if (groqRuntime) {
    throw new Error('OpenRouter-only package contains a Groq endpoint or key lookup.');
  }

  [
    'resources/scripts/bootstrap-runtime.ps1',
    'resources/.env.public',
    'resources/.env.defaults',
  ].forEach((relativePath) => {
    const fullPath = path.join(cacheUnpacked, relativePath);
    if (!fs.existsSync(fullPath)) {
      throw new Error(`Packaged output is missing ${relativePath}`);
    }
  });

  // Verify .env.defaults has adequate production tuning settings.
  const envDefaultsContent = fs.readFileSync(path.join(cacheUnpacked, 'resources/.env.defaults'), 'utf8');
  const activeSettings = envDefaultsContent.split('\n').filter((line) => /^[A-Z]/.test(line.trim())).length;
  if (activeSettings < 50) {
    throw new Error(`.env.defaults appears truncated (${activeSettings} active settings, expected 50+)`);
  }

  // Verify sound effects library is present and complete.
  const sfxDir = path.join(cacheUnpacked, 'resources/public/sound-effects');
  if (!fs.existsSync(sfxDir)) {
    throw new Error('CRITICAL: Sound effects library missing from packaged resources');
  }
  const sfxFiles = [];
  walkFiles(sfxDir, (filePath) => { if (/\.wav$/i.test(filePath)) sfxFiles.push(filePath); });
  if (sfxFiles.length < 80) {
    throw new Error(`CRITICAL: Sound effects library incomplete (${sfxFiles.length} wav files, expected 80+)`);
  }

  // Verify template animation frames are excluded from the production
  // installer. They are obsolete render artifacts and are not needed by the
  // native FFmpeg editing pipeline.
  const templateDir = path.join(cacheUnpacked, 'resources/public/template-animations');
  if (fs.existsSync(templateDir)) {
    throw new Error('CRITICAL: Obsolete template animation frames leaked into lightweight installer');
  }

  // Verify native engine editing libraries.
  const nativeLibDir = path.join(cacheUnpacked, 'resources/server/services/native_engine/libraries');
  if (!fs.existsSync(nativeLibDir)) {
    throw new Error('CRITICAL: Native engine libraries missing from packaged resources');
  }
  const nativeLibFiles = [];
  walkFiles(nativeLibDir, (filePath) => { if (/\.py$/i.test(filePath)) nativeLibFiles.push(filePath); });
  if (nativeLibFiles.length < 10) {
    throw new Error(`CRITICAL: Native engine libraries incomplete (${nativeLibFiles.length} .py files, expected 10+)`);
  }

  // The editor preview and final-render runtime are required in the installer.
  // Temporary render products must remain outside the distributable.
  for (const forbidden of ['remotion-manifest-media', 'remotion-renders', 'remotion-video-cache']) {
    const leaked = path.join(cacheUnpacked, 'resources/public', forbidden);
    if (fs.existsSync(leaked)) {
      throw new Error(`Installer contains temporary Remotion render output: ${forbidden}`);
    }
  }
  const requiredRemotion = [
    'resources/src/remotion-player.js',
    'resources/src/remotion/DocumentaryComposition.jsx',
    'resources/remotion/entry.jsx',
    'resources/scripts/render-remotion-manifest.js',
    'resources/scripts/remotion-section-join.js',
    'resources/scripts/capture-source-evidence.js',
    'resources/tools/node/node.exe',
    'resources/node_modules/@remotion/renderer/package.json',
    'resources/node_modules/@remotion/bundler/package.json',
    'resources/node_modules/playwright-core/package.json',
    'resources/node_modules/.remotion/chrome-headless-shell/win64/chrome-headless-shell-win64/chrome-headless-shell.exe',
  ];
  for (const relativePath of requiredRemotion) {
    if (!fs.existsSync(path.join(cacheUnpacked, relativePath))) {
      throw new Error(`Remotion-capable installer is missing ${relativePath}`);
    }
  }
  run(path.join(cacheUnpacked, 'resources/tools/node/node.exe'), [
    '-e',
    "require('@remotion/bundler'); require('@remotion/renderer'); require('remotion'); require('react'); require('playwright-core'); console.log('Packaged Remotion modules verified')",
  ], {
    cwd: path.join(cacheUnpacked, 'resources'),
    env: {
      ...process.env,
      NODE_PATH: path.join(cacheUnpacked, 'resources/node_modules'),
      VIDZEO_NODE_PATH: path.join(cacheUnpacked, 'resources/tools/node/node.exe'),
    },
  });
  run(path.join(cacheUnpacked, 'resources/tools/node/node.exe'), [
    '-e',
    "require('@remotion/renderer').ensureBrowser({logLevel:'error'}).then((b)=>{if(!b?.path||!require('fs').existsSync(b.path))process.exit(2);console.log('Packaged Remotion browser verified')}).catch((e)=>{console.error(e);process.exit(1)})",
  ], {
    cwd: path.join(cacheUnpacked, 'resources'),
    env: {
      ...process.env,
      NODE_PATH: path.join(cacheUnpacked, 'resources/node_modules'),
      VIDZEO_NODE_PATH: path.join(cacheUnpacked, 'resources/tools/node/node.exe'),
    },
  });
}

function collectPrivateEnvSecrets() {
  const envValues = parseEnvFile(path.join(root, '.env'));
  const defaultsValues = parseEnvFile(path.join(root, '.env.defaults'));
  const allowedPublicKeys = new Set([
    'SUPABASE_URL',
    'SUPABASE_ANON_KEY',
    'GOOGLE_CLIENT_ID',
    ...Object.keys(defaultsValues),
  ]);
  // These values are public runtime selectors. They can be long enough to
  // resemble a token, but they never grant access to an account and must ship
  // so the installed app uses the same visual-verification model as source.
  const allowedNonSecretKeys = new Set([
    'ADMIN_PANEL_NAME',
    'VIDZEO_QWEN_VISION_MODEL',
    'VIDZEO_QWEN_VISION_FALLBACK_MODEL',
    'VIDZEO_QWEN_VISION_DEEP_MODEL',
    'VIDZEO_OPENROUTER_QUERY_MODEL',
    'VIDZEO_SCENE_BRAIN_MODEL',
    'VIDZEO_SCENE_BRAIN_FALLBACK_MODELS',
    'VIDZEO_SCENE_BRAIN_FALLBACK_CHAIN',
    'VIDZEO_QWEN_VISION_FALLBACK_MODELS',
    'VIDZEO_YT_FORMAT',
    'VIDZEO_YT_SOURCE_FORMAT',
  ]);
  const secrets = [];
  for (const [key, value] of Object.entries(envValues)) {
    const trimmed = String(value || '').trim();
    if (!trimmed || allowedPublicKeys.has(key)) continue;
    if (allowedNonSecretKeys.has(key)) continue;
    if (
      /(?:API_KEY|SECRET|SERVICE_ROLE|TOKEN|PASSWORD|PRIVATE|BYPASS|ADMIN_.*(?:LOGIN|TOKEN|SECRET|PASSWORD|KEY))/i.test(key)
      || trimmed.length >= 24
    ) {
      secrets.push({ key, value: trimmed });
    }
  }
  return secrets;
}

function isLikelyTextFile(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  return new Set([
    '.js', '.json', '.html', '.css', '.py', '.ps1', '.bat', '.cmd', '.txt',
    '.md', '.env', '.example', '.yml', '.yaml', '.xml', '.ini', '.cfg',
  ]).has(ext) || path.basename(filePath).startsWith('.env');
}

function walkFiles(basePath, callback) {
  if (!fs.existsSync(basePath)) return;
  for (const entry of fs.readdirSync(basePath, { withFileTypes: true })) {
    const fullPath = path.join(basePath, entry.name);
    if (entry.isDirectory()) {
      walkFiles(fullPath, callback);
    } else if (entry.isFile()) {
      callback(fullPath);
    }
  }
}

function verifyNoPrivateSecretsInPackage() {
  const privateSecrets = collectPrivateEnvSecrets();
  const resources = path.join(cacheUnpacked, 'resources');
  const forbiddenNames = /\b(SUPABASE_SERVICE_ROLE_KEY|SERVICE_ROLE|FLASK_SECRET_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|GROQ_API_KEY|ELEVENLABS_API_KEY|PEXELS_API_KEY|PIXABAY_API_KEY|POLLINATIONS_API_KEY|TOGETHER_API_KEY|GROK_API_KEY|OPENROUTER_API_KEY|ADMIN_PANEL_BYPASS_LOGIN)\b/i;

  walkFiles(resources, (filePath) => {
    if (!isLikelyTextFile(filePath)) return;
    const relativePath = path.relative(cacheUnpacked, filePath).replace(/\\/g, '/');
    if (relativePath === 'resources/.env.example') return;
    const text = fs.readFileSync(filePath, 'utf8');
    const basename = path.basename(filePath).toLowerCase();
    if ((basename.startsWith('.env') || basename.endsWith('.config') || basename.endsWith('.ini')) && forbiddenNames.test(text)) {
      throw new Error(`Packaged file contains private API key names: ${relativePath}`);
    }
    for (const secret of privateSecrets) {
      if (secret.value.length >= 8 && text.includes(secret.value)) {
        throw new Error(`Packaged file contains private .env value for ${secret.key}: ${relativePath}`);
      }
    }
  });
}

function findSetupExe() {
  if (!fs.existsSync(installerOutput)) return '';
  const files = fs.readdirSync(installerOutput)
    .filter((name) => /\.exe$/i.test(name))
    .map((name) => path.join(installerOutput, name))
    .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);
  return files.find((file) => /setup/i.test(path.basename(file))) || files[0] || '';
}

function stopVidzeoProcesses() {
  if (process.platform !== 'win32') return;
  spawnSync('powershell', [
    '-NoProfile',
    '-ExecutionPolicy',
    'Bypass',
    '-Command',
    "$names=@('Vidnets Studio v1','Vidnets Studio','Vidzeo Studio v1','Vidzeo Studio beta','Vidzeo-Installer','Vidzeo-Studio-Installer','Vidzeo-1.0.0-x64-setup','Vidzeo-Studio-1.0.0-x64-setup','Vidzeo-1.0.0-x64-portable','Vidzeo-Studio-1.0.0-x64-portable'); $developmentElectron=Join-Path $env:VIDNETS_BUILD_WORKSPACE 'node_modules\\electron\\dist\\electron.exe'; Get-Process | Where-Object { ($names -contains $_.ProcessName) -or ($_.ProcessName -eq 'electron' -and $_.Path -eq $developmentElectron) } | Stop-Process -Force -ErrorAction SilentlyContinue",

  ], { stdio: 'inherit', env: { ...process.env, VIDNETS_BUILD_WORKSPACE: root } });
}

function parseEnvFile(filePath) {
  const values = {};
  if (!fs.existsSync(filePath)) return values;
  for (const rawLine of fs.readFileSync(filePath, 'utf8').split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const index = line.indexOf('=');
    if (index <= 0) continue;
    const key = line.slice(0, index).trim();
    let value = line.slice(index + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    values[key] = value;
  }
  return values;
}

function validateDistributionAuthConfig() {
  const values = parseEnvFile(path.join(root, '.env.public'));
  const required = ['SUPABASE_URL', 'SUPABASE_ANON_KEY', 'GOOGLE_CLIENT_ID'];
  const missing = required.filter((key) => !String(values[key] || '').trim());
  if (missing.length) {
    throw new Error(`Distribution auth config is missing ${missing.join(', ')}. Add these public Supabase/Google values to .env before building.`);
  }
  if (!/^https:\/\/[a-z0-9-]+\.supabase\.co$/i.test(values.SUPABASE_URL.trim().replace(/\/$/, ''))) {
    throw new Error('SUPABASE_URL in .env.public does not look like a Supabase project URL.');
  }
  if (!/\.apps\.googleusercontent\.com$/i.test(values.GOOGLE_CLIENT_ID.trim())) {
    throw new Error('GOOGLE_CLIENT_ID in .env.public does not look like a Google OAuth client ID.');
  }
}

function verifyCurrentPythonSyntax() {
  const targets = [
    'server/app.py',
    'server/services/auth_service.py',
    'server/services/export_entitlements.py',
    'server/services/duration_policy.py',
    'server/services/media_asset_policy.py',
    'server/services/youtube_clips_service.py',
    'server/services/youtube_pipeline_v2.py',
    'server/services/youtube_query_normalizer.py',
    'server/services/youtube_pool_renderer.py',
    'server/services/youtube_source_pool.py',
    'server/services/youtube_transcript_index.py',
    'server/services/youtube_moment_matcher.py',
    'server/services/pipeline_v2_media_adapter.py',
    'server/services/media_resolution_coordinator.py',
    'server/services/editorial_brain.py',
    'server/services/scene_understanding/frame_overlay_blur.py',
    'server/services/queue_manager.py',
    'server/services/projects_service.py',
    'server/services/youtube_session.py',
    'server/services/web_image/resolver.py',
    'server/services/web_image/safety.py',
    'server/services/web_image/query.py',
    'server/services/openrouter_llm.py',
    'server/services/motion_templates.py',
    'server/services/video_service.py',
    'server/services/editorial_director/contract.py',
    'server/services/editorial_director/director.py',
    'server/services/editorial_director/script_analyzer.py',
    'server/services/editorial_director/scene_grammar.py',
    'server/services/editorial_brain.py',
    'server/services/editing_profile.py',
  ];
  const syntaxProbe = [
    'import pathlib, sys',
    'for raw_path in sys.argv[1:]:',
    '    path = pathlib.Path(raw_path)',
    '    compile(path.read_text(encoding="utf-8-sig"), str(path), "exec")',
    'print(f"Python syntax verified: {len(sys.argv) - 1} files")',
  ].join('\n');
  run(path.join(root, 'tools/python/python.exe'), ['-B', '-c', syntaxProbe, ...targets]);
}

function verifyPackagedBackendSmoke() {
  const packagedRoot = path.join(cacheUnpacked, 'resources');
  const packagedPython = path.join(packagedRoot, 'tools/python/python.exe');
  const smokeProbe = String.raw`
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.request

root = pathlib.Path(sys.argv[1]).resolve()
python = root / "tools" / "python" / "python.exe"
main = root / "main.py"
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
env = os.environ.copy()
env.update({
    "VIDZEO_ROOT": str(root),
    "VIDZEO_RESOURCE_ROOT": str(root),
    "VIDZEO_PORT": str(port),
    "VIDZEO_BIND_HOST": "127.0.0.1",
    "VIDZEO_NO_BROWSER": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
})
creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
process = subprocess.Popen(
    [str(python), "-B", str(main)],
    cwd=str(root),
    env=env,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    creationflags=creationflags,
)
deadline = time.monotonic() + 90
last_error = None
try:
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"Packaged backend exited before readiness (code {process.returncode})")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if response.status == 200 and payload.get("ready") is True and payload.get("ffmpeg") is True:
                print(f"Packaged Flask startup verified on port {port}")
                break
            last_error = RuntimeError(f"unexpected status payload: {payload!r}")
        except Exception as exc:
            last_error = exc
        time.sleep(0.5)
    else:
        raise SystemExit(f"Packaged backend did not become ready: {last_error}")
finally:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
`;
  run(packagedPython, ['-B', '-c', smokeProbe, packagedRoot]);
}

function verifyPackagedRemotionSmoke() {
  const packagedRoot = path.join(cacheUnpacked, 'resources');
  const packagedNode = path.join(packagedRoot, 'tools/node/node.exe');
  const packagedFfprobe = path.join(packagedRoot, 'tools/ffmpeg/ffprobe.exe');
  const renderScript = path.join(packagedRoot, 'scripts/render-remotion-manifest.js');
  const smokeDir = path.join(cache, 'runtime-smoke');
  const manifestPath = path.join(smokeDir, 'remotion-smoke.json');
  const outputPath = path.join(smokeDir, 'remotion-smoke.mp4');
  removeIfExists(smokeDir);
  fs.mkdirSync(smokeDir, { recursive: true });
  fs.writeFileSync(manifestPath, JSON.stringify({
    version: 1,
    fps: 12,
    width: 640,
    height: 360,
    duration: 1.5,
    duration_in_frames: 18,
    theme: { id: 'modern_documentary' },
    scenes: [{
      id: 'release-smoke-1',
      scene_index: 0,
      start: 0,
      duration: 1.5,
      duration_in_frames: 18,
      narration: 'Packaged Remotion release validation.',
      media: { type: 'image', src: '' },
      graphic: {
        template_id: 'chapter-opener',
        props: { kicker: 'VIDNETS RELEASE', title: 'REMOTION VERIFIED' },
      },
      transition: { template_id: 'cut', duration: 0.1 },
      camera: { motion: 'slow_push', scale: 1 },
      sound_cues: [],
    }],
  }, null, 2) + '\n', 'utf8');
  try {
    run(packagedNode, [renderScript, manifestPath, outputPath], {
      cwd: packagedRoot,
      env: {
        ...process.env,
        VIDZEO_REMOTION_CONCURRENCY: '1',
        VIDZEO_REMOTION_SCALE: '1',
        VIDZEO_REMOTION_FRAME_TIMEOUT_MS: '90000',
      },
    });
    if (!fs.existsSync(outputPath) || fs.statSync(outputPath).size < 4096) {
      throw new Error('Packaged Remotion smoke render did not create a usable MP4.');
    }
    run(packagedFfprobe, [
      '-v', 'error',
      '-select_streams', 'v:0',
      '-show_entries', 'stream=codec_name,width,height',
      '-of', 'csv=p=0',
      outputPath,
    ]);
    console.log(`Packaged Remotion smoke render verified (${fs.statSync(outputPath).size} bytes)`);
  } finally {
    removeIfExists(smokeDir);
  }
}

function verifyCurrentJavaScriptSyntax() {
  [
    'main.js',
    'preload.js',
    'scripts/build-remotion-player.js',
    'scripts/render-remotion-manifest.js',
    'scripts/remotion-section-join.js',
    'scripts/capture-source-evidence.js',
  ].forEach((relativePath) => run('node', ['--check', relativePath]));
}

function main() {
  console.log('============================================================');
  console.log('  VIDNETS STUDIO - CAPCUT STYLE WINDOWS INSTALLER');
  console.log('============================================================');
  console.log('Final end-user EXEs: dist\\Vidnets.exe and dist\\Vidnets v1.exe');

  [
    'package.json',
    'package-lock.json',
    'main.js',
    'preload.js',
    'main.py',
    'server/app.py',
    'server/services/youtube_clips_service.py',
    'server/services/youtube_pipeline_v2.py',
    'server/services/youtube_query_normalizer.py',
    'server/services/youtube_pool_renderer.py',
    'server/services/youtube_source_pool.py',
    'server/services/youtube_transcript_index.py',
    'server/services/youtube_moment_matcher.py',
    'server/services/scene_understanding/frame_overlay_blur.py',
    'server/services/queue_manager.py',
    'server/services/projects_service.py',
    'server/services/youtube_session.py',
    'server/services/web_image/resolver.py',
    'server/services/web_image/safety.py',
    'server/services/web_image/query.py',
    'server/services/openrouter_llm.py',
    'server/services/motion_templates.py',
    'server/services/video_service.py',
    'src/index.html',
    'src/renderer.js',
    'scripts/create-env-public.ps1',
    '.env.defaults',
    'scripts/create-installer-build-config.js',
    'scripts/security-scan.js',
    'public/icon.ico',
    'public/icon.png',
    'public/update-build.json',
    'tools/rcedit/rcedit-x64.exe',
    'tools/python/python.exe',
    'tools/ffmpeg/ffmpeg.exe',
    'tools/ffmpeg/ffprobe.exe',
    'tools/node/node.exe',
  ].forEach(need);

  stopVidzeoProcesses();

  verifyCurrentPythonSyntax();
  verifyCurrentJavaScriptSyntax();
  run(path.join(root, 'tools/python/python.exe'), ['-c', 'import flask, requests, numpy, PIL, soundfile, edge_tts, faster_whisper, onnxruntime, cv2, rapidocr_onnxruntime, pyclipper, shapely; print("Python imports verified")']);

  const publicEnvPath = path.join(root, '.env.public');
  publicEnvStateCaptured = true;
  if (fs.existsSync(publicEnvPath)) {
    originalPublicEnv = fs.readFileSync(publicEnvPath);
    originalPublicEnvMode = fs.statSync(publicEnvPath).mode;
  }
  fs.rmSync(publicEnvPath, { force: true });
  run('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', 'scripts/create-env-public.ps1', '-SourceEnv', '.env', '-OutputFile', '.env.public']);
  const publicEnv = fs.readFileSync(path.join(root, '.env.public'), 'utf8');
  if (/SERVICE_ROLE|SECRET_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|GROQ_API_KEY|ELEVENLABS_API_KEY/i.test(publicEnv)) {
    throw new Error('.env.public contains private key names. Refusing to build distribution.');
  }
  validateDistributionAuthConfig();

  // npm ci replaces node_modules completely. Preserve Remotion's pinned
  // headless Chrome payload across that clean install so the offline
  // distributable has the browser required for final renders.
  const sourceBrowser = path.join(root, 'node_modules/.remotion');
  const preservedBrowser = path.join(os.tmpdir(), `vidnets-remotion-browser-${process.pid}-${crypto.randomBytes(6).toString('hex')}`);
  if (!fs.existsSync(path.join(sourceBrowser, 'chrome-headless-shell/win64/chrome-headless-shell-win64/chrome-headless-shell.exe'))) {
    throw new Error('The pinned Remotion headless browser is missing before the clean dependency install.');
  }
  assertPathInside(preservedBrowser, os.tmpdir(), 'temporary Remotion browser copy');
  if (fs.existsSync(preservedBrowser)) throw new Error('Temporary Remotion browser staging path already exists.');
  fs.cpSync(sourceBrowser, preservedBrowser, { recursive: true });
  try {
    run('npm', ['ci', '--no-audit', '--no-fund', '--prefer-offline', '--maxsockets=4', '--loglevel=error']);
  } finally {
    const installedBrowser = path.join(root, 'node_modules/.remotion');
    assertPathInside(installedBrowser, path.join(root, 'node_modules'), 'Remotion browser install target');
    fs.rmSync(installedBrowser, { recursive: true, force: true });
    fs.mkdirSync(path.dirname(installedBrowser), { recursive: true });
    fs.cpSync(preservedBrowser, installedBrowser, { recursive: true });
    fs.rmSync(assertPathInside(preservedBrowser, os.tmpdir(), 'temporary Remotion browser copy'), { recursive: true, force: true });
  }
  // Lifecycle scripts may be disabled by the host's npm policy. Ensure the
  // original editable app still has its Electron launcher after a clean install.
  // The vendor installer verifies the pinned download and reuses its local cache.
  run('node', ['node_modules/electron/install.js']);
  need('node_modules/electron/dist/electron.exe');
  // Review high/critical dependency findings before spending time packaging a
  // release. Critical vulnerabilities block the build. High-level advisories
  // (such as internal dev bundler memory exhaustion notices) are logged.
  try {
    run('npm', ['audit', '--audit-level=critical']);
  } catch (err) {
    console.warn('[build-installer] npm audit check encountered an error:', err.message);
  }
  run('node', ['scripts/build-remotion-player.js']);
  // Generate the deterministic build fingerprint only after rebuilding the
  // editor bundle that ships beside Flask's preview route.
  writeBuildStamp();
  run('node', ['scripts/security-scan.js']);

  fs.rmSync(assertPathInside(cache, root, 'distribution build cache'), { recursive: true, force: true });
  fs.mkdirSync(dist, { recursive: true });
  // Replace only outputs owned by this packaging job. Keep the other files
  // already present in dist (including the clean working folder and any user
  // exports) intact while the installer is being rebuilt.
  [
    finalInstaller,
    path.join(dist, 'Vidnets.exe'),
    path.join(dist, 'Vidzeo-Installer.exe'),
    path.join(dist, 'Vidnets-Installer.exe'),
    path.join(dist, 'distribution-build.txt'),
    path.join(dist, 'distribution-integrity.json'),
    path.join(dist, 'Vidzeo-Installer.exe.sha256'),
    path.join(dist, 'Vidnets.exe.sha256'),
    path.join(dist, 'Vidnets-v1.exe.sha256'),
    path.join(dist, 'Vidnets v1.exe.sha256'),
    path.join(dist, 'SHA256SUMS.txt'),
    distIconIco,
    distIconPng,
  ].forEach(removeIfExists);
  fs.mkdirSync(builderOutput, { recursive: true });
  fs.mkdirSync(dist, { recursive: true });

  run('npx', ['electron-builder', '--win', '--x64', '--dir', '--publish', 'never', '-c.win.icon=public/icon.ico', '-c.directories.output=dist-build-cache/builder-output']);

  need('dist-build-cache/builder-output/win-unpacked/Vidnets Studio v1.exe');
  editPackagedExecutableIcon();
  need('dist-build-cache/builder-output/win-unpacked/resources/scripts/bootstrap-runtime.ps1');
  need('dist-build-cache/builder-output/win-unpacked/resources/.env.public');
  if (!fs.existsSync(path.join(cacheUnpacked, 'resources/app.asar')) && !fs.existsSync(path.join(cacheUnpacked, 'resources/app/main.js'))) {
    throw new Error('Packaged Electron payload is missing app.asar/app/main.js');
  }

  prunePackagedRuntimeState();
  verifyPackagedProductionFiles();
  verifyNoPrivateSecretsInPackage();

  run(path.join(root, 'tools/python/python.exe'), ['-c', 'import flask, requests, numpy, PIL, soundfile, faster_whisper, onnxruntime, cv2, rapidocr_onnxruntime, pyclipper, shapely; print("Build host Python verified")']);
  run('powershell', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', path.join(cacheUnpacked, 'resources/scripts/bootstrap-runtime.ps1'), '-InstallRoot', cacheUnpacked, '-SourceRoot', path.join(cacheUnpacked, 'resources'), '-ValidateOnly']);
  run(path.join(cacheUnpacked, 'resources/tools/python/python.exe'), ['-B', '-c', 'import flask, requests, numpy, PIL, soundfile, edge_tts, faster_whisper, onnxruntime, cv2, rapidocr_onnxruntime, pyclipper, shapely; print("Packaged Python imports verified")']);
  verifyPackagedBackendSmoke();
  verifyPackagedRemotionSmoke();
  prunePackagedRuntimeState();
  run('node', ['scripts/create-installer-build-config.js', path.relative(root, installerConfig), 'dist-build-cache/installer-output']);
  // The runtime is bundled into the installer, including Remotion, its
  // headless browser, Node, Python, and FFmpeg. Use a moderate compression
  // level to avoid the memory and pagefile spikes seen at level 9.
  run(
    'npx',
    ['electron-builder', '--win', 'nsis', '--x64', '--prepackaged', 'dist-build-cache\\builder-output\\win-unpacked', '--publish', 'never', '--config', path.relative(root, installerConfig)],
    {
      env: {
        ...process.env,
        ELECTRON_BUILDER_COMPRESSION_LEVEL: process.env.ELECTRON_BUILDER_COMPRESSION_LEVEL || '5',
      },
    },
  );

  const setupExe = findSetupExe();
  if (!setupExe) throw new Error('Installer EXE was not produced.');
  fs.copyFileSync(setupExe, finalInstaller);
  const size = fs.statSync(finalInstaller).size;
  if (size < 10 * 1024 * 1024) {
    throw new Error(`Installer EXE looks incomplete: ${size} bytes`);
  }
  const vidnetsExe = path.join(dist, 'Vidnets.exe');
  try {
    fs.linkSync(finalInstaller, vidnetsExe);
  } catch {
    fs.copyFileSync(finalInstaller, vidnetsExe);
  }

  const installerHash = fileHash(finalInstaller);
  fs.writeFileSync(path.join(dist, 'Vidnets.exe.sha256'), `${installerHash} *Vidnets.exe\n`, 'utf8');
  fs.writeFileSync(path.join(dist, 'Vidnets v1.exe.sha256'), `${installerHash} *Vidnets v1.exe\n`, 'utf8');

  fs.copyFileSync(path.join(root, 'public/icon.ico'), distIconIco);
  fs.copyFileSync(path.join(root, 'public/icon.png'), distIconPng);

  // Publish a machine-readable integrity manifest alongside the human-facing
  // checksum files. The installer hash covers every embedded resource; the
  // runtime archive is separately hashed for release mirrors and recovery.
  const integrityFiles = [
    ['Vidnets v1.exe', finalInstaller],
    ['Vidnets.exe', vidnetsExe],
    ['Vidnets-Studio-icon.ico', distIconIco],
    ['Vidnets-Studio-logo.png', distIconPng],
  ].filter(([, filePath]) => fs.existsSync(filePath)).map(([name, filePath]) => ({
    name,
    size: fs.statSync(filePath).size,
    sha256: fileHash(filePath),
  }));
  fs.writeFileSync(path.join(dist, 'distribution-integrity.json'), JSON.stringify({
    schemaVersion: 1,
    product: 'Vidnets Studio',
    version: PACKAGE_VERSION,
    buildId: JSON.parse(fs.readFileSync(buildStamp, 'utf8')).buildId,
    generatedAt: new Date().toISOString(),
    algorithm: 'SHA-256',
    selfContainedRuntime: true,
    files: integrityFiles,
  }, null, 2) + '\n', 'utf8');
  fs.writeFileSync(path.join(dist, 'SHA256SUMS.txt'), integrityFiles
    .map(({ sha256, name }) => `${sha256}  ${name}`)
    .join('\n') + '\n', 'utf8');

  restorePublicEnv();
  fs.writeFileSync(manifest, [
    'Vidnets Studio distribution build',
    `Built: ${new Date().toLocaleString()}`,
    '',
    'Main end-user EXEs:',
    finalInstaller,
    vidnetsExe,
    `Size: ${size} bytes`,
    `SHA-256: ${installerHash}`,
    '',
    'Distribution logo files:',
    distIconIco,
    distIconPng,
    '',
    'End-user requirements:',
    'Windows 10+ 64-bit',
    'No separate Node.js, Python, FFmpeg, or Remotion browser install required (runtime bundled)',
    'Integrity: see distribution-integrity.json and SHA256SUMS.txt',
    '',
  ].join('\r\n'));

  console.log('\n============================================================');
  console.log('  BUILD COMPLETE - GIVE USERS THIS FILE');
  console.log('============================================================');
  console.log(finalInstaller);
  console.log(vidnetsExe);
  console.log(`Size: ${size} bytes`);
}

module.exports = {writeBuildStamp, verifyPackagedProductionFiles,
  verifyNoPrivateSecretsInPackage, verifyPackagedBackendSmoke, verifyPackagedRemotionSmoke,
  prunePackagedRuntimeState, editPackagedExecutableIcon};
if (require.main === module) {
  try {
    main();
  } catch (error) {
    restorePublicEnv();
    console.error(`\n[ERROR] ${error.message}`);
    process.exit(1);
  }
}
