<template>
  <div class="session-panel">
    <div class="session-toolbar">
      <button
        class="icon-btn"
        title="New Chat"
        @click="createNew"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        >
          <line
            x1="12"
            y1="5"
            x2="12"
            y2="19"
          /><line
            x1="5"
            y1="12"
            x2="19"
            y2="12"
          />
        </svg>
      </button>
    </div>
    <div class="session-list">
      <div
        v-if="allSessions.length === 0 && !hasCurrent"
        class="panel-empty"
      >
        No sessions yet
      </div>
      <div
        v-if="hasCurrent && !currentInList"
        :class="['session-item', 'active']"
        @click="selectSession(currentId)"
      >
        <div class="si-title">
          {{ displayName(currentId) }}
        </div>
        <div class="si-preview">
          New conversation
        </div>
      </div>
      <div
        v-for="s in allSessions"
        :key="s.id"
        :class="['session-item', { active: s.id === currentId }]"
        @click="selectSession(s.id)"
      >
        <button
          class="si-delete"
          title="Delete"
          @click.stop="deleteSession(s.id)"
        >
          &times;
        </button>
        <div class="si-title">
          {{ displayName(s.id) }}
        </div>
        <div class="si-preview">
          {{ s.preview || 'Empty' }}
        </div>
        <div class="si-meta">
          <span class="si-time">{{ s.saved_at || '' }}</span>
          <span class="si-count">{{ s.message_count || 0 }} msgs</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useSession } from '@/composables/useSession.js'

const {
  currentId,
  sessions: allSessions,
  loadList,
  switchSession,
  createNew: createNewSession,
  removeSession,
  displayName: _displayName,
} = useSession()

const hasCurrent = computed(() => !!currentId.value)
const currentInList = computed(() => allSessions.value.some(s => s.id === currentId.value))

function displayName(id) {
  return _displayName(id)
}

async function selectSession(id) {
  await switchSession(id)
}

async function createNew() {
  createNewSession()
}

async function deleteSession(id) {
  if (!confirm(`Delete session "${displayName(id)}"?`)) return
  await removeSession(id)
}

onMounted(() => loadList())
</script>

<style scoped>
.session-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.session-toolbar {
  display: flex;
  justify-content: flex-end;
  padding: 7px 8px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.icon-btn {
  background: none;
  border: 1px solid transparent;
  color: var(--t3);
  cursor: pointer;
  border-radius: 7px;
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.12s;
}

.icon-btn:hover {
  background: var(--bg-100);
  color: var(--t1);
  border-color: var(--border-light);
}

.icon-btn svg {
  width: 14px;
  height: 14px;
}

.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.session-item {
  padding: 10px 11px;
  border: 1px solid transparent;
  border-radius: 8px;
  cursor: pointer;
  margin-bottom: 2px;
  transition: all 0.12s;
  position: relative;
}

.session-item:hover { background: var(--bg2); }

.session-item.active {
  background: var(--bg-100);
  border-color: var(--border-strong);
}

.si-title {
  font-size: 12px;
  font-weight: 650;
  font-family: var(--mono);
  color: var(--t1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 3px;
}

.si-preview {
  font-size: 11px;
  color: var(--t3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.si-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 4px;
}

.si-time {
  font-size: 10px;
  color: var(--t3);
  font-family: var(--mono);
}

.si-count {
  font-size: 10px;
  color: var(--t3);
  font-family: var(--mono);
  background: var(--bg-100);
  padding: 1px 5px;
  border-radius: 8px;
}

.si-delete {
  position: absolute;
  top: 8px;
  right: 8px;
  background: none;
  border: none;
  color: var(--t3);
  cursor: pointer;
  font-size: 12px;
  display: none;
  padding: 2px 4px;
  border-radius: 3px;
}

.session-item:hover .si-delete { display: block; }
.si-delete:hover {
  color: var(--red);
  background: var(--red-bg);
}

.panel-empty {
  padding: 20px;
  text-align: center;
  color: var(--t3);
  font-size: 12px;
}
</style>
