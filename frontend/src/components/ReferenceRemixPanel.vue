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

const emit = defineEmits(['close', 'notify'])

const remix = ref(null)
const loading = ref(false)
const rendering = ref(false)
const error = ref('')
const middleOrder = ref([])
const activePlanSignature = ref('')
const activeUploadId = ref('')
let pollTimer = null
let requestSequence = 0

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
  if (isAwaitingOrder.value) return '确认中间片段顺序'
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
  if (!props.open || !remix.value?.id || !isRunning.value) return
  pollTimer = setTimeout(() => loadRemix(remix.value.id, { silent: true }), delay)
}

function setPlanOrder(current) {
  const signature = `${current?.id || ''}:${current?.updated_at || ''}`
  if (!current || signature === activePlanSignature.value) return
  activePlanSignature.value = signature
  middleOrder.value = (current.remix_plan?.middle_order || []).map(String)
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

async function renderRemix() {
  if (!remix.value?.id || !canRender.value) return
  rendering.value = true
  error.value = ''
  try {
    const current = await api.renderRemix(remix.value.id, {
      strategy: 'manual',
      middle_order: middleOrder.value,
    })
    remix.value = current
    setPlanOrder(current)
    schedulePoll()
    emit('notify', '正在拼接原片片段')
  } catch (err) {
    error.value = err.message
    await loadRemix(remix.value.id, { silent: true })
  } finally {
    rendering.value = false
  }
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
  await ensureRemix()
}

function close() {
  requestSequence += 1
  loading.value = false
  stopPolling()
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
        remix.value = null
        activePlanSignature.value = ''
        middleOrder.value = []
        error.value = ''
      }
      document.addEventListener('keydown', handleKeydown)
      ensureRemix()
    } else {
      document.removeEventListener('keydown', handleKeydown)
      stopPolling()
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
          data-testid="reference-remix-panel"
        >
          <header class="remix-panel-head">
            <div>
              <span class="remix-kicker">原片结构</span>
              <h2 id="remix-panel-title">{{ statusTitle }}</h2>
              <p>保留开头和结尾，只调整中间 value 片段。成片只使用原片片段，不调用视频生成模型。</p>
            </div>
            <button class="icon-button" type="button" aria-label="关闭原片重排" @click="close">
              <X :size="18" />
            </button>
          </header>

          <div v-if="isRunning || loading" class="remix-running" aria-live="polite">
            <LoaderCircle class="is-spinning" :size="20" />
            <div>
              <strong>{{ statusTitle }}</strong>
              <span>
                {{
                  remix?.current_stage === 'decompose'
                    ? '正在识别段落、开头、结尾和中间价值片段'
                    : '正在裁切并重新拼接片段，保持原片音轨'
                }}
              </span>
            </div>
            <button v-if="isRunning" class="button subtle" type="button" @click="cancelRemix">
              取消
            </button>
          </div>

          <template v-else>
            <div class="remix-source-row">
              <video
                class="remix-source-video"
                :src="video.previewUrl || video.url"
                controls
                preload="metadata"
              ></video>
              <div class="remix-source-copy">
                <strong>{{ video.name }}</strong>
                <span>{{ formatDuration(video.duration_ms) }}</span>
                <p>{{ remix?.summary || '正在读取原片结构' }}</p>
                <small v-if="remix?.provider">
                  片段理解：{{ remix.provider }}{{ remix.model ? ` / ${remix.model}` : '' }}
                </small>
              </div>
            </div>

            <section
              v-if="isAwaitingOrder || isCompleted || (status === 'failed' && orderedMiddle.length)"
              class="remix-timeline"
            >
              <div class="remix-bookend">
                <span>开头锁定</span>
                <strong>
                  {{ formatTimecode(plan.opening?.start_ms || 0) }}
                  <template>至</template>
                  {{ formatTimecode(plan.opening?.end_ms || 0) }}
                </strong>
                <small>{{ formatDuration((plan.opening?.end_ms || 0) - (plan.opening?.start_ms || 0)) }}</small>
              </div>

              <div class="remix-middle-head">
                <div>
                  <strong>中间片段</strong>
                  <span>调整顺序后生成一条成片</span>
                </div>
                <span class="remix-middle-count">{{ orderedMiddle.length }} 段</span>
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
                    <span>{{ unit.role === 'value' ? 'Value' : unit.role }}</span>
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
                      :disabled="!canMoveMiddle(index, -1)"
                      @click="moveMiddle(index, -1)"
                    >
                      <ArrowUp :size="14" />
                    </button>
                    <button
                      class="icon-button compact"
                      type="button"
                      :aria-label="`下移 ${unit.id}`"
                      :disabled="!canMoveMiddle(index, 1)"
                      @click="moveMiddle(index, 1)"
                    >
                      <ArrowDown :size="14" />
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
                <small>{{ formatDuration((plan.ending?.end_ms || 0) - (plan.ending?.start_ms || 0)) }}</small>
              </div>
            </section>

            <section v-if="outputUrl" class="remix-output" data-testid="reference-remix-output">
              <header>
                <span><Check :size="15" /> 成片已生成</span>
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

            <p v-if="error || remix?.error" class="page-error">
              {{ error || remix?.error }}
            </p>
            <ul v-if="remix?.warnings?.length" class="warning-list">
              <li v-for="(warning, index) in remix.warnings" :key="index">{{ warning }}</li>
            </ul>

            <footer class="remix-panel-actions">
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
                v-if="
                  isAwaitingOrder ||
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
                        : '生成重排视频'
                }}
              </button>
            </footer>
          </template>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>
