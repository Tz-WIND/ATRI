<template>
  <div class="status-bar">
    <div class="status-left">
      <span
        v-if="activeModel"
        class="status-item"
      >
        {{ activeModel }}
      </span>
    </div>
    <div class="status-right">
      <span
        v-if="tokenInfo"
        class="status-item"
      >
        {{ tokenCount }} tokens
        <template v-if="tokenInfo.cost != null">
          &middot; ${{ tokenInfo.cost.toFixed(4) }}
        </template>
      </span>
      <span
        v-if="sessionCount != null"
        class="status-item"
      >
        {{ sessionCount }} sessions
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useSession } from '@/composables/useSession.js'
import { useProviders } from '@/composables/useProviders.js'
import { useChat } from '@/composables/useChat.js'

const { sessions } = useSession()
const { activeModel } = useProviders()
const { tokenInfo } = useChat()

const tokenCount = computed(() => {
  if (!tokenInfo.value) return ''
  return ((tokenInfo.value.prompt || 0) + (tokenInfo.value.completion || 0)).toLocaleString()
})
const sessionCount = computed(() => sessions.value.length || null)
</script>

<style scoped>
.status-bar {
  height: var(--status-h);
  background: var(--acc);
  color: rgba(24, 24, 24, 0.86);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 12px;
  font-size: 11px;
  font-family: var(--mono);
  flex-shrink: 0;
  user-select: none;
}

.status-left,
.status-right {
  display: flex;
  align-items: center;
  gap: 14px;
}

.status-item {
  display: flex;
  align-items: center;
  gap: 5px;
  opacity: 0.9;
}
</style>
