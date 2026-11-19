<script setup>
import SystemAssetsSettings from './SystemAssetsSettings.vue'
import UserManagement from './UserManagement.vue'

const props = defineProps({
  currentUser: { type: Object, required: true },
})
const emit = defineEmits(['notify'])
</script>

<template>
  <section class="page-scroll" data-testid="model-settings-page">
    <div class="page-stack">
      <header class="page-intro">
        <p>
          系统级配置统一在此维护。Skill 与 Tool 为全局能力，账号权限按用户分别管理。
        </p>
      </header>

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
