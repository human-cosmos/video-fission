<script setup>
import { Columns2, Download } from '@lucide/vue'
import { computed, nextTick, ref } from 'vue'
import { formatDuration } from '../utils/format'
import {
  candidateVideoUrl,
  sourceVideoUrl,
} from '../utils/productionMedia'
import AspectVideo from './AspectVideo.vue'
import VideoCompareModal from './VideoCompareModal.vue'

const props = defineProps({
  payload: { type: Object, default: () => ({}) },
})

const activeCandidate = ref(null)
const compareTrigger = ref(null)
const candidateVideos = computed(() =>
  Array.isArray(props.payload?.candidate_videos)
    ? props.payload.candidate_videos.filter((item) => item?.id && item?.video_url)
    : [],
)
const originalVideoUrl = computed(() => sourceVideoUrl(props.payload))
const activeCandidateUrl = computed(() =>
  activeCandidate.value ? candidateVideoUrl(props.payload, activeCandidate.value) : '',
)
const activeCandidateLabel = computed(() => {
  const index = candidateVideos.value.findIndex((item) => item.id === activeCandidate.value?.id)
  return index >= 0 ? `候选 ${candidateLetter(index)}` : '候选视频'
})

function candidateLetter(index) {
  return String.fromCharCode(65 + index)
}

function downloadName(candidate, index) {
  const id = String(candidate?.id || `candidate-${index + 1}`).replace(/[^a-zA-Z0-9_-]+/g, '-')
  return `${id || `candidate-${index + 1}`}.mp4`
}

function openCompare(candidate, event) {
  if (!candidate || !originalVideoUrl.value) return
  compareTrigger.value = event?.currentTarget || null
  activeCandidate.value = candidate
}

function closeCompare() {
  activeCandidate.value = null
}

function restoreCompareFocus() {
  nextTick(() => compareTrigger.value?.focus())
}
</script>

<template>
  <section
    v-if="candidateVideos.length"
    class="video-card studio-card studio-output video-output"
    data-testid="video-output-card"
  >
    <header class="candidate-video-head">
      <div>
        <h3>候选视频</h3>
        <p>三种创意已分别生成，可下载或与原视频对比。</p>
      </div>
      <span>{{ candidateVideos.length }} / 3</span>
    </header>

    <div class="candidate-video-list" data-testid="video-candidate-grid">
      <article
        v-for="(candidate, index) in candidateVideos"
        :key="candidate.id"
        class="candidate-video-row"
        :data-testid="`video-candidate-${candidate.id}`"
      >
        <header class="candidate-video-title">
          <span class="candidate-video-letter">{{ candidateLetter(index) }}</span>
          <div>
            <strong>{{ candidate.title || `候选视频 ${candidateLetter(index)}` }}</strong>
            <small v-if="candidate.angle">{{ candidate.angle }}</small>
          </div>
          <time v-if="candidate.duration_ms">
            {{ formatDuration(candidate.duration_ms) }}
          </time>
        </header>

        <AspectVideo
          :src="candidateVideoUrl(payload, candidate)"
          :width="Number(candidate.width) || 0"
          :height="Number(candidate.height) || 0"
          :testid="`video-candidate-player-${candidate.id}`"
        />

        <footer class="candidate-video-actions">
          <a
            class="button primary"
            :href="candidateVideoUrl(payload, candidate)"
            :download="downloadName(candidate, index)"
            :data-testid="`video-candidate-download-${candidate.id}`"
          >
            <Download :size="14" />
            下载 MP4
          </a>
          <button
            class="button subtle"
            type="button"
            :disabled="!originalVideoUrl"
            :data-testid="`video-candidate-compare-${candidate.id}`"
            @click="openCompare(candidate, $event)"
          >
            <Columns2 :size="14" />
            对比原视频
          </button>
        </footer>
      </article>
    </div>

    <VideoCompareModal
      :open="Boolean(activeCandidate)"
      :source-url="originalVideoUrl"
      :candidate-url="activeCandidateUrl"
      :candidate-label="activeCandidateLabel"
      :candidate-title="activeCandidate?.title || ''"
      @close="closeCompare"
      @closed="restoreCompareFocus"
    />
  </section>
</template>
