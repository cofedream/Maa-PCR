"""Compare page/slot readback and callback calls without connecting a device."""
import argparse
from collections import Counter
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
from math import ceil
from pathlib import Path
import statistics
import subprocess
import sys
import time

import cv2
import numpy as np

SOURCE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SOURCE / 'src'), str(SOURCE / 'tests')]
from pcr_maa import native
from pcr_maa.preset_execution_native import NativePreset
from pcr_maa.replay import SavedImages
from test_r39_recognition import ConfirmationPage, args, confirmation_context


def stats(samples):
    return dict(median_seconds=statistics.median(samples),
                p95_seconds=sorted(samples)[ceil(.95 * len(samples)) - 1])


def synthetic_page(slot, render_python):
    renderer = r"""import sys
from PIL import Image, ImageDraw, ImageFont
image = Image.new('RGB', (1280, 720), 'white')
draw = ImageDraw.Draw(image)
font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 28)
if sys.argv[1] != 'None':
    draw.text((485, 32), '关卡一览', font=font, fill='black')
    draw.text((700, 536), '使用券张数', font=font, fill='black')
else:
    draw.text((10, 300), 'UNKNOWN', font=font, fill='black')
image.save(sys.stdout.buffer, format='PNG')
"""
    encoded = subprocess.run([render_python, '-X', 'utf8', '-B', '-c', renderer, str(slot)],
                             capture_output=True, check=True).stdout
    result = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
    if slot is not None:
        x = 85 + (slot - 1) * 157
        color = cv2.cvtColor(np.uint8([[[107, 255, 255]]]), cv2.COLOR_HSV2BGR)[0, 0]
        result[103:116, x:x+16] = color
    return result


def benchmark(baseline, output, render_python):
    native.RUNTIME = output.parent / 'native'
    resources = dict(r38=native.load_resource(baseline / 'assets/resource'),
                     r39=native.load_resource())
    report = dict(evidence='public synthetic frames and callbacks; not live-game acceptance',
                  warmups_per_case=5, paired_samples_per_case=20, real_game_inputs=0,
                  scope='page/slot readback and direct confirmation callback invocations',
                  native_ocr_invocations=None,
                  native_ocr_count_note='SDK receipts do not expose all internal And-node OCR calls',
                  fingerprints={}, cases=[])
    for revision, root in (('r38', baseline), ('r39', SOURCE)):
        report['fingerprints'][revision] = {str(path.relative_to(root)):
            hashlib.sha256(path.read_bytes()).hexdigest() for path in (
                root / 'assets/resource/pipeline/presets.json',
                root / 'src/pcr_maa/preset_confirmation.py',
                root / 'src/pcr_maa/preset_execution_native.py')}
    for name, slot in [('selected_target_slot', 2), ('selected_wrong_slot', 3), ('unknown_page', None)]:
        image = synthetic_page(slot, render_python)
        controllers = {version: SavedImages([[image]]) for version in resources}
        taskers = {version: native.bind(resource, controllers[version]) for version, resource in resources.items()}
        port = NativePreset(taskers['r39'])
        timings, screenshots, cold, proof = dict(r38=[], r39=[]), dict(r38=[], r39=[]), {}, {}
        def run(version):
            controller = controllers[version]
            before = controller.frame
            started = time.perf_counter()
            result = native.observe_tasker(taskers[version]) if version == 'r38' else port.verify_preset(2)['ocr_receipt']
            elapsed = time.perf_counter() - started
            observation = result['observation']
            expected_page = 'unknown' if slot is None else 'presets'
            assert result['native_ok'] is True and observation['page'] == expected_page, result
            assert observation['selected_slot'] == slot, result
            assert controller.frame > before, 'readback must acquire a fresh frame'
            assert not controller.clicks and not controller.swipes
            return elapsed, controller.frame - before, result
        for version in resources:
            elapsed, screens, receipt = run(version)
            cold[version] = dict(elapsed_seconds=elapsed, screenshots=screens)
            proof[version] = receipt
        for _ in range(5):
            run('r38'); run('r39')
        for iteration in range(20):
            for version in (('r38', 'r39') if iteration % 2 == 0 else ('r39', 'r38')):
                elapsed, screens, _ = run(version)
                timings[version].append(elapsed)
                screenshots[version].append(screens)
        report['cases'].append(dict(name=name, cold=cold, r38=stats(timings['r38']), r39=stats(timings['r39']),
            classification='positive' if slot == 2 else 'negative_wrong_slot_or_page',
            median_ratio=statistics.median(timings['r39']) / statistics.median(timings['r38']),
            samples_seconds=timings, screenshots=screenshots, input_count=0, receipts=proof))
        print(name + ' complete', file=sys.stderr, flush=True)
    spec = spec_from_file_location('pcr_maa._baseline_confirmation', baseline / 'src/pcr_maa/preset_confirmation.py')
    original = module_from_spec(spec)
    spec.loader.exec_module(original)
    for kind in ('short', 'top', 'bottom', 'page'):
        for rows in (1, 3):
            timings, calls = dict(r38=[], r39=[]), {}
            def run(version):
                context = confirmation_context(rows, kind=kind)
                page = (original.ConfirmationPage if version == 'r38' else ConfirmationPage)()
                start = time.perf_counter()
                result = page.analyze(context, args(kind))
                assert result is not None
                return time.perf_counter() - start, result.detail, Counter(name for name, _, _ in context.calls)
            for _ in range(5):
                for version in ('r38', 'r39'): run(version)
            for iteration in range(20):
                details = {}
                for version in (('r38', 'r39') if iteration % 2 == 0 else ('r39', 'r38')):
                    elapsed, details[version], counts = run(version)
                    calls[version] = dict(counts)
                    timings[version].append(elapsed)
                assert details['r38'] == details['r39']
            report['cases'].append(dict(name=f'confirmation_{kind}_{rows}_rows',
                timing_scope='synthetic Python callback only; not native OCR latency',
                r38=stats(timings['r38']), r39=stats(timings['r39']),
                direct_recognition_calls=calls, business_and_raw_evidence_equal=True, input_count=0))
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True, type=Path, help='Frozen R38 source directory')
    parser.add_argument('--output', required=True, type=Path, help='JSON evidence path under the project runtime')
    parser.add_argument('--render-python', default=sys.executable, help='Python with working Pillow for synthetic Chinese text')
    options = parser.parse_args()
    baseline, output = options.baseline.resolve(), options.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report = benchmark(baseline, output, options.render_python)
    print(json.dumps([{key: case[key] for key in ('name', 'r38', 'r39')} for case in report['cases']], indent=2))


if __name__ == '__main__':
    main()
