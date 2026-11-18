<script setup>
import { computed } from 'vue'
import {
  isLocalOpeningEstimate,
  isModelOpening,
  mediaDurationMs,
  openingOf,
  openingReason,
  openingTitle,
  openingTrack,
  shouldShowOpening,
} from '../utils/understandDisplay'

const props = defineProps({
  payload: { type: Object, default: () => ({}) },
})

const opening = computed(() => openingOf(props.payload))
const visible = computed(() => shouldShowOpening(opening.value))
const confirmed = computed(() => isModelOpening(opening.value))
const provisional = computed(() => isLocalOpeningEstimate(opening.value))
const title = computed(() => openingTitle(opening.value))
const reason = computed(() => openingReason(opening.value))
const track = computed(() => openingTrack(opening.value, mediaDurationMs(props.payload, opening.value)))
</script>

<template>
  <div
    v-if="visible"
    class="opening-block"
    :class="{ 'opening-block--model': confirmed, 'opening-block--local': provisional }"
    :data-method="opening.method || ''"
    :data-testid="confirmed ? 'video-opening-model' : provisional ? 'video-opening-local' : 'video-opening-unspecified'"
  >
    <p class="opening-title" data-testid="video-opening-range">{{ title }}</p>
    <p v-if="reason" class="opening-reason" data-testid="video-opening-reason">{{ reason }}</p>
    <div v-if="track" class="opening-track" data-testid="video-opening-track">
      <i
        class="opening-track-span"
        :style="{ left: `${track.startPct}%`, width: `${track.widthPct}%` }"
      />
    </div>
  </div>
</template>
