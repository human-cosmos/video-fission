<script setup>
import { ChevronRight } from '@lucide/vue'
import { computed, ref, watch } from 'vue'
import {
  ACTION_LABELS,
  ROLE_LABELS,
  cleanDisplayText,
  formatDuration,
  formatTimecode,
  isTechnicalVisual,
  segmentAction,
  segmentFrameNames,
} from '../utils/format'
import { clipMediaUrl, frameMediaUrl } from '../utils/productionMedia'
import { openingOf, segmentInOpening, slotTrail } from '../utils/understandDisplay'
import AspectVideo from './AspectVideo.vue'
import VideoOpeningRange from './VideoOpeningRange.vue'

const props = defineProps({
  payload: { type: Object, default: () => ({}) },
  rerunning: { type: Boolean, default: false },
})

defineEmits(['rerun-brief'])

const hiddenFrames = ref({})

const productionId = computed(() => props.payload?.production_id || '')
const segments = computed(
  () => props.payload?.segments || props.payload?.decomposition?.segments || [],
)
const shots = computed(() => props.payload?.shots || props.payload?.script?.shots || [])
const clips = computed(() => props.payload?.clips || [])
const replacePlan = computed(() => props.payload?.replace_plan || null)
const opening = computed(() => openingOf(props.payload))
const canRerunBrief = computed(
  () =>
    Boolean(productionId.value) &&
    status.value !== 'running' &&
    status.value !== 'draft' &&
    !props.rerunning,
)

watch(productionId, () => {
  hiddenFrames.value = {}
})

function actionOf(segment) {
  return segmentAction(segment, replacePlan.value, shots.value)
}

function hideFrame(key) {
  hiddenFrames.value = { ...hiddenFrames.value, [key]: true }
}

function clipUrl(clip) {
  return clipMediaUrl(clip, productionId.value)
}

function segmentClipUrl(segment) {
  return clipMediaUrl(segment, productionId.value)
}
</script>

<template>
  <section class="video-card studio-card" data-testid="video-segments-card">
    <details class="studio-disclosure" data-testid="video-segments-disclosure">
      <summary class="studio-disclosure-summary">
        <span>
          <strong>原片与片段</strong>
          <small>{{ segments.length }} 个片段{{ clips.length ? ` · ${clips.length} 个产物` : '' }}</small>
        </span>
        <ChevronRight :size="15" />
      </summary>
      <div class="studio-disclosure-body">
    <p v-if="replacePlan" class="page-muted" data-testid="video-replace-plan">
      本轮新生成：{{ (replacePlan.resolved_segment_ids || []).join('、') || '无' }}
    </p>
    <VideoOpeningRange :payload="payload" />
    <ol v-if="segments.length" class="segment-list">
      <li
        v-for="segment in segments"
        :key="segment.id"
        class="segment-item"
        :class="{ 'segment-item--in-opening': segmentInOpening(segment, opening) }"
        :data-testid="`video-segment-${segment.id}`"
      >
        <header>
          <strong>{{ ROLE_LABELS[segment.role] || segment.role }}</strong>
          <span class="segment-time">
            {{ formatTimecode(segment.start_ms) }} – {{ formatTimecode(segment.end_ms) }}
          </span>
          <em
            v-if="actionOf(segment)"
            class="segment-action"
            :class="actionOf(segment)"
            :data-testid="`video-segment-action-${segment.id}`"
          >
            {{ ACTION_LABELS[actionOf(segment)] || actionOf(segment) }}
          </em>
          <span
            v-if="segment.reuse_recommended"
            class="segment-reuse"
            :data-testid="`video-segment-reuse-${segment.id}`"
          >
            建议复用
          </span>
        </header>
        <p v-if="slotTrail(segment)" class="page-muted" :data-testid="`video-segment-slot-${segment.id}`">
          {{ slotTrail(segment) }}
        </p>
        <p v-if="cleanDisplayText(segment.description)" class="segment-description">
          {{ cleanDisplayText(segment.description) }}
        </p>
        <p v-if="cleanDisplayText(segment.summary)">{{ cleanDisplayText(segment.summary) }}</p>
        <p v-if="segment.visual && !isTechnicalVisual(segment.visual)" class="page-muted">
          画面：{{ cleanDisplayText(segment.visual) }}
        </p>
        <AspectVideo
          v-if="segmentClipUrl(segment)"
          :src="segmentClipUrl(segment)"
          :width="Number(segment.width) || 0"
          :height="Number(segment.height) || 0"
          :testid="`video-segment-clip-${segment.id}`"
          compact
        />
        <div class="frame-row">
          <img
            v-for="name in segmentFrameNames(segment.id)"
            v-show="!hiddenFrames[`${segment.id}:${name}`]"
            :key="name"
            :src="frameMediaUrl(productionId, name)"
            :alt="`${segment.id} 关键帧`"
            @error="hideFrame(`${segment.id}:${name}`)"
          />
        </div>
      </li>
    </ol>

    <div v-if="clips.length" class="clip-grid">
      <figure v-for="clip in clips" :key="clip.shot_id || clip.path">
        <AspectVideo
          v-if="clipUrl(clip)"
          :src="clipUrl(clip)"
          :width="Number(clip.width) || 0"
          :height="Number(clip.height) || 0"
          compact
        />
        <figcaption>
          {{ clip.shot_id }}
          <template v-if="clip.duration_ms"> · {{ formatDuration(clip.duration_ms) }}</template>
        </figcaption>
      </figure>
    </div>

    <div class="studio-rerun-row">
      <button
        class="button primary"
        type="button"
        data-testid="video-rerun-brief"
        :disabled="!canRerunBrief"
        @click="$emit('rerun-brief', productionId)"
      >
        按最新说明重跑
      </button>
    </div>
      </div>
    </details>
  </section>
</template>
