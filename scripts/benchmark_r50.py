"""Compare actual source trees using public synthetic callbacks, without a device."""
import argparse
from collections import Counter
from contextlib import redirect_stdout
import hashlib
import json
from math import ceil
from pathlib import Path
import socket
import statistics
import subprocess
import sys
import time
from types import SimpleNamespace as NS
from uuid import uuid4


CASES = tuple(f'confirmation_{kind}_{rows}' for kind in ('short', 'top', 'bottom', 'page')
              for rows in (1, 3)) + ('confirmation_missing_count', 'confirmation_unsafe',
                                     'labyrinth_reward', 'labyrinth_map', 'labyrinth_map_date_changed')


def found(text=None, *, hit=True, words=None):
    words = words if words is not None else ([] if text is None else
        [dict(text=text, score=.99, box=[1, 1, 20, 20])])
    return NS(hit=hit, raw_detail=dict(filtered=words, best=words[0] if words else None),
              reco_id=123, box=[0, 0, 1, 1])


class RecognitionContext:
    def __init__(self, results, pipeline=None):
        self.results, self.pipeline = results, pipeline
        self.calls = []
        self.tasker = NS(stopping=False)

    def run_recognition(self, name, image, override=None):
        self.calls.append(name)
        return self.results.get(name, found(hit=False))

    def get_node_data(self, name):
        return self.pipeline[name]


def confirmation_fixture(root, case, image):
    from pcr_maa.preset_confirmation import ConfirmationPage
    parts = case.split('_')
    negative = parts[1] in ('missing', 'unsafe')
    kind, rows = ('short', 1) if negative else (parts[1], int(parts[2]))
    results = dict(ExecConfirmHeader=found(), ExecConfirmStamina=found(str(rows * 10)),
        ExecConfirmTickets=found(str(rows * 3)), ExecOwnedStamina=found('100'),
        ExecStageTotal=found(str(rows) + '处'), ExecTimesTotal=found(str(rows * 3)),
        ExecRowCategory=found('主线关卡'), ExecRowDifficulty=found('高难'),
        ExecRowCount=found('3'), ExecRowRemaining=found('3/3'), ExecRowSkipLabel=found('跳过次数'),
        ExecRowsOCR=found(words=[dict(text=f'测试1-{i + 1}', score=.99,
            box=[100, 140 + i * 80, 150, 20]) for i in range(rows)]))
    if kind == 'page':
        results.update(ExecRailBlue=found(), ExecRailGray=found())
    elif kind == 'top':
        results.update(ExecBlueTop=found(), ExecGrayBottom=found())
    elif kind == 'bottom':
        results.update(ExecGrayTop=found(), ExecBlueBottom=found())
    if case == 'confirmation_missing_count':
        results['ExecRowCount'] = found(hit=False)
    elif case == 'confirmation_unsafe':
        results['ExecUnsafeStop'] = found()
    pipeline = json.loads((root / 'assets/resource/pipeline/preset_execution.json').read_text(encoding='utf-8'))
    context = RecognitionContext(results, pipeline)
    callback, argv = ConfirmationPage(), NS(image=image, custom_recognition_param=json.dumps(kind))
    return lambda: callback.analyze(context, argv), context, negative


def reward_fixture(image):
    from pcr_maa.labyrinth.character_recognition import CharacterRecognition
    word = dict(text='还剩2次', box=[500, 100, 120, 40], score=.99)
    card = dict(slot=0, character_id='1001', name='佩可莉姆', trusted=True, identity_source='maa_name')
    recognizer = NS(stopped=lambda: False,
        reward_offers=lambda *a, **kw: dict(page='reward', cards=[dict(card)]))
    results = {name: found(words=[word]) for name in ('LabyRoleRewardTitle', 'LabyRoleRewardRemaining',
                'LabyRoleRewardName0', 'LabyRoleRewardName1', 'LabyRoleRewardName2')}
    context = RecognitionContext(results)
    callback = CharacterRecognition('benchmark', recognizer)
    argv = NS(image=image, custom_recognition_param=json.dumps(dict(mode='read', page='reward')))
    return lambda: callback.analyze(context, argv), context, False


