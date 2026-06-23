<template>
  <div class="piano-head">
    <div>
      <span>Piano Roll</span>
      <strong>{{ clipName }}</strong>
    </div>
    <div class="piano-actions">
      <div
        class="piano-control piano-quantize"
        title="选择钢琴窗量化网格"
      >
        <span>量化</span>
        <button
          class="piano-quantize-button"
          type="button"
          @click.stop="emit('toggle-quantize-menu')"
        >
          <strong>{{ quantizeLabel }}</strong>
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          ><path d="m6 9 6 6 6-6" /></svg>
        </button>
        <div
          v-if="quantizeMenuOpen"
          class="piano-quantize-menu"
        >
          <button
            v-for="option in quantizeOptions"
            :key="option.id"
            type="button"
            :class="{ active: quantizeId === option.id }"
            @click.stop="emit('set-quantize-option', option.id)"
          >
            {{ option.label }}
          </button>
        </div>
      </div>
      <button
        :class="['mini-btn text', { active: snapActive }]"
        title="音符和控制器拖拽是否吸附到当前量化"
        @click="emit('toggle-snap')"
      >
        吸附 {{ snapActive ? '量化' : '关闭' }}
      </button>
      <select
        :value="subtrackCreateValue"
        class="piano-subtrack-select"
        title="创建钢琴窗附属小轨道"
        @change="onSubtrackChange"
      >
        <option value="">
          + 小轨道
        </option>
        <option
          v-for="option in subtrackOptions"
          :key="option.id"
          :value="option.id"
          :disabled="option.disabled"
        >
          {{ option.label }}
        </option>
      </select>
      <button
        :class="['mini-btn text', { active: tool === 'select' }]"
        title="Select and move notes"
        @click="emit('set-tool', 'select')"
      >
        Select
      </button>
      <button
        :class="['mini-btn text', { active: tool === 'draw' }]"
        title="Draw notes by dragging"
        @click="emit('set-tool', 'draw')"
      >
        Draw
      </button>
      <button
        class="mini-btn text danger"
        title="Delete selected notes"
        :disabled="selectedNoteCount === 0"
        @click="emit('delete-selected-notes')"
      >
        Del
      </button>
      <button
        class="mini-btn"
        title="Close piano roll"
        @click="emit('close')"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        ><path d="M18 6 6 18M6 6l12 12" /></svg>
      </button>
    </div>
  </div>
</template>

<script setup>
defineProps({
  clipName: { type: String, required: true },
  quantizeMenuOpen: { type: Boolean, default: false },
  quantizeLabel: { type: String, required: true },
  quantizeOptions: { type: Array, required: true },
  quantizeId: { type: String, required: true },
  snapActive: { type: Boolean, default: false },
  subtrackCreateValue: { type: String, default: '' },
  subtrackOptions: { type: Array, required: true },
  tool: { type: String, required: true },
  selectedNoteCount: { type: Number, default: 0 },
})

const emit = defineEmits([
  'toggle-quantize-menu',
  'set-quantize-option',
  'toggle-snap',
  'update:subtrackCreateValue',
  'create-subtrack',
  'set-tool',
  'delete-selected-notes',
  'close',
])

function onSubtrackChange(event) {
  emit('update:subtrackCreateValue', event.target.value)
  emit('create-subtrack')
}
</script>

<style scoped>
.piano-head {
  height: 34px;
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
  padding: 0 10px;
  color: var(--t3);
  font-size: 11px;
  text-transform: none;
  border-bottom: 1px solid rgba(229, 236, 245, 0.1);
  background: #262b30;
}

.piano-head div:first-child {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.piano-head span {
  color: var(--t3);
  text-transform: uppercase;
  font-size: 11px;
}

.piano-head strong {
  color: var(--t1);
  font-size: 12px;
}

.piano-actions {
  min-width: 0;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.piano-control {
  height: 28px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 8px;
  border: 1px solid rgba(229, 236, 245, 0.13);
  border-radius: 6px;
  background: #2b3035;
  color: var(--t2);
  font-size: 11px;
  font-weight: 650;
}

.piano-control span {
  color: var(--t3);
  text-transform: none;
  font-size: 10px;
}

.piano-subtrack-select {
  height: 28px;
  min-width: 92px;
  border: 1px solid rgba(229, 236, 245, 0.14);
  border-radius: 6px;
  padding: 0 8px;
  background: #2b3035;
  color: #d9e0e8;
  font: 650 11px var(--mono);
  cursor: pointer;
}

.piano-subtrack-select:focus {
  outline: 1px solid rgba(240, 209, 122, 0.42);
  outline-offset: 1px;
}

.piano-quantize {
  position: relative;
}

.piano-quantize-button {
  min-width: 70px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  border: 0;
  padding: 0;
  background: transparent;
  color: var(--t1);
  font: inherit;
  cursor: pointer;
}

.piano-quantize-button strong {
  color: var(--t1);
  font-size: 12px;
}

.piano-quantize-button svg {
  width: 13px;
  height: 13px;
  color: var(--t3);
}

.piano-quantize-menu {
  position: absolute;
  top: 31px;
  left: 0;
  z-index: 12;
  min-width: 100%;
  padding: 4px;
  border: 1px solid rgba(229, 236, 245, 0.2);
  border-radius: 6px;
  background: #2a2e33;
  box-shadow: 0 12px 26px rgba(0, 0, 0, 0.38);
}

.piano-quantize-menu button {
  width: 100%;
  min-height: 26px;
  display: flex;
  align-items: center;
  border: 0;
  border-radius: 4px;
  padding: 4px 8px;
  background: transparent;
  color: #d8dee6;
  cursor: pointer;
  font-size: 12px;
  font-weight: 650;
  text-align: left;
}

.piano-quantize-menu button:hover,
.piano-quantize-menu button.active {
  background: #0d74c9;
  color: #fff;
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

.mini-btn.active {
  color: #17191c;
  border-color: rgba(240, 209, 122, 0.72);
  background: #f0d17a;
}

.mini-btn.danger:hover {
  color: #ffd4cf;
  border-color: rgba(255, 141, 127, 0.42);
  background: rgba(255, 141, 127, 0.14);
}

.mini-btn svg {
  width: 15px;
  height: 15px;
}

:global(.studio-page.embedded) .piano-head {
  height: 32px;
  padding: 0 8px;
}

:global(.studio-page.embedded) .piano-head div:first-child {
  min-width: 0;
}

:global(.studio-page.embedded) .piano-head strong {
  max-width: 112px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

:global(.studio-page.embedded) .piano-actions {
  gap: 4px;
  overflow: auto;
}
</style>
