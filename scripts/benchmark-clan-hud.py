"""Measure clock OCR and full-team HUD readback on saved frames only."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
from ocr_benchmark import deny_external_io, digest


def clock_seconds(recognition):
    raw = getattr(recognition, 'raw_detail', None)
    words = raw.get('filtered') if type(raw) is dict else None
    if (getattr(recognition, 'hit', False) is not True or type(words) is not list
            or len(words) != 1 or type(words[0]) is not dict):
        return None
    text = words[0].get('text', '')
    if type(text) is not str or re.fullmatch(r'(?:1\s*[:：]\s*[0-2][0-9]|0\s*[:：]\s*[0-5][0-9])', text) is None:
        return None
    minute, second = re.split('[:：]', text)
    return int(minute) * 60 + int(second)


def observe(context, image, recognizer):
    """Every recognition receives the one callback image; no screenshot here."""
    start = time.perf_counter()
    timer = context.run_recognition('GTBattleTimer', image)
    remaining = clock_seconds(timer)
    ocr_done = time.perf_counter()
    state = recognizer.read_controls(image)
    end = time.perf_counter()
    complete = (remaining is not None and type(state.get('auto')) is bool and len(state.get('sets', [])) == 5
                and all(type(value) is bool for value in state['sets'])
                and len(state.get('unit_ids', [])) == 5
                and set(state['unit_ids']) == set(recognizer.team))
    return dict(remaining_seconds=remaining, controls_complete=complete, state=state,
                clock_ocr=timer.raw_detail, ocr_seconds=ocr_done-start, hud_seconds=end-ocr_done,
                callback_seconds=end-start)


def validate_samples(document):
    samples = document.get('samples') if type(document) is dict else None
    if type(samples) is not list or not samples:
        raise ValueError('saved_frame_samples_required')
    ids = set()
    for sample in samples:
        if (type(sample) is not dict or not re.fullmatch(r'[a-z0-9_-]{1,50}', str(sample.get('id', '')))
                or sample['id'] in ids):
            raise ValueError('invalid_or_duplicate_sample_id')
        ids.add(sample['id'])
        path = Path(sample['path'])
        if (not path.is_absolute() or not path.resolve().is_relative_to(ROOT.resolve())
                or any(':' in part for part in path.parts[1:])):
            raise ValueError('saved_frame_outside_project')
        if not path.is_file() or digest(path) != sample.get('sha256'):
            raise ValueError('saved_frame_hash_mismatch')
        if (type(sample.get('expected_remaining_seconds')) is not int
                or not 0 <= sample['expected_remaining_seconds'] <= 89
                or type(sample.get('expected_controls_complete')) is not bool):
            raise ValueError('saved_frame_expectation_required')
    return samples


def summarize(rows):
    metrics = ('end_to_end_seconds', 'ocr_seconds', 'hud_seconds', 'callback_seconds')
    return {key: dict(max_seconds=max(row[key] for row in rows),
                      max_warm_seconds=max(row[key] for row in rows if row['repeat'] > 0))
            for key in metrics}


def fingerprint():
    paths = [Path(__file__), ROOT/'scripts/ocr_benchmark.py']
    paths += [ROOT/'src/pcr_maa'/name for name in ('native.py', 'replay.py',
              'guide_trial_characters.py', 'guide_trial_battle.py', 'guide_identity.py',
              'labyrinth/character_recognition.py')]
    paths += list((ROOT/'assets/resource/image/guide-trial').glob('battle-*.png'))
    paths += [ROOT/'assets/resource/model/ocr'/name for name in ('det.onnx', 'rec.onnx', 'keys.txt')]
    paths += [ROOT/'assets/labyrinth/character-pack'/name for name in ('icons.json', 'characters.landosolroster.json')]
    return {str(path.relative_to(ROOT)).replace('\\', '/'): digest(path) for path in paths}


def benchmark(manifest, output, repeats):
    from maa.custom_recognition import CustomRecognition
    from maa.resource import Resource
    from pcr_maa import native
    from pcr_maa.guide_trial_battle import battle_pipeline
    from pcr_maa.guide_trial_characters import GuideTrialCharacters
    from pcr_maa.guide_identity import read_source
    from pcr_maa.replay import SavedImages
    deny_external_io()
    samples = validate_samples(json.loads(manifest.read_text(encoding='utf-8')))
    output.mkdir(parents=True, exist_ok=False)
    native.RUNTIME = output/'native'
    bundle = output/'bundle'
    (bundle/'pipeline').mkdir(parents=True)
    (bundle/'model/ocr').mkdir(parents=True)
    for name in ('det.onnx', 'rec.onnx', 'keys.txt'):
        shutil.copy2(ROOT/'assets/resource/model/ocr'/name, bundle/'model/ocr'/name)
    base = battle_pipeline(samples[0]['stage'])
    nodes = {'GTBattleTimer': base['GTBattleTimer']}
    nodes['ClanHudBenchmark'] = dict(recognition='Custom', custom_recognition='ClanHudBenchmark',
        action='DoNothing', next=[], timeout=1, pre_delay=0, post_delay=0)
    (bundle/'pipeline/hud.json').write_text(json.dumps(nodes, ensure_ascii=False), encoding='utf-8')

    class ReadOnlyImages(SavedImages):
        def click(self, *args):
            raise AssertionError('offline_benchmark_input_forbidden')
        swipe = click

    class Probe(CustomRecognition):
        def __init__(self):
            super().__init__()
            self.reader, self.rows, self.error = None, [], None

        def analyze(self, context, argv):
            try:
                row = observe(context, argv.image, self.reader)
                self.rows.append(row)
                return self.AnalyzeResult(box=[0, 0, 1, 1], detail=row)
            except BaseException as exc:
                self.error = exc
                return None

    native.setup()
    resource = Resource()
    if not resource.use_cpu() or not native.wait_job(resource.post_bundle(bundle), seconds=30).succeeded:
        raise RuntimeError('benchmark_cpu_resource_load_failed')
    probe = Probe()
    if not resource.register_custom_recognition('ClanHudBenchmark', probe):
        raise RuntimeError('benchmark_callback_registration_failed')
    controller = ReadOnlyImages([[native.read_image(samples[0]['path'])]])
    tasker = native.bind(resource, controller)
    rows = []
    started = datetime.now(timezone.utc).isoformat()
    try:
        for sample in samples:
            source = read_source(sample['stage'])
            plan = dict(stage=sample['stage'], source_image_sha256=source['ImageSha256'],
                        unit_ids=[member['UnitId'] for member in source['Members']], stars=[5]*5)
            reader = GuideTrialCharacters(sample['stage'], plan=plan)
            probe.reader = reader
            image = native.read_image(sample['path'])
            controller.stages = [[image]]
            controller.stage = controller.frame = 0
            for repeat in range(repeats):
                before, captured = len(probe.rows), controller.frame
                start = time.perf_counter()
                job = native.wait_job(tasker.post_task('ClanHudBenchmark'), seconds=10)
                elapsed = time.perf_counter()-start
                if probe.error is not None:
                    raise probe.error
                if not job.succeeded or len(probe.rows) != before+1 or controller.frame != captured+1:
                    raise RuntimeError('benchmark_requires_one_frame_and_one_callback')
                row = dict(probe.rows[-1], sample=sample['id'], repeat=repeat,
                           first_for_frame=repeat == 0, process_cold=not rows,
                           end_to_end_seconds=elapsed, screenshot_calls=controller.frame-captured)
                if (row['remaining_seconds'] != sample['expected_remaining_seconds']
                        or row['controls_complete'] is not sample['expected_controls_complete']):
                    (output/'failure-readback.json').write_text(json.dumps(dict(expected=sample, observed=row),
                        ensure_ascii=False, indent=2), encoding='utf-8')
                    raise RuntimeError('saved_frame_readback_changed:' + sample['id'])
                rows.append(row)
        if controller.clicks or controller.swipes:
            raise RuntimeError('offline_benchmark_received_input')
    finally:
        stop = native.stop_tasker(tasker)
        if stop.get('idle') is not True:
            raise RuntimeError('benchmark_owner_teardown_incomplete:' + str(stop))
    report = dict(version=1, status='passed', evidence='offline_saved_frame', started_at=started,
                  repeats=repeats, manifest_sha256=digest(manifest), samples=samples,
                  code_and_resource_sha256=fingerprint(), rows=rows, timing=summarize(rows),
                  inference_provider='CPU', warm_summary_excludes='First read of each saved frame',
                  device_inputs=0, adb_connections=0, production_calibration_written=False,
                  seconds_axis_enabled=False, stop=stop,
                  limitations=['SavedImages memory copy, native clock OCR and full-team HUD readback only.',
                               'Existing deep-domain HUD frames; no clan page or device capture acceptance.',
                               'No full clan read_tick page guards, IPC latency, device capture, input, game clock progression or click readback measured.',
                               'These samples do not qualify a production timing budget or enable seconds-axis execution.'])
    (output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(evidence=report['evidence'], samples=len(samples), readings=len(rows),
                          timing=report['timing'], device_inputs=0), ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--samples', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=6)
    args = parser.parse_args()
    if not 2 <= args.repeats <= 100:
        parser.error('repeats must be between 2 and 100')
    if not args.output.resolve().is_relative_to((ROOT/'.runtime').resolve()):
        parser.error('output must be a new project .runtime directory')
    benchmark(args.samples.resolve(), args.output.resolve(), args.repeats)


if __name__ == '__main__':
    main()
