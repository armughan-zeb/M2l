const fs = require('node:fs');
const path = require('node:path');

function defaultCallFf(options) {
  return require(path.join(__dirname, '..', 'node_modules', '@remotion', 'renderer', 'dist', 'call-ffmpeg.js')).callFf(options);
}

async function promoteExport(staged, destination, rename = fs.promises.rename) {
  // Same-volume rename replaces atomically. Never unlink the previous master
  // or copy a partially written replacement over it when Windows holds a lock.
  for (let attempt = 0; ; attempt++) {
    try { await rename(staged, destination); return; }
    catch (error) {
      if (!['EPERM', 'EACCES', 'EBUSY'].includes(error.code) || attempt >= 2) throw error;
      await new Promise(resolve => setTimeout(resolve, 200 * (attempt + 1)));
    }
  }
}

async function joinRemotionSections({videoFiles, audioFiles, outputPath, fps, frames,
  width, height, callFf = defaultCallFf, timeoutMs = 300000}) {
  if (!videoFiles.length || videoFiles.length !== audioFiles.length) {
    throw new Error('Final join requires matching video and audio sections');
  }
  for (const file of [...videoFiles, ...audioFiles]) {
    if (!fs.existsSync(file) || !fs.statSync(file).isFile() || fs.statSync(file).size < 44) {
      throw new Error(`Final join section missing or empty: ${path.basename(file)}`);
    }
  }
  const parent = path.dirname(path.resolve(outputPath));
  fs.mkdirSync(parent, {recursive:true});
  const folder = fs.mkdtempSync(path.join(parent, '.remotion-join-'));
  try {
    // Only relative ASCII names enter FFconcat files. Titles, Unicode and quote
    // escaping never reach its parser. Hard links avoid copying large sections.
    for (const [prefix, files, extension] of [['v', videoFiles, 'mp4'], ['a', audioFiles, 'wav']]) {
      const names = [];
      for (let i = 0; i < files.length; i++) {
        const name = `${prefix}${String(i).padStart(6, '0')}.${extension}`;
        const alias = path.join(folder, name);
        try { fs.linkSync(files[i], alias); }
        catch (error) {
          if (!['EXDEV', 'EPERM', 'ENOTSUP', 'EACCES'].includes(error.code)) throw error;
          fs.copyFileSync(files[i], alias, fs.constants.COPYFILE_EXCL);
        }
        names.push(`file '${name}'`);
      }
      fs.writeFileSync(path.join(folder, `${prefix}.ffconcat`), `ffconcat version 1.0\n${names.join('\n')}\n`);
    }
    const options = {timeout:Math.max(1000, Math.min(600000, Number(timeoutMs) || 300000)), killSignal:'SIGKILL'};
    // One continuous PCM input, one AAC encode; video packets are stream-copied.
    const encode = {bin:'ffmpeg', indent:false, logLevel:'error', options,
      args:['-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
        '-f', 'concat', '-safe', '1', '-i', path.join(folder, 'v.ffconcat'),
        '-f', 'concat', '-safe', '1', '-i', path.join(folder, 'a.ffconcat'),
        '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac',
        '-b:a', '160k', '-ar', '48000', '-threads:a', '1', '-movflags', '+faststart', '-f', 'mp4', outputPath]};
    for (let attempt = 1; ; attempt++) {
      try { await callFf(encode); break; }
      catch (error) {
        // A killed/failed launch can return code 1 with no diagnostic on a
        // pressured Windows host. Retry just the join, never the frame render.
        const transient = error.timedOut || ['ENOMEM', 'EAGAIN', 'EBUSY'].includes(error.code)
          || (error.exitCode === 1 && !String(error.stderr || '').trim());
        if (!transient || attempt >= 3) throw error;
        console.warn(`[Remotion] Final join transient failure; retry ${attempt + 1}/3 using saved sections`);
        await new Promise(resolve => setTimeout(resolve, 500 * attempt));
      }
    }
    if (!fs.existsSync(outputPath) || fs.statSync(outputPath).size < 4096) throw new Error('Final join produced an empty export');
    const probe = await callFf({bin:'ffprobe', indent:false, logLevel:'error', options:{timeout:30000},
      args:['-v','error','-show_streams','-show_format','-of','json',outputPath]});
    const data = JSON.parse(probe.stdout);
    const video = data.streams?.find(row => row.codec_type === 'video');
    const audio = data.streams?.find(row => row.codec_type === 'audio');
    const expectedDuration = Number(frames) / Number(fps);
    const duration = Number(data.format?.duration);
    if (!video || !audio || audio.codec_name !== 'aac'
        || (width && Number(video.width) !== width) || (height && Number(video.height) !== height)
        || !Number.isFinite(duration) || duration <= 0
        || (expectedDuration > 0 && Math.abs(duration - expectedDuration) > Math.max(0.25, 2 / fps))
        || (frames && Number(video.nb_frames) !== frames)) {
      throw new Error('Final join stream, frame count or duration validation failed');
    }
    return {duration, frames:Number(video.nb_frames), width:video.width, height:video.height, audio:audio.codec_name};
  } catch (cause) {
    const detail = String(cause.stderr || (cause.command
      ? `FFmpeg exited ${cause.exitCode ?? cause.signal ?? 'unexpectedly'}${cause.timedOut ? ' (timeout)' : ''}`
      : cause.message)).slice(-3000);
    const error = new Error(`VIDZEO_FINALIZE_FAILED: ${detail}. Completed render sections are saved; retry the final join.`, {cause});
    error.code = 'VIDZEO_FINALIZE_FAILED';
    throw error;
  } finally {
    // The directory is allocated here and contains only aliases owned here.
    try { fs.rmSync(folder, {recursive:true, force:true}); }
    catch (error) { console.warn(`[Remotion] Join alias cleanup deferred: ${error.code || 'cleanup_failed'}`); }
  }
}

module.exports = {joinRemotionSections, promoteExport};
