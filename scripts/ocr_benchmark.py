"""Evaluate pinned native OCR models on grouped saved frames; no device/network IO."""
import argparse
import hashlib
import html
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def validate(samples, models):
    def require(condition, reason):
        if not condition:
            raise ValueError(reason)
    groups, hashes, ids = {}, {}, set()
    for row in samples:
        require(row['id'] not in ids, 'duplicate_sample_id')
        ids.add(row['id'])
        require(row['split'] in ('calibration', 'held_out'), 'invalid_split')
        require(type(row['expected']) is bool and row['category'] in ('fixed_text', 'numeric_identity', 'dynamic_text'), 'invalid_category_or_expectation')
        require(type(row.get('real_failure', False)) is bool, 'invalid_real_failure')
        require(row['group'] and row['entry'] and row['pipelines'], 'missing_sample_fields')
        require(digest(row['path']) == row['sha256'], 'sample_hash_mismatch')
        require(groups.setdefault(row['group'], row['split']) == row['split'], 'group_split_leak')
        require(hashes.setdefault(row['sha256'], row['group']) == row['group'], 'duplicate_image_group_leak')
    model_ids = set()
    for model in models:
        require(re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,39}', model['id']) is not None and model['id'] not in model_ids, 'invalid_or_duplicate_model_id')
        model_ids.add(model['id'])
        require(set(model['files']) == {'det.onnx', 'rec.onnx', 'keys.txt'}, 'model_files_incomplete')
        for name, row in model['files'].items():
            require(digest(Path(model['directory']) / name) == row['sha256'], 'model_hash_mismatch')


def deny_external_io():
    import socket
    from maa.controller import AdbController
    from maa.toolkit import Toolkit
    def deny(*args, **kwargs):
        raise RuntimeError('offline benchmark denies network and real ADB')
    socket.socket.connect = deny
    socket.create_connection = deny
    AdbController.__init__ = deny
    Toolkit.find_adb_devices = deny


