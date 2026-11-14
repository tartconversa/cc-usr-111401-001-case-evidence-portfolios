# 资料与现有计算约定

## 修订快照

schema_version为整数1，metadata为业务方案字典，collections为业务集合字典。每条记录有非空字符串id和正整数revision。
同集合相同id取最高revision；同id同revision内容不同即冲突，即便存在更高修订也拒绝。deleted=true为撤回，之后更高修订可恢复。
撤回记录不要求携带业务字段。输入顺序与相同记录重试不改变语义；非有限数字拒绝。
Snapshot.rows返回独立副本；input_digest绑定有效修订和全部metadata。输出data_version沿用metadata，不由程序自造资料版本。
既有摘要入口为app.service.inspect(payload)，输出schema_version、input_digest、data_version和summary。

## 业务资料

documents 的 id 是文档身份，village 是村庄身份，revision 为正整数。最高修订有效，deleted=true 表示撤稿。paragraphs 为带稳定 id、tokens 和 text 的段落。facets 是条件到证据数组的映射，每条含 value、paragraph_id；不同来源可互相矛盾。tokens 已由上游分词，大小写敏感，不再切中文字符。baseline.search(snapshot, terms) 返回文档词项命中数及段落身份，按分数降序、文档键升序。

各集合必要字段：{"documents": ["village", "paragraphs", "facets"]}。

## 当前能力

app.baseline.analyze(snapshot)产生已有摘要，app.service.inspect负责绑定输入身份；新增分析可以增设独立入口，已有接口及上述数据语义保持兼容。

## 参照案例组合（app.combine）

查询字段（除注明外均为可选）：budget为必填非负有限数；hard_conditions为[{facet, expect}]硬条件；mechanisms为[{facet, expect?, weight?}]经营机制覆盖项，缺省expect=true、weight=1；resources为[{facet, expect?, weight?}]资源匹配项；similarity_penalty为非负相似惩罚系数；costs为文档id到正成本的映射，缺省成本1；region_cap为地域上限正整数，regions为村庄id到地域id映射，缺省地域即村庄本身；exact_limit（1到22，缺省16）为穷举候选上限；combination_limit为反事实返回组合条数上限。

目标与约束在同一组选择上联合权衡，不做单篇排序后截断：目标值=已覆盖机制权重之和+已符合资源项权重之和−similarity_penalty×选中文档间token集合Jaccard相似度之和；约束为预算、同一village至多一个组合位置、地域上限。三值判定按facet证据分为met（存在等于expect且段落存在的证据）、violated（仅有不等于expect的有效证据）、conflict（两类有效证据并存，来源互相冲突）、missing（无有效证据或仅悬挂引用）；硬条件仅met放行，未提及不等于符合。悬挂paragraph_id在evidence_issues中以dangling_paragraph_id单独说明，不参与判定。

可行候选数不超过exact_limit时穷举全部可行组合，返回enumeration_count、optimal_objective与optimality_gap=0；否则按单位成本边际增益贪心，返回greedy_iterations、upper_bound及其bound_detail、gap与relative_gap。上界=可行文档可覆盖的全部机制权重+按预算最大篇数截断的资源奖励和，乐观忽略惩罚与约束耦合，可由查询资料独立复算；similarity_penalty=0且无分区约束时1-1/e保证适用，其余情形guarantee_note明示不宣称固定常数保证。

app.service.build_reference(payload, query)、build_relaxation(payload, query, facet)、build_replacement(payload, query, excluded_id, replaced_id)在组合结果外绑定schema_version、input_digest、data_version；每篇入选资料附active revision，证据文本只从该修订的paragraphs读取，来源更正后digest变化且旧段落id不再出现。build_relaxation返回放松单个硬条件后的newly_feasible_documents及含新增案例的组合（穷举模式给完整计数与截断列表）；build_replacement区分blocked_hard_condition、blocked_constraint、objective_deteriorates/equal/improves，并给出目标值前后明细与覆盖增减的原文证据。组合只比较资料中已存在的证据，不生成资料未陈述的成功结论。
