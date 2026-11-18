const TYPE_LABELS = {
  agentMessage: '回复',
  userMessage: '你',
  reasoning: '推理',
  plan: '计划',
  commandExecution: '命令',
  fileChange: '文件变更',
  mcpToolCall: 'MCP 工具',
  dynamicToolCall: '动态工具',
  functionCallOutput: '工具输出',
  webSearch: '联网检索',
  imageView: '查看图片',
  imageGeneration: '生成图片',
  contextCompaction: '上下文压缩',
  tokenUsage: 'Token',
  collabAgentToolCall: '协作 Agent',
  subAgentActivity: '子 Agent',
  video_progress: '出片进度',
  video_understanding: '视频理解',
  video_plan: '任务规划',
  video_segments: '片段',
  video_observe: '观察',
  video_output: '成片',
}

export function itemLabel(itemType) {
  return TYPE_LABELS[itemType] || itemType || '运行事件'
}

export function payloadText(item) {
  const payload = item?.payload || {}
  return (
    payload.text ||
    payload.summary ||
    payload.output ||
    payload.command ||
    payload.message ||
    ''
  )
}

export function toolCallName(item) {
  const payload = item?.payload || {}
  return (
    payload.tool ||
    payload.name ||
    payload.toolName ||
    payload.server ||
    itemLabel(item?.item_type)
  )
}

export function eventTouchesConversation(data) {
  return Boolean(data?.kind || data?.payload)
}

export function isRefreshEvent(kind) {
  return /^(item\.|item_upserted|production_updated|production\.|agent\.message\.|reasoning\.|plan\.|command\.|fileChange\.|mcpTool\.|turn\.|approval\.|clarification\.|tool\.|conversation\.|error$)/.test(
    String(kind || ''),
  )
}

export function compactTitle(value, fallback = '新对话') {
  const text = String(value || '')
    .replace(/\s+/g, ' ')
    .trim()
  if (!text) return fallback
  return text.length > 38 ? `${text.slice(0, 38)}…` : text
}

export function formatRelativeTime(iso) {
  if (!iso) return ''
  const value = new Date(iso)
  const diff = Date.now() - value.getTime()
  if (!Number.isFinite(diff)) return ''
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)} 天前`
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'short',
    day: 'numeric',
  }).format(value)
}

