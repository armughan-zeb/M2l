import importlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

RESOURCE = Path(__file__).resolve().parents[1]
WORKSPACE = tempfile.TemporaryDirectory(prefix='vidzeo-runtime-proof-')
os.environ.update({
    'VIDZEO_ROOT': WORKSPACE.name,
    'VIDZEO_RESOURCE_ROOT': str(RESOURCE),
    'VIDZEO_SECURE_DATA_DIR': str(Path(WORKSPACE.name) / 'secure'),
    'VIDZEO_QUEUE_FILE': str(Path(WORKSPACE.name) / 'queue.json'),
    'VIDZEO_SESSION_SECRET_FILE': str(Path(WORKSPACE.name) / 'session-secret'),
    'VIDZEO_DESKTOP_SECRET': 'fixture-cms-runtime-proof',
    'FLASK_SKIP_DOTENV': '1',
})
sys.path.insert(0, str(RESOURCE))
engine = importlib.import_module('server.app')


class CmsRuntimeOwnership(unittest.TestCase):
    def setUp(self):
        self.client = engine.app.test_client()
        self.proof = {'X-Vidzeo-Runtime-Key': 'fixture-cms-runtime-proof'}

    def test_matching_runtime_is_identified(self):
        result = self.client.get('/api/cms-runtime', headers=self.proof)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.get_json(), {'ok': True})

    def test_unproved_runtime_and_session_mutation_are_denied(self):
        self.assertEqual(self.client.get('/api/cms-runtime').status_code, 403)
        self.assertEqual(self.client.get('/api/cms-runtime', headers={'X-Vidzeo-Runtime-Key': 'wrong'}).status_code, 403)
        self.assertEqual(self.client.post('/api/youtube/session/disconnect').status_code, 403)

    def test_browser_and_forwarded_requests_cannot_reuse_proof(self):
        for extra in ({'Origin': 'https://foreign.invalid'}, {'X-Forwarded-For': '127.0.0.1'}, {'Forwarded': 'for=127.0.0.1'}):
            self.assertEqual(self.client.get('/api/cms-runtime', headers={**self.proof, **extra}).status_code, 403)


if __name__ == '__main__':
    unittest.main()
