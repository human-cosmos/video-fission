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
- 视频创作管线：可配置多模态拆解、真实检索 `video-references` 的意图/脚本、可配置视频生成、ffmpeg 合成；默认 `local_ffmpeg`，无 API Key 也可上传参考视频，经 3 选 1 脚本后下载可播放的 `final.mp4`
- decompose 增强：可选 TransNetV2 + PySceneDetect 转场检测、整片/片段理解与观察卡；缺依赖或无 Key 时自动退化，不中断出片
- 模型设置持久化到 SQLite；API Key 只写不回显；`gemini_gateway` 的 `token_url` 可明文回读
- 后端单元测试、真实 Codex 集成测试和 Playwright 浏览器 E2E

## 系统要求

- Python 3.11+
- Node.js 20+
- ffmpeg / ffprobe（本机已验证 7.1.x）
- 已安装并登录官方 Codex CLI（仅对话工作台需要；本地视频管线不依赖 Codex 或 API Key）
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

打开 `http://127.0.0.1:5173`。

工作台可在「对话 / 系统设置」之间切换，原有 Codex 对话能力保持不变。

运行日志默认同时写入终端和 `status/video-fission.log`，单文件 20MB、保留 10 份。日志带 `request_id` / `production_id` / `stage` 等关联字段，覆盖 API 请求、外部模型与视频 Provider、Codex RPC、转场/切片、片段理解、镜头生成和 FFmpeg 合成。用 `VF_LOG_LEVEL=DEBUG` 可查看更细的媒体探测和 FFmpeg 命令；提示词默认只记录长度与摘要，调试时可临时设置 `VF_LOG_PROMPTS=1` 输出截断预览。

### 视频创作

默认无需 API Key。在对话中上传参考视频（最大 500MB）并填写创作说明后发送。时间线会展示理解、规划、片段和成片卡片；配置了理解模型后还会出现整片标签、三级标签和观察卡。完成后可播放并下载 `final.mp4` 和字幕 `final.srt`。刷新后会从已有任务恢复进度。

「系统设置」可配置多模态（`local_ffmpeg` / `openai_compatible` / `gemini_gateway`）、视频生成、TTS，以及理解与切片开关（`transition_detection` / `clip_analysis` / `observe` / `clip_workers` / `gemini_max_size_mb` / `observe_timeout_seconds`，观察卡超时默认 360 秒）。`gemini_gateway` 支持对话接口、可选媒体上传地址、换票地址、来源标识和输出 Token 上限；配置上传地址时优先上传视频并以 `fileData.fileUri` 调用，否则尝试 `inlineData`，无效时再退化为关键帧。`.env` 的 `VF_GEMINI_*` 会在当前项目首次启动或旧默认配置迁移时写入用户设置，显式自定义过的数据库设置优先。API Key 只写不回显；留空保存表示保留原 Key。管线实现细节见后端与 `docs/ARCHITECTURE.md`。

视频生成支持 `seedance` 内网 Provider。默认地址为 `https://api.giikin.com`，Seedance 2.0 Flash 对应的协议模型名为 `seedance-2-0-fast`（也兼容输入 `seedance-2.0-flash`）。提交走 `/acore/model/v1/task/generate/video`，状态查询走 `/acore/model/v1/task/video/{taskId}`；下载后会统一转成项目要求的分辨率、帧率和精确时长。也可以使用 `npx dksk auth <key>` 登录后，在系统设置中保存同一认证 Key。

可选转场检测依赖单独安装，不要把它和 `uv sync --extra dev` 绑在一起：

```bash
uv sync --extra shot-detect
```

`shot-detect` 包含 CPU 可跑的 `torch`、`opencv-python`、`numpy`、`scenedetect`，以及包内约 30MB 的 TransNetV2 权重。缺 extra 或检测失败时自动退化为 ffmpeg 场景切分并写 warning，默认 pytest 仍须通过。

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
│   └── users/{user}/      # 每用户简报、关键帧、片段、final.mp4 / final.srt
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

