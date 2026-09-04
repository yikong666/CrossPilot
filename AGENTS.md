# CrossPilot Codex 开发规范

本文件适用于 CrossPilot 仓库及其全部子目录。所有主 Agent、子 Agent 和后续 Codex 会话在开始工作前必须完整阅读并遵守本文件。

## 1. 项目与仓库

- 项目名称：CrossPilot——跨境电商智能决策 Multi-Agent 系统
- 远程仓库：`https://github.com/yikong666/CrossPilot.git`
- 稳定分支：`main`
- 日常集成分支：`develop`
- 需求依据：`CrossPilot_项目需求文档.md`
- 实施依据：`docs/superpowers/plans/2026-09-04-crosspilot-implementation.md`
- 进度记录：`PROGRESS.md`

需求文档定义“做什么”，开发规划定义“怎么做”。两份文档与本文件发生冲突时，优先级为：

1. 用户在当前对话中的最新明确指令；
2. 本 `AGENTS.md` 的开发治理和安全规则；
3. `CrossPilot_项目需求文档.md`；
4. `2026-09-04-crosspilot-implementation.md`；
5. 代码中的历史实现和注释。

不得自行扩大范围。发现需求不明确、文档矛盾或需要改变架构时，停止相关开发，向用户列出问题、可选方案和推荐方案，获得明确同意后再修改文档和代码。

---

## 2. 强制开发模式

采用“主 Agent 统一协调，子 Agent 按模块并行，分阶段验收”的开发方式。

主 Agent 的职责：

- 阅读需求、开发规划和当前仓库状态；
- 维护全局计划、公共契约和阶段状态；
- 给子 Agent 分配边界明确的任务；
- 审查所有子 Agent 的 diff 和测试结果；
- 处理合并、集成、回归测试、提交和远程推送；
- 只由主 Agent 向用户汇报阶段结果并请求确认。

子 Agent 的职责：

- 只处理主 Agent 分配的模块和文件；
- 开始前说明将修改的文件和依赖的接口；
- 完成后返回变更摘要、测试命令、测试结果、已知问题和 commit；
- 不得擅自修改公共契约、需求文档、开发计划或其他 Agent 的文件；
- 不得自行合并到 `develop` 或 `main`；
- 不得自行推送远程仓库、创建发布或修改 GitHub 设置；
- 不得仅凭自述宣称完成，主 Agent 必须独立验证。

并行任务必须使用独立分支或 Git worktree。多个 Agent 不得同时修改同一个文件。确需修改公共文件时，由子 Agent 提交修改建议，主 Agent统一实施。

---

## 3. 用户审批门禁

### 3.1 核心规则

每个阶段只能按以下顺序执行：

1. 主 Agent 公布本阶段目标、任务、子 Agent 分工和验收条件；
2. 用户明确同意开始本阶段；
3. 主 Agent 和子 Agent 实施当前阶段；
4. 主 Agent 审查代码并运行本阶段全部验证；
5. 更新 `PROGRESS.md`；
6. 提交到本地 `develop`；
7. 自动推送 `develop` 到远程 GitHub；
8. 向用户提交阶段完成报告和后续步骤；
9. 停止工作，等待用户回复“继续”“同意”或同义的明确授权；
10. 获得授权后才能进入下一阶段。

即使当前阶段提前完成、测试全部通过或仍有剩余时间，也不得自动进入下一阶段。用户提出修改意见时，继续停留在当前阶段；修改、复测、提交并再次推送后，重新汇报并等待确认。

除非用户明确取消阶段审批机制，否则“继续完成整个项目”“自行处理”等模糊表述不能替代下一阶段的明确确认。

### 3.2 阶段开始汇报模板

```markdown
## 阶段 N 开始说明

- 阶段名称：
- 本阶段目标：
- 计划完成的任务：
- 子 Agent 分工：
- 预计修改目录：
- 验收条件：
- 本阶段完成后将推送：`develop`

请确认是否开始本阶段。
```

### 3.3 阶段完成汇报模板