def map_fixture(image, case):
    from pcr_maa.labyrinth import autoplay, scene
    from pcr_maa.labyrinth.routes import Node, Route

    class SceneContext(RecognitionContext):
        def __init__(self):
            super().__init__({})
            self.observer = scene.SceneRecognition()
            self.keys = set(scene._RULES['map'][0]) | {'AREA'}
            if case.endswith('date_changed'):
                self.keys.add('DATE')

        def run_recognition(self, name, frame, override=None):
            self.calls.append(name)
            if name == scene.NODE:
                params = (override or {}).get(name, {}).get('custom_recognition_param', {})
                result = self.observer.analyze(self, NS(image=frame, custom_recognition_param=params))
                return NS(hit=result.box is not None, raw_detail=result.detail)
            if name.startswith('LabySceneTemplate_'):
                key = name[len('LabySceneTemplate_'):].rsplit('_', 1)[0]
            elif name.startswith('LabySceneWord_'):
                key = name[len('LabySceneWord_'):]
            else:
                return None
            return found('1' if key == 'AREA' else 'test') if key in self.keys else None

    nodes = (Node(10101, 1, 1, 1, 1, (10201,)), Node(10201, 1, 2, 1, 2, ()))
    route = Route(99, nodes, nodes, 'protocol')
    columns = [[dict(block_type=2, active=True, box=[400, 400, 100, 100])]]
    callback = autoplay.RouteTargetRecognition(NS(collect=lambda frame: columns))
    callback.route, callback.current = route, route.nodes[0]
    context = SceneContext()
    argv = NS(image=image, custom_recognition_param=json.dumps(dict(mode='read')))
    return lambda: callback.analyze(context, argv), context, False


def fixture(root, case):
    import numpy as np
    image = np.zeros((720, 1280, 3), np.uint8)
    if case.startswith('confirmation_'):
        return confirmation_fixture(root, case, image)
    if case == 'labyrinth_reward':
        return reward_fixture(image)
    if case in ('labyrinth_map', 'labyrinth_map_date_changed'):
        return map_fixture(image, case)
    raise ValueError(f'Unknown fixture: {case}')


def source_fingerprints(root):
    files = {}
    for name, module in tuple(sys.modules.items()):
        if name == 'pcr_maa' or name.startswith('pcr_maa.'):
            path = Path(module.__file__).resolve()
            if not path.is_relative_to(root / 'src'):
                raise RuntimeError(f'{name} loaded outside requested source: {path}')
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    for relative in ('assets/resource/pipeline/preset_execution.json',
                     'assets/resource/image/labyrinth/scene-anchors.json'):
        path = root / relative
        if path.is_file():
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(files.items()))


def deny_external_access(*args, **kwargs):
    raise RuntimeError('Offline benchmark prohibits network and real device access')


def check_result(case, result, negative):
    if (result is None) != negative:
        raise AssertionError(f'{case}: unexpected acceptance or rejection')
    if negative:
        return None
    detail = result.detail
    if case.startswith('confirmation_'):
        _, kind, rows = case.split('_')
        valid = (detail.get('kind') == kind and len(detail.get('rows', [])) == int(rows)
                 and detail.get('summary', {}).get('times') == int(rows) * 3)
    elif case == 'labyrinth_reward':
        valid = detail.get('page') == 'reward' and detail.get('remaining') == 2
    elif case.endswith('date_changed'):
        valid = (detail.get('status') == 'scene_changed'
                 and detail.get('scene', {}).get('kind') == 'date_changed')
    else:
        valid = detail.get('status') == 'target_observed' and detail.get('target', {}).get('target_block_id') == 10201
    if not valid:
        raise AssertionError(f'{case}: incorrect synthetic business result')
    return detail


def worker_main(root, runtime, cases):
    root = root.resolve()
    sys.path.insert(0, str(root / 'src'))
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = deny_external_access
    with redirect_stdout(sys.stderr):
        from maa import controller
        from maa.toolkit import Toolkit
        controller.AdbController = Toolkit.find_adb_devices = deny_external_access
        for case in cases:
            fixture(root, case)
        native = sys.modules.get('pcr_maa.native')
        if native is not None:
            native.RUNTIME = runtime.resolve()
        fingerprints = source_fingerprints(root)
    print(json.dumps(dict(root=str(root), files=fingerprints)), flush=True)
    for line in sys.stdin:
        command = json.loads(line)
        if command.get('finish'):
            current = source_fingerprints(root)
            if current != fingerprints:
                raise RuntimeError('Source changed during benchmark')
            print(json.dumps(dict(source_unchanged=True)), flush=True)
            return
        case = command['case']
        with redirect_stdout(sys.stderr):
            action, context, negative = fixture(root, case)
            started = time.perf_counter()
            result = action()
            elapsed = time.perf_counter() - started
        detail = check_result(case, result, negative)
        print(json.dumps(dict(elapsed_seconds=elapsed, business=detail,
            raw_result=detail, counts=dict(screenshot=0, recognition=len(context.calls), input=0,
                                         native_ocr=None), recognition_nodes=dict(Counter(context.calls))),
            ensure_ascii=False), flush=True)


