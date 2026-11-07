const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const ROOT = path.resolve(__dirname, '..');
const DIST = path.join(ROOT, 'dist');

function run(desc, cmd, cwd) {
    console.log(`\n=== ${desc} ===`);
    console.log(`> ${cmd}`);
    execSync(cmd, { cwd: cwd || ROOT, stdio: 'inherit', shell: true });
}

function main() {
    console.log('============================================================');
    console.log('  VIDNETS STUDIO - Full Distribution Build');
    console.log('============================================================');

    // Step 1: Clean previous dist
    if (fs.existsSync(DIST)) {
        console.log('\nCleaning previous dist/...');
        fs.rmSync(DIST, { recursive: true, force: true });
    }

    // Step 2: Create source product folder (pre-filtered Python packages)
    run('Creating source product folder', 'python create_product_folder.py --source-only');

    // Step 3: Build the Electron app folder
    run('Building Electron app folder (this will install npm deps + compile)', 'python create_product_folder.py');

    // Step 4: Run security scan
    run('Running security scan', 'node scripts/security-scan.js');

    // Step 5: Verify outputs
    const appDir = path.join(DIST, 'Vidnets-Studio-Product-App');
    const appExe = path.join(appDir, 'Vidnets Studio v1.exe');
    const appAsar = path.join(appDir, 'resources', 'app.asar');
    const mainPy = path.join(appDir, 'resources', 'main.py');
    const pythonExe = path.join(appDir, 'resources', 'tools', 'python', 'python.exe');
    const ffmpegExe = path.join(appDir, 'resources', 'tools', 'ffmpeg', 'ffmpeg.exe');

    const checks = [
        ['EXE', appExe],
        ['app.asar', appAsar],
        ['main.py', mainPy],
        ['python.exe', pythonExe],
        ['ffmpeg.exe', ffmpegExe],
    ];

    let allOk = true;
    for (const [label, filePath] of checks) {
        const ok = fs.existsSync(filePath);
        console.log(`  ${ok ? '✓' : '✗'} ${label}: ${filePath}`);
        if (!ok) allOk = false;
    }

    // Verify packaged Python imports
    if (pythonExe && fs.existsSync(pythonExe)) {
        try {
            execSync(
                `"${pythonExe}" -c "import flask, requests, numpy, scipy, PIL, soundfile, faster_whisper, onnxruntime, cv2, rapidocr_onnxruntime, pyclipper, shapely; print('verified')"`,
                { cwd: path.join(appDir, 'resources'), stdio: 'pipe', timeout: 30000 }
            );
            console.log('  ✓ Packaged Python imports verified');
        } catch (e) {
            console.log('  ✗ Packaged Python import verification failed');
            allOk = false;
        }
    }

    console.log('\n============================================================');
    if (allOk) {
        console.log('  BUILD COMPLETE - All checks passed');
        console.log(`  App folder: ${appDir}`);
        console.log('  Run: START_VIDNETS_STUDIO.bat or Vidnets Studio v1.exe');
    } else {

        console.log('  BUILD COMPLETE - Some checks FAILED');
        process.exit(1);
    }
    console.log('============================================================');
}

main();
