<template>
  <div
    class="modal-backdrop track-create-backdrop"
    @click.self="$emit('close')"
    @keydown.esc.stop.prevent="$emit('close')"
  >
    <section
      class="track-create-dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="track-create-title"
      tabindex="-1"
    >
      <header class="track-create-dialog-head">
        <div>
          <span>New Track</span>
          <h2 id="track-create-title">
            Create Track
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

      <div class="track-create-form">
        <label class="track-create-field">
          <span>Name</span>
          <input
            ref="nameInput"
            v-model="nameValue"
            type="text"
            autocomplete="off"
            placeholder="Auto name"
            @keydown.enter.stop.prevent="$emit('create')"
          >
        </label>
        <label class="track-create-field track-create-color-field">
          <span>Color</span>
          <div class="track-create-color-control">
            <input
              v-model="colorValue"
              type="color"
              title="Track color"
              aria-label="Track color"
            >
            <div class="track-create-swatches">
              <button
                v-for="swatch in palette"
                :key="swatch"
                type="button"
                :class="['track-create-swatch', { active: colorValue === swatch }]"
                :style="{ background: swatch }"
                :aria-label="`Use ${swatch}`"
                @click="colorValue = swatch"
              />
            </div>
          </div>
        </label>
        <label class="track-create-field">
          <span>Type</span>
          <select v-model="typeValue">
            <option value="instrument">
              Instrument
            </option>
            <option value="audio">
              Audio
            </option>
            <option value="bus">
              Bus
            </option>
            <option value="automation">
              Automation
            </option>
          </select>
        </label>
        <label
          v-if="typeValue !== 'automation'"
          class="track-create-field"
        >
          <span>Output</span>
          <select v-model="outputBusIdValue">
            <option :value="null">
              Master
            </option>
            <option
              v-for="bus in outputBuses"
              :key="bus.id"
              :value="bus.id"
            >
              {{ bus.name }}
            </option>
          </select>
        </label>
        <label
          v-if="typeValue === 'automation'"
          class="track-create-field"
        >
          <span>Parameter</span>
          <button
            class="automation-parameter-button"
            type="button"
            @click="$emit('open-automation-parameter-picker')"
          >
            {{ automationTargetLabel }}
          </button>
        </label>
        <label
          v-if="typeValue === 'audio'"
          class="track-create-field"
        >
          <span>Channels</span>
          <select v-model="channelTypeValue">
            <option value="mono">
              Mono
            </option>
            <option value="multichannel">
              Multi-channel
            </option>
          </select>
        </label>
      </div>

      <footer class="track-create-actions">
        <button
          class="mini-btn text"
          type="button"
          @click="$emit('close')"
        >
          Cancel
        </button>
        <button
          class="mini-btn text active"
          type="button"
          @click="$emit('create')"
        >
          Create
        </button>
      </footer>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import './StudioDialogs.css'

const props = defineProps({
  name: { type: String, default: '' },
  color: { type: String, required: true },
  type: { type: String, required: true },
  channelType: { type: String, required: true },
  outputBusId: { type: [Number, String], default: null },
  automationTargetLabel: { type: String, required: true },
  palette: { type: Array, required: true },
  outputBuses: { type: Array, required: true },
})

const emit = defineEmits([
  'update:name',
  'update:color',
  'update:type',
  'update:channelType',
  'update:outputBusId',
  'open-automation-parameter-picker',
  'close',
  'create',
])

const nameInput = ref(null)

const nameValue = computed({
  get: () => props.name,
  set: value => emit('update:name', value),
})
const colorValue = computed({
  get: () => props.color,
  set: value => emit('update:color', value),
})
const typeValue = computed({
  get: () => props.type,
  set: value => emit('update:type', value),
})
const channelTypeValue = computed({
  get: () => props.channelType,
  set: value => emit('update:channelType', value),
})
const outputBusIdValue = computed({
  get: () => props.outputBusId,
  set: value => emit('update:outputBusId', value),
})

onMounted(() => {
  nameInput.value?.focus?.()
})
</script>