def memory_bytes():
    if sys.platform == 'win32':
        import ctypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + [(name, ctypes.c_size_t) for name in
                ('peak', 'working', 'peak_paged', 'paged', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile')]
        counters = Counters(); counters.cb = ctypes.sizeof(counters)
        ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        if not ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise OSError('cannot_measure_process_memory')
        return counters.working, counters.peak
    import resource
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    return value, value


def worker(samples_path, models_path, model_id, output):
    from pcr_maa import native
    from pcr_maa.replay import SavedImages
    from maa.resource import Resource
    import cv2
    import numpy as np
    deny_external_io()
    samples = read(samples_path)['samples']; models = read(models_path)['models']
    validate(samples, models)
    model = next(row for row in models if row['id'] == model_id)
    work = output.parent / (model_id + '-work'); work.mkdir(parents=True, exist_ok=False)
    bundle = work / 'bundle'
    shutil.copytree(ROOT / 'assets/resource', bundle, ignore=shutil.ignore_patterns('model'))
    target = bundle / 'model/ocr'; target.mkdir(parents=True)
    for name in model['files']:
        shutil.copy2(Path(model['directory']) / name, target / name)
    native.RUNTIME = work / 'native'
    start = time.perf_counter(); resource = native.load_resource(bundle)
    controller = SavedImages([[np.zeros((720, 1280, 3), np.uint8)]])
    tasker = native.bind(resource, controller)
    loading = time.perf_counter() - start
    rows = []
    cold_endpoint = None
    try:
        for sample in samples:
            image = native.read_image(sample['path'])
            controller.stages = [[image]]
            controller.stage = controller.frame = 0
            for method, nodes in sample['pipelines'].items():
                # Timings cover the complete endpoint, including its page guards.
                # All input is an in-memory SavedImages controller.
                if not resource.override_pipeline(nodes):
                    raise RuntimeError('pipeline_override_failed')
                if not resource.override_pipeline({'BenchmarkEndpoint': dict(recognition='And',
                    all_of=[sample['entry']], action='DoNothing', next=[], on_error=[], timeout=1,
                    pre_delay=0, post_delay=0)}):
                    raise RuntimeError('endpoint_override_failed')
                times, hits, words = [], [], []
                for repeat in range(4):
                    start = time.perf_counter()
                    receipt = native.run(tasker, 'BenchmarkEndpoint')
                    elapsed = time.perf_counter() - start
                    if cold_endpoint is None:
                        cold_endpoint = elapsed
                    if repeat:
                        times.append(elapsed); hits.append(receipt['native_ok'] and any(
                            n['name']=='BenchmarkEndpoint' and n['completed'] for n in receipt['nodes']))
                    words = [n['recognition'] for n in receipt['nodes'] if n['recognition']]
                rows.append(dict(sample=sample['id'], group=sample['group'], split=sample['split'],
                    category=sample['category'], method=method, expected=sample['expected'],
                    real_failure=sample.get('real_failure', False), hits=hits, seconds=times, recognition=words))
    finally:
        native.stop_tasker(tasker)
    rss, peak = memory_bytes()
    output.write_text(json.dumps(dict(status='compatible', model=model_id, load_seconds=loading,
        cold_endpoint_seconds=cold_endpoint, cold_ready_seconds=loading + (cold_endpoint or 0),
        rss_bytes=rss, peak_rss_bytes=peak, model_bytes=sum(r['size'] for r in model['files'].values()), rows=rows),
        ensure_ascii=False, indent=2), encoding='utf-8')


def percentile(values, p=.95):
    return sorted(values)[max(0, __import__('math').ceil(len(values)*p)-1)] if values else None


def gates(results, baseline_id):
    baseline = next((r for r in results if r.get('model') == baseline_id and r.get('status') == 'compatible'), None)
    selections = {}
    for category in ('fixed_text', 'numeric_identity', 'dynamic_text'):
        choices = []
        for result in results:
            if result.get('status') != 'compatible' or baseline is None:
                continue
            rows = [r for r in result['rows'] if r['category'] == category and r['split'] == 'held_out' and r['method'] == 'method']
            # Model gains must be measured with the method held fixed. Otherwise
            # a crop improvement could incorrectly qualify a new weight set.
            before = {r['sample']: r for r in baseline['rows'] if r['category'] == category and r['method'] == 'method'}
            original = {r['sample']: r for r in baseline['rows'] if r['category'] == category and r['method'] == 'baseline'}
            comparable = [r for r in rows if r['sample'] in before]
            reasons = []
            if not comparable or not any(r['expected'] for r in rows) or not any(not r['expected'] for r in rows):
                reasons.append('missing_held_out_positive_or_negative')
            if any(any(r['hits']) for r in rows if not r['expected']): reasons.append('dangerous_false_accept')
            if any((all(before[r['sample']]['hits']) or all(original.get(r['sample'], before[r['sample']])['hits']))
                   and r['expected'] and not all(r['hits']) for r in comparable):
                reasons.append('baseline_success_regression')
            improved = any(r['real_failure'] and r['expected'] and all(r['hits']) and not any(before[r['sample']]['hits']) for r in comparable)
            if not improved: reasons.append('no_real_failed_image_improvement')
            p95 = percentile([s for r in rows for s in r['seconds']])
            old95 = percentile([s for r in comparable for s in before[r['sample']]['seconds']])
            if p95 is None or old95 is None or p95 > old95 + 1: reasons.append('p95_budget_or_coverage')
            choices.append(dict(model=result['model'], accepted=not reasons, reasons=reasons,
                                p95_seconds=p95, baseline_p95_seconds=old95))
        passed = [r for r in choices if r['accepted']]
        selections[category] = dict(selected=min(passed, key=lambda r:r['p95_seconds'])['model'] if passed else baseline_id,
                                    retained_baseline=not passed, candidates=choices)
    return selections


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--samples', required=True, type=Path); parser.add_argument('--models', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path); parser.add_argument('--worker')
    args = parser.parse_args()
    if args.worker:
        worker(args.samples, args.models, args.worker, args.output); return
    samples, models = read(args.samples), read(args.models)
    validate(samples['samples'], models['models'])
    args.output.mkdir(parents=True, exist_ok=False)
    results = []
    for model in models['models']:
        destination = args.output / (model['id'] + '.json')
        with (args.output / (model['id'] + '.log')).open('w', encoding='utf-8') as log:
            try:
                completed = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(Path(__file__).resolve()),
                    '--samples', str(args.samples.resolve()), '--models', str(args.models.resolve()), '--output', str(destination.resolve()),
                    '--worker', model['id']], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=300)
                result = read(destination) if completed.returncode == 0 else dict(model=model['id'], status='incompatible_or_worker_failed', exit_code=completed.returncode)
            except subprocess.TimeoutExpired:
                result = dict(model=model['id'], status='timeout')
        results.append(result)
        print(model['id'], result['status'], flush=True)
    report = dict(schema=1, framework='5.13.0', samples_sha256=digest(args.samples), models_sha256=digest(args.models),
        corpus_groups=len({r['group'] for r in samples['samples']}), results=results,
        selection=gates(results, models['baseline']), limitations=samples.get('limitations', []), device_inputs=0, installed=False)
    (args.output/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    # HTML omits images and raw OCR (which may contain account identifiers).
    summary = {k:v for k,v in report.items() if k != 'results'}
    summary['models'] = [{k:v for k,v in r.items() if k != 'rows'} for r in results]
    (args.output/'report.html').write_text('<!doctype html><meta charset="utf-8"><title>OCR 离线评测</title><h1>OCR 离线评测</h1><p>保存画面；未操作实际安装或游戏。原始 OCR 仅在本地 JSON。</p><pre>'+html.escape(json.dumps(summary, ensure_ascii=False, indent=2))+'</pre>', encoding='utf-8')


if __name__ == '__main__':
    main()
