<script setup>
import { ChevronRight } from '@lucide/vue'
import { computed } from 'vue'
import { cleanDisplayText, formatDuration, referenceLabel } from '../utils/format'
import { isOpeningShot, openingOf } from '../utils/understandDisplay'
import {
  isSelectedCandidate,
  landingList,
  landingPageOf,
  landingParseStatus,
  landingStatusText,
  scriptCandidatesOf,
  shouldOfferRegenerateScripts,
  shouldOfferRegenerateVideo,
  shouldShowLanding,
  sharedPlanShots,
  voiceoverSummary,
} from '../utils/planDisplay'
import { agentPlanSkills, agentPlanTools, hasAgentTrace, latestAgentPlan, skillLabel, toolLabel } from '../utils/agentTraceDisplay'
import VideoOpeningRange from './VideoOpeningRange.vue'

const props = defineProps({
  payload: { type: Object, default: () => ({}) },
  rerunning: { type: Boolean, default: false },
})

const emit = defineEmits([
  'regenerate-scripts',
  'regenerate-video',
  'approve-scripts',
  'request-script-revision',
])

const intent = computed(() => props.payload?.intent || {})
const skeleton = computed(() => props.payload?.skeleton || {})
const references = computed(() => intent.value?.references || props.payload?.references || [])
const replacePlan = computed(() => props.payload?.replace_plan || null)
const resolved = computed(() => replacePlan.value?.resolved_segment_ids || [])
const opening = computed(() => openingOf(props.payload))
const shots = computed(() => sharedPlanShots(props.payload))
const landing = computed(() => landingPageOf(props.payload))
const landingVisible = computed(() => shouldShowLanding(landing.value))
const landingStatus = computed(() => landingParseStatus(landing.value))
const candidates = computed(() => scriptCandidatesOf(props.payload))
const canRescript = computed(() => shouldOfferRegenerateScripts(props.payload))
const canRegenVideo = computed(() => shouldOfferRegenerateVideo(props.payload))
const busy = computed(() => props.rerunning)
const productionId = computed(() => props.payload?.production_id || '')
const showAgentPlan = computed(() => hasAgentTrace(props.payload))
const agentPlan = computed(() => latestAgentPlan(props.payload))
const planSkills = computed(() => agentPlanSkills(agentPlan.value).map((name) => ({ name, label: skillLabel(name) })))
const planTools = computed(() => agentPlanTools(agentPlan.value).map((name) => ({ name, label: toolLabel(name) })))
const awaitingApproval = computed(() => props.payload?.status === 'awaiting_script_approval')
const referenceImages = computed(() =>
  Array.isArray(props.payload?.reference_images) ? props.payload.reference_images : [],
)

function shotDuration(shot) {
  const explicit = Number(shot?.duration_ms)
  if (Number.isFinite(explicit) && explicit > 0) return explicit
  const start = Math.max(0, Number(shot?.start_ms) || 0)
  const end = Number(shot?.end_ms)
  if (Number.isFinite(end) && end > start) return end - start
  return 0
}

function rescript() {
  if (!canRescript.value || busy.value || !productionId.value) return
  emit('regenerate-scripts', productionId.value)
}

function regenVideo() {
  if (!canRegenVideo.value || busy.value || !productionId.value) return
  emit('regenerate-video', productionId.value)
}

function approveScripts() {
  if (!awaitingApproval.value || busy.value || !productionId.value) return
  emit('approve-scripts', productionId.value)
}

function requestRevision() {
  emit('request-script-revision')
}
</script>

