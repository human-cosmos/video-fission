<script setup>
import { onMounted, reactive, ref } from 'vue'
import { KeyRound, Plus, Trash2, UsersRound } from '@lucide/vue'
import { api } from '../api'

const props = defineProps({
  currentUser: { type: Object, required: true },
})
const emit = defineEmits(['notify'])
const users = ref([])
const loading = ref(true)
const creating = ref(false)
const loadError = ref('')
const form = reactive({
  username: '',
  display_name: '',
  password: '',
  role: 'user',
})

async function loadUsers() {
  loading.value = true
  loadError.value = ''
  try {
    users.value = (await api.users()).items || []
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '成员列表读取失败'
  } finally {
    loading.value = false
  }
}

async function createUser() {
  if (!form.username.trim() || form.password.length < 8) {
    emit('notify', '用户名必填，密码至少 8 位')
    return
  }
  creating.value = true
  try {
    await api.createUser({
      username: form.username.trim(),
      display_name: form.display_name.trim(),
      password: form.password,
      role: form.role,
    })
    Object.assign(form, { username: '', display_name: '', password: '', role: 'user' })
    await loadUsers()
    emit('notify', '成员账号已创建')
  } catch (error) {
    emit('notify', error.message)
  } finally {
    creating.value = false
  }
}

async function updateRole(user, role) {
  try {
    const updated = await api.updateUser(user.id, { role })
    Object.assign(user, updated)
    emit('notify', '成员角色已更新')
  } catch (error) {
    emit('notify', error.message)
    await loadUsers()
  }
}

async function removeUser(user) {
  if (!window.confirm(`确定删除账号 @${user.username}？其历史数据将不再可访问。`)) return
  try {
    await api.deleteUser(user.id)
    await loadUsers()
    emit('notify', '成员账号已删除')
  } catch (error) {
    emit('notify', error.message)
  }
}

async function resetPassword(user) {
  const password = window.prompt(`为 @${user.username} 设置新密码（至少 8 位）`)
  if (password === null) return
  if (password.length < 8) {
    emit('notify', '密码至少需要 8 位')
    return
  }
  try {
    await api.updateUser(user.id, { password })
    emit('notify', user.id === props.currentUser.id ? '密码已更新，请重新登录' : '成员密码已重置')
    if (user.id === props.currentUser.id) {
      window.setTimeout(() => window.location.reload(), 700)
    }
  } catch (error) {
    emit('notify', error.message)
  }
}

onMounted(loadUsers)
</script>

<template>
  <section class="user-panel">
    <header>
      <div class="user-panel__icon"><UsersRound :size="19" /></div>
      <div>
        <h2>成员与账号</h2>
        <p>管理员可创建本地账号并调整成员角色。</p>
      </div>
    </header>

    <form class="user-create" @submit.prevent="createUser">
      <label>
        <span>用户名</span>
        <input v-model="form.username" autocomplete="off" placeholder="例如 zhangsan" />
      </label>
      <label>
        <span>显示名称</span>
        <input v-model="form.display_name" autocomplete="off" placeholder="可选" />
      </label>
      <label>
        <span>初始密码</span>
        <input v-model="form.password" type="password" autocomplete="new-password" placeholder="至少 8 位" />
      </label>
      <label>
        <span>角色</span>
        <select v-model="form.role">
          <option value="user">成员</option>
          <option value="admin">管理员</option>
        </select>
      </label>
      <button class="button primary" :disabled="creating">
        <Plus :size="15" />{{ creating ? '创建中…' : '创建账号' }}
      </button>
    </form>

    <div class="user-list">
      <p v-if="loading" class="user-empty">正在读取成员…</p>
      <p v-else-if="loadError" class="user-empty user-empty--error">
        {{ loadError }}
        <button type="button" @click="loadUsers">重试</button>
      </p>
      <template v-else>
        <div v-for="user in users" :key="user.id" class="user-row">
          <span class="user-avatar">{{ (user.display_name || user.username || 'U')[0].toUpperCase() }}</span>
          <div class="user-identity">
            <strong>{{ user.display_name || user.username }}</strong>
            <span>@{{ user.username }}</span>
          </div>
          <select
            :value="user.role"
            :disabled="user.id === currentUser.id"
            @change="updateRole(user, $event.target.value)"
          >
            <option value="user">成员</option>
            <option value="admin">管理员</option>
          </select>
          <button class="user-action" type="button" title="重置密码" @click="resetPassword(user)">
            <KeyRound :size="15" />
          </button>
          <button
            class="user-action user-action--danger"
            type="button"
            :disabled="user.id === currentUser.id"
            :title="user.id === currentUser.id ? '不能删除当前账号' : '删除账号'"
            @click="removeUser(user)"
          >
            <Trash2 :size="15" />
          </button>
        </div>
      </template>
    </div>
  </section>
