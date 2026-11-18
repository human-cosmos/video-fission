<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { needsRemoteFields } from '../utils/format'

const emit = defineEmits(['notify'])

const loading = ref(true)
const saving = ref(false)
const error = ref('')
const settings = ref(null)
const testing = reactive({
  multimodal: false,
  video_generator: false,
  tts: false,
})
const testResults = reactive({
  multimodal: null,
  video_generator: null,
  tts: null,
})

const form = reactive(emptyForm())
const keys = reactive({
  multimodal: '',
  video_generator: '',
  tts: '',
})
const clearKey = reactive({
  multimodal: false,
  video_generator: false,
  tts: false,
})

const MULTIMODAL_PROVIDERS = [
  { value: 'local_ffmpeg', label: 'local_ffmpeg（本地默认）' },
  { value: 'openai_compatible', label: 'openai_compatible' },
]
const VIDEO_PROVIDERS = [
  { value: 'local_ffmpeg', label: 'local_ffmpeg（本地默认）' },
  { value: 'openai_compatible', label: 'openai_compatible' },
  { value: 'generic_http', label: 'generic_http' },
]
const TTS_PROVIDERS = [
  { value: 'none', label: 'none（不配音）' },
  { value: 'openai_compatible', label: 'openai_compatible' },
]

function emptyProvider() {
  return {
    provider: 'local_ffmpeg',
    base_url: '',
    model: '',
    timeout_seconds: 120,
    api_key_set: false,
  }
}

function emptyForm() {
  return {
    multimodal: { ...emptyProvider(), timeout_seconds: 120 },
    video_generator: {
      ...emptyProvider(),
      timeout_seconds: 300,
      fallback_to_local: true,
    },
    tts: {
      provider: 'none',
      base_url: '',
      model: '',
      timeout_seconds: 120,
      api_key_set: false,
      voice: 'alloy',
    },
    compose: {
      burn_in_subtitles: true,
      mix_reference_bgm: true,
      bgm_volume: 0.18,
    },
  }
}

function applySettings(payload) {
  const next = emptyForm()
  for (const key of ['multimodal', 'video_generator', 'tts']) {
    Object.assign(next[key], payload?.[key] || {})
  }
  Object.assign(next.compose, payload?.compose || {})
  Object.assign(form.multimodal, next.multimodal)
  Object.assign(form.video_generator, next.video_generator)
  Object.assign(form.tts, next.tts)
  Object.assign(form.compose, next.compose)
  settings.value = payload
  keys.multimodal = ''
  keys.video_generator = ''
  keys.tts = ''
  clearKey.multimodal = false
  clearKey.video_generator = false
  clearKey.tts = false
}

function providerPayload(name) {
  const source = form[name]
  const payload = {
    provider: source.provider,
    base_url: source.base_url || '',
    model: source.model || '',
    timeout_seconds: Number(source.timeout_seconds) || 0,
  }
  if (name === 'video_generator') {
    payload.fallback_to_local = Boolean(source.fallback_to_local)
  }
  if (name === 'tts') {
    payload.voice = source.voice || 'alloy'
  }
  if (clearKey[name]) {
    payload.api_key = null
  } else if (keys[name]) {
    payload.api_key = keys[name]
  }
  return payload
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
      multimodal: providerPayload('multimodal'),
      video_generator: providerPayload('video_generator'),
      tts: providerPayload('tts'),
      compose: {
        burn_in_subtitles: Boolean(form.compose.burn_in_subtitles),
        mix_reference_bgm: Boolean(form.compose.mix_reference_bgm),
        bgm_volume: Number(form.compose.bgm_volume),
      },
    }
    applySettings(await api.updateSettings(payload))
    emit('notify', '模型设置已保存')
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    saving.value = false
  }
}

async function testTarget(target) {
  testing[target] = true
  testResults[target] = null
  try {
    testResults[target] = await api.testSettings(target)
  } catch (err) {
    testResults[target] = {
      ok: false,
      provider: form[target].provider,
      detail: err instanceof Error ? err.message : String(err),
    }
  } finally {
    testing[target] = false
  }
}

function markClearKey(name) {
  clearKey[name] = true
  keys[name] = ''
}

function keyPlaceholder(name) {
  if (clearKey[name]) return '将在保存后清除'
  if (form[name].api_key_set) return '已设置，留空则保留'
  return '未设置'
}

onMounted(loadSettings)
</script>

