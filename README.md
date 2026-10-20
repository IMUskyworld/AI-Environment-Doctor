# envdoctor（AI Environment Doctor）

[![CI](https://github.com/IMUskyworld/envdoctor/actions/workflows/ci.yml/badge.svg)](https://github.com/IMUskyworld/envdoctor/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

**「这个 AI 项目能不能在我这台机器上跑？」——用证据回答，而不是靠猜。**

envdoctor 扫描一个 GitHub 项目（或本地项目）的依赖、Python 约束、CUDA 线索与 ML 栈，探查你的机器（GPU / 驱动 / 解释器 / 工具链 / uv / conda），按带来源的规则库给出逐项判定，最后**在系统临时目录里真的创建一个一次性环境、真的安装、真的跑一遍 import 冒烟和最小模型**，然后告诉你：能不能跑、差什么、先修哪个。

```bash
envdoctor doctor https://github.com/xxx/yyy
```

**支持平台：Windows 10/11 x64**（本项目只面向 Windows 打磨与测试；代码本身不绑定平台，
在 WSL2 里也能跑，但不在官方支持与 CI 覆盖范围内）。

专为 **Windows + NVIDIA + PyTorch + uv/conda + CUDA** 场景打磨。

| | |
| --- | --- |
| 判定不靠猜 | 每条结论都带 `evidence`（扫描/探查/矩阵/规则/日志）与修复动作；证据不足标记 `unknown`，绝不假装 `runs` |
| 真的会跑 | 临时 venv → 真安装 → import 冒烟 → 设备检查 → 最小模型前向/反向；全程有检查点与原始日志 |
| 不碰你的环境 | 验证环境在系统临时目录，默认跑完即删；前后对解释器包集合做指纹比对（`user-env-unchanged`） |
| 知情同意 | 安装第三方依赖会执行其构建脚本——默认必须交互确认；`--yes` 跳过、`--no-verify` 完全只做静态分析 |
| 可审计的知识库 | CUDA↔最低驱动矩阵、规则库、包级 CUDA 需求都是 JSON 数据文件，每条带来源链接，改数据不用改代码 |

## 快速开始

```powershell
# 从源码运行（开发与试用；CJK 路径下 editable install 会坏，见 DEVELOPMENT.md §24）
git clone <this repo>; cd "AI Environment Doctor"
uv venv --python 3.11
uv pip install typer pydantic rich packaging psutil jinja2 httpx pyyaml nvidia-ml-py   # 国内网络：加 UV_DEFAULT_INDEX=<镜像>
.venv\Scripts\activate
python scripts/envdoctor_dev.py doctor /path/to/project
```

装到 **ASCII 路径**后即可直接用 `envdoctor` 命令：

```powershell
uv tool install .        # 或 pipx install . / pip install .
envdoctor doctor https://github.com/xxx/yyy
```

> 真实验证阶段需要 [`uv`](https://docs.astral.sh/uv/)（创建一次性环境）。
> 缺 `uv` 时 envdoctor 会明确说明并只做静态分析，不会假装跑过。

## 用法

```bash
envdoctor doctor  <目标>              # 全流程：scan → probe → analyze → [确认] → verify → report
envdoctor doctor  <目标> --no-verify  # 只做静态分析（零安装、零执行）
envdoctor doctor  <目标> --yes        # 跳过确认（CI/批处理）；非交互终端无 --yes 会显式跳过验证
envdoctor analyze <目标>              # 判定明细（不装任何东西）
envdoctor scan    <目标>              # 项目画像
envdoctor probe                       # 机器画像
envdoctor web                         # Web 控制台（浏览器界面，见下）
envdoctor version                     # 版本信息
```

常用参数：`--python 3.12` 指定验证解释器、`--index-url` 换安装源、`--smoke-cmd "pytest -q"` 追加冒烟命令、`--keep-env` 保留临时环境、`--json/--html` 另存报告、`--config <path>` 指定配置文件、`--explain` 附加 AI 解释（可选，默认关）。

**退出码**（`doctor`）：`0` 能跑（runs）· `1` 不能跑（cannot_run）· `2` 证据不足（unknown）· `3` 内部错误 · `130` 中断。

## Web 控制台

```bash
envdoctor web            # 默认 http://127.0.0.1:8765，自动打开浏览器
envdoctor web --port 9000 --no-open
```

浏览器里可以：填目标（本地路径或 git URL）→ **扫描并判定** → 看逐条判定与可展开的证据链 →
勾选知情同意后 **真实验证**（与 CLI 同一套流水线：临时 venv → 真安装 → import 冒烟 → 设备检查 →
最小模型），过程中有实时日志，完成后 `report.json` / `report.html` 与 CLI 共用同一份 run 目录。

安全边界（比 CLI 更严格）：

- 默认只监听 `127.0.0.1`；每次启动生成一次性令牌，所有请求都要带；
- `Host` 头必须是回环地址（防 DNS rebinding），写操作必须同源；
- 「真实验证」必须显式勾选知情同意才会触发，与 CLI 的确认门一个标准；
- `--allow-remote` 才允许非回环访问（会明确告警，请自行承担风险）。

## 输出示例

```
❌ CANNOT RUN
存在阻断性判定：项目需要 GPU，但本机未检测到 NVIDIA GPU

判定明细
状态       │ 方面    │ 结论
✗ BLOCKED  │ gpu     │ 项目需要 GPU，但本机未检测到 NVIDIA GPU
           │         │ 依赖包含 GPU 专属组件（xformers/flash-attn/nvidia-*/+cuXXX），CPU 无法替代
✓ OK       │ deps    │ CUDA wheel 源已配置
– N/A      │ python  │ 未声明 Python 版本要求
```

另有自包含的 `report.html`（离线可开、无外部资源引用）与机器可读的 `report.json`，全部落在 `.envdoctor/runs/<run-id>/`。

## 它怎么工作

```
TARGET ──▶ scan   ──▶ 项目画像：依赖 / Python 约束 / ML 栈 / CUDA 线索
MACHINE ──▶ probe  ──▶ 机器画像：GPU / 驱动 / 解释器 / 工具链
              │
              ▼
         compat（规则引擎 + 知识库）──▶ verdicts：ok / warn / blocked / unknown / na
              │
              ▼
         确认门（交互 [y/N]；非交互需 --yes；--no-verify 跳过整段）
              │
              ▼
         verify（系统临时目录里的一次性 venv）
              │  venv → install → imports → device → minimal-model → smoke-cmd
              ▼
         report：控制台 + report.json + report.html
```

**知识库是数据驱动的**（改知识不用改代码）：

| 文件 | 内容 |
| --- | --- |
| `src/envdoctor/compat/data/cuda_driver_matrix.json` | CUDA 11.8 ~ 13.0 ↔ 最低驱动（Windows / Linux），数值取自 NVIDIA 官方发布说明 |
| `src/envdoctor/compat/data/compatibility_rules.json` | 生态坑规则（TF / JAX / flash-attn / bitsandbytes / triton / xformers / deepspeed / vLLM / apex / NumPy 2 ABI / cuDNN 主版本…），每条带官方来源 |
| `src/envdoctor/compat/data/package_cuda_requirements.json` | 包自己的构建钉住了哪个 CUDA runtime（如 onnxruntime-gpu 1.21+ → CUDA 12.8） |

新增一条规则只需要改 JSON + 补一个测试，见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 安全边界（请读）

- `verify` 会执行第三方包的构建脚本（PEP 517/setup.py），隔离等级是「一次性 venv」而非沙箱；只在你信任的项目上运行。不确定就用 `--no-verify`。
- envdoctor 从不修改你的真实环境：不装全局包、不改 PATH、不动 conda base。
- `--explain` 只发送脱敏摘要（家目录/用户名/主机名替换为占位符；环境变量与文件内容永不上传），且默认关闭。
- 无遥测；联网只发生在 clone、安装源预检、真实安装与 `--explain`。
- 详见 [SECURITY.md](SECURITY.md)。

## 开发

```powershell
uv venv --python 3.11
uv pip install -e ".[dev]"   # ASCII 路径下；CJK 路径请用 scripts/envdoctor_dev.py
.venv\Scripts\activate
python -m pytest -q                                        # 单元 + 集成
$env:ENVDOCTOR_TEST_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"; python -m pytest -q -m network   # 真实验证
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1   # 完整质量闸门
```

- 验收：`python scripts/verify_mvp.py` 逐条核验 `DEVELOPMENT.md §22` 的完成定义。
- 基准：`python scripts/bench_overhead.py` 对照性能目标。
- 规格书：[DEVELOPMENT.md](DEVELOPMENT.md)（产品定义、数据协议、安全模型、测试策略、决策记录）。
- 决策记录：[docs/adr/](docs/adr/)。
- 问题反馈：[Issues](https://github.com/IMUskyworld/envdoctor/issues) · 变更历史：[CHANGELOG.md](CHANGELOG.md)

## 目录结构

```
src/envdoctor/
├── domain/    pydantic 数据模型与枚举（scan/probe/verdict/checkpoint/report）
├── scan/      目标解析、各生态文件解析、ML 栈与 CUDA 线索检测
├── probe/     nvidia-smi / NVML / 解释器 / 工具链探查
├── compat/    判定引擎 + 知识库（矩阵与规则都是 JSON 数据文件）
├── verify/    验证计划、一次性 venv、安装、冒烟脚本、清理
├── report/    控制台渲染、JSON / 自包含 HTML 报告
├── explain/   可选 LLM 解释层（脱敏）
├── web/       Web 控制台（标准库 HTTP 服务 + 单页界面，零新增依赖）
└── cli/       typer 命令接线、退出码映射、中断处理
```

## English summary

envdoctor answers "can this AI project run on my machine?" with evidence: it scans the
project, probes the machine (GPU/driver/interpreters/toolchain), applies a source-backed
compatibility knowledge base, then **really installs and smoke-tests the project in a
throwaway venv** and reports runs / cannot_run / unknown with per-claim evidence and fixes.
Windows + NVIDIA + PyTorch first; local-first, no telemetry, never touches your real
environments.

## License

MIT
