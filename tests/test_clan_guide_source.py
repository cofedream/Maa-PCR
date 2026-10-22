"""Synthetic boundary checks; these are not video or live-game acceptance evidence."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from pcr_maa.clan_guide_source import (
    build_page_jobs, digest, parse_damage, parse_header, parse_support, parse_switches,
    operation_mark, period_from_title, prepare_bundle, static_frames, verify_source,
)


class ClanGuideSourceTest(unittest.TestCase):
    def test_stable_slide_detection_keeps_real_middle_frames_once(self):
        import cv2
        import numpy as np
        frames = [np.full((720,1280,3), v, np.uint8) for v in ([240]*4+[10]*4)]
        class Capture:
            index = 0
            released = False
            def get(self, prop):
                return 1 if prop == cv2.CAP_PROP_FPS else len(frames)
            def set(self, prop, value):
                self.index = int(value/1000)
            def read(self):
                return True, frames[self.index].copy()
            def release(self):
                self.released = True
        capture = Capture()
        with TemporaryDirectory(dir=Path.cwd()/'.runtime') as directory, patch('cv2.VideoCapture', return_value=capture):
            result = static_frames('synthetic.mp4',Path(directory)/'frames')
            self.assertEqual([row[0] for row in result], [2,6])
            self.assertTrue(all(path.is_file() for _,path,_ in result))
        self.assertTrue(capture.released)

    def test_operation_circle_cross_and_unknown_pixels(self):
        import cv2
        import numpy as np
        frame = np.full((720,1280,3), 240, np.uint8)
        table = dict(x=120, y=226, cell=64)
        cv2.circle(frame, (152,352), 4, (30,30,30), 1)
        cv2.line(frame, (212,348), (220,356), (30,30,30), 1)
        cv2.line(frame, (220,348), (212,356), (30,30,30), 1)
        self.assertIs(operation_mark(frame,table,0), True)
        self.assertIs(operation_mark(frame,table,1), False)
        self.assertIsNone(operation_mark(frame,table,2))

    def test_period_and_phase_are_not_september_constants(self):
        self.assertEqual(period_from_title('2026.9月天秤座纯auto'), '2026-09')
        self.assertEqual(period_from_title('2027年10月作业'), '2027-10')
        self.assertIsNone(period_from_title('往期参考'))
        self.assertEqual(parse_header([dict(text='BC面 1~22周目')], 'other'),
                         dict(phase='BC', round_min=1, round_max=22))
        self.assertEqual(parse_header([dict(text='23+周目')], 'D'),
                         dict(phase='D', round_min=23, round_max=None))
        self.assertIsNone(parse_header([], '未知分P')['phase'])

    def test_support_damage_and_explicit_switches(self):
        self.assertEqual(parse_support('qsjs'), 'qsjs')
        self.assertEqual(parse_support('缺谁借谁'), 'qsjs')
        self.assertEqual(parse_support('借菲欧'), 'specified')
        self.assertIsNone(parse_support('无支援说明'))
        self.assertEqual(parse_damage('参考 24340w'), 243400000)
        self.assertEqual(parse_damage('伤害 15.5万'), 155000)
        self.assertIsNone(parse_damage('24340w 24500w'))
        ids = [100101, 100201, 100301, 100401, 100501]
        self.assertEqual(parse_switches('1:20 AUTO ON\n0:59 SET 2 关', ids), [
            dict(remaining_seconds=80, auto=True, set_by_unit={}),
            dict(remaining_seconds=59, auto=None, set_by_unit={'100201':False})])
        self.assertEqual(parse_switches('59 SET 1 开\n59 SET 2 关\n59 AUTO ON', ids), [
            dict(remaining_seconds=59,auto=True,set_by_unit={'100101':True,'100201':False})])
        self.assertIsNone(parse_switches('59 AUTO ON\n59 AUTO OFF', ids))
        self.assertIsNone(parse_switches('80 SET 1 开\n85 SET 1 关', ids))
        self.assertIsNone(parse_switches('卡帧按UB\n1:31 AUTO ON', ids))
        self.assertIsNone(parse_switches('可可萝 UB 后 SET 1 开',ids))
        self.assertIsNone(parse_switches('UB后关闭AUTO',ids))
        self.assertIsNone(parse_switches('50 SET 1 开', [None]*5))
        self.assertIsNone(parse_switches('50秒 SET 角色甲 开',ids))

    def test_source_is_bound_to_bv_page_and_actual_pixels(self):
        with TemporaryDirectory(dir=Path.cwd()/'.runtime') as directory:
            image = Path(directory)/'p2/frames/00035.png'; image.parent.mkdir(parents=True)
            image.write_bytes(b'original fixture pixels')
            job = dict(source=dict(url='https://www.bilibili.com/video/BV1Rbh86XEsq/?p=2',
                bvid='BV1Rbh86XEsq', page=2, cid=22, at_seconds=35, job_id='fixture', image_path=str(image), image_sha256=digest(image)))
            (image.parent.parent/'acquisition.json').write_text(json.dumps(job['source']),encoding='utf-8')
            (Path(directory)/'recognition.json').write_text(json.dumps([dict(page=2,observations=[dict(
                at_seconds=35,image_path=str(image),tables=[dict(job_id='fixture')])])]),encoding='utf-8')
            self.assertTrue(verify_source(job))
            job['source']['cid'] = 11
            with self.assertRaisesRegex(ValueError, 'acquisition_mismatch'): verify_source(job)
            job['source']['cid'] = 22
            job['source']['at_seconds'] = 34
            with self.assertRaisesRegex(ValueError, 'observation_mismatch'): verify_source(job)
            job['source']['at_seconds'] = 35
            job['source']['job_id'] = 'changed'
            with self.assertRaisesRegex(ValueError, 'observation_mismatch'): verify_source(job)
            job['source']['job_id'] = 'fixture'
            job['source']['page'] = 1
            with self.assertRaisesRegex(ValueError, 'page_mismatch'): verify_source(job)
            job['source']['page'] = 2
            image.write_bytes(b'changed fixture pixels')
            with self.assertRaisesRegex(ValueError, 'image_changed'): verify_source(job)

    def test_wrong_bv_missing_page_and_existing_output_fail_closed(self):
        def download(url, path, limit):
            path.write_text(json.dumps(dict(code=0, data=dict(bvid='BV1111111111', pages=[]))), encoding='utf-8')
        with TemporaryDirectory(dir=Path.cwd()/'.runtime') as directory, patch('pcr_maa.clan_guide_source._download', download):
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, 'metadata_mismatch'):
                prepare_bundle('https://www.bilibili.com/video/BV1Rbh86XEsq/', root/'wrong')
            with self.assertRaises(FileExistsError):
                prepare_bundle('https://www.bilibili.com/video/BV1Rbh86XEsq/', root/'wrong')
            for bad in ('https://example.com/video/BV1Rbh86XEsq/', 'https://b23.tv/a',
                        'https://www.bilibili.com/video/BV1Rbh86XEsq/?p=0'):
                with self.assertRaises(ValueError):
                    prepare_bundle(bad, root/'bad')

    def test_multiple_teams_dedupe_and_unknowns_require_review(self):
        import numpy as np
        table = dict(x=170, y=160, cell=37, height=35, boss=1, team_label='刀1')
        table2 = dict(table, x=380, boss=2)
        members = [dict(unit_id=None, name=None, proposal_unit_id=100101+i*100,
                        proposal_name='fixture', star=None, set=None, score=.7, margin=.01) for i in range(5)]
        def recognize(image, scale=1):
            return [dict(text='D面 23+周目' if image.shape[0] == 150 else 'qsjs 24340w', box=[1,1,10,10], score=.99)]
        with TemporaryDirectory(dir=Path.cwd()/'.runtime') as directory:
            path = Path(directory)/'frame.png'; path.write_bytes(b'synthetic boundary image')
            evidence = dict(source_url='https://www.bilibili.com/video/BV1Rbh86XEsq/?p=2',
                            bvid='BV1Rbh86XEsq', page=2, cid=22, part='D', title='2026.9月纯auto')
            frame = np.zeros((720,1280,3), np.uint8)
            with patch('pcr_maa.labyrinth.character_recognition.load_character_pack', return_value=([],[],{})), \
                 patch('pcr_maa.clan_guide_source.table_candidates', return_value=[table, table2]), \
                 patch('pcr_maa.clan_guide_source.identify_members', side_effect=lambda *a:[dict(m) for m in members]):
                jobs, records = build_page_jobs([(30,path,frame),(32,path,frame)], evidence, '2026-09', recognize)
            self.assertEqual(len(jobs), 2)
            self.assertEqual([j['boss'] for j in jobs], [1,2])
            self.assertEqual(len({j['source']['job_id'] for j in jobs}), 2)
            self.assertTrue(all(j['unit_ids'] == [None]*5 for j in jobs))
            self.assertTrue(all('confirmed_for' not in j and j['auto'] is True for j in jobs))
            self.assertTrue(all(j['support_mode'] == 'qsjs' and j['reference_damage'] == 243400000 for j in jobs))
            self.assertTrue(all(any('不能执行' in w for w in j['warnings']) for j in jobs))
            self.assertEqual(len(records), 2)

    def test_changed_set_timeline_or_round_is_not_deduplicated(self):
        import numpy as np
        table = dict(x=170,y=160,cell=37,height=35,boss=1,team_label='刀1')
        members = [dict(unit_id=100101+i*100,name='fixture',proposal_unit_id=100101+i*100,
                        star=5,set=True,score=.9) for i in range(5)]
        with TemporaryDirectory(dir=Path.cwd()/'.runtime') as directory:
            path = Path(directory)/'frame.png'; path.write_bytes(b'fixture')
            evidence = dict(source_url='https://www.bilibili.com/video/BV1Rbh86XEsq/?p=2',
                            bvid='BV1Rbh86XEsq',page=2,cid=22,part='D',title='2026.9月纯auto')
            for changed in ('set','switches','round','unsupported'):
                def recognize(image,scale=1):
                    second = int(image[0,0,0])
                    text = f'D面 {23+second if changed=="round" else 23}+周目' if image.shape[0]==150 else ''
                    return [dict(text=text,box=[1,1,10,10],score=.99)]
                def details(words,tab,people,frame):
                    second = int(frame[0,0,0])
                    for m in people: m['set'] = bool(second) if changed=='set' else True
                    return '手动UB触发' if changed=='unsupported' else f'{80-second if changed=="switches" else 80} AUTO 开'
                frames = [(30+i,path,np.full((720,1280,3),i,np.uint8)) for i in range(2)]
                with patch('pcr_maa.labyrinth.character_recognition.load_character_pack',return_value=([],[],{})), \
                     patch('pcr_maa.clan_guide_source.table_candidates',return_value=[table]), \
                     patch('pcr_maa.clan_guide_source.identify_members',side_effect=lambda *a:[dict(m) for m in members]), \
                     patch('pcr_maa.clan_guide_source.table_details',side_effect=details):
                    jobs,_ = build_page_jobs(frames,evidence,'2026-09',recognize)
                if changed == 'unsupported':
                    self.assertEqual(len(jobs),1)
                    self.assertIsNone(jobs[0]['switches'])
                    self.assertIsNone(jobs[0]['auto'])
                    self.assertTrue(any('不能当纯auto执行' in w for w in jobs[0]['warnings']))
                else:
                    self.assertEqual(len(jobs),2,changed)

    def test_cross_page_acquisition_is_rejected_before_reading_video(self):
        from contextlib import nullcontext
        def download(url, path, limit):
            path.write_text(json.dumps(dict(code=0, data=dict(bvid='BV1Rbh86XEsq', title='2026.9月', pages=[
                dict(page=1,cid=11,part='BC'), dict(page=2,cid=22,part='D')]))), encoding='utf-8')
        with TemporaryDirectory(dir=Path.cwd()/'.runtime') as directory, \
             patch('pcr_maa.clan_guide_source._download', download), \
             patch('pcr_maa.clan_guide_source.offline_ocr', return_value=nullcontext(None)), \
             patch('pcr_maa.clan_guide_source.acquire_video', return_value=(Path('never.mp4'), dict(bvid='BV1Rbh86XEsq',page=2,cid=22))), \
             patch('pcr_maa.clan_guide_source.static_frames') as frames:
            with self.assertRaisesRegex(ValueError, 'cross_page_mismatch'):
                prepare_bundle('https://www.bilibili.com/video/BV1Rbh86XEsq/', Path(directory)/'cross')
            frames.assert_not_called()
            frames.return_value = []
            with patch('pcr_maa.clan_guide_source.build_page_jobs',return_value=([],[])):
                bundle = prepare_bundle('https://www.bilibili.com/video/BV1Rbh86XEsq/?p=2',Path(directory)/'only-p2')
            self.assertEqual([p['page'] for p in bundle['pages']],[2])
            self.assertEqual(frames.call_count,1)


if __name__ == '__main__':
    unittest.main()
