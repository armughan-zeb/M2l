"""Dependency-free Windows speech recovery with measured word boundaries."""
import json
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path


def generate_audio(text, output_path):
    if sys.platform != 'win32':
        raise RuntimeError('Offline Windows speech is unavailable on this platform.')
    shell = shutil.which('powershell.exe')
    if not shell:
        shell = str(Path(os.environ.get('SystemRoot', 'C:/Windows')) /
                    'System32/WindowsPowerShell/v1.0/powershell.exe')
    target = Path(output_path).with_suffix('.wav')
    target.parent.mkdir(parents=True, exist_ok=True)
    request = target.with_suffix('.speech-request.json')
    word_file = Path(str(target) + '.words.json')
    request.write_text(json.dumps({'text': str(text)}, ensure_ascii=False), encoding='utf-8')
    try:
        result = subprocess.run(
            [shell, '-NoProfile', '-NonInteractive', '-File',
             str(Path(__file__).with_name('local_speech.ps1')),
             '-InputPath', str(request.resolve()), '-OutputPath', str(target.resolve())],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=max(90, len(str(text).split()) * 1.5 + 60),
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if result.returncode or not target.is_file():
            raise RuntimeError(f'Offline speech synthesis failed: {result.stderr.strip()[-500:]}')
        with wave.open(str(target), 'rb') as audio:
            duration = audio.getnframes() / audio.getframerate()
        words = json.loads(word_file.read_text(encoding='utf-8-sig'))
        if isinstance(words, dict):
            words = [words]
        grouped = []
        for word in words:
            # SAPI emits several phonetic events for the same numeric span.
            # Preserve its first measured onset and one copy of the script.
            if grouped and (word.get('position'), word.get('count')) == (grouped[-1].get('position'), grouped[-1].get('count')):
                continue
            grouped.append(word)
        words = grouped
        rows = []
        for index, word in enumerate(words):
            start = max(0.0, float(word['start']))
            end = min(duration, float(words[index + 1]['start'])) if index + 1 < len(words) else duration
            if str(word.get('word', '')).strip() and end > start:
                rows.append({'word': word['word'], 'start': start, 'end': end})
        from .narration_quality import validate_narration_audio
        validate_narration_audio(target)
        if rows:
            from .speech_timing import save_speech_timing
            save_speech_timing(target, rows, source='tts_word_boundary')
        return str(target), duration
    finally:
        request.unlink(missing_ok=True)
        word_file.unlink(missing_ok=True)
