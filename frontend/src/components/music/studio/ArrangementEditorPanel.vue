<template>
  <div
    class="arrangement"
    :style="layout.arrangementStyle"
  >
    <div class="arrangement-head-grid">
      <div class="track-list-head">
        <span>Tracks</span>
        <button
          class="mini-btn track-create-trigger"
          type="button"
          title="Add Track"
          aria-label="Add track"
          @click="emit('add-track')"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          ><path d="M12 5v14M5 12h14" /></svg>
        </button>
      </div>

      <div class="arrangement-toolbar">
        <div>
          <span>Timeline</span>
          <strong>{{ toolbar.selectedClipCount }} selected</strong>
        </div>
        <div class="timeline-actions arrangement-actions">
          <div
            class="timeline-control piano-quantize"
            title="选择时间线量化网格"
          >
            <span>量化</span>
            <button
              class="piano-quantize-button"
              type="button"
              @click.stop="emit('toggle-timeline-quantize-menu')"
            >
              <strong>{{ toolbar.pianoQuantizeLabel }}</strong>
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              ><path d="m6 9 6 6 6-6" /></svg>
            </button>
            <div
              v-if="toolbar.timelineQuantizeMenuOpen"
              class="piano-quantize-menu"
            >
              <button
                v-for="option in toolbar.pianoQuantizeOptions"
                :key="`timeline-${option.id}`"
                type="button"
                :class="{ active: toolbar.pianoQuantizeId === option.id }"
                @click.stop="emit('set-piano-quantize-option', option.id)"
              >
                {{ option.label }}
              </button>
            </div>
          </div>
          <button
            :class="['mini-btn text', { active: toolbar.pianoSnapActive }]"
            title="MIDI 写入是否吸附到当前量化"
            @click="emit('toggle-piano-snap')"
          >
            吸附 {{ toolbar.pianoSnapActive ? '量化' : '关闭' }}
          </button>
          <select
            :value="toolbar.pianoSubtrackCreateValue"
            class="piano-subtrack-select"
            title="创建全局小轨道"
            @change="onSubtrackChange"
          >
            <option value="">
              + 小轨道
            </option>
            <option
              v-for="option in toolbar.pianoSubtrackOptions"
              :key="`timeline-${option.id}`"
              :value="option.id"
              :disabled="option.disabled"
            >
              {{ option.label }}
            </option>
          </select>
          <button
            :class="['mini-btn text', { active: toolbar.timelineTool === 'select' }]"
            title="Select and move clips"
            @click="emit('set-timeline-tool', 'select')"
          >
            Select
          </button>
          <button
            :class="['mini-btn text', { active: toolbar.timelineTool === 'draw' }]"
            title="Draw MIDI into the selected instrument track"
            @click="emit('set-timeline-tool', 'draw')"
          >
            Draw
          </button>
          <button
            class="mini-btn text danger"
            title="Delete selected clips"
            :disabled="toolbar.selectedClipCount === 0"
            @click="emit('delete-selected-clips')"
          >
            Del
          </button>
        </div>
      </div>
    </div>

    <button
      class="track-list-resize-handle"
      type="button"
      title="Resize track list"
      aria-label="Resize track list"
      @pointerdown="event => emit('start-track-list-resize', event)"
    />

    <div
      ref="arrangementWrap"
      :class="[
        'arrangement-canvas-wrap',
        {
          'audio-drop-active': audioDrop.active,
          'audio-importing': audioDrop.importing,
        },
      ]"
      :style="layout.wrapStyle"
      @dragenter.prevent="event => emit('audio-drag-enter', event)"
      @dragover.prevent="event => emit('audio-drag-over', event)"
      @dragleave="event => emit('audio-drag-leave', event)"
      @drop.prevent="event => emit('audio-drop', event)"
      @scroll="event => emit('scroll', event)"
    >
      <div class="arrangement-scroll-inner">
        <TrackListPanel
          :tracks="trackList.tracks"
          :active-track="trackList.activeTrack"
          :visible-subtracks="trackList.visibleSubtracks"
          :plugin-options="trackList.pluginOptions"
          :can-drag-track-row="trackList.canDragTrackRow"
          :is-track-reorder-dragging="trackList.isTrackReorderDragging"
          :is-track-reorder-drop-target="trackList.isTrackReorderDropTarget"
          :track-row-meta-label="trackList.trackRowMetaLabel"
          :is-instrument-track="trackList.isInstrumentTrack"
          :is-audio-track="trackList.isAudioTrack"
          :is-bus-track="trackList.isBusTrack"
          :is-automation-track="trackList.isAutomationTrack"
          :plugin-slot="trackList.pluginSlot"
          :plugin-slot-value="trackList.pluginSlotValue"
          :plugin-slot-label="trackList.pluginSlotLabel"
          :selected-plugin-missing="trackList.selectedPluginMissing"
          :available-output-buses="trackList.availableOutputBuses"
          :is-plugin-editor-open="trackList.isPluginEditorOpen"
          :can-open-plugin-editor="trackList.canOpenPluginEditor"
          :automation-target-label="trackList.automationTargetLabel"
          :automation-point-count="trackList.automationPointCount"
          @select-track="trackId => emit('select-track', trackId)"
          @open-context-menu="(event, track) => emit('open-context-menu', event, track)"
          @start-reorder="(event, track) => emit('start-reorder', event, track)"
          @reorder-over="(event, track) => emit('reorder-over', event, track)"
          @drop-reorder="(event, track) => emit('drop-reorder', event, track)"
          @end-reorder="emit('end-reorder')"
          @row-keydown="(event, trackId) => emit('row-keydown', event, trackId)"
          @plugin-select="(track, slotId, value) => emit('plugin-select', track, slotId, value)"
          @update-track-output-bus="(track, value) => emit('update-track-output-bus', track, value)"
          @toggle-plugin-editor="track => emit('toggle-plugin-editor', track)"
          @update-track="(trackId, patch) => emit('update-track', trackId, patch)"
          @open-automation-picker="track => emit('open-automation-picker', track)"
        />

        <div class="arrangement-timeline-stack">
          <canvas
            ref="arrangementHeaderCanvas"
            class="editor-canvas arrangement-header-canvas"
            @pointerdown="event => emit('arrangement-pointer-down', event)"
            @wheel="event => emit('arrangement-wheel', event)"
            @contextmenu.prevent
          />
          <div class="arrangement-scroll-content">
            <canvas
              ref="arrangementCanvas"
              class="editor-canvas arrangement-canvas"
              @dblclick="event => emit('arrangement-double-click', event)"
              @pointerdown="event => emit('arrangement-pointer-down', event)"
              @wheel="event => emit('arrangement-wheel', event)"
              @contextmenu.prevent
            />
          </div>
        </div>
      </div>
      <div
        v-if="audioDrop.active || audioDrop.importing"
        class="audio-drop-layer"
        aria-hidden="true"
      >
        <span class="audio-drop-glyph">
          <i />
          <i />
          <i />
          <i />
          <i />
        </span>
      </div>
      <div
        v-if="contextMenus.automation.open"
        class="automation-context-menu"
        :style="{ left: `${contextMenus.automation.x}px`, top: `${contextMenus.automation.y}px` }"
        @pointerdown.stop
      >
        <button @click="emit('confirm-create-automation')">
          Create automation track
        </button>
        <small>{{ contextMenus.automation.label }}</small>
      </div>
      <div
        v-if="contextMenus.track.open"
        class="track-context-menu"
        :style="{ left: `${contextMenus.track.x}px`, top: `${contextMenus.track.y}px` }"
        @pointerdown.stop
        @contextmenu.prevent.stop
      >
        <small>{{ contextMenus.track.name }}</small>
        <button
          class="track-context-delete"
          type="button"
          :disabled="trackList.tracks.length <= 1 || contextMenus.loading"
          @click="emit('delete-track-from-context-menu')"
        >
          Delete Track
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import TrackListPanel from './TrackListPanel.vue'

