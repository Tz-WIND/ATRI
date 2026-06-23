<template>
  <div
    v-if="open"
    class="modal-backdrop automation-parameter-backdrop"
    @click.self="emit('close')"
    @keydown.esc.stop.prevent="emit('close')"
  >
    <section
      class="automation-parameter-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="automation-parameter-title"
      tabindex="-1"
    >
      <header class="track-create-dialog-head">
        <div>
          <span>Automation</span>
          <h2 id="automation-parameter-title">
            Select Parameter
          </h2>
        </div>
        <button
          class="mini-btn"
          type="button"
          title="Close"
          aria-label="Close"
          @click="emit('close')"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          ><path d="M6 6l12 12M18 6L6 18" /></svg>
        </button>
      </header>

      <div class="automation-parameter-columns">
        <section class="automation-parameter-column">
          <h3>Available</h3>
          <button
            v-for="target in defaultTargets"
            :key="target.key"
            type="button"
            class="automation-parameter-row"
            @click="emit('bind-target', target.target)"
          >
            <strong>{{ target.label }}</strong>
            <span>{{ target.detail }}</span>
          </button>
        </section>
        <section class="automation-parameter-column learned">
          <h3>MIDI Learn</h3>
          <button
            type="button"
            class="automation-learn-refresh"
            @click="emit('refresh-captured')"
          >
            Refresh captured
          </button>
          <div
            v-for="item in learnedTargets"
            :key="item.id"
            class="automation-learned-row"
            role="button"
            tabindex="0"
            @click="emit('bind-target', item.target)"
            @keydown.enter.stop.prevent="emit('bind-target', item.target)"
            @keydown.space.stop.prevent="emit('bind-target', item.target)"
          >
            <input
              :value="item.name"
              @pointerdown.stop
              @click.stop
              @change="emit('rename-learned-target', item.id, $event.target.value)"
            >
            <small>{{ item.detail }}</small>
          </div>
        </section>
      </div>
    </section>
  </div>
</template>

<script setup>
defineProps({
  open: { type: Boolean, default: false },
  defaultTargets: { type: Array, default: () => [] },
  learnedTargets: { type: Array, default: () => [] },
})

const emit = defineEmits([
  'close',
  'bind-target',
  'refresh-captured',
  'rename-learned-target',
])
</script>

<style scoped>
.automation-parameter-dialog {
  width: min(760px, 100%);
  max-height: calc(100% - 24px);
  overflow: hidden;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  border: 1px solid rgba(229, 236, 245, 0.16);
  border-radius: 8px;
  background: #202428;
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.46);
}

.track-create-dialog-head {
  min-height: 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 12px 14px;
  border-bottom: 1px solid rgba(229, 236, 245, 0.1);
}

.track-create-dialog-head span {
  color: var(--orange);
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
}

.track-create-dialog-head h2 {
  margin: 2px 0 0;
  color: var(--t1);
  font-size: 15px;
}

.automation-parameter-columns {
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(280px, 0.88fr);
  gap: 0;
  overflow: hidden;
}

.automation-parameter-column {
  min-width: 0;
  min-height: 0;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px;
}

.automation-parameter-column.learned {
  border-left: 1px solid rgba(229, 236, 245, 0.08);
  background: rgba(12, 15, 18, 0.22);
}

.automation-parameter-column h3 {
  margin: 0 0 4px;
  color: var(--t4);
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
}

.automation-parameter-row,
.automation-learned-row {
  width: 100%;
  min-width: 0;
  border: 1px solid rgba(229, 236, 245, 0.1);
  border-radius: 6px;
  background: rgba(11, 13, 15, 0.36);
}

.automation-parameter-row {
  display: grid;
  gap: 3px;
  padding: 9px 10px;
  color: var(--t2);
  cursor: pointer;
  text-align: left;
}

.automation-parameter-row:hover,
.automation-parameter-row:focus-visible,
.automation-learned-row:hover,
.automation-learned-row:focus-visible,
.automation-learned-row:focus-within {
  border-color: rgba(240, 209, 122, 0.34);
  background: rgba(240, 209, 122, 0.07);
}

.automation-parameter-row strong {
  min-width: 0;
  overflow: hidden;
  color: var(--t1);
  font-size: 12px;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.automation-parameter-row span,
.automation-learned-row small {
  min-width: 0;
  overflow: hidden;
  color: var(--t4);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.automation-learn-refresh {
  width: 100%;
  height: 28px;
  border: 1px solid rgba(229, 236, 245, 0.12);
  border-radius: 6px;
  background: rgba(229, 236, 245, 0.05);
  color: var(--t3);
  font-size: 11px;
  cursor: pointer;
}

.automation-learn-refresh:hover,
.automation-learn-refresh:focus-visible {
  border-color: rgba(127, 201, 167, 0.34);
  color: var(--t1);
}

.automation-learned-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 7px;
  padding: 8px;
  cursor: pointer;
}

.automation-learned-row input {
  min-width: 0;
  width: 100%;
  height: 28px;
  border: 1px solid rgba(229, 236, 245, 0.12);
  border-radius: 5px;
  background: #111418;
  color: var(--t2);
  font-size: 12px;
  padding: 0 8px;
  cursor: text;
}

.automation-learned-row input:focus {
  outline: none;
  border-color: rgba(240, 209, 122, 0.5);
  box-shadow: 0 0 0 2px rgba(240, 209, 122, 0.12);
}

.automation-learned-row small {
  grid-column: 1 / -1;
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

.mini-btn svg {
  width: 15px;
  height: 15px;
}

@media (max-width: 1120px) {
  .automation-parameter-dialog {
    max-height: calc(100% - 16px);
  }

  .automation-parameter-columns {
    grid-template-columns: minmax(0, 1fr);
  }

  .automation-parameter-column.learned {
    border-top: 1px solid rgba(229, 236, 245, 0.08);
    border-left: 0;
  }
}
</style>
