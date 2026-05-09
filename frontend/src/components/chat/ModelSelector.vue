<template>
  <div
    ref="dropdown"
    class="model-dropdown"
  >
    <button
      class="model-chip"
      @click="toggleOpen"
    >
      <svg
        class="chip-icon"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
      >
        <circle
          cx="12"
          cy="12"
          r="3"
        /><path d="M12 1v4m0 14v4M4.22 4.22l2.83 2.83m9.9 9.9l2.83 2.83M1 12h4m14 0h4M4.22 19.78l2.83-2.83m9.9-9.9l2.83-2.83" />
      </svg>
      <span>{{ activeModel || 'Select model' }}</span>
    </button>
    <div
      v-if="open"
      class="model-menu"
      @click.stop
    >
      <div class="menu-search">
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        >
          <circle
            cx="11"
            cy="11"
            r="8"
          /><path d="M21 21l-4.35-4.35" />
        </svg>
        <input
          ref="searchInput"
          v-model="search"
          type="text"
          placeholder="Search models..."
          @input="filterModels"
        >
      </div>
      <div class="menu-list">
        <div
          v-if="filteredModels.length === 0"
          class="menu-empty"
        >
          {{ search ? 'No matches' : 'No active models. Enable models in Settings.' }}
        </div>
        <div
          v-for="m in filteredModels"
          :key="m.model + m.provider"
          :class="['menu-item', { active: m.model === activeModel }]"
          @click="selectModel(m)"
        >
          <span class="mi-check">{{ m.model === activeModel ? '✓' : '' }}</span>
          <span class="mi-name">{{ m.model }}</span>
          <span
            v-if="m.provider"
            class="mi-provider"
          >{{ m.provider }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'
import { useProviders } from '@/composables/useProviders.js'

const { activeModel, activeModels, switchModel, loadStatus } = useProviders()

const open = ref(false)
const search = ref('')
const dropdown = ref(null)
const searchInput = ref(null)

const filteredModels = computed(() => {
  const q = search.value.toLowerCase()
  if (!q) return activeModels.value
  return activeModels.value.filter(m =>
    m.model.toLowerCase().includes(q) ||
    (m.provider || '').toLowerCase().includes(q)
  )
})

function toggleOpen() {
  open.value = !open.value
  if (open.value) {
    search.value = ''
    nextTick(() => searchInput.value?.focus())
  }
}

async function selectModel(m) {
  open.value = false
  await switchModel(m.provider || '', m.model)
  await loadStatus()
}

function handleClickOutside(e) {
  if (dropdown.value && !dropdown.value.contains(e.target)) {
    open.value = false
  }
}

onMounted(() => document.addEventListener('click', handleClickOutside))
onUnmounted(() => document.removeEventListener('click', handleClickOutside))
</script>

<style scoped>
.model-dropdown {
  position: relative;
}

.model-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 12px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 8px;
  background: transparent;
  color: var(--t2);
  font-size: 13px;
  font-family: var(--mono);
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.model-chip span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.model-chip:hover {
  border-color: rgba(255, 255, 255, 0.28);
  background: rgba(255, 255, 255, 0.04);
  color: var(--t1);
}

.chip-icon {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  color: var(--acc2);
}

.model-menu {
  position: absolute;
  bottom: calc(100% + 8px);
  left: 0;
  width: 320px;
  background: var(--bg1);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.5);
  z-index: 200;
  overflow: hidden;
}

.menu-search {
  padding: 8px;
  border-bottom: 1px solid var(--border);
  position: relative;
}

.menu-search input {
  width: 100%;
  background: var(--bg0);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 7px 10px 7px 32px;
  color: var(--t1);
  font-size: 13px;
  font-family: var(--mono);
  outline: none;
}

.menu-search input:focus {
  border-color: var(--acc);
}

.menu-search svg {
  position: absolute;
  left: 18px;
  top: 50%;
  transform: translateY(-50%);
  width: 14px;
  height: 14px;
  color: var(--t3);
  pointer-events: none;
}

.menu-list {
  max-height: 300px;
  overflow-y: auto;
  padding: 4px;
}

.menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.1s;
}

.menu-item:hover {
  background: var(--bg2);
}

.menu-item.active {
  background: rgba(55, 148, 255, 0.1);
}

.mi-check {
  width: 16px;
  flex-shrink: 0;
  color: var(--acc2);
  font-size: 13px;
  text-align: center;
}

.mi-name {
  flex: 1;
  font-size: 13px;
  font-family: var(--mono);
  color: var(--t1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mi-provider {
  font-size: 11px;
  color: var(--t3);
  font-family: var(--mono);
}

.menu-empty {
  padding: 20px;
  text-align: center;
  color: var(--t3);
  font-size: 12px;
}
</style>