defineProps({
  layout: { type: Object, default: () => ({ arrangementStyle: {}, wrapStyle: {} }) },
  toolbar: {
    type: Object,
    default: () => ({
      selectedClipCount: 0,
      timelineQuantizeMenuOpen: false,
      pianoQuantizeLabel: '',
      pianoQuantizeOptions: [],
      pianoQuantizeId: '',
      pianoSnapActive: false,
      pianoSubtrackCreateValue: '',
      pianoSubtrackOptions: [],
      timelineTool: 'select',
    }),
  },
  trackList: {
    type: Object,
    default: () => ({
      tracks: [],
      activeTrack: null,
      visibleSubtracks: [],
      pluginOptions: { vst3: [], vst2: [] },
      canDragTrackRow: () => false,
      isTrackReorderDragging: () => false,
      isTrackReorderDropTarget: () => false,
      trackRowMetaLabel: () => '',
      isInstrumentTrack: () => false,
      isAudioTrack: () => false,
      isBusTrack: () => false,
      isAutomationTrack: () => false,
      pluginSlot: () => ({}),
      pluginSlotValue: () => '',
      pluginSlotLabel: () => '',
      selectedPluginMissing: () => false,
      availableOutputBuses: () => [],
      isPluginEditorOpen: () => false,
      canOpenPluginEditor: () => false,
      automationTargetLabel: () => '',
      automationPointCount: () => 0,
    }),
  },
  audioDrop: { type: Object, default: () => ({ active: false, importing: false }) },
  contextMenus: {
    type: Object,
    default: () => ({
      automation: { open: false, x: 0, y: 0, label: '' },
      track: { open: false, x: 0, y: 0, name: '' },
      loading: false,
    }),
  },
})

