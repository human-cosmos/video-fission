# Seedance 视频生成 API

本文档整理 Acore 内网 Seedance 视频生成接口，包括：

1. 提交视频生成任务
2. 查询视频任务详情

当前项目通过 `seedance` Provider 对接这些接口。默认服务地址为：

```text
https://api.giikin.com
```

所有请求使用 Bearer Token 认证：

```http
Authorization: Bearer <API_KEY>
Accept: application/json
Content-Type: application/json
```

## 调用流程

```text
POST /acore/model/v1/task/generate/video
  -> 获取 task_id
GET /acore/model/v1/task/video/{taskId}
  -> pending / processing / completed / failed / cancelled
  -> completed 后读取 data.result.video.url 并下载视频
```

---

## 1. 提交视频生成任务

### 基本信息

| 项目 | 内容 |
| --- | --- |
| 方法 | `POST` |
| 路径 | `/acore/model/v1/task/generate/video` |
| 请求类型 | `application/json` |
| 描述 | 支持文生视频、图生视频、首尾帧视频等多种模式 |
| Operation ID | `generateVideo` |

完整地址：

```text
POST https://api.giikin.com/acore/model/v1/task/generate/video
```

### 请求头

| 请求头 | 必填 | 说明 |
| --- | --- | --- |
| `Authorization` | 是 | `Bearer <API_KEY>` |
| `Content-Type` | 是 | `application/json` |
| `Accept` | 否 | `application/json` |

### 请求字段

#### VideoGenerateRequest

`model` 为必填字段。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `model` | string | 是 | 模型名称。OpenAPI 枚举：`seedance-2-0`、`seedance-2-0-fast` |
| `prompt` | string | 条件必填 | 文本提示词。`input_contents` 中不包含 `text` 类型时必填 |
| `duration` | integer | 否 | 视频时长，单位秒。`-1` 表示自动时长 |
| `mode` | string | 否 | 调用模式，示例：`async_poll` |
| `source` | string | 否 | 来源标识，便于服务端统计和追踪 |
| `options` | object | 否 | 生成选项，见 `GenerateOptions` |
| `userInfo` | object | 否 | 用户信息。个人 Key 非必传，通过开放平台调用时通常必传 |
| `aspect_ratio` | string | 否 | 宽高比：`16:9`、`9:16`、`21:9`、`adaptive` |
| `callback_url` | string | 否 | 异步回调地址 |
| `output_format` | string | 否 | 输出格式，示例：`mp4` |
| `input_contents` | array | 否 | 多模态输入内容列表 |

#### GenerateOptions

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `resolution` | string | 分辨率：`480p`、`720p` |
| `watermark` | boolean | 是否添加水印 |
| `tools` | array | 工具列表，例如联网搜索 |
| `generate_audio` | boolean | 是否生成音频 |
| `return_last_frame` | boolean | 是否返回最后一帧 |
| `omni_reference_task_type` | string | Omni 参考任务类型 |

#### Tool

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `type` | string | 是 | 工具类型，例如 `web_search` |

#### UserInfo

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `userId` | integer | 用户 ID |
| `tenantId` | integer | 租户 ID |
| `areaId` | integer | 团队 ID |
| `orgCode` | string | 组织编码 |
| `postIds` | integer[] | 岗位 ID 列表 |
| `appId` | string | 应用编号 |

#### InputContent

`type` 为必填字段。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `type` | string | 是 | 内容类型：`text`、`image_url`、`video_url`、`audio_url` |
| `text` | string | 否 | 文本内容，`type=text` 时使用 |
| `role` | string | 否 | 角色：`first_frame`、`last_frame`、`reference_image`、`reference_video`、`reference_audio` |
| `image_url` | object | 否 | 图片地址对象，`type=image_url` 时使用 |
| `video_url` | object | 否 | 视频地址对象，`type=video_url` 时使用 |
| `audio_url` | object | 否 | 音频地址对象，`type=audio_url` 时使用 |

媒体地址对象结构：

```json
{
  "url": "https://example.com/media"
}
```

适用于 `ImageUrl`、`VideoUrl` 和 `AudioUrl`，其中 `url` 为必填字段。

### 文生视频请求示例

