<script setup>
import { ChevronRight, CircleAlert, Copy, FileText, Terminal, Wrench } from 'lucide-vue-next'
import { nextTick, reactive, ref, watch } from 'vue'
import { itemLabel, payloadText, toolCallName } from '../utils/events'
import { renderMarkdown } from '../utils/markdown'

const props = defineProps({
  conversation: { type: Object, default: null },
})

const emit = defineEmits(['decide-approval', 'answer-clarification', 'copy'])
const scroller = ref(null)
const clarificationDrafts = reactive({})

function scrollToBottom(behavior = 'smooth') {
  nextTick(() => {
    if (scroller.value) {
      scroller.value.scrollTo({
        top: scroller.value.scrollHeight,
        behavior,
      })
    }
  })
}

function approvalDetail(approval) {
  const request = approval.request || {}
  if (request.kind === 'command') {
    return [request.command, request.cwd].filter(Boolean).join('\n')
  }
  if (request.paths?.length) return request.paths.join('\n')
  return JSON.stringify(request, null, 2)
}

function questionOptions(question) {
  const values = question.options || []
  return values.map((option) =>
    typeof option === 'string' ? option : option.label || option.value,
  )
}

function questionValue(question) {
  if (!clarificationDrafts[question.id]) clarificationDrafts[question.id] = ''
  return clarificationDrafts[question.id]
}

function submitClarification(clarification) {
  const answers = {}
  for (const question of clarification.questions || []) {
    const value = clarificationDrafts[question.id]
    if (question.required && !String(value || '').trim()) return
    if (value !== undefined && String(value).trim()) answers[question.id] = value
  }
  emit('answer-clarification', { id: clarification.id, answers })
}

function copyText(text) {
  emit('copy', text)
}

function structuredText(item) {
  const payload = item.payload || {}
  return payload.output || payload.patch || payload.text || ''
}

watch(
  () => props.conversation?.items?.length,
  (next, previous) => {
    if (next !== previous) scrollToBottom()
  },
)

watch(
  () => props.conversation?.id,
  () => scrollToBottom('instant'),
)

defineExpose({ scrollToBottom })
</script>