const emit = defineEmits([
  'add-track',
  'toggle-timeline-quantize-menu',
  'set-piano-quantize-option',
  'toggle-piano-snap',
  'update-piano-subtrack-create-value',
  'create-piano-subtrack',
  'set-timeline-tool',
  'delete-selected-clips',
  'start-track-list-resize',
  'audio-drag-enter',
  'audio-drag-over',
  'audio-drag-leave',
  'audio-drop',
  'scroll',
  'select-track',
  'open-context-menu',
  'start-reorder',
  'reorder-over',
  'drop-reorder',
  'end-reorder',
  'row-keydown',
  'plugin-select',
  'update-track-output-bus',
  'toggle-plugin-editor',
  'update-track',
  'open-automation-picker',
  'arrangement-pointer-down',
  'arrangement-wheel',
  'arrangement-double-click',
  'confirm-create-automation',
  'delete-track-from-context-menu',
])

const arrangementWrap = ref(null)
const arrangementHeaderCanvas = ref(null)
const arrangementCanvas = ref(null)

defineExpose({
  arrangementWrap,
  arrangementHeaderCanvas,
  arrangementCanvas,
})

function onSubtrackChange(event) {
  emit('update-piano-subtrack-create-value', event.target.value)
  emit('create-piano-subtrack')
}
</script>

<style scoped>
.arrangement {
  --track-list-width: 246px;
  position: relative;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border-bottom: 1px solid rgba(229, 236, 245, 0.14);
}

.arrangement-head-grid {
  flex: 0 0 auto;
  min-width: 0;
  display: grid;
  grid-template-columns: var(--track-list-width) minmax(0, 1fr);
}

.track-list-head,
.arrangement-toolbar {
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 10px;
  color: var(--t3);
  font-size: 11px;
  text-transform: uppercase;
  border-bottom: 1px solid rgba(229, 236, 245, 0.1);
  background: #262b30;
}

.track-list-head {
  gap: 6px;
}

.track-list-head span {
  flex: 1 1 auto;
}

.arrangement-toolbar {
  flex: 0 0 auto;
  min-width: 0;
  text-transform: none;
}

