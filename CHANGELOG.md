# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.2.0] - 2026-10-20

### 新增

- **Web 控制台**：`envdoctor web` 启动本地浏览器界面（默认 `http://127.0.0.1:8765`，
  自动打开浏览器）。填目标 → 扫描并判定 → 看逐条判定、证据链与修复建议 →
  勾选知情同意后做真实验证（与 CLI 同一套流水线），全程实时日志。
  实现只用 Python 标准库，**零新增运行时依赖**；前端是单文件自包含页面（离线可用、无外部请求）。
  安全：默认仅监听回环地址、每次启动一次性令牌、`Host` 头校验（防 DNS rebinding）、
  写操作同源校验、真实验证需显式同意。
  Web 与 CLI 共用 `.envdoctor/runs/` 产物，报告只有一份真相。

## [0.1.0] - 2026-10-20

首个公开版本：扫描项目 + 探查机器 + 规则引擎判定 + 一次性环境真实验证。

### 新增

- **流水线**：`doctor` / `scan` / `probe` / `analyze` / `version` 五个子命令，
  完整链路为 scan → probe → analyze → 确认门 → verify → report。
- **扫描器**：解析 `requirements*.txt`（含 `-r` 递归、markers、extras、索引源）、
  `pyproject.toml`（PEP 621 与 poetry）、`setup.cfg` / `setup.py`（纯正则，不执行）、
  `environment.yml`、`Pipfile`、`.python-version`；识别 ML 栈角色与 CUDA 线索。
- **机器探查**：nvidia-smi → NVML → 无 降级链（失败必带原因）；
  解释器发现（当前解释器 / `py -0p` / `uv python list`）；工具链、内存、临时盘。
- **判定引擎**：内置判定（Python 匹配、GPU 需求强度、驱动 ↔ CUDA 矩阵、
  `+cuXXX` 索引源、torch CPU wheel 风险、conda 环境）+ 数据驱动的生态规则库，
  每条判定带 `evidence[]` 与 `confidence`。
- **包级 CUDA 需求**：`package_cuda_requirements.json` 记录「包自己的构建钉住了哪个
  CUDA runtime」（如 onnxruntime-gpu 1.21+ → CUDA 12.8），并与驱动矩阵交叉验证。
- **真实验证**：临时 venv → 真实安装 → import 冒烟 → 设备检查 → 最小模型前向/反向
  → 用户附加的 `--smoke-cmd`；每步一个检查点，原始输出落盘 `logs/`；
  默认跑完即删（`--keep-env` 可保留）。
- **报告**：rich 控制台、`report.json`、**完全自包含**的 `report.html`（离线可开）。
- **可选 AI 解释层**（`--explain`，默认关闭）：脱敏摘要 → OpenAI 兼容端点 → 自然语言结论。
- **知识库**：CUDA ↔ 最低驱动矩阵（11.8 ~ 13.0，数值取自 NVIDIA 官方发布说明）、
  11 条带官方来源的生态规则（TF/JAX/flash-attn/bitsandbytes/triton/xformers/deepspeed/
  vLLM/apex/NumPy 2 ABI/cuDNN 主版本）。
- **契约**：退出码 `0` runs · `1` cannot_run · `2` unknown · `3` 内部错误 · `130` 中断。
- **诚实性红线**：静态分析永远不报 `runs`；证据不足一律 `unknown`。

### 已知限制

- 真实验证依赖 `uv`；缺失时明确跳过并说明原因（`overall=unknown`）。
- conda 只解析不执行（`environment.yml` 的 conda 依赖不参与 pip 安装）。
- 隔离等级是「一次性 venv」，**不是沙箱**；安装会执行第三方构建脚本。
- GPU 真实路径需要带 NVIDIA 驱动的 Windows/Linux 机器（`pytest -m gpu`）。
