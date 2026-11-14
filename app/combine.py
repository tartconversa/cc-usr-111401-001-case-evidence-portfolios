"""参照案例组合：证据约束下的互补组合选择。

所有判断只基于快照中参与检索的最高修订资料；未被原文证据支持的条件
保持 unknown，不推断资料未陈述的结论。新增独立入口，不改动既有接口。
"""
import math
from .catalog import load

# 判定标签：met 明示符合 / violated 明示不符 / conflict 来源互相矛盾 / missing 证据缺失
STATUSES = ('met', 'violated', 'conflict', 'missing')
DEFAULT_EXACT_LIMIT = 16
DEFAULT_COMBINATION_LIMIT = 20


def _is_number(value):
    return type(value) in (int, float) and not isinstance(value, bool) and math.isfinite(value)


def _same_value(left, right):
    # bool 与 int 分别看待，要求类型与值都相同
    return type(left) is type(right) and left == right


def _index_documents(snapshot):
    docs = []
    for row in snapshot.rows('documents'):
        paragraphs = {p['id']: p for p in row['paragraphs']}
        token_set = set()
        for paragraph in row['paragraphs']:
            token_set.update(paragraph['tokens'])
        issues = []
        for facet, entries in row.get('facets', {}).items():
            for entry in entries:
                if entry['paragraph_id'] not in paragraphs:
                    issues.append({'facet': facet, 'paragraph_id': entry['paragraph_id'],
                                   'reason': 'dangling_paragraph_id'})
        docs.append({'id': row['id'], 'village': row['village'], 'revision': row['revision'],
                     'paragraphs': paragraphs, 'token_set': token_set,
                     'facets': row.get('facets', {}), 'evidence_issues': issues})
    return docs


def _condition_view(doc, facet, expect):
    entries = doc['facets'].get(facet, [])
    supporting, contradicting = [], []
    for entry in entries:
        paragraph = doc['paragraphs'].get(entry['paragraph_id'])
        if paragraph is None:
            continue  # 悬挂引用在 evidence_issues 中单独说明，不参与判定
        record = {'paragraph_id': entry['paragraph_id'], 'value': entry['value'],
                  'text': paragraph['text']}
        if _same_value(entry['value'], expect):
            supporting.append(record)
        else:
            contradicting.append(record)
    if not supporting and not contradicting:
        status = 'missing'
    elif supporting and contradicting:
        status = 'conflict'
    elif supporting:
        status = 'met'
    else:
        status = 'violated'
    return {'facet': facet, 'expect': expect, 'status': status,
            'supporting': supporting, 'contradicting': contradicting}


def _validate_condition(item, require_expect, label):
    if not isinstance(item, dict) or not isinstance(item.get('facet'), str) or not item['facet']:
        raise ValueError(f'{label}应为含非空facet的对象')
    if require_expect and 'expect' not in item:
        raise ValueError(f'{label}缺少expect')
    if 'expect' in item and item['expect'] is None:
        raise ValueError(f'{label}的expect不能为null')
    return {'facet': item['facet'], 'expect': item.get('expect', True)}


