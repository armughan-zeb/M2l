import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../server')))
from services.real_media_service import MediaConfig, MultiSourceSearchEngine


class StockApiProvenanceTests(unittest.TestCase):
    def test_all_api_media_types_keep_source_and_creator(self):
        fixtures = [
            ('_pexels_videos', 'videos', {
                'id': 11, 'url': 'https://www.pexels.com/video/11/',
                'user': {'name': 'Camera Person', 'url': 'https://www.pexels.com/@camera/'},
                'video_files': [{'link': 'https://media.test/11.mp4', 'width': 1280, 'height': 720}]
            }, 'Pexels License'),
            ('_pexels_photos', 'photos', {
                'id': 12, 'url': 'https://www.pexels.com/photo/12/',
                'photographer': 'Camera Person', 'src': {'original': 'https://media.test/12.jpg'}
            }, 'Pexels License'),
            ('_pixabay_videos', 'hits', {
                'id': 13, 'pageURL': 'https://pixabay.com/videos/13/', 'user': 'Camera Person',
                'videos': {'medium': {'url': 'https://media.test/13.mp4', 'width': 1280, 'height': 720}}
            }, 'Pixabay Content License'),
            ('_pixabay_images', 'hits', {
                'id': 14, 'pageURL': 'https://pixabay.com/photos/14/', 'user': 'Camera Person',
                'largeImageURL': 'https://media.test/14.jpg'
            }, 'Pixabay Content License'),
        ]
        with tempfile.TemporaryDirectory() as directory:
            engine = MultiSourceSearchEngine(MediaConfig(cache_dir=directory))
            for method, key, asset, license_name in fixtures:
                with self.subTest(method=method):
                    response = Mock(status_code=200)
                    response.json.return_value = {key: [asset]}
                    with patch('services.real_media_service._call_with_provider_limit', return_value=response):
                        rows = getattr(engine, method)('car driving')
                    self.assertEqual(len(rows), 1)
                    info = rows[0].score_breakdown
                    self.assertEqual(info['source_url'], asset.get('url', asset.get('pageURL')))
                    self.assertEqual(info['creator'], 'Camera Person')
                    self.assertEqual(info['provider_asset_id'], str(asset['id']))
                    self.assertEqual(info['license'], license_name)
                    self.assertTrue(info['license_verification_required'])
                    self.assertEqual(info['retrieval_method'], 'official_api')


if __name__ == '__main__':
    unittest.main()
