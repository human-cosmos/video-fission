<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, productionArtifactUrl, productionEventUrl } from '../api'
import { compactTitle } from '../utils/events'
import {
  ACTION_LABELS,
  ROLE_LABELS,
  STAGE_LABELS,
  STAGE_ORDER,
  STATUS_LABELS,
  formatBytes,
  formatDuration,
  formatTimecode,
  historyStatusText,
  humanizeMsText,
  isTechnicalVisual,
  pickPositiveBytes,
  referenceLabel,
  segmentFrameNames,
} from '../utils/format'

const emit = defineEmits(['notify'])

const STORAGE_KEY = 'video-fission.activeProduction'
const ACCEPT = '.mp4,.mov,.webm,.m4v,video/mp4,video/quicktime,video/webm,video/x-m4v'
const MAX_UPLOAD_BYTES = 500 * 1024 * 1024
const EVENT_TYPES = ['stage_started', 'stage_completed', 'warning', 'error', 'completed']

const history = ref([])
const historyLoading = ref(false)
const production = ref(null)
const title = ref('')
const brief = ref('')
const upload = ref(null)
const uploading = ref(false)
const starting = ref(false)
const rerunning = ref(false)
const policyMode = ref('default')
const regenSelected = ref({})
const error = ref('')
const dragOver = ref(false)
const hiddenFrames = ref({})
const fileInput = ref(null)
const composerOpen = ref(true)
const playerAspect = ref(null)
const clipAspects = ref({})

let eventSource = null
let lastEventId = 0
let refreshTimer = null
let refreshQueued = false

const selectedId = computed(() => production.value?.id || '')
const isRunning = computed(() => production.value?.status === 'running')
const canStart = computed(
  () =>
    Boolean(upload.value?.id && brief.value.trim()) &&
    !uploading.value &&
    !starting.value &&
    !rerunning.value &&
    !isRunning.value,
)
const currentStage = computed(() => production.value?.current_stage || '')
const stages = computed(() => {
  const active = currentStage.value
  const activeIndex = STAGE_ORDER.indexOf(active)
  return STAGE_ORDER.map((id, index) => ({
    id,
    label: STAGE_LABELS[id] || id,
    state:
      production.value?.status === 'failed' && id === active
        ? 'failed'
        : active === 'done' || (activeIndex >= 0 && index < activeIndex)
          ? 'done'
          : id === active
            ? 'active'
            : 'pending',
  }))
})
const segments = computed(() => production.value?.decomposition?.segments || [])
const references = computed(() => production.value?.intent?.references || [])
const shots = computed(() => production.value?.script?.shots || [])
const clips = computed(() => production.value?.clips || [])
const output = computed(() => production.value?.output || {})
function withRevision(url) {
  if (!url) return ''
  const rev = production.value?.updated_at
  if (!rev) return url
  return `${url}${url.includes('?') ? '&' : '?'}t=${encodeURIComponent(rev)}`
}
const videoUrl = computed(() => withRevision(output.value.video_url || ''))
const srtUrl = computed(() => withRevision(output.value.srt_url || ''))
const POLICY_MODES = [
  { id: 'default', label: '默认', hint: '只新生成开场和结尾，中间尽量复用' },
  { id: 'all', label: '全部新生成', hint: '每一段都重新生成' },
  { id: 'brief', label: '按创作说明判断', hint: '从创作说明里解析要重做的片段' },
]
const replacePlan = computed(() => production.value?.replace_plan || null)
const selectedSegmentIds = computed(() =>
  segments.value.map((segment) => segment.id).filter((id) => regenSelected.value[id]),
)
const canRerunSelected = computed(
  () =>
    Boolean(selectedId.value) &&
    production.value?.status === 'completed' &&
    selectedSegmentIds.value.length > 0 &&
    !isRunning.value &&
    !starting.value &&
    !rerunning.value,
)

const policyLabel = computed(
  () => POLICY_MODES.find((item) => item.id === policyMode.value)?.label || '',
)
const sourceAspect = computed(() => {
  const source = production.value?.output || production.value?.source_video || upload.value || {}
  const width = Number(source.width) || 0
  const height = Number(source.height) || 0
  return width && height ? { width, height } : playerAspect.value
})
const hasProduction = computed(() => Boolean(production.value?.id))

