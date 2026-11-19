# Video Fission

Video Fission 是一个从 `hot-product-system` 智能中枢能力中抽离出的独立项目。它保留 Codex 对话、持久化、Skill 和 Tool，并参考原项目加入本地账号、JWT 会话与企业 CAS 单点登录。项目不依赖 MongoDB、Redis 或原业务前端。

## 已实现

- Codex CLI `app-server --stdio` JSON-RPC 对接
- 每会话独立持久化 Codex Thread，支持断线后恢复
- 新建、重命名、归档、取消归档、删除、分叉、上下文压缩、历史同步
- SSE 实时事件流，持久化 Agent 回复、推理、计划、命令、文件变更和工具调用轨迹
- 持续追问（steer）、停止执行、审批和用户澄清
- 模型与推理强度选择
- 用户名密码登录、HttpOnly Cookie/Bearer JWT、退出登录和登录失败限流
- 企业 CAS 单点登录，并将企业账号同步为本地用户
- 用户会话、模型设置、工作目录、附件、创作任务和产物按用户隔离；Skill / Tool 保持全局
- 文件上传和附件上下文
- SQLite 数据存储，默认启用 WAL
- 项目内 Skill 目录、图形化创建/编辑/启停/删除
- 参考视频意图、UGC 脚本生成和 13 篇吹风机/除吹雪机参考脚本共享技能链
- 受控动态 Tool 注册、执行与运行记录
- 响应式 Vue 3 对话工作台
- 单 Codex 编排出片：当前对话负责生成创意意图、骨架和三份脚本，Python 后台自动生成并合成三条候选视频，用户选定一条后执行最终合成；不再启动额外的 Production Codex 子进程，也不走 `PipelineEngine` 固定阶段循环
- 独立原片重排快捷入口：保留参考视频开头和结尾，只重排中间 value 片段并直接拼接原片；单任务单成片，不使用视频生成模型，也不进入候选视频选择
- decompose 增强：可选 TransNetV2 + PySceneDetect 转场检测、整片理解与深度观察；Gemini 每次整片理解响应单独落盘。生产拆解仍要求真实 Gemini；出片 Tool 在无 Key 时可以做诚实的本地切分，但必须标明这是降级结果，不能写成模型理解
- 模型设置持久化到 SQLite；API Key 只写不回显；`gemini_gateway` 的 `token_url` 可明文回读
- 后端单元测试、真实 Codex 集成测试和 Playwright 浏览器 E2E

## 系统要求

- Python 3.11+
- Node.js 20+
- ffmpeg / ffprobe（本机已验证 7.1.x）
- 已安装并登录官方 Codex CLI（仅对话工作台和创意内容生成需要；后台出片执行不启动第二个 Codex 进程；单测使用可注入的 Fake Planner）
- 当前已验证版本：`codex-cli 0.169.0`

```bash
codex --version
codex login
```

## 启动

```bash
uv sync --extra dev
npm install --prefix frontend
./scripts/dev.sh
```

开发服务器监听 `0.0.0.0:5174`，本机可打开 `http://127.0.0.1:5174/`，
局域网设备可通过 `http://<本机局域网IP>:5174/` 访问。
使用局域网 IP 时，还需要将该前端 Origin 加入 `VF_ALLOWED_ORIGINS`，例如
`VF_ALLOWED_ORIGINS=http://192.168.1.59:5174`；启用企业 SSO 时同步设置
`VF_SSO_FRONTEND_URL=http://192.168.1.59:5174`。

工作台可在「对话 / 系统设置」之间切换，原有 Codex 对话能力保持不变。

运行日志默认同时写入终端和 `status/video-fission.log`，单文件 20MB、保留 10 份。显式配置 `VF_LOG_FILE` 会覆盖测试进程继承的环境判定，避免服务进程误关文件日志；`GET /api/health` 的 `logging` 字段只报告是否启用和日志文件名，不暴露绝对路径。日志带 `request_id` / `production_id` / `stage` 等关联字段，覆盖 API 请求、外部模型与视频 Provider、Codex RPC、转场/切片、深度观察、镜头生成和 FFmpeg 合成。用 `VF_LOG_LEVEL=DEBUG` 可查看更细的媒体探测和 FFmpeg 命令；提示词默认只记录长度与摘要，调试时可临时设置 `VF_LOG_PROMPTS=1` 输出截断预览。