```markdown
## 阶段 N 完成报告

- 阶段名称：
- 当前状态：完成 / 部分完成 / 阻塞
- 已完成内容：
- 主要修改文件：
- 子 Agent 及其产出：
- 测试与验证：
  - 命令：
  - 结果：
- 验收条件完成情况：X / Y
- 已知问题和限制：
- Git 分支：`develop`
- Commit：`<完整或短哈希> <提交信息>`
- 远程推送：成功 / 失败及原因
- 下一阶段名称：
- 下一阶段拟完成步骤：
- 需要用户决定的问题：无 / 具体问题

当前已暂停。回复“继续”进入下一阶段；回复“调整：……”修改当前阶段。
```

汇报中的测试结果、完成数量、Commit 和推送状态必须来自实际命令输出，不得估计、伪造或使用“应该通过”等表述。

### 3.4 阻塞汇报模板

```markdown
## 阶段 N 阻塞报告

- 阻塞位置：
- 直接原因：
- 已执行的排查：
- 当前保留的有效成果：
- 是否产生未推送提交：
- 可选处理方案：
  1. 方案一及影响
  2. 方案二及影响
- 推荐方案及理由：

当前已停止，不进入下一阶段，请选择处理方案。
```

认证失败、仓库权限不足、远程分支冲突、测试无法通过、需求矛盾和需要破坏性操作均属于必须停止并汇报的阻塞条件。

---

## 4. 开发阶段划分

阶段必须与开发规划中的 Task 保持对应，不得自行合并阶段以绕过用户审批。

### 阶段 0：仓库与文档初始化

- 检查远程地址、分支、工作区和 Git 身份；
- 将需求文档、开发计划和 `AGENTS.md` 放入正确路径；
- 建立 `.gitignore`、`PROGRESS.md` 和基础 README；
- 建立或切换到 `develop`；
- 不编写业务功能。

### 阶段 1：项目骨架与公共契约

- 执行开发计划 Task 1；
- 锁定目录结构、Pydantic 契约、WorkflowState、SSEEvent 和环境配置；
- 公共契约测试通过后才允许并行开发。

### 阶段 2：基础能力并行开发

- 执行 Task 2～6；
- Neo4j Schema、仿真数据和初始化脚本；
- BGE-M3、实体解析、Text-to-Cypher 和校验重试；
- Python 定价计算器与费用规则仓库；
- FastAPI/SSE 骨架；
- Streamlit 单页工作台骨架。

允许多个子 Agent 并行，但必须遵守文件所有权。阶段结束时先逐模块测试，再运行合并后的回归测试。

### 阶段 3：专业 Agent 与 LangGraph 编排

- 执行 Task 7～8；
- 完成市场、竞品、定价和合规准备 Agent；
- 完成 Supervisor、动态路由、并行执行、结果校验、暂停恢复和 Strategy；
- 验证单 Agent、双 Agent、四 Agent、缺参和补充规划流程。

### 阶段 4：系统联调、日志与评测

- 执行 Task 9～10；
- 接通 Streamlit、FastAPI、LangGraph 和 Neo4j；
- 完成图谱依据展示、结构化 Trace 和30条评测集；
- 输出真实评测结果和失败样本，不得修改结果迎合预期。

### 阶段 5：容器化与最终验收

- 执行 Task 11；
- 完成 Docker Compose、README、完整测试和8个演示用例；
- 从空环境进行一次冷启动验证；
- 推送最终 `develop` 后向用户提交最终验收报告；
- 此阶段结束仍不得自行合并 `main`。

### 阶段 6：发布到 main

只有用户明确回复“同意合并 main”后才可执行：

1. 拉取并确认远程 `develop` 与本地一致；
2. 在干净环境重新运行全量测试和 Docker 验收；
3. 创建从 `develop` 到 `main` 的合并或 Pull Request；
4. 非 fast-forward 合并时保留清晰合并记录；
5. 推送 `main`；
6. 如需创建版本标签，另行获得用户确认；
7. 汇报最终 main Commit、测试结果和仓库地址。

---

## 5. Git 与 GitHub 规范