function statusLabel(status) {
  return STATUS_LABELS[status] || status || ''
}

function historyLabel(item) {
  const titleText = String(item?.title || '').trim()
  const briefText = String(item?.brief || '').trim()
  const raw = titleText || briefText || item?.id || ''
  if (titleText && briefText && titleText === briefText) return compactTitle(titleText, item.id)
  return raw
}

function historyTitleAttr(item) {
  return String(item?.title || item?.brief || item?.id || '')
}

function uploadSizeLabel(file) {
  const bytes = pickPositiveBytes(file?.size, file?.file_size, file?.bytes, file?.byte_size)
  return bytes ? formatBytes(bytes) : ''
}

function aspectStyle(dims) {
  const width = Number(dims?.width) || 0
  const height = Number(dims?.height) || 0
  if (!width || !height) return {}
  return { '--player-aspect': `${width} / ${height}` }
}

function aspectClass(dims) {
  const width = Number(dims?.width) || 0
  const height = Number(dims?.height) || 0
  if (!width || !height) return 'player-frame'
  return height > width ? 'player-frame is-portrait' : 'player-frame is-landscape'
}

function capturePlayerAspect(event) {
  const el = event?.target
  const width = Number(el?.videoWidth) || 0
  const height = Number(el?.videoHeight) || 0
  if (width && height) playerAspect.value = { width, height }
}

function captureClipAspect(event, key) {
  const el = event?.target
  const width = Number(el?.videoWidth) || 0
  const height = Number(el?.videoHeight) || 0
  if (!key || !width || !height) return
  clipAspects.value = { ...clipAspects.value, [key]: { width, height } }
}

function clipAspect(clip) {
  const key = clip?.shot_id || clip?.path
  return clipAspects.value[key] || sourceAspect.value || {
    width: Number(clip?.width) || 0,
    height: Number(clip?.height) || 0,
  }
}

function showReuseHint(segment) {
  return Boolean(segment?.reuse_recommended) && !segmentAction(segment)
}

async function resolveSourceBytes(payload, current) {
  const source = payload?.source_video || {}
  const inherited =
    current && (!source.upload_id || current.id === source.upload_id)
      ? pickPositiveBytes(current.size, current.file_size)
      : 0
  const known = pickPositiveBytes(source.size, source.file_size, source.bytes, source.byte_size, inherited)
  if (known) return known
  if (!payload?.id) return 0
  const name = source.path ? String(source.path).split(/[\\/]/).pop() : ''
  const candidates = name ? [name] : ['source.mp4', 'source.mov', 'source.webm', 'source.m4v']
  for (const file of candidates) {
    try {
      const response = await fetch(productionArtifactUrl(payload.id, file), { method: 'HEAD' })
      const length = Number(response.headers.get('content-length'))
      if (response.ok && Number.isFinite(length) && length > 0) return length
    } catch {
      // Artifact may use a different suffix; try the next candidate.
    }
  }
  return 0
}


function roleLabel(role) {
  return ROLE_LABELS[role] || role || ''
}

function actionLabel(action) {
  return ACTION_LABELS[action] || action || ''
}

function segmentAction(segment) {
  const id = segment?.id
  if (!id) return ''
  const resolved = replacePlan.value?.resolved_segment_ids
  if (Array.isArray(resolved)) {
    return resolved.includes(id) ? 'generate' : 'reuse'
  }
  const shot = shots.value.find((item) => item.source_segment_id === id)
  if (shot?.action) return shot.action
  return ''
}

function setRegen(segmentId, checked) {
  regenSelected.value = { ...regenSelected.value, [segmentId]: Boolean(checked) }
}

function syncComposerFrom(payload, { resetSelection = false } = {}) {
  const mode = payload?.replace_policy?.mode
  if (['default', 'all', 'brief'].includes(mode)) {
    policyMode.value = mode
  } else if (resetSelection) {
    policyMode.value = 'default'
  }
  if (resetSelection) regenSelected.value = {}
}