### 视频创作

在对话中上传参考视频（最大 500MB）并填写创作说明后发送。当前对话 Codex 先调用 `prepare_video_production`，由后台完成接入、拆解、落地页提取和参考脚本检索，并把完整 `creative_context` 返回给当前对话；Codex 再基于这些真实结果生成 `intent`、`skeleton` 和恰好 3 个 `script_candidates`，通过 `run_video_production` 提交，设置 `resume_from=script`。后台只运行细粒度 Tool，不再创建第二个 Codex 进程。时间线会展示理解、规划、片段和成片卡片。生产模式下整片视频理解、镜头拆分和深度观察必须使用真实 `gemini_gateway` 结果，默认模型为 `gemini-3.8-flash`；无 Key 时理解 Tool 可以诚实降级为本地切分，并明确标注不是模型结果。深度观察只保留画面主体、动作、场景道具、机位、字幕文字、信息呈现、口播、声音、序列作用、主张与不确定性，不再输出未被链路消费的复查、时间原点和证据覆盖元数据。生成镜头必须配置真实视频模型通道，完成后可播放并下载 `final.mp4`。音频分离结果会作为参考 BGM 以低音量混入最终音轨；合成之后可以用 `burn_subtitles` Tool 写 SRT 并硬烧字幕；未对齐时使用安全默认位置，不会写成「已按参考对齐」。

完整片段时间轴与摘要会落到用户工作区的 `productions/{production_id}/creative-context.json` 和 `timeline-summary.md`，供当前对话直接读取，不需要查询数据库。Codex app-server 的只读查询和执行审批由后端自动允许，前端不再展示人工审批卡。

「系统设置」可配置多模态（`local_ffmpeg` / `openai_compatible` / `gemini_gateway`）、视频生成模型，以及理解与切片开关（`transition_detection` / `observe` / `clip_workers` / `gemini_max_size_mb` / `observe_timeout_seconds`，Gemini 请求与观察卡超时默认 360 秒）。视频生成只保留真实模型通道，默认 `seedance`；旧 `local_ffmpeg` 视频设置会自动迁移到 Seedance。`gemini_gateway` 支持对话接口、可选媒体上传地址、换票地址、来源标识、输出 Token 上限和输入 Token 预算；配置上传地址时优先上传视频并以 `fileData.fileUri` 调用。API Key 只写不回显；留空保存表示保留原 Key。视频生成 Key 还支持读取 `VF_SEEDANCE_API_KEY` 或 `npx dksk auth <key>` 写入的 `~/.acore/auth.json`，不会把机器级凭据复制进数据库。管线实现细节见后端与 `docs/ARCHITECTURE.md`。

视频生成支持 `seedance` 内网 Provider。默认地址为 `https://api.giikin.com`，Seedance 2.0 Flash 对应的协议模型名为 `seedance-2-0-fast`（也兼容输入 `seedance-2.0-flash`）。提交走 `/acore/model/v1/task/generate/video`，状态查询走 `/acore/model/v1/task/video/{taskId}`；下载后会统一转成项目要求的分辨率、帧率和精确时长。也可以使用 `npx dksk auth <key>` 登录后，在系统设置中保存同一认证 Key。

Seedance 只接收文本提示词，不接收参考视频、参考音频或 `input_contents`。参考视频仅用于本地拆解、片段复用和脚本生成，不会再经 `media_public_base_url` 或 GCloud 上传给视频模型。人声和 BGM 由视频模型按提示词直接生成。

默认安装会同时安装基础转场检测依赖：

```bash
uv sync --extra dev
```

基础依赖包含 `opencv-python`、`numpy`、`scenedetect` 和 CPU 版 `torch`。包内另带约 30MB 的 TransNetV2 权重，因此默认环境会同时使用 PySceneDetect 与 CNN 转场分数，不再因缺少 `torch` 静默降级。PySceneDetect 失败时才退化为 ffmpeg 场景切分，并在任务事件和日志中写明异常详情。

如果旧环境曾同时安装两套 OpenCV，升级时执行一次
`uv sync --extra dev --reinstall-package opencv-python`，避免卸载 headless 包时一起移除共享的 `cv2` 文件。

