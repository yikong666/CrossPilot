# Task 7/8 Market 与 Pricing 组合复审修复记录

## 范围

- 只修改 `app/agents/supervisor.py` 以及分配的三个测试文件。
- 保留未跟踪的 `task-8-market-answer-report.md`，未纳入本次提交。

## TDD 记录

### RED

1. 新增 Supervisor 单元回归测试后执行 `uv run pytest tests/unit/test_supervisor.py -q`：
   `2 failed, 10 passed`。失败明确显示：英文 profit/margin 未触发 pricing fallback，且模型已选择 pricing 时原有 `required_fields` 未补充 `margin`、也未去重。
2. 新增真实 `Supervisor` + `LLMClient` 结构化 fake transport + `PricingAgent` + 编译 workflow 的集成回归测试后执行
   `uv run pytest tests/integration/test_workflow.py -q -k real_supervisor_pricing`：
   `1 failed`。首次流的末事件实际为 `workflow_completed`，而非缺少 `selling_price_usd` 时应有的 `input_required`。

### GREEN

1. Supervisor 对中英文利润/毛利/profit/margin 问题：
   - 保留模型已有 `required_fields`；
   - 去重后补充 `profit` 与 `margin`；
   - fallback 新增 pricing 任务时同样添加这些已实现输出；
   - 仅询问售价、目标售价或成本时不会因此要求当前售价。
2. `uv run pytest tests/unit/test_supervisor.py -q`：`12 passed`。
3. `uv run pytest tests/integration/test_workflow.py -q -k real_supervisor_pricing`：
   `1 passed, 37 deselected`。该流程先请求 `selling_price_usd`，恢复后完成，并在答案中包含非空 `profit="1.50"` 和 `margin="0.0600"`。
4. reducer 测试的 Market fixture 已由废弃的聚合字段改为 `prices`、`brands`，并在 reducer 合并前断言 Market 成功、样本数、完整价格统计和头部品牌份额。

## 最终验证

| 命令 | 实际结果 |
| --- | --- |
| `uv run pytest tests/unit/test_supervisor.py tests/unit/test_specialist_agents.py tests/integration/test_workflow.py -q` | `55 passed` |
| `uv run pytest tests/unit/test_market_agent.py tests/unit/test_pricing_agent.py tests/unit/test_pricing_calculator.py tests/integration/test_fee_repository.py -q` | `56 passed, 1 skipped` |
| `uv run ruff check .` | `All checks passed!` |
| `uv run mypy --python-version 3.12 app/agents/supervisor.py` | `Success: no issues found in 1 source file` |
| `TEMP/TMP=.pytest-tmp; uv run pytest -q --basetemp .pytest-tmp\\stage3-final-verify` | `251 passed, 3 skipped, 1 warning` |
| `git diff --check` | 通过 |

## 环境说明

- 当前 worktree 的 `uv` 解释器为 Python 3.12，但 `pyproject.toml` 的 Mypy 目标是 3.11。直接运行 `uv run mypy app/agents/supervisor.py` 会被 NumPy 3.12-only stub 在项目代码分析前阻断；按当前解释器重跑 3.12 目标后通过。
- 直接全量 pytest 使用用户全局 Temp 目录时遇到 `WinError 5`。将 `TEMP`、`TMP` 与 `--basetemp` 限定到本 worktree 的已忽略 `.pytest-tmp` 后，全量测试通过。
