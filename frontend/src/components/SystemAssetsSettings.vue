<script setup>
import { Braces, Pencil, Plus, Sparkles, Trash2, Wrench } from '@lucide/vue'
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import SkillEditorModal from './SkillEditorModal.vue'

const emit = defineEmits(['notify', 'changed'])
const tab = ref('skills')
const skills = ref([])
const tools = ref([])
const loading = ref(true)
const error = ref('')
const editorOpen = ref(false)
const editingSkill = ref(null)
const saving = ref(false)

const customSkills = computed(() => skills.value.filter((skill) => !skill.read_only))

async function loadAssets() {
  loading.value = true
  error.value = ''
  try {
    const [skillPayload, toolPayload] = await Promise.all([api.skills(), api.tools()])
    skills.value = skillPayload.items || []
    tools.value = toolPayload.items || []
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '能力配置读取失败'
  } finally {
    loading.value = false
  }
}

function openEditor(skill = null) {
  editingSkill.value = skill
  editorOpen.value = true
}

async function saveSkill(payload) {
  saving.value = true
  try {
    if (editingSkill.value) {
      await api.updateSkill(editingSkill.value.slug, payload)
    } else {
      await api.createSkill(payload)
    }
    editorOpen.value = false
    await loadAssets()
    emit('changed')
    emit('notify', editingSkill.value ? 'Skill 已更新' : 'Skill 已创建')
  } catch (cause) {
    emit('notify', cause.message)
  } finally {
    saving.value = false
  }
}

async function toggleSkill(skill, enabled) {
  try {
    await api.toggleSkill(skill.slug, enabled)
    await loadAssets()
    emit('changed')
    emit('notify', enabled ? 'Skill 已启用' : 'Skill 已停用')
  } catch (cause) {
    emit('notify', cause.message)
    await loadAssets()
  }
}

async function deleteSkill(skill) {
  if (!window.confirm(`确定删除全局 Skill「${skill.name}」？`)) return
  try {
    await api.deleteSkill(skill.slug)
    await loadAssets()
    emit('changed')
    emit('notify', 'Skill 已删除')
  } catch (cause) {
    emit('notify', cause.message)
  }
}

onMounted(loadAssets)
</script>

<template>
  <section class="assets-panel">
    <header class="assets-head">
      <div class="assets-symbol"><Wrench :size="19" /></div>
      <div>
        <h2>Skill 与 Tool</h2>
        <p>全局能力配置，修改会对所有已登录用户生效。</p>
      </div>
      <div class="assets-tabs">
        <button :class="{ active: tab === 'skills' }" @click="tab = 'skills'">
          <Sparkles :size="13" /> Skills {{ skills.length }}
        </button>
        <button :class="{ active: tab === 'tools' }" @click="tab = 'tools'">
          <Braces :size="13" /> Tools {{ tools.length }}
        </button>
      </div>
    </header>

    <p v-if="loading" class="assets-state">正在读取全局能力…</p>
    <p v-else-if="error" class="assets-state assets-state--error">
      {{ error }}
      <button type="button" @click="loadAssets">重试</button>
    </p>

    <template v-else-if="tab === 'skills'">
      <div class="assets-toolbar">
        <span>{{ customSkills.length }} 个自定义 Skill · {{ skills.length - customSkills.length }} 个内置规范</span>
        <button class="button primary" data-testid="settings-create-skill" @click="openEditor()">
          <Plus :size="14" /> 新建 Skill
        </button>
      </div>
      <div class="global-skill-list">
        <article
          v-for="skill in skills"
          :key="`${skill.source}-${skill.slug}`"
          class="global-skill"
          :class="{ disabled: !skill.enabled }"
        >
          <span class="global-skill__icon"><Sparkles :size="15" /></span>
          <div class="global-skill__copy">
            <strong>{{ skill.name }}</strong>
            <span>{{ skill.source === 'bundled' ? '内置规范' : '全局自定义' }}</span>
            <p>{{ skill.description }}</p>
          </div>
          <label class="global-switch" :title="skill.read_only ? '内置 Skill 始终启用' : '全局启用或停用'">
            <input
              type="checkbox"
              :checked="skill.enabled"
              :disabled="skill.read_only"
              @change="toggleSkill(skill, $event.target.checked)"
            />
            <span></span>
          </label>
          <div v-if="!skill.read_only" class="global-skill__actions">
            <button type="button" title="编辑 Skill" @click="openEditor(skill)">
              <Pencil :size="14" />
            </button>
            <button class="danger" type="button" title="删除 Skill" @click="deleteSkill(skill)">
              <Trash2 :size="14" />
            </button>
          </div>
        </article>
      </div>
    </template>

    <div v-else class="tool-catalog">
      <details v-for="tool in tools" :key="tool.name" class="global-tool">
        <summary>
          <span class="global-skill__icon"><Braces :size="15" /></span>
          <div>
            <strong>{{ tool.title }}</strong>
            <code>{{ tool.name }}</code>
          </div>
        </summary>
        <p>{{ tool.description }}</p>
        <pre>{{ JSON.stringify(tool.input_schema, null, 2) }}</pre>
      </details>
    </div>

    <SkillEditorModal
      :open="editorOpen"
      :skill="editingSkill"
      :busy="saving"
      @close="editorOpen = false"
      @save="saveSkill"
    />
  </section>
