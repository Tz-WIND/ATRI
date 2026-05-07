<template>
  <div class="prov-sidebar">
    <div class="sidebar-head">
      <span class="sidebar-title">Sources</span>
      <button
        class="btn btn-ghost"
        @click="$emit('add')"
      >
        + Add
      </button>
    </div>
    <div class="sidebar-list">
      <div
        v-if="providers.length === 0"
        class="sidebar-empty"
      >
        No providers yet
      </div>
      <button
        v-for="p in providers"
        :key="p.name"
        :class="['prov-source', { active: p.name === selectedName }]"
        @click="$emit('select', p.name)"
      >
        <span
          class="prov-dot"
          :style="{ background: p.api_key ? 'var(--green)' : 'var(--orange)' }"
        />
        <div class="prov-info">
          <div class="prov-name">
            {{ p.name }}
          </div>
          <div class="prov-url">
            {{ p.base_url || '(default)' }}
          </div>
        </div>
        <div class="prov-actions">
          <button
            class="btn-delete"
            @click.stop="$emit('delete', p.name)"
          >
            &times;
          </button>
        </div>
      </button>
    </div>
  </div>
</template>

<script setup>
defineProps({
  providers: { type: Array, required: true },
  selectedName: { type: String, default: '' },
})
defineEmits(['select', 'add', 'delete'])
</script>

<style scoped>
.prov-sidebar {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.sidebar-head {
  padding: 14px 16px 10px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.sidebar-title {
  font-size: 13px;
  font-weight: 700;
  font-family: var(--mono);
  color: var(--t2);
  text-transform: uppercase;
  letter-spacing: 0.4px;
}

.btn {
  padding: 3px 10px;
  border-radius: 6px;
  border: 1px solid var(--border);
  cursor: pointer;
  font-size: 11px;
  font-weight: 600;
  font-family: var(--mono);
  transition: all 0.12s;
}

.btn-ghost {
  background: none;
  color: var(--t2);
}

.btn-ghost:hover {
  background: var(--bg2);
  color: var(--t1);
}

.sidebar-list {
  flex: 1;
  overflow-y: auto;
  padding: 6px 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.sidebar-empty {
  padding: 20px;
  text-align: center;
  color: var(--t3);
  font-size: 13px;
}

.prov-source {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  border: none;
  background: transparent;
  color: inherit;
  text-align: left;
  width: 100%;
  transition: background 0.1s;
}

.prov-source:hover { background: var(--bg2); }
.prov-source.active { background: var(--bg3); }

.prov-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.prov-info {
  flex: 1;
  min-width: 0;
}

.prov-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--t1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.prov-url {
  font-size: 11px;
  color: var(--t3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin-top: 2px;
}

.prov-actions {
  opacity: 0;
  margin-left: auto;
  flex-shrink: 0;
}

.prov-source:hover .prov-actions { opacity: 1; }

.btn-delete {
  background: none;
  border: none;
  color: var(--t3);
  cursor: pointer;
  font-size: 14px;
  padding: 2px 6px;
  border-radius: 4px;
}

.btn-delete:hover {
  color: var(--red);
  background: rgba(244, 135, 113, 0.1);
}
</style>
