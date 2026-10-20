# envdoctor 开发文档（AI Environment Doctor）

版本：v0.1（2026-09-22）
状态：MVP 已实现并验收，逐条核验见 §25。

一句话：把「这个 AI 项目能不能在我这台机器上跑」从猜测变成**带证据的结论**，并且真的在临时环境里装一遍、跑一遍来验证。

---

## 1. 项目定位

用户拿到一个 GitHub 上的 AI/ML 项目（或自己的本地项目），最常遇到的第一个问题是：

> 它在我这台电脑上到底能不能跑？

现有工具给出的答案通常是碎片化的：README 里一句「建议 Python 3.10 + CUDA 11.8」，报错后靠搜索引擎猜原因，或者一段「可能是版本不兼容」的模糊结论。

envdoctor 的目标是给出三层递进的答案：

1. **静态结论**：扫描项目（依赖、Python 约束、CUDA 线索、ML 栈）+ 探查机器（GPU、驱动、解释器、工具链），按规则引擎给出逐项判定。
2. **真实验证**：在系统临时目录里创建一个一次性虚拟环境，真实安装依赖，跑 import 冒烟测试和最小模型（前向/反向），把「能不能跑」变成实测结果。
3. **可执行修复建议**：每条判定都带证据（evidence）和修复动作（fix），例如「升级驱动到 ≥ 555.85（来源：CUDA 12.5 最低驱动要求）」而不是「可能是 CUDA 不兼容」。

三条硬性原则（与 LabBox 一脉相承）：

- **Reproducible or explicit**：无法证明的结论必须明确标记为 `unknown` 并说明缺什么证据，绝不把猜测伪装成结论。
- **Never fake certainty**：没有 nvidia-smi / 没有驱动时，不是「一切正常」，而是「GPU 状态未知（原因是 …）」。
- **用户的机器神圣不可侵犯**：envdoctor 永远只在系统临时目录里创建一次性环境；默认流程结束后清理；不修改 PATH、不装全局包、不动 conda base、不写目标项目目录。

## 2. MVP 范围

**做：**

- 扫描本地路径或 GitHub URL（`git clone --depth 1`）指向的项目。
- 识别：requirements*.txt（含 `-r` 递归、markers、extras、`--index-url`/`--extra-index-url`/`-f`、`+cuXXX` 本地版本）、pyproject.toml（PEP 621 与 poetry）、setup.cfg/setup.py 关键字段、environment.yml / environment.yaml（conda，含 `pip:` 子段）、Pipfile、`.python-version`。
- ML 栈识别：torch / torchvision / torchaudio / tensorflow / jax / xformers / flash-attn / bitsandbytes / deepspeed / triton / transformers / accelerate / onnxruntime-gpu / cupy / nvidia-* 等，并区分「核心框架 / 扩展 / 工具」。
- CUDA 线索提取：`+cuXXX` 本地版本、pytorch 官方 index URL（`download.pytorch.org/whl/cu121`）、`nvidia-*-cu12` 包名、conda 的 `pytorch-cuda=` / `cudatoolkit=`。
- 机器探查：OS / 架构、Python 解释器清单（当前解释器、Windows `py -0p`、`uv python list --only-installed`）、工具链（uv / pip / git / conda 是否存在及版本）、NVIDIA GPU（nvidia-smi → NVML 降级链）、RAM、临时盘剩余空间、控制台编码。
- 兼容性分析：Python 版本匹配、GPU 有无与需求强度、驱动版本 ↔ 所需 CUDA runtime（内置驱动矩阵）、生态规则库（TF/JAX/flash-attn/xformers/bitsandbytes/triton 等已知坑，每条带来源）。
- 真实验证（默认执行，安装前交互确认）：临时 venv → 安装 → import 冒烟 → 设备检查 → 最小模型前向/反向 → 可选 `--smoke-cmd`。
- 报告：控制台（rich）、`report.json`（schema 化）、自包含 `report.html`。
- 可选 AI 解释层（`--explain`，默认关闭）：把证据链摘要脱敏后交给 OpenAI 兼容端点，生成自然语言结论与修复建议；失败只降级为警告。

**不做（MVP 明确排除）：**

- 不做依赖解析器，不替代 pip/uv/conda；不实现自己的 resolver。
- 不自动修复用户的真实环境（不装驱动、不改 PATH、不动全局包）。
- 不做容器/VM 隔离；隔离等级只有「一次性 venv」，安全边界在 §17 明确说明。
- 不下载模型权重、不下载数据集、不跑完整训练（用最小模型代替；用户可用 `--smoke-cmd` 自带命令）。
- 不执行 conda 环境创建（environment.yml 只做解析与提示；验证路径统一走 uv/pip）。理由：本机无 conda，且 uv 已覆盖核心场景；后续版本可扩展。
- 不做 LLM 训练侧调优建议、不做性能基准（那是别的工具的领域）。

## 3. 目标用户和使用场景

| 用户 | 场景 |
| --- | --- |
| 本人（Windows 11 + RTX 3090 24GB） | 从 GitHub 捡 ML 项目时先体检：值不值得配环境、要升驱动吗、CUDA 版本选哪个。 |
| 同学 / 实验室 | 拿到别人的代码跑不起来，先跑 envdoctor 得到「缺什么、装什么、版本组合」，而不是截图报错问人。 |
| 复现论文 / 开源实验前 | 确认本机环境满足最低条件，并生成一份可粘贴给 issue 的证据报告。 |
| CI 场景（远期） | `--yes --json` 无人值守产出机器可读结论。 |

典型命令：

```bash
envdoctor doctor https://github.com/xxx/yyy     # 全流程（安装前会交互确认）
envdoctor doctor ./local/project --no-verify    # 只做静态分析
envdoctor analyze https://github.com/xxx/yyy --json out.json
envdoctor probe                                 # 只看机器自身状况
```

## 4. CLI 设计

基于 typer，五个子命令：

```
envdoctor doctor  <TARGET>   完整流水线：scan → probe → analyze → [确认] → verify → report
envdoctor scan    <TARGET>   只扫项目，输出项目画像
envdoctor probe              只探查本机，输出机器画像
envdoctor analyze <TARGET>   scan + probe + 规则引擎，输出逐项判定（不装任何东西）
envdoctor web                Web 控制台（浏览器界面，§26）
envdoctor version            版本信息
```

`doctor` / `scan` / `analyze` 公共参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `TARGET` | 必填 | 本地路径，或 `https://` / `git@` 的 git URL（`--depth 1` 克隆到缓存目录） |
| `--json <PATH>` | run 目录 | 另存 JSON 结果到指定路径 |
| `--workdir <DIR>` | `./.envdoctor` | 运行目录根（放 runs/、cache/） |
| `--refresh` | 关 | 忽略缓存的克隆，重新 clone |
| `-v / -q` | 正常 | 详细 / 安静 |

