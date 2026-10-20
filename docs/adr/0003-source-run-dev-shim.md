# ADR 0003：开发期从源码运行（dev shim），不做 editable install

- 状态：已采纳（2026-09-22，继承自 LabBox 的实测教训）
- 背景：项目位于 `C:\code\github小项目\AI Environment Doctor`（CJK + 空格）。可编辑安装生成的 `.pth` 由 `site.py` 按 **locale 编码**（cp936）解码，非 ASCII 路径会变成乱码，`import envdoctor` 失败，`envdoctor.exe` 无法启动。
- 决策：
  - 开发与测试一律通过 `scripts/envdoctor_dev.py`（注入 `src` 到 `sys.path`）或 pytest `pythonpath=["src", "tests"]` 运行；
  - venv 只装第三方依赖，不装本项目；
  - 用户安装走 ASCII 路径（`uv tool install .` / pipx）。
- 理由：把环境限制变成工程约束，而不是每次运行前手动绕坑；同时保持安装产物对用户可用。
- 相关：DEVELOPMENT.md §24（开发约束）。
