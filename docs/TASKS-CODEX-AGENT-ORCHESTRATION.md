# 任务书：Codex Agent 编排出片链路

> 架构更新（2026-11-19）：本文件保留为历史任务合同。当前实现已改为单 Codex：当前对话生成创意内容，后台使用确定性执行器，不再启动独立 Production Codex 子进程。以 `docs/ARCHITECTURE.md` 为准。

- 状态：已锁定
- 锁定时间：2026-11-19 10:35 Asia/Shanghai
- 基线提交：`725ecc9`（`chore: remove backend test suite`）
- 工作区：`/workspace/video-fission`
- 前端开发地址：`http://127.0.0.1:5174/`（以 `frontend/package.json` 为准；README 里的 5173 是过时表述，本轮只改正文案，不改端口）
- 本任务书是本轮实施与交叉验证的合同。合同变更须先改本文件并写 `status/STATUS.md`，再改代码。

## 1. 背景与目标

当前出片是 Python `PipelineEngine` 按固定阶段推进：`ingest → decompose → intent → script → generate → compose`。Codex CLI 已用于对话工作台，以及管线内部的 `VideoScriptAgent`（只做意图和三脚本）。Gemini 理解、Seedance 生成、FFmpeg 合成都封装在管线模块里；对话侧只有粗粒度工具 `start_video_production` / `run_video_production` / `get_video_production`，等于把整条 Python 管线当黑盒启动。

本轮目标：把**整条任务执行流程**交给 Codex CLI Agent 负责，包括用户原始意图理解、任务规划、技能加载、技能与工具调用、结果输出。Gemini 视频理解、Seedance 视频生成、FFmpeg 合成，以及切分/开场、落地页、参考检索、音频分离、字幕硬烧等能力，都作为 Skill 或 Tool 挂载给该 Agent。

## 2. 架构决策（二选一后锁定）

方案 A：把出片编排直接塞进用户正在用的 Codex 对话线程。否决。长耗时工具、脚本选择暂停、追问恢复会和普通对话搅在一起，也容易破坏现有对话工作台。

方案 B（采用）：**保留现有 Codex 对话工作台**。出片由一个**任务级 Production Agent**（基于现有 `VideoScriptAgent` / `CodexAppServer` 扩展，不要再套一层 Agent）负责编排。Python 管线能力拆成可单独调用的 Tool；现有 `video-references`、`creative-intent-script-skeleton`、`universal-ugc-video-script-generation` 等 Skill 由 Production Agent 自行加载。对话工作台仍可通过 `start_video_production` / `run_video_production` 启动或恢复任务，但这两个入口必须启动 Production Agent，而不是再调用 `PipelineEngine.run` 的阶段循环。

一条出片链路只允许一个编排者。落地后不得再保留 Python 阶段机作为静默后备。Codex 不可用时，任务必须给出明确失败，不得假装已经出片。

禁止 Production Agent 再去拉起 `VideoScriptAgent` 或第二个 Codex 运行时（禁止套娃）。意图和脚本改由同一个 Production Agent 加载现有 Skill 后产出。

## 3. 范围

### 要做

1. 把现有管线能力拆成 Fine-grained Tool（名称可微调，语义必须稳定、可单测），至少覆盖：
   - 参考视频理解 / 切分与开场（Gemini + 现有 understand / opening，缺 Key 时结构化失败或降级说明，不得伪造成模型结果）
   - 落地页卖点提取（已有则复用）
   - 参考脚本检索（`video-references`）
   - 时间线与开场连续块锁定
   - 创意意图与三脚本候选（Agent 加载现有 Skill 后写入 production）
   - 查询/更新任务状态、写入进度与产物路径
   - 单镜/多镜视频生成（Seedance / OpenAI Compatible / Generic HTTP；不得恢复本地关键帧伪生成）
   - 音频分离、字幕硬烧、FFmpeg 时间线合成
   - 在同一 production 上处理追问（澄清 / 局部 patch / 恢复），不得一说「再改一下」就从 ingest 重来
2. 实现 Production Agent 循环：理解用户意图 → 制定计划 → 加载所需 Skill → 按计划调用 Tool → 在暂停点等待用户 → 输出结果并更新卡片。
3. HTTP 与对话入口保持兼容、只做加法：`POST` 创建/运行/选择脚本、追问恢复等现有路由仍可用；内部改为走 Agent 编排。
4. 前端用现有进度卡、计划/工具轨迹、消息时间线展示 Agent 计划与 Tool/Skill 调用，不恢复旧的 CapabilityPanel 或顶栏溢出菜单。能靠现有卡片和事件表达就不要新增卡片类型。
5. 补本轮聚焦的自动化测试，并更新 README；补写 README 已引用但仓库中缺失的 `docs/ARCHITECTURE.md`（只写本轮后的真实结构，不编造未实现合同）。
6. 交叉验证：独立复跑新测试，对照本任务书第 4–5 节核对行为，给出命令、结果和代码位置。

### 不做

- 不以真实 Gemini / Seedance / 内网模型做端到端或 5174 完整链路验证。本环境连不上这些模型。
- 不配置、不索要、不写入真实 API Key。
- 不启动本机 Mac 服务，不改本机 `.env`，未经要求不 git commit / push，不写回本机。
- 不重写 Codex 对话工作台（会话、steer、审批、压缩、分叉等保持可用）。
- 不改认证 / SSO / 用户隔离模型。
- 不把官方 Playwright e2e 指到正在给人看的 5174。
- 不整包恢复用户已删除的历史 `backend/tests` 测试套件；本轮只新增编排与 Tool 所需测试。若必须引用旧夹具，只最小量补回，并说明原因。
- 不恢复旧 UI chrome。不把 OpenMotion Studio / HTML 时间线 / Motion.md 搬进来。
- 不扩大到工作台二创或无关重构。

