<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import SystemAssetsSettings from './SystemAssetsSettings.vue'
import UserManagement from './UserManagement.vue'

const props = defineProps({
  currentUser: { type: Object, required: true },
})
const emit = defineEmits(['notify'])

const loading = ref(true)
const saving = ref(false)
const error = ref('')

const form = reactive(emptyForm())

function emptyUnderstand() {
  return {
    transition_detection: true,
    clip_analysis: true,
    observe: true,
    clip_workers: 3,
    gemini_max_size_mb: 15,
    observe_timeout_seconds: 360,
  }
}

function emptyForm() {
  return {
    compose: {
      burn_in_subtitles: true,
      mix_reference_bgm: true,
      bgm_volume: 0.18,
    },
    understand: emptyUnderstand(),
  }
}

function applySettings(payload) {
  const next = emptyForm()
  Object.assign(next.compose, payload?.compose || {})
  Object.assign(next.understand, payload?.understand || {})
  Object.assign(form.compose, next.compose)
  Object.assign(form.understand, next.understand)
}

const canSave = computed(() => !saving.value && !loading.value)

async function loadSettings() {
  loading.value = true
  error.value = ''
  try {
    applySettings(await api.settings())
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

async function saveSettings() {
  saving.value = true
  error.value = ''
  try {
    const payload = {
      compose: {
        burn_in_subtitles: Boolean(form.compose.burn_in_subtitles),
        mix_reference_bgm: Boolean(form.compose.mix_reference_bgm),
        bgm_volume: Number(form.compose.bgm_volume),
      },
      understand: {
        transition_detection: Boolean(form.understand.transition_detection),
        clip_analysis: Boolean(form.understand.clip_analysis),
        observe: Boolean(form.understand.observe),
        clip_workers: Number(form.understand.clip_workers) || 1,
        gemini_max_size_mb: Number(form.understand.gemini_max_size_mb) || 15,
        observe_timeout_seconds: Number(form.understand.observe_timeout_seconds) || 360,
      },
    }
    applySettings(await api.updateSettings(payload))
    emit('notify', '设置已保存')
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    saving.value = false
  }
}

onMounted(loadSettings)
</script>

<template>
  <section class="page-scroll" data-testid="model-settings-page">
    <div class="page-stack">
      <header class="page-intro">
        <p>
          系统级配置统一在此维护。Skill 与 Tool 为全局能力。
        </p>
      </header>

      <p v-if="error" class="page-error">{{ error }}</p>
      <p v-else-if="loading" class="page-muted">正在读取设置…</p>

      <form v-else class="settings-grid" @submit.prevent="saveSettings">
        <fieldset class="settings-card" data-testid="settings-understand">
          <legend>理解与切片</legend>
          <label class="check-row">
            <input
              v-model="form.understand.transition_detection"
              type="checkbox"
              data-testid="settings-understand-transition_detection"
            />
            转场检测（缺依赖时自动退化）
          </label>
          <label class="check-row">
            <input
              v-model="form.understand.clip_analysis"
              type="checkbox"
              data-testid="settings-understand-clip_analysis"
            />
            片段级理解
          </label>
          <label class="check-row">
            <input
              v-model="form.understand.observe"
              type="checkbox"
              data-testid="settings-understand-observe"
            />
            生成观察卡
          </label>
          <label class="compact-field">
            片段并发
            <input v-model.number="form.understand.clip_workers" type="number" min="1" max="8" />
          </label>
          <label class="compact-field">
            送模型视频上限（MB）
            <input v-model.number="form.understand.gemini_max_size_mb" type="number" min="1" max="100" />
          </label>
          <label class="compact-field">
            观察卡超时（秒）
            <input
              v-model.number="form.understand.observe_timeout_seconds"
              type="number"
              min="30"
              max="3600"
              data-testid="settings-understand-observe_timeout_seconds"
            />
          </label>
        </fieldset>

        <fieldset class="settings-card">
          <legend>合成</legend>
          <label class="check-row">
            <input v-model="form.compose.burn_in_subtitles" type="checkbox" />
            硬烧字幕（同时保留 sidecar SRT）
          </label>
          <label class="check-row">
            <input v-model="form.compose.mix_reference_bgm" type="checkbox" />
            融合参考视频 BGM / 原声
          </label>
          <label class="compact-field">
            BGM 音量
            <input v-model.number="form.compose.bgm_volume" type="number" min="0" max="1" step="0.01" />
          </label>
        </fieldset>

        <div class="settings-submit">
          <button class="button primary" type="submit" :disabled="!canSave" data-testid="save-settings">
            {{ saving ? '保存中…' : '保存设置' }}
          </button>
          <button class="button subtle" type="button" :disabled="loading" @click="loadSettings">重新加载</button>
        </div>
      </form>
      <SystemAssetsSettings
        @notify="emit('notify', $event)"
      />
      <UserManagement
        v-if="props.currentUser.role === 'admin'"
        :current-user="props.currentUser"
        @notify="emit('notify', $event)"
      />
    </div>
  </section>
</template>