.arrangement-toolbar div:first-child {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.arrangement-toolbar span {
  color: var(--t3);
  text-transform: uppercase;
  font-size: 11px;
}

.arrangement-toolbar strong {
  color: var(--t1);
  font-size: 12px;
}

.arrangement-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.timeline-control {
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

.timeline-control span {
  color: var(--t3);
  text-transform: none;
  font-size: 10px;
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

.track-list-resize-handle {
  position: absolute;
  z-index: 8;
  top: 0;
  bottom: 0;
  left: calc(var(--track-list-width) - 4px);
  width: 8px;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  cursor: col-resize;
  touch-action: none;
}

.track-list-resize-handle::after {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: 4px;
  width: 1px;
  background: rgba(229, 236, 245, 0.14);
  transition: background 120ms ease;
}

.track-list-resize-handle:hover::after,
.track-list-resize-handle:focus-visible::after {
  background: rgba(143, 216, 199, 0.72);
}

.track-list-resize-handle:focus-visible {
  outline: 1px solid rgba(143, 216, 199, 0.8);
  outline-offset: -1px;
}

.arrangement-canvas-wrap {
  flex: 1 1 auto;
  position: relative;
  min-width: 0;
  min-height: 0;
  overflow: auto;
  overscroll-behavior: contain;
}

.arrangement-canvas-wrap.audio-drop-active,
.arrangement-canvas-wrap.audio-importing {
  box-shadow: inset 0 0 0 1px rgba(88, 167, 184, 0.52);
}

.audio-drop-layer {
  pointer-events: none;
  position: absolute;
  inset: 30px 0 0 var(--track-list-width);
  z-index: 6;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(88, 167, 184, 0.12);
  border: 1px dashed rgba(143, 216, 199, 0.42);
}

.audio-drop-glyph {
  width: 78px;
  height: 42px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  border-radius: 6px;
  background: rgba(16, 18, 21, 0.72);
  box-shadow: 0 14px 34px rgba(0, 0, 0, 0.28);
}

.audio-drop-glyph i {
  width: 4px;
  height: 18px;
  border-radius: 999px;
  background: #8fd8c7;
  animation: audio-pulse 0.78s ease-in-out infinite alternate;
}

.audio-drop-glyph i:nth-child(2) {
  height: 30px;
  animation-delay: 0.08s;
}

.audio-drop-glyph i:nth-child(3) {
  height: 22px;
  animation-delay: 0.16s;
}

.audio-drop-glyph i:nth-child(4) {
  height: 34px;
  animation-delay: 0.24s;
}

.audio-drop-glyph i:nth-child(5) {
  height: 14px;
  animation-delay: 0.32s;
}

@keyframes audio-pulse {
  from {
    opacity: 0.42;
    transform: scaleY(0.72);
  }
  to {
    opacity: 1;
    transform: scaleY(1);
  }
}

.arrangement-scroll-inner {
  min-width: 100%;
  display: grid;
  grid-template-columns: var(--track-list-width) max-content;
  align-items: start;
}

.arrangement-timeline-stack {
  grid-column: 2;
  grid-row: 1;
  min-width: 100%;
}

.arrangement-header-canvas {
  position: sticky;
  top: 0;
  z-index: 5;
  background: #17191c;
}

.arrangement-scroll-content {
  min-width: 100%;
}

.arrangement-canvas {
  min-width: 0;
}

.editor-canvas {
  display: block;
  min-width: 100%;
}

.automation-context-menu,
.track-context-menu {
  position: fixed;
  z-index: 80;
  display: grid;
  gap: 4px;
  min-width: 176px;
  padding: 7px;
  border: 1px solid rgba(229, 236, 245, 0.16);
  border-radius: 7px;
  background: rgba(24, 27, 31, 0.96);
  box-shadow: 0 16px 40px rgba(0, 0, 0, 0.34);
}

.automation-context-menu button,
.track-context-menu button {
  border: 0;
  border-radius: 5px;
  padding: 7px 9px;
  text-align: left;
  cursor: pointer;
}

.automation-context-menu button {
  background: rgba(240, 209, 122, 0.18);
  color: #f4f0dc;
}

.track-context-delete {
  background: rgba(255, 141, 127, 0.14);
  color: #ffd4cf;
}

.track-context-delete:hover:not(:disabled),
.track-context-delete:focus-visible:not(:disabled) {
  background: rgba(255, 141, 127, 0.22);
}

.track-context-delete:disabled {
  cursor: not-allowed;
  opacity: 0.46;
}

.automation-context-menu small,
.track-context-menu small {
  min-width: 0;
  overflow: hidden;
  color: rgba(229, 236, 245, 0.56);
  padding: 0 3px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
