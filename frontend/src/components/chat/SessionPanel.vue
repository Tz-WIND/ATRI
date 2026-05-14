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
    <div class="workstation-dock">
      <div class="dock-head">
        <span>Workstation</span>
        <span :class="['dock-dot', { on: host.running }]" />
      </div>
      <button
        class="workstation-open"
        @click="$emit('open-workstation')"
      >
        <span class="workstation-icon">
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          >
            <path d="M4 17V7" />
            <path d="M8 19V5" />
            <path d="M12 16V8" />
            <path d="M16 21V3" />
            <path d="M20 18V6" />
          </svg>
        </span>
        <span class="workstation-copy">
          <strong>{{ project?.title || 'ATRI Session' }}</strong>
          <small>{{ tracks.length }} tracks · {{ totalNotes }} notes</small>
        </span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useDawHost } from '@/composables/useDawHost.js'
import { useSession } from '@/composables/useSession.js'

defineEmits(['open-workstation'])

const {
  currentId,
  sessions: allSessions,
  loadList,
  switchSession,
  createNew: createNewSession,
  removeSession,
  displayName: _displayName,
} = useSession()

const { project, tracks, totalNotes, host, loadProject } = useDawHost()

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

onMounted(() => {
  loadList()
  loadProject()
})
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
  min-height: 120px;
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

.workstation-dock {
  flex-shrink: 0;
  padding: 9px 8px 10px;
  border-top: 1px solid var(--border);
  background: rgba(24, 24, 24, 0.5);
}

.dock-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 7px;
  color: var(--t3);
  font-family: var(--mono);
  font-size: 10px;
  text-transform: uppercase;
}

.dock-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--red);
  box-shadow: 0 0 0 3px rgba(255, 141, 127, 0.1);
}

.dock-dot.on {
  background: var(--ok);
  box-shadow: 0 0 0 3px rgba(143, 216, 199, 0.12);
}

.workstation-open {
  width: 100%;
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr);
  align-items: center;
  gap: 9px;
  min-height: 54px;
  padding: 8px;
  border: 1px solid rgba(240, 209, 122, 0.18);
  border-radius: 8px;
  background: rgba(240, 209, 122, 0.08);
  color: var(--t1);
  cursor: pointer;
  text-align: left;
}

.workstation-open:hover {
  background: rgba(240, 209, 122, 0.12);
  border-color: rgba(240, 209, 122, 0.28);
}

.workstation-icon {
  width: 34px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 7px;
  background: #16191c;
  color: #f0d17a;
}

.workstation-icon svg {
  width: 18px;
  height: 18px;
}

.workstation-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.workstation-copy strong,
.workstation-copy small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workstation-copy strong {
  font-size: 12px;
}

.workstation-copy small {
  color: var(--t3);
  font-size: 11px;
}
</style>