</template>

<style scoped>
.assets-panel {
  padding: 24px;
  margin-top: 18px;
  background: var(--paper-bright);
  border: 1px solid var(--line);
  border-radius: 18px;
}
.assets-head {
  display: flex;
  align-items: center;
  gap: 13px;
}
.assets-symbol {
  display: grid;
  place-items: center;
  flex: 0 0 40px;
  width: 40px;
  height: 40px;
  color: var(--electric);
  background: rgba(46, 97, 243, 0.08);
  border-radius: 12px;
}
.assets-head h2 {
  margin: 0;
  font-size: 16px;
}
.assets-head p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 11px;
}
.assets-tabs {
  display: flex;
  gap: 5px;
  margin-left: auto;
  padding: 4px;
  background: var(--paper);
  border-radius: 10px;
}
.assets-tabs button {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 32px;
  padding: 0 11px;
  color: var(--muted);
  cursor: pointer;
  background: transparent;
  border: 0;
  border-radius: 7px;
  font: inherit;
  font-size: 10px;
}
.assets-tabs button.active {
  color: var(--ink);
  background: var(--paper-bright);
  box-shadow: 0 2px 8px rgba(18, 35, 31, 0.06);
}
.assets-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 20px;
  padding: 12px 14px;
  color: var(--muted);
  background: var(--paper);
  border-radius: 12px;
  font-size: 10px;
}
.assets-toolbar .button {
  min-height: 34px;
}
.global-skill-list,
.tool-catalog {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}
.global-skill {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) auto auto;
  gap: 11px;
  align-items: center;
  padding: 12px 8px;
  border-bottom: 1px solid var(--line);
}
.global-skill:last-child {
  border-bottom: 0;
}
.global-skill.disabled {
  opacity: 0.56;
}
.global-skill__icon {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  color: var(--signal-deep);
  background: rgba(240, 100, 69, 0.08);
  border-radius: 9px;
}
.global-skill__copy {
  display: grid;
  gap: 2px;
  min-width: 0;
}
.global-skill__copy strong {
  font-size: 12px;
}
.global-skill__copy > span {
  color: var(--muted);
  font-size: 9px;
}
.global-skill__copy p {
  margin: 3px 0 0;
  color: #65736c;
  font-size: 10px;
  line-height: 1.55;
}
.global-switch {
  position: relative;
  width: 35px;
  height: 20px;
}
.global-switch input {
  position: absolute;
  inset: 0;
  z-index: 1;
  width: 100%;
  height: 100%;
  margin: 0;
  cursor: pointer;
  opacity: 0;
}
.global-switch span {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: #c6cbc5;
  border-radius: 999px;
  transition: background 150ms ease;
}
.global-switch span::after {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 14px;
  height: 14px;
  content: "";
  background: white;
  border-radius: 50%;
  transition: transform 150ms ease;
}
.global-switch input:checked + span {
  background: var(--signal);
}
.global-switch input:checked + span::after {
  transform: translateX(15px);
}
.global-switch input:disabled + span {
  cursor: not-allowed;
  opacity: 0.55;
}
.global-skill__actions {
  display: flex;
  gap: 3px;
}
.global-skill__actions button {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  color: var(--muted);
  cursor: pointer;
  background: transparent;
  border: 0;
  border-radius: 8px;
}
.global-skill__actions button:hover {
  color: var(--ink);
  background: var(--paper);
}
.global-skill__actions button.danger:hover {
  color: var(--danger);
}
.global-tool {
  padding: 12px 14px;
  background: var(--paper);
  border-radius: 12px;
}
.global-tool summary {
  display: flex;
  align-items: center;
  gap: 10px;
  cursor: pointer;
  list-style: none;
}
.global-tool summary::-webkit-details-marker {
  display: none;
}
.global-tool summary > div {
  display: grid;
  gap: 2px;
}
.global-tool strong {
  font-size: 12px;
}
.global-tool code {
  color: var(--muted);
  font-size: 9px;
}
.global-tool p {
  margin: 12px 0 0 42px;
  color: var(--muted);
  font-size: 10px;
  line-height: 1.6;
}
.global-tool pre {
  overflow: auto;
  max-height: 250px;
  margin: 10px 0 0 42px;
  padding: 12px;
  color: #dbece4;
  background: #10201b;
  border-radius: 9px;
  font-size: 9px;
}
.assets-state {
  margin: 18px 0 0;
  color: var(--muted);
  font-size: 11px;
}
.assets-state--error {
  color: var(--danger);
}
.assets-state button {
  margin-left: 8px;
  color: var(--electric);
  cursor: pointer;
  background: transparent;
  border: 0;
}
@media (max-width: 720px) {
  .assets-head {
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .assets-tabs {
    width: 100%;
    margin-left: 0;
  }
  .assets-tabs button {
    flex: 1;
    justify-content: center;
  }
  .global-skill {
    grid-template-columns: 32px minmax(0, 1fr) auto;
  }
  .global-skill__actions {
    grid-column: 2 / -1;
    justify-content: flex-end;
  }
}
</style>
