import json
import time

from services.editorial_director.creative_review_cache import load_pass_cache, save_pass_cache
from services.editorial_director.pixel_revision import review_and_revise


def test_disk_cache_only_persists_passes_and_expires(tmp_path):
    path = tmp_path / 'passes.json'
    save_pass_cache(path, {'a' * 64: {'status': 'pass'}, 'b' * 64: {'status': 'unavailable'}})
    assert list(load_pass_cache(path)) == ['a' * 64]
    payload = json.loads(path.read_text())
    payload['saved_at'] = time.time() - 8 * 86400
    path.write_text(json.dumps(payload))
    assert load_pass_cache(path) == {}
    path.write_text('{broken')
    assert load_pass_cache(path) == {}


def test_exact_pixels_brief_and_model_are_required_for_reuse(tmp_path):
    frame = tmp_path / 'frame.png'
    frame.write_bytes(b'actual sampled bytes')
    film = {'fps': 30, 'scenes': [{'narration': 'A reef.', 'from': 0, 'duration_in_frames': 90,
            'primary_visual': {'type': 'SmartImage', 'variant': 'editorial_crop'}}]}
    samples = [{'frame': 30, 'path': str(frame)}]
    calls = []
    class Client:
        model = 'model-a'
        def review_editorial_frames(self, paths, context):
            calls.append(context)
            return {'status': 'pass', 'confidence': 95, 'issues': [], 'patch': None}
    cache = {}
    def review():
        return review_and_revise(film, samples, lambda budget: Client(), pass_cache=cache)
    review()
    path = tmp_path / 'cache.json'
    save_pass_cache(path, cache)
    cache = load_pass_cache(path)
    review()
    assert len(calls) == 1
    frame.write_bytes(b'changed actual sampled bytes')
    review()
    assert len(calls) == 2
    film['scenes'][0]['narration'] = 'Another reef.'
    review()
    assert len(calls) == 3
    Client.model = 'model-b'
    review()
    assert len(calls) == 4
