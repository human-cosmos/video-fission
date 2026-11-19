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
  video_observe: '深度观察',
  video_output: '成片',
}

const TRACE_ITEM_TYPES = new Set([
  'reasoning',
  'commandExecution',
  'functionCallOutput',
  'dynamicToolCall',
  'mcpToolCall',
  'webSearch',
  'fileChange',
])

const HIDDEN_CHAT_ITEM_TYPES = new Set(TRACE_ITEM_TYPES)

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

export function agentMessagePhase(item) {
  return String(item?.payload?.phase || '').trim()
}

export function isAgentCommentary(item) {
  return item?.item_type === 'agentMessage' && agentMessagePhase(item) === 'commentary'
}

export function agentMessageStatusText(item) {
  if (item?.status === 'inProgress') return '执行中'
  if (item?.status === 'failed') return '未完成'
  if (isAgentCommentary(item)) return '进度更新'
  return '已完成'
}

export function canShowTraceOutput(item) {
  return !['commandExecution', 'functionCallOutput'].includes(item?.item_type)
}

export function hasVideoPlanContent(item) {
  if (item?.item_type !== 'video_plan') return true
  const payload = item.payload || {}
  if (Array.isArray(payload.script_candidates) && payload.script_candidates.length) return true
  return [
    payload.intent,
    payload.skeleton,
    payload.script,
    payload.replace_plan,
    payload.opening,
    payload.landing_page,
  ].some((value) => {
    if (!value) return false
    if (Array.isArray(value)) return value.length > 0
    if (typeof value === 'object') return Object.keys(value).length > 0
    return true
  })
}

export function groupExecutionItems(items = [], { includeTrace = false } = {}) {
  if (!includeTrace) {
    return items.filter(
      (item) =>
        !HIDDEN_CHAT_ITEM_TYPES.has(item.item_type) &&
        !(item.item_type === 'video_plan' && !hasVideoPlanContent(item)),
    )
  }

  const rows = []
  let traceItems = []

  function flushTraceItems() {
    if (!traceItems.length) return
    if (traceItems.length === 1) {
      rows.push(traceItems[0])
    } else {
      const first = traceItems[0]
      const last = traceItems[traceItems.length - 1]
      rows.push({
        id: `execution-group-${first.id}-${last.id}`,
        item_type: 'executionGroup',
        status: traceItems.some((item) => item.status === 'inProgress')
          ? 'inProgress'
          : traceItems.some((item) => item.status === 'failed')
            ? 'failed'
            : 'completed',
        items: traceItems,
      })
    }
    traceItems = []
  }

  for (const item of items) {
    if (item.item_type === 'video_plan' && !hasVideoPlanContent(item)) continue
    if (TRACE_ITEM_TYPES.has(item.item_type)) {
      traceItems.push(item)
    } else {
      flushTraceItems()
      rows.push(item)
    }
  }
  flushTraceItems()
  return rows
}

export function eventTouchesConversation(data) {
  return Boolean(data?.kind || data?.payload)
}

export function isRefreshEvent(kind) {
  return /^(item\.|item_upserted|production_updated|production\.|agent\.message\.|reasoning\.|plan\.|command\.|fileChange\.|mcpTool\.|turn\.|thread\.name\.updated|approval\.|clarification\.|tool\.|conversation\.|error$)/.test(
    String(kind || ''),
  )
}

export function isConversationListEvent(kind) {
  return /^(item\.completed|item_upserted|item\.local|tool\.completed|turn\.|conversation\.|thread\.name\.updated|error$)/.test(
    String(kind || ''),
  )
}

export function isProductionRefreshEvent(kind) {
  return /^(production_updated|production\.)/.test(String(kind || ''))
}

export function conversationStateLabel(conversation) {
  const state =
    conversation?.list_state ||
    (conversation?.is_running
      ? 'running'
      : conversation?.last_error
        ? 'failed'
        : conversation?.has_warnings
          ? 'warning'
          : 'completed')
  return {
    running: '运行中',
    failed: '执行失败',
    warning: '工具警告',
    completed: '已完成',
    empty: '草稿',
  }[state] || '已完成'
}

export function mergeConversationSummaries(existing = [], incoming = []) {
  const existingById = new Map(existing.map((item) => [item.id, item]))
  const incomingIds = new Set(incoming.map((item) => item.id))
  return [
    ...incoming.map((item) => ({ ...existingById.get(item.id), ...item })),
    ...existing.filter((item) => !incomingIds.has(item.id)),
  ]
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

