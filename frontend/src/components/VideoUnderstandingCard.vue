<script setup>
import { computed } from 'vue'
import {
  ROLE_LABELS,
  cleanDisplayText,
  formatTimecode,
} from '../utils/format'
import { sourceVideoUrl } from '../utils/productionMedia'
import { slotTrail, videoDescribe, videoTagsOf, visibleVideoTags } from '../utils/understandDisplay'
import AspectVideo from './AspectVideo.vue'
import VideoOpeningRange from './VideoOpeningRange.vue'

const props = defineProps({
  payload: { type: Object, default: () => ({}) },
})

const summary = computed(
  () =>
    cleanDisplayText(props.payload?.summary || props.payload?.decomposition?.summary) ||
    '暂无整片摘要',
)
const description = computed(() => videoDescribe(props.payload))
const tags = computed(() => visibleVideoTags(videoTagsOf(props.payload)))
const segments = computed(
  () => props.payload?.segments || props.payload?.decomposition?.segments || [],
)
const source = computed(() => props.payload?.source_video || props.payload?.reference_video || {})
const sourceUrl = computed(() => sourceVideoUrl(props.payload))
</script>

<template>
  <section class="video-card studio-card" data-testid="video-understanding-card">
    <h3>视频理解</h3>
    <p class="summary-text">{{ summary }}</p>
    <p v-if="description" class="describe-text" data-testid="video-understanding-describe">
      {{ description }}
    </p>
    <ul v-if="tags.length" class="tag-list" data-testid="video-understanding-tags">
      <li v-for="tag in tags" :key="tag.key" class="tag-chip">
        <span class="tag-label">{{ tag.label }}</span>
        {{ tag.values.join('、') }}
      </li>
    </ul>
    <VideoOpeningRange :payload="payload" />
    <AspectVideo
      v-if="sourceUrl"
      :src="sourceUrl"
      :width="Number(source.width) || 0"
      :height="Number(source.height) || 0"
      testid="video-understanding-player"
      compact
    />
    <ol v-if="segments.length" class="segment-list">
      <li v-for="segment in segments" :key="segment.id" class="segment-item">
        <header>
          <strong>{{ ROLE_LABELS[segment.role] || segment.role }}</strong>
          <span class="segment-time">
            {{ formatTimecode(segment.start_ms) }} – {{ formatTimecode(segment.end_ms) }}
          </span>
        </header>
        <p v-if="slotTrail(segment)" class="page-muted">{{ slotTrail(segment) }}</p>
        <p v-if="cleanDisplayText(segment.summary || segment.points)">
          {{ cleanDisplayText(segment.summary || segment.points) }}
        </p>
      </li>
    </ol>
  </section>
</template>
