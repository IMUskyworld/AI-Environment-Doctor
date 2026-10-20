# 提交前请确认

- [ ] `uv run ruff check src tests scripts` 通过
- [ ] `uv run ruff format --check src tests scripts` 通过
- [ ] `uv run pyright` 无错误
- [ ] `uv run pytest -q -m "not gpu"` 全绿
- [ ] `uv run python scripts/export_schemas.py --check` 显示 schemas 是最新的
      （改了 `domain/models.py` 就必须重新导出）
- [ ] `uv run python scripts/verify_mvp.py --skip-tests` 无 FAIL

# 改动说明

<!-- 这次改了什么，为什么 -->

# 知识库改动（如果动了 `compat/data/*.json`）

- [ ] 每条新增规则都带 `source.url`（可审计的官方来源）
- [ ] 每条新增规则都有对应单元测试（`tests/unit/test_knowledge_base.py`）
- [ ] 驱动矩阵的数值与 NVIDIA 官方表一致，并在 `note` 里说明推导方式
- [ ] `reviewed_on` 已更新为今天

# 诚实性红线（不可放宽）

- [ ] 证据不足时状态是 `unknown`，不是「看起来正常」
- [ ] 静态分析（`--no-verify`）不会给出 `runs`
- [ ] 每条判定都带 `evidence[]`，来源可回溯