def _validate_query(query, docs):
    if not isinstance(query, dict):
        raise ValueError('查询应为对象')
    if not _is_number(query.get('budget')) or query['budget'] < 0:
        raise ValueError('budget应为非负有限数')
    penalty = query.get('similarity_penalty', 0)
    if not _is_number(penalty) or penalty < 0:
        raise ValueError('similarity_penalty应为非负有限数')
    region_cap = query.get('region_cap', 1)
    if type(region_cap) is not int or region_cap < 1:
        raise ValueError('region_cap应为正整数')
    mechanisms, seen = [], set()
    for item in query.get('mechanisms', []):
        condition = _validate_condition(item, False, 'mechanisms条目')
        weight = item.get('weight', 1)
        if not _is_number(weight) or weight < 0:
            raise ValueError('mechanism权重应为非负有限数')
        if condition['facet'] in seen:
            raise ValueError('mechanism facet重复')
        seen.add(condition['facet'])
        mechanisms.append({**condition, 'weight': weight})
    resources = []
    for item in query.get('resources', []):
        condition = _validate_condition(item, False, 'resources条目')
        weight = item.get('weight', 1)
        if not _is_number(weight) or weight < 0:
            raise ValueError('resource权重应为非负有限数')
        resources.append({**condition, 'weight': weight})
    hard_conditions = [_validate_condition(item, True, 'hard_conditions条目')
                       for item in query.get('hard_conditions', [])]
    costs = {}
    for key, cost in query.get('costs', {}).items():
        if not isinstance(key, str) or not _is_number(cost) or cost <= 0:
            raise ValueError('costs应为文档id到正有限数的映射')
        costs[key] = cost
    regions = query.get('regions', {})
    if not isinstance(regions, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in regions.items()):
        raise ValueError('regions应为村庄id到地域id的字符串映射')
    exact_limit = query.get('exact_limit', DEFAULT_EXACT_LIMIT)
    if type(exact_limit) is not int or not (1 <= exact_limit <= 22):
        raise ValueError('exact_limit应为1到22之间的整数')
    combination_limit = query.get('combination_limit', DEFAULT_COMBINATION_LIMIT)
    if type(combination_limit) is not int or combination_limit < 1:
        raise ValueError('combination_limit应为正整数')
    known = {doc['id'] for doc in docs}
    unknown_costs = set(costs) - known
    if unknown_costs:
        raise ValueError(f'costs包含未知文档: {sorted(unknown_costs)}')
    return {'budget': query['budget'], 'similarity_penalty': penalty, 'region_cap': region_cap,
            'mechanisms': mechanisms, 'resources': resources, 'hard_conditions': hard_conditions,
            'costs': costs, 'regions': regions, 'exact_limit': exact_limit,
            'combination_limit': combination_limit}


def _build_context(snapshot, query):
    docs = _index_documents(snapshot)
    q = _validate_query(query, docs)
    for doc in docs:
        doc['cost'] = q['costs'].get(doc['id'], 1)
        doc['region'] = q['regions'].get(doc['village'], doc['village'])
        doc['hard_views'] = [_condition_view(doc, c['facet'], c['expect']) for c in q['hard_conditions']]
        doc['mechanism_views'] = [_condition_view(doc, c['facet'], c['expect']) for c in q['mechanisms']]
        doc['resource_views'] = [_condition_view(doc, c['facet'], c['expect']) for c in q['resources']]
        doc['feasible'] = all(view['status'] == 'met' for view in doc['hard_views'])
        doc['resource_reward'] = sum(
            spec['weight'] for spec, view in zip(q['resources'], doc['resource_views'])
            if view['status'] == 'met')
    return q, docs


def _jaccard(left, right):
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _objective(combo, q):
    covered, reward = set(), 0.0
    for doc in combo:
        reward += doc['resource_reward']
        for spec, view in zip(q['mechanisms'], doc['mechanism_views']):
            if view['status'] == 'met':
                covered.add(spec['facet'])
    coverage = sum(spec['weight'] for spec in q['mechanisms'] if spec['facet'] in covered)
    penalty = 0.0
    for i, left in enumerate(combo):
        for right in combo[i + 1:]:
            penalty += _jaccard(left['token_set'], right['token_set'])
    penalty *= q['similarity_penalty']
    return {'objective': coverage + reward - penalty, 'coverage': coverage,
            'resource_reward': reward, 'similarity_penalty': penalty,
            'covered_facets': sorted(covered)}


def _can_add(doc, selected, q):
    if sum(item['cost'] for item in selected) + doc['cost'] > q['budget']:
        return False
    if any(item['village'] == doc['village'] for item in selected):
        return False  # 一个村的不同资料只能占一个组合位置
    region_counts = {}
    for item in selected:
        region_counts[item['region']] = region_counts.get(item['region'], 0) + 1
    if region_counts.get(doc['region'], 0) >= q['region_cap']:
        return False
    return True


def _enumerate_feasible(candidates, q):
    combos = []

    def visit(index, chosen):
        if index == len(candidates):
            combos.append(list(chosen))
            return
        visit(index + 1, chosen)
        doc = candidates[index]
        if _can_add(doc, chosen, q):
            chosen.append(doc)
            visit(index + 1, chosen)
            chosen.pop()

    visit(0, [])
    return combos


def _best_combo(combos, q):
    best, best_value, best_key = [], 0.0, ()
    for combo in combos:
        value = _objective(combo, q)['objective']
        key = tuple(sorted(doc['id'] for doc in combo))
        if value > best_value or (value == best_value and key < best_key):
            best, best_value, best_key = combo, value, key
    return best