也可以分别启动：

```bash
# 终端 1
cd backend
../.venv/bin/python -m uvicorn video_fission.main:app --host 127.0.0.1 --port 8000

# 终端 2
cd frontend
npm run dev
```

生产构建后，FastAPI 会直接托管 `frontend/dist`：

```bash
npm run build --prefix frontend
cd backend
../.venv/bin/python -m uvicorn video_fission.main:app --host 127.0.0.1 --port 8000
```

访问 `http://127.0.0.1:8000`。

## 配置

复制 `.env.example` 为 `.env`，按需设置：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `CODEX_BIN` | PATH 中的 `codex` | Codex CLI 可执行文件 |
| `VF_HOST` | `127.0.0.1` | 监听地址 |
| `VF_PORT` | `8000` | 后端端口 |
| `VF_WORKSPACE` | `data/workspace` | Codex 隔离工作区 |
| `VF_DATA_DIR` | `data` | 数据、附件、产物与 Skill 根目录 |
| `VF_CODEX_TIMEOUT_SECONDS` | `180` | 单次 RPC 超时 |
| `VF_APPROVAL_TIMEOUT_SECONDS` | `300` | 审批/澄清等待超时 |
| `VF_AUTH_REQUIRED` | `true` | 是否启用登录保护；仅自动化测试可显式关闭 |
| `VF_AUTH_SECRET_KEY` | 自动生成 | JWT 签名密钥，生产环境建议显式配置 `openssl rand -hex 32` |
| `VF_AUTH_ADMIN_USERNAME` | `admin` | 首次启动时创建的引导管理员 |
| `VF_AUTH_ADMIN_PASSWORD` | 自动生成 | 首次管理员密码；未配置时写入 `data/bootstrap_admin.txt` |
| `VF_AUTH_TOKEN_EXPIRE_MINUTES` | `1440` | 登录会话有效分钟数 |
| `VF_AUTH_COOKIE_DOMAIN` | 空 | API 与前端位于同一父域下不同主机时配置，例如 `.example.com` |
| `VF_ALLOWED_ORIGINS` | 空 | 额外允许提交认证 API 的浏览器 Origin，逗号分隔；反向代理域名不自动匹配时需配置 |
| `VF_SSO_CLIENT_ID` / `VF_SSO_CLIENT_SECRET` | 空 | CAS 应用凭证；为空时只提供账号密码登录 |
| `VF_SSO_CALLBACK_URL` | 按请求生成 | CAS 回调地址，反向代理部署建议显式配置 |
| `VF_SSO_FRONTEND_URL` | `http://127.0.0.1:5174` | SSO 成功后返回的前端地址 |
| `VF_GEMINI_API_URL` | 空 | Gemini conversationV5 对话接口 |
| `VF_GEMINI_UPLOAD_URL` | 空 | Gemini 媒体上传接口；配置后优先使用 `fileUri` |
| `VF_GEMINI_TOKEN_URL` | SSO access token URL | 公司共享 token 获取地址 |
| `VF_GEMINI_MODEL` | 空 | 多模态默认模型 |
| `VF_GEMINI_SOURCE` | 空 | Gemini 网关请求来源标识 |
| `VF_GEMINI_MAX_CONCURRENCY` | `6` | 单进程 Gemini HTTP 最大并发数 |
| `VF_ENABLE_REFERENCE_REMIX` | `true` | 是否启用独立原片重排快捷入口；关闭后前端隐藏入口且相关写接口不可用 |

环境变量由当前 shell 读取；项目不会自动覆盖已有 `.env`。

## 数据与目录

```text
data/
├── video_fission.db       # SQLite
├── auth_secret            # 未显式配置时自动生成的 JWT 密钥（600 权限）
├── bootstrap_admin.txt    # 自动生成的首次管理员密码（600 权限）
├── workspace/
│   └── users/{user}/      # 每用户 Codex 隔离工作区
├── uploads/               # 对话附件
│   └── users/{user}/      # 每用户附件与 productions 参考视频
├── artifacts/             # Tool 生成产物
│   └── users/{user}/      # 每用户简报、关键帧、片段、final.mp4
├── skills/                # 全局自定义 Skill
└── skills_disabled/       # 已停用的全局 Skill
skills/                    # 随项目分发的内置 Skill
```

