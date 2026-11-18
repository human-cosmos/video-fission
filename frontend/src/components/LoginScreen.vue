<script setup>
import { computed, ref } from 'vue'
import { ArrowRight, Film, KeyRound, ShieldCheck, UserRound } from '@lucide/vue'
import BrandLockup from './BrandLockup.vue'

const props = defineProps({
  busy: { type: Boolean, default: false },
  error: { type: String, default: '' },
})
const emit = defineEmits(['login', 'sso'])

const username = ref('')
const password = ref('')
const localError = ref('')
const message = computed(() => localError.value || props.error)

function submit() {
  localError.value = ''
  if (!username.value.trim() || !password.value) {
    localError.value = '请输入用户名和密码'
    return
  }
  emit('login', {
    username: username.value.trim(),
    password: password.value,
  })
}
</script>

<template>
  <main class="auth-shell">
    <div class="auth-glow auth-glow--one"></div>
    <div class="auth-glow auth-glow--two"></div>

    <section class="auth-story">
      <BrandLockup />
      <div class="auth-kicker"><span></span> 私有的 AI 视频创作工作台</div>
      <h1>把灵感、会话与成片<br /><em>留在你的工作空间</em></h1>
      <p class="auth-lede">
        每个账号拥有独立的对话、模型配置与创作产物。登录后继续处理你的视频项目。
      </p>
      <div class="auth-features">
        <div><ShieldCheck :size="20" /><span><b>账号隔离</b>会话与文件按用户授权</span></div>
        <div><Film :size="20" /><span><b>项目续接</b>创作进度与成片集中归档</span></div>
        <div><KeyRound :size="20" /><span><b>双入口</b>账号密码或企业单点登录</span></div>
      </div>
    </section>

    <section class="auth-card">
      <div class="auth-card__mark"><Film :size="22" /></div>
      <p class="auth-eyebrow">VIDEO FISSION</p>
      <h2>登录工作台</h2>
      <p class="auth-subtitle">使用你的账号继续创作</p>

      <form @submit.prevent="submit">
        <label>
          <span>用户名</span>
          <div class="auth-input">
            <UserRound :size="18" />
            <input
              v-model="username"
              autocomplete="username"
              autofocus
              placeholder="输入用户名"
            />
          </div>
        </label>
        <label>
          <span>密码</span>
          <div class="auth-input">
            <KeyRound :size="18" />
            <input
              v-model="password"
              type="password"
              autocomplete="current-password"
              placeholder="输入密码"
            />
          </div>
        </label>

        <p v-if="message" class="auth-error" role="alert">{{ message }}</p>

        <button class="auth-submit" type="submit" :disabled="busy">
          {{ busy ? '正在登录…' : '进入工作台' }}
          <ArrowRight v-if="!busy" :size="18" />
        </button>
      </form>

      <div class="auth-divider"><span>或</span></div>
      <button class="auth-sso" type="button" :disabled="busy" @click="emit('sso')">
        企业单点登录（SSO）
      </button>
      <p class="auth-footnote">首次部署的引导管理员密码见数据目录中的 bootstrap_admin.txt</p>
    </section>
  </main>
</template>

<style scoped>
.auth-shell {
  --auth-ink: #1e2733;
  min-height: 100vh;
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(380px, 0.75fr);
  gap: clamp(40px, 8vw, 130px);
  align-items: center;
  position: relative;
  overflow: hidden;
  padding: clamp(36px, 7vw, 96px);
  color: var(--auth-ink);
  background: #f5f7fb;
}
.auth-glow {
  display: none;
}
.auth-glow--one {
  width: 420px;
  height: 420px;
  left: -170px;
  bottom: -220px;
  background: rgba(74, 141, 248, 0.08);
}
.auth-glow--two {
  width: 280px;
  height: 280px;
  right: 8%;
  top: -170px;
  background: rgba(28, 140, 114, 0.08);
}
.auth-story,
.auth-card {
  position: relative;
  z-index: 1;
}
.auth-story {
  max-width: 720px;
}
.auth-kicker {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: clamp(70px, 13vh, 130px);
  color: #667085;
  font-size: 12px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}
