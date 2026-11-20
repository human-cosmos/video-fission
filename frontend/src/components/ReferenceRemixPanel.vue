<script setup>
import {
  ArrowDown,
  ArrowUp,
  Check,
  Download,
  Film,
  LoaderCircle,
  RefreshCw,
  Shuffle,
  X,
} from '@lucide/vue'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../api'
import { formatDuration, formatTimecode } from '../utils/format'
import AspectVideo from './AspectVideo.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  video: { type: Object, default: null },
})

const emit = defineEmits(['close', 'notify', 'task-update'])

const remix = ref(null)
const loading = ref(false)
const rendering = ref(false)
const error = ref('')
const middleOrder = ref([])
const orderBaseline = ref([])
const activePlanSignature = ref('')
const activeUploadId = ref('')
let pollTimer = null
let requestSequence = 0
let autoRenderAttemptId = ''

const status = computed(() => String(remix.value?.status || ''))
const plan = computed(() => remix.value?.remix_plan || {})
const segments = computed(() => remix.value?.segments || [])
const unitById = computed(
  () => new Map((plan.value.middle_units || []).map((unit) => [String(unit.id), unit])),
)
const orderedMiddle = computed(() => {
  return middleOrder.value.map((id) => unitById.value.get(String(id))).filter(Boolean)
})
const outputUrl = computed(() => remix.value?.output?.video_url || '')
const isRunning = computed(() => status.value === 'running')
const isAwaitingOrder = computed(() => status.value === 'awaiting_order')
const isCompleted = computed(() => status.value === 'completed')
const orderAdjusted = computed(
  () => JSON.stringify(middleOrder.value) !== JSON.stringify(orderBaseline.value),
)
const orderNotice = computed(() => {
  if (orderAdjusted.value) {
    return {
      title: '顺序已调整，尚未重新拼接',
      detail: '点击下方按钮，按当前顺序生成新成片',
    }
  }
  if (isCompleted.value) {
    return {
      title: '当前成片已按此顺序生成',
      detail: '你可以继续调整，再按当前顺序重新拼接',
    }
  }
  return {
    title: '当前顺序已按分析结果打乱',
    detail: '系统将直接按此顺序拼接，你仍可在成片后调整',
  }
})
const canRender = computed(
  () =>
    Boolean(remix.value?.id) &&
    middleOrder.value.length > 1 &&
    !loading.value &&
    !rendering.value &&
    !isRunning.value,
)
const statusTitle = computed(() => {
  if (loading.value) return '正在准备原片'
  if (isRunning.value) {
    return remix.value?.current_stage === 'decompose'
      ? '正在理解原片片段'
      : '正在拼接原片'
  }
  if (isAwaitingOrder.value) return '正在按默认顺序拼接'
  if (isCompleted.value) return '原片重排已完成'
  if (status.value === 'failed') return '原片重排失败'
  if (status.value === 'cancelled') return '原片重排已取消'
  return '原片重排'
})

function storageKey(uploadId) {
  return `video-fission.remix.${uploadId || 'unknown'}`
}

function stopPolling() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

function schedulePoll(delay = 900) {
  stopPolling()
  if (!remix.value?.id || !isRunning.value) return
  pollTimer = setTimeout(() => loadRemix(remix.value.id, { silent: true }), delay)
}

function setPlanOrder(current) {
  const signature = `${current?.id || ''}:${current?.updated_at || ''}`
  if (!current || signature === activePlanSignature.value) return
  activePlanSignature.value = signature
  middleOrder.value = (current.remix_plan?.middle_order || []).map(String)
  orderBaseline.value = [...middleOrder.value]
}

async function loadRemix(remixId, { silent = false } = {}) {
  if (!remixId) return
  if (!silent) loading.value = true
  try {
    const current = await api.remix(remixId)
    remix.value = current
    setPlanOrder(current)
    error.value = current.error || ''
    schedulePoll()
  } catch (err) {
    if (!silent) {
      if (err.status === 404) {
        remix.value = null
        window.localStorage.removeItem(storageKey(props.video?.id))
      }
    }
    error.value = silent ? '进度刷新失败，正在重试' : err.message
    if (silent) schedulePoll(1800)
  } finally {
    if (!silent) loading.value = false
  }
}