`doctor` 专有参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--no-verify` | 关 | 跳过真实验证（只做静态分析；总体结论将如实为 `unknown`，不会假装 `runs`） |
| `--yes` | 关 | 跳过安装前的交互确认（CI/批处理用） |
| `--keep-env` | 关 | 保留临时验证环境，打印路径供检查 |
| `--python <X.Y>` | 自动 | 指定验证用解释器（从已发现的解释器里选） |
| `--index-url <URL>` | 项目自身优先 | 覆盖安装源；只影响验证用临时环境 |
| `--extra-index-url <URL>` | — | 追加安装源，可重复 |
| `--smoke-cmd <CMD>` | — | 额外冒烟命令（在临时环境里执行），可重复 |
| `--timeout <SEC>` | 600 | 单步超时（安装步骤） |
| `--explain` | 关 | 附加 AI 解释（§14） |
| `--html <PATH>` | run 目录 | 指定 HTML 报告输出路径 |

**退出码**（稳定契约，供脚本/CI 使用）。以下映射针对 `doctor`（只有它给出「能否运行」的结论）；`scan` / `probe` / `analyze` 成功即 0，目标/参数错误为 3：

| 码 | 含义 |
| --- | --- |
| 0 | `runs`（可能带 warning 级注意事项） |
| 1 | `cannot_run`（存在 blocked 判定或实测失败） |
| 2 | `unknown`（证据不足：未实测、关键数据缺失等） |
| 3 | 内部错误（EnvDoctor 自身异常） |
| 130 | 用户中断（Ctrl+C），临时环境已清理 |

**总体结论判定规则**（`overall`）：

1. 存在任一 `blocked` verdict → `cannot_run`。
2. 执行了 verify：全部必需检查点 pass → `runs`；任一 fail → `cannot_run`；关键检查点 skip/error 导致未能验证 → `unknown`。
3. 未执行 verify（`--no-verify` 或确认被拒）：无 blocked 时 → `unknown`，并列出「需要实测才能确认」的条目。静态分析永远不给 `runs`，这是诚实性红线。

## 5. 总体架构

```
                    ┌─────────────┐
   TARGET ─────────▶│   scan      │  scan.json     项目画像：依赖/约束/CUDA 线索/ML 栈
 (path | git url)   └──────┬──────┘
                           │
   MACHINE ────────▶┌──────┴──────┐
   (nvidia-smi/     │   probe     │  probe.json     机器画像：GPU/驱动/解释器/工具链
    NVML/py/uv)     └──────┬──────┘
                           │
                    ┌──────┴──────┐
                    │  compat     │  verdicts.json  逐项判定：ok/warn/blocked/unknown/na
                    │  (规则引擎)  │     ▲ 知识库：data/cuda_driver_matrix.json
                    └──────┬──────┘     │        data/compatibility_rules.json
                           │
                    ┌──────┴──────┐
                    │  确认门      │  交互 [y/N]；非交互需 --yes；--no-verify 跳过整段
                    └──────┬──────┘
                           │
                    ┌──────┴──────┐
                    │  verify     │  verify.json    checkpoints: venv/install/imports/
                    │ (uv 一次性   │                  device/minimal-model/smoke-cmd
                    │  临时环境)   │     logs/*.log   原始子进程输出（字节级保存）
                    └──────┬──────┘
                           │
                    ┌──────┴──────┐
                    │  report     │  report.json + report.html + 控制台
                    │  (+explain) │  explain 可选：脱敏摘要 → LLM → 自然语言建议
                    └─────────────┘
```

模块划分（`src/envdoctor/`）：

| 模块 | 职责 |
| --- | --- |
| `domain/` | pydantic 数据模型 + 枚举（ScanResult / MachineProfile / Verdict / Checkpoint / Report） |
| `scan/` | 目标解析（路径/git clone）、各生态文件解析、ML 栈与 CUDA 线索检测 |
| `probe/` | nvidia-smi 与 NVML 来源抽象、Python 解释器发现、工具链检测 |
| `compat/` | 规则引擎、CUDA↔驱动矩阵查询、内置规则库（数据文件驱动） |
| `verify/` | 验证计划、一次性 venv、安装、冒烟脚本生成与执行、清理 |
| `report/` | 控制台渲染、report.json 写出、HTML 模板渲染 |
| `explain/` | 可选 LLM 解释层（OpenAI 兼容，脱敏） |
| `web/` | Web 控制台：标准库 HTTP 服务、后台任务、单页前端（§26） |
| `cli/` | typer 命令接线、退出码映射、中断处理 |

## 6. 运行生命周期

`envdoctor doctor` 的完整生命周期：

1. **resolve**：解析 TARGET。路径 → 直接使用；git URL → 查 `.envdoctor/cache/repos/<hash>/`，命中且非 `--refresh` 则复用，否则 `git clone --depth 1`（`GIT_TERMINAL_PROMPT=0`，不弹凭据）。
2. **scan**：解析生态文件 → `ScanResult` → 落盘 `scan.json`。解析失败的单个文件不致命，记 warning 继续。
3. **probe**：按 nvidia-smi → NVML 顺序尝试；失败降级为 `gpu.present=false, source=none, reason=…`；解释器与工具链探测并行。→ `probe.json`。
4. **analyze**：通用判定（Python 匹配、GPU 需求强度、驱动-运行时矩阵）+ 规则库匹配 → `verdicts.json`。
5. **gate**：打印验证计划摘要（解释器、安装源、包数、临时目录、将执行的步骤），等待 `[y/N]`。`--yes` 直接过；非交互终端（无 TTY）且无 `--yes` → 跳过验证并**明确打印原因**（不静默），总体结论 `unknown`。
6. **verify**：创建临时 venv → 安装 → import 冒烟 → device 检查 → 最小模型 → 可选 smoke-cmd；每步一个 checkpoint，原始输出字节级落盘 `logs/`。结束后默认删除临时环境；`--keep-env` 保留并打印路径。
7. **report**：渲染控制台 + 写 `report.json` / `report.html`；`--explain` 时追加 LLM 解释（失败仅警告）。
8. **exit**：按 §4 退出码规则退出。

**中断行为**：任一阶段 Ctrl+C → 捕获 `KeyboardInterrupt` → 清理临时环境 → 在 run 目录写 `interrupted: true` 的标记文件 → 退出码 130。run 目录里已落盘的部分结果保留（不删除、不伪装完整）。

## 7. 本地目录协议

```
<workdir>/                            # 默认 ./.envdoctor
├── runs/
│   └── <YYYYMMDD-HHMMSS>-<8hex>/     # 一次完整运行
│       ├── scan.json                 # 项目画像
│       ├── probe.json                # 机器画像
│       ├── verdicts.json             # 逐项判定
│       ├── verify.json               # 检查点结果（未验证时不存在）
│       ├── logs/                     # 子进程原始输出，<checkpoint>.log
│       ├── report.json               # 完整报告（= 以上聚合 + overall）
│       ├── report.html               # 自包含 HTML
│       └── interrupted               # 中断时出现的标记文件（内容为时间戳）
└── cache/
    └── repos/<sha256-12>/            # git 克隆缓存（可被 --refresh 失效）
```

- 所有落盘文件为 UTF-8（`\n` 换行），即使控制台是 cp936。
- `report.json` 是权威产物；`scan/probe/verdicts/verify` 是流水线中间证据，便于单独排查。
- run 目录只增不改：重复运行产生新目录，不覆盖历史。

## 8. 数据协议

所有模型定义于 `domain/models.py`，schema 导出见 `docs/schemas/`。以下为字段级契约（节选，完整版本以 schema 为准）。

### 8.1 ScanResult（scan.json）

```jsonc
{
  "schema_version": "1.0",
  "target": {
    "kind": "local" | "git",
    "original": "https://github.com/xxx/yyy",
    "path": "C:/.../cache/repos/ab12cd34ef56",
    "commit": "3a7643e…",            // git 目标才有
    "cloned": true
  },
  "python": { "requires": ">=3.10", "source": "pyproject:project.requires-python" },
  "package_managers": ["pip", "uv"],
  "dependency_files": [
    { "kind": "requirements", "path": "requirements.txt", "index_urls": ["https://download.pytorch.org/whl/cu121"] }
  ],
  "dependencies": [
    { "name": "torch", "specifier": "==2.4.1", "extras": [], "marker": null,
      "source": "requirements.txt", "group": "default" }
  ],
  "ml_stack": [
    { "name": "torch", "role": "core", "specifier": "==2.4.1", "notes": [] },
    { "name": "xformers", "role": "extension", "specifier": null, "notes": ["与 torch 版本强绑定"] }
  ],
  "cuda": {
    "requested_runtime": "12.1",             // 由 +cu121 / index-url / nvidia-*-cu12 推断；可空
    "evidence": ["requirements.txt: --index-url https://download.pytorch.org/whl/cu121"],
    "gpu_required_strength": "hard" | "soft" | "none"
    // hard: flash-attn/xformers/deepspeed/triton/bitsandbytes/nvidia-*/+cuXXX 等强 GPU 信号
    // soft: 只有 torch/tf/jax 这类可 CPU 回退的框架
  },
  "entrypoints": ["train.py", "scripts/train.sh"],   // 猜测的入口，仅供参考
  "warnings": [ { "code": "...", "message": "..." } ]
}
```

### 8.2 MachineProfile（probe.json）

```jsonc
{
  "schema_version": "1.0",
  "os": { "name": "Windows", "release": "11", "arch": "AMD64" },
  "python": { "current": "3.11.9", "executable": "…", "console_encoding": "cp936" },
  "interpreters": [ { "version": "3.11.9", "path": "…", "source": "current|py-launcher|uv" } ],
  "tooling": { "uv": "0.12.10", "pip": "24.0", "git": "2.55.0", "conda": null },
  "gpu": {
    "present": false,
    "source": "none" | "nvidia-smi" | "nvml",
    "reason": "nvidia-smi not found",       // present=false 时必有原因
    "driver_version": null,
    "driver_max_cuda": null,                // 驱动能支持的最高 CUDA runtime
    "devices": [ { "index": 0, "name": "NVIDIA GeForce RTX 3090",
                   "vram_bytes": 25769803776, "compute_capability": "8.6" } ]
  },
  "memory_bytes": 34359738368,
  "temp_disk_free_bytes": 511000000000,
  "collected_at": "2026-09-22T21:30:00+08:00",
  "warnings": []
}
```

### 8.3 Verdict（verdicts.json 的元素）

```jsonc
{
  "id": "driver_vs_cuda",
  "aspect": "gpu",                     // python|platform|gpu|cuda|deps|ecosystem|verify
  "state": "ok" | "warn" | "blocked" | "unknown" | "not_applicable",
  "title": "驱动版本满足 CUDA 12.1 要求",
  "summary": "驱动 551.61 ≥ 最低要求 531.14（Windows）",
  "evidence": [ { "kind": "matrix" | "probe" | "scan" | "rule" | "log",
                  "detail": "CUDA 12.1 (Windows) 最低驱动 531.14",
                  "source": "data/cuda_driver_matrix.json" } ],
  "fix": { "kind": "instruction" | "command", "text": "升级 NVIDIA 驱动到 ≥ 531.14" },
  "rule_id": null,                      // 内置判定为 null，规则库命中时为规则 id
  "confidence": "high" | "medium" | "low"
}
```

### 8.4 Checkpoint（verify.json 的元素）

```jsonc
{
  "id": "install",
  "title": "安装依赖（uv pip install）",
  "state": "pass" | "fail" | "skip" | "error",
  "seconds": 84.2,
  "detail": "installed 41 packages",
  "log_path": "logs/install.log",
  "skip_reason": null
}
```

### 8.5 Report（report.json）

```jsonc
{
  "schema_version": "1.0",
  "tool": { "name": "envdoctor", "version": "0.1.0" },
  "run_id": "20260922-213000-1a2b3c4d",
  "generated_at": "2026-09-22T21:35:00+08:00",
  "overall": "runs" | "cannot_run" | "unknown",
  "overall_reason": "全部必需检查点通过；2 条注意事项",
  "verify_performed": true,
  "target": { "...": "ScanResult.target" },
  "scan": { "...": "ScanResult" },
  "probe": { "...": "MachineProfile" },
  "verdicts": [ "...": "Verdict[]" ],
  "checkpoints": [ "...": "Checkpoint[]" ],
  "explain": { "model": "qwen…", "text": "…", "error": null },
  "durations": { "scan_s": 0.4, "probe_s": 1.8, "analyze_s": 0.1, "verify_s": 120.0, "total_s": 123.0 }
}
```

## 9. 扫描器设计

### 9.1 目标解析

- 本地路径：必须存在且是目录；否则 `TARGET_NOT_FOUND`。
- git URL（`https://`、`http://`、`git@` 前缀）：clone 到 `cache/repos/<sha256(url)[:12]>/`，`--depth 1`，超时 120s；失败映射 `GIT_CLONE_FAILED` 并携带 stderr 摘要。已存在缓存且非 `--refresh` → 直接复用（记录 `cloned: false`）。
- 其他字符串形态 → 当作路径尝试；路径不成立且形态像远程地址（`github.com/owner/repo` 这类简写，会被补成 `https://`）时降级为 URL 尝试；两者都失败 → `TARGET_NOT_FOUND`（详情里说明 git 侧的错误码）。

