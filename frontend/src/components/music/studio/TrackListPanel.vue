<template>
  <aside class="track-list">
    <div class="track-list-sticky-header">
      <div
        class="track-lane-spacer"
        aria-hidden="true"
      />

      <div
        v-for="subtrackId in visibleSubtracks"
        :key="`arrangement-subtrack-${subtrackId}`"
        class="track-global-subtrack-row"
      >
        <span>{{ subtrackId === 'meter' ? '拍号轨' : '和声轨' }}</span>
        <small>{{ subtrackId === 'meter' ? 'Global Meter' : 'Global Harmony' }}</small>
      </div>
    </div>

    <template
      v-for="track in tracks"
      :key="track.id"
    >
      <div
        :class="[
          'track-row',
          {
            active: activeTrack?.id === track.id,
            'reorder-dragging': isTrackReorderDragging(track),
            'reorder-before': isTrackReorderDropTarget(track, 'before'),
            'reorder-after': isTrackReorderDropTarget(track, 'after'),
          },
        ]"
        :draggable="canDragTrackRow(track)"
        role="button"
        tabindex="0"
        @click="emit('select-track', track.id)"
        @contextmenu.prevent="event => emit('open-context-menu', event, track)"
        @dragstart.stop="event => emit('start-reorder', event, track)"
        @dragover.prevent.stop="event => emit('reorder-over', event, track)"
        @drop.prevent.stop="event => emit('drop-reorder', event, track)"
        @dragend.stop="emit('end-reorder')"
        @keydown="event => emit('row-keydown', event, track.id)"
      >
        <span
          class="track-color"
          :style="{ background: track.color }"
        />
        <span class="track-main">
          <span class="track-title-line">
            <strong
              class="track-title-text"
              :title="track.name"
            >{{ track.name }}</strong>
            <small
              class="track-meta-text"
              :title="trackRowMetaLabel(track)"
            >{{ trackRowMetaLabel(track) }}</small>
          </span>
          <span
            v-if="isInstrumentTrack(track)"
            class="track-plugin-bar"
            @click.stop
          >
            <select
              class="track-plugin-select"
              :value="pluginSlotValue(track, 'instrument')"
              :title="pluginSlotLabel(track, 'instrument')"
              @change="event => emit('plugin-select', track, 'instrument', event.target.value)"
            >
              <option value="builtin::ATRI Basic Synth">
                ATRI Basic Synth
              </option>
              <option
                v-if="selectedPluginMissing(track, 'instrument')"
                :value="pluginSlotValue(track, 'instrument')"
              >
                {{ pluginSlot(track, 'instrument').name }}
              </option>
              <option
                v-for="plugin in pluginOptions.vst3"
                :key="`track-vst3-${track.id}-${plugin.path}`"
                :value="`vst3::${plugin.path}`"
              >
                {{ plugin.name }}
              </option>
              <option
                v-for="plugin in pluginOptions.vst2"
                :key="`track-vst2-${track.id}-${plugin.path}`"
                :value="`vst2::${plugin.path}`"
                disabled
              >
                {{ plugin.name }} (VST2)
              </option>
            </select>
            <select
              class="track-plugin-select track-output-select"
              :value="track.output_bus_id ?? ''"
              title="Output"
              @change.stop="event => emit('update-track-output-bus', track, event.target.value)"
            >
              <option value="">
                Master
              </option>
              <option
                v-for="bus in availableOutputBuses(track.id)"
                :key="`out-${track.id}-${bus.id}`"
                :value="bus.id"
              >
                {{ bus.name }}
              </option>
            </select>
            <button
              :class="['track-plugin-open', { active: isPluginEditorOpen(track.id) }]"
              :disabled="!canOpenPluginEditor(track)"
              :title="isPluginEditorOpen(track.id) ? 'Native editor open' : 'Open native plugin editor'"
              @click.stop="emit('toggle-plugin-editor', track)"
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
                stroke-linecap="round"
              ><path d="M4 7h10" /><path d="M18 7h2" /><path d="M4 17h2" /><path d="M10 17h10" /><circle
                cx="16"
                cy="7"
                r="2"
              /><circle
                cx="8"
                cy="17"
                r="2"
              /></svg>
            </button>
          </span>
          <span
            v-else-if="isAudioTrack(track)"
            class="track-plugin-bar audio-channel-bar"
            @click.stop
          >
            <select
              class="track-plugin-select"
              :value="track.channel_type || 'multichannel'"
              title="Audio channel type"
              @change.stop="event => emit('update-track', track.id, { channel_type: event.target.value })"
            >
              <option value="mono">
                Mono
              </option>
              <option value="multichannel">
                Multi-channel
              </option>
            </select>
            <select
              class="track-plugin-select track-output-select"
              :value="track.output_bus_id ?? ''"
              title="Output"
              @change.stop="event => emit('update-track-output-bus', track, event.target.value)"
            >
              <option value="">
                Master
              </option>
              <option
                v-for="bus in availableOutputBuses(track.id)"
                :key="`out-${track.id}-${bus.id}`"
                :value="bus.id"
              >
                {{ bus.name }}
              </option>
            </select>
          </span>
          <span
            v-else-if="isBusTrack(track)"
            class="track-plugin-bar bus-output-bar"
            @click.stop
          >
            <select
              class="track-plugin-select track-output-select"
              :value="track.output_bus_id ?? ''"
              title="Output"
              @change.stop="event => emit('update-track-output-bus', track, event.target.value)"
            >
              <option value="">
                Master
              </option>
              <option
                v-for="bus in availableOutputBuses(track.id)"
                :key="`out-${track.id}-${bus.id}`"
                :value="bus.id"
              >
                {{ bus.name }}
              </option>
            </select>
          </span>
          <span
            v-else-if="isAutomationTrack(track)"
            class="track-plugin-bar automation-target-bar"
            @click.stop
          >
            <button
              class="automation-target-select"
              type="button"
              @click.stop="emit('open-automation-picker', track)"
            >
              {{ automationTargetLabel(track.target) }}
            </button>
            <small>{{ automationPointCount(track) }} pts</small>
          </span>
        </span>
        <span class="track-buttons">
          <button
            :class="['track-flag', { on: track.mute }]"
            title="Mute"
            @click.stop="emit('update-track', track.id, { mute: !track.mute })"
          >M</button>
          <button
            :class="['track-flag', { on: track.solo }]"
            title="Solo"
            @click.stop="emit('update-track', track.id, { solo: !track.solo })"
          >S</button>
        </span>
      </div>
    </template>
  </aside>