function clipUrl(clip) {
  if (clip?.url) return clip.url
  if (!production.value?.id || !clip?.path) return ''
  const name = String(clip.path).split('/').pop()
  return name ? productionArtifactUrl(production.value.id, name) : ''
}

function frameUrl(segmentId, name) {
  if (!production.value?.id) return ''
  return productionArtifactUrl(production.value.id, name)
}

function hideFrame(key) {
  hiddenFrames.value = { ...hiddenFrames.value, [key]: true }
}

function closeStream() {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
  if (refreshTimer) {
    clearTimeout(refreshTimer)
    refreshTimer = null
  }
  refreshQueued = false
}

function scheduleRefresh() {
  refreshQueued = true
  if (refreshTimer) return
  refreshTimer = setTimeout(async () => {
    refreshTimer = null
    if (!refreshQueued || !selectedId.value) return
    refreshQueued = false
    await loadProduction(selectedId.value, { quiet: true })
  }, 160)
}

function rememberEventId(event) {
  const raw = event?.id ?? event?.lastEventId
  const value = Number(raw)
  if (Number.isFinite(value) && value > lastEventId) lastEventId = value
}

function handleStreamEvent(event) {
  try {
    const data = event.data ? JSON.parse(event.data) : {}
    rememberEventId({ id: data.id, lastEventId: event.lastEventId })
    scheduleRefresh()
  } catch {
    scheduleRefresh()
  }
}

function connectStream(productionId, afterId = 0) {
  closeStream()
  lastEventId = afterId
  const source = new EventSource(productionEventUrl(productionId, afterId))
  eventSource = source
  source.onmessage = handleStreamEvent
  for (const type of EVENT_TYPES) {
    source.addEventListener(type, handleStreamEvent)
  }
  source.onerror = () => {
    if (production.value?.status === 'running') scheduleRefresh()
  }
}

async function loadHistory() {
  historyLoading.value = true
  try {
    const payload = await api.productions()
    history.value = payload.items || payload || []
  } catch (err) {
    if (!production.value) {
      error.value = err instanceof Error ? err.message : String(err)
    }
  } finally {
    historyLoading.value = false
  }
}

async function loadProduction(id, { quiet = false, connect = true } = {}) {
  if (!id) return
  try {
    const payload = await api.production(id)
    const switched = payload.id !== production.value?.id
    production.value = payload
    title.value = payload.title || ''
    brief.value = payload.brief || ''
    syncComposerFrom(payload, { resetSelection: switched })
    const previousUpload = upload.value
    const size = await resolveSourceBytes(payload, previousUpload)
    upload.value = payload.source_video
      ? {
          id: payload.source_video.upload_id,
          name: payload.source_video.filename,
          duration_ms: payload.source_video.duration_ms,
          width: payload.source_video.width,
          height: payload.source_video.height,
          fps: payload.source_video.fps,
          size,
        }
      : previousUpload
    if (switched) {
      composerOpen.value = false
      playerAspect.value = null
      clipAspects.value = {}
    }
    window.localStorage.setItem(STORAGE_KEY, id)
    hiddenFrames.value = {}
    if (connect && payload.status === 'running') {
      connectStream(id, lastEventId)
    } else if (payload.status !== 'running') {
      closeStream()
    }
    await loadHistory()
  } catch (err) {
    if (!quiet) error.value = err instanceof Error ? err.message : String(err)
  }
}

function resetComposer() {
  closeStream()
  production.value = null
  lastEventId = 0
  title.value = ''
  brief.value = ''
  upload.value = null
  policyMode.value = 'default'
  regenSelected.value = {}
  error.value = ''
  starting.value = false
  rerunning.value = false
  hiddenFrames.value = {}
  composerOpen.value = true
  playerAspect.value = null
  clipAspects.value = {}
  window.localStorage.removeItem(STORAGE_KEY)
}

