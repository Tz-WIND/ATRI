<template>
  <div
    class="project-library-popover"
    @click.stop
  >
    <div class="project-library-head">
      <span>Project Library</span>
      <button
        class="mini-btn text"
        type="button"
        :disabled="loading"
        @click="emit('save-copy')"
      >
        Save Copy
      </button>
    </div>
    <input
      :value="copyTitle"
      class="project-copy-input"
      type="text"
      aria-label="Project copy title"
      :placeholder="`${project?.title || 'ATRI Session'} Copy`"
      @input="emit('update:copyTitle', $event.target.value)"
      @keydown.enter.prevent="emit('save-copy')"
    >
    <div class="project-library-list">
      <button
        v-for="archive in archives"
        :key="archive.id"
        :class="['project-library-item', { active: archive.id === activeProjectId }]"
        type="button"
        :disabled="loading"
        @click="emit('open-archive', archive.id)"
      >
        <span>
          <strong>{{ archive.title || 'ATRI Session' }}</strong>
          <small>{{ archiveTimeLabel(archive) }}</small>
        </span>
        <em>{{ archive.track_count || 0 }} tracks</em>
      </button>
    </div>
  </div>
</template>

<script setup>
defineProps({
  project: { type: Object, default: null },
  archives: { type: Array, default: () => [] },
  activeProjectId: { type: String, default: '' },
  copyTitle: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  archiveTimeLabel: { type: Function, required: true },
})

const emit = defineEmits(['update:copyTitle', 'save-copy', 'open-archive'])
</script>

<style scoped>
.project-library-popover {
  position: absolute;
  top: calc(100% + 10px);
  left: 0;
  z-index: 40;
  width: min(360px, calc(100vw - 28px));
  padding: 10px;
  border: 1px solid rgba(229, 236, 245, 0.14);
  border-radius: 8px;
  background: #202428;
  box-shadow: 0 18px 44px rgba(0, 0, 0, 0.38);
}

.project-library-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
  color: var(--t2);
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
}

.project-copy-input {
  width: 100%;
  height: 30px;
  margin-bottom: 8px;
  padding: 0 9px;
  border: 1px solid rgba(229, 236, 245, 0.13);
  border-radius: 6px;
  background: #17191c;
  color: var(--t1);
  font-size: 12px;
}

.project-library-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 260px;
  overflow: auto;
}

.project-library-item {
  width: 100%;
  min-height: 48px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px;
  border: 1px solid rgba(229, 236, 245, 0.1);
  border-radius: 6px;
  background: #262b30;
  color: var(--t2);
  cursor: pointer;
  text-align: left;
}

.project-library-item:hover {
  color: var(--t1);
  border-color: rgba(229, 236, 245, 0.2);
  background: #2f353b;
}

.project-library-item.active {
  border-color: rgba(240, 209, 122, 0.42);
  background: rgba(240, 209, 122, 0.1);
}

.project-library-item span {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.project-library-item strong {
  font-size: 12px;
  color: var(--t1);
}

.project-library-item small,
.project-library-item em {
  color: var(--t4);
  font-size: 11px;
  font-style: normal;
}

.mini-btn {
  width: 28px;
  height: 28px;
  min-width: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(229, 236, 245, 0.13);
  border-radius: 6px;
  background: #2b3035;
  color: var(--t2);
  cursor: pointer;
  transition: background 0.14s, border-color 0.14s, color 0.14s;
}

.mini-btn:hover {
  color: var(--t1);
  background: #343b42;
  border-color: rgba(229, 236, 245, 0.22);
}

.mini-btn:disabled {
  cursor: not-allowed;
  opacity: 0.52;
}

.mini-btn.text {
  width: auto;
  padding: 0 10px;
  font-size: 12px;
  font-weight: 650;
}
</style>
