"""Portable local-agent client. No Electron, browser, or external inference API."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

BUILD_ID = '2026-10-09-local-agent-v1'


def configure(runtime_root=None):
    from services.agent_access import installation_file
    resources = Path(__file__).resolve().parent.parent
    registration = installation_file(resources)
    saved = json.loads(registration.read_text(encoding='utf-8')) if registration.is_file() else {}
    root = Path(runtime_root or saved.get('runtime_root') or resources).resolve()
    packaged = bool(saved.get('packaged', (resources / 'app.asar').is_file()))
    env = dict(os.environ)
    node_name = 'node.exe' if os.name == 'nt' else 'node'
    node_candidate = resources / 'tools' / 'node' / node_name
    if not node_candidate.is_file():
        import shutil
        sys_node = shutil.which('node')
        node_candidate = Path(sys_node) if sys_node else node_candidate

    values = {'VIDZEO_ROOT': root, 'VIDZEO_RESOURCE_ROOT': resources,
              'VIDZEO_SECURE_DATA_DIR': root / 'secure', 'VIDZEO_SESSION_SECRET_FILE': root / '.vidzeo_session_secret',
              'VIDZEO_OUTPUT_DIR': root / 'output', 'VIDZEO_TEMP_DIR': root / 'temp',
              'VIDZEO_QUEUE_FILE': root / 'server' / 'queue.json',
              'VIDZEO_SERVER_DIR': resources / 'server', 'VIDZEO_SRC_DIR': resources / 'src',
              'VIDZEO_FONTS_DIR': resources / 'fonts', 'VIDZEO_PUBLIC_DIR': resources / 'public',
              'VIDZEO_TEMPLATE_LIBRARY_DIR': resources / 'resources' / 'template-library',
              'VIDZEO_TEMPLATE_CUSTOM_DIR': root / 'template-library' / 'custom',
              'VIDZEO_NODE_PATH': node_candidate,
              'NODE_PATH': resources / 'node_modules', 'VIDZEO_PACKAGED': '1' if packaged else '0',
              'VIDZEO_NO_BROWSER': '1', 'VIDZEO_BIND_HOST': '127.0.0.1',
              'VIDZEO_AUTH_MODE': saved.get('auth_mode') or env.get('VIDZEO_AUTH_MODE') or 'supabase'}
    if saved.get('allow_packaged_local_auth') == '1':
        values['VIDZEO_ALLOW_PACKAGED_LOCAL_AUTH'] = '1'
    env.update({key: str(value) for key, value in values.items()})
    env['PATH'] = os.pathsep.join([str(resources / 'tools' / 'node'), str(resources / 'tools' / 'ffmpeg'), env.get('PATH', '')])
    os.environ.update({key: env[key] for key in values})
    return resources, root, env


def _json_http(url, *, key='', body=None, timeout=20):
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    req = urllib.request.Request(url, headers=headers, data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            error = json.loads(exc.read()).get('error') or 'Request rejected.'
        except Exception:
            error = 'Request rejected.'
        raise RuntimeError(f'Vidnets returned {exc.code}: {error}') from None


def service_url(root):
    descriptor = root / 'secure' / 'agent-service.json'
    if not descriptor.is_file():
        launcher_name = 'VIDNETS_AGENT.cmd' if os.name == 'nt' else './VIDNETS_AGENT.sh'
        raise RuntimeError(f'Start the service first: {launcher_name} start')
    state = json.loads(descriptor.read_text(encoding='utf-8'))
    port = int(state['port'])
    if port < 1 or port > 65535 or state.get('build_id') != BUILD_ID:
        raise RuntimeError('Start this installation\'s current service first.')
    return f'http://127.0.0.1:{port}'


def start(resources, root, env):
    from services.agent_access import _read
    from services.agent_bridge import atomic_json, _request_lock
    _read()  # Requires explicit pairing; never silently enables account bypass.
    secure = root / 'secure'
    secure.mkdir(parents=True, exist_ok=True)
    with _request_lock(secure / 'agent-service.json'):
        try:
            url = service_url(root)
            status = _json_http(url + '/api/status', timeout=3)
            if status.get('pipeline_build_id') == BUILD_ID and Path(status.get('resource_root', '')).resolve() == resources:
                return {'ready': True, 'url': url, 'reused': True}
        except (OSError, ValueError, RuntimeError, urllib.error.URLError):
            pass
        port = None
        for candidate in range(5010, 5110):
            with socket.socket() as probe:
                try:
                    if os.name == 'nt':
                        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    else:
                        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    probe.bind(('127.0.0.1', candidate))
                    port = candidate
                    break
                except OSError:
                    continue
        if port is None:
            raise RuntimeError('No free local service port was found.')
        env['VIDZEO_PORT'] = str(port)
        logs = root / 'logs'
        logs.mkdir(parents=True, exist_ok=True)
        logfile = logs / 'agent-service.log'
        py_name = 'python.exe' if os.name == 'nt' else 'python'
        python = resources / 'tools' / 'python' / py_name
        if not python.is_file():
            python = Path(sys.executable)
        with logfile.open('ab') as stream:
            child = subprocess.Popen([str(python), '-u', str(resources / 'main.py')],
                cwd=str(resources), env=env, stdout=stream, stderr=stream, stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        url = f'http://127.0.0.1:{port}'
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError(f'Service exited. Inspect {logfile}')
            try:
                status = _json_http(url + '/api/status', timeout=2)
                if status.get('pipeline_build_id') == BUILD_ID and Path(status.get('resource_root', '')).resolve() == resources:
                    atomic_json(secure / 'agent-service.json', {'port': port, 'pid': child.pid,
                                'build_id': BUILD_ID, 'resource_root': str(resources)})
                    return {'ready': True, 'url': url, 'log_file': str(logfile), 'pid': child.pid}
            except (OSError, ValueError, RuntimeError, urllib.error.URLError):
                pass
            time.sleep(.25)
        # Only stop the process this invocation created, never another app.
        child.terminate()
        raise RuntimeError(f'Service startup exceeded 45 seconds. Inspect {logfile}')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Vidnets service for an external agent\'s own reasoning and vision.')
    parser.add_argument('--runtime-root', help='Writable Vidnets data folder; normally discovered from Settings pairing.')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('start')
    commands.add_parser('capabilities')
    commands.add_parser('pending')
    for name in ('request', 'job', 'retry'):
        commands.add_parser(name).add_argument('id')
    commands.add_parser('submit').add_argument('file')
    answer = commands.add_parser('answer')
    answer.add_argument('id')
    answer.add_argument('file', help='UTF-8 text or JSON containing the exact requested response.')
    args = parser.parse_args(argv)
    resources, root, env = configure(args.runtime_root)
    if args.command == 'start':
        result = start(resources, root, env)
    else:
        from services.agent_access import _read
        key = _read()['key']
        url = service_url(root)
        status = _json_http(url + '/api/status', timeout=5)
        if status.get('pipeline_build_id') != BUILD_ID or Path(status.get('resource_root', '')).resolve() != resources:
            raise RuntimeError('The local service belongs to a different installation. Run start again.')
        if args.command == 'submit':
            body = json.loads(Path(args.file).read_text(encoding='utf-8-sig'))
            if not isinstance(body, dict):
                raise ValueError('The generation file must be a JSON object.')
            body.update(llm_provider='external_agent', execution_mode='queue', response_mode='json')
            result = _json_http(url + '/api/generate', key=key, body=body, timeout=60)
        elif args.command == 'answer':
            content = Path(args.file).read_text(encoding='utf-8-sig')
            try:
                content = json.loads(content)
            except ValueError:
                pass
            result = _json_http(url + '/api/agent/requests/' + args.id + '/complete', key=key, body={'content': content})
        elif args.command == 'retry':
            result = _json_http(url + '/api/agent/jobs/' + args.id + '/retry', key=key, body={})
        else:
            path = {'capabilities': '/api/agent/capabilities', 'pending': '/api/agent/requests',
                    'request': '/api/agent/requests/' + str(getattr(args, 'id', '')),
                    'job': '/api/agent/jobs/' + str(getattr(args, 'id', ''))}[args.command]
            result = _json_http(url + path, key=key)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, PermissionError, urllib.error.URLError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