async function ensureRemix() {
  if (!props.open || !props.video?.id) return
  const sequence = ++requestSequence
  stopPolling()
  loading.value = true
  error.value = ''
  try {
    const savedId = window.localStorage.getItem(storageKey(props.video.id))
    if (savedId) {
      try {
        const current = await api.remix(savedId)
        if (sequence !== requestSequence) return
        remix.value = current
        setPlanOrder(current)
        error.value = current.error || ''
        if (current.status === 'running') schedulePoll()
        return
      } catch (err) {
        if (err.status !== 404) {
          error.value = err.message
          return
        }
        window.localStorage.removeItem(storageKey(props.video.id))
      }
    }
    const created = await api.createRemix({
      video_upload_id: props.video.id,
      title: props.video.name ? `${props.video.name} 原片重排` : null,
    })
    if (sequence !== requestSequence) return
    remix.value = created
    window.localStorage.setItem(storageKey(props.video.id), created.id)
    setPlanOrder(created)
    error.value = created.error || ''
    if (created.status === 'running') schedulePoll()
  } catch (err) {
    if (sequence === requestSequence) error.value = err.message
  } finally {
    if (sequence === requestSequence) loading.value = false
  }
}

function targetValueIndex(index, direction) {
  let cursor = index + direction
  while (cursor >= 0 && cursor < middleOrder.value.length) {
    const unit = unitById.value.get(String(middleOrder.value[cursor]))
    if (unit?.role === 'value') return cursor
    cursor += direction
  }
  return -1
}

function canMoveMiddle(index, direction) {
  const unit = unitById.value.get(String(middleOrder.value[index]))
  return unit?.role === 'value' && targetValueIndex(index, direction) >= 0
}

function moveMiddle(index, direction) {
  const target = targetValueIndex(index, direction)
  if (target < 0) return
  const next = [...middleOrder.value]
  ;[next[index], next[target]] = [next[target], next[index]]
  middleOrder.value = next
}

async function startRender(automatic = false) {
  if (!remix.value?.id || !canRender.value) return
  const remixId = remix.value.id
  const videoId = props.video?.id
  rendering.value = true
  error.value = ''
  try {
    const current = await api.renderRemix(remixId, {
      strategy: 'manual',
      middle_order: middleOrder.value,
    })
    if (remix.value?.id !== remixId || props.video?.id !== videoId) return
    remix.value = current
    setPlanOrder(current)
    schedulePoll()
    emit(
      'notify',
      automatic ? '原片分析完成，正在按默认顺序拼接' : '正在拼接原片片段',
    )
  } catch (err) {
    if (remix.value?.id !== remixId || props.video?.id !== videoId) return
    const message = err.message
    await loadRemix(remixId, { silent: true })
    if (
      remix.value?.id === remixId &&
      props.video?.id === videoId &&
      remix.value.status === 'awaiting_order' &&
      !remix.value.error
    ) {
      error.value = message
    }
  } finally {
    rendering.value = false
  }
}

async function renderRemix() {
  await startRender(false)
}

async function autoRenderDefaultOrder() {
  const current = remix.value
  if (
    !props.open ||
    !current?.id ||
    current.status !== 'awaiting_order' ||
    middleOrder.value.length < 2 ||
    loading.value ||
    rendering.value ||
    autoRenderAttemptId === current.id
  ) {
    return
  }
  autoRenderAttemptId = current.id
  await startRender(true)
}

async function cancelRemix() {
  if (!remix.value?.id) return
  try {
    remix.value = await api.cancelRemix(remix.value.id)
    stopPolling()
    emit('notify', '已取消原片重排')
  } catch (err) {
    error.value = err.message
    await loadRemix(remix.value.id, { silent: true })
  }
}

