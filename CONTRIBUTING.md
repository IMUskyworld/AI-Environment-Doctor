# 贡献指南

感谢你愿意为 envdoctor 花时间。这个项目的价值建立在**结论可信**之上，
所以贡献的第一原则是：**宁可说「不知道」，也不要编一个看起来确定的结论。**

## 开发环境

```powershell
uv venv --python 3.11
uv pip install -e ".[dev]"     # ASCII 路径下可以直接 editable install
.venv\Scripts\activate
```

> **Windows + 非 ASCII 路径的坑**：本仓库曾位于 `C:\code\github小项目\...`。
> 在这类路径下 editable install 会坏掉——`.pth` 文件按 locale 编码解码，
> 中文路径变成乱码，`import envdoctor` 直接失败。遇到这种情况改用源码运行：
> `python scripts/envdoctor_dev.py doctor <目标>`（它把 `src` 注入 `sys.path`）。
> 详见 DEVELOPMENT.md §24。

## 质量闸门（提交前必须全绿）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1
```

它依次执行：

| 步骤 | 命令 | 作用 |
| --- | --- | --- |
| lint | `ruff check src tests scripts` | 静态检查 |
| format | `ruff format --check src` | 格式统一 |
| types | `pyright` | 类型检查（只看 `src`） |
| schemas | `scripts/export_schemas.py` | 数据模型变更后重新导出 JSON Schema |
| tests | `pytest -q -m "not gpu"` | 单元 + 集成测试 |
| acceptance | `scripts/verify_mvp.py --skip-tests` | 逐条核验 DEVELOPMENT.md §22 |

## 贡献知识库（最常见的贡献方式）

判定规则的**内容**在数据文件里，改内容不用改代码：

- `src/envdoctor/compat/data/compatibility_rules.json` — 生态坑规则
- `src/envdoctor/compat/data/cuda_driver_matrix.json` — CUDA ↔ 最低驱动矩阵
- `src/envdoctor/compat/data/package_cuda_requirements.json` — 包级 CUDA 构建要求

新增一条规则的硬性要求：

1. **必须有可审计的官方来源**（`source.url` 指向官方文档，而不是博客转述）；
2. **必须有单元测试**（`tests/unit/test_knowledge_base.py`）；
3. **必须写清楚修复动作**（`fix`），而不只是描述问题；
4. 数值型知识（版本、驱动号）**只改数据文件**，不要写进代码；
5. 更新数据文件的 `reviewed_on` 字段。

条件表达式（`when`）目前支持这些 `kind`：

`lib_present` / `lib_may_install_below` / `lib_may_install_at_or_above` /
`lib_pinned_below` / `lib_requires_at_least` / `os` / `gpu_present` /
`driver_lt` / `cuda_runtime_ge` / `python_available_lt` / `file_present`，
并支持 `all` / `any` / `not` 组合。

> `lib_may_install_*` 读作「**可能**解析到某侧版本」（未固定版本 ⇒ 风险成立）；
> `lib_pinned_below` / `lib_requires_at_least` 读作「**保证**在某侧版本」
> （未固定版本 ⇒ 不成立）。选错方向会把结论说反，务必想清楚。

## 不要破坏的三条红线

1. **证据不足 → `unknown`**，不要外推、不要猜。
2. **静态分析永远不报 `runs`**（没实测过就是不知道）。
3. **不碰用户环境**：验证只发生在系统临时目录里的一次性 venv 中。

## 提交信息

用中文或英文都可以，但请说清「改了什么」和「为什么」；
涉及判定语义的改动请附上对应的测试与来源链接。