### 9.2 依赖文件解析

| 文件 | 解析内容 | 备注 |
| --- | --- | --- |
| `requirements*.txt` | 依赖行、`-r` 递归（深度 ≤ 5，防环）、`-e`（记录不解析）、extras、markers、`--index-url` / `--extra-index-url` / `-f` / `--find-links` | 用 `packaging.requirements.Requirement` 解析规范行；解析失败的行记 warning 不中断 |
| `pyproject.toml` | PEP 621 `[project]`（dependencies / optional-dependencies / requires-python）、poetry `[tool.poetry.dependencies]`、build-system.requires | 两种风格都给 `group` 标注 |
| `setup.cfg` / `setup.py` | `install_requires`、`python_requires`（setup.py 用保守正则抽取，不执行代码） | 绝不 `exec` setup.py |
| `environment.yml` | conda 依赖（`python=X.Y`、`pytorch-cuda=12.1`、`cudatoolkit`）、`pip:` 子段（按 requirements 行解析） | 仅解析，不执行 conda |
| `Pipfile` | `[packages]` / `[dev-packages]` / `[requires] python_version` | 不解析 lock |
| `.python-version` | 版本字符串 | 优先级最低 |

**requires-python 来源优先级**：pyproject `requires-python` > setup.cfg/setup.py `python_requires` > environment.yml `python=` > Pipfile `python_version` > `.python-version`。

### 9.3 ML 栈与 CUDA 线索

- ML 栈词典（name → role）维护在 `scan/scanner.py`：core（框架：torch/tensorflow/jax/keras…）、extension（平台或编译敏感：xformers/flash-attn/bitsandbytes/deepspeed/triton…）、library（生态库：transformers/accelerate/scikit-learn…）、tooling（工具：onnxruntime-gpu/cupy/tensorrt/nvidia-*…）。import 冒烟只覆盖 core / extension / library（tooling 无稳定导入名）。
- CUDA runtime 诉求推断（按证据强度取第一个命中的）：
  1. `+cuXXX` 本地版本（如 `torch==2.4.1+cu121`）→ 12.1；
  2. pytorch index URL（`/whl/cu121`）→ 12.1；
  3. `nvidia-*-cu12` / `pytorch-cuda=12.x` / `cudatoolkit=11.8` → 对应版本；
  4. 以上都没有 → `requested_runtime: null` + evidence 说明「未显式声明，实际由安装时解析的 wheel 决定」。
