import logging
import os
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path


class _SafeStream:
    def __init__(self, stream):
        self._stream = stream
        self._lock = threading.RLock()

    def write(self, data):
        if isinstance(data, bytes):
            try:
                data = data.decode('utf-8', errors='replace')
            except Exception:
                data = str(data)
        acquired = self._lock.acquire(timeout=2.0)
        if not acquired:
            return 0
        try:
            return self._stream.write(data)
        except UnicodeEncodeError:
            safe_data = str(data).encode('ascii', 'replace').decode('ascii')
            try:
                return self._stream.write(safe_data)
            except OSError:
                return 0
        except OSError:
            return 0
        finally:
            self._lock.release()

    def flush(self):
        acquired = self._lock.acquire(timeout=2.0)
        if not acquired:
            return None
        try:
            return self._stream.flush()
        except OSError:
            return None
        finally:
            self._lock.release()

    def reconfigure(self, *args, **kwargs):
        if hasattr(self._stream, 'reconfigure'):
            try:
                return self._stream.reconfigure(*args, **kwargs)
            except OSError:
                return None
        return None

    def __getattr__(self, name):
        return getattr(self._stream, name)


if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

sys.stdout = _SafeStream(sys.stdout)
sys.stderr = _SafeStream(sys.stderr)


ROOT_DIR = Path(__file__).parent.absolute()
SERVER_DIR = ROOT_DIR / 'server'
LOG_DIR = ROOT_DIR / 'logs'

sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(SERVER_DIR))

LOG_DIR.mkdir(exist_ok=True)
(ROOT_DIR / 'output').mkdir(exist_ok=True)
(ROOT_DIR / 'temp').mkdir(exist_ok=True)


def _write_process_crash(exc_type, exc_value, exc_tb):
    """Persist uncaught backend failures so the Electron shell can explain them."""
    try:
        log_path = LOG_DIR / 'backend-process.log'
        if log_path.exists() and log_path.stat().st_size > 4 * 1024 * 1024:
            previous = LOG_DIR / 'backend-process.previous.log'
            try:
                previous.unlink(missing_ok=True)
            except Exception:
                pass
            log_path.replace(previous)
        with log_path.open('a', encoding='utf-8') as handle:
            handle.write(f'{datetime.now(timezone.utc).isoformat()} [ERROR] Unhandled backend exception\n')
            traceback.print_exception(exc_type, exc_value, exc_tb, file=handle)
    except Exception:
        pass


def _thread_exception(args):
    _write_process_crash(args.exc_type, args.exc_value, args.exc_traceback)


sys.excepthook = _write_process_crash
if hasattr(threading, 'excepthook'):
    threading.excepthook = _thread_exception


def load_local_env():
    candidate_dirs = [
        ROOT_DIR,
        Path(os.environ.get('VIDZEO_ROOT', '')).resolve() if os.environ.get('VIDZEO_ROOT') else None,
        Path(sys.executable).parent if getattr(sys, 'frozen', False) else None,
        Path.cwd(),
    ]
    env_paths = []
    for base in filter(None, candidate_dirs):
        env_paths.append(base / '.env')
        # Distribution builds intentionally omit private .env files. Keep the
        # non-secret pipeline tuning embedded in the app so packaged runs do
        # not silently fall back to low-quality/legacy defaults.
        env_paths.append(base / '.env.defaults')
        env_paths.append(base / '.env.public')

    for env_path in env_paths:
        if not env_path.is_file():
            continue

        try:
            for raw_line in env_path.read_text(encoding='utf-8', errors='ignore').splitlines():
                line = raw_line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except Exception:
            pass



def configure_environment():
    os.environ.setdefault('VIDZEO_AUTH_MODE', 'local')
    os.environ.setdefault('VIDZEO_ALLOW_PACKAGED_LOCAL_AUTH', '1')
    os.environ.setdefault('FLASK_SKIP_DOTENV', '1')
    os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '3')
    os.environ.setdefault('TF_ENABLE_ONEDNN_OPTS', '0')
    os.environ.setdefault('CUDA_VISIBLE_DEVICES', '-1')
    os.environ.setdefault('TF_SILENCE_DEPRECATION_WARNINGS', '1')
    os.environ.setdefault('TRANSFORMERS_VERBOSITY', 'error')
    os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
    os.environ.setdefault('HF_HUB_DISABLE_PROGRESS_BARS', '1')
    os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
    os.environ.setdefault('TORCH_NUM_THREADS', '2')
    os.environ.setdefault('OMP_NUM_THREADS', '2')
    os.environ.setdefault('MKL_NUM_THREADS', '2')


def configure_logging():
    # Route standard Python INFO logs to stdout so Electron labels them as [Python]
    # rather than [Python ERR]. Keep noisy third-party libraries quiet.
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    )
    warnings_to_quiet = ['transformers', 'torch', 'urllib3', 'werkzeug', 'faster_whisper', 'httpcore', 'httpx']
    for logger_name in warnings_to_quiet:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def start_server():
    from server.app import app, run_startup_maintenance

    print('[Vidzeo] Backend booting in lazy mode...')
    run_startup_maintenance()
    # Stuck-job debugger: background watchdog that notices a processing job
    # with no progress change and escalates (re-wake, advance scene, or
    # restart for auto-resume). Never raises; a failed start is logged only.
    try:
        from server.services.job_debugger import start_job_debugger

        start_job_debugger()
    except Exception as exc:
        print(f'[job-debugger] failed to start: {exc}')
    bind_host = os.environ.get('VIDZEO_BIND_HOST', '127.0.0.1').strip() or '127.0.0.1'
    try:
        port = int(os.environ.get('VIDZEO_PORT', '5000') or 5000)
    except (TypeError, ValueError):
        port = 5000
    if not 1024 <= port <= 65535:
        port = 5000
    print(f'[Vidzeo] Server listening on http://{bind_host}:{port}')
    try:
        app.run(host=bind_host, port=port, threaded=True, debug=False)
    except KeyboardInterrupt:
        print('[Vidzeo] Backend stopped by request.')
    except Exception as exc:
        _write_process_crash(type(exc), exc, exc.__traceback__)
        print('[Vidzeo] Backend hit an unrecoverable error. The desktop app will retry automatically.')
        raise


if __name__ == '__main__':
    load_local_env()
    configure_environment()
    configure_logging()
    start_server()