SQLite 保存以下数据：

- `users`：本地账号、bcrypt 密码哈希、角色与 SSO 身份映射
- `user_settings`：每用户模型、端点和密钥配置
- `conversations`：会话设置、Codex Thread ID 和运行状态
- `items`：对话消息与结构化执行项
- `events`：可重放的 SSE 事件
- `approvals`：审批请求和决定
- `clarifications`：Agent 提问和用户回答
- `tool_runs`：动态 Tool 的入参、输出与成功状态
- `app_settings`：模型与合成设置；密钥单独存，读取只返回 `api_key_set`
- `video_uploads` / `productions` / `production_events`：参考视频、创作任务和 SSE 事件

## 登录与用户隔离

首次启用认证启动时，系统会创建一个管理员账号。若没有配置 `VF_AUTH_ADMIN_PASSWORD`，随机密码只写入 `data/bootstrap_admin.txt`（权限 `600`）；登录后请立即通过用户 API 修改密码并按需创建成员账号。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/auth/login` | `application/x-www-form-urlencoded`：`username`、`password` |
| `GET` | `/api/auth/me` | 当前用户 |
| `POST` | `/api/auth/logout` | 清除登录 Cookie |
| `GET` | `/api/sso/login` | 获取 CAS 登录地址 |
| `GET` | `/api/sso/callback` | CAS ticket 回调 |
| `GET` / `POST` | `/api/users` | 管理员查看或创建用户 |
| `PATCH` / `DELETE` | `/api/users/{id}` | 管理员更新或删除用户 |

除认证、SSO 入口与健康检查外，所有 `/api` 接口都要求登录。服务端对每个会话、附件、创作任务、事件流和产物下载执行所有权校验；其他用户直接使用已知 ID 访问时统一返回 404。Skill 与 Tool 是全局配置，不在用户之间隔离，相关管理界面统一位于系统设置。SSE 通过同源 HttpOnly Cookie 认证，不把长期令牌放进事件流 URL。

升级旧数据时，首次创建管理员会把原有的未归属会话、创作任务和全局模型设置归入该管理员，避免历史内容丢失。用户自定义目录使用用户 ID 的 SHA-256 短摘要作为目录名，不直接暴露账号或原始 ID。

密码或角色被管理员修改后，该用户的旧 JWT 会因 `token_version` 变化立即失效。删除并重新创建同名账号也不会复用旧令牌。SSO 只会继续已绑定的企业身份；如果同名本地账号尚未绑定企业身份，系统会拒绝自动接管，避免把本地管理员权限直接交给同名企业账号。

所有非只读 API 请求会校验浏览器 `Origin`。反向代理使用新的外部域名时，请把完整 Origin 写入 `VF_ALLOWED_ORIGINS`，并在生产环境显式设置 `VF_SSO_CALLBACK_URL` 与 `VF_SSO_FRONTEND_URL`。

## 视频创作 API

视频生成默认提供者为 `seedance`，不再静默回退本地。不要把 API Key 写进仓库、README 或前端日志。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` / `PUT` | `/api/settings` | 读取或更新设置；Key 只写，留空保留，JSON `null` 清除 |
| `POST` | `/api/settings/test` | 探活 `multimodal` / `video_generator` |
| `POST` | `/api/productions/uploads` | 上传参考视频，最大 500MB，需能被 ffprobe 解析 |
| `POST` | `/api/productions` | 创建草稿（`title`、`brief`、`video_upload_id`，可选 `replace_policy`） |
| `GET` | `/api/productions` / `/api/productions/{id}` | 列表或详情；详情含只读 `replace_plan` |
| `PATCH` | `/api/productions/{id}` | 更新 `title` / `brief` / `replace_policy`；运行中 409 |
| `POST` | `/api/productions/{id}/run` | 启动或恢复后台确定性执行。空 body 兼容；可选 `{ replace_policy, resume_from }`。`resume_from` 允许 `ingest\|decompose\|intent\|script\|generate\|finalize`；省略时按跟进消息分类，已有拆解时默认仍从 `intent` 重跑；运行中 409 |
| `POST` | `/api/productions/{id}/select-video` | 在 `awaiting_video_choice` 时选定候选视频（body：`{ "video_id" }`），随后进入最终合成与字幕烧录 |
| `POST` | `/api/productions/{id}/select-script` | 旧版脚本选择入口，仅为后端兼容保留；当前界面不使用 |
| `POST` | `/api/productions/{id}/cancel` | 取消 |
| `DELETE` | `/api/productions/{id}` | 删除任务和产物；仍被引用的源视频不删 |
| `GET` | `/api/productions/{id}/events?after_id=` | SSE 进度 |
| `GET` | `/api/productions/{id}/artifacts/{name}` | 下载产物，禁止路径穿越 |