async function restartRemix() {
  const id = remix.value?.id
  if (id) {
    try {
      await api.deleteRemix(id)
    } catch {
      // A failed task may already have been removed.
    }
  }
  window.localStorage.removeItem(storageKey(props.video?.id))
  remix.value = null
  activePlanSignature.value = ''
  middleOrder.value = []
  orderBaseline.value = []
  await ensureRemix()
}

function close() {
  emit('close')
}

function segmentFor(unit) {
  return segments.value.find(
    (segment) => String(segment.id) === String(unit?.source_segment_id),
  )
}

function unitFrame(unit) {
  const segment = segmentFor(unit)
  return segment?.frame_urls?.[0] || ''
}

function unitClip(unit) {
  const segment = segmentFor(unit)
  return segment?.clip_url || ''
}

function handleKeydown(event) {
  if (props.open && event.key === 'Escape') close()
}

watch(
  () => [props.open, props.video?.id],
  ([open, videoId]) => {
    if (open) {
      if (!videoId) return
      if (activeUploadId.value !== videoId) {
        activeUploadId.value = videoId
        autoRenderAttemptId = ''
        remix.value = null
        activePlanSignature.value = ''
        middleOrder.value = []
        orderBaseline.value = []
        error.value = ''
      }
      document.addEventListener('keydown', handleKeydown)
      ensureRemix()
    } else {
      autoRenderAttemptId = ''
      document.removeEventListener('keydown', handleKeydown)
    }
  },
  { immediate: true },
)

watch(
  remix,
  (current) => {
    emit(
      'task-update',
      current
        ? {
            id: current.id,
            title: current.title,
            status: current.status,
            current_stage: current.current_stage,
            error: current.error,
            source_video: current.source_video,
            output: current.output,
          }
        : null,
    )
  },
)

