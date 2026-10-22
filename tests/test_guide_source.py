import unittest
from unittest.mock import patch
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from pcr_maa.guide_source import video_reference, _public_url, acquire_video


class GuideSourceTest(unittest.TestCase):
    def test_exact_video_and_page(self):
        self.assertEqual(video_reference('https://www.bilibili.com/video/BV1DeNY6dEJP/?p=10'), ('BV1DeNY6dEJP', 10))
        self.assertEqual(video_reference('https://www.bilibili.com/video/BV1Fvpu6DE4x/'), ('BV1Fvpu6DE4x', 1))

    def test_invalid_reference_is_not_guessed(self):
        for url in ('https://b23.tv/example', 'https://example.com/video/BV1DeNY6dEJP/',
                    'https://www.bilibili.com/video/BV1DeNY6dEJP/?p=0',
                    'https://www.bilibili.com/video/BV1DeNY6dEJP/?p=1&p=10',
                    'https://www.bilibili.com/video/BV1DeNY6dEJP/?p=１０',
                    'https://www.bilibili.com/video/BV1DeNY6dEJP/?p=1&token=private',
                    'https://user:pass@www.bilibili.com/video/BV1DeNY6dEJP/'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                video_reference(url)

    def test_public_hosts_and_redirect_scope(self):
        _public_url('https://api.bilibili.com/x/web-interface/view')
        _public_url('https://upos-sz-mirrorcos.bilivideo.com/video.mp4', True)
        for url in ('http://api.bilibili.com/test', 'https://api.bilibili.com.evil/test',
                    'https://localhost/test', 'file:///private', 'https://api.bilibili.com:443/test'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                _public_url(url)
        with self.assertRaises(ValueError):
            _public_url('https://api.bilibili.com/video', True)

    def test_acquisition_binds_exact_page_and_keeps_files(self):
        def download(url, path, limit, **kwargs):
            if path.name == 'metadata.json':
                value = {'code': 0, 'data': {'bvid': 'BV1DeNY6dEJP', 'pages': [
                    {'page': 1, 'cid': 1, 'part': 'wrong page'}, {'page': 10, 'cid': 88, 'part': '火8-8'}]}}
            elif path.name == 'playback.json':
                self.assertIn('cid=88', url)
                value = {'code': 0, 'data': {'durl': [{'url': 'https://test.bilivideo.com/fixture'}]}}
            else:
                path.write_bytes(b'fixture-video'); return
            with path.open('x', encoding='utf-8') as stream:
                json.dump(value, stream)
        with TemporaryDirectory(dir=Path.cwd()/'.runtime') as directory, patch('pcr_maa.guide_source._download', download):
            video, result = acquire_video('https://www.bilibili.com/video/BV1DeNY6dEJP/?p=10', directory)
            self.assertEqual(result['cid'], 88)
            self.assertEqual(result['part'], '火8-8')
            self.assertEqual(video.read_bytes(), b'fixture-video')
            before = (Path(directory)/'metadata.json').read_bytes()
            with self.assertRaises(FileExistsError):
                acquire_video('https://www.bilibili.com/video/BV1DeNY6dEJP/?p=10', directory)
            self.assertEqual((Path(directory)/'metadata.json').read_bytes(), before)

    def test_wrong_video_and_missing_page_stop_before_playback(self):
        for data in ({'bvid': 'BV1Fvpu6DE4x', 'pages': [{'page': 10}]},
                     {'bvid': 'BV1DeNY6dEJP', 'pages': [{'page': 1}]}):
            calls = []
            def download(url, path, limit, **kwargs):
                calls.append(path.name)
                path.write_text(json.dumps({'code': 0, 'data': data}), encoding='utf-8')
            with TemporaryDirectory(dir=Path.cwd()/'.runtime') as directory, patch('pcr_maa.guide_source._download', download):
                with self.assertRaises(ValueError):
                    acquire_video('https://www.bilibili.com/video/BV1DeNY6dEJP/?p=10', directory)
                self.assertEqual(calls, ['metadata.json'])


if __name__ == '__main__':
    unittest.main()