出片采用单 Codex 两阶段架构：当前对话先通过 `prepare_video_production` 获得真实拆解与参考检索结果，再加载 `production-orchestration`、`video-references`、`creative-intent-script-skeleton` 和 `universal-ugc-video-script-generation` 生成 `intent`、`skeleton` 与恰好 3 个 `script_candidates`；随后通过 `run_video_production` 提交。`ProductionAgent` 是后台确定性执行器，按状态和 `resume_from` 调用细粒度 Tool，不进行模型规划，也不会启动新的 `CodexAppServer`。当前流程不再选择脚本：`generate_candidate_videos` 会依次生成、分离音频并合成三条候选视频，完成后停在 `awaiting_video_choice`；用户选定一条后，后台从 `finalize` 继续，以该视频的脚本和片段执行最终合成与字幕烧录。音频分离结果会真正混入最终音轨。意图和脚本来自当前对话，不再另启 `VideoScriptAgent`。跟进留在同一 `production_id` 上，含糊的「再改一下」会先澄清，不会从 ingest 重来。brief 中的落地页 URL 会安全抽取卖点，没有 URL 不阻止出片。关键帧落在 `data/artifacts/productions/{id}/{seg_id}_frame_01.jpg`（可有 `_02` / `_03`）。`replace_policy.mode` 为 `default|all|selected|brief`；未点名片段时保持默认 Hook/CTA 生成、中间复用。`mode=selected` 未命中任何段时 `/run` 返回 400。任务公开载荷包含 `candidate_videos`，出片卡片展示三条候选项供选择，生产事件包含 `agent_plan` / `skill_loaded` / `tool_started` / `tool_completed` / `candidate_video_completed` / `awaiting_video_choice`。

`VF_ALLOW_LOCAL_COPY_FALLBACK=1` 仅用于测试，不应在部署环境启用。

配置 `gemini_gateway` 后，整片理解的每一次网关响应都会单独写入生产产物目录的 `gemini_api_calls/video_understanding/`。每个 JSON 包含完整响应、调用标签、输入形态（video/frames）、模型、耗时和成功/失败状态；网络或解析失败也会留下错误记录。该记录只影响诊断，写盘失败不会中断拆解或出片。

远程多模态走 `{base_url}/chat/completions`；视频生成可走 `seedance` 内网协议、`openai_compatible` 的 `{base_url}/video/generations` 或 `generic_http` 的 `POST {base_url}`。`seedance` 使用 `task_id` 与 `result.video.url`，旧格式的 `url` / `job_id` 轮询仍保留兼容。视频生成失败会直接失败，不回退本地关键帧。存在 generate 镜头但未配置 `base_url` 或 API Key 时，生成 Tool 必须明确失败，且不得出现「已还原」「已按参考对齐」这类表述。语音、音乐和环境音由视频模型直接生成并随片段音轨返回；缺少音轨的生成片段视为失败。合成之后可用 `burn_subtitles` 生成 SRT 并硬烧字幕。

## Tool

对话工作台仍注册这些会话 Tool：

| Tool | 用途 |
| --- | --- |
| `workspace_snapshot` | 查看隔离工作区文件 |
| `read_workspace_file` | 读取受控工作区文本文件 |
| `conversation_metrics` | 查询当前会话的持久化统计 |
| `create_video_brief` | 生成并保存 Markdown 视频简报 |
| `prepare_video_production` | 接入、拆解和检索参考视频，返回完整创意上下文 |
| `get_video_production` | 查询创作任务状态与产物 |
| `run_video_production` | 执行或恢复已有后台创作任务，可按需更新创意内容 |