class Worker:
    def __init__(self, root, runtime, cases):
        runtime.mkdir(parents=True)
        self.log = (runtime / 'stderr.log').open('w', encoding='utf-8')
        command = [sys.executable, '-X', 'utf8', '-B', str(Path(__file__).resolve()),
                   '--worker-source', str(root), '--worker-runtime', str(runtime)]
        for case in cases:
            command += ['--case', case]
        self.process = subprocess.Popen(command, cwd=runtime, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.log, text=True, encoding='utf-8', bufsize=1)
        try:
            self.metadata = self.read()
        except Exception:
            self.close()
            raise

    def read(self):
        line = self.process.stdout.readline()
        if not line:
            self.process.wait()
            raise RuntimeError(f'Benchmark worker failed ({self.process.returncode}); see {self.log.name}')
        return json.loads(line)

    def request(self, command):
        self.process.stdin.write(json.dumps(command) + '\n')
        self.process.stdin.flush()
        return self.read()

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()
        self.log.close()


def stats(samples):
    return dict(median_seconds=statistics.median(samples),
                p95_seconds=sorted(samples)[ceil(.95 * len(samples)) - 1])


def require_equal(case, pair):
    if pair['baseline']['business'] != pair['candidate']['business']:
        raise ValueError(f'{case}: baseline/candidate business results differ')


def benchmark(baseline, candidate, output, *, cases=CASES):
    baseline, candidate, output = baseline.resolve(), candidate.resolve(), output.resolve()
    if output.exists():
        raise FileExistsError(f'Preserve existing benchmark report; choose a new output: {output}')
    output.parent.mkdir(parents=True, exist_ok=True)
    runtime = output.parent / ('workers-' + uuid4().hex)
    workers = {}
    report = dict(evidence='public synthetic callbacks; not native OCR, device or live-game acceptance',
        warmups_per_case=5, paired_samples_per_case=20, real_game_inputs=0,
        timing_scope='actual Python callback only; excludes fixture construction and subprocess IPC',
        native_ocr_count_note='Synthetic recognition responses do not execute the SDK OCR engine',
        fixture_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), cases=[])
    try:
        for label, root in (('baseline', baseline), ('candidate', candidate)):
            workers[label] = Worker(root, runtime / label, cases)
        report['sources'] = {label: worker.metadata for label, worker in workers.items()}
        for case in cases:
            timings = dict(baseline=[], candidate=[])
            counts = dict(baseline=[], candidate=[])
            raw_equal = True
            for iteration in range(25):
                order = ('baseline', 'candidate') if iteration % 2 == 0 else ('candidate', 'baseline')
                pair = {label: workers[label].request(dict(case=case)) for label in order}
                require_equal(case, pair)
                raw_equal &= pair['baseline']['raw_result'] == pair['candidate']['raw_result']
                if iteration >= 5:
                    for label in order:
                        timings[label].append(pair[label]['elapsed_seconds'])
                        counts[label].append(pair[label]['counts'])
            report['cases'].append(dict(name=case,
                baseline=stats(timings['baseline']), candidate=stats(timings['candidate']),
                median_ratio=statistics.median(timings['candidate']) / statistics.median(timings['baseline']),
                samples_seconds=timings, call_counts=counts, business_results_equal=True,
                raw_evidence_equal=raw_equal, business_result=pair['baseline']['business'],
                recognition_nodes={label: pair[label]['recognition_nodes'] for label in workers}))
            print(case + ' complete', file=sys.stderr, flush=True)
        report['source_unchanged_during_run'] = all(worker.request(dict(finish=True))['source_unchanged']
                                                   for worker in workers.values())
        with output.open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        return report
    finally:
        for worker in workers.values():
            worker.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, help='Frozen baseline source directory')
    parser.add_argument('--candidate', type=Path, help='Candidate source directory')
    parser.add_argument('--output', type=Path, help='JSON evidence path under the project .runtime')
    parser.add_argument('--case', action='append', choices=CASES)
    parser.add_argument('--worker-source', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--worker-runtime', type=Path, help=argparse.SUPPRESS)
    options = parser.parse_args()
    cases = tuple(options.case or CASES)
    if options.worker_source:
        worker_main(options.worker_source, options.worker_runtime, cases)
    elif options.baseline and options.candidate and options.output:
        report = benchmark(options.baseline, options.candidate, options.output, cases=cases)
        print(json.dumps([{key: case[key] for key in ('name', 'baseline', 'candidate')}
                          for case in report['cases']], indent=2))
    else:
        parser.error('--baseline, --candidate and --output are required')


if __name__ == '__main__':
    main()