- **gpu_required_strength**：出现强 GPU 信号（flash-attn / xformers / deepspeed / triton / bitsandbytes / nvidia-* / `+cuXXX`）→ `hard`；只有 torch/tf/jax → `soft`（可 CPU 回退）；都没有 → `none`。
- 入口猜测：根目录常见名（train.py / main.py / train_*.py / scripts/*.sh）+ pyproject `[project.scripts]`，仅用于报告展示，不自动执行。

## 10. 机器探查设计

### 10.1 GPU 探查（降级链，每一级都记录尝试与失败原因）

1. `nvidia-smi`（Windows 下也尝试 `C:\Windows\System32\nvidia-smi.exe`）：
   默认输出解析 `CUDA Version: X.Y`（驱动支持的最高 runtime）+ 设备表（名称/显存/驱动版本/算力，能取则取）。
2. NVML（`nvidia-ml-py`，即 `pynvml` 命名空间）：`nvmlInit` 失败即放弃，记录异常摘要。
3. 无来源成功 → `gpu = {present: false, source: "none", reason: "<第 1、2 级的失败原因>"}`。

不做的事：不把 `pytorch` 是否装过当成 GPU 证据；不把环境变量里的 `CUDA_VISIBLE_DEVICES` 当硬件证据。

### 10.2 Python 解释器发现

- 当前解释器（`sys.executable` / `sys.version_info`）→ source=current。
- Windows `py -0p` 解析 → source=py-launcher。
- `uv python list --only-installed` 解析（uv 存在时）→ source=uv。
- 按 `(major, minor)` 去重；同版本保留第一个来源（current > uv > py-launcher 优先级用于展示，验证选解释器时另有规则见 §12）。

### 10.3 工具链与资源

- `uv --version` / `python -m pip --version` / `git --version` / `conda --version`，缺哪个记 `null`（不是错误）。
- RAM：`psutil.virtual_memory().total`（psutil 缺失时降级为 null）。
- 临时盘剩余：`shutil.disk_usage(tempfile.gettempdir())`。
- 控制台编码：`sys.stdout.encoding`（cp936 是已知现实，见 §24）。

### 10.4 子进程纪律

- 所有探查子进程：超时 5s（git clone 除外，120s），`GIT_TERMINAL_PROMPT=0`。
- 输出解码：先 UTF-8，失败回退 GBK，再失败 `errors="replace"`；**任何地方都不假设 UTF-8**（LabBox 实测教训）。
- 探查失败永远不抛到顶层：转成 `warnings[]` 中的条目 + 对应字段 `null`，宁可 `unknown` 不造假。

## 11. 兼容性分析引擎

### 11.1 判定状态机

| state | 含义 | 对 overall 的影响 |
| --- | --- | --- |
| `ok` | 有证据表明满足 | 无 |
| `warn` | 可运行但需要改动 / 有已知坑 | 无（进入注意事项列表） |
| `blocked` | 有证据表明当前不可运行 | `cannot_run` |
| `unknown` | 缺少证据（如无 GPU 数据但项目需要 GPU） | 可能推高 overall 到 `unknown` |
| `not_applicable` | 该项目不涉及此项 | 无 |

### 11.2 内置判定（代码实现）

| id | 触发 | 判定逻辑 |
| --- | --- | --- |
| `python_available` | 总是 | requires-python 与已发现解释器集合求交；空集 → blocked（fix：`uv python install X.Y`） |
| `gpu_presence` | gpu_required_strength ∈ {hard, soft} | hard 且无 GPU → blocked；soft 且无 GPU → warn（「只能 CPU 运行，显存相关功能不可用」）；无 GPU 数据（probe 失败）→ unknown |
| `driver_vs_cuda` | GPU 存在 ∧ requested_runtime 有值 | 驱动支持上限（`driver_max_cuda`，矩阵反查）≥ 所需 runtime → ok；否则 blocked（fix 给最低驱动版本 + 来源） |
| `cuda_index_missing` | 存在 `+cuXXX` 本地版本但无 pytorch 官方 index | warn（PyPI 不提供 `+cuXXX` wheel；fix：`--index-url https://download.pytorch.org/whl/cuXXX`） |
| `torch_cpu_wheel_risk` | GPU 存在 ∧ 无任何 CUDA 线索 ∧ torch 存在 | warn（「未声明 CUDA 版本，解析出的 wheel 可能是 CPU 版；实测阶段会确认 `torch.version.cuda`」） |
| `conda_env_present` | environment.yml 存在 | info（warn 级）：提示验证走 pip 子集，非 conda 精确复现 |
| `package_cuda_<包名>` | 包在 `package_cuda_requirements.json` 里 ∧ 其构建钉住某个 CUDA runtime | 驱动满足 → ok；不满足且版本**被固定**在高位 → blocked；未固定（可能解析到高位）→ warn；驱动版本未知 → unknown |

> `package_cuda_*` 解决的问题：有些包自己就用特定 CUDA 版本构建（onnxruntime-gpu 1.21+ 用 CUDA 12.8、1.27+ 用 CUDA 13.0），
> 而项目文件里往往根本没提 CUDA。没有这条判定时，真实驱动要求对扫描是**不可见**的。

### 11.3 规则库（数据驱动：`compat/data/compatibility_rules.json`）

规则结构：

```jsonc
{
  "id": "tf_gpu_windows_post_210",
  "title": "TensorFlow ≥ 2.11 在 Windows 无原生 GPU 支持",
  "severity": "warning",
  "platforms": ["windows"],
  "when": { "all": [ { "kind": "lib_present", "name": "tensorflow" } ] },
  "message": "TensorFlow 2.11+ 已停止 Windows 原生 GPU 支持（最后一代 2.10）。",
  "fix": { "kind": "instruction", "text": "GPU 训练请用 WSL2，或降级 tensorflow==2.10.*" },
  "source": { "name": "TensorFlow 官方安装文档", "url": "https://www.tensorflow.org/install/pip#windows-native" },
  "confidence": "high"
}
```

条件的 kind（MVP 全集，引擎实现于 `compat/engine.py`）：
`lib_present(name)` / `lib_may_install_below(name, version)` / `lib_may_install_at_or_above(name, version)` /
`lib_pinned_below(name, version)` / `lib_requires_at_least(name, version)` /
`os(name)` / `gpu_present(bool)` / `driver_lt(version)` / `cuda_runtime_ge(version)` /
`python_available_lt(version)` / `file_present(name)`；
布尔组合：`{"all": [...]}` / `{"any": [...]}` / `{"not": {...}}`。

**两组「可能 vs 保证」的区别（写规则时最容易搞反的地方）：**

| kind | 读法 | 未固定版本时 | 例子 |
| --- | --- | --- | --- |
| `lib_may_install_below` | **可能**解析到 < X（风险成立） | True | `bitsandbytes` 未固定 → 可能装到不支持 Windows 的旧版 → 命中规则 |
| `lib_may_install_at_or_above` | **可能**解析到 ≥ X | True | `numpy` 未固定 → 可能装到 2.x |
| `lib_pinned_below` | **保证** < X | False | `torch==2.1.0` → 一定低于 2.3（NumPy 2 ABI 冲突成立） |
| `lib_requires_at_least` | **保证** ≥ X | False | `onnxruntime-gpu>=1.27` → 一定需要 CUDA 13.0 |

（后两个 `lib_may_install_*` 用 PEP 440 说明符推断「是否可能解析到某侧版本」，未固定版本时为 True；
`lib_pinned_below` / `lib_requires_at_least` 表达的是「确定在哪一侧」，未固定版本时为 False。）

MVP 规则清单（每条都有单元测试）：

| id | 要点 |
| --- | --- |
| `tf_gpu_windows_post_210` | TF ≥ 2.11 Windows 无原生 GPU |
| `jax_windows_no_gpu_wheel` | JAX 官方无 Windows GPU wheel（CPU 可用） |
| `flash_attn_windows_source_build` | flash-attn Windows 无预编译 wheel，需 CUDA toolkit + MSVC 源码构建（耗时长） |
| `bitsandbytes_windows_old` | bitsandbytes < 0.43 无 Windows 支持（0.43+ 官方支持） |
| `triton_windows_old` | triton 旧版无官方 Windows wheel；3.x 起才有 |
| `xformers_torch_pin` | xformers 与 torch 版本强绑定（同一发布列车），未固定版本时提示按 torch 选择 |
| `deepspeed_windows` | deepspeed 在 Windows 需要 triton/编译器变通，官方主要支持 Linux |
| `vllm_no_windows_support` | vLLM 官方文档明确不支持 Windows 原生运行（blocker；WSL2 是官方推荐路径） |
| `apex_windows_experimental` | NVIDIA Apex 把 Windows 标为 [Experimental]，带扩展的构建通常失败 |
| `numpy2_torch_abi_break` | NumPy 2.0 破坏 C ABI；torch < 2.3 的 wheel 按 NumPy 1.x 编译，导入即失败 |
| `onnxruntime_cudnn_major_mismatch` | onnxruntime-gpu ≥1.19 用 cuDNN 9，torch ≤2.3 用 cuDNN 8，两者不兼容 |

### 11.4 CUDA ↔ 驱动矩阵（`compat/data/cuda_driver_matrix.json`）

结构：`{"entries": [{"cuda": "12.1", "windows_min_driver": "531.14", "linux_min_driver": "530.30.02", "note": "..."}], "source_url": "NVIDIA CUDA Toolkit Release Notes"}`。
覆盖 11.8 / 12.0–12.9 / 13.0。查询两个方向：
`required_cuda → min_driver(platform)` 与 `driver_version → max_cuda`（遍历取满足的最大 runtime）。
矩阵是**数据文件**：修正/扩充不需要改代码；`source` 字段永远保留，报告里的结论会带出处。

- 数值口径：官方发布说明的 **GA 行**「Corresponding Driver Version」。
- 推导出来的行必须在 `note` 里写明推导方式（例：12.7 与 12.8 同属 R570 分支，GA 最低驱动相同；
  13.x 的次版本兼容下限为驱动 ≥ 580）。

> 诚实性说明：矩阵数值取自 NVIDIA 官方文档的最低驱动表；若与官方文档冲突，以官方为准并修正数据文件（有测试锁定格式与已核对过的数值——数值的正确性靠来源字段可审计）。

## 12. 真实验证设计

### 12.1 确认门

安装是唯一会执行第三方构建脚本的步骤，默认必须确认：

```
将要执行的真实验证计划
  解释器    Python 3.11.9  (.venv 临时环境)
  安装源    https://pypi.tuna.tsinghua.edu.cn/simple
  依赖      41 个包（来自 requirements.txt）
  临时目录  C:\Users\…\Temp\envdoctor-verify-8f3a
  步骤      venv → install → imports → device → minimal-model
⚠ 安装过程会执行第三方包的构建脚本（setup.py / build backend），且消耗网络流量。
继续？ [y/N]
```

- `--yes` → 跳过确认直接执行（CI 用）。
- 无 TTY 且无 `--yes` → **跳过验证**，控制台显式打印「非交互终端且未提供 --yes，已跳过真实验证；补上 --yes 或进入交互终端重跑」，overall=unknown。
- 确认被拒（输入 N）→ 同上，但打印「已按你的选择跳过」。

### 12.2 检查点

| id | 步骤 | pass 条件 | 失败处理 |
| --- | --- | --- | --- |
| `venv` | `uv venv --python <X.Y> <temp>` | 目录出现且解释器可执行 | fail → 终止后续（依赖链） |
| `install` | `uv pip install -r <生成的 req 文件>`（带项目 index 配置） | 退出码 0 | fail → 终止，日志尾 40 行进 detail |
| `imports` | 对每个识别到的 ML 库逐个 `python -c "import X; print(X.__version__)"` | 全部导入成功 | 单库失败 → fail（记录该库） |
| `device` | 生成脚本打印解释器/框架版本/`torch.cuda.is_available()`/设备名 | 脚本正常退出 | error |
| `minimal-model` | 生成脚本：tiny 张量前向+反向（torch）/常数矩阵乘（tf/jax），在可用设备上跑 | 正常退出 | fail |
| `smoke-cmd` | 每条 `--smoke-cmd` | 退出码 0 | fail |

- 依赖文件生成规则：优先原始 `requirements*.txt`（多文件合并 + `-r` 展开）；无 requirements 时从 pyproject/setup/conda-pip 子段生成临时 requirements.txt（写进 run 目录，注释标明来源）。
- 安装源优先级：`--index-url`（显式覆盖）> 项目声明的 index-url > 配置默认 > PyPI。
  安装前做一次安装源可达性预检（HEAD 5s）；不可达 → `install` 检查点 **skip** 并给 fix（换源/代理），不空耗超时。
- 解释器选择：满足 requires-python 的解释器集合中取最高版本；无满足者时给出选项（warn + 用 `--python` 显式指定才继续）。
- 超时：`venv` 60s、`install` `--timeout`（默认 600s）、其余步骤 120s。
- 清理：默认删除临时环境（即使失败）；`--keep-env` 保留并在报告中打印路径。**验证环境永远在系统临时目录**（不在项目目录、不在 .envdoctor 内），路径形如 `%TEMP%/envdoctor-verify-<8hex>`。

### 12.3 不改用户环境的证据

验证前后各取一次当前解释器的包清单快照（`importlib.metadata` 扫描，排序取 hash），两次 hash 必须一致；不一致 → 报告中给出 `USER_ENV_MODIFIED` 警告（正常情况下不可能发生，作为保险丝存在）。

## 13. 报告设计

### 13.1 控制台（rich）

- 顶部横幅：`✅ RUNS` / `❌ CANNOT_RUN` / `❓ UNKNOWN` + 一句话原因。
- 项目摘要（target、Python 约束、ML 栈、CUDA 诉求、GPU 需求强度）。
- 判定表：state 图标、aspect、标题、一句话结论；warn/blocked 行追加修复动作。
- 检查点表（若验证执行）：每步状态、耗时、失败时 detail。
- 注意事项 & 修复清单（聚合所有 fix）。
- 产物路径（run 目录、report.html）。
- 控制台输出永远可读降级：不支持 Unicode 的环境用 ASCII 图标。

### 13.2 report.json

按 §8.5 结构；`schema_version=1.0`。所有字符串 UTF-8。

### 13.3 report.html

- jinja2 模板（`report/templates/report.html.j2`），**完全自包含**：无外部 CSS/JS/字体/图片引用（离线可开，适合贴进 issue）。
- 内容：结论横幅、判定表、检查点表、证据链（可折叠细节）、机器画像与项目画像摘要、修复清单、生成时间与工具版本。

## 14. AI 解释层（--explain，默认关闭）

- 目的：把证据链翻译成自然语言结论 + 修复建议（「为什么是这个结论、先修哪个」）。
- 输入：报告的**脱敏摘要**——判定列表、检查点结果、机器画像关键字段。脱敏规则见 §17.3；**永远不上传**：环境变量、文件内容、完整路径（家目录被替换为 `<home>`）、用户名/主机名。
- 端点：OpenAI 兼容 `POST {base_url}/chat/completions`（配置项见 §16）。模型/端点/密钥来自配置或环境变量；缺失时 → 警告「未配置 LLM 端点，已跳过 --explain」，退出码不变。
- 输出：`explain.text` 写入 report.json + HTML 的「AI 解释」区块 + 控制台尾段；失败（超时/HTTP 错/输出异常）→ 警告，exit code 不变。
- 不做：不让 LLM 覆盖或修改任何判定状态；LLM 文字是**附加**解释，证据链始终是权威。

## 15. 技术栈

| 依赖 | 用途 |
| --- | --- |
| `typer` | CLI 框架 |
| `pydantic` v2 | 数据模型与 schema 校验 |
| `rich` | 控制台渲染 |
| `packaging` | 版本/说明符解析（Requirement、SpecifierSet） |
| `psutil` | RAM 探测 |
| `jinja2` | HTML 报告模板 |
| `httpx` | LLM 端点调用（--explain） |
| `pyyaml` | environment.yml 解析 |
| `nvidia-ml-py` | NVML 降级链第二级（无驱动时优雅失败） |

开发依赖：`pytest` / `pytest-cov` / `ruff` / `pyright`。构建后端 hatchling；Python ≥ 3.11。
**运行时零强制外部命令**：uv 用于验证环境（缺失时 verify 阶段给 blocked/warn + fix），git 仅克隆 URL 目标时需要。

## 16. 配置设计

查找顺序：`--config <path>` > `./envdoctor.toml` > `~/.config/envdoctor/config.toml`（Windows 亦读 `%APPDATA%\envdoctor\config.toml`）。

```toml
[defaults]
index_url = "https://pypi.tuna.tsinghua.edu.cn/simple"   # 可选：验证环境默认安装源
timeout_s = 600
keep_env = false

[llm]
enabled = false          # 显式关闭解释层（即使传了 --explain）；不写这一项 = 跟随命令行
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
model = "qwen3.8-flash"

[verify]
# 预留：更多验证控制项
```

环境变量覆盖（优先级最高）：`ENVDOCTOR_INDEX_URL`、`ENVDOCTOR_LLM_BASE_URL`、`ENVDOCTOR_LLM_API_KEY`、`ENVDOCTOR_LLM_MODEL`、`ENVDOCTOR_LLM_ENABLED`。
密钥只从环境变量读取，**不写入任何落盘文件**；`--explain` 日志里绝不出现密钥。
配置文件里的未知键会被忽略（旧配置不会让工具报错）。

## 17. 安全与隐私

### 17.1 执行第三方代码的风险（明确披露）

`verify` 阶段会真实安装依赖，这会执行第三方包的构建脚本（setup.py / PEP 517 backend），等价于「在这台机器上运行该项目的代码」。缓解措施：

- 默认交互确认 + 明确风险文案；`--yes` 是显式知情选择。
- 隔离等级：一次性 venv（**不是**容器/沙箱；venv 不提供安全隔离，只提供干净与可清理）。文档与确认文案都如实说明这一点。
- `--no-verify` 提供零执行路径（纯静态分析）。
- 不在验证环境里执行项目的训练脚本；只跑 envdoctor 自己生成的冒烟脚本 + 用户显式提供的 `--smoke-cmd`。

### 17.2 克隆安全

`git clone --depth 1`（只取默认分支最新提交），`GIT_TERMINAL_PROMPT=0`（绝不弹凭据输入），不执行仓库内任何脚本或 git hook（clone 不触发服务器端之外的东西；不做 checkout 后处理）。

### 17.3 脱敏清单（--explain 与日志）

| 内容 | 处理 |
| --- | --- |
| 家目录绝对路径 | 替换为 `<home>` |
| 用户名 / 主机名 | 替换为 `<user>` / `<host>` |
| 环境变量 | 永不读取、永不上传 |
| `*_KEY` / `*_TOKEN` / `*_SECRET` 形态的任何值 | 不进入报告；日志中不可能出现（不记录） |
| LLM 密钥 | 只驻留内存，不入日志/报告 |

### 17.4 无遥测

envdoctor 自身不发起任何非必要网络请求：仅在（a）clone 目标、（b）安装源预检、（c）真实安装、（d）`--explain` 时联网，且（b）（c）（d）在报告里可追溯。

## 18. 性能要求

| 操作 | 目标（Windows 本机） | 说明 |
| --- | --- | --- |
| `probe` | ≤ 2.5s | 3~4 个子进程（nvidia-smi 缺失时立即返回） |
| `scan`（≤500 文件的仓库） | ≤ 1.0s | 只读元数据文件，不遍历全部源码 |
| `analyze` | ≤ 0.5s | 纯内存计算 |
| 报告生成 | ≤ 0.5s | |
| `doctor`（含验证） | 由安装耗时主导 | 安装前有预检，源不可达不空等 |

`scripts/bench_overhead.py` 输出实测值并与上表对照。

## 19. 测试策略

| 层级 | 内容 | 运行条件 |
| --- | --- | --- |
| unit | 解析器（requirements 变体/pyproject 双风格/conda/setup.cfg）、ML 栈检测、CUDA 线索推断、nvidia-smi 样本解析（Win/Linux）、`py -0p` 解析、uv list 解析、矩阵双向查询、**每条规则**、引擎条件 DSL（含「可能 vs 保证」两组语义）、脱敏、配置加载（文件/环境变量/非法配置）、explain（httpx MockTransport）、报告 schema、退出码映射 | 永远 |
| integration | 对 5 个 fixture 项目跑 `analyze`/`doctor --no-verify`（FakeProbe 注入，无网络）：`torch-cu121`、`tf-legacy`、`conda-pip`、`clean-uv`、`broken-reqs`；CLI 子进程端到端（dev shim）；内部错误 → 退出码 3；uv 缺失时 `verify_performed` 不得为真 | 永远 |
| network | 真实验证一个微型依赖（真实 uv venv + 清华镜像安装 + import 冒烟 + 最小模型 CPU） | `-m network`（本机可跑） |
| gpu | 真卡：nvidia-smi 解析、驱动矩阵对比、torch CUDA 最小模型 | `-m gpu`（3090 机器） |

fixture 项目位于 `tests/fixtures/projects/`，是最小但真实的项目形态（不 mock 文件系统）。
知识库相关的测试集中在 `tests/unit/test_knowledge_base.py`（规则、矩阵数值、包级 CUDA 需求），
配置相关的测试在 `tests/unit/test_config.py`。

## 20. 可观测性与日志

- 控制台：默认正常信息；`-v` 增加子进程命令与耗时；`-q` 只留结论与错误。
- 文件：run 目录 `logs/<checkpoint>.log` 保存子进程**原始输出字节**（写入前尝试 UTF-8→GBK→replace 解码，原文保留在文件中供排查）。
- 失败诊断：`install` 失败时 detail 带日志尾 40 行；`git clone` 失败带 stderr 摘要。
- 所有对外错误带稳定 code（`errors.py`），终端/JSON/测试匹配 code 而非文案。

## 21. 开发里程碑

| 里程碑 | 内容 | 完成标志 |
| --- | --- | --- |
| M1 | domain 模型 + scan（含目标解析/克隆） | fixture 项目全部扫出预期画像 |
| M2 | probe（nvidia-smi/NVML/解释器/工具链） | 本机 probe 输出完整画像且无 GPU 时如实降级 |
| M3 | compat 引擎 + 知识库 | 所有内置判定与规则有测试，矩阵双向查询正确 |
| M4 | verify（venv/install/冒烟/最小模型/清理） | network 测试真装真跑通过 |
| M5 | report（console/JSON/HTML）+ explain | 报告三形态齐全，explain 有 MockTransport 测试 |
| M6 | CLI 接线 + 闸门 + 文档 + CI | 全部质量闸门绿，verify_mvp 全 PASS |

## 22. MVP 完成定义（可执行标准）

以下 12 条全部有可执行证据（脚本输出 / 测试），才算 MVP 完成：

1. `probe` 在无 GPU 机器输出完整机器画像，`gpu.present=false` 且带明确 reason；缺 nvidia-smi 不导致非零退出。
2. 5 个 fixture 项目的 `analyze` 均产出 verdicts，字段齐全且通过 schema 校验（`scripts/verify_mvp.py`）。
3. 无 GPU 机器 + `torch-cu121` fixture → 至少一条 `blocked` 且 overall=`cannot_run`；另用合成的「旧驱动」场景验证 `driver_vs_cuda` 的 fix 含矩阵来源的最低驱动版本。
4. `doctor <fixture> --no-verify` 全流程落盘 scan/probe/verdicts/report.json/report.html。
5. 静态-only 时 overall 不为 `runs`（诚实性：只给 `unknown` + 说明需实测）。
6. 真实验证（network）：临时 venv 真装依赖 → import 冒烟 → 最小模型，checkpoints 全 pass，退出码 0，临时环境随后被清理。
7. 确认门行为：无 TTY 且无 `--yes` → 跳过 verify 且显式提示（测试断言退出码 unknown、checkpoints 为空）；`--yes` → 不再交互。
8. 用户环境不变：验证前后当前解释器包清单 hash 一致（证据进报告）。
9. 中断安全：doctor 中断 → 临时环境被清理、退出码 130、run 目录有 `interrupted` 标记。
10. `--explain` 未配置端点 → 警告且不影响退出码（MockTransport 测试）。
11. HTML 报告自包含（扫描产物中无 http(s) 外部资源引用）。
12. 质量闸门：`ruff` + `pyright`(src) + `pytest -m "not gpu"` 全绿；`scripts/verify_mvp.py` 输出逐条 PASS/SKIP（SKIP 必须带原因）。
13. 知识库可审计：`compat/data/*.json` 里每条规则都带官方来源链接与修复动作，每条都有单元测试；驱动矩阵的数值与官方表一致。

## 23. 已定稿决策与剩余待确认问题

### 23.1 已定稿（2026-09-22）

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| 命名 | 文件夹 `AI Environment Doctor`，包/命令 `envdoctor` | 用户选定；语义与「环境医生」贴合，撞名风险低 |
| 验证默认策略 | 先确认再装跑（`--yes` 跳过 / `--no-verify` 纯静态） | 用户选定；安装会执行第三方构建脚本，知情同意是底线 |
| AI 解释层 | 可选 `--explain`，默认关闭 | 用户选定；核心价值在确定性证据链，LLM 是附加层 |
| 目标平台优先级 | Windows + NVIDIA + PyTorch 做到极致（3090 是 Windows） | 用户确认 3090 系统为 Windows |
| 验证后端 | uv（不做 conda 执行） | 本机有 uv 无 conda；uv 创建临时环境快且干净 |
| 临时环境位置 | 系统临时目录，永不在项目内 | 保证「用户的机器神圣不可侵犯」 |
| 静态-only 不报 runs | 是（红线） | 诚实性；「没实测就不知道」 |

### 23.2 剩余待确认（不阻塞）

- 3090 机器上的 `pytest -m gpu` 实测（本机无 GPU，只能给命令）。
- Linux 侧行为（代码跨平台，但本机无 WSL/Docker，CI 补位）。
- 仓库地址与远端（`git remote add origin …`）由所有者决定——发布动作从来由用户拍板。

## 24. 开发约束（本机实测教训，来自 LabBox 时期）

1. **CJK + 空格路径**：本项目位于 `C:\code\github小项目\AI Environment Doctor`，editable install 的 `.pth` 会以 locale 编码解码而变成乱码 → 一律**从源码运行**（`scripts/envdoctor_dev.py` / pytest `pythonpath=src`），不做 editable 安装。
2. **本机无 GPU / NVML / nvidia-smi**：GPU 路径只能靠 fake 与样本输出测试；真卡验证交给 3090（`-m gpu`）。
3. **控制台 cp936**：CLI 入口 `reconfigure(encoding="utf-8", errors="replace")`；子进程输出解码永不假设 UTF-8。
4. **网络**：开发期依赖安装建议走镜像（`UV_DEFAULT_INDEX=<mirror>`，国内更稳）；安装源可达性预检正是为了在网络受限时给出清晰指引而不是空等超时。
5. **无 WSL / Docker**：Linux 结论只能在 CI 或用户机器上得出，不能谎称已验证。
6. **`uv` 是真实验证的硬依赖**：`verify` 阶段靠 `uv venv` / `uv pip install` 建一次性环境。缺 `uv` 时 envdoctor 明确跳过验证并把 `verify_performed` 记为 false（不谎报跑过），`overall=unknown`。

## 25. 实现状态（MVP 完成定义逐条核验）

> 回填于 2026-10-20。验收脚本：`python scripts/verify_mvp.py`（`--skip-tests` 可在调用方已跑过 pytest 时使用）。

### 25.1 逐条核验（§22）

本机（Windows 11、无 NVIDIA 工具、使用清华镜像、已安装 uv 0.13.1）实测：**13 PASS / 0 SKIP / 0 FAIL**。

| # | 标准 | 结果 | 证据摘要 |
| --- | --- | --- | --- |
| 22-1 | probe 无 GPU 降级 | PASS | `gpu.present=false` 且带完整 reason（nvidia-smi 未找到 + NVML LibraryNotFound），exit 0 |
| 22-2 | 5 个 fixture 判定字段齐全 | PASS | torch-cu121=3、tf-legacy=2、conda-pip=4、clean-uv=1、broken-reqs=3，字段/证据完整 |
| 22-3 | blocked → cannot_run + 矩阵来源 | PASS | 本机 `gpu_presence=blocked → cannot_run`；合成旧驱动 528.49 场景 `driver_vs_cuda=blocked`，fix 含 531.14，证据指向 `cuda_driver_matrix.json` |
| 22-4 | 全流程产物落盘 | PASS | `scan.json / probe.json / verdicts.json / report.json / report.html` 五类齐全 |
| 22-5 | 静态-only 不报 runs | PASS | `--no-verify` → `overall=unknown` |
| 22-6 | 真实验证（network） | PASS | CLI + `--yes`：venv/install/imports/user-env-unchanged 全 pass，overall=runs，临时环境已清理 |
| 22-7 | 确认门行为 | PASS | 非交互终端且无 `--yes` → 显式跳过验证（exit=2，原因写明） |
| 22-8 | 用户环境不变 | PASS | 验证前后解释器包集合指纹一致（写入报告） |
| 22-9 | 中断安全 | PASS | 合成 KeyboardInterrupt → 退出码 130 + run 目录 `interrupted` 标记（真实控制台 Ctrl+C 建议人工复核一次，见 25.3） |
| 22-10 | `--explain` 降级 | PASS | 未配置端点 → 明确警告「AI 解释已跳过」，退出码不变 |
| 22-11 | HTML 自包含 | PASS | 产物中无外部 `src=`/`href=` 引用 |
| 22-12 | 质量闸门 | PASS | ruff + ruff format + pyright(0 errors) + pytest 全绿 |
| 22-13 | 知识库可审计 | PASS | 11 条规则全部带官方来源与修复动作；矩阵覆盖 12 个 CUDA 版本（11.8 ~ 13.0） |

测试规模：146 项常规测试 + 1 项 network 真装真跑（`-m network`）；其中 Web 控制台 14 项
（路由、令牌、回环 Host、同源、知情同意、真实 HTTP 往返）；无 GPU 机器上 `-m gpu` 用例按 marker 跳过。

### 25.2 性能实测（§18 目标对照）

`python scripts/bench_overhead.py`（取 3 次最小值）：

| 操作 | 实测 | 目标 |
| --- | --- | --- |
| probe | 0.370s | ≤ 2.5s |
| scan（fixture / 本仓库） | 0.002s | ≤ 1.0s |
| analyze | < 0.001s | ≤ 0.5s |
| report（JSON+HTML） | 0.011s | ≤ 0.5s |

### 25.3 本机无法执行 / 需要外部条件的事项

1. **GPU 真机路径**：`pytest -m gpu` 与真实驱动的 `nvidia-smi` 解析需要 3090 Windows 机器；
   GPU 相关逻辑在本机由 fake 与样本输出覆盖（`tests/fixtures/smi/`）。
2. **Linux 腿**：本机无 WSL/Docker，Linux 行为只能由 CI（ubuntu-latest）覆盖。
3. **真实控制台 Ctrl+C**：合成中断已覆盖清理与退出码；LabBox 的经验是真实终端验证能挖出合成测试挖不到的 bug，建议在真机终端人工触发一次（`doctor` 运行中按一次 Ctrl+C）。
4. **发布动作**：仓库地址、远端与首次推送由所有者执行（`git remote add origin …` + `git push`）。

### 25.4 与规格书的已知偏差（诚实性记录）

- `version` 命令额外提供（§4 未列出，无破坏性）。
- 安装源优先级以 `--index-url` 覆盖项目配置（§4 与 §12.2 已按此对齐）。
- import 冒烟跳过 tooling 角色依赖（无稳定导入名），以及导入代价过高/依赖 GPU 初始化的包
  （vLLM、SGLang、megatron-core 等），两类都会在计划 warnings 里点名。
- §4 的 `--config` 选项在实现中补齐（`doctor` 子命令）。
- §9.1 的「路径失败再降级 URL」在实现中补齐（`github.com/owner/repo` 这类简写可直接用）。

## 26. Web 控制台（`envdoctor web`）

CLI 之外的第二个前端：**同一套流水线，浏览器界面**。目标用户是「不想在终端里来回翻输出」的人，
以及需要把诊断结果贴给同事的场景（报告本来就是自包含 HTML）。

### 26.1 设计约束

| 约束 | 理由 |
| --- | --- |
| **零新增运行时依赖**（只用标准库 `http.server`） | envdoctor 的卖点是「哪里都能装」；为一个本地界面引入 Web 框架不划算 |
| 单文件自包含前端（内联 CSS/JS，无 CDN） | 与 `report.html` 同一原则：离线可用、无外部请求、无遥测 |
| 复用 CLI 的产物协议 | Web 与 CLI 共享 `.envdoctor/runs/`，报告、证据、日志只有一份真相 |
| 默认只监听 `127.0.0.1` + 一次性令牌 | 这个进程有能力安装第三方代码，必须假设「本机也可能有恶意网页」 |

### 26.2 架构

```
src/envdoctor/web/
├── app.py       路由与业务逻辑（与传输层解耦，可脱网单测）
├── jobs.py      后台任务（线程 + 状态机：queued/running/done/failed）
├── server.py    HTTP 层（BaseHTTPRequestHandler）+ 安全校验
└── assets/index.html   单页前端（内联样式与脚本）
```

- **任务模型**：`analyze`（scan → probe → analyze → 出报告）与 `verify`（装 → 冒烟 → 出报告）。
  长任务在后台线程跑，浏览器轮询 `/api/jobs/<id>` 看状态与日志；`verify` 阶段还会实时读
  run 目录下的 `logs/*.log` 尾部，让「正在安装」可见。
- **API**（全部要求令牌）：

  | 方法 | 路径 | 作用 |
  | --- | --- | --- |
  | GET | `/` | 单页界面（令牌注入页面） |
  | GET | `/api/machine` | 机器画像（含 cwd 与工作目录） |
  | GET | `/api/runs` | 历史运行列表 |
  | GET | `/api/runs/<id>` | 该次运行的 `report.json` |
  | GET | `/api/runs/<id>/report.html` | 自包含 HTML 报告 |
  | POST | `/api/jobs` | 启动任务：`{kind: analyze\|verify, target\|run_id, consent}` |
  | GET | `/api/jobs/<id>` | 任务状态、阶段、日志、结果 |
  | DELETE | `/api/runs/<id>` | 删除一次运行 |

### 26.3 安全模型

1. **默认回环监听**：`--host 127.0.0.1`；`--allow-remote` 才允许对外（启动时明确告警）。
2. **一次性令牌**：每次启动生成，页面内注入；无令牌一律 403。
3. **Host 头校验**：必须是回环地址 —— 阻断 DNS rebinding（恶意网页把自己的域名解析到 127.0.0.1 再来打本机）。
4. **同源校验**：写操作（POST/DELETE）的 `Origin` 必须与 `Host` 同源。
5. **知情同意**：真实验证必须带 `consent: true`，与 CLI 的确认门同一标准；界面上的复选框写清了风险。

> 历史事故：早期实现把 `Origin: http://127.0.0.1:8766` 的 scheme 当成主机名，导致**同源** POST 被误判为跨源而拒绝。
> 回归测试 `test_http_accepts_same_origin_post` 与 `test_hostname_strips_scheme_and_port` 锁住这个行为。

## 27. 实现状态（Web 控制台）

| 项 | 状态 |
| --- | --- |
| `envdoctor web` 启动、令牌、自动开浏览器 | ✅ |
| 扫描/判定、证据链、修复建议展示 | ✅ |
| 真实验证（勾选同意后）：venv→install→imports→device→minimal-model | ✅ 实测 `overall=runs` |
| 实时日志（读 run 目录 `logs/*.log` 尾部） | ✅ |
| 历史运行浏览与删除 | ✅ |
| 安全：令牌 / 回环 Host / 同源 / 显式同意 | ✅ 有单测 |
| 打包：wheel 与 sdist 都带 `web/assets/index.html` | ✅ 已验证安装后可服务 |
| 未做 | 多用户/鉴权/持久化任务队列（本地单人工具，刻意不做） |
