# CrossPilot 开发进度

| 阶段 | 状态 | 完成日期 | Commit | 测试 | 备注 |
| --- | --- | --- | --- | --- | --- |
| 阶段0：仓库初始化 | completed | 2026-09-04 | `4e248ee` | Git、文档路径、敏感文件排除检查通过 | `develop` 已推送到 `origin` |
| 阶段1：公共契约 | completed | 2026-09-04 | `abe860b` | 15项契约测试；Ruff、Mypy、导入和锁文件检查通过 | `develop` 已推送到 `origin` |
| 阶段2：基础能力 | completed | 2026-09-04 | `d9e331e` | 真实 Neo4j 全量 130项通过；Ruff、compileall、模块级 Mypy 通过 | Task 2～6 已审查并集成；1项 Starlette 依赖弃用警告 |
| 阶段3：Agent与编排 | pending | - | - | - | - |
| 阶段4：联调与评测 | pending | - | - | - | - |
| 阶段5：容器化验收 | pending | - | - | - | - |
| 阶段6：发布main | pending | - | - | - | - |

## 当前阶段

- 阶段：阶段2：基础能力
- 状态：`completed`
- 已完成：Neo4j Schema、96条仿真商品与幂等导入；受控 Text-to-Cypher、实体解析与 BGE-M3 适配；Decimal 定价与费用规则；FastAPI/SSE 骨架；Streamlit 单页工作台骨架。
- 阻塞项：无。
- 下一步：等待用户回复“继续”后进入阶段3，实施四个专业 Agent 与 LangGraph 编排。
