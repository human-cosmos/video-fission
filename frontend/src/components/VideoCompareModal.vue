<script setup>
import { Pause, Play, Volume2, VolumeX, X } from '@lucide/vue'
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

const PLAYBACK_RATE = 2
const DRIFT_TOLERANCE = 0.15

const props = defineProps({
  open: { type: Boolean, default: false },
  sourceUrl: { type: String, default: '' },
  candidateUrl: { type: String, default: '' },
  candidateLabel: { type: String, default: '候选视频' },
  candidateTitle: { type: String, default: '' },
})

const emit = defineEmits(['close', 'closed'])

const sourceVideo = ref(null)
const candidateVideo = ref(null)
const closeButton = ref(null)
const dialog = ref(null)
const sourceReady = ref(false)
const candidateReady = ref(false)
const sourceDuration = ref(0)
const candidateDuration = ref(0)
const currentTime = ref(0)
const isPlaying = ref(false)
const audioEnabled = ref(false)
const error = ref('')

let syncFrame = 0

const ready = computed(() => sourceReady.value && candidateReady.value)
const maxDuration = computed(() => {
  const durations = [sourceDuration.value, candidateDuration.value].filter(
    (value) => Number.isFinite(value) && value > 0,
  )
  return durations.length ? Math.min(...durations) : 0
})
const durationLabel = computed(() => formatClock(maxDuration.value))

