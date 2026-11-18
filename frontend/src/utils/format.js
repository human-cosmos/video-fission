export function formatTimecode(ms) {
  const value = Math.max(0, Number(ms) || 0)
  const totalSeconds = Math.floor(value / 1000)
  const millis = Math.round(value % 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}.${String(millis).padStart(3, '0')}`
}

export function formatDuration(ms) {
  const value = Math.max(0, Number(ms) || 0)
  if (value < 1000) return `${Math.round(value)} ms`
  const seconds = value / 1000
  return `${seconds.toFixed(value % 1000 === 0 ? 0 : 1)} 秒`
}

export function formatBytes(bytes) {
  const value = Math.max(0, Number(bytes) || 0)
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / (1024 * 1024)).toFixed(1)} MB`
}

export const WORKSPACE_MODES = [
  { id: 'chat', label: '对话' },
  { id: 'studio', label: '视频创作' },
  { id: 'settings', label: '模型设置' },
]

export const STAGE_ORDER = [
  'ingest',
  'decompose',
  'intent',
  'script',
  'generate',
  'compose',
  'done',
]

export const STAGE_LABELS = {
  ingest: '接入',
  decompose: '拆解',
  intent: '意图',
  script: '脚本',
  generate: '生成',
  compose: '合成',
  done: '完成',
}

export const STATUS_LABELS = {
  draft: '草稿',
  running: '进行中',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

export const ROLE_LABELS = {
  hook: 'Hook 开场',
  value: 'Value 价值',
  cta: 'CTA 号召',
  other: '其他',
}

export const ACTION_LABELS = {
  reuse: '复用',
  generate: '新生成',
}

export function segmentFrameNames(segmentId) {
  return [1, 2, 3].map(
    (index) => `${segmentId}_frame_0${index}.jpg`,
  )
}

export function referenceLabel(path) {
  const text = String(path || '').trim()
  if (!text) return ''
  const parts = text.split(/[\\/]/).filter(Boolean)
  return parts[parts.length - 1] || text
}

export function historyStatusText(item) {
  const status = STATUS_LABELS[item?.status] || item?.status || ''
  if (item?.status === 'completed' || item?.current_stage === 'done') return status
  const stage = STAGE_LABELS[item?.current_stage] || item?.current_stage || ''
  if (status && stage) return `${status} · ${stage}`
  return status || stage
}

export function pickPositiveBytes(...values) {
  for (const value of values) {
    const n = Number(value)
    if (Number.isFinite(n) && n > 0) return n
  }
  return 0
}

export function humanizeMsText(text) {
  return String(text || '').replace(/(\d+)\s*ms\b/gi, (_, value) => formatDuration(Number(value)))
}

export function isTechnicalVisual(text) {
  const value = String(text || '')
  return /\d+\s*x\s*\d+\s*@\s*[\d.]+fps/i.test(value) || /抽取\s*\d+\s*帧/.test(value)
}

export function needsRemoteFields(provider) {
  return provider === 'openai_compatible' || provider === 'generic_http'
}
