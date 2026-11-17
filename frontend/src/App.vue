<script setup>
import {
  Activity,
  Archive,
  ArrowUp,
  Check,
  ChevronDown,
  GitBranch,
  Menu,
  MoreHorizontal,
  Paperclip,
  PanelRight,
  RefreshCw,
  Settings2,
  Square,
  Trash2,
  X,
  Zap,
} from 'lucide-vue-next'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, eventUrl } from './api'
import CapabilityPanel from './components/CapabilityPanel.vue'
import ConversationSidebar from './components/ConversationSidebar.vue'
import MessageTimeline from './components/MessageTimeline.vue'
import SkillEditorModal from './components/SkillEditorModal.vue'
import { isRefreshEvent } from './utils/events'

const conversations = ref([])
const currentConversation = ref(null)
const runtime = ref({})
const models = ref([])
const skills = ref([])
const tools = ref([])
const loading = ref(true)
const listLoading = ref(false)
const bootError = ref('')
const composer = ref('')
const attachments = ref([])
const skillMentions = ref([])
const uploading = ref(false)
const sending = ref(false)
const showArchived = ref(false)
const mobileSidebar = ref(false)
const mobileCapabilities = ref(false)
const skillModalOpen = ref(false)
const editingSkill = ref(null)
const skillBusy = ref(false)
const toast = ref('')
const streamConnected = ref(false)
const timeline = ref(null)
const composerInput = ref(null)
let eventSource = null
let refreshTimer = null
let toastTimer = null
let titleBeforeEdit = ''

const currentModel = computed(() =>
  models.value.find((model) => model.id === currentConversation.value?.model),
)
const supportedEfforts = computed(
  () => currentModel.value?.supported_reasoning_efforts || ['low', 'medium', 'high'],
)
const visibleConversations = computed(() =>
  conversations.value.filter((item) =>
    showArchived.value ? item.is_archived : !item.is_archived,
  ),
)
const hasConversation = computed(() => Boolean(currentConversation.value?.id))
const canSend = computed(
  () =>
    !sending.value &&
    !uploading.value &&
    (composer.value.trim() || attachments.value.length),
)

function notify(message) {
  toast.value = message
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => {
    toast.value = ''
  }, 3200)
}

function setBootError(error) {
  bootError.value = error instanceof Error ? error.message : String(error)
}

async function bootstrap() {
  loading.value = true
  bootError.value = ''
  try {
    const [health, capabilityPayload, modelPayload] = await Promise.all([
      api.health(),
      api.capabilities(),
      api.models().catch(() => ({ items: [] })),
    ])
    runtime.value = health.runtime
    skills.value = capabilityPayload.skills || []
    tools.value = capabilityPayload.tools || []
    models.value = modelPayload.items || []
    await loadConversations()
    if (conversations.value.length && !currentConversation.value) {
      const savedId = window.localStorage.getItem('video-fission.activeConversation')
      const savedExists = conversations.value.some((item) => item.id === savedId)
      await selectConversation(
        (savedExists ? savedId : null) ||
          conversations.value.find((item) => !item.is_archived)?.id ||
          conversations.value[0].id,
      )
    }
  } catch (error) {
    setBootError(error)
  } finally {
    loading.value = false
  }
}

async function loadCapabilities() {
  const payload = await api.capabilities()
  runtime.value = payload.runtime
  skills.value = payload.skills || []
  tools.value = payload.tools || []
}

async function loadConversations() {
  listLoading.value = true
  try {
    const payload = await api.conversations(true)
    conversations.value = payload.items || []
  } finally {
    listLoading.value = false
  }
}

function closeStream() {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
  streamConnected.value = false
}

function connectStream(conversationId) {
  closeStream()
  eventSource = new EventSource(eventUrl(conversationId, 0))
  eventSource.onopen = () => {
    streamConnected.value = true
  }
  eventSource.onerror = () => {
    streamConnected.value = false
  }
  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      if (!isRefreshEvent(data.kind)) return
      scheduleRefresh(conversationId)
    } catch {
      // Ignore malformed stream payloads; EventSource will continue.
    }
  }
}

