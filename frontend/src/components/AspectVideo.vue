<script setup>
import { Play } from '@lucide/vue'
import { computed, nextTick, ref, watch } from 'vue'
import { aspectClass, aspectStyle } from '../utils/format'

const props = defineProps({
  src: { type: String, required: true },
  width: { type: Number, default: 0 },
  height: { type: Number, default: 0 },
  testid: { type: String, default: '' },
  compact: { type: Boolean, default: false },
  poster: { type: String, default: '' },
  label: { type: String, default: '' },
  active: { type: Boolean, default: null },
})

const emit = defineEmits(['activate'])
const measured = ref(null)
const internalActive = ref(false)
const video = ref(null)
const dims = computed(() => {
  if (measured.value) return measured.value
  const width = Number(props.width) || 0
  const height = Number(props.height) || 0
  return width && height ? { width, height } : null
})
const isActive = computed(() =>
  props.active === null ? internalActive.value : Boolean(props.active),
)

watch(
  () => props.src,
  () => {
    measured.value = null
    if (props.active === null) internalActive.value = false
  },
)

watch(
  isActive,
  async (active) => {
    if (!active) return
    await nextTick()
    video.value?.play().catch(() => {})
  },
  { immediate: true },
)

function activate() {
  if (isActive.value) return
  if (props.active === null) internalActive.value = true
  emit('activate')
}

function onMeta(event) {
  const width = Number(event?.target?.videoWidth) || 0
  const height = Number(event?.target?.videoHeight) || 0
  if (width && height) measured.value = { width, height }
}
</script>

<template>
  <div
    :class="[aspectClass(dims), { 'is-compact': compact }]"
    :style="aspectStyle(dims)"
  >
    <button
      v-if="!isActive"
      class="player-activate"
      type="button"
      :aria-label="label ? `播放${label}` : '播放视频'"
      :data-testid="testid || undefined"
      @click="activate"
    >
      <img
        v-if="poster"
        class="player-poster"
        :src="poster"
        alt=""
        loading="lazy"
        decoding="async"
      />
      <span class="player-activate-icon" aria-hidden="true">
        <Play :size="18" fill="currentColor" />
      </span>
      <span class="player-activate-copy">{{ label || '播放视频' }}</span>
    </button>
    <video
      v-else
      ref="video"
      :key="src"
      :src="src"
      :poster="poster || undefined"
      controls
      preload="metadata"
      playsinline
      :data-testid="testid || undefined"
      @loadedmetadata="onMeta"
    ></video>
  </div>
</template>