function formatClock(seconds) {
  const value = Math.max(0, Number(seconds) || 0)
  const minutes = Math.floor(value / 60)
  const remainder = Math.floor(value % 60)
  return `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
}

function mediaDuration(video) {
  const duration = Number(video?.duration)
  return Number.isFinite(duration) && duration > 0 ? duration : 0
}

function setRate(video) {
  if (!video) return
  video.defaultPlaybackRate = PLAYBACK_RATE
  video.playbackRate = PLAYBACK_RATE
}

function applyAudio() {
  if (sourceVideo.value) sourceVideo.value.muted = true
  if (candidateVideo.value) candidateVideo.value.muted = !audioEnabled.value
}

function cancelSyncLoop() {
  if (syncFrame) {
    cancelAnimationFrame(syncFrame)
    syncFrame = 0
  }
}

function syncDisplayedTime() {
  const source = sourceVideo.value
  const candidate = candidateVideo.value
  const sourceTime = Number(source?.currentTime) || 0
  const candidateTime = Number(candidate?.currentTime) || 0
  currentTime.value = Math.min(Math.max(sourceTime, candidateTime), maxDuration.value || Infinity)
}

function pausePlayback() {
  cancelSyncLoop()
  sourceVideo.value?.pause()
  candidateVideo.value?.pause()
  isPlaying.value = false
  syncDisplayedTime()
}

function finishPlayback() {
  cancelSyncLoop()
  sourceVideo.value?.pause()
  candidateVideo.value?.pause()
  isPlaying.value = false
  currentTime.value = maxDuration.value
}

function syncLoop() {
  if (!isPlaying.value) return
  const source = sourceVideo.value
  const candidate = candidateVideo.value
  if (!source || !candidate) {
    pausePlayback()
    return
  }

  const endTime = maxDuration.value
  if (
    endTime > 0 &&
    (source.currentTime >= endTime - 0.03 || candidate.currentTime >= endTime - 0.03)
  ) {
    finishPlayback()
    return
  }

  const drift = candidate.currentTime - source.currentTime
  if (Math.abs(drift) > DRIFT_TOLERANCE) {
    candidate.currentTime = Math.min(
      source.currentTime,
      candidateDuration.value || source.currentTime,
    )
  }
  currentTime.value = Math.min(source.currentTime, endTime || source.currentTime)
  syncFrame = requestAnimationFrame(syncLoop)
}

async function togglePlayback() {
  if (isPlaying.value) {
    pausePlayback()
    return
  }
  if (!ready.value || error.value) return

  const source = sourceVideo.value
  const candidate = candidateVideo.value
  if (!source || !candidate) return

  if (maxDuration.value && currentTime.value >= maxDuration.value - 0.03) {
    source.currentTime = 0
    candidate.currentTime = 0
    currentTime.value = 0
  }

  setRate(source)
  setRate(candidate)
  applyAudio()
  error.value = ''
  try {
    const results = await Promise.allSettled([source.play(), candidate.play()])
    if (results.some((result) => result.status === 'rejected')) {
      throw new Error('播放失败')
    }
    isPlaying.value = !source.paused && !candidate.paused
    if (isPlaying.value) syncFrame = requestAnimationFrame(syncLoop)
  } catch {
    pausePlayback()
    error.value = '视频暂时无法播放，请关闭后重试。'
  }
}

function seek(event) {
  const time = Math.max(0, Number(event.target.value) || 0)
  currentTime.value = time
  const source = sourceVideo.value
  const candidate = candidateVideo.value
  if (source) {
    source.currentTime = Math.min(time, sourceDuration.value || time)
  }
  if (candidate) {
    candidate.currentTime = Math.min(time, candidateDuration.value || time)
  }
}

function onLoadedMetadata(video, side) {
  if (side === 'source') {
    sourceReady.value = true
    sourceDuration.value = mediaDuration(video)
  } else {
    candidateReady.value = true
    candidateDuration.value = mediaDuration(video)
  }
  setRate(video)
  applyAudio()
}

function onMediaError() {
  error.value = '视频加载失败，请关闭后重试。'
  pausePlayback()
}

function toggleAudio() {
  audioEnabled.value = !audioEnabled.value
  applyAudio()
}

function close() {
  pausePlayback()
  emit('close')
}

function handleKeydown(event) {
  if (!props.open) return
  if (event.key === 'Escape') {
    event.preventDefault()
    close()
    return
  }
  if (event.key !== 'Tab' || !dialog.value) return

  const focusable = [...dialog.value.querySelectorAll(
    'button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
  )].filter((element) => !element.hasAttribute('hidden'))
  if (!focusable.length) return

  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  const active = document.activeElement
  if (event.shiftKey && (active === first || !dialog.value.contains(active))) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && (active === last || !dialog.value.contains(active))) {
    event.preventDefault()
    first.focus()
  }
}

function resetModal() {
  cancelSyncLoop()
  isPlaying.value = false
  sourceReady.value = false
  candidateReady.value = false
  sourceDuration.value = 0
  candidateDuration.value = 0
  currentTime.value = 0
  audioEnabled.value = false
  error.value = ''
  nextTick(() => {
    for (const video of [sourceVideo.value, candidateVideo.value]) {
      if (!video) continue
      video.pause()
      video.currentTime = 0
      video.muted = true
      setRate(video)
    }
    applyAudio()
    closeButton.value?.focus()
  })
}

watch(
  () => [props.open, props.sourceUrl, props.candidateUrl],
  ([open]) => {
    if (open) resetModal()
    else pausePlayback()
  },
  { immediate: true },
)

watch(
  () => props.open,
  (open) => {
    if (open) document.addEventListener('keydown', handleKeydown)
    else document.removeEventListener('keydown', handleKeydown)
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  cancelSyncLoop()
  document.removeEventListener('keydown', handleKeydown)
})
</script>

<template>
  <Teleport to="body">
    <Transition name="video-compare-modal" @after-leave="$emit('closed')">
      <div
        v-if="open"
        class="video-compare-backdrop"
        role="presentation"
        @mousedown.self="close"
      >
        <section
          ref="dialog"
          class="video-compare-dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby="video-compare-title"
          data-testid="video-compare-modal"
        >
          <header class="video-compare-head">
            <div>
              <span class="video-compare-kicker">{{ candidateLabel }}</span>
              <h2 id="video-compare-title">{{ candidateTitle || '原视频与候选视频对比' }}</h2>
            </div>
            <button
              ref="closeButton"
              class="icon-button"
              type="button"
              aria-label="关闭视频对比"
              data-testid="video-compare-close"
              @click="close"
            >
              <X :size="18" />
            </button>
          </header>

          <div class="video-compare-stage">
            <figure class="video-compare-item">
              <figcaption>
                <span>原视频</span>
                <small>基准</small>
              </figcaption>
              <div class="video-compare-player">
                <video
                  ref="sourceVideo"
                  :src="sourceUrl"
                  muted
                  playsinline
                  preload="metadata"
                  data-testid="video-compare-source"
                  @loadedmetadata="onLoadedMetadata($event.target, 'source')"
                  @error="onMediaError"
                ></video>
              </div>
            </figure>

            <figure class="video-compare-item">
              <figcaption>
                <span>候选视频</span>
                <small>{{ candidateLabel }}</small>
              </figcaption>
              <div class="video-compare-player">
                <video
                  ref="candidateVideo"
                  :src="candidateUrl"
                  muted
                  playsinline
                  preload="metadata"
                  data-testid="video-compare-candidate"
                  @loadedmetadata="onLoadedMetadata($event.target, 'candidate')"
                  @error="onMediaError"
                ></video>
              </div>
            </figure>
          </div>

          <p v-if="error" class="video-compare-error" role="alert">{{ error }}</p>

          <footer class="video-compare-controls">
            <button
              class="button primary video-compare-toggle"
              type="button"
              :disabled="!ready"
              :aria-label="isPlaying ? '暂停两个视频' : '同时播放两个视频'"
              data-testid="video-compare-toggle"
              @click="togglePlayback"
            >
              <Pause v-if="isPlaying" :size="15" fill="currentColor" />
              <Play v-else :size="15" fill="currentColor" />
              {{ isPlaying ? '暂停' : '播放' }}
            </button>

            <span class="video-compare-time" data-testid="video-compare-time">
              {{ formatClock(currentTime) }} / {{ durationLabel }}
            </span>

            <input
              class="video-compare-seek"
              type="range"
              min="0"
              :max="maxDuration || 0"
              step="0.05"
              :value="currentTime"
              :disabled="!ready"
              aria-label="两个视频的播放进度"
              data-testid="video-compare-seek"
              @input="seek"
            />

            <span class="video-compare-rate" aria-label="播放速度 2 倍">2× 同步</span>

            <button
              class="icon-button video-compare-audio"
              type="button"
              :aria-label="audioEnabled ? '关闭候选视频声音' : '开启候选视频声音'"
              :title="audioEnabled ? '关闭候选视频声音' : '开启候选视频声音'"
              data-testid="video-compare-audio"
              @click="toggleAudio"
            >
              <Volume2 v-if="audioEnabled" :size="16" />
              <VolumeX v-else :size="16" />
            </button>
          </footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>
