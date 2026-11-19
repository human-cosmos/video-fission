<script setup>
import { Check, Download } from '@lucide/vue'
import { computed } from 'vue'
import { candidateVideoUrl, outputVideoUrl } from '../utils/productionMedia'
import AspectVideo from './AspectVideo.vue'

const props = defineProps({
  payload: { type: Object, default: () => ({}) },
  selecting: { type: Boolean, default: false },
})

const emit = defineEmits(['select-video'])
const videoUrl = computed(() => outputVideoUrl(props.payload))
const candidateVideos = computed(() =>
  Array.isArray(props.payload?.candidate_videos)
    ? props.payload.candidate_videos.filter((item) => item?.video_url)
    : [],
)
const awaitingChoice = computed(() => props.payload?.status === 'awaiting_video_choice')
const selectedVideoId = computed(() => String(props.payload?.selected_script_id || ''))
const dims = computed(() => {
  const source = props.payload?.output || props.payload || {}
  return {
    width: Number(source.width) || 0,
    height: Number(source.height) || 0,
  }
})

function chooseVideo(candidate) {
  if (!awaitingChoice.value || props.selecting || !candidate?.id) return
  emit('select-video', {
    productionId: props.payload?.production_id,
    videoId: candidate.id,
  })
}
</script>

<template>
  <section
    v-if="candidateVideos.length || videoUrl"
    class="video-card studio-card studio-output video-output"
    data-testid="video-output-card"
  >
    <template v-if="candidateVideos.length">
      <header class="candidate-video-head">
        <div>
          <h3>候选视频</h3>
          <p>3 个创意已分别生成视频，选择一条继续最终合成。</p>
        </div>
        <span>{{ candidateVideos.length }} / 3</span>
      </header>
      <div class="candidate-video-grid" data-testid="video-candidate-grid">
        <article
          v-for="(candidate, index) in candidateVideos"
          :key="candidate.id"
          class="candidate-video-card"
          :class="{ 'is-selected': candidate.id === selectedVideoId }"
          :data-testid="`video-candidate-${candidate.id}`"
        >
          <header class="candidate-video-title">
            <span>0{{ index + 1 }}</span>
            <div>
              <strong>{{ candidate.title || candidate.id }}</strong>
              <small v-if="candidate.angle">{{ candidate.angle }}</small>
            </div>
          </header>
          <AspectVideo
            :src="candidateVideoUrl(payload, candidate)"
            :width="Number(candidate.width) || 0"
            :height="Number(candidate.height) || 0"
            :testid="`video-candidate-player-${candidate.id}`"
          />
          <footer class="candidate-video-actions">
            <span v-if="candidate.id === selectedVideoId" class="candidate-selected">
              <Check :size="13" />
              已选用
            </span>
            <button
              v-else-if="awaitingChoice"
              class="button primary"
              type="button"
              data-testid="video-candidate-select"
              :disabled="selecting"
              @click="chooseVideo(candidate)"
            >
              使用这个视频
            </button>
          </footer>
        </article>
      </div>
    </template>
    <h3 v-if="videoUrl">最终成片</h3>
    <AspectVideo
      v-if="videoUrl"
      :src="videoUrl"
      :width="dims.width"
      :height="dims.height"
      testid="video-player"
    />
    <div v-if="videoUrl" class="studio-actions">
      <a
        v-if="videoUrl"
        class="button primary"
        data-testid="video-download-mp4"
        :href="videoUrl"
        download="final.mp4"
      >
        <Download :size="14" />
        下载 MP4
      </a>
    </div>
  </section>
</template>
