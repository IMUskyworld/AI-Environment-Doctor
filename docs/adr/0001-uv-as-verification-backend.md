# ADR 0001：验证后端选 uv，不做 conda 执行

- 状态：已采纳（2026-09-22）
- 背景：真实验证需要「快速创建一次性环境 + 安装依赖」。候选是 pip/venv、conda、uv。
- 决策：验证路径统一使用 `uv venv` + `uv pip install`；`environment.yml` 只解析不执行。
- 理由：
  - 本机（Windows 开发机）有 uv 无 conda；uv 创建环境亚秒级，pip/venv 需数十秒；
  - conda 的求解与 channel 语义复杂，正确复现 conda 环境是另一个产品；
  - `environment.yml` 的 conda 依赖（如 `pytorch-cuda`）不是 pip 包，纳入安装列表会直接失败——解析后跳过并给出提示，是诚实且可用的折中。
- 代价：conda-only 项目拿不到「精确复现」结论，报告里以 `conda_env_present` 判定显式说明。
- 触发复议的条件：出现大量 conda-only 用户需求，或 uv 缺失环境需要降级路径。