### 5.1 远程仓库检查

开始阶段 0 时必须运行只读检查：

```bash
git status --short --branch
git remote -v
git branch --all
```

- 如果 `origin` 不存在，可以添加：`https://github.com/yikong666/CrossPilot.git`；
- 如果 `origin` 已存在但地址不同，禁止覆盖，先向用户汇报；
- 如果工作区存在用户未提交修改，必须保留并说明，不得重置、覆盖或丢弃；
- 如果远程仓库存在未知提交，必须先 fetch 并比较分支，不得直接强推覆盖。

### 5.2 分支策略

- `main`：只保存用户验收后的稳定版本；
- `develop`：阶段集成和自动推送分支；
- 子 Agent 分支：`agent/<stage>-<module>`；
- 临时修复分支：`fix/<stage>-<issue>`。

子 Agent 分支合并进 `develop` 前，主 Agent 必须检查 diff、运行相关测试并确认未修改越界文件。合并顺序遵循开发规划中的依赖顺序。

### 5.3 Commit 规范

使用 Conventional Commits：

```text
docs: initialize project governance
chore: initialize shared contracts
feat(graph): add neo4j seed pipeline
feat(agents): add specialist agents
feat(workflow): add supervisor orchestration
test: add evaluation dataset
fix(api): handle interrupted stream resume
```

- 一个 Commit 只表达一个完整目的；
- 不使用“update”“modify”“fix bug”等无法说明范围的提交信息；
- 已推送的阶段 Commit 不进行 amend 或历史重写；
- 用户要求修改时新增修复 Commit，保留可追溯历史。

### 5.4 阶段自动推送

用户已经授权：每个阶段通过验收检查并完成 Commit 后，主 Agent可以自动将 `develop` 推送到以下远程，无需再次请求推送许可：

```text
origin: https://github.com/yikong666/CrossPilot.git
branch: develop
```

推送前必须完成：

1. `git status --short --branch`，确认目标分支为 `develop`；
2. `git diff --check`，确认没有空白错误；
3. 运行本阶段测试和所有受影响回归测试；
4. 检查暂存文件，不包含 `.env`、密钥、密码、Token、模型权重、Neo4j volume、运行日志或缓存；
5. 更新 `PROGRESS.md`；
6. 创建阶段 Commit；
7. 使用普通非强制 push 推送 `develop`。

允许使用：

```bash
git push -u origin develop
git push origin develop
```

禁止使用：

```bash
git push --force
git push --force-with-lease
git reset --hard
git clean -fd
git checkout -- .
```

除非用户针对明确目标单独授权，否则不得删除远程分支、标签、Release、Issue、Pull Request 或仓库文件历史。

推送失败时：

- 不得绕过身份验证或从其他位置提取凭证；
- 不得在聊天、日志或命令输出中展示 Token；
- 不得改用强制推送；
- 保留本地 Commit，汇报错误、当前 Commit 和用户需要完成的操作；
- 推送问题解决前，不进入下一阶段。

### 5.5 main 分支保护

- 开发期间禁止直接向 `main` 提交业务代码；
- 最终合并前必须获得“同意合并 main”的明确指令；
- 如果仓库支持分支保护，建议为 `main` 开启禁止 force push、要求 CI 通过和要求 Pull Request；
- Codex 不得擅自关闭或降低已有分支保护规则。

---

## 6. PROGRESS.md 规范

阶段 0 创建 `PROGRESS.md`，之后每阶段完成时更新。至少包含：

```markdown
# CrossPilot 开发进度

| 阶段 | 状态 | 完成日期 | Commit | 测试 | 备注 |
| --- | --- | --- | --- | --- | --- |
| 阶段0：仓库初始化 | pending | - | - | - | - |
| 阶段1：公共契约 | pending | - | - | - | - |
| 阶段2：基础能力 | pending | - | - | - | - |
| 阶段3：Agent与编排 | pending | - | - | - | - |
| 阶段4：联调与评测 | pending | - | - | - | - |
| 阶段5：容器化验收 | pending | - | - | - | - |
| 阶段6：发布main | pending | - | - | - | - |

## 当前阶段

- 阶段：
- 状态：
- 已完成：
- 阻塞项：
- 下一步：
```