</template>

<style scoped>
.user-panel {
  padding: 24px;
  margin-top: 18px;
  background: var(--paper-bright);
  border: 1px solid var(--line);
  border-radius: 10px;
}
.user-panel > header {
  display: flex;
  align-items: center;
  gap: 13px;
  margin-bottom: 20px;
}
.user-panel__icon {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  color: var(--electric);
  background: rgba(46, 97, 243, 0.08);
  border-radius: 9px;
}
.user-panel h2 {
  margin: 0;
  font-size: 16px;
}
.user-panel header p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 11px;
}
.user-create {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr)) auto;
  gap: 10px;
  align-items: end;
  padding: 16px;
  background: var(--paper);
  border-radius: 10px;
}
.user-create label {
  display: grid;
  gap: 6px;
  color: var(--muted);
  font-size: 10px;
}
.user-create input,
.user-create select,
.user-row select {
  height: 38px;
  min-width: 0;
  padding: 0 10px;
  color: var(--ink);
  background: var(--paper-bright);
  border: 1px solid var(--line);
  border-radius: 8px;
  font: inherit;
}
.user-create .button {
  height: 38px;
  white-space: nowrap;
}
.user-list {
  margin-top: 14px;
}
.user-row {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) 110px 34px 34px;
  gap: 11px;
  align-items: center;
  min-height: 58px;
  padding: 9px 6px;
  border-bottom: 1px solid var(--line);
}
.user-row:last-child {
  border-bottom: 0;
}
.user-avatar {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  color: var(--electric);
  background: #e6eefb;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 700;
}
.user-identity {
  display: grid;
  gap: 2px;
}
.user-identity strong {
  font-size: 12px;
}
.user-identity span {
  color: var(--muted);
  font-size: 10px;
}
.user-action {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  color: var(--electric);
  cursor: pointer;
  background: transparent;
  border: 0;
  border-radius: 8px;
}
.user-action:hover:not(:disabled) {
  background: rgba(46, 97, 243, 0.08);
}
.user-action--danger {
  color: var(--danger);
}
.user-action--danger:hover:not(:disabled) {
  background: rgba(190, 57, 47, 0.08);
}
.user-action:disabled {
  cursor: not-allowed;
  opacity: 0.25;
}
.user-empty {
  margin: 0;
  padding: 18px 4px;
  color: var(--muted);
  font-size: 11px;
}
.user-empty--error {
  color: var(--danger);
}
.user-empty button {
  margin-left: 8px;
  color: var(--electric);
  cursor: pointer;
  background: transparent;
  border: 0;
}
@media (max-width: 900px) {
  .user-create {
    grid-template-columns: 1fr 1fr;
  }
}
@media (max-width: 560px) {
  .user-panel {
    padding: 17px;
  }
  .user-create {
    grid-template-columns: 1fr;
  }
  .user-row {
    grid-template-columns: 34px minmax(0, 1fr) 82px 32px 32px;
  }
}
</style>
