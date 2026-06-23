<template>
  <header
    class="studio-topbar"
    :class="{ embedded }"
  >
    <div class="session-title">
      <span class="session-kicker">ATRI Studio</span>
      <button
        class="project-library-trigger"
        type="button"
        title="Project library"
        @click.stop="emit('toggle-project-library')"
      >
        <strong>{{ project?.title || 'Session' }}</strong>
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        ><path d="m6 9 6 6 6-6" /></svg>
      </button>
      <ProjectLibraryPopover
        v-if="projectLibraryOpen"
        :copy-title="projectCopyTitle"
        :project="project"
        :archives="projectArchives"
        :active-project-id="activeProjectId"
        :loading="loading"
        :archive-time-label="archiveTimeLabel"
        @update:copy-title="value => emit('update:projectCopyTitle', value)"
        @save-copy="emit('save-copy')"
        @open-archive="archiveId => emit('open-archive', archiveId)"
      />
    </div>

    <div class="transport">
      <button
        class="tool-btn primary"
        :disabled="loading"
        :title="playing ? 'Pause' : 'Play'"
        @click="emit('toggle-play')"
      >
        <svg
          v-if="playing"
          viewBox="0 0 24 24"
          fill="currentColor"
        ><rect
          x="6"
          y="5"
          width="4"
          height="14"
        /><rect
          x="14"
          y="5"
          width="4"
          height="14"
        /></svg>
        <svg
          v-else
          viewBox="0 0 24 24"
          fill="currentColor"
        ><polygon points="7,4 19,12 7,20" /></svg>
      </button>
      <button
        class="tool-btn"
        title="Stop"
        @click="emit('stop-playback')"
      >
        <svg
          viewBox="0 0 24 24"
          fill="currentColor"
        ><rect
          x="6"
          y="6"
          width="12"
          height="12"
          rx="1"
        /></svg>
      </button>
      <div class="clock mono">
        {{ positionLabel }}
      </div>
      <label
        class="tempo-box mono"
        title="Tempo BPM"
        @wheel.prevent="event => emit('tempo-wheel', event)"
        @contextmenu.prevent="event => emit('tempo-context-menu', event)"
      >
        <input
          :value="tempoInput"
          type="number"
          min="1"
          step="1"
          aria-label="Tempo BPM"
          @input="event => emit('update:tempoInput', numericValue(event))"
          @focus="emit('update:tempoInputFocused', true)"
          @blur="emit('update:tempoInputFocused', false); emit('sync-tempo-field')"
          @change="emit('update-tempo')"
          @keydown.enter="emit('update-tempo')"
        >
        <span>BPM</span>
      </label>
      <div
        :ref="setTimeSignatureRoot"
        class="time-signature-picker mono"
        title="Time signature"
      >
        <button
          class="time-signature-display"
          type="button"
          aria-label="Edit time signature"
          @click.stop="emit('toggle-time-signature-popover')"
        >
          {{ timeSignatureLabel }}
        </button>
        <div
          v-if="timeSignaturePopoverOpen"
          class="time-signature-popover"
          @click.stop
        >
          <label class="time-signature-numerator">
            <span>拍号</span>
            <input
              :value="timeSignatureNumerator"
              type="number"
              min="1"
              max="255"
              step="1"
              aria-label="Time signature numerator"
              @input="event => emit('update:timeSignatureNumerator', numericValue(event))"
              @focus="emit('update:timeSignatureNumeratorFocused', true)"
              @blur="emit('update:timeSignatureNumeratorFocused', false); emit('sync-time-signature-fields')"
              @change="emit('update-time-signature')"
              @keydown.enter="emit('update-time-signature')"
            >
          </label>
          <div class="time-signature-duration-row">
            <span>节拍时长</span>
            <button
              class="time-signature-denominator-trigger"
              type="button"
              @click.stop="emit('update:timeSignatureDenominatorPopoverOpen', !timeSignatureDenominatorPopoverOpen)"
            >
              {{ timeSignatureDenominatorLabel }}
            </button>
          </div>
          <div
            v-if="timeSignatureDenominatorPopoverOpen"
            class="time-signature-denominator-popover"
          >
            <button
              v-for="denominator in timeSignatureDenominatorOptions"
              :key="denominator"
              type="button"
              :class="{ active: denominator === timeSignatureDenominator }"
              @click.stop="emit('set-time-signature-denominator', denominator)"
            >
              {{ denominatorLabel(denominator) }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <div class="host-controls">
      <div
        class="host-status"
        aria-label="Audio host diagnostics"
      >
        <span class="host-status-item">
          <span :class="['host-dot', { online: host.running }]" />
          <span class="host-label">{{ host.running ? 'Host Online' : 'Host Offline' }}</span>
        </span>
        <span class="host-status-item">
          <span :class="['host-dot', { connected: audioConnected }]" />
          <span class="host-label">
            {{ audioConnected ? 'Audio WS Connected' : 'Audio WS Disconnected' }}
          </span>
        </span>
        <span class="host-status-item">
          <span :class="['host-dot', { connected: hostStreamingEnabled, streaming: pcmStreaming }]" />
          <span class="host-label">
            {{ hostStreamingEnabled ? (pcmStreaming ? 'PCM Streaming' : 'PCM Waiting') : 'PCM Idle' }}
          </span>
        </span>
      </div>
      <button
        :class="['tool-btn text', { active: mixerVisible }]"
        title="Show mixer rack"
        @click="emit('open-mixer')"
      >
        Mixer
      </button>
      <button
        class="tool-btn"
        type="button"
        title="Export audio"
        aria-label="Export audio"
        :disabled="loading || exporting"
        @click="emit('open-export')"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        ><path d="M12 3v12" /><path d="m7 10 5 5 5-5" /><path d="M5 21h14" /></svg>
      </button>
      <button
        :class="['tool-btn text', { active: inspectorVisible }]"
        title="Show or hide inspector"
        @click="emit('update:inspectorVisible', !inspectorVisible)"
      >
        Inspector
      </button>
    </div>
  </header>
</template>

<script setup>
import ProjectLibraryPopover from './ProjectLibraryPopover.vue'
import { numberInputModelValue } from './numericInputValue.js'

defineProps({
  embedded: { type: Boolean, default: false },
  project: { type: Object, default: null },
  projectArchives: { type: Array, default: () => [] },
  activeProjectId: { type: String, default: '' },
  projectLibraryOpen: { type: Boolean, default: false },
  projectCopyTitle: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  playing: { type: Boolean, default: false },
  positionLabel: { type: String, required: true },
  tempoInput: { type: [Number, String], required: true },
  tempoInputFocused: { type: Boolean, default: false },
  timeSignatureNumerator: { type: [Number, String], required: true },
  timeSignatureNumeratorFocused: { type: Boolean, default: false },
  timeSignatureDenominator: { type: Number, required: true },
  timeSignatureLabel: { type: String, required: true },
  timeSignatureDenominatorLabel: { type: String, required: true },
  timeSignatureDenominatorOptions: { type: Array, required: true },
  timeSignaturePopoverOpen: { type: Boolean, default: false },
  timeSignatureDenominatorPopoverOpen: { type: Boolean, default: false },
  host: { type: Object, required: true },
  audioConnected: { type: Boolean, default: false },
  hostStreamingEnabled: { type: Boolean, default: false },
  pcmStreaming: { type: Boolean, default: false },
  mixerVisible: { type: Boolean, default: false },
  exporting: { type: Boolean, default: false },
  inspectorVisible: { type: Boolean, default: true },
  archiveTimeLabel: { type: Function, required: true },
  denominatorLabel: { type: Function, required: true },
  setTimeSignatureRoot: { type: Function, required: true },
})

const emit = defineEmits([
  'update:projectCopyTitle',
  'toggle-project-library',
  'save-copy',
  'open-archive',
  'toggle-play',
  'stop-playback',
  'update:tempoInput',
  'update:tempoInputFocused',
  'sync-tempo-field',
  'update-tempo',
  'tempo-wheel',
  'tempo-context-menu',
  'toggle-time-signature-popover',
  'update:timeSignatureNumerator',
  'update:timeSignatureNumeratorFocused',
  'sync-time-signature-fields',
  'update-time-signature',
  'update:timeSignatureDenominatorPopoverOpen',
  'set-time-signature-denominator',
  'open-mixer',
  'open-export',
  'update:inspectorVisible',
])

function numericValue(event) {
  return numberInputModelValue(event.target.value)
}
</script>

<style scoped>
.studio-topbar {
  height: 54px;
  display: grid;
  grid-template-columns: minmax(170px, 1fr) auto minmax(300px, 1fr);
  align-items: center;
  gap: 14px;
  padding: 0 14px;
  border-bottom: 1px solid rgba(229, 236, 245, 0.12);
  background: #24282c;
}

.session-title {
  position: relative;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.session-kicker {
  font-size: 10px;
  text-transform: uppercase;
  color: var(--orange);
  letter-spacing: 0;
  font-family: var(--mono);
}

.session-title strong {
  font-size: 14px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.project-library-trigger {
  width: 100%;
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--t1);
  cursor: pointer;
  text-align: left;
}

.project-library-trigger svg {
  width: 14px;
  height: 14px;
  flex: 0 0 auto;
  color: var(--t4);
}

.transport,
.host-controls {
  display: flex;
  align-items: center;
  gap: 8px;
}

.host-controls {
  justify-content: flex-end;
}

.host-status {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 8px 12px;
  min-width: 0;
}

.host-status-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.tool-btn {
  height: 32px;
  min-width: 32px;
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

.tool-btn:hover {
  color: var(--t1);
  background: #343b42;
  border-color: rgba(229, 236, 245, 0.22);
}

.tool-btn:disabled {
  cursor: not-allowed;
  opacity: 0.52;
}

.tool-btn.primary {
  background: #0d74c9;
  border-color: #2588d5;
  color: white;
}

.tool-btn.active {
  color: #f0d17a;
  border-color: rgba(240, 209, 122, 0.34);
  background: rgba(240, 209, 122, 0.1);
}

.tool-btn.text {
  width: auto;
  padding: 0 10px;
  font-size: 12px;
  font-weight: 650;
}

.tool-btn svg {
  width: 15px;
  height: 15px;
}

.clock {
  min-width: 132px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 10px;
  color: #f0d17a;
  background: #141618;
  border: 1px solid rgba(240, 209, 122, 0.18);
  border-radius: 6px;
  font-size: 14px;
}

.tempo-box {
  height: 32px;
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 0 8px;
  border: 1px solid rgba(229, 236, 245, 0.12);
  border-radius: 6px;
  color: var(--t3);
  background: #1d2024;
  font-size: 11px;
}

.tempo-box input {
  width: 50px;
  min-width: 0;
  border: 0;
  padding: 0;
  background: transparent;
  color: var(--t1);
  font-family: var(--mono);
  font-size: 12px;
  font-weight: 700;
}

.tempo-box input:focus {
  outline: none;
}

.tempo-box:focus-within {
  border-color: rgba(240, 209, 122, 0.5);
  box-shadow: 0 0 0 2px rgba(240, 209, 122, 0.12);
}

.time-signature-picker {
  position: relative;
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.time-signature-display {
  height: 32px;
  min-width: 66px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(229, 236, 245, 0.12);
  border-radius: 6px;
  padding: 0 10px;
  color: var(--t3);
  background: #1d2024;
  font-family: var(--mono);
  font-size: 12px;
  font-weight: 700;
  cursor: pointer;
}

.time-signature-display:hover {
  color: var(--t1);
  border-color: rgba(240, 209, 122, 0.3);
  background: #25292e;
}

.time-signature-popover {
  position: absolute;
  top: 38px;
  left: 50%;
  z-index: 20;
  width: 166px;
  display: grid;
  gap: 8px;
  padding: 8px;
  border: 1px solid rgba(229, 236, 245, 0.18);
  border-radius: 7px;
  background: #24282c;
  box-shadow: 0 16px 38px rgba(0, 0, 0, 0.42);
  transform: translateX(-50%);
}

.time-signature-numerator,
.time-signature-duration-row {
  display: grid;
  grid-template-columns: 62px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
}

.time-signature-numerator span,
.time-signature-duration-row span {
  color: var(--t4);
  font-size: 10px;
  text-transform: uppercase;
}

.time-signature-numerator input,
.time-signature-denominator-trigger {
  height: 26px;
  min-width: 0;
  border: 1px solid rgba(229, 236, 245, 0.12);
  border-radius: 4px;
  background: #101215;
  color: var(--t1);
  font-family: var(--mono);
  font-size: 11px;
}

.time-signature-numerator input {
  width: 100%;
  padding: 0 7px;
}

.time-signature-denominator-trigger {
  width: 100%;
  padding: 0 8px;
  text-align: left;
  cursor: pointer;
}

.time-signature-denominator-popover {
  position: absolute;
  top: 76px;
  right: 8px;
  z-index: 21;
  width: 86px;
  display: grid;
  gap: 3px;
  padding: 4px;
  border: 1px solid rgba(229, 236, 245, 0.2);
  border-radius: 6px;
  background: #30353a;
  box-shadow: 0 12px 26px rgba(0, 0, 0, 0.38);
}

.time-signature-denominator-popover button {
  height: 24px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: #d8dee6;
  font-family: var(--mono);
  font-size: 11px;
  text-align: left;
  cursor: pointer;
}

.time-signature-denominator-popover button:hover,
.time-signature-denominator-popover button.active {
  background: #0d74c9;
  color: #fff;
}

.time-signature-numerator input:focus,
.time-signature-denominator-trigger:focus {
  outline: none;
  border-color: rgba(240, 209, 122, 0.5);
  box-shadow: 0 0 0 2px rgba(240, 209, 122, 0.12);
}

.host-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--red);
  box-shadow: 0 0 0 3px rgba(255, 141, 127, 0.12);
}

.host-dot.online {
  background: var(--ok);
  box-shadow: 0 0 0 3px rgba(143, 216, 199, 0.12);
}

.host-dot.connected {
  background: #f0d17a;
  box-shadow: 0 0 0 3px rgba(240, 209, 122, 0.14);
}

.host-dot.streaming {
  background: #58a7b8;
  box-shadow: 0 0 0 3px rgba(88, 167, 184, 0.16);
}

.host-label {
  color: var(--t3);
  font-size: 12px;
  white-space: nowrap;
}

@media (max-width: 1120px) {
  .studio-topbar {
    grid-template-columns: 1fr;
    height: auto;
    padding: 10px;
  }

  .host-controls {
    justify-content: flex-start;
    flex-wrap: wrap;
  }
}

.studio-topbar.embedded {
  height: auto;
  min-height: 104px;
  grid-template-columns: 1fr;
  align-items: stretch;
  gap: 8px;
  padding: 9px;
  background: #202428;
}

.studio-topbar.embedded .session-title {
  min-width: 0;
}

.studio-topbar.embedded .session-title strong {
  font-size: 13px;
}

.studio-topbar.embedded .transport {
  justify-content: space-between;
  gap: 6px;
}

.studio-topbar.embedded .clock {
  min-width: 0;
  flex: 1;
  padding: 0 7px;
  font-size: 12px;
}

.studio-topbar.embedded .tempo-box,
.studio-topbar.embedded .time-signature-picker {
  display: none;
}

.studio-topbar.embedded .host-controls {
  justify-content: space-between;
  gap: 6px;
}

.studio-topbar.embedded .host-status {
  gap: 6px;
}

.studio-topbar.embedded .host-label {
  display: none;
}

.studio-topbar.embedded .tool-btn.text {
  padding: 0 8px;
  font-size: 11px;
}
</style>
