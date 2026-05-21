<template>
  <div class="activity-bar">
    <div class="activity-top">
      <div
        class="activity-logo"
        title="ATRI"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        >
          <path d="M12 2L2 7l10 5 10-5-10-5z" /><path d="M2 17l10 5 10-5" /><path d="M2 12l10 5 10-5" />
        </svg>
      </div>
    </div>
    <div class="activity-items">
      <button
        v-for="page in pages"
        :key="page.id"
        :class="['activity-item', { active: activePage === page.id }]"
        :title="page.label"
        @click="$emit('navigate', page.id)"
      >
        <div
          class="activity-icon"
          v-html="icons[page.icon]"
        />
      </button>
    </div>
    <div class="activity-bottom">
      <button
        class="activity-item"
        title="Toggle Sidebar"
        @click="$emit('toggleSidebar')"
      >
        <div
          class="activity-icon"
          v-html="icons.layout"
        />
      </button>
    </div>
  </div>
</template>

<script setup>
defineProps({
  pages: { type: Array, required: true },
  activePage: { type: String, required: true },
})
defineEmits(['navigate', 'toggleSidebar'])

const icons = {
  chat: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>',
  studio: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 17V7"/><path d="M8 19V5"/><path d="M12 16V8"/><path d="M16 21V3"/><path d="M20 18V6"/></svg>',
  music: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>',
  folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>',
  plug: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 11h4m-2-2v4m6-2h4m-2-2v4"/><rect x="2" y="6" width="8" height="8" rx="1"/><rect x="14" y="6" width="8" height="8" rx="1"/><path d="M6 14v4a2 2 0 002 2h8a2 2 0 002-2v-4"/></svg>',
  server: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M12 1v4m0 14v4M4.22 4.22l2.83 2.83m9.9 9.9l2.83 2.83M1 12h4m14 0h4M4.22 19.78l2.83-2.83m9.9-9.9l2.83-2.83"/></svg>',
  wand: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 000 1.4l1.6 1.6a1 1 0 001.4 0l3.77-3.77a6 6 0 01-7.94 7.94l-6.91 6.91a2.12 2.12 0 01-3-3l6.91-6.91a6 6 0 017.94-7.94l-3.76 3.76z"/></svg>',
  knowledge: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.66 3.58 3 8 3s8-1.34 8-3V5"/><path d="M4 11v6c0 1.66 3.58 3 8 3s8-1.34 8-3v-6"/></svg>',
  gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>',
  layout: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>',
}
</script>

<style scoped>
.activity-bar {
  width: var(--activity-w);
  margin: 8px;
  margin-right: 7px;
  background: rgba(24, 24, 24, 0.82);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-soft);
  backdrop-filter: blur(18px);
  display: flex;
  flex-direction: column;
  align-items: center;
  flex-shrink: 0;
  user-select: none;
  -webkit-app-region: no-drag;
}

.activity-top {
  padding: 11px 0 9px;
}

.activity-logo {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--acc2);
  background: var(--acc-bg);
  border: 1px solid rgba(125, 168, 232, 0.22);
  border-radius: 8px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.045);
}

.activity-logo svg {
  width: 20px;
  height: 20px;
}

.activity-items {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 4px 0;
}

.activity-bottom {
  padding: 8px 0 12px;
}

.activity-item {
  width: 38px;
  height: 38px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px solid transparent;
  background: transparent;
  color: var(--t3);
  cursor: pointer;
  border-radius: 8px;
  position: relative;
  transition: color 0.15s, background 0.15s, border-color 0.15s, transform 0.15s;
}

.activity-item:hover {
  color: var(--t2);
  background: var(--bg-100);
  border-color: var(--border-light);
}

.activity-item.active {
  color: var(--t1);
  background: var(--bg-100);
  border-color: var(--border-strong);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.035);
}

.activity-item.active::before {
  content: '';
  position: absolute;
  left: -7px;
  top: 10px;
  bottom: 10px;
  width: 3px;
  background: var(--acc2);
  border-radius: 999px;
  box-shadow: 0 0 14px rgba(158, 191, 255, 0.32);
}

.activity-icon {
  width: 19px;
  height: 19px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.activity-icon :deep(svg) {
  width: 19px;
  height: 19px;
}

.activity-badge {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--acc2);
  border: 2px solid var(--bg1);
}
</style>