```json
{
  "model": "seedance-2-0-fast",
  "prompt": "一只白色纸飞机在晴朗蓝天下缓慢飞行，真实摄影，竖屏，镜头平稳",
  "duration": 5,
  "mode": "async_poll",
  "source": "video-fission",
  "aspect_ratio": "9:16",
  "output_format": "mp4",
  "options": {
    "resolution": "720p",
    "watermark": false,
    "generate_audio": true,
    "return_last_frame": false
  }
}
```

本项目向 Seedance 提交参考视频生成时，会在 `input_contents` 中放入一条 `type=video_url`、`role=reference_video`，并设置 `options.generate_audio=true`；人声、音乐和环境音由视频模型直接生成。参考视频优先使用 `video_generator.media_public_base_url` 拼接的直链，为空或指向本机时改用多模态设置中的 GCloud 上传地址。

### 图生视频请求示例

```json
{
  "model": "seedance-2-0-fast",
  "prompt": "保持商品外观不变，镜头缓慢环绕展示产品",
  "duration": 5,
  "mode": "async_poll",
  "source": "video-fission",
  "aspect_ratio": "9:16",
  "output_format": "mp4",
  "input_contents": [
    {
      "type": "image_url",
      "role": "first_frame",
      "image_url": {
        "url": "https://example.com/product.jpg"
      }
    }
  ],
  "options": {
    "resolution": "720p",
    "generate_audio": false
  }
}
```

### 成功响应

HTTP 200。

#### RVideoGenerateResponse

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | integer | 状态码 |
| `bizCode` | integer | 业务状态码 |
| `data` | object | 视频生成任务信息 |
| `msg` | string | 返回消息 |

#### VideoGenerateResponse

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `task_id` | string | 任务 ID |
| `status_url` | string | 状态查询 URL |
| `estimated_time` | integer | 预估生成时间，单位秒 |

响应示例：

```json
{
  "code": 200,
  "bizCode": 3001000,
  "data": {
    "task_id": "task_1794986189088_gen19st8",
    "status_url": "/api/v1/tasks/task_1794986189088_gen19st8",
    "estimated_time": 120
  },
  "msg": "提交成功"
}
```

### 错误响应

HTTP 401 或业务层错误使用 `RVoid`。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | integer | 状态码 |
| `bizCode` | integer | 业务状态码 |
| `data` | object | 错误承载数据 |
| `msg` | string | 错误消息 |

示例：

```json
{
  "code": 400,
  "bizCode": 0,
  "data": null,
  "msg": "模型不存在"
}
```

### 注意事项

- OpenAPI 请求体中的 `duration_ms`、`width`、`height`、`fps` 不属于该接口字段。
- 视频时长使用秒级整数 `duration`。
- 宽高比使用 `aspect_ratio`，分辨率使用 `options.resolution`。
- 提交响应中的 `status_url` 可能是网关内部路径。当前项目固定使用正式的 `/acore/model/v1/task/video/{taskId}` 查询任务。
- `seedance-2.0-flash` 不是该接口文档中的模型 ID。项目会将其映射为实际可用的 `seedance-2-0-fast`。

---

## 2. 查询视频任务详情

### 基本信息

| 项目 | 内容 |
| --- | --- |
| 方法 | `GET` |
| 路径 | `/acore/model/v1/task/video/{taskId}` |
| 描述 | 根据任务 ID 查询视频生成任务的状态和结果 |
| Operation ID | `getVideoTaskStatus` |

完整地址：

```text
GET https://api.giikin.com/acore/model/v1/task/video/{taskId}
```

### 路径参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `taskId` | string | 是 | 提交任务后返回的 `task_id` |

### 成功响应

HTTP 200。

#### RVideoTaskDetailResponse

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | integer | 状态码 |
| `bizCode` | integer | 业务状态码 |
| `data` | object | 视频任务详情 |
| `msg` | string | 返回消息 |

#### VideoTaskDetailResponse

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | `pending`、`processing`、`completed`、`failed`、`cancelled` |
| `result` | object | 任务结果，完成前可能为 `null` |
| `error` | object | 错误信息，成功时为 `null` |
| `task_id` | string | 任务 ID |

#### TaskResult

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `video` | object | 视频信息 |
| `metadata` | object | 任务元数据 |
| `cost` | object | 消费金额信息 |
| `provider_metadata` | object | Provider 元数据 |

