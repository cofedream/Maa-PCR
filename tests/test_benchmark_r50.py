"""A benchmark must import each requested tree and reject changed business results."""
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/benchmark_r50.py'
spec = spec_from_file_location('benchmark_r50', SCRIPT)
benchmark = module_from_spec(spec)
spec.loader.exec_module(benchmark)


def synthetic_source(root, result):
    package = root / 'src/pcr_maa'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('', encoding='utf-8')
    module = package / 'preset_confirmation.py'
    module.write_text('from types import SimpleNamespace\n'
        'class ConfirmationPage:\n'
        '    def analyze(self, context, argv):\n'
        f'        return SimpleNamespace(detail={{"kind": "short", "rows": [{{"count": 3}}], '
        f'"summary": {{"times": 3}}, "source_tag": {result!r}}})\n', encoding='utf-8')
    pipeline = root / 'assets/resource/pipeline'
    pipeline.mkdir(parents=True)
    (pipeline / 'preset_execution.json').write_text('{}', encoding='utf-8')
    return module


def test_worker_loads_the_requested_source_in_an_isolated_process(tmp_path):
    root = tmp_path / 'requested-baseline'
    module = synthetic_source(root, 'actual-baseline')
    worker = benchmark.Worker(root, tmp_path / 'worker', ('confirmation_short_1',))
    try:
        assert worker.metadata['root'] == str(root.resolve())
        assert worker.metadata['files']['src/pcr_maa/preset_confirmation.py'] == hashlib.sha256(module.read_bytes()).hexdigest()
        result = worker.request(dict(case='confirmation_short_1'))
        assert result['business']['source_tag'] == 'actual-baseline'
        assert result['counts'] == dict(screenshot=0, recognition=0, input=0, native_ocr=None)
        assert worker.request(dict(finish=True))['source_unchanged'] is True
    finally:
        worker.close()


def test_changed_business_result_rejects_the_comparison_without_success_report(tmp_path):
    baseline, candidate = tmp_path / 'baseline', tmp_path / 'candidate'
    synthetic_source(baseline, 'baseline-result')
    synthetic_source(candidate, 'different-candidate-result')
    output = tmp_path / 'timing/report.json'
    with pytest.raises(ValueError, match='business results differ'):
        benchmark.benchmark(baseline, candidate, output, cases=('confirmation_short_1',))
    assert not output.exists()


def test_existing_report_is_preserved_before_starting_workers(tmp_path):
    output = tmp_path / 'existing-report.json'
    original = b'{"historical_evidence":"preserve"}\n'
    output.write_bytes(original)
    with pytest.raises(FileExistsError, match='Preserve existing benchmark report'):
        benchmark.benchmark(tmp_path / 'absent-baseline', tmp_path / 'absent-candidate', output)
    assert output.read_bytes() == original
