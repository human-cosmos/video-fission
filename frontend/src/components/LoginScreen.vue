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
  --auth-ink: #e8f6ed;
  min-height: 100vh;
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(380px, 0.75fr);
  gap: clamp(40px, 8vw, 130px);
  align-items: center;
  position: relative;
  overflow: hidden;
  padding: clamp(36px, 7vw, 96px);
  color: var(--auth-ink);
  background:
    linear-gradient(rgba(151, 214, 177, 0.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(151, 214, 177, 0.045) 1px, transparent 1px),
    radial-gradient(circle at 18% 20%, #174236 0, transparent 38%),
    #07130f;
  background-size: 44px 44px, 44px 44px, auto, auto;
}
.auth-glow {
  position: absolute;
  border-radius: 999px;
  filter: blur(2px);
  pointer-events: none;
}
.auth-glow--one {
  width: 420px;
  height: 420px;
  left: -170px;
  bottom: -220px;
  background: rgba(208, 255, 104, 0.11);
}
.auth-glow--two {
  width: 280px;
  height: 280px;
  right: 8%;
  top: -170px;
  background: rgba(87, 224, 182, 0.1);
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
  color: #a9cbbb;
  font-size: 12px;
  letter-spacing: 0.16em;
  text-transform: uppercase;
}
.auth-kicker span {
  width: 34px;
  height: 1px;
  background: #d7ff70;
}
.auth-story h1 {
  margin: 20px 0 22px;
  max-width: 700px;
  font-family: Georgia, 'Times New Roman', serif;
  font-size: clamp(44px, 4.8vw, 68px);
  font-weight: 400;
  line-height: 0.98;
  letter-spacing: -0.045em;
}
.auth-story h1 em {
  color: #d7ff70;
  font-weight: 400;
}
.auth-lede {
  max-width: 580px;
  color: #a8beb4;
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
  border: 1px solid rgba(179, 219, 196, 0.15);
  border-radius: 16px;
  background: rgba(11, 31, 24, 0.66);
  color: #bfd3c8;
  font-size: 12px;
}
.auth-features svg {
  flex: none;
  color: #d7ff70;
}
.auth-features span {
  display: grid;
  gap: 3px;
}
.auth-features b {
  color: #eef9f1;
  font-size: 13px;
}
.auth-card {
  width: 100%;
  max-width: 450px;
  justify-self: end;
  padding: clamp(28px, 4.4vw, 46px);
  border: 1px solid rgba(210, 240, 222, 0.16);
  border-radius: 28px;
  background: rgba(10, 26, 21, 0.88);
  box-shadow: 0 36px 100px rgba(0, 0, 0, 0.42);
  backdrop-filter: blur(22px);
}
.auth-card__mark {
  width: 46px;
  height: 46px;
  display: grid;
  place-items: center;
  margin-bottom: 26px;
  border-radius: 14px;
  color: #0d1c16;
  background: #d7ff70;
}
.auth-eyebrow {
  margin: 0 0 8px;
  color: #799789;
  font-size: 10px;
  letter-spacing: 0.2em;
}
.auth-card h2 {
  margin: 0;
  font-family: Georgia, 'Times New Roman', serif;
  font-size: 34px;
  font-weight: 400;
  letter-spacing: -0.03em;
}
.auth-subtitle {
  margin: 8px 0 30px;
  color: #839b8f;
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
  color: #b4c9bf;
  font-size: 12px;
}
.auth-input {
  min-height: 50px;
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 0 15px;
  border: 1px solid rgba(203, 232, 215, 0.13);
  border-radius: 14px;
  background: rgba(4, 14, 10, 0.6);
  transition: border-color 160ms ease, box-shadow 160ms ease;
}
.auth-input:focus-within {
  border-color: rgba(215, 255, 112, 0.58);
  box-shadow: 0 0 0 3px rgba(215, 255, 112, 0.08);
}
.auth-input svg {
  color: #6e8c7e;
}
.auth-input input {
  min-width: 0;
  flex: 1;
  border: 0;
  outline: 0;
  color: #f1faf4;
  background: transparent;
  font: inherit;
}
.auth-input input::placeholder {
  color: #52695e;
}
.auth-error {
  margin: -3px 0 0;
  padding: 10px 12px;
  border-radius: 10px;
  color: #ffc7b5;
  background: rgba(190, 71, 42, 0.13);
  font-size: 12px;
}
.auth-submit,
.auth-sso {
  min-height: 50px;
  border: 0;
  border-radius: 14px;
  cursor: pointer;
  font: inherit;
  font-weight: 650;
}
.auth-submit {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 9px;
  margin-top: 4px;
  color: #0d1c16;
  background: #d7ff70;
}
.auth-submit:hover:not(:disabled) {
  background: #e5ff9b;
}
.auth-submit:disabled,
.auth-sso:disabled {
  cursor: wait;
  opacity: 0.62;
}
.auth-divider {
  position: relative;
  margin: 26px 0 18px;
  border-top: 1px solid rgba(201, 230, 213, 0.1);
  text-align: center;
}
.auth-divider span {
  position: relative;
  top: -9px;
  padding: 0 12px;
  color: #61786d;
  background: #0a1a15;
  font-size: 11px;
}
.auth-sso {
  width: 100%;
  border: 1px solid rgba(203, 232, 215, 0.18);
  color: #dcebe3;
  background: transparent;
}
.auth-sso:hover:not(:disabled) {
  border-color: rgba(215, 255, 112, 0.4);
  color: #efffc5;
}
.auth-footnote {
  margin: 19px 0 0;
  color: #597064;
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