def _upper_bound(candidates, q):
    # 可核对的乐观上界：覆盖不超过可行文档能覆盖的全部机制权重；
    # 资源奖励为非负可加项，按预算允许的最大篇数截断；相似惩罚下界为0。
    if not candidates:
        return 0.0, {'coverable_weight': 0.0, 'resource_reward_cap': 0.0, 'max_items_by_budget': 0}
    max_items = int(q['budget'] // min(doc['cost'] for doc in candidates))
    coverable = set()
    for doc in candidates:
        for spec, view in zip(q['mechanisms'], doc['mechanism_views']):
            if view['status'] == 'met':
                coverable.add(spec['facet'])
    coverable_weight = sum(spec['weight'] for spec in q['mechanisms'] if spec['facet'] in coverable)
    rewards = sorted((doc['resource_reward'] for doc in candidates), reverse=True)
    resource_cap = sum(rewards[:max_items])
    bound = coverable_weight + resource_cap
    return bound, {'coverable_weight': coverable_weight, 'resource_reward_cap': resource_cap,
                   'max_items_by_budget': max_items}


def _greedy_solve(candidates, q):
    selected = []
    iterations = 0
    while True:
        choice, best_ratio, best_delta = None, None, 0.0
        for doc in candidates:
            if doc in selected or not _can_add(doc, selected, q):
                continue
            delta = _objective(selected + [doc], q)['objective'] - _objective(selected, q)['objective']
            ratio = delta / doc['cost']
            if delta > 0 and (best_ratio is None or ratio > best_ratio or
                              (ratio == best_ratio and (choice is None or doc['id'] < choice['id']))):
                choice, best_ratio, best_delta = doc, ratio, delta
        if choice is None:
            break
        selected.append(choice)
        iterations += 1
    singleton = _best_combo([[doc] for doc in candidates], q)
    greedy_value = _objective(selected, q)['objective']
    singleton_value = _objective(singleton, q)['objective']
    if singleton_value > greedy_value:
        selected, greedy_value = singleton, singleton_value
    return selected, iterations


def _guarantee_note(q, mode):
    if mode == 'exhaustive':
        return '穷举全部可行组合，目标值即全局最优，缺口为0。'
    if q['similarity_penalty'] == 0:
        return ('similarity_penalty=0时目标为单调次模（加权覆盖）加模（资源奖励）；'
                '仅预算约束下按单位成本边际增益贪心并对照最优单篇，适用1-1/e近似保证；'
                '同村与地域分区约束并存时本实现不宣称固定常数保证，请以上界缺口为准，'
                '或调大exact_limit改走穷举。')
    return ('similarity_penalty>0时目标非单调，不适用固定常数近似保证；'
            '所给上界仍然有效，缺口可核对；可调大exact_limit改走穷举。')


def _view_payload(view):
    return {key: view[key] for key in ('facet', 'expect', 'status', 'supporting', 'contradicting')}


def _combination_payload(combo, q, mode, enumeration_count=None, iterations=None):
    value = _objective(combo, q)
    items = []
    for doc in combo:
        coverage_evidence = []
        for spec, view in zip(q['mechanisms'], doc['mechanism_views']):
            if view['status'] == 'met':
                coverage_evidence.append({'facet': spec['facet'], 'supporting': view['supporting']})
        items.append({'document': doc['id'], 'village': doc['village'], 'revision': doc['revision'],
                      'cost': doc['cost'], 'region': doc['region'],
                      'coverage_evidence': coverage_evidence,
                      'hard_conditions': [_view_payload(v) for v in doc['hard_views']],
                      'resources': [_view_payload(v) for v in doc['resource_views']],
                      'mechanisms': [_view_payload(v) for v in doc['mechanism_views']],
                      'evidence_issues': doc['evidence_issues']})
    candidates = [doc for doc in q['_docs'] if doc['feasible']]
    bound, bound_detail = _upper_bound(candidates, q)
    gap = bound - value['objective']
    payload = {'mode': mode, 'selected': [doc['id'] for doc in combo], 'items': items,
               'budget_used': sum(doc['cost'] for doc in combo), 'budget': q['budget'],
               'objective': value,
               'approximation': {'upper_bound': bound, 'bound_detail': bound_detail,
                                 'gap': gap,
                                 'relative_gap': (gap / abs(value['objective'])
                                                  if value['objective'] else None),
                                 'guarantee_note': _guarantee_note(q, mode)},
               'constraints': {'village_cap': 1, 'region_cap': q['region_cap'],
                               'similarity_penalty': q['similarity_penalty']}}
    if enumeration_count is not None:
        payload['enumeration_count'] = enumeration_count
        payload['approximation']['optimal_objective'] = value['objective']
        payload['approximation']['optimality_gap'] = 0.0
    if iterations is not None:
        payload['greedy_iterations'] = iterations
    return payload


def _solve(candidates, q):
    if len(candidates) <= q['exact_limit']:
        combos = _enumerate_feasible(candidates, q)
        combo = _best_combo(combos, q)
        return combo, 'exhaustive', len(combos), None
    combo, iterations = _greedy_solve(candidates, q)
    return combo, 'greedy', None, iterations


def _uncovered_mechanisms(candidates, q):
    notes = []
    for spec in q['mechanisms']:
        status_counts = {status: 0 for status in STATUSES}
        for doc in candidates:
            view = next(v for v in doc['mechanism_views'] if v['facet'] == spec['facet'])
            status_counts[view['status']] += 1
        if status_counts['met'] == 0:
            notes.append({'facet': spec['facet'], 'reason': 'no_met_evidence_among_feasible',
                          'status_counts': status_counts})
    return notes


def plan(snapshot, query):
    """主入口：返回证据绑定的参照案例组合。"""
    q, docs = _build_context(snapshot, query)
    q['_docs'] = docs
    candidates = [doc for doc in docs if doc['feasible']]
    excluded = []
    for doc in docs:
        if not doc['feasible']:
            excluded.append({'document': doc['id'], 'village': doc['village'], 'revision': doc['revision'],
                             'failed_hard_conditions': [_view_payload(v) for v in doc['hard_views']
                                                        if v['status'] != 'met'],
                             'evidence_issues': doc['evidence_issues']})
    combo, mode, enumeration_count, iterations = _solve(candidates, q)
    payload = _combination_payload(combo, q, mode, enumeration_count, iterations)
    payload['excluded_documents'] = excluded
    payload['uncovered_mechanisms'] = _uncovered_mechanisms(candidates, q)
    payload['feasible_document_count'] = len(candidates)
    return payload


def relax_condition(snapshot, query, facet):
    """取消一个硬条件后，说明新增的可行文档与可行组合。"""
    q, docs = _build_context(snapshot, query)
    if not any(condition['facet'] == facet for condition in q['hard_conditions']):
        raise ValueError('待放松的条件不在hard_conditions中')
    q['_docs'] = docs
    before = {doc['id'] for doc in docs if doc['feasible']}
    relaxed = dict(q)
    relaxed['hard_conditions'] = [c for c in q['hard_conditions'] if c['facet'] != facet]
    for doc in docs:
        doc['relaxed_views'] = [_condition_view(doc, c['facet'], c['expect'])
                                for c in relaxed['hard_conditions']]
        doc['relaxed_feasible'] = all(v['status'] == 'met' for v in doc['relaxed_views'])
    after = {doc['id'] for doc in docs if doc['relaxed_feasible']}
    new_ids = after - before
    new_docs = []
    for doc in docs:
        if doc['id'] in new_ids:
            blocking = next(v for v in doc['hard_views'] if v['facet'] == facet)
            new_docs.append({'document': doc['id'], 'village': doc['village'], 'revision': doc['revision'],
                             'relaxed_condition': _view_payload(blocking),
                             'evidence_issues': doc['evidence_issues']})
    candidates = [doc for doc in docs if doc['relaxed_feasible']]
    combo, mode, enumeration_count, iterations = _solve(candidates, relaxed)
    payload = _combination_payload(combo, relaxed, mode, enumeration_count, iterations)
    payload['relaxed_facet'] = facet
    payload['newly_feasible_documents'] = new_docs
    if mode == 'exhaustive':
        combos = _enumerate_feasible(candidates, relaxed)
        fresh = []
        for candidate in combos:
            ids = {doc['id'] for doc in candidate}
            if ids & new_ids:
                value = _objective(candidate, relaxed)
                fresh.append({'documents': sorted(ids), 'objective': value})
        fresh.sort(key=lambda item: (-item['objective']['objective'], item['documents']))
        payload['newly_feasible_combination_total'] = len(fresh)
        payload['newly_feasible_combinations'] = fresh[:q['combination_limit']]
    else:
        payload['newly_feasible_combinations'] = 'approx_mode_see_selected_combination'
    return payload


def explain_replacement(snapshot, query, excluded_id, replaced_id):
    """说明被排除案例为何无法（或可以）替换组合中的当前某篇。"""
    q, docs = _build_context(snapshot, query)
    q['_docs'] = docs
    by_id = {doc['id']: doc for doc in docs}
    if excluded_id not in by_id or replaced_id not in by_id:
        raise ValueError('案例id不存在')
    excluded, replaced = by_id[excluded_id], by_id[replaced_id]
    candidates = [doc for doc in docs if doc['feasible']]
    combo, mode, enumeration_count, _ = _solve(candidates, q)
    if replaced not in combo:
        raise ValueError('被替换案例不在当前最优组合中')
    result = {'mode': mode, 'excluded_document': excluded_id, 'replaced_document': replaced_id,
              'current_combination': [doc['id'] for doc in combo]}
    hard_failures = [_view_payload(v) for v in excluded['hard_views'] if v['status'] != 'met']
    if hard_failures:
        result['decision'] = 'blocked_hard_condition'
        result['reasons'] = hard_failures
        result['evidence_issues'] = excluded['evidence_issues']
        return result
    trial = [doc for doc in combo if doc['id'] != replaced_id]
    constraint_reasons = []
    if sum(doc['cost'] for doc in trial) + excluded['cost'] > q['budget']:
        constraint_reasons.append('budget')
    if any(doc['village'] == excluded['village'] for doc in trial):
        constraint_reasons.append('village_cap')
    region_counts = {}
    for doc in trial:
        region_counts[doc['region']] = region_counts.get(doc['region'], 0) + 1
    if region_counts.get(excluded['region'], 0) >= q['region_cap']:
        constraint_reasons.append('region_cap')
    if constraint_reasons:
        result['decision'] = 'blocked_constraint'
        result['reasons'] = constraint_reasons
        return result
    before = _objective(combo, q)
    after = _objective(trial + [excluded], q)
    gained = sorted(set(after['covered_facets']) - set(before['covered_facets']))
    lost = sorted(set(before['covered_facets']) - set(after['covered_facets']))
    evidence = {}
    for facet in gained + lost:
        evidence[facet] = []
        for doc in combo + [excluded]:
            view = next(v for v in doc['mechanism_views'] if v['facet'] == facet)
            if view['supporting']:
                evidence[facet].append({'document': doc['id'], 'supporting': view['supporting']})
    if after['objective'] > before['objective']:
        decision = 'objective_improves'
    elif after['objective'] < before['objective']:
        decision = 'objective_deteriorates'
    else:
        decision = 'objective_equal'
    result.update({'decision': decision,
                   'objective_before': before, 'objective_after': after,
                   'objective_delta': after['objective'] - before['objective'],
                   'coverage_gained': gained, 'coverage_lost': lost,
                   'coverage_evidence': evidence,
                   'resource_reward_delta': after['resource_reward'] - before['resource_reward'],
                   'similarity_penalty_delta': after['similarity_penalty'] - before['similarity_penalty']})
    return result


def main():
    import json
    import sys
    from pathlib import Path
    if len(sys.argv) < 4:
        raise SystemExit('用法: python -m app.combine plan|relax|replace 资料.json 查询.json [参数...]')
    command, data_path, query_path = sys.argv[1:4]
    payload = json.loads(Path(data_path).read_text(encoding='utf-8'))
    query = json.loads(Path(query_path).read_text(encoding='utf-8'))
    from .service import SCHEMAS
    snapshot = load(payload, SCHEMAS)
    if command == 'plan':
        result = plan(snapshot, query)
    elif command == 'relax':
        if len(sys.argv) != 5:
            raise SystemExit('relax需要给出待放松的facet')
        result = relax_condition(snapshot, query, sys.argv[4])
    elif command == 'replace':
        if len(sys.argv) != 6:
            raise SystemExit('replace需要给出被排除案例id与被替换案例id')
        result = explain_replacement(snapshot, query, sys.argv[4], sys.argv[5])
    else:
        raise SystemExit(f'未知命令: {command}')
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))


if __name__ == '__main__':
    main()