function scheduleRefresh(conversationId) {
  clearTimeout(refreshTimer)
  refreshTimer = setTimeout(async () => {
    if (currentConversation.value?.id !== conversationId) return
    try {
      const [detail, list] = await Promise.all([
        api.conversation(conversationId),
        api.conversations(true),
      ])
      currentConversation.value = detail
      conversations.value = list.items || []
    } catch {
      // Keep the current transcript visible during transient stream reconnects.
    }
  }, 140)
}

async function selectConversation(id) {
  if (!id) return
  closeStream()
  try {
    currentConversation.value = await api.conversation(id)
    window.localStorage.setItem('video-fission.activeConversation', id)
    titleBeforeEdit = currentConversation.value.title
    attachments.value = []
    skillMentions.value = []
    mobileSidebar.value = false
    connectStream(id)
    await loadConversations()
    await nextTick()
    timeline.value?.scrollToBottom('instant')
  } catch (error) {
    notify(error.message)
  }
}

async function newConversation() {
  try {
    const model = currentConversation.value?.model || models.value[0]?.id || null
    const effort =
      models.value.find((item) => item.id === model)?.default_reasoning_effort || 'high'
    const created = await api.createConversation({
      model,
      reasoning_effort: effort,
    })
    conversations.value.unshift(created)
    await selectConversation(created.id)
    mobileSidebar.value = false
    await nextTick()
    composerInput.value?.focus()
  } catch (error) {
    notify(error.message)
  }
}

async function refreshCurrent() {
  if (!currentConversation.value?.id) return
  const id = currentConversation.value.id
  try {
    currentConversation.value = await api.conversation(id)
    await loadConversations()
  } catch (error) {
    notify(error.message)
  }
}

async function saveTitle() {
  const title = currentConversation.value?.title?.trim()
  if (!title || title === titleBeforeEdit) return
  try {
    const updated = await api.updateConversation(currentConversation.value.id, { title })
    currentConversation.value = { ...currentConversation.value, ...updated }
    titleBeforeEdit = updated.title
    await loadConversations()
  } catch (error) {
    notify(error.message)
  }
}

async function updateModel() {
  if (!currentConversation.value?.id) return
  const model = models.value.find((item) => item.id === currentConversation.value.model)
  const effort =
    model?.default_reasoning_effort ||
    currentConversation.value.reasoning_effort ||
    'high'
  currentConversation.value.reasoning_effort = effort
  try {
    await api.updateConversation(currentConversation.value.id, {
      model: currentConversation.value.model,
      reasoning_effort: effort,
    })
    notify('下一条消息起使用新模型')
  } catch (error) {
    notify(error.message)
  }
}

async function updateEffort() {
  if (!currentConversation.value?.id) return
  try {
    await api.updateConversation(currentConversation.value.id, {
      reasoning_effort: currentConversation.value.reasoning_effort,
    })
  } catch (error) {
    notify(error.message)
  }
}

async function uploadFiles(event) {
  const files = Array.from(event.target.files || [])
  event.target.value = ''
  if (!files.length) return
  uploading.value = true
  try {
    for (const file of files) {
      const uploaded = await api.upload(file)
      attachments.value.push(uploaded)
    }
    notify(`已添加 ${files.length} 个文件`)
  } catch (error) {
    notify(error.message)
  } finally {
    uploading.value = false
  }
}

function removeAttachment(id) {
  attachments.value = attachments.value.filter((file) => file.id !== id)
}

function useSkill(skill) {
  if (!skillMentions.value.some((item) => item.path === `skill://${skill.slug}`)) {
    skillMentions.value.push({
      name: skill.name,
      path: `skill://${skill.slug}`,
    })
  }
  mobileCapabilities.value = false
  composerInput.value?.focus()
}

function removeMention(path) {
  skillMentions.value = skillMentions.value.filter((item) => item.path !== path)
}