.auth-kicker span {
  width: 34px;
  height: 1px;
  background: var(--electric);
}
.auth-story h1 {
  margin: 20px 0 22px;
  max-width: 700px;
  font-family: var(--font-display);
  font-size: clamp(44px, 4.8vw, 68px);
  font-weight: 650;
  line-height: 1.06;
  letter-spacing: 0;
}
.auth-story h1 em {
  color: var(--electric);
  font-style: normal;
  font-weight: 650;
}
.auth-lede {
  max-width: 580px;
  color: #667085;
  font-size: 16px;
  line-height: 1.8;
}
.auth-features {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 44px;
}
.auth-features div {
  display: flex;
  gap: 12px;
  align-items: center;
  min-width: 190px;
  padding: 14px 16px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: #ffffff;
  color: #667085;
  font-size: 12px;
}
.auth-features svg {
  flex: none;
  color: var(--electric);
}
.auth-features span {
  display: grid;
  gap: 3px;
}
.auth-features b {
  color: var(--ink);
  font-size: 13px;
}
.auth-card {
  width: 100%;
  max-width: 450px;
  justify-self: end;
  padding: clamp(28px, 4.4vw, 46px);
  border: 1px solid var(--line);
  border-radius: 14px;
  background: #ffffff;
  box-shadow: 0 18px 48px rgba(30, 39, 51, 0.1);
}
.auth-card__mark {
  width: 46px;
  height: 46px;
  display: grid;
  place-items: center;
  margin-bottom: 26px;
  border-radius: 10px;
  color: #ffffff;
  background: var(--electric);
}
.auth-eyebrow {
  margin: 0 0 8px;
  color: #667085;
  font-size: 10px;
  letter-spacing: 0.2em;
}
.auth-card h2 {
  margin: 0;
  font-family: var(--font-display);
  font-size: 34px;
  font-weight: 650;
  letter-spacing: 0;
}
.auth-subtitle {
  margin: 8px 0 30px;
  color: #667085;
  font-size: 13px;
}
.auth-card form,
.auth-card label {
  display: grid;
}
.auth-card form {
  gap: 18px;
}
.auth-card label {
  gap: 8px;
}
.auth-card label > span {
  color: #475467;
  font-size: 12px;
}
.auth-input {
  min-height: 50px;
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 0 15px;
  border: 1px solid var(--line);
  border-radius: 9px;
  background: #f8fafc;
  transition: border-color 160ms ease, box-shadow 160ms ease;
}
.auth-input:focus-within {
  border-color: #8fb0e8;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.1);
}
.auth-input svg {
  color: #7c8da3;
}
.auth-input input {
  min-width: 0;
  flex: 1;
  border: 0;
  outline: 0;
  color: var(--ink);
  background: transparent;
  font: inherit;
}
.auth-input input::placeholder {
  color: #98a2b3;
}
.auth-error {
  margin: -3px 0 0;
  padding: 10px 12px;
  border-radius: 10px;
  color: #a84333;
  background: #fff0ed;
  font-size: 12px;
}
.auth-submit,
.auth-sso {
  min-height: 48px;
  border: 0;
  border-radius: 9px;
  cursor: pointer;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  transition:
    background 150ms ease,
    border-color 150ms ease,
    color 150ms ease,
    transform 150ms ease;
}
.auth-submit {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 9px;
  margin-top: 4px;
  color: #ffffff;
  background: var(--electric);
}
.auth-submit:hover:not(:disabled) {
  background: #1d4ed8;
}

.auth-submit:active:not(:disabled),
.auth-sso:active:not(:disabled) {
  transform: translateY(1px);
}
.auth-submit:disabled,
.auth-sso:disabled {
  cursor: wait;
  opacity: 0.62;
}
.auth-divider {
  position: relative;
  margin: 26px 0 18px;
  border-top: 1px solid var(--line);
  text-align: center;
}
.auth-divider span {
  position: relative;
  top: -9px;
  padding: 0 12px;
  color: #98a2b3;
  background: #ffffff;
  font-size: 11px;
}
.auth-sso {
  width: 100%;
  border: 1px solid var(--line-strong);
  color: var(--ink);
  background: #ffffff;
}
.auth-sso:hover:not(:disabled) {
  border-color: #8fb0e8;
  background: #eff5ff;
  color: #1d4ed8;
}
.auth-footnote {
  margin: 19px 0 0;
  color: #98a2b3;
  font-size: 10px;
  line-height: 1.6;
  text-align: center;
}
@media (max-width: 900px) {
  .auth-shell {
    grid-template-columns: 1fr;
    align-items: start;
    padding: 28px 20px 46px;
  }
  .auth-story {
    display: none;
  }
  .auth-card {
    max-width: 480px;
    justify-self: center;
    margin-top: 8vh;
  }
}
</style>
