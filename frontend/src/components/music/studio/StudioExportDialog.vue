<template>
  <div
    class="modal-backdrop export-backdrop"
    @click.self="$emit('close')"
    @keydown.esc.stop.prevent="$emit('close')"
  >
    <section
      class="export-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="export-dialog-title"
      tabindex="-1"
    >
      <header class="track-create-dialog-head">
        <div>
          <span>Bounce</span>
          <h2 id="export-dialog-title">
            Export Audio
          </h2>
        </div>
        <button
          class="mini-btn"
          type="button"
          title="Close"
          aria-label="Close"
          @click="$emit('close')"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          ><path d="M6 6l12 12M18 6L6 18" /></svg>
        </button>
      </header>

      <div class="track-create-form export-form">
        <label class="track-create-field">
          <span>Target</span>
          <select v-model="targetValue">
            <option value="entire_project">
              Entire Project
            </option>
            <option value="selected_tracks">
              Selected Tracks
            </option>
          </select>
        </label>
        <div
          v-if="targetValue === 'selected_tracks'"
          class="export-track-list"
        >
          <label
            v-for="track in exportableTracks"
            :key="track.id"
            class="export-track-row"
          >
            <input
              v-model="selectedTrackIdsValue"
              type="checkbox"
              :value="track.id"
            >
            <span>{{ track.name || `Track ${track.id}` }}</span>
          </label>
        </div>
        <label class="track-create-field">
          <span>Mode</span>
          <select v-model="modeValue">
            <option value="mixdown">
              Mixdown
            </option>
            <option value="stems">
              Stems
            </option>
          </select>
        </label>
        <label class="track-create-field">
          <span>Format</span>
          <select v-model="formatValue">
            <option value="wav">
              WAV
            </option>
            <option value="flac">
              FLAC
            </option>
            <option value="mp3">
              MP3
            </option>
          </select>
        </label>
        <label class="track-create-field">
          <span>Rate</span>
          <select v-model.number="sampleRateValue">
            <option :value="44100">
              44.1 kHz
            </option>
            <option :value="48000">
              48 kHz
            </option>
            <option :value="96000">
              96 kHz
            </option>
            <option :value="192000">
              192 kHz
            </option>
          </select>
        </label>
        <label
          v-if="formatValue !== 'mp3'"
          class="track-create-field"
        >
          <span>Depth</span>
          <select v-model="bitDepthValue">
            <option value="i16">
              16-bit PCM
            </option>
            <option value="i24">
              24-bit PCM
            </option>
            <option
              v-if="formatValue === 'wav'"
              value="f32"
            >
              32-bit Float
            </option>
          </select>
        </label>
        <label
          v-if="formatValue === 'mp3'"
          class="track-create-field"
        >
          <span>Bitrate</span>
          <select v-model="bitrateValue">
            <option value="128k">
              128 kbps
            </option>
            <option value="192k">
              192 kbps
            </option>
            <option value="256k">
              256 kbps
            </option>
            <option value="320k">
              320 kbps
            </option>
          </select>
        </label>
      </div>

      <div
        v-if="errorMessage"
        class="export-status error"
      >
        {{ errorMessage }}
      </div>
      <a
        v-if="result?.download_url"
        class="export-status link"
        :href="result.download_url"
        target="_blank"
        rel="noreferrer"
      >
        {{ result.filename }}
      </a>

      <footer class="track-create-actions">
        <button
          class="mini-btn text"
          type="button"
          :disabled="exporting"
          @click="$emit('close')"
        >
          Cancel
        </button>
        <button
          class="mini-btn text active"
          type="button"
          :disabled="exporting || (targetValue === 'selected_tracks' && !selectedTrackIdsValue.length)"
          @click="$emit('export')"
        >
          {{ exporting ? 'Exporting' : 'Export' }}
        </button>
      </footer>
    </section>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import './StudioDialogs.css'

const props = defineProps({
  target: { type: String, required: true },
  mode: { type: String, required: true },
  format: { type: String, required: true },
  sampleRate: { type: Number, required: true },
  bitDepth: { type: String, required: true },
  bitrate: { type: String, required: true },
  selectedTrackIds: { type: Array, required: true },
  exportableTracks: { type: Array, required: true },
  result: { type: Object, default: null },
  errorMessage: { type: String, default: '' },
  exporting: { type: Boolean, default: false },
})

const emit = defineEmits([
  'update:target',
  'update:mode',
  'update:format',
  'update:sampleRate',
  'update:bitDepth',
  'update:bitrate',
  'update:selectedTrackIds',
  'close',
  'export',
])

const targetValue = computed({
  get: () => props.target,
  set: value => emit('update:target', value),
})
const modeValue = computed({
  get: () => props.mode,
  set: value => emit('update:mode', value),
})
const formatValue = computed({
  get: () => props.format,
  set: value => emit('update:format', value),
})
const sampleRateValue = computed({
  get: () => props.sampleRate,
  set: value => emit('update:sampleRate', value),
})
const bitDepthValue = computed({
  get: () => props.bitDepth,
  set: value => emit('update:bitDepth', value),
})
const bitrateValue = computed({
  get: () => props.bitrate,
  set: value => emit('update:bitrate', value),
})
const selectedTrackIdsValue = computed({
  get: () => props.selectedTrackIds,
  set: value => emit('update:selectedTrackIds', value),
})
</script>
