"""List every visual input rule and dispatch site; coverage needs separate replay evidence."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.ocr_inventory import inventory
INPUTS = {'Click', 'Swipe', 'LongPress', 'ClickKey', 'InputText', 'TouchDown', 'TouchMove', 'TouchUp'}


def audit(root=ROOT):
    root = Path(root)
    nodes, sources, fingerprints = {}, {}, {}
    for path in sorted((root / 'assets/resource/pipeline').glob('*.json')):
        relative = path.relative_to(root).as_posix()
        fingerprints[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        for name, value in json.loads(path.read_text(encoding='utf-8')).items():
            if name in nodes:
                raise ValueError('Duplicate pipeline node: ' + name)
            nodes[name], sources[name] = value, relative

    def leaves(rule, seen=()):
        if isinstance(rule, str):
            if rule not in nodes:
                return [dict(reference=rule, status='runtime_rule_required')]
            if rule in seen:
                return [dict(reference=rule, status='recognition_cycle')]
            return leaves(nodes[rule], (*seen, rule))
        method = rule.get('recognition', 'DirectHit')
        if method in ('And', 'Or'):
            return [leaf for child in rule.get('all_of', rule.get('any_of', [])) for leaf in leaves(child, seen)]
        return [dict(name=seen[-1] if seen else 'inline', method=method,
                     roi=rule.get('roi'), expected=rule.get('expected'),
                     threshold=rule.get('threshold'), only_rec=rule.get('only_rec', False),
                     custom_recognition=rule.get('custom_recognition'))]

    static = [dict(name=name, source=sources[name], action=value['action'],
                   recognition=value.get('recognition', 'DirectHit'),
                   target=value.get('target'), max_hit=value.get('max_hit'),
                   rules=leaves(value), replay_coverage='requires_saved_frame_result')
              for name, value in nodes.items() if value.get('action') in INPUTS]
    visual = inventory(root)
    dynamic = [row for row in visual['visual_nodes']
               if ':' in row['source'] and row['action'] in INPUTS]
    dispatch = []
    for path in sorted((root / 'src/pcr_maa').rglob('*.py')):
        source = path.read_text(encoding='utf-8')
        for node in ast.walk(ast.parse(source)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ('run', 'run_task', 'post_task', 'run_recognition')):
                dispatch.append(dict(source=path.relative_to(root).as_posix(), line=node.lineno,
                                     call=ast.get_source_segment(source, node)))
    return dict(schema=1, static_inputs=static, dynamic_factory_inputs=dynamic,
                factories=visual['factories'], python_construction_sites=visual['python_construction_sites'],
                dispatch_sites=dispatch, pipeline_sha256=fingerprints,
                limits=['Static inspection is not image recognition or live acceptance.',
                        'Parameterized Python constructions require their caller tests and actual frame evidence.',
                        'Unknown, obscured or unsupported layouts must not grant input permission.'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = audit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: len(report[key]) for key in
                     ('static_inputs', 'dynamic_factory_inputs', 'factories', 'python_construction_sites', 'dispatch_sites')}))