<template>
  <div ref="scroller" class="message-scroller">
    <div v-if="!conversation" class="hero-empty">
      <div class="hero-eyebrow"><span></span> CODEX CREATIVE ENGINE</div>
      <h1>把一个视频想法<br />切成<span>可执行的镜头。</span></h1>
      <p>
        对话、推演、查资料、写脚本、调用工具。所有上下文与执行轨迹留在同一个工作台。
      </p>
      <div class="hero-metrics">
        <div><strong>01</strong><span>持续对话</span></div>
        <div><strong>02</strong><span>Skill 复用</span></div>
        <div><strong>03</strong><span>Tool 落地</span></div>
      </div>
      <div class="hero-beam" aria-hidden="true"></div>
    </div>

    <div v-else class="transcript" data-testid="message-timeline">
      <article
        v-for="(item, index) in conversation.items"
        :key="item.id"
        class="message"
        :class="[
          `message--${item.item_type}`,
          { 'message--streaming': item.status === 'inProgress' },
        ]"
        :style="{ '--line-hue': `${index * 7 + 18}deg` }"
      >
        <div class="message-spine" aria-hidden="true">
          <span></span>
        </div>

        <template v-if="item.item_type === 'userMessage'">
          <div class="user-message" data-testid="user-message">
            <div class="user-label">YOU <span>INPUT</span></div>
            <p>{{ item.payload?.text }}</p>
            <div v-if="item.payload?.mentions?.length" class="mention-row">
              <span v-for="mention in item.payload.mentions" :key="mention.path">
                @{{ mention.name }}
              </span>
            </div>
            <div v-if="item.payload?.files?.length" class="attachment-row">
              <span v-for="file in item.payload.files" :key="file.path">
                <FileText :size="13" /> {{ file.name }}
              </span>
            </div>
          </div>
        </template>

        <template v-else-if="item.item_type === 'agentMessage'">
          <div class="agent-message">
            <div class="agent-heading">
              <span class="agent-glyph">VF</span>
              <div>
                <strong>Video Fission</strong>
                <small>{{ item.status === 'inProgress' ? '正在生成' : '智能中枢' }}</small>
              </div>
              <button
                v-if="payloadText(item)"
                class="icon-button compact"
                title="复制回复"
                @click="copyText(payloadText(item))"
              >
                <Copy :size="14" />
              </button>
            </div>
            <div class="markdown-body" v-html="renderMarkdown(payloadText(item))"></div>
            <span v-if="item.status === 'inProgress'" class="typing-caret"></span>
          </div>
        </template>

        <template v-else-if="item.item_type === 'reasoning'">
          <details class="trace-block trace-block--reasoning">
            <summary>
              <ChevronRight :size="14" />
              <span>推理轨迹</span>
              <small>{{ item.status === 'inProgress' ? '进行中' : '已折叠' }}</small>
            </summary>
            <p>{{ payloadText(item) }}</p>
          </details>
        </template>

        <template v-else-if="item.item_type === 'plan'">
          <div class="plan-block">
            <div class="trace-label">PLAN / 计划</div>
            <div class="markdown-body" v-html="renderMarkdown(payloadText(item))"></div>
          </div>
        </template>

        <template
          v-else-if="['commandExecution', 'dynamicToolCall', 'mcpToolCall', 'webSearch', 'fileChange'].includes(item.item_type)"
        >
          <details class="trace-block" :open="item.status === 'inProgress'">
            <summary>
              <Terminal v-if="item.item_type === 'commandExecution'" :size="14" />
              <Wrench v-else :size="14" />
              <span>{{ itemLabel(item.item_type) }}</span>
              <code>{{ toolCallName(item) }}</code>
              <small>{{ item.status }}</small>
            </summary>
            <pre v-if="structuredText(item)">{{ structuredText(item) }}</pre>
          </details>
        </template>

        <template v-else>
          <details class="trace-block">
            <summary>
              <Wrench :size="14" />
              <span>{{ itemLabel(item.item_type) }}</span>
              <small>{{ item.status }}</small>
            </summary>
            <pre>{{ JSON.stringify(item.payload, null, 2) }}</pre>
          </details>
        </template>
      </article>

      <article
        v-for="approval in conversation.pending_approvals || []"
        :key="approval.id"
        class="message interaction-message"
      >
        <div class="message-spine"><span></span></div>
        <div class="interaction-card approval-card" data-testid="approval-card">
          <div class="interaction-kicker"><CircleAlert :size="15" /> 需要你的批准</div>
          <h3>{{ approval.request?.reason || 'Codex 请求继续执行' }}</h3>
          <pre>{{ approvalDetail(approval) }}</pre>
          <div class="interaction-actions">
            <button class="button primary" @click="$emit('decide-approval', approval.id, 'accept')">
              允许本次
            </button>
            <button class="button subtle" @click="$emit('decide-approval', approval.id, 'decline')">
              拒绝
            </button>
          </div>
        </div>
      </article>

      <article
        v-for="clarification in conversation.pending_clarifications || []"
        :key="clarification.id"
        class="message interaction-message"
      >
        <div class="message-spine"><span></span></div>
        <form class="interaction-card clarification-card" @submit.prevent="submitClarification(clarification)">
          <div class="interaction-kicker">AGENT QUESTION</div>
          <div v-for="question in clarification.questions" :key="question.id" class="question-row">
            <label :for="`${clarification.id}-${question.id}`">
              {{ question.header || question.question }}
            </label>
            <small v-if="question.question && question.question !== question.header">
              {{ question.question }}
            </small>
            <select
              v-if="questionOptions(question).length"
              :id="`${clarification.id}-${question.id}`"
              v-model="clarificationDrafts[question.id]"
            >
              <option value="">请选择</option>
              <option v-for="option in questionOptions(question)" :key="option" :value="option">
                {{ option }}
              </option>
            </select>
            <input
              v-else
              :id="`${clarification.id}-${question.id}`"
              v-model="clarificationDrafts[question.id]"
              :required="question.required"
              placeholder="输入回答"
            />
          </div>
          <div class="interaction-actions">
            <button class="button primary" type="submit">提交回答</button>
          </div>
        </form>
      </article>

      <div v-if="conversation.status === 'running'" class="thinking-line">
        <span></span>
        <p>正在沿执行轨迹推进</p>
      </div>

      <div v-if="conversation.last_error" class="error-banner" data-testid="conversation-error">
        <CircleAlert :size="16" />
        <span>{{ conversation.last_error }}</span>
      </div>
    </div>

    <button
      v-if="conversation?.items?.length"
      class="scroll-bottom"
      title="滚动到底部"
      @click="scrollToBottom()"
    >
      ↓
    </button>
  </div>
</template>

