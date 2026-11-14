"""发布带资料身份的已有业务摘要。"""
import json
import sys
from pathlib import Path
from .catalog import load
from .baseline import analyze
from . import combine

SCHEMAS = {'documents': ['village', 'paragraphs', 'facets']}

def inspect(payload):
    snapshot = load(payload, SCHEMAS)
    return {'schema_version': 1, 'input_digest': snapshot.digest,
            'data_version': snapshot.metadata.get('data_version'), 'summary': analyze(snapshot)}


def _bind(snapshot, result):
    result = {'schema_version': 1, 'input_digest': snapshot.digest,
              'data_version': snapshot.metadata.get('data_version'), **result}
    return result


def build_reference(payload, query):
    """参照案例组合：证据绑定当前参与检索的修订。"""
    snapshot = load(payload, SCHEMAS)
    return _bind(snapshot, combine.plan(snapshot, query))


def build_relaxation(payload, query, facet):
    """放松单个硬条件后的新增可行文档与组合。"""
    snapshot = load(payload, SCHEMAS)
    return _bind(snapshot, combine.relax_condition(snapshot, query, facet))


def build_replacement(payload, query, excluded_id, replaced_id):
    """被排除案例无法替代当前组合某篇的原因。"""
    snapshot = load(payload, SCHEMAS)
    return _bind(snapshot, combine.explain_replacement(snapshot, query, excluded_id, replaced_id))

def main():
    if len(sys.argv) != 2:
        raise SystemExit('请指定资料JSON路径')
    payload = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    print(json.dumps(inspect(payload), ensure_ascii=False, allow_nan=False, indent=2))

if __name__ == '__main__':
    main()
