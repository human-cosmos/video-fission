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
  { id: 'settings', label: '系统设置' },
]

export const POLICY_MODES = [
  { id: 'default', label: '默认 Hook+CTA', hint: '只新生成开场和结尾，中间尽量复用' },
  { id: 'brief', label: '按说明判断', hint: '从创作说明里解析要重做的片段' },
  { id: 'all', label: '全部重做', hint: '每一段都重新生成' },
]

export const VIDEO_ITEM_TYPES = [
  'video_progress',
  'video_understanding',
  'video_plan',
  'video_segments',
  'video_observe',
  'video_output',
]

export const VIDEO_ACCEPT = '.mp4,.mov,.webm,.m4v,video/mp4,video/quicktime,video/webm,video/x-m4v'
export const MAX_PRODUCTION_UPLOAD_BYTES = 500 * 1024 * 1024

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
  awaiting_script_choice: '待选择脚本',
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
  if (item?.status === 'awaiting_script_choice') return status
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
  return (
    provider === 'openai_compatible' ||
    provider === 'generic_http' ||
    provider === 'gemini_gateway' ||
    provider === 'seedance'
  )
}

export function isVideoFile(file) {
  if (!file) return false
  if (file.kind === 'production_video') return true
  const type = String(file.content_type || file.type || '')
  if (type.startsWith('video/')) return true
  return /\.(mp4|mov|webm|m4v)$/i.test(String(file.name || file.filename || ''))
}

export function aspectStyle(dims) {
  const width = Number(dims?.width) || 0
  const height = Number(dims?.height) || 0
  if (!width || !height) return {}
  return { '--player-aspect': `${width} / ${height}` }
}

export function aspectClass(dims, { framed = true } = {}) {
  const width = Number(dims?.width) || 0
  const height = Number(dims?.height) || 0
  const base = framed ? 'player-frame' : ''
  if (!width || !height) return base || 'player-frame'
  return `${base} ${height > width ? 'is-portrait' : 'is-landscape'}`.trim()
}

export function segmentAction(segment, replacePlan, shots = []) {
  const id = segment?.id
  if (!id) return ''
  const resolved = replacePlan?.resolved_segment_ids
  if (Array.isArray(resolved)) {
    return resolved.includes(id) ? 'generate' : 'reuse'
  }
  const shot = (shots || []).find(
    (item) => item.source_segment_id === id || item.segment_id === id,
  )
  return shot?.action || segment?.action || ''
}

export function cleanDisplayText(text) {
  let value = String(text || '').trim()
  if (!value) return ''
  if (isTechnicalVisual(value)) return ''
  value = value
    .replace(/时长\s*\d+\s*ms[。.\s]*/gi, '')
    .replace(/按时间窗口划分[。.]?/g, '')
    .replace(/场景切点划分[。.]?/g, '')
  value = humanizeMsText(value)
    .replace(/[ \t]+/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
  return value
}

export function withRevision(url, rev) {
  if (!url) return ''
  if (!rev) return url
  return `${url}${url.includes('?') ? '&' : '?'}t=${encodeURIComponent(rev)}`
}

export function toProductionVideoFile(upload) {
  if (!upload) return null
  return {
    kind: 'production_video',
    id: upload.id,
    name: upload.name || upload.filename || 'reference.mp4',
    path: '',
    size: upload.size || upload.file_size || 0,
    content_type: upload.content_type || 'video/mp4',
    duration_ms: upload.duration_ms,
    width: upload.width,
    height: upload.height,
  }
}