<template>
  <section class="page-scroll" data-testid="model-settings-page">
    <div class="page-stack">
      <header class="page-intro">
        <p>
          默认使用 <code>local_ffmpeg</code>，无需 API Key 即可跑完成本地管线。密钥只写不回显，保存后不会出现在此页面或日志里。
        </p>
      </header>

      <p v-if="error" class="page-error">{{ error }}</p>
      <p v-else-if="loading" class="page-muted">正在读取设置…</p>

      <form v-else class="settings-grid" @submit.prevent="saveSettings">
        <fieldset class="settings-card">
          <legend>多模态理解</legend>
          <label>
            提供者
            <select v-model="form.multimodal.provider">
              <option v-for="item in MULTIMODAL_PROVIDERS" :key="item.value" :value="item.value">
                {{ item.label }}
              </option>
            </select>
          </label>
          <template v-if="needsRemoteFields(form.multimodal.provider)">
            <label>
              接口地址
              <input v-model="form.multimodal.base_url" type="url" placeholder="https://api.example.com/v1" autocomplete="off" />
            </label>
            <label>
              模型
              <input v-model="form.multimodal.model" type="text" autocomplete="off" />
            </label>
            <label>
              API 密钥
              <input
                v-model="keys.multimodal"
                type="password"
                autocomplete="new-password"
                :placeholder="keyPlaceholder('multimodal')"
                @input="clearKey.multimodal = false"
              />
            </label>
          </template>
          <label class="compact-field">
            超时（秒）
            <input v-model.number="form.multimodal.timeout_seconds" type="number" min="1" max="3600" />
          </label>
          <div class="settings-actions">
            <button type="button" class="button subtle" :disabled="!form.multimodal.api_key_set && !keys.multimodal" @click="markClearKey('multimodal')">
              清除 Key
            </button>
            <button type="button" class="button subtle" :disabled="testing.multimodal" data-testid="test-multimodal" @click="testTarget('multimodal')">
              {{ testing.multimodal ? '测试中…' : '测试连接' }}
            </button>
          </div>
          <p v-if="testResults.multimodal" class="test-detail" :class="{ ok: testResults.multimodal.ok }">
            {{ testResults.multimodal.ok ? '通过' : '失败' }}
            · {{ testResults.multimodal.provider }}
            · {{ testResults.multimodal.detail }}
          </p>
        </fieldset>

        <fieldset class="settings-card">
          <legend>视频生成</legend>
          <label>
            提供者
            <select v-model="form.video_generator.provider">
              <option v-for="item in VIDEO_PROVIDERS" :key="item.value" :value="item.value">
                {{ item.label }}
              </option>
            </select>
          </label>
          <template v-if="needsRemoteFields(form.video_generator.provider)">
            <label>
              接口地址
              <input v-model="form.video_generator.base_url" type="url" placeholder="https://api.example.com/v1" autocomplete="off" />
            </label>
            <label>
              模型
              <input v-model="form.video_generator.model" type="text" autocomplete="off" />
            </label>
            <label>
              API 密钥
              <input
                v-model="keys.video_generator"
                type="password"
                autocomplete="new-password"
                :placeholder="keyPlaceholder('video_generator')"
                @input="clearKey.video_generator = false"
              />
            </label>
          </template>
          <label class="compact-field">
            超时（秒）
            <input v-model.number="form.video_generator.timeout_seconds" type="number" min="1" max="3600" />
          </label>
          <label class="check-row">
            <input v-model="form.video_generator.fallback_to_local" type="checkbox" />
            远端失败时回退到 local_ffmpeg
          </label>
          <div class="settings-actions">
            <button type="button" class="button subtle" :disabled="!form.video_generator.api_key_set && !keys.video_generator" @click="markClearKey('video_generator')">
              清除 Key
            </button>
            <button type="button" class="button subtle" :disabled="testing.video_generator" data-testid="test-video-generator" @click="testTarget('video_generator')">
              {{ testing.video_generator ? '测试中…' : '测试连接' }}
            </button>
          </div>
          <p v-if="testResults.video_generator" class="test-detail" :class="{ ok: testResults.video_generator.ok }">
            {{ testResults.video_generator.ok ? '通过' : '失败' }}
            · {{ testResults.video_generator.provider }}
            · {{ testResults.video_generator.detail }}
          </p>
        </fieldset>

        <fieldset class="settings-card">
          <legend>配音 TTS</legend>
          <label>
            提供者
            <select v-model="form.tts.provider">
              <option v-for="item in TTS_PROVIDERS" :key="item.value" :value="item.value">
                {{ item.label }}
              </option>
            </select>
          </label>
          <template v-if="needsRemoteFields(form.tts.provider)">
            <label>
              接口地址
              <input v-model="form.tts.base_url" type="url" placeholder="https://api.example.com/v1" autocomplete="off" />
            </label>
            <label>
              模型
              <input v-model="form.tts.model" type="text" autocomplete="off" />
            </label>
            <label>
              API 密钥
              <input
                v-model="keys.tts"
                type="password"
                autocomplete="new-password"
                :placeholder="keyPlaceholder('tts')"
                @input="clearKey.tts = false"
              />
            </label>
          </template>
          <label>
            音色
            <input v-model="form.tts.voice" type="text" autocomplete="off" />
          </label>
          <div class="settings-actions">
            <button type="button" class="button subtle" :disabled="!form.tts.api_key_set && !keys.tts" @click="markClearKey('tts')">
              清除 Key
            </button>
            <button type="button" class="button subtle" :disabled="testing.tts" data-testid="test-tts" @click="testTarget('tts')">
              {{ testing.tts ? '测试中…' : '测试连接' }}
            </button>
          </div>
          <p v-if="testResults.tts" class="test-detail" :class="{ ok: testResults.tts.ok }">
            {{ testResults.tts.ok ? '通过' : '失败' }}
            · {{ testResults.tts.provider }}
            · {{ testResults.tts.detail }}
          </p>
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
    </div>
  </section>
</template>
