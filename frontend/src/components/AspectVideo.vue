<script setup>
import { computed, ref } from 'vue'
import { aspectClass, aspectStyle } from '../utils/format'

const props = defineProps({
  src: { type: String, required: true },
  width: { type: Number, default: 0 },
  height: { type: Number, default: 0 },
  testid: { type: String, default: '' },
  compact: { type: Boolean, default: false },
})

const measured = ref(null)
const dims = computed(() => {
  if (measured.value) return measured.value
  const width = Number(props.width) || 0
  const height = Number(props.height) || 0
  return width && height ? { width, height } : null
})

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
    <video
      :key="src"
      :src="src"
      controls
      preload="metadata"
      :data-testid="testid || undefined"
      @loadedmetadata="onMeta"
    ></video>
  </div>
</template>