出片能力另外注册在同一 `ToolRegistry` 上，但只有后台确定性执行器能调用：`ingest_reference_video`、`understand_reference_video`、`extract_landing_points`、`search_video_references`、`lock_timeline_opening`、`write_intent_and_scripts`、`get_production_state`、`update_production_progress`、`generate_candidate_videos`、`generate_video_shots`、`separate_audio`、`compose_timeline`、`burn_subtitles`、`handle_production_followup`。当前对话 Codex 只看到粗粒度入口 `prepare_video_production`、`run_video_production` 和状态查询工具。定义分别位于 `backend/video_fission/tools.py` 与 `production_tools.py`。

## Skill

内置 Skill 位于 `skills/`：

| Skill | 用途 |
| --- | --- |
| `video-brief` | 通用短视频策划简报 |
| `creative-intent-script-skeleton` | 理解创作说明和参考视频拆解，先收敛意图、概念和 Hook/Value/CTA 骨架 |
| `universal-ugc-video-script-generation` | 在骨架确认后生成镜头级分镜、片段复用/生成计划和音视频合成要求 |
| `video-references` | 2 个品类、13 篇 UGC 参考脚本；供上述两个创作 Skill 共享检索 |
| `production-orchestration` | 任务级出片编排：三个创意全部生成候选视频、视频选择点，以及开场连续块、最终合成门禁、诚实失败等不变量 |

参考脚本和全量索引导航位于 `skills/video-references/references/`。意图阶段读取首行骨架和注意项，完整脚本阶段读取至少 3 篇目标脚本全文。

用户可在「系统设置」中创建全局 Skill，文件会写入 `data/skills/<slug>/SKILL.md`；停用时移动到 `data/skills_disabled/<slug>`。聊天侧能力台只负责查看并引用 Skill。

新增对话或刷新 Runtime 时，应用通过 `skills/extraRoots/set` 注册：

```text
data/skills
skills
```

`GET /api/runtime/skills` 会启动一个临时 Codex Runtime 并返回真实发现的 Skill，而不是只读取本地文件。
对话 Agent 通过服务端 `read_skill` 工具读取已注册 Skill 的完整 `SKILL.md` 和目录内参考文件，
不依赖终端命令或用户工作区路径。

## 测试

基础测试：

```bash
cd backend && uv run pytest -q
npm test --prefix frontend
npm run build --prefix frontend
```

后端管线测试使用仓库内夹具 `backend/tests/fixtures/sample-reference.mp4`（约 8 秒，含 Hook/Value/CTA 色块和简单音频）。测试通过 Provider 桩验证真实远端生成路径、模型音轨、拼接和 `final.mp4` 输出，不再覆盖本地视频伪生成。

真实 Codex 集成测试：

```bash
RUN_LIVE_CODEX=1 uv run pytest backend/tests/test_live_codex.py -q
```

真实 Gemini / Seedance 探活和已在 5174 运行的浏览器冒烟测试默认跳过：

```bash
RUN_LIVE_PROVIDERS=1 uv run pytest backend/tests/test_live_providers.py -q
RUN_LIVE_5174=1 VF_LIVE_USERNAME=admin VF_LIVE_PASSWORD=... npm run test:e2e:live --prefix frontend
```

Seedance 探活额外读取 `VF_LIVE_SEEDANCE_BASE_URL`、`VF_LIVE_SEEDANCE_API_KEY` 和可选的 `VF_LIVE_SEEDANCE_MODEL`。这些测试不会写入项目配置或输出密钥。

Playwright E2E：

```bash
cd frontend
npm run test:e2e
```

E2E 覆盖：

1. 页面连接 Codex Runtime
2. 创建新对话并收到真实模型回复
3. 调用 `create_video_brief` 并落盘
4. 在系统设置中创建、停用、重新启用全局 Skill
5. 刷新页面后恢复会话、工具轨迹和 Skill
6. 390px 移动端下会话列表和能力抽屉可达
7. 未创建对话时上传附件，发送后附件不会丢失

E2E 使用独立的后端进程和 `.e2e-data/` 数据目录，不会读写正常的 `data/` 对话。

详细的设计与抽离说明见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

Seedance 视频生成接口、请求字段和任务查询协议见 [docs/API-SEEDANCE-VIDEO.md](docs/API-SEEDANCE-VIDEO.md)。

