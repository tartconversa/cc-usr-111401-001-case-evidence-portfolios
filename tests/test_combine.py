"""参照案例组合：三值证据、联合优化、反事实与修订绑定的端到端检查。"""
import copy
import itertools
import json
import unittest
from app.catalog import load
from app import combine
from app.service import SCHEMAS, build_reference, build_relaxation, build_replacement


def para(pid, tokens, text):
    return {'id': pid, 'tokens': tokens, 'text': text}


def entry(value, pid):
    return {'value': value, 'paragraph_id': pid}


def doc(did, village, revision, paragraphs, facets, extra=None):
    row = {'id': did, 'revision': revision, 'village': village,
           'paragraphs': paragraphs, 'facets': facets}
    if extra:
        row.update(extra)
    return row


def build_payload():
    return {
        'schema_version': 1,
        'metadata': {'data_version': 'reference-1'},
        'collections': {'documents': [
            doc('d1', 'v1', 1,
                [para('p1a', ['电商', '工坊'], 'v1开展电商与工坊培训。'),
                 para('p1b', ['海拔'], 'v1海拔八百米。')],
                {'电商': [entry(True, 'p1a')], '工坊': [entry(True, 'p1a')],
                 '污染': [entry(False, 'p1b')], '海拔': [entry(800, 'p1b')],
                 '资金': [entry('少', 'p1b')]}),
            doc('d2', 'v2', 1,
                [para('p2a', ['电商', '物流'], 'v2电商物流成熟。'),
                 para('p2b', ['海拔'], 'v2海拔八百米。')],
                {'电商': [entry(True, 'p2a')], '污染': [entry(False, 'p2b')],
                 '海拔': [entry(800, 'p2b')], '资金': [entry('多', 'p2b')]}),
            doc('d3', 'v3', 1,
                [para('p3a', ['民宿'], 'v3开办民宿。'),
                 para('p3b', ['污染'], '报道A称无污染，报道B称存在污染。')],
                {'民宿': [entry(True, 'p3a')], '污染': [entry(False, 'p3b'), entry(True, 'p3b')]}),
            doc('d4', 'v4', 1,
                [para('p4a', ['研学', '古建'], 'v4古建研学。'),
                 para('p4b', ['海拔'], 'v4海拔八百米。')],
                {'研学': [entry(True, 'p4a')], '污染': [entry(False, 'p4b')],
                 '海拔': [entry(800, 'p4b')], '资金': [entry('少', 'p4b')]}),
            doc('d5', 'v5', 1,
                [para('p5a', ['合作社'], 'v5合作社统购统销。')],
                {'合作社': [entry(True, 'p5a')], '污染': [entry(False, 'p5a')]}),
            doc('d6', 'v6', 1,
                [para('p6a', ['电商'], 'v6电商但海拔不达标。'),
                 para('p6b', ['海拔'], 'v6海拔一千二百米。')],
                {'电商': [entry(True, 'p6a')], '污染': [entry(False, 'p6b')],
                 '海拔': [entry(1200, 'p6b')]}),
            doc('d1b', 'v1', 1,
                [para('pb', ['电商'], '同一村庄v1的另一份资料。')],
                {'电商': [entry(True, 'pb')], '污染': [entry(False, 'pb')]}),
        ]}
    }


QUERY = {
    'budget': 3,
    'costs': {'d5': 2},
    'hard_conditions': [{'facet': '污染', 'expect': False}, {'facet': '海拔', 'expect': 800}],
    'mechanisms': [
        {'facet': '电商', 'weight': 2},
        {'facet': '工坊', 'weight': 2},
        {'facet': '研学', 'weight': 1.5},
        {'facet': '民宿', 'weight': 1},
        {'facet': '合作社', 'weight': 1},
    ],
    'resources': [{'facet': '资金', 'expect': '少', 'weight': 1}],
    'similarity_penalty': 0.5,
    'regions': {'v1': '华北', 'v2': '华北', 'v3': '华东', 'v4': '华东', 'v5': '西南', 'v6': '西南'},
    'region_cap': 2,
}


def active_rows(payload):
    winners = {}
    for row in payload['collections']['documents']:
        if row['id'] not in winners or winners[row['id']]['revision'] < row['revision']:
            winners[row['id']] = row
    return [row for row in winners.values() if not row.get('deleted', False)]