</template>

<script setup>
defineProps({
  tracks: { type: Array, default: () => [] },
  activeTrack: { type: Object, default: null },
  visibleSubtracks: { type: Array, default: () => [] },
  pluginOptions: { type: Object, default: () => ({ vst3: [], vst2: [] }) },
  canDragTrackRow: { type: Function, required: true },
  isTrackReorderDragging: { type: Function, required: true },
  isTrackReorderDropTarget: { type: Function, required: true },
  trackRowMetaLabel: { type: Function, required: true },
  isInstrumentTrack: { type: Function, required: true },
  isAudioTrack: { type: Function, required: true },
  isBusTrack: { type: Function, required: true },
  isAutomationTrack: { type: Function, required: true },
  pluginSlot: { type: Function, required: true },
  pluginSlotValue: { type: Function, required: true },
  pluginSlotLabel: { type: Function, required: true },
  selectedPluginMissing: { type: Function, required: true },
  availableOutputBuses: { type: Function, required: true },
  isPluginEditorOpen: { type: Function, required: true },
  canOpenPluginEditor: { type: Function, required: true },
  automationTargetLabel: { type: Function, required: true },
  automationPointCount: { type: Function, required: true },
})

const emit = defineEmits([
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
])
</script>

<style scoped>
.track-list {
  position: relative;
  z-index: 4;
  grid-column: 1;
  grid-row: 1;
  align-self: start;
  width: var(--track-list-width);
  min-width: var(--track-list-width);
  background: #202428;
  box-shadow: 10px 0 20px rgba(0, 0, 0, 0.22);
  transform: translateX(var(--arrangement-scroll-left, 0px));
  will-change: transform;
}

.track-lane-spacer {
  height: 30px;
  border-bottom: 1px solid rgba(229, 236, 245, 0.08);
  background: #1b1f23;
}

.track-list-sticky-header {
  position: sticky;
  top: 0;
  z-index: 5;
  background: #202428;
}

