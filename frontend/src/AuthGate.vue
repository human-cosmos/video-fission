<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from './api'
import App from './App.vue'
import LoginScreen from './components/LoginScreen.vue'

const TOKEN_KEY = 'video-fission.token'
const ready = ref(false)
const busy = ref(false)
const user = ref(null)
const error = ref('')

function clearSession() {
  user.value = null
  window.localStorage.removeItem(TOKEN_KEY)
  window.localStorage.removeItem('video-fission.activeConversation')
}

async function loadSession() {
  try {
    user.value = await api.me()
  } catch {
    clearSession()
  } finally {
    ready.value = true
  }
}

async function login(credentials) {
  busy.value = true
  error.value = ''
  try {
    const payload = await api.login(credentials.username, credentials.password)
    window.localStorage.setItem(TOKEN_KEY, payload.access_token)
    user.value = payload.user
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '登录失败'
  } finally {
    busy.value = false
  }
}

async function startSSO() {
  clearSession()
  busy.value = true
  error.value = ''
  try {
    await api.logout()
    const payload = await api.ssoLoginUrl()
    window.location.assign(payload.auth_url)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : '企业登录暂不可用'
    busy.value = false
  }
}

async function logout() {
  busy.value = true
  try {
    await api.logout()
  } finally {
    clearSession()
    busy.value = false
  }
}

function onAuthExpired() {
  clearSession()
}

onMounted(async () => {
  const params = new URLSearchParams(window.location.search)
  const ssoSuccess = params.get('sso') === 'success'
  const ssoError = params.get('sso_error')
  if (ssoError) {
    error.value = `企业登录失败：${ssoError}`
  }
  if (ssoSuccess || ssoError) {
    window.history.replaceState({}, '', window.location.pathname + window.location.hash)
  }
  await loadSession()
  window.addEventListener('video-fission:auth-expired', onAuthExpired)
})

onBeforeUnmount(() => {
  window.removeEventListener('video-fission:auth-expired', onAuthExpired)
})
</script>

<template>
  <div v-if="!ready" class="auth-boot">
    <span></span>
    <p>正在确认登录状态</p>
  </div>
  <LoginScreen
    v-else-if="!user"
    :busy="busy"
    :error="error"
    @login="login"
    @sso="startSSO"
  />
  <App v-else :current-user="user" @logout="logout" />
</template>

<style scoped>
.auth-boot {
  min-height: 100vh;
  display: grid;
  place-content: center;
  gap: 16px;
  color: #667085;
  background: #f5f7fb;
  text-align: center;
}
.auth-boot span {
  width: 34px;
  height: 34px;
  margin: auto;
  border: 2px solid #dce4ee;
  border-top-color: #2563eb;
  border-radius: 999px;
  animation: auth-spin 0.8s linear infinite;
}
.auth-boot p {
  margin: 0;
  font-size: 12px;
  letter-spacing: 0.08em;
}
@keyframes auth-spin {
  to { transform: rotate(360deg); }
}
</style>