## 4. 必须遵守的产品不变量

这些不变量写进 Tool 实现和编排 Skill，不能只写在提示词里。

- 开场 `[0, opening_end)` 必须是一个连续的 `shot_opening` 块，不得再发明 3000ms hook 切点。
- 脚本阶段产出恰好 3 个候选，并在 `awaiting_script_choice` 暂停；对话自动开跑路径不得自动选定脚本，也不得为了「跑通」丢掉或替换配音。
- `generate` 必须已有 `selected_script_id`，不得把尚未选定的 `production.script.shots` 当成已选。
- 生成镜头只允许 Seedance / OpenAI Compatible / Generic HTTP，不得用本地关键帧伪生成冒充模型出片。
- 无 Key、工具失败、占位或未真正调用视频模型时，不得出现「已还原」「已按参考对齐」「已还原参考」或等同表述，也不得把本地/降级结果展示成已确认的模型结果。
- 同一任务的追问留在同一 `production_id`。
- 用户检查点保留：脚本三选一、需要澄清时暂停；`select-script` 不得在未选定脚本时偷偷开始生成。

## 5. 验收标准（开发完成，不是正式出片验收）

本轮验收 = 开发完成 + 单测 + 交叉验证。**不是** Gemini/Seedance 真出片验收，也**不是** 5174 完整链路验收。

通过条件：

1. `ProductionService.run` / 对话工具 `start_video_production` / `run_video_production` / 选择脚本恢复，走的是 Production Agent，而不是 `PipelineEngine` 的固定阶段循环。可用代码引用和桩测试证明。
2. 存在可注入的 Agent 运行时桩（Fake Codex / 脚本化 tool-call 计划）。单测必须能不启动真实 `codex`、不访问 Gemini/Seedance，就验证：
   - Agent 会先规划再按需加载 Skill、调用 Tool；
   - 无脚本选定时不会进入 generate；
   - 开场连续块被 Tool 锁定；
   - Codex 不可用时任务失败且错误明确；
   - 无视频模型 Key 时生成 Tool 失败或明确降级，且不出现第 4 节禁止文案；
   - 追问留在同一 production。
3. 各 Tool 可被直接单测（provider 用桩）。FFmpeg 相关测试可用仓库已有或最小夹具，不依赖内网模型。
4. 前端若改了计划/工具轨迹展示，`npm test --prefix frontend` 要通过。
5. Claude 交叉验证必须给出独立复跑的命令和结果，并对照第 4–5 节指出通过或缺陷；不能只写「看起来没问题」。
6. README 与 `docs/ARCHITECTURE.md` 与实现一致。`status/STATUS.md` 记录进展和收口结论。

明确不在本轮验收内：真实 Gemini 理解、真实 Seedance 出片、5174 手工完整走查、正式视觉还原验收。

## 6. 任务拆分与分工

### Codex Bot（后端实施）

1. 把 `pipeline.py` 中可复用的能力抽成 Tool，挂到现有 `ToolRegistry`；收紧 `run_video_production` 语义为「交给 Production Agent 执行/恢复」，不再在 Tool 里直接跑阶段循环。
2. 实现 Production Agent（扩展 `script_agent.py` 或同等模块），复用 `CodexAppServer`、Skill `extraRoots`、动态 Tool 注册与取消/审批事件。
3. 新增编排 Skill（例如 `production-orchestration`），写清计划顺序、暂停点和第 4 节不变量。
4. 把 `ProductionService.run` / `select_script` / 追问恢复接到 Agent。
5. 增加本轮后端单测，更新 README 与 `ARCHITECTURE.md`。
6. 按交叉验证缺陷修复，并复跑相关测试。

### Claude Bot（前端 + 交叉验证）

1. 若生产任务/对话需要展示 Agent 计划与 Tool/Skill 轨迹，在现有卡片和事件体系上补齐，不恢复旧 chrome，不无故新增卡片类型。
2. 相关前端单测要绿。
3. 后端有可跑测试后，独立交叉验证：对照本任务书复跑测试、抽查关键路径代码，把证据（命令、结果、文件位置）写到群里。发现缺陷先报告再等修复，再复验。

### Grok Bot

盯盘、合同解释与收口。Grok 不写本轮功能代码，不代替交叉验证。合同变更由 Grok 先改任务书和 STATUS。

建议顺序：先 Tool 化并挂上桩测试 → 再接 Production Agent 编排 → 再改前端展示 → 交叉验证与修复。不要先改 UI 再改编排。

## 7. 运行与协作约束

- 只在 `/workspace/video-fission` 开发，不要改本机仓库，不要启动本机 8000/5174/5173。
- 工作区 5174 如需查看页面可以启动；不要动可能占用中的 URRE 5173。
- 不要为了本轮验收去跑真实视频生成模型，也不要配置 Key。
- 需要验证「无 Key / 无 Codex」行为时，用桩和显式缺配置，不要写假成功。
- 提交由用户决定，本轮不要自行 git commit / push。
- 进度写入 `status/STATUS.md`。交叉验证必须有证据。