默认提供者为 `local_ffmpeg`。不要把 API Key 写进仓库、README 或前端日志。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` / `PUT` | `/api/settings` | 读取或更新设置；Key 只写，留空保留，JSON `null` 清除 |
| `POST` | `/api/settings/test` | 探活 `multimodal` / `video_generator` / `tts` |
| `POST` | `/api/productions/uploads` | 上传参考视频，最大 500MB，需能被 ffprobe 解析 |
| `POST` | `/api/productions` | 创建草稿（`title`、`brief`、`video_upload_id`，可选 `replace_policy`） |
| `GET` | `/api/productions` / `/api/productions/{id}` | 列表或详情；详情含只读 `replace_plan` |
| `PATCH` | `/api/productions/{id}` | 更新 `title` / `brief` / `replace_policy`；运行中 409 |
| `POST` | `/api/productions/{id}/run` | 启动或重跑管线。空 body 兼容；可选 `{ replace_policy, resume_from }`。`resume_from` 允许 `ingest\|decompose\|intent\|script\|generate`；省略时按跟进消息分类，已有拆解时默认仍从 `intent` 重跑；运行中 409 |
| `POST` | `/api/productions/{id}/select-script` | 在 `awaiting_script_choice` 时选用脚本（body：`{ "script_id" }`），随后从 generate 继续 |
| `POST` | `/api/productions/{id}/cancel` | 取消 |
| `DELETE` | `/api/productions/{id}` | 删除任务和产物；仍被引用的源视频不删 |
| `GET` | `/api/productions/{id}/events?after_id=` | SSE 进度 |
| `GET` | `/api/productions/{id}/artifacts/{name}` | 下载产物，禁止路径穿越 |

阶段顺序：`ingest` → `decompose` → `intent` → `script` → `generate` → `compose`。`script` 产出 3 个候选后暂停，需选用后才生成；跟进可再出脚本或在已选脚本上再生成。brief 中的落地页 URL 会安全抽取卖点，没有 URL 不阻止出片。关键帧落在 `data/artifacts/productions/{id}/{seg_id}_frame_01.jpg`（可有 `_02` / `_03`）。`GET /api/health` 在原有字段外增加 `ffmpeg` 和 `pipeline`。`replace_policy.mode` 为 `default|all|selected|brief`；未点名片段时保持默认 Hook/CTA 生成、中间复用。勾选优先于 brief 点名。`mode=selected` 未命中任何段时 `/run` 返回 400。

远程多模态走 `{base_url}/chat/completions`；视频生成可走 `seedance` 内网协议、`openai_compatible` 的 `{base_url}/video/generations` 或 `generic_http` 的 `POST {base_url}`。`seedance` 使用 `task_id` 与 `result.video.url`，旧格式的 `url` / `job_id` 轮询仍保留兼容。失败且 `fallback_to_local=true` 时回退本地并写入 warning。TTS 未配置不会阻塞出片。默认按参考样式（读不到则用安全默认位置）硬烧字幕，并只混合已分离的参考 BGM，可在设置中关闭。

## Tool

默认注册 6 个动态 Tool：

| Tool | 用途 |
| --- | --- |
| `workspace_snapshot` | 查看隔离工作区文件 |
| `read_workspace_file` | 读取受控工作区文本文件 |
| `conversation_metrics` | 查询当前会话的持久化统计 |
| `create_video_brief` | 生成并保存 Markdown 视频简报 |
| `start_video_production` | 按创作说明和参考视频启动管线 |
| `get_video_production` | 查询创作任务状态与产物 |

Tool 定义位于 `backend/video_fission/tools.py`。新增 Tool 时注册名称、描述、JSON Schema 和执行函数；会话创建 Thread 时会自动把 Tool 契约下发给 Codex。

## Skill

内置 Skill 位于 `skills/`：

| Skill | 用途 |
| --- | --- |
| `video-brief` | 通用短视频策划简报 |
| `creative-intent-script-skeleton` | 理解创作说明和参考视频拆解，先收敛意图、概念和 Hook/Value/CTA 骨架 |
| `universal-ugc-video-script-generation` | 在骨架确认后生成镜头级分镜、片段复用/生成计划和音视频合成要求 |
| `video-references` | 2 个品类、13 篇 UGC 参考脚本；供上述两个创作 Skill 共享检索 |

参考脚本和全量索引导航位于 `skills/video-references/references/`。意图阶段读取首行骨架和注意项，完整脚本阶段读取至少 3 篇目标脚本全文。

用户可在「系统设置」中创建全局 Skill，文件会写入 `data/skills/<slug>/SKILL.md`；停用时移动到 `data/skills_disabled/<slug>`。聊天侧能力台只负责查看并引用 Skill。

新增对话或刷新 Runtime 时，应用通过 `skills/extraRoots/set` 注册：

```text
data/skills
skills
```

`GET /api/runtime/skills` 会启动一个临时 Codex Runtime 并返回真实发现的 Skill，而不是只读取本地文件。

## 测试

基础测试：

```bash
uv run pytest -q
npm test --prefix frontend
npm run build --prefix frontend
```

后端管线测试使用仓库内夹具 `backend/tests/fixtures/sample-reference.mp4`（约 8 秒，含 Hook/Value/CTA 色块和简单音频）。无网络、无 API Key 的 `POST /run` 路径会生成可被 ffprobe 解析的 `final.mp4` 和 `final.srt`。

真实 Codex 集成测试：

```bash
RUN_LIVE_CODEX=1 uv run pytest backend/tests/test_live_codex.py -q
```

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