async function send() {
  if (!canSend.value) return
  let conversation = currentConversation.value
  if (!conversation?.id) {
    await newConversation()
    conversation = currentConversation.value
  }
  if (!conversation?.id) return
  sending.value = true
  const text = composer.value.trim()
  const payload = {
    text,
    files: attachments.value,
    mentions: skillMentions.value,
  }
  try {
    if (conversation.status === 'running') {
      await api.steer(conversation.id, payload)
    } else {
      await api.sendMessage(conversation.id, payload)
    }
    composer.value = ''
    attachments.value = []
    skillMentions.value = []
    await refreshCurrent()
    timeline.value?.scrollToBottom()
  } catch (error) {
    notify(error.message)
  } finally {
    sending.value = false
    composerInput.value?.focus()
  }
}

async function stopTurn() {
  if (!currentConversation.value?.id) return
  try {
    await api.interrupt(currentConversation.value.id)
    await refreshCurrent()
    notify('已停止当前执行')
  } catch (error) {
    notify(error.message)
  }
}

async function decideApproval(id, decision) {
  try {
    await api.decideApproval(id, decision)
    await refreshCurrent()
  } catch (error) {
    notify(error.message)
  }
}

async function answerClarification({ id, answers }) {
  try {
    await api.answerClarification(id, answers)
    await refreshCurrent()
  } catch (error) {
    notify(error.message)
  }
}

async function archiveConversation(id) {
  try {
    await api.archive(id)
    if (id === currentConversation.value?.id) {
      currentConversation.value = {
        ...currentConversation.value,
        is_archived: true,
        archived_at: new Date().toISOString(),
      }
    }
    await loadConversations()
    notify('对话已归档')
  } catch (error) {
    notify(error.message)
  }
}

async function unarchiveConversation(id) {
  try {
    await api.unarchive(id)
    if (id === currentConversation.value?.id) {
      currentConversation.value = {
        ...currentConversation.value,
        is_archived: false,
        archived_at: null,
      }
    }
    await loadConversations()
    notify('已取消归档')
  } catch (error) {
    notify(error.message)
  }
}

async function forkConversation() {
  if (!currentConversation.value?.id) return
  try {
    const forked = await api.fork(currentConversation.value.id)
    await loadConversations()
    await selectConversation(forked.id)
    notify('已从当前上下文创建分叉')
  } catch (error) {
    notify(error.message)
  }
}

async function compactConversation() {
  if (!currentConversation.value?.id) return
  try {
    await api.compact(currentConversation.value.id)
    notify('已开始压缩上下文')
  } catch (error) {
    notify(error.message)
  }
}

async function syncConversation() {
  if (!currentConversation.value?.id) return
  try {
    const result = await api.sync(currentConversation.value.id)
    await refreshCurrent()
    notify(`已同步 ${result.synced} 条运行时记录`)
  } catch (error) {
    notify(error.message)
  }
}

async function deleteConversation() {
  if (!currentConversation.value?.id) return
  if (!window.confirm('永久删除这个对话及其本地记录？')) return
  try {
    await api.deleteConversation(currentConversation.value.id)
    window.localStorage.removeItem('video-fission.activeConversation')
    closeStream()
    currentConversation.value = null
    await loadConversations()
    const next = conversations.value.find((item) => !item.is_archived)
    if (next) await selectConversation(next.id)
    notify('对话已删除')
  } catch (error) {
    notify(error.message)
  }
}

async function toggleSkill(slug, enabled) {
  try {
    await api.toggleSkill(slug, enabled)
    await loadCapabilities()
    notify(enabled ? 'Skill 已启用' : 'Skill 已停用')
  } catch (error) {
    notify(error.message)
  }
}

function openSkillEditor(skill = null) {
  editingSkill.value = skill
  skillModalOpen.value = true
}

async function saveSkill(payload) {
  skillBusy.value = true
  try {
    if (editingSkill.value) {
      await api.updateSkill(editingSkill.value.slug, payload)
    } else {
      await api.createSkill(payload)
    }
    skillModalOpen.value = false
    await loadCapabilities()
    notify(editingSkill.value ? 'Skill 已更新' : 'Skill 已创建')
  } catch (error) {
    notify(error.message)
  } finally {
    skillBusy.value = false
  }
}

async function deleteSkill(skill) {
  if (!window.confirm(`删除 Skill「${skill.name}」？`)) return
  try {
    await api.deleteSkill(skill.slug)
    await loadCapabilities()
    notify('Skill 已删除')
  } catch (error) {
    notify(error.message)
  }
}