async function handleFiles(fileList) {
  const file = fileList?.[0]
  if (!file) return
  if (file.size > MAX_UPLOAD_BYTES) {
    error.value = '视频超过 500MB 上限'
    return
  }
  uploading.value = true
  error.value = ''
  try {
    upload.value = await api.uploadProductionVideo(file)
    emit('notify', `已上传 ${upload.value.name || file.name}`)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    uploading.value = false
    if (fileInput.value) fileInput.value.value = ''
  }
}

function onDrop(event) {
  dragOver.value = false
  handleFiles(event.dataTransfer?.files)
}

async function startProduction() {
  if (!canStart.value) return
  starting.value = true
  error.value = ''
  lastEventId = 0
  try {
    const created = await api.createProduction({
      title: title.value.trim(),
      brief: brief.value.trim(),
      video_upload_id: upload.value.id,
      replace_policy: { mode: policyMode.value || 'default' },
    })
    production.value = created
    composerOpen.value = false
    window.localStorage.setItem(STORAGE_KEY, created.id)
    await api.runProduction(created.id)
    await loadProduction(created.id)
    emit('notify', '已开始创作')
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
    if (production.value?.id) await loadProduction(production.value.id, { connect: true })
  } finally {
    starting.value = false
  }
}

async function rerunSelected() {
  if (!canRerunSelected.value) return
  rerunning.value = true
  error.value = ''
  lastEventId = 0
  try {
    await api.runProduction(selectedId.value, {
      replace_policy: {
        mode: 'selected',
        segment_ids: selectedSegmentIds.value,
      },
      resume_from: 'intent',
    })
    await loadProduction(selectedId.value)
    emit('notify', '已按选中片段重新生成并合成')
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
    if (selectedId.value) await loadProduction(selectedId.value, { connect: true })
  } finally {
    rerunning.value = false
  }
}

