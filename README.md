# CrossPilot

CrossPilot 是一个面向美国 Amazon 选品场景的跨境电商智能决策 Multi-Agent 系统。第一版 MVP 聚焦消费电子配件、儿童玩具和家居用品，通过市场、竞品、定价和合规准备四个专业 Agent 提供可追溯的分析结果。

> 当前仓库处于初始化阶段，尚未提供可运行的业务功能。商品与市场数据将使用离线仿真数据；合规结果仅用于资料准备和风险提示，不构成法律意见或厂家认证。

## 项目边界

- 目标平台：美国 Amazon。
- 数据范围：90～120 条离线仿真商品数据。
- 核心组件：Streamlit、FastAPI、LangGraph、Neo4j、BGE-M3 和 OpenAI 兼容模型接口。
- 定价计算使用 Python `Decimal`，不由大模型心算。
- 市场、竞品和合规查询采用受控、参数化、只读 Text-to-Cypher。
- MVP 不接入 Amazon 实时 API，不包含爬虫、账户系统或自动执行平台操作。

## 项目文档

- [开发治理](AGENTS.md)
- [项目需求](CrossPilot_%E9%A1%B9%E7%9B%AE%E9%9C%80%E6%B1%82%E6%96%87%E6%A1%A3.md)
- [实施计划](docs/superpowers/plans/2026-09-04-crosspilot-implementation.md)
- [开发进度](PROGRESS.md)

需求文档定义项目范围，实施计划定义任务与技术步骤，`AGENTS.md` 定义阶段审批、Git、安全和验收规则。

## 分支策略

- `main`：用户最终验收后的稳定版本。
- `develop`：阶段成果集成分支。
- `agent/<stage>-<module>`：子 Agent 独立开发分支。
- `fix/<stage>-<issue>`：阶段内临时修复分支。

每个阶段必须经过用户批准后才能开始。阶段完成后需运行对应验证、更新 `PROGRESS.md`、提交并推送 `develop`，随后暂停等待下一阶段授权。未经明确同意，不得合并或推送到 `main`。

## 当前状态

请查看 [PROGRESS.md](PROGRESS.md) 获取实际完成状态、测试证据和下一步安排。完整的安装、启动、数据初始化、评测和演示说明将在容器化验收阶段补充，并以实际验证结果为准。