async function copyReply(text) {
  try {
    await navigator.clipboard.writeText(text)
    notify('已复制回复')
  } catch {
    notify('复制失败，请手动选择文本')
  }
}

function usePrompt(text) {
  composer.value = text
  nextTick(() => composerInput.value?.focus())
}

function insertLineBreak(event) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    send()
  }
}

function handleShortcut(event) {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'n') {
    event.preventDefault()
    newConversation()
  }
}

watch(showArchived, async () => {
  await loadConversations()
})

onMounted(() => {
  bootstrap()
  window.addEventListener('keydown', handleShortcut)
})

onBeforeUnmount(() => {
  closeStream()
  clearTimeout(refreshTimer)
  clearTimeout(toastTimer)
  window.removeEventListener('keydown', handleShortcut)
})
</script>

<template>
  <div class="app-shell">
    <ConversationSidebar
      :conversations="visibleConversations"
      :current-id="currentConversation?.id || ''"
      :runtime="runtime"
      :show-archived="showArchived"
      :loading="listLoading"
      :mobile-open="mobileSidebar"
      @new="newConversation"
      @select="selectConversation"
      @archive="archiveConversation"
      @unarchive="unarchiveConversation"
      @toggle-archived="showArchived = !showArchived"
      @close-mobile="mobileSidebar = false"
    />

    <main class="workspace">
      <header class="topbar">
        <button class="icon-button mobile-only" aria-label="打开会话列表" @click="mobileSidebar = true">
          <Menu :size="19" />
        </button>
        <div class="conversation-heading">
          <input
            v-if="currentConversation"
            v-model="currentConversation.title"
            class="title-input"
            aria-label="对话标题"
            @focus="titleBeforeEdit = currentConversation.title"
            @blur="saveTitle"
            @keydown.enter.target.blur
          />
          <h1 v-else>Video Fission</h1>
          <div class="conversation-meta">
            <span v-if="streamConnected" class="live-label"><i></i> LIVE</span>
            <span v-else-if="currentConversation" class="live-label muted">历史已保存</span>
            <span v-if="currentConversation?.status === 'running'">
              <Activity :size="12" /> 执行中
            </span>
          </div>
        </div>

        <div class="topbar-controls">
          <label v-if="currentConversation" class="select-control">
            <span class="control-prefix">M</span>
            <select v-model="currentConversation.model" aria-label="模型" @change="updateModel">
              <option v-for="model in models" :key="model.id" :value="model.id">
                {{ model.name }}
              </option>
            </select>
            <ChevronDown :size="13" />
          </label>
          <label v-if="currentConversation" class="select-control effort-control">
            <span class="control-prefix"><Zap :size="12" /></span>
            <select
              v-model="currentConversation.reasoning_effort"
              aria-label="推理强度"
              @change="updateEffort"
            >
              <option v-for="effort in supportedEfforts" :key="effort" :value="effort">
                {{ effort }}
              </option>
            </select>
            <ChevronDown :size="13" />
          </label>

          <details v-if="currentConversation" class="more-menu">
            <summary class="icon-button" aria-label="更多操作"><MoreHorizontal :size="19" /></summary>
            <div class="menu-popover">
              <button @click="syncConversation"><RefreshCw :size="14" /> 同步历史</button>
              <button @click="forkConversation"><GitBranch :size="14" /> 创建分叉</button>
              <button @click="compactConversation"><Settings2 :size="14" /> 压缩上下文</button>
              <button
                v-if="!currentConversation.is_archived"
                @click="archiveConversation(currentConversation.id)"
              >
                <Archive :size="14" /> 归档
              </button>
              <button class="danger" @click="deleteConversation">
                <Trash2 :size="14" /> 永久删除
              </button>
            </div>
          </details>
          <button
            class="icon-button mobile-only"
            aria-label="打开能力台"
            @click="mobileCapabilities = true"
          >
            <PanelRight :size="19" />
          </button>
        </div>
      </header>

      <div v-if="bootError" class="boot-error">
        <strong>无法连接 Video Fission</strong>
        <span>{{ bootError }}</span>
        <button class="button subtle" @click="bootstrap">重试</button>
      </div>

      <div v-else-if="loading" class="loading-stage">
        <span class="loading-mark"></span>
        <p>正在准备创作工作台</p>
      </div>

      <MessageTimeline
        v-else
        ref="timeline"
        :conversation="currentConversation"
        @decide-approval="decideApproval"
        @answer-clarification="answerClarification"
        @copy="copyReply"
      />

      <section class="composer-dock">
        <div v-if="!hasConversation" class="prompt-rail">
          <button
            v-for="prompt in [
              ['把一个产品卖点拆成 30 秒短视频', '卖点拆解'],
              ['为美妆新品写一个反常识开场', '开场钩子'],
              ['设计一套三段式种草脚本', '脚本结构'],
            ]"
            :key="prompt[1]"
            data-testid="quick-prompt"
            @click="usePrompt(prompt[0])"
          >
            <span>{{ prompt[1] }}</span>
            {{ prompt[0] }}
            <ArrowUp :size="13" />
          </button>
        </div>

        <div class="composer">
          <div v-if="skillMentions.length || attachments.length" class="composer-context">
            <span
              v-for="mention in skillMentions"
              :key="mention.path"
              class="context-chip skill-chip"
            >
              @{{ mention.name }}
              <button aria-label="移除 Skill" @click="removeMention(mention.path)">
                <X :size="11" />
              </button>
            </span>
            <span v-for="file in attachments" :key="file.id" class="context-chip">
              {{ file.name }}
              <button :aria-label="`移除 ${file.name}`" @click="removeAttachment(file.id)">
                <X :size="11" />
              </button>
            </span>
          </div>
          <textarea
            ref="composerInput"
            v-model="composer"
            data-testid="message-input"
            rows="1"
            :placeholder="
              currentConversation?.status === 'running'
                ? '追问当前执行，或补充新的限制…'
                : '描述你想拆解、策划或完成的视频任务…'
            "
            @keydown="insertLineBreak"
          ></textarea>
          <div class="composer-footer">
            <div class="composer-tools">
              <label class="icon-button file-button" title="添加文件">
                <Paperclip :size="18" />
                <input
                  type="file"
                  multiple
                  hidden
                  :disabled="uploading"
                  @change="uploadFiles"
                />
              </label>
              <button
                class="capability-trigger"
                type="button"
                @click="mobileCapabilities = true"
              >
                <Zap :size="13" />
                {{ skills.length + tools.length }} 项能力
              </button>
              <span v-if="uploading" class="uploading-label">上传中…</span>
            </div>
            <div class="send-actions">
              <button
                v-if="currentConversation?.status === 'running'"
                class="stop-button"
                data-testid="stop-run"
                title="停止执行"
                @click="stopTurn"
              >
                <Square :size="15" fill="currentColor" />
              </button>
              <button
                class="send-button"
                :disabled="!canSend"
                data-testid="send-message"
                :title="currentConversation?.status === 'running' ? '追问' : '发送'"
                @click="send"
              >
                <ArrowUp :size="19" />
              </button>
            </div>
          </div>
        </div>
        <p class="composer-hint">
          <span>ENTER 发送 · SHIFT + ENTER 换行</span>
          <span>Codex 只读沙箱 · 工具输出进入会话记录</span>
        </p>
      </section>
    </main>

    <CapabilityPanel
      :skills="skills"
      :tools="tools"
      :mobile-open="mobileCapabilities"
      @create-skill="openSkillEditor()"
      @edit-skill="openSkillEditor"
      @delete-skill="deleteSkill"
      @toggle-skill="toggleSkill"
      @use-skill="useSkill"
      @close-mobile="mobileCapabilities = false"
    />

    <SkillEditorModal
      :open="skillModalOpen"
      :skill="editingSkill"
      :busy="skillBusy"
      @close="skillModalOpen = false"
      @save="saveSkill"
    />

    <Transition name="toast">
      <div v-if="toast" class="toast-message" role="status">
        <Check :size="15" />
        {{ toast }}
      </div>
    </Transition>
  </div>
</template>

