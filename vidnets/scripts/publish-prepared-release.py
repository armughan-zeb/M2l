"""Publish the prepared Windows installer using the local Git credential manager."""
import hashlib
import json
import subprocess
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT.parent.parent / 'Vidnets Releases'
REPO = 'armughan-zeb/Vidnets-Studio-Releases'
manifest = json.loads((RELEASE / 'latest.json').read_text(encoding='utf-8-sig'))
artifact = manifest['artifact']
installer = RELEASE / 'artifacts' / artifact['name']
tag = manifest['releasePage'].rsplit('/', 1)[-1]
hasher = hashlib.sha256()
with installer.open('rb') as stream:
    for block in iter(lambda: stream.read(1024 * 1024), b''):
        hasher.update(block)
assert installer.stat().st_size == artifact['size']
assert hasher.hexdigest() == artifact['sha256']
credential = subprocess.run(['git', 'credential', 'fill'],
    input='protocol=https\nhost=github.com\n\n', text=True, capture_output=True, check=True, timeout=30)
fields = dict(line.split('=', 1) for line in credential.stdout.splitlines() if '=' in line)
if not fields.get('password'):
    raise RuntimeError('GitHub credential manager has no credential')
session = requests.Session()
session.headers.update({'Authorization': 'Bearer ' + fields['password'],
    'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'Vidnets-Release-Publisher'})
api = 'https://api.github.com/repos/' + REPO

def api_call(method, path, **kwargs):
    response = session.request(method, api + path, timeout=60, **kwargs)
    if not response.ok:
        raise RuntimeError(f'GitHub {method} {path}: HTTP {response.status_code}')
    return response.json()

existing = session.get(api + '/releases/tags/' + tag, timeout=30)
if existing.status_code == 404:
    # Drafts may be absent from the by-tag endpoint until GitHub creates the
    # tag. Reuse the draft on retry instead of creating duplicate releases.
    release = next((row for row in api_call('GET', '/releases?per_page=100')
        if row.get('draft') and row.get('tag_name') == tag), None)
    if release is None:
        release = api_call('POST', '/releases', json={'tag_name': tag, 'target_commitish': 'main',
            'name': f"Vidnets Studio {manifest['version']} — launch release", 'draft': True,
            'prerelease': False, 'body': (RELEASE / 'RELEASE_NOTES.md').read_text(encoding='utf-8')})
else:
    existing.raise_for_status()
    release = existing.json()
print('Release prepared: ' + tag, flush=True)
assets = api_call('GET', f"/releases/{release['id']}/assets")
asset = next((row for row in assets if row['name'] == artifact['name']), None)
if asset:
    if asset.get('state') == 'starter' and release.get('draft'):
        cleanup = session.delete(api + f"/releases/assets/{asset['id']}", timeout=30)
        cleanup.raise_for_status()
        asset = None
    elif asset['size'] != artifact['size'] or asset.get('digest') != 'sha256:' + artifact['sha256']:
        raise RuntimeError('Existing release asset differs; refusing to replace it')
if not asset:
    url = release['upload_url'].split('{', 1)[0] + '?name=' + quote(artifact['name'])
    print(f"Uploading verified installer ({artifact['size']} bytes)…", flush=True)
    # Native Windows TLS handles long binary uploads more reliably here. The
    # credential is passed only through stdin, never command arguments or disk.
    response_path = RELEASE / 'artifacts' / 'upload-response.json'
    config = f'header = "Authorization: Bearer {fields["password"]}"\n'
    upload = subprocess.run(['curl.exe', '--config', '-', '--http1.1', '--silent', '--show-error',
        '--connect-timeout', '30', '--max-time', '0', '--speed-limit', '1024', '--speed-time', '180', '--request', 'POST',
        '--header', 'Content-Type: application/octet-stream', '--header', 'Expect:', '--upload-file', str(installer),
        '--output', str(response_path), '--write-out', '%{http_code}', url],
        input=config, text=True, capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if upload.returncode or upload.stdout.strip() != '201':
        raise RuntimeError(f'Installer upload failed: curl exit {upload.returncode}, HTTP {upload.stdout.strip()}')
    asset = json.loads(response_path.read_text(encoding='utf-8'))
if asset.get('state') != 'uploaded' or asset['size'] != artifact['size']:
    raise RuntimeError('Uploaded asset size/state validation failed')
if asset.get('digest') != 'sha256:' + artifact['sha256']:
    raise RuntimeError('GitHub asset checksum does not match the verified installer')
release = api_call('GET', f"/releases/{release['id']}")
release = api_call('PATCH', f"/releases/{release['id']}", json={'tag_name': tag,
    'target_commitish': release.get('target_commitish', 'main'), 'draft': False, 'make_latest': 'true',
    'body': (RELEASE / 'RELEASE_NOTES.md').read_text(encoding='utf-8')})
# Draft assets use temporary untagged download URLs. Read the public asset again.
asset = api_call('GET', f"/releases/assets/{asset['id']}")
if asset['browser_download_url'] != artifact['url']:
    raise RuntimeError('Published asset URL does not match the prepared channel')
record = {'release_url': release['html_url'], 'tag': tag, 'asset_url': asset['browser_download_url'],
    'asset_id': asset['id'], 'size': asset['size'], 'digest': asset['digest'], 'published': not release['draft']}
(RELEASE / 'artifacts' / 'deployment-receipt.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record, indent=2), flush=True)