async function cancelProduction() {
  if (!selectedId.value) return
  try {
    await api.cancelProduction(selectedId.value)
    await loadProduction(selectedId.value, { connect: false })
    emit('notify', '已取消任务')
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

async function deleteProduction(id) {
  const target = id || selectedId.value
  if (!target) return
  try {
    await api.deleteProduction(target)
    if (selectedId.value === target) resetComposer()
    await loadHistory()
    emit('notify', '已删除任务')
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  }
}

onMounted(async () => {
  await loadHistory()
  const stored = window.localStorage.getItem(STORAGE_KEY)
  if (stored) await loadProduction(stored)
})

watch(
  () => production.value?.status,
  (status) => {
    if (status && status !== 'running') closeStream()
  },
)

onBeforeUnmount(closeStream)
</script>

<template>
  <Teleport to="#studio-history-rail">
    <aside class="studio-history">
      <div class="history-head">
        <h2>历史任务</h2>
        <button class="button subtle" type="button" data-testid="new-production" @click="resetComposer">
          新建
        </button>
      </div>
      <p v-if="historyLoading" class="page-muted">加载中…</p>
      <p v-else-if="!history.length" class="page-muted">还没有创作任务</p>
      <ol v-else class="history-list">
        <li v-for="item in history" :key="item.id">
          <button
            type="button"
            class="history-item"
            :class="{ active: item.id === selectedId }"
            :title="historyTitleAttr(item)"
            @click="loadProduction(item.id)"
          >
            <strong>{{ historyLabel(item) }}</strong>
            <span>{{ historyStatusText(item) }}</span>
          </button>
        </li>
      </ol>
    </aside>
  </Teleport>

  <section class="page-scroll" data-testid="video-studio-page">
    <div class="studio-main">
      <header v-if="!hasProduction" class="page-intro">
        <p>上传参考视频并填写创作说明。默认本地提供者无需 API Key，可跑完全程并下载可播放的成片。</p>
      </header>

      <p v-if="error" class="page-error">{{ error }}</p>

      <details
        class="studio-card studio-composer"
        data-testid="studio-composer"
        :open="composerOpen"
        @toggle="composerOpen = $event.target.open"
      >
        <summary class="composer-recap" data-testid="studio-composer-toggle">
          <template v-if="hasProduction">
            <strong>{{ upload?.name || upload?.filename || '参考视频' }}</strong>
            <span v-if="brief">{{ compactTitle(brief, '') }}</span>
            <span v-if="policyLabel">策略：{{ policyLabel }}</span>
            <em>展开编辑</em>
          </template>
          <template v-else>创作输入</template>
        </summary>

        <div class="studio-composer-body">
          <label
            class="dropzone"
            :class="{ 'is-over': dragOver, 'has-file': Boolean(upload) }"
            @dragenter.prevent="dragOver = true"
            @dragover.prevent="dragOver = true"
            @dragleave.prevent="dragOver = false"
            @drop.prevent="onDrop"
          >
            <input
              ref="fileInput"
              data-testid="studio-upload"
              type="file"
              hidden
              :accept="ACCEPT"
              :disabled="uploading || isRunning"
              @change="handleFiles($event.target.files)"
            />
            <template v-if="upload">
              <strong>{{ upload.name || upload.filename || '已上传参考视频' }}</strong>
              <span>
                <template v-if="uploadSizeLabel(upload)">{{ uploadSizeLabel(upload) }}</template>
                <template v-if="upload.duration_ms">
                  {{ uploadSizeLabel(upload) ? ' · ' : '' }}{{ formatDuration(upload.duration_ms) }}
                </template>
                <template v-if="upload.width && upload.height"> · {{ upload.width }}×{{ upload.height }}</template>
                <template v-if="upload.fps"> · {{ upload.fps }} fps</template>
              </span>
            </template>
            <template v-else>
              <strong>{{ uploading ? '正在上传…' : '拖放或选择参考视频' }}</strong>
              <span>mp4 / mov / webm，最大 500MB</span>
            </template>
          </label>

          <label class="field">
            标题（可选）
            <input v-model="title" type="text" maxlength="120" :disabled="isRunning" placeholder="例如：夜景航拍裂变" />
          </label>
          <label class="field">
            创作说明
            <textarea
              v-model="brief"
              data-testid="studio-brief"
              rows="6"
              maxlength="20000"
              :disabled="isRunning"
              placeholder="描述新视频要讲什么、给谁看、希望保留或重做哪些片段…"
            ></textarea>
          </label>
          <fieldset class="studio-policy" data-testid="studio-policy">
            <legend>替换策略</legend>
            <div class="studio-policy-options">
              <label
                v-for="option in POLICY_MODES"
                :key="option.id"
                :title="option.hint"
                :class="{ active: policyMode === option.id }"
              >
                <input
                  v-model="policyMode"
                  type="radio"
                  name="studio-policy"
                  :value="option.id"
                  :data-testid="`studio-policy-${option.id}`"
                  :disabled="isRunning"
                />
                {{ option.label }}
              </label>
            </div>
            <p class="page-muted">默认只新生成开场和结尾；全部新生成会重做每一段；按创作说明判断则从说明里解析要重做的片段。</p>
          </fieldset>
          <div class="studio-actions">
            <button
              class="button primary"
              type="button"
              data-testid="studio-start"
              :disabled="!canStart"
              @click="startProduction"
            >
              {{ starting ? '启动中…' : isRunning ? '创作中…' : '开始创作' }}
            </button>
            <button
              v-if="isRunning"
              class="button subtle"
              type="button"
              data-testid="studio-cancel"
              @click="cancelProduction"
            >
              取消
            </button>
            <button
              v-if="selectedId && !isRunning"
              class="button subtle"
              type="button"
              @click="deleteProduction(selectedId)"
            >
              删除任务
            </button>
          </div>
        </div>
      </details>

      <section v-if="production" class="studio-card" data-testid="studio-progress">
        <div class="status-row">
          <strong>{{ statusLabel(production.status) }}</strong>
          <span v-if="production.id" data-testid="studio-production-id">{{ production.id }}</span>
        </div>
        <ol class="stage-rail">
          <li v-for="stage in stages" :key="stage.id" :class="stage.state">
            <i></i>
            {{ stage.label }}
          </li>
        </ol>
        <p v-if="production.error" class="page-error">{{ production.error }}</p>
        <ul v-if="production.warnings?.length" class="warning-list">
          <li v-for="(warning, index) in production.warnings" :key="index">{{ warning }}</li>
        </ul>
      </section>

      <section v-if="videoUrl || srtUrl" class="studio-card studio-output" data-testid="studio-output">
        <h3>成片</h3>
        <div v-if="videoUrl" :class="aspectClass(sourceAspect)" :style="aspectStyle(sourceAspect)">
          <video
            :key="videoUrl"
            :src="videoUrl"
            controls
            preload="metadata"
            data-testid="studio-player"
            @loadedmetadata="capturePlayerAspect"
          ></video>
        </div>
        <div class="studio-actions">
          <a v-if="videoUrl" class="button primary" data-testid="studio-download-mp4" :href="videoUrl" download="final.mp4">
            下载 MP4
          </a>
          <a v-if="srtUrl" class="button subtle" data-testid="studio-download-srt" :href="srtUrl" download="final.srt">
            下载 SRT
          </a>
        </div>
      </section>

      <details
        v-if="production?.decomposition"
        class="studio-card studio-fold"
        :key="videoUrl ? 'decomp-done' : 'decomp-pending'"
        :open="!videoUrl"
      >
        <summary class="studio-fold-toggle">
          <h3>整片理解与拆解</h3>
          <span v-if="segments.length" class="page-muted">{{ segments.length }} 段</span>
        </summary>
        <p class="summary-text">{{ humanizeMsText(production.decomposition.summary) || '暂无整片摘要' }}</p>
        <p class="page-muted">
          提供者 {{ production.decomposition.provider || '—' }}
          <template v-if="production.decomposition.model"> · {{ production.decomposition.model }}</template>
        </p>
        <p v-if="replacePlan" class="page-muted" data-testid="studio-replace-plan">
          本轮新生成：{{ (replacePlan.resolved_segment_ids || []).join('、') || '无' }}
        </p>
        <ol class="segment-list">
          <li
            v-for="segment in segments"
            :key="segment.id"
            class="segment-item"
            :data-testid="`studio-segment-${segment.id}`"
          >
            <header>
              <strong>{{ roleLabel(segment.role) }}</strong>
              <span class="segment-time">{{ formatTimecode(segment.start_ms) }} – {{ formatTimecode(segment.end_ms) }}</span>
              <em
                v-if="segmentAction(segment)"
                class="segment-action"
                :class="segmentAction(segment)"
                :data-testid="`studio-segment-action-${segment.id}`"
              >
                {{ actionLabel(segmentAction(segment)) }}
              </em>
              <em v-if="showReuseHint(segment)" class="segment-hint">建议复用</em>
              <label class="segment-regen">
                <input
                  type="checkbox"
                  :data-testid="`studio-segment-regen-${segment.id}`"
                  :disabled="isRunning || rerunning"
                  :checked="Boolean(regenSelected[segment.id])"
                  @change="setRegen(segment.id, $event.target.checked)"
                />
                重新生成
              </label>
            </header>
            <p v-if="segment.summary">{{ humanizeMsText(segment.summary) }}</p>
            <p v-if="segment.visual && !isTechnicalVisual(segment.visual)" class="page-muted">画面：{{ segment.visual }}</p>
            <p v-if="segment.audio" class="page-muted">声音：{{ segment.audio }}</p>
            <p v-if="segment.on_screen_text" class="page-muted">字幕：{{ segment.on_screen_text }}</p>
            <div class="frame-row">
              <img
                v-for="name in segmentFrameNames(segment.id)"
                v-show="!hiddenFrames[`${segment.id}:${name}`]"
                :key="name"
                :src="frameUrl(segment.id, name)"
                :alt="`${segment.id} 关键帧`"
                @error="hideFrame(`${segment.id}:${name}`)"
              />
            </div>
          </li>
        </ol>
        <div class="studio-rerun-row">
          <button
            class="button primary"
            type="button"
            data-testid="studio-rerun-selected"
            :disabled="!canRerunSelected"
            @click="rerunSelected"
          >
            {{ rerunning ? '重新生成中…' : '按选中片段重新生成并合成' }}
          </button>
          <p class="page-muted">勾选要重做的片段后，将从意图阶段重跑并覆盖成片。</p>
        </div>
      </details>

      <section v-if="clips.length" class="studio-card">
        <h3>片段预览</h3>
        <div class="clip-grid">
          <figure v-for="clip in clips" :key="clip.shot_id || clip.path">
            <div v-if="clipUrl(clip)" :class="aspectClass(clipAspect(clip))" :style="aspectStyle(clipAspect(clip))">
              <video
                :src="clipUrl(clip)"
                controls
                preload="metadata"
                @loadedmetadata="captureClipAspect($event, clip.shot_id || clip.path)"
              ></video>
            </div>
            <figcaption>
              {{ clip.shot_id }} · {{ clip.provider || '—' }} · {{ formatDuration(clip.duration_ms) }}
            </figcaption>
          </figure>
        </div>
      </section>

      <section v-if="production?.intent || production?.skeleton" class="studio-card">
        <h3>意图与骨架</h3>
        <dl v-if="production.intent" class="intent-grid">
          <div>
            <dt>目标</dt>
            <dd>{{ production.intent.goal || '—' }}</dd>
          </div>
          <div>
            <dt>受众</dt>
            <dd>{{ production.intent.audience || '—' }}</dd>
          </div>
          <div>
            <dt>承诺</dt>
            <dd>{{ production.intent.promise || '—' }}</dd>
          </div>
        </dl>
        <p v-if="production.intent?.assumptions?.length" class="page-muted">
          假设：{{ production.intent.assumptions.join('；') }}
        </p>
        <ul v-if="references.length" class="reference-list">
          <li v-for="item in references" :key="item.path">
            <strong :title="item.path">{{ referenceLabel(item.path) }}</strong>
            <span v-if="item.why">{{ item.why }}</span>
          </li>
        </ul>
        <div v-if="production.skeleton" class="skeleton-block">
          <div class="skeleton-oneliner">
            <span>一行骨架</span>
            <p>{{ production.skeleton.one_liner || '—' }}</p>
          </div>
          <dl class="skeleton-beats">
            <div>
              <dt>Hook</dt>
              <dd>{{ production.skeleton.hook || '—' }}</dd>
            </div>
            <div>
              <dt>Value</dt>
              <dd>{{ production.skeleton.value || '—' }}</dd>
            </div>
            <div>
              <dt>CTA</dt>
              <dd>{{ production.skeleton.cta || '—' }}</dd>
            </div>
          </dl>
        </div>
      </section>

      <section v-if="shots.length" class="studio-card">
        <h3>分镜脚本</h3>
        <p class="page-muted">
          总时长 {{ formatDuration(production.script?.total_duration_ms) }}
          <template v-if="production.script?.audio_plan">
            · 配音 {{ production.script.audio_plan.voiceover_language || '—' }}
            · BGM {{ production.script.audio_plan.bgm || '—' }}
            · 字幕 {{ production.script.audio_plan.subtitles ? '开' : '关' }}
          </template>
        </p>
        <div class="table-wrap">
          <table class="shot-table">
            <thead>
              <tr>
                <th>镜头</th>
                <th>角色</th>
                <th>时长</th>
                <th>动作</th>
                <th>画面 / 文案</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="shot in shots" :key="shot.id">
                <td>{{ shot.id }}</td>
                <td class="nowrap">{{ roleLabel(shot.role) }}</td>
                <td class="nowrap">{{ formatDuration(shot.duration_ms) }}</td>
                <td class="nowrap">
                  {{ actionLabel(shot.action) }}
                  <span v-if="shot.source_segment_id"> · {{ shot.source_segment_id }}</span>
                </td>
                <td>
                  <p v-if="shot.on_screen_text">字幕：{{ shot.on_screen_text }}</p>
                  <p v-if="shot.voiceover">口播：{{ shot.voiceover }}</p>
                  <p v-if="shot.visual && !isTechnicalVisual(shot.visual)">{{ shot.visual }}</p>
                  <details v-if="shot.prompt" class="shot-prompt">
                    <summary>生成提示</summary>
                    <p>{{ shot.prompt }}</p>
                  </details>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </div>
  </section>
</template>