.track-global-subtrack-row {
  width: 100%;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 10px;
  border-bottom: 1px solid rgba(229, 236, 245, 0.1);
  background: #171b1f;
  color: var(--t2);
  font-size: 11px;
  font-weight: 700;
}

.track-global-subtrack-row small {
  min-width: 0;
  overflow: hidden;
  color: var(--t4);
  font-size: 10px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.track-row {
  width: 100%;
  height: 72px;
  position: relative;
  display: grid;
  grid-template-columns: 4px minmax(0, 1fr) auto;
  gap: 9px;
  align-items: center;
  padding: 6px 9px;
  border: 0;
  border-bottom: 1px solid rgba(229, 236, 245, 0.08);
  background: transparent;
  color: var(--t2);
  cursor: grab;
  overflow: hidden;
  text-align: left;
}

.track-row.reorder-dragging {
  opacity: 0.58;
  cursor: grabbing;
}

.track-row.reorder-before::before,
.track-row.reorder-after::after {
  content: '';
  position: absolute;
  left: 8px;
  right: 8px;
  z-index: 2;
  height: 2px;
  border-radius: 999px;
  background: #f0d17a;
  box-shadow: 0 0 0 1px rgba(240, 209, 122, 0.18);
  pointer-events: none;
}

.track-row.reorder-before::before {
  top: 0;
}

.track-row.reorder-after::after {
  bottom: -1px;
}

.track-row.active {
  background: rgba(158, 191, 255, 0.11);
  color: var(--t1);
}

.track-row:focus-visible {
  outline: 1px solid rgba(240, 209, 122, 0.42);
  outline-offset: -2px;
}

.track-color {
  width: 4px;
  height: 24px;
  border-radius: 2px;
  flex: 0 0 auto;
}

.track-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
}

.track-title-line {
  width: 100%;
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 0;
}

.track-title-text,
.track-meta-text {
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  display: block;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.track-title-text {
  color: var(--t1);
  font-size: 13px;
  line-height: 16px;
}

.track-meta-text {
  color: var(--t4);
  font-size: 11px;
  line-height: 13px;
}

.track-buttons {
  justify-self: end;
  margin-left: auto;
  display: flex;
  gap: 5px;
}

.track-plugin-bar {
  width: 100%;
  min-width: 0;
  height: 24px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 70px 24px;
  gap: 5px;
}

.track-plugin-bar.audio-channel-bar {
  grid-template-columns: minmax(0, 1fr) 70px;
}

.track-plugin-bar.bus-output-bar {
  grid-template-columns: minmax(0, 1fr);
}

.track-plugin-bar.automation-target-bar {
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  color: rgba(229, 236, 245, 0.72);
  font-size: 10px;
}

.automation-target-select {
  min-width: 0;
  overflow: hidden;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.track-plugin-select {
  min-width: 0;
  width: 100%;
  height: 24px;
  border: 1px solid rgba(229, 236, 245, 0.12);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  border-radius: 5px;
  background: #101215;
  color: var(--t2);
  font-size: 11px;
}

.track-output-select {
  color: var(--t3);
}

.track-plugin-open {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(229, 236, 245, 0.12);
  border-radius: 5px;
  background: #181b1f;
  color: var(--t4);
  cursor: pointer;
}

.track-plugin-open:hover,
.track-plugin-open.active {
  color: #f0d17a;
  border-color: rgba(240, 209, 122, 0.34);
  background: rgba(240, 209, 122, 0.1);
}

.track-plugin-open:disabled {
  cursor: not-allowed;
  opacity: 0.46;
}

.track-plugin-open svg {
  width: 14px;
  height: 14px;
}

.track-flag {
  width: 24px;
  height: 24px;
  border: 1px solid rgba(229, 236, 245, 0.1);
  border-radius: 5px;
  background: #181b1f;
  color: var(--t4);
  font-family: var(--mono);
  font-size: 10px;
  cursor: pointer;
}

.track-flag.on {
  color: #f0d17a;
  border-color: rgba(240, 209, 122, 0.32);
  background: rgba(240, 209, 122, 0.12);
}

.track-flag:disabled {
  cursor: not-allowed;
  opacity: 0.46;
}
</style>