状态只能使用 `pending`、`in_progress`、`blocked`、`completed`。Commit 哈希必须在提交后回填；如果为保证同一 Commit 同时记录哈希而无法自引用，可以在表格中记录该阶段的代码 Commit，并用随后单独的 `docs(progress)` Commit 更新进度。

---

## 7. 代码与架构约束

### 7.1 范围约束

第一版严格遵循 MVP：

- 只支持美国 Amazon；
- 只支持消费电子配件、儿童玩具和家居用品；
- 使用90～120条仿真离线商品数据；
- 不接 Amazon 实时 API，不编写平台爬虫；
- 不实现评论情感、销量预测、库存、广告、Listing 自动执行；
- 不实现用户登录、长期会话或公网生产部署；
- 不使用 MCP、Function Calling、A2A、FAISS、Milvus 或关系型数据库；
- 不用硬编码答案代替 Agent、图谱查询或计算过程。

### 7.2 模块边界

- Supervisor 只负责意图识别、任务拆分、路由和补充规划；
- Market、Competitor、Pricing、Compliance 是四个同级专业 Agent；
- 多 Agent 被选择时必须真正并行执行；
- Result Validator 独立检查结果完整性；
- Strategy 只在组合问题或市场进入问题调用；
- 市场、竞品和合规通过受控 Text-to-Cypher 查询 Neo4j；
- 定价金额和费率计算必须使用 Python `Decimal`；
- 大模型不允许直接执行数据库写操作或自行心算利润。

### 7.3 Text-to-Cypher 安全要求

- 仅允许单条、参数化、只读 Cypher；
- 用户输入必须作为 params 传递，不直接拼接；
- 先做危险关键词、多语句和 Schema 允许列表校验；
- 再通过 Neo4j `EXPLAIN`；
- 全部通过后才能执行真实查询；
- 校验或执行失败最多生成3次；
- 达到上限后明确失败，禁止无限重试；
- 默认最多返回50行并限制图谱子图大小。

### 7.4 错误处理

- 不吞掉异常，不使用空 `except`；
- 领域错误使用明确异常类型或结构化结果；
- 给用户的错误信息简洁可操作，技术详情写入 Trace；
- 某个并行 Agent 失败时保留其他成功结果；
- 缺少用户信息时暂停并追问，不静默填充默认值；
- 任何 fallback 必须在结果和日志中明确标识。

### 7.5 配置与依赖

- 使用 `.env.example` 提供变量名，不提供真实值；
- 依赖统一写入 `pyproject.toml` 和锁定文件；
- 不在子模块私自增加重复依赖；
- BGE-M3 模型在运行时下载或使用缓存，不提交模型文件；
- Neo4j 数据卷、Python缓存、测试缓存和本地日志必须被 `.gitignore` 排除。

---

## 8. 测试与质量门禁

### 8.1 开发原则

- 新功能先写能表达验收行为的失败测试，再实现最小代码使其通过；
- 修复缺陷必须增加回归测试；
- 不为了让测试通过而删除断言、跳过测试或硬编码测试输入；
- FakeLLM 仅用于确定性单元和 CI 测试，真实模型评测必须单独标记；
- 不得将预期答案注入模型提示词来制造高分。

### 8.2 每阶段最低验证

| 阶段 | 必须验证 |
| --- | --- |
| 阶段0 | Git远程、分支、文档路径、敏感文件排除 |
| 阶段1 | 契约校验、Reducer合并、静态检查 |
| 阶段2 | 图谱导入、查询校验、定价公式、API/UI骨架测试 |
| 阶段3 | 路由、并行、interrupt/resume、replan、Strategy按需调用 |
| 阶段4 | SSE端到端、图谱依据、日志脱敏、30条评测运行 |
| 阶段5 | 全量Pytest、Docker冷启动、8个演示用例、README复现 |
| 阶段6 | 合并后的全量测试、Docker验证和main远程Commit确认 |

