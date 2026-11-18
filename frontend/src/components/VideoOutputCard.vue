<script setup>
import { computed } from 'vue'
import { outputSrtUrl, outputVideoUrl } from '../utils/productionMedia'
import AspectVideo from './AspectVideo.vue'

const props = defineProps({
  payload: { type: Object, default: () => ({}) },
})

const videoUrl = computed(() => outputVideoUrl(props.payload))
const srtUrl = computed(() => outputSrtUrl(props.payload))
const dims = computed(() => {
  const source = props.payload?.output || props.payload || {}
  return {
    width: Number(source.width) || 0,
    height: Number(source.height) || 0,
  }
})
</script>

<template>
  <section v-if="videoUrl || srtUrl" class="video-card studio-card studio-output video-output" data-testid="video-output-card">
    <h3>成片</h3>
    <AspectVideo
      v-if="videoUrl"
      :src="videoUrl"
      :width="dims.width"
      :height="dims.height"
      testid="video-player"
    />
    <div class="studio-actions">
      <a
        v-if="videoUrl"
        class="button primary"
        data-testid="video-download-mp4"
        :href="videoUrl"
        download="final.mp4"
      >
        下载 MP4
      </a>
      <a
        v-if="srtUrl"
        class="button subtle"
        data-testid="video-download-srt"
        :href="srtUrl"
        download="final.srt"
      >
        下载 SRT
      </a>
    </div>
  </section>
</template>