<template>
  <section class="video-card studio-card" data-testid="video-plan-card">
    <details class="studio-disclosure" data-testid="video-plan-disclosure">
      <summary class="studio-disclosure-summary">
        <span>
          <strong>任务规划</strong>
          <small>{{ candidates.length ? `${candidates.length} 个脚本候选` : '创作判断与执行计划' }}</small>
        </span>
        <ChevronRight :size="15" />
      </summary>
      <div class="studio-disclosure-body">
    <details
      v-if="showAgentPlan"
      class="agent-trace agent-trace--collapsible"
      data-testid="video-plan-agent-trace"
    >
      <summary class="agent-trace-summary">
        <span class="agent-trace-summary-copy">
          <strong>编排计划</strong>
          <small v-if="planTools.length">{{ planTools.length }} 个工具步骤</small>
        </span>
        <ChevronRight :size="14" />
      </summary>
      <p v-if="planSkills.length" class="agent-trace-skills" data-testid="video-plan-agent-skills">
        Skill：{{ planSkills.map((item) => item.label).join('、') }}
      </p>
      <ol v-if="planTools.length" class="agent-trace-list" data-testid="video-plan-agent-steps">
        <li v-for="(step, index) in planTools" :key="`${step.name}-${index}`">
          <span>{{ index + 1 }}</span>
          <strong>{{ step.label }}</strong>
          <code>{{ step.name }}</code>
        </li>
      </ol>
    </details>
    <section
      v-if="landingVisible"
      class="landing-summary"
      :class="{
        'landing-summary--ok': landingStatus === 'ok',
        'landing-summary--partial': landingStatus === 'partial',
        'landing-summary--failed': landingStatus === 'failed',
      }"
      :data-status="landingStatus"
      data-testid="video-plan-landing"
    >
      <strong data-testid="video-plan-landing-status">{{ landingStatusText(landing) }}</strong>
      <a
        v-if="landing.source_url"
        class="landing-source"
        :href="landing.source_url"
        target="_blank"
        rel="noreferrer"
        data-testid="video-plan-landing-url"
      >{{ landing.source_url }}</a>
      <p v-if="landing.product || landing.audience" class="landing-meta">
        <span v-if="landing.product">{{ cleanDisplayText(landing.product) }}</span>
        <span v-if="landing.audience">{{ cleanDisplayText(landing.audience) }}</span>
      </p>
      <ul v-if="landingList(landing.selling_points).length" class="landing-points" data-testid="video-plan-selling-points">
        <li v-for="point in landingList(landing.selling_points)" :key="point">{{ point }}</li>
      </ul>
      <p v-if="landing.method && landingStatus !== 'ok'" class="page-muted">抽取方式：{{ landing.method }}</p>
    </section>
    <section
      v-if="referenceImages.length"
      class="reference-image-strip"
      data-testid="video-plan-reference-images"
    >
      <figure v-for="image in referenceImages" :key="image.id || image.path || image.name">
        <img :src="image.url" :alt="image.name || '参考图'" />
        <figcaption>{{ image.name || '参考图' }}</figcaption>
      </figure>
    </section>
    <VideoOpeningRange :payload="payload" />
    <ol v-if="shots.length" class="opening-shot-list" data-testid="video-plan-shots">
      <li
        v-for="shot in shots"
        :key="shot.id || shot.shot_id"
        class="opening-shot-item"
        :class="{ 'opening-shot-item--block': isOpeningShot(shot, opening) }"
        :data-testid="isOpeningShot(shot, opening) ? 'video-plan-opening-shot' : `video-plan-shot-${shot.id || shot.shot_id}`"
      >
        <strong>{{ isOpeningShot(shot, opening) ? '开头连续块' : (shot.id || shot.shot_id) }}</strong>
        <span v-if="shotDuration(shot)">{{ formatDuration(shotDuration(shot)) }}</span>
        <em v-if="shot.action">{{ shot.action }}</em>
      </li>
    </ol>
    <div v-if="candidates.length" class="script-candidates" data-testid="video-script-candidates">
      <header class="script-candidates-head">
        <strong>创意脚本</strong>
        <span>
          {{
            payload.status === 'awaiting_script_approval'
              ? '等待确认后生成视频'
              : payload.status === 'awaiting_video_choice'
                ? '3 条候选视频已生成，可下载'
                : '3 个创意将分别生成视频'
          }}
        </span>
      </header>
      <ol class="script-candidate-list">
        <li
          v-for="candidate in candidates"
          :key="candidate.id || candidate.title"
          class="script-candidate"
          :class="{ 'script-candidate--selected': isSelectedCandidate(candidate, payload) }"
          :data-testid="`video-script-candidate-${candidate.id}`"
        >
          <div class="script-candidate-copy">
            <strong>{{ candidate.title || candidate.id }}</strong>
            <p v-if="candidate.angle" class="script-candidate-angle">{{ cleanDisplayText(candidate.angle) }}</p>
            <p v-if="voiceoverSummary(candidate)" class="script-candidate-voice" data-testid="video-script-voiceover">
              {{ voiceoverSummary(candidate) }}
            </p>
          </div>
          <span
            v-if="isSelectedCandidate(candidate, payload)"
            class="script-selected-flag"
            data-testid="video-script-selected"
          >已选用</span>
        </li>
      </ol>
      <div v-if="canRescript || canRegenVideo" class="script-followups">
        <button
          v-if="canRescript"
          class="button subtle"
          type="button"
          data-testid="video-rescript"
          :disabled="busy"
          @click="rescript"
        >
          再出 3 个脚本
        </button>
        <button
          v-if="canRegenVideo"
          class="button subtle"
          type="button"
          data-testid="video-regenerate"
          :disabled="busy"
          @click="regenVideo"
        >
          再生成视频
        </button>
      </div>
    </div>
    <dl
      v-if="
        intent.goal ||
        intent.true_intent ||
        intent.audience ||
        intent.promise ||
        intent.selling_points?.length ||
        intent.pain_points?.length
      "
      class="intent-grid"
    >
      <div v-if="intent.goal">
        <dt>目标</dt>
        <dd>{{ cleanDisplayText(intent.goal) || '—' }}</dd>
      </div>
      <div v-if="intent.true_intent">
        <dt>真实意图</dt>
        <dd>{{ cleanDisplayText(intent.true_intent) || '—' }}</dd>
      </div>
      <div v-if="intent.audience">
        <dt>受众</dt>
        <dd>{{ cleanDisplayText(intent.audience) || '—' }}</dd>
      </div>
      <div v-if="intent.promise">
        <dt>承诺</dt>
        <dd>{{ cleanDisplayText(intent.promise) || '—' }}</dd>
      </div>
      <div v-if="intent.selling_points?.length">
        <dt>产品卖点</dt>
        <dd>{{ intent.selling_points.join('；') }}</dd>
      </div>
      <div v-if="intent.pain_points?.length">
        <dt>用户痛点</dt>
        <dd>{{ intent.pain_points.join('；') }}</dd>
      </div>
    </dl>
    <ul v-if="references.length" class="reference-list">
      <li v-for="item in references" :key="item.path || item">
        <strong :title="item.path">{{ referenceLabel(item.path || item) }}</strong>
        <span v-if="item.why">{{ cleanDisplayText(item.why) }}</span>
      </li>
    </ul>
    <div v-if="skeleton.one_liner || skeleton.hook || skeleton.value || skeleton.cta" class="skeleton-block">
      <div v-if="cleanDisplayText(skeleton.one_liner)" class="skeleton-oneliner">
        <span>一行骨架</span>
        <p>{{ cleanDisplayText(skeleton.one_liner) }}</p>
      </div>
      <dl class="skeleton-beats">
        <div>
          <dt>Hook</dt>
          <dd>{{ cleanDisplayText(skeleton.hook) || '—' }}</dd>
        </div>
        <div>
          <dt>Value</dt>
          <dd>{{ cleanDisplayText(skeleton.value) || '—' }}</dd>
        </div>
        <div>
          <dt>CTA</dt>
          <dd>{{ cleanDisplayText(skeleton.cta) || '—' }}</dd>
        </div>
      </dl>
    </div>
    <p v-if="replacePlan" class="page-muted" data-testid="video-replace-plan">
      本轮新生成：{{ resolved.join('、') || '无' }}
    </p>
      </div>
    </details>
    <section
      v-if="awaitingApproval"
      class="script-approval"
      data-testid="video-script-approval"
    >
      <div class="script-approval-copy">
        <span class="script-approval-kicker">脚本待确认</span>
        <strong>先检查方向和表达，再开始生成视频</strong>
        <p>
          {{ candidates.map((candidate) => candidate.title || candidate.id).join(' · ') }}
          已准备好。确认后会调用 Seedance 生成 3 条候选视频。
        </p>
      </div>
      <div class="script-approval-actions">
        <button class="button subtle" type="button" data-testid="video-script-revise" @click="requestRevision">
          继续修改
        </button>
        <button
          class="button primary"
          type="button"
          data-testid="video-script-approve"
          :disabled="busy"
          @click="approveScripts"
        >
          {{ busy ? '处理中…' : '确认脚本并生成视频' }}
        </button>
      </div>
    </section>
  </section>
</template>