def view_status(row, facet, expect):
    entries = row['facets'].get(facet, [])
    paragraph_ids = {p['id'] for p in row['paragraphs']}
    ok = any(e['value'] == expect and type(e['value']) is type(expect)
             and e['paragraph_id'] in paragraph_ids for e in entries)
    bad = any(not (e['value'] == expect and type(e['value']) is type(expect))
              and e['paragraph_id'] in paragraph_ids for e in entries)
    if not entries or not any(e['paragraph_id'] in paragraph_ids for e in entries):
        return 'missing'
    if ok and bad:
        return 'conflict'
    return 'met' if ok else 'violated'


def brute_force(payload, query):
    """独立于combine模块实现的穷举，用于核对目标值与最优性。"""
    rows = active_rows(payload)
    by_id = {row['id']: row for row in rows}
    feasible = [row for row in rows
                if all(view_status(row, c['facet'], c['expect']) == 'met'
                       for c in query['hard_conditions'])]
    costs = {row['id']: query.get('costs', {}).get(row['id'], 1) for row in rows}
    weights = {m['facet']: m.get('weight', 1) for m in query['mechanisms']}

    def allowed(combo):
        if sum(costs[d['id']] for d in combo) > query['budget']:
            return False
        if len({d['village'] for d in combo}) != len(combo):
            return False
        counts = {}
        for d in combo:
            region = query['regions'].get(d['village'], d['village'])
            counts[region] = counts.get(region, 0) + 1
            if counts[region] > query['region_cap']:
                return False
        return True

    def objective(combo):
        covered = set()
        reward = 0.0
        for d in combo:
            for m in query['mechanisms']:
                if view_status(d, m['facet'], m.get('expect', True)) == 'met':
                    covered.add(m['facet'])
            for r in query['resources']:
                if view_status(d, r['facet'], r.get('expect', True)) == 'met':
                    reward += r.get('weight', 1)
        coverage = sum(weights[f] for f in covered)
        tokens = {d['id']: set().union(*[set(p['tokens']) for p in d['paragraphs']]) for d in combo}
        ids = list(tokens)
        penalty = 0.0
        for i, left in enumerate(ids):
            for right in ids[i + 1:]:
                union = tokens[left] | tokens[right]
                penalty += len(tokens[left] & tokens[right]) / len(union) if union else 0.0
        return coverage + reward - query['similarity_penalty'] * penalty

    best, best_value = [], 0.0
    for size in range(len(feasible) + 1):
        for combo in itertools.combinations(feasible, size):
            combo = list(combo)
            if not allowed(combo):
                continue
            value = objective(combo)
            if value > best_value:
                best, best_value = combo, value
    return [d['id'] for d in best], best_value, {d['id'] for d in feasible}, by_id


class CombinationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = build_payload()
        cls.snapshot = load(cls.payload, SCHEMAS)
        cls.result = combine.plan(cls.snapshot, QUERY)

    def test_three_valued_judgement_and_exclusions(self):
        result = self.result
        self.assertEqual(result['mode'], 'exhaustive')
        excluded = {item['document']: item for item in result['excluded_documents']}
        d3 = excluded['d3']['failed_hard_conditions'][0]
        self.assertEqual(d3['facet'], '污染')
        self.assertEqual(d3['status'], 'conflict')
        self.assertEqual({e['value'] for e in d3['supporting']}, {False})
        self.assertEqual({e['value'] for e in d3['contradicting']}, {True})
        d5 = excluded['d5']['failed_hard_conditions'][0]
        self.assertEqual(d5['facet'], '海拔')
        self.assertEqual(d5['status'], 'missing')  # 未提及不等于符合
        d6 = excluded['d6']['failed_hard_conditions'][0]
        self.assertEqual((d6['facet'], d6['status']), ('海拔', 'violated'))
        self.assertTrue(all(
            item['status'] == 'met'
            for item in result['items'][0]['hard_conditions']))

    def test_village_single_slot_and_budget_region_constraints(self):
        selected = set(self.result['selected'])
        self.assertFalse({'d1', 'd1b'} <= selected)  # 同村只占一个位置
        self.assertLessEqual(self.result['budget_used'], QUERY['budget'])
        regions = [item['region'] for item in self.result['items']]
        self.assertTrue(all(regions.count(region) <= QUERY['region_cap'] for region in set(regions)))

    def test_exhaustive_matches_independent_bruteforce(self):
        expected_ids, expected_value, feasible_ids, _ = brute_force(self.payload, QUERY)
        self.assertEqual(self.result['feasible_document_count'], len(feasible_ids))
        self.assertEqual(set(self.result['selected']), set(expected_ids))
        self.assertAlmostEqual(self.result['objective']['objective'], expected_value)
        # 乐观上界始终有效；穷举模式另外给出已验证的最优值与0最优缺口
        self.assertGreaterEqual(self.result['approximation']['upper_bound'], expected_value)
        self.assertEqual(self.result['approximation']['optimal_objective'], expected_value)
        self.assertEqual(self.result['approximation']['optimality_gap'], 0.0)
        self.assertGreaterEqual(self.result['enumeration_count'], 1)

    def test_evidence_bound_to_revision_text(self):
        item = next(item for item in self.result['items'] if item['document'] == 'd1')
        evidence = next(e for e in item['coverage_evidence'] if e['facet'] == '电商')
        self.assertEqual(evidence['supporting'][0]['paragraph_id'], 'p1a')
        self.assertIn('电商', evidence['supporting'][0]['text'])
        self.assertEqual(item['revision'], 1)

    def test_dangling_paragraph_id_is_reported_and_not_treated_as_met(self):
        payload = build_payload()
        rows = payload['collections']['documents']
        rows.append(doc('d7', 'v7', 1, [para('p7', ['电商'], 'v7电商。')],
                        {'电商': [entry(True, 'p7')], '污染': [entry(False, 'gone')],
                         '海拔': [entry(800, 'p7')]}))
        result = combine.plan(load(payload, SCHEMAS), QUERY)
        excluded = {item['document']: item for item in result['excluded_documents']}
        self.assertIn('d7', excluded)
        issue = next(item for item in excluded['d7']['evidence_issues']
                     if item['reason'] == 'dangling_paragraph_id')
        self.assertEqual(issue['paragraph_id'], 'gone')
        failed = {v['facet']: v['status'] for v in excluded['d7']['failed_hard_conditions']}
        self.assertEqual(failed['污染'], 'missing')

    def test_greedy_mode_reports_checkable_bound(self):
        query = {**QUERY, 'exact_limit': 2, 'similarity_penalty': 0}
        result = combine.plan(self.snapshot, query)
        self.assertEqual(result['mode'], 'greedy')
        self.assertGreaterEqual(result['approximation']['upper_bound'],
                                result['objective']['objective'])
        self.assertGreaterEqual(result['approximation']['gap'], 0.0)
        self.assertLessEqual(result['budget_used'], QUERY['budget'])
        self.assertEqual(len({item['village'] for item in result['items']}), len(result['items']))
        self.assertIn('1-1/e', result['approximation']['guarantee_note'])

    def test_greedy_with_penalty_has_no_constant_guarantee_claim(self):
        query = {**QUERY, 'exact_limit': 2}
        result = combine.plan(self.snapshot, query)
        self.assertIn('不适用固定常数', result['approximation']['guarantee_note'])

    def test_uncovered_mechanism_reports_missing_status(self):
        notes = {item['facet']: item for item in self.result['uncovered_mechanisms']}
        self.assertIn('民宿', notes)
        self.assertEqual(notes['民宿']['status_counts']['met'], 0)

    def test_relax_hard_condition_lists_new_combinations(self):
        result = combine.relax_condition(self.snapshot, QUERY, '海拔')
        newly = {item['document'] for item in result['newly_feasible_documents']}
        self.assertIn('d5', newly)
        self.assertNotIn('d3', newly)  # d3被矛盾证据挡住，放松海拔不解决
        combos = result['newly_feasible_combinations']
        self.assertTrue(any(set(c['documents']) & newly for c in combos))
        self.assertEqual(result['newly_feasible_combination_total'], len(combos))
        self.assertLessEqual(len(combos), combine.DEFAULT_COMBINATION_LIMIT)
        # 放松未知条件应报错
        with self.assertRaisesRegex(ValueError, '不在hard_conditions'):
            combine.relax_condition(self.snapshot, QUERY, '不存在')

    def test_replacement_blocked_and_objective_delta(self):
        blocked = combine.explain_replacement(self.snapshot, QUERY, 'd3', 'd1')
        self.assertEqual(blocked['decision'], 'blocked_hard_condition')
        self.assertEqual(blocked['reasons'][0]['status'], 'conflict')
        swap = combine.explain_replacement(self.snapshot, QUERY, 'd2', 'd1')
        self.assertIn(swap['decision'], ('objective_deteriorates', 'objective_equal', 'objective_improves'))
        self.assertAlmostEqual(swap['objective_delta'],
                               swap['objective_after']['objective']
                               - swap['objective_before']['objective'])
        self.assertIn('工坊', swap['coverage_lost'])
        non_selected = next(doc_id for doc_id in ('d1', 'd2', 'd4')
                            if doc_id not in self.result['selected'])
        with self.assertRaisesRegex(ValueError, '不在当前最优组合'):
            combine.explain_replacement(self.snapshot, QUERY, 'd2', non_selected)
        with self.assertRaisesRegex(ValueError, '案例id不存在'):
            combine.explain_replacement(self.snapshot, QUERY, 'nope', 'd1')

    def test_revision_update_rebinds_evidence_and_ranking(self):
        payload = build_payload()
        rows = payload['collections']['documents']
        # d1 修订：移除电商证据，段落id与文本全部更换
        rows.append(doc('d1', 'v1', 2,
                        [para('p1c', ['工坊'], '修订后仅保留工坊。')],
                        {'工坊': [entry(True, 'p1c')],
                         '污染': [entry(False, 'p1c')], '海拔': [entry(800, 'p1c')],
                         '资金': [entry('少', 'p1c')]}))
        before = build_reference(self.payload, QUERY)
        after = build_reference(payload, QUERY)
        self.assertNotEqual(before['input_digest'], after['input_digest'])
        if 'd1' in after['selected']:
            item = next(i for i in after['items'] if i['document'] == 'd1')
            self.assertEqual(item['revision'], 2)
            facets = {e['facet'] for e in item['coverage_evidence']}
            self.assertNotIn('电商', facets)
            self.assertTrue(all(
                pid == 'p1c'
                for e in item['mechanisms'] for s in e['supporting']
                for pid in [s['paragraph_id']]))

    def test_service_wrapper_binds_identity(self):
        wrapped = build_reference(self.payload, QUERY)
        self.assertEqual(wrapped['schema_version'], 1)
        self.assertEqual(len(wrapped['input_digest']), 64)
        self.assertEqual(wrapped['data_version'], 'reference-1')
        self.assertEqual(wrapped['input_digest'], self.snapshot.digest)
        relaxed = build_relaxation(self.payload, QUERY, '海拔')
        self.assertEqual(relaxed['input_digest'], wrapped['input_digest'])
        replacement = build_replacement(self.payload, QUERY, 'd3', 'd1')
        self.assertEqual(replacement['data_version'], 'reference-1')

    def test_greedy_agrees_with_exhaustive_on_small_instance(self):
        # 小规模应能与穷举结果核对：两种模式选出同一组、同一目标值
        exhaustive = combine.plan(self.snapshot, QUERY)
        greedy = combine.plan(self.snapshot, {**QUERY, 'exact_limit': 1})
        self.assertEqual(greedy['mode'], 'greedy')
        self.assertEqual(set(greedy['selected']), set(exhaustive['selected']))
        self.assertAlmostEqual(greedy['objective']['objective'],
                               exhaustive['objective']['objective'])
        self.assertGreaterEqual(greedy['approximation']['upper_bound'],
                               greedy['objective']['objective'])

    def test_result_does_not_mutate_snapshot(self):
        snapshot = load(self.payload, SCHEMAS)
        before = snapshot.rows('documents')
        result = combine.plan(snapshot, QUERY)
        result['items'][0]['coverage_evidence'].clear()
        result['objective']['objective'] = 999
        self.assertEqual(snapshot.rows('documents'), before)

    def test_query_validation(self):
        for bad in [
            {'budget': -1}, {'budget': 1, 'similarity_penalty': -1},
            {'budget': 1, 'region_cap': 0},
            {'budget': 1, 'mechanisms': [{'facet': 'x'}, {'facet': 'x'}]},
            {'budget': 1, 'hard_conditions': [{'facet': '污染'}]},
            {'budget': 1, 'costs': {'unknown': 1}},
            {'budget': float('inf')},
        ]:
            with self.assertRaises(ValueError):
                combine.plan(self.snapshot, {**QUERY, **bad})


if __name__ == '__main__':
    unittest.main()
