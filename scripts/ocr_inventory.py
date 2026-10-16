"""Inventory static visual nodes and every Python recognition construction site."""
import argparse
import ast
import importlib
import inspect
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))


def inventory(root=ROOT):
    rows, sites, factories = [], [], []
    def walk(value, source, pointer):
        if isinstance(value, dict):
            if 'recognition' in value:
                rows.append(dict(source=source, node=pointer, method=value['recognition'],
                    threshold=value.get('threshold', 'Maa default'), roi=value.get('roi', 'Maa default'),
                    only_rec=value.get('only_rec', False), expected=value.get('expected'),
                    target=value.get('target'), guards=value.get('all_of', value.get('any_of', [])),
                    action=value.get('action', 'DoNothing'), sample_coverage='uncovered_until_manifest_match'))
            for key, child in value.items(): walk(child, source, pointer+'/'+str(key))
        elif isinstance(value, list):
            for i, child in enumerate(value): walk(child, source, pointer+'/'+str(i))
    for path in sorted((root/'assets/resource/pipeline').glob('*.json')):
        walk(json.loads(path.read_text(encoding='utf-8')), path.relative_to(root).as_posix(), '')
    for path in sorted((root/'src/pcr_maa').rglob('*.py')):
        source = path.read_text(encoding='utf-8'); tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and any(k.arg == 'recognition' for k in node.keywords) or isinstance(node, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == 'recognition' for k in node.keys):
                sites.append(dict(source=path.relative_to(root).as_posix(), line=node.lineno,
                    construction=ast.get_source_segment(source, node),
                    coverage='construction_site; runtime_parameters_require_samples'))
        # Only named, pure pipeline factories. Never execute business adapters.
        if path.parent.name != 'labyrinth': continue
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.endswith('_pipeline')]
        if not functions: continue
        module = importlib.import_module('pcr_maa.labyrinth.'+path.stem)
        for function in functions:
            fn = getattr(module, function.name)
            required = [p for p in inspect.signature(fn).parameters.values() if p.default is p.empty]
            supplied = {'expected_player_id':123, 'namespace':'Inventory', 'roi':(0,80,1280,530),
                        'count':1, 'templates':{str(i):dict(top=['synthetic.png'],bottom=['synthetic.png']) for i in range(1001,1006)}}
            if any(p.name not in supplied for p in required):
                factories.append(dict(source=path.name, factory=function.name, status='parameterized_unexpanded'))
                continue
            try:
                nodes = fn(**{p.name:supplied[p.name] for p in required})
                walk(nodes, path.relative_to(root).as_posix()+':'+function.name, '')
                factories.append(dict(source=path.name, factory=function.name, status='expanded', node_count=len(nodes)))
            except Exception as error:
                factories.append(dict(source=path.name, factory=function.name, status='unexpanded', reason=type(error).__name__))
    for row in rows:
        label = str(row['expected'] or '')
        row['review_class'] = ('numeric_or_identity' if any(word in label for word in ('0-9', '\\d', 'ID')) else
                               'fixed_text' if label.startswith('^') and label.endswith('$') else
                               'catalogue_or_dynamic' if row['method']=='OCR' else 'visual_or_composite')
        row['referenced_by'] = [other['source']+':'+other['node'] for other in rows
            if row['node'].strip('/') in [g for g in other['guards'] if isinstance(g, str)]]
    return dict(schema=1, visual_nodes=rows, python_construction_sites=sites, factories=factories,
        policy=dict(fixed_text='precise sampled crop + only_rec; whole composite positive and negative validation',
                    fixed_icon='template + page/word/position guards; no blanket replacement',
                    numeric_identity='exact complete parse, bounds and business relation; no fuzzy correction',
                    catalogue_or_dynamic='catalogue/context + unknown/ambiguity stop',
                    uncovered='retain original rule/model and safety checks'))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args(); result=inventory()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(visual_nodes=len(result['visual_nodes']), construction_sites=len(result['python_construction_sites']), factories=result['factories']), ensure_ascii=False))
