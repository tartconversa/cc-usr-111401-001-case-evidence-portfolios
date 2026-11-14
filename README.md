# 乡村案例检索的证据约束与互补组合

本项目提供段落证据与三值条件判定相关资料的现有业务摘要。当前代码执行资料修订读取与基础统计，不包含扩展分析。

app/catalog.py 按集合和记录键解析修订，拒绝同修订冲突；app/baseline.py 承载当前业务计算；app/service.py 输出摘要及输入摘要哈希；app/combine.py 提供证据约束下的参照案例组合（三值判定、联合优化、反事实与替换解释）。
数据字段及现有接口见 [资料契约](docs/data-contract.md)。fixtures/sample.json 是合成小数据，不对应真实村庄或个人。
运行环境为 Python 3.14，现有模块仅使用标准库。

读取并计算当前摘要：

    python -m app.service fixtures/sample.json

参照案例组合（查询JSON放在资料目录之外）：

    python -m app.combine plan 资料.json 查询.json
    python -m app.combine relax 资料.json 查询.json 待放松facet
    python -m app.combine replace 资料.json 查询.json 被排除案例id 被替换案例id

服务入口：service.build_reference / build_relaxation / build_replacement，输出与inspect同样绑定input_digest与data_version。

运行当前模块检查：

    python -m unittest discover -s tests -v

检查源码编译：

    python -m compileall -q app tests
