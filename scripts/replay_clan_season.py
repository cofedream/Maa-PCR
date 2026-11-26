"""Replay v2 clan page layouts on labeled screenshots without opening a device.

The frame list specifies period, content_version and cases with page, file, expect
and optionally phase, boss, damage or action. An action checks its visible label.
Paths in the list are relative to that list. See docs/CLAN_CALIBRATION.md.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))


def summarize(cases, pages):
    summary = {}
    for page in pages:
        rows = [r for r in cases if r['page'] == page and 'action' not in r]
        assessed = [r for r in rows if r['matched'] is not None]
        if not rows:
            status = 'missing'
        elif any(r['matched'] != r['expect'] for r in assessed):
            status = 'changed'
        elif len(assessed) != len(rows) or not any(r['expect'] for r in rows):
            status = 'unverified'
        else:
            status = 'positive_only' if all(r['expect'] for r in rows) else 'matched'
        summary[page] = status
    return summary


def load_cases(frames):
    data = json.loads(frames.read_bytes())
    if (not isinstance(data, dict) or not isinstance(data.get('period'), str)
            or not re.fullmatch(r'20\d{2}-(?:0[1-9]|1[0-2])', data['period'])
            or not isinstance(data.get('content_version'), str)
            or not re.fullmatch(r'\d{12}', data['content_version'])
            or not isinstance(data.get('cases'), list) or not data['cases']):
        raise ValueError('Expected period, content_version and labeled cases')
    for case in data['cases']:
        if (not isinstance(case, dict) or set(case) - {'page','file','expect','phase','boss','damage','action','slots'}
                or not isinstance(case.get('page'), str) or not isinstance(case.get('file'), str)
                or not case['file'] or type(case.get('expect')) is not bool
                or type(case.get('boss', 1)) is not int or not 1 <= case.get('boss', 1) <= 5
                or case.get('phase', 'C') not in ('B','C','D','E')
                or ('damage' in case and (case['page'] != 'result' or type(case['damage']) is not int or case['damage'] < 0))
                or ('action' in case and not isinstance(case['action'], str))
                or ('slots' in case and (case['page'] != 'lobby' or type(case['slots']) is not list
                    or len(case['slots']) != 5 or any(n is not None and (type(n) is not int or n < 1) for n in case['slots'])))):
            raise ValueError('Invalid labeled frame: ' + repr(case))
    return data


def observe(context, image, case, profile, period):
    from pcr_maa import clan_battle_native as battle
    reader = battle.ClanPageFields()
    page = case['page']
    spec = profile['pages'][page]
    failed, fields = [], {}

    def hit(name):
        seen = context.run_recognition(name, image)
        return seen is not None and seen.hit is True

    def read(field):
        return reader.word(context, image, f'CBField_{page}_{field}')

    if hit('LabyDisconnect6002Observed'):
        failed.append('disconnect')
    for index in range(len(spec['labels'])):
        if not hit(f'CBLabel_{page}_{index}'):
            failed.append(f'label_{index}')
    if page in ('formation', 'support') and hit('CBFormal_' + page):
        failed.append('formal_mode')
    for field in spec.get('fields', {}):
        try:
            fields[field] = read(field)
        except RuntimeError as exc:
            failed.append(str(exc))
    if 'period' in fields:
        # An archived date range has no printed year. Do not substitute today's clock.
        text = fields['period']
        displayed = text.replace('年', '-').replace('月', '')
        if '/' in text:
            month = int(text.split('/', 1)[0])
            matches_period = month == int(period[-2:])
        else:
            matches_period = displayed == period
        if not matches_period:
            failed.append('period')
    phase = profile.get('phase_labels', {}).get(fields.get('phase'))
    if 'phase' in case and 'phase' in fields and case['phase'] != phase:
        failed.append('phase')
    if 'boss' in fields:
        pattern = battle._boss_name_pattern(profile, [case.get('boss', 1)], phase=phase)
        if re.fullmatch(pattern, fields['boss']) is None:
            failed.append('boss_name_or_level')
    if 'damage' in fields:
        try:
            verify = read('damage_verify')
            fields['damage'] = battle.result_damage_line(fields['damage'], verify)
            if 'damage' in case and fields['damage'] != case['damage']:
                failed.append('expected_damage')
        except RuntimeError as exc:
            failed.append(str(exc))
    if page == 'target' and sum(hit('CBLiveState_' + flag) for flag in ('on','off','absent')) != 1:
        failed.append('live_state')
    if 'slots' in case:
        try:
            fields['slots'] = [reader.visible_boss_slot(context, image, boss)['round']
                               for boss in range(1, 6)]
            if fields['slots'] != case['slots']:
                failed.append('expected_lobby_slots')
        except RuntimeError as exc:
            failed.append(str(exc))
    if 'action' in case and not hit('CBActionLabel_' + case['action']):
        failed.append('action_label')
    return {'matched': not failed, 'failed': failed, 'fields': fields}


def replay(profile_path, frames_path, output, overlay=None):
    from maa.custom_recognition import CustomRecognition
    from pcr_maa import clan_battle_native as battle, native
    from pcr_maa.frame_recognition import same_frame_context
    from pcr_maa.replay import SavedImages
    from ocr_benchmark import deny_external_io

    profile_bytes = profile_path.read_bytes()
    profile = json.loads(profile_bytes)
    if profile.get('version') != 2 or profile.get('frame_size') != [1280, 720]:
        raise ValueError('Expected a 1280x720 v2 layout')
    manifest = load_cases(frames_path)
    inputs = {profile_path.resolve(), frames_path.resolve()}
    inputs.update((frames_path.parent / case['file']).resolve() for case in manifest['cases'])
    if output.suffix.lower() != '.json' or output.resolve() in inputs:
        raise ValueError('Choose a separate JSON report, outside the inputs')
    period = manifest['period']
    cases = []
    for case in manifest['cases']:
        if case['page'] not in battle.V2_PAGES:
            raise ValueError('Unknown page: ' + case['page'])
    class Probe(CustomRecognition):
        def analyze(self, context, argv):
            try:
                self.result = observe(same_frame_context(context, argv.image), argv.image, self.case, profile, period)
            except Exception as exc:
                self.error = exc
            return self.AnalyzeResult([0,0,1,1], {'observed': True})

    output.parent.mkdir(parents=True, exist_ok=True)
    deny_external_io()
    # Keep the registered resource, callback, controller and tasker alive until teardown.
    native.RUNTIME = output.parent / (output.stem + '-native')
    resource = native.load_resource(cpu_only=True)
    if overlay is not None:
        native.RESOURCE = overlay.resolve(strict=True)
        if not native.wait_job(resource.post_bundle(native.RESOURCE)).succeeded:
            raise RuntimeError('Could not load template bundle')
    probe = Probe()
    if not resource.register_custom_recognition('ClanSeasonLayoutReplay', probe):
        raise RuntimeError('Could not register replay recognizer')
    ctrl = SavedImages([[]])
    tasker = native.bind(resource, ctrl)
    try:
        for case in manifest['cases']:
            page = case['page']
            path = frames_path.parent / case['file']
            frame_sha = hashlib.sha256(path.read_bytes()).hexdigest()
            image = native.read_image(path)
            row = {**case, 'file': path.name, 'sha256': frame_sha}
            cases.append(row)
            spec = profile['pages'].get(page)
            if spec is None:
                row.update(matched=None, error='page_not_in_profile')
                continue
            plan = {'content_version': manifest['content_version'], 'boss': case.get('boss', 1)}
            try:
                nodes = battle.page_recognition_nodes(profile, plan, page)
                if 'slots' in case:
                    if 'boss_slots' not in spec:
                        raise ValueError('lobby_slot_guards_missing')
                    nodes.update(battle.simulation_navigation_pipeline(profile))
                if 'action' in case:
                    name = case['action']
                    action = profile['actions'].get(name, {})
                    if action.get('page') != page or not isinstance(action.get('label'), dict):
                        raise ValueError('no_simple_action_label:' + name)
                    nodes['CBActionLabel_' + name] = battle.action_label_node(name, action, plan)
            except (ValueError, KeyError, TypeError) as exc:
                row.update(matched=None, error=str(exc))
                continue
            nodes['ClanSeasonReplayFrame'] = dict(recognition='Custom', custom_recognition='ClanSeasonLayoutReplay',
                action='DoNothing', pre_delay=0, post_delay=0, next=[])
            if not resource.override_pipeline(nodes):
                raise RuntimeError('Could not load page recognizers')
            ctrl.stages = [[image]]
            probe.case, probe.result, probe.error = case, None, None
            before = ctrl.frame
            receipt = native.run(tasker, 'ClanSeasonReplayFrame')
            if probe.error is not None:
                raise probe.error
            if not receipt['native_ok'] or probe.result is None or ctrl.frame != before + 1:
                raise RuntimeError('Saved frame was not observed')
            row.update(probe.result)
    finally:
        if not native.stop_tasker(tasker)['idle'] or ctrl.clicks or ctrl.swipes:
            raise RuntimeError('Read-only replay did not close cleanly')
    action_cases = []
    for case in cases:
        if 'action' in case:
            action_cases.append(dict(page=f"{case['page']}/{case['action']}",
                                     expect=case['expect'], matched=case['matched']))
    report = dict(kind='clan_season_layout_replay', period=period, content_version=manifest['content_version'],
                  input_profile_sha256=hashlib.sha256(profile_bytes).hexdigest(),
                  manifest_sha256=hashlib.sha256(frames_path.read_bytes()).hexdigest(),
                  scope='v2 simulation: visible page labels, printed fields and requested action labels only',
                  device_inputs=0, capabilities=[], cases=cases,
                  actions=summarize(action_cases, sorted({c['page'] for c in action_cases})),
                  pages=summarize(cases, sorted(set(profile['pages']) | {c['page'] for c in cases})))
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous-profile', '--profile', required=True, type=Path)
    parser.add_argument('--frames', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--resource', type=Path, help='optional reviewed overlay with an image/ directory')
    args = parser.parse_args()
    report = replay(args.previous_profile, args.frames, args.output, args.resource)
    print(json.dumps(report['pages'], ensure_ascii=False))


if __name__ == '__main__':
    main()
