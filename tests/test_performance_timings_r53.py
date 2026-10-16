"""Incremental native scalar collection; synthetic logs, no controller input."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from pcr_maa.labyrinth import timing


class Source:
    def add_sink(self, sink):return 1
    def add_context_sink(self, sink):return 2
    def remove_sink(self, number):pass
    def remove_context_sink(self, number):pass


def collector(path):
    return timing.NativeTimings(Source(), Source(), timing.Timings(), trace_path=path)


def capture(value):
    return f'[DBG][MaaNS::CtrlUnitNS::ControlUnitMgr::screencap] | leave, {value}ms\n'


def recognition(value, kind='OCRer'):
    return f'[DBG][MaaNS::VisionNS::{kind}::analyze] synthetic [cost={value}ms]\n'


def append(path, text):
    with path.open('a', encoding='utf-8') as handle:handle.write(text)


def test_snapshot_excludes_existing_files_and_rotation_resumes_same_identity(tmp_path):
    path=tmp_path/'maafw.log'
    old=tmp_path/'maafw.bak.2026.10.16-02.01.00.1.log'
    path.write_text(capture(900), encoding='utf-8')
    old.write_text(capture(800), encoding='utf-8')
    native=collector(path)
    append(path,capture(1))
    native.finish_native_trace()
    rotated=tmp_path/'maafw.bak.2026.10.16-02.02.00.22.log'
    path.rename(rotated)
    append(rotated,capture(2))
    path.write_text(capture(3)+recognition(4), encoding='utf-8')
    native.finish_native_trace()
    native.finish_native_trace()
    result=native.timings.payload()
    assert result['screenshot']['count']==3 and result['screenshot']['total_ms']==6
    assert result['recognition']['count']==1 and result['recognition']['total_ms']==4
    assert set(result)==timing.Timings.categories
    assert native.trace_diagnostics()==dict(status='collected',issues=[],files_seen=3,files_read=3)
    native.close()


def test_only_exact_current_and_timestamp_backup_family_is_read(tmp_path):
    path=tmp_path/'maafw.log'
    native=collector(path)
    for index, milliseconds in enumerate(('1','12','123'),1):
        (tmp_path/f'maafw.bak.2026.10.16-02.02.00.{milliseconds}.log').write_text(capture(index),encoding='utf-8')
    for name in ('maafw.log.1','maafw.bak.other.log','other.bak.2026.10.16-02.02.00.1.log',
                 'maafw.bak.2026.10.16-02.02.00.1234.log','maafw.bak.2026.10.16-02.02.00.1.log.copy'):
        (tmp_path/name).write_text(capture(999),encoding='utf-8')
    native.finish_native_trace()
    assert native.timings.payload()['screenshot']['total_ms']==6
    assert native.trace_diagnostics()['files_seen']==3
    native.close()


def test_partial_line_waits_for_completion_and_repeated_summary_never_recounts(tmp_path):
    path=tmp_path/'maafw.log'
    native=collector(path)
    path.write_text(recognition(12).rstrip('\n'),encoding='utf-8')
    native.finish_native_trace()
    assert native.timings.payload()['recognition'] is None
    append(path,'\n'+capture(3))
    native.finish_native_trace()
    append(path,recognition(4))
    native.finish_native_trace()
    native.finish_native_trace()
    result=native.timings.payload()
    assert result['recognition']['count']==2 and result['recognition']['total_ms']==16
    assert result['screenshot']['count']==1
    native.close()


@pytest.mark.parametrize('content',('', '[DBG] screencap -p process started\n',
    '[DBG][MaaNS::ControlUnitMgr::screencap] | enter\n'+recognition(80,'CustomRecognizer')))
def test_no_native_scalar_remains_unobserved_instead_of_zero(tmp_path,content):
    path=tmp_path/'maafw.log'
    native=collector(path)
    path.write_text(content,encoding='utf-8')
    native.finish_native_trace()
    result=native.timings.payload()
    assert result['screenshot'] is result['recognition'] is None
    native.close()


def test_real_zero_is_a_sample_and_only_six_leaf_vision_classes_are_counted(tmp_path):
    path=tmp_path/'maafw.log'
    native=collector(path)
    kinds=('OCRer','TemplateMatcher','ColorMatcher','FeatureMatcher','NeuralNetworkClassifier','NeuralNetworkDetector')
    path.write_text(capture(0)+''.join(recognition(0,kind) for kind in kinds)
        +recognition(900,'CustomRecognizer')+recognition(900,'AndRecognizer')
        +'[DBG][MaaNS::TaskNS::Recognizer::analyze] [cost=900ms]\n',encoding='utf-8')
    native.finish_native_trace()
    result=native.timings.payload()
    assert result['screenshot']['count']==1 and result['screenshot']['total_ms']==0
    assert result['recognition']['count']==6 and result['recognition']['total_ms']==0
    native.close()


def test_unreadable_trace_reports_incomplete_without_false_zero_or_input_evidence_change(tmp_path,monkeypatch):
    path=tmp_path/'maafw.log'
    native=collector(path)
    saved=native.tasker._labyrinth_input_evidence
    path.write_text(capture(5),encoding='utf-8')
    original=Path.open
    def unreadable(candidate,*args,**kwargs):
        if candidate==path:raise PermissionError('synthetic private text must not escape')
        return original(candidate,*args,**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path,'open',unreadable)
        with pytest.raises(RuntimeError,match='^native_trace_incomplete$'):native.finish_native_trace()
    assert native.timings.payload()['screenshot'] is None
    assert native.tasker._labyrinth_input_evidence is saved and saved=={}
    assert native.trace_diagnostics()['issues']==['trace_read_failed']
    with pytest.raises(RuntimeError,match='native_trace_incomplete'):native.finish_native_trace()
    assert native.timings.payload()['screenshot']['count']==1
    native.close()


def test_read_failure_after_complete_line_does_not_recount_that_line(tmp_path,monkeypatch):
    path=tmp_path/'maafw.log'
    native=collector(path)
    path.write_text(capture(2)+capture(3),encoding='utf-8')
    original=Path.open
    class FaultyReader:
        def __init__(self,handle):self.handle=handle;self.calls=0
        def __enter__(self):return self
        def __exit__(self,*args):return self.handle.__exit__(*args)
        def __getattr__(self,name):return getattr(self.handle,name)
        def readline(self):
            self.calls+=1
            if self.calls==2:raise OSError('synthetic read failure')
            return self.handle.readline()
    with monkeypatch.context() as patch:
        patch.setattr(Path,'open',lambda candidate,*args,**kwargs:
            FaultyReader(original(candidate,*args,**kwargs)) if candidate==path else original(candidate,*args,**kwargs))
        with pytest.raises(RuntimeError,match='native_trace_incomplete'):native.finish_native_trace()
    with pytest.raises(RuntimeError,match='native_trace_incomplete'):native.finish_native_trace()
    assert native.timings.payload()['screenshot']['count']==2
    assert native.timings.payload()['screenshot']['total_ms']==5
    native.close()


@pytest.mark.parametrize('replacement',(capture(9),capture(999)*5))
def test_truncation_or_overwrite_is_incomplete_and_never_replays_old_bytes(tmp_path,replacement):
    path=tmp_path/'maafw.log'
    path.write_text(capture(8),encoding='utf-8')
    native=collector(path)
    append(path,capture(2))
    native.finish_native_trace()
    path.write_text(replacement,encoding='utf-8')
    with pytest.raises(RuntimeError,match='native_trace_incomplete'):native.finish_native_trace()
    assert native.trace_diagnostics()['issues']==['trace_truncated_or_rewritten']
    append(path,capture(3))
    with pytest.raises(RuntimeError,match='native_trace_incomplete'):native.finish_native_trace()
    assert native.timings.payload()['screenshot']['count']==2
    assert native.timings.payload()['screenshot']['total_ms']==5
    native.close()


def test_unreliable_identity_and_lost_rotation_are_explicitly_incomplete(tmp_path,monkeypatch):
    path=tmp_path/'maafw.log'
    path.write_text(capture(2),encoding='utf-8')
    native=collector(path)
    original=os.fstat
    with monkeypatch.context() as patch:
        patch.setattr(os,'fstat',lambda handle:SimpleNamespace(st_ino=0,st_dev=0,st_size=original(handle).st_size))
        with pytest.raises(RuntimeError,match='native_trace_incomplete'):native.finish_native_trace()
    assert 'trace_identity_unavailable' in native.trace_diagnostics()['issues']
    path.unlink()
    with pytest.raises(RuntimeError,match='native_trace_incomplete'):native.finish_native_trace()
    assert 'trace_file_missing' in native.trace_diagnostics()['issues']
    assert native.timings.payload()['screenshot'] is None
    native.close()