#### VideoInfo

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `url` | string | 视频 URL |
| `duration` | integer | 视频时长，单位秒 |
| `format` | string | 视频格式，例如 `mp4` |
| `last_frame_url` | string/null | 最后一帧图片 URL |

#### Metadata

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `model` | string | 模型名称 |
| `generationTime` | number | 生成耗时，单位秒 |

#### CostInfo

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | 消费状态 |
| `currency` | string | 币种 |
| `channel_cost` | number | 渠道消费 |
| `model_cost` | number | 模型消费 |
| `unit_price` | number | 单价 |
| `billing_mode` | string | 计费模式 |
| `billing_unit` | string | 计费单位 |
| `metered_duration_seconds` | integer | 计费时长，单位秒 |
| `total_cost` | number | 总消费 |

#### ErrorInfo

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | string | 错误码 |
| `message` | string | 错误信息 |

### 处理中响应示例

```json
{
  "code": 200,
  "bizCode": 3001000,
  "data": {
    "status": "processing",
    "result": null,
    "error": null,
    "task_id": "task_1794986189088_gen19st8"
  },
  "msg": "操作成功"
}
```

### 完成响应示例

```json
{
  "code": 200,
  "bizCode": 3001000,
  "data": {
    "status": "completed",
    "result": {
      "video": {
        "url": "https://resource.giikin.com/ai/genai/example/video.mp4",
        "duration": 5,
        "format": "mp4",
        "last_frame_url": null
      },
      "metadata": {
        "model": "seedance-2-0-fast",
        "resolution": "720p",
        "ratio": "9:16",
        "duration": 5,
        "generation_time": 77.2
      },
      "cost": {
        "status": "confirmed",
        "currency": "CNY",
        "unit_price": 0.38,
        "billing_mode": "per_second",
        "billing_unit": "second",
        "metered_duration_seconds": 5,
        "total_cost": 1.9
      },
      "provider_metadata": {
        "generate_audio": true,
        "output_format": "mp4",
        "fps": 24
      }
    },
    "error": null,
    "task_id": "task_1794986189088_gen19st8"
  },
  "msg": "操作成功"
}
```

### 失败响应示例

```json
{
  "code": 200,
  "bizCode": 3001000,
  "data": {
    "status": "failed",
    "result": null,
    "error": {
      "code": "GENERATION_FAILED",
      "message": "视频生成失败"
    },
    "task_id": "task_example"
  },
  "msg": "操作成功"
}
```

### 状态说明

| 状态 | 处理方式 |
| --- | --- |
| `pending` | 任务已创建，继续轮询 |
| `processing` | 正在生成，继续轮询 |
| `completed` | 读取 `data.result.video.url` 并下载视频 |
| `failed` | 读取 `data.error.code` 和 `data.error.message` |
| `cancelled` | 任务已取消，不再下载 |

### 错误响应

HTTP 401 或查询失败使用 `RVoid`：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | integer | 状态码 |
| `bizCode` | integer | 业务状态码 |
| `data` | object | 错误承载数据 |
| `msg` | string | 错误消息 |

示例：

```json
{
  "code": 400,
  "bizCode": 3006045,
  "data": null,
  "msg": "异步任务不存在"
}
```

---

## 项目中的实现

当前项目对应实现位于：

```text
backend/video_fission/providers.py
```

主要函数：

| 函数 | 用途 |
| --- | --- |
| `generate_seedance_video` | 提交任务、轮询状态、下载视频 |
| `probe_seedance` | 查询模型列表并校验模型是否启用 |
| `_seedance_video_url` | 从 `data.result.video.url` 或兼容响应中提取视频 URL |
| `_seedance_model` | 将用户模型别名映射到协议模型 ID |

项目配置：

```json
{
  "video_generator": {
    "provider": "seedance",
    "base_url": "https://api.giikin.com",
    "source": "video-fission",
    "resolution": "720p",
    "model": "seedance-2-0-fast",
    "timeout_seconds": 600,
    "fallback_to_local": false
  }
}
```

下载后的远端视频会统一转码为：

- H.264
- `yuv420p`
- 项目目标宽度和高度
- 项目目标帧率
- 精确镜头时长