### 8.3 完成声明

在宣称阶段完成前，主 Agent 必须重新执行能够证明该阶段完成的命令，并读取完整结果。历史测试、子 Agent 的口头结论或“代码看起来正确”均不能作为完成证据。

如果某项验证无法运行，阶段状态必须标记为 `blocked` 或 `partial`，并说明缺失的证据，不得宣称完成。

---

## 9. GitHub 管理补充

### 9.1 CI

在不影响两天 MVP 主线的前提下，阶段 1 或阶段 5 应增加 `.github/workflows/ci.yml`：

- 对 push 到 `develop` 和 Pull Request 到 `main` 触发；
- 安装项目依赖；
- 执行 Ruff 和 Pytest；
- 不在 CI 中使用真实模型 API Key；
- Neo4j 集成测试使用 CI service container 或明确分组；
- CI 失败时不得合并 `main`。

### 9.2 Issue 和 Pull Request

- 如果 GitHub CLI 已登录且用户允许，可为每个开发阶段建立一个 Issue；
- Issue 内容引用开发计划 Task 和验收条件；
- 阶段代码完成但尚未获用户确认时，不关闭对应 Issue；
- 最终建议通过 `develop -> main` Pull Request 展示完整变更和 CI；
- 创建、关闭或编辑 Issue/PR 前必须处于本项目范围，不得操作其他仓库。

Issue/PR 管理是补充能力。GitHub CLI 未配置时不能阻塞核心开发，使用 `PROGRESS.md` 和阶段 Commit 保持进度可追踪。

---

## 10. 安全与禁止事项

任何 Agent 均不得：

- 提交 `.env`、API Key、密码、Token、Cookie、SSH 私钥或其他凭证；
- 在聊天和日志中输出完整凭证；
- 强制推送、重写已推送历史或破坏远程分支；
- 删除用户文件、远程数据、分支、标签或 Release；
- 使用 `git reset --hard`、`git clean -fd` 等破坏性命令；
- 覆盖用户已有未提交修改；
- 修改仓库 remote 指向而不告知用户；
- 关闭测试、降低断言或修改评测标准来掩盖失败；
- 声称仿真数据是真实 Amazon 数据；
- 把合规准备提示描述为法律结论或厂家认证结果；
- 未获用户同意进入下一开发阶段；
- 未获用户明确同意将 `develop` 合并到 `main`。

需要额外权限、真实凭证、付费服务、外部数据或破坏性操作时，停止并向用户说明目的、影响和更安全的替代方案。

---

## 11. 沟通规范

- 默认使用中文向用户汇报；
- 先给结论和当前状态，再说明技术细节；
- 长时间任务开始前说明正在做什么，关键里程碑后主动更新；
- 不连续发送无实质内容的进度消息；
- 遇到失败如实报告命令、错误摘要和影响；
- 每次只请求用户决定真正会影响范围或结果的问题；
- 文件引用使用可点击路径，Commit 和分支使用代码格式；
- 阶段报告必须包含下一阶段的具体步骤，而不是只说“继续开发”。

---

## 12. 初次接手本仓库的必做步骤

任何新的主 Agent 在开始开发前必须：

1. 完整阅读 `AGENTS.md`；
2. 完整阅读 `CrossPilot_项目需求文档.md`；
3. 完整阅读 `docs/superpowers/plans/2026-09-04-crosspilot-implementation.md`；
4. 检查当前 `PROGRESS.md`，确认正在进行或等待审批的阶段；
5. 执行 Git 只读检查，确认工作区、分支和远程状态；
6. 检查最近提交，避免重复已经完成的任务；
7. 如果上一个阶段正在等待用户确认，不得继续编码；
8. 向用户汇报当前状态、建议继续的阶段和该阶段任务；
9. 获得用户明确同意后再开始实施。

本项目的目标不仅是快速生成代码，还要让用户能够随时知道：当前完成到了哪里、代码是否经过验证、远程仓库保存了什么、下一步将做什么，以及哪些问题需要他决定。