watch(
  [isAwaitingOrder, loading, rendering, () => middleOrder.value.length],
  ([awaitingOrder, isLoading, isRendering, middleCount]) => {
    if (
      awaitingOrder &&
      !isLoading &&
      !isRendering &&
      middleCount > 1
    ) {
      autoRenderDefaultOrder()
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  document.removeEventListener('keydown', handleKeydown)
  stopPolling()
})
</script>

<template>
  <Teleport to="body">
    <Transition name="remix-modal">
      <div
        v-if="open && video"
        class="remix-backdrop"
        role="presentation"
        @mousedown.self="close"
      >
        <section
          class="remix-panel"
          role="dialog"
          aria-modal="true"
          aria-labelledby="remix-panel-title"
          aria-describedby="remix-panel-description"
          data-testid="reference-remix-panel"
        >
          <header class="remix-panel-head">
            <div class="remix-panel-heading">
              <span class="remix-title-icon" aria-hidden="true">
                <Shuffle :size="17" />
              </span>
              <div>
                <span class="remix-kicker">原片结构</span>
                <h2 id="remix-panel-title">{{ statusTitle }}</h2>
                <p id="remix-panel-description">
                  开头与结尾保持锁定，只重排中间片段并使用原片音轨。
                </p>
              </div>
            </div>
            <button
              class="icon-button remix-close"
              type="button"
              aria-label="关闭原片重排"
              @click="close"
            >
              <X :size="18" />
            </button>
          </header>

          <div class="remix-panel-body">
            <div v-if="isRunning || loading" class="remix-running" aria-live="polite">
              <span class="remix-running-icon" aria-hidden="true">
                <LoaderCircle class="is-spinning" :size="22" />
              </span>
              <div class="remix-running-copy">
                <strong>{{ statusTitle }}</strong>
                <span>
                  {{
                    remix?.current_stage === 'decompose'
                      ? '正在识别段落、开头、结尾和中间价值片段'
                      : '正在裁切并重新拼接片段，保持原片音轨'
                  }}
                </span>
                <div class="remix-stage-track" aria-hidden="true">
                  <span
                    class="remix-stage-step"
                    :class="{
                      'is-active': remix?.current_stage === 'decompose',
                      'is-done': remix?.current_stage && remix.current_stage !== 'decompose',
                    }"
                  >
                    <b>1</b>
                    理解片段
                  </span>
                  <span class="remix-stage-line"></span>
                  <span
                    class="remix-stage-step"
                    :class="{
                      'is-active': remix?.current_stage && remix.current_stage !== 'decompose',
                    }"
                  >
                    <b>2</b>
                    拼接成片
                  </span>
                </div>
              </div>
              <button
                v-if="isRunning"
                class="button subtle remix-cancel"
                type="button"
                @click="cancelRemix"
              >
                取消任务
              </button>
            </div>

            <template v-else>
              <div class="remix-workspace">
                <aside class="remix-source-panel">
                  <div class="remix-source-media">
                    <video
                      class="remix-source-video"
                      :src="video.previewUrl || video.url"
                      controls
                      preload="metadata"
                    ></video>
                    <span class="remix-source-time">
                      {{ formatDuration(video.duration_ms) }}
                    </span>
                  </div>
                  <div class="remix-source-copy">
                    <span class="remix-section-label">素材原片</span>
                    <strong>{{ video.name }}</strong>
                    <p>{{ remix?.summary || '正在读取原片结构' }}</p>
                    <small v-if="remix?.provider">
                      片段理解：{{ remix.provider }}{{ remix.model ? ` / ${remix.model}` : '' }}
                    </small>
                  </div>
                </aside>

                <section class="remix-order-panel">
                  <header class="remix-order-head">
                    <div>
                      <strong>{{ orderNotice.title }}</strong>
                      <span>{{ orderNotice.detail }}</span>
                    </div>
                    <span class="remix-middle-count">{{ orderedMiddle.length }} 段</span>
                  </header>

                  <section
                    v-if="isAwaitingOrder || isCompleted || (status === 'failed' && orderedMiddle.length)"
                    class="remix-timeline"
                  >
                    <div class="remix-bookend">
                      <span>开头锁定</span>
                      <strong>
                        {{ formatTimecode(plan.opening?.start_ms || 0) }}
                        至
                        {{ formatTimecode(plan.opening?.end_ms || 0) }}
                      </strong>
                      <small>
                        {{ formatDuration((plan.opening?.end_ms || 0) - (plan.opening?.start_ms || 0)) }}
                      </small>
                    </div>

                    <ol class="remix-unit-list">
                      <li
                        v-for="(unit, index) in orderedMiddle"
                        :key="unit.id"
                        class="remix-unit"
                        :data-testid="`remix-unit-${unit.id}`"
                      >
                        <span class="remix-unit-index">{{ String(index + 1).padStart(2, '0') }}</span>
                        <div class="remix-unit-media">
                          <img v-if="unitFrame(unit)" :src="unitFrame(unit)" :alt="unit.id" />
                          <AspectVideo
                            v-else-if="unitClip(unit)"
                            :src="unitClip(unit)"
                            :width="video.width"
                            :height="video.height"
                            compact
                          />
                          <Film v-else :size="20" />
                        </div>
                        <div class="remix-unit-copy">
                          <span>{{ unit.role === 'value' ? '可替换片段' : unit.role }}</span>
                          <strong>{{ unit.summary || unit.id }}</strong>
                          <small>
                            {{ formatTimecode(unit.start_ms) }} 至 {{ formatTimecode(unit.end_ms) }}
                            · {{ formatDuration(unit.end_ms - unit.start_ms) }}
                          </small>
                        </div>
                        <div class="remix-unit-actions">
                          <button
                            class="icon-button compact"
                            type="button"
                            :aria-label="`上移 ${unit.id}`"
                            :title="canMoveMiddle(index, -1) ? '上移片段' : '已经位于可移动范围顶部'"
                            :disabled="!canMoveMiddle(index, -1)"
                            @click="moveMiddle(index, -1)"
                          >
                            <ArrowUp :size="15" />
                          </button>
                          <button
                            class="icon-button compact"
                            type="button"
                            :aria-label="`下移 ${unit.id}`"
                            :title="canMoveMiddle(index, 1) ? '下移片段' : '已经位于可移动范围底部'"
                            :disabled="!canMoveMiddle(index, 1)"
                            @click="moveMiddle(index, 1)"
                          >
                            <ArrowDown :size="15" />
                          </button>
                        </div>
                      </li>
                    </ol>

                    <div class="remix-bookend remix-bookend-end">
                      <span>结尾锁定</span>
                      <strong>
                        {{ formatTimecode(plan.ending?.start_ms || 0) }}
                        至
                        {{ formatTimecode(plan.ending?.end_ms || 0) }}
                      </strong>
                      <small>
                        {{ formatDuration((plan.ending?.end_ms || 0) - (plan.ending?.start_ms || 0)) }}
                      </small>
                    </div>
                  </section>

                  <div v-if="!orderedMiddle.length" class="remix-order-empty">
                    <Film :size="22" />
                    <strong>等待片段结构</strong>
                    <span>原片拆解完成后，可调整的中间片段会显示在这里。</span>
                  </div>
                </section>
              </div>

              <section v-if="outputUrl" class="remix-output" data-testid="reference-remix-output">
                <header>
                  <div>
                    <span><Check :size="15" /> 成片已生成</span>
                    <small>当前顺序已完成拼接</small>
                  </div>
                  <a
                    class="button primary"
                    :href="outputUrl"
                    download="reference-remix.mp4"
                    data-testid="reference-remix-download"
                  >
                    <Download :size="14" />
                    下载 MP4
                  </a>
                </header>
                <AspectVideo
                  :src="outputUrl"
                  :width="remix?.output?.width || video.width"
                  :height="remix?.output?.height || video.height"
                  testid="reference-remix-player"
                />
              </section>

              <div v-if="error || remix?.error" class="remix-feedback">
                <p class="page-error">{{ error || remix?.error }}</p>
              </div>
              <ul v-if="remix?.warnings?.length" class="warning-list">
                <li v-for="(warning, index) in remix.warnings" :key="index">{{ warning }}</li>
              </ul>
            </template>
          </div>

          <footer v-if="!isRunning && !loading" class="remix-panel-actions">
            <span class="remix-action-note">
              {{
                isAwaitingOrder
                  ? '默认顺序拼接未启动，请重试'
                  : isCompleted
                    ? '重新拼接会按当前片段顺序生成新成片'
                    : '只重排片段，不重新生成画面'
              }}
            </span>
            <div class="remix-action-buttons">
              <button
                v-if="!remix && error"
                class="button subtle"
                type="button"
                @click="ensureRemix"
              >
                <RefreshCw :size="14" />
                重试
              </button>
              <button
                v-if="status === 'failed' || status === 'cancelled'"
                class="button subtle"
                type="button"
                @click="restartRemix"
              >
                <RefreshCw :size="14" />
                重新识别
              </button>
              <button
                v-if="isAwaitingOrder && error"
                class="button primary"
                type="button"
                :disabled="!canRender || rendering"
                @click="renderRemix"
              >
                <RefreshCw :size="14" />
                重试自动拼接
              </button>
              <button
                v-if="
                  isCompleted ||
                  (status === 'failed' && plan.ordered_units?.length)
                "
                class="button primary"
                type="button"
                :disabled="!canRender || rendering"
                data-testid="reference-remix-render"
                @click="renderRemix"
              >
                <Shuffle :size="14" />
                {{
                  rendering
                    ? '正在提交'
                    : status === 'failed'
                      ? '重试拼接'
                      : isCompleted
                        ? '按当前顺序重新拼接'
                        : '开始拼接'
                }}
              </button>
            </div>
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>
