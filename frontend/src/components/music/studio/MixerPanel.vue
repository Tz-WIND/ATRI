<template>
  <div
    ref="panel"
    class="mixer-panel"
  >
    <div
      class="piano-resize-handle"
      role="separator"
      aria-orientation="horizontal"
      title="Resize mixer"
      @pointerdown="context.startResize($event)"
    >
      <span />
    </div>
    <div class="mixer-head">
      <div>
        <span>Mixer</span>
        <strong>{{ context.mixerTracks.length }} routes</strong>
      </div>
      <div class="mixer-actions">
        <button
          class="mini-btn text"
          :disabled="context.pluginsLoading"
          @click="context.loadPlugins()"
        >
          {{ context.pluginsLoading ? 'Scanning' : 'Scan' }}
        </button>
        <button
          class="mini-btn"
          title="Close mixer"
          @click="context.close()"
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
    <div class="mixer-strip-body">
      <div class="mixer-track-strip-scroll">
        <div class="mixer-strip-row">
          <section
            v-for="track in context.mixerTracks"
            :key="`mixer-${track.id}`"
            :class="['mixer-strip', { active: context.activeTrack?.id === track.id }]"
            @click="context.selectTrack(track.id)"
          >
            <div class="mixer-strip-head">
              <span
                class="track-color"
                :style="{ background: track.color }"
              />
              <strong :title="track.name">
                {{ track.name }}
              </strong>
              <small>{{ context.trackTypeLabel(track) }}</small>
            </div>

            <div class="mixer-section mixer-inserts">
              <span class="mixer-section-label">Insert</span>
              <label
                v-if="!context.canUseMixerInserts(track)"
                class="mixer-insert-slot empty"
              >
                <span>No Inserts</span>
                <select disabled>
                  <option>Unavailable</option>
                </select>
              </label>
              <template v-else>
                <label
                  v-for="slot in context.mixerInsertSlots(track)"
                  :key="`${track.id}-${slot.id}`"
                  :class="['mixer-insert-slot', { empty: context.pluginSlot(track, slot.id).type === 'empty' }]"
                >
                  <span>{{ context.uniqueMixerPluginLabel(track, slot) }}</span>
                  <select
                    :value="context.pluginSlotValue(track, slot.id)"
                    :title="context.pluginSlotLabel(track, slot.id)"
                    @change="context.pluginSelect(track, slot.id, $event.target.value)"
                  >
                    <option value="empty::">
                      Empty
                    </option>
                    <option
                      v-if="context.selectedPluginMissing(track, slot.id)"
                      :value="context.pluginSlotValue(track, slot.id)"
                    >
                      {{ context.pluginSlot(track, slot.id).name }}
                    </option>
                    <option
                      v-for="plugin in context.pluginOptions.vst3"
                      :key="`${track.id}-${slot.id}-vst3-${plugin.path}`"
                      :value="`vst3::${plugin.path}`"
                    >
                      {{ plugin.name }}
                    </option>
                    <option
                      v-for="plugin in context.pluginOptions.vst2"
                      :key="`${track.id}-${slot.id}-vst2-${plugin.path}`"
                      :value="`vst2::${plugin.path}`"
                      disabled
                    >
                      {{ plugin.name }} (VST2)
                    </option>
                  </select>
                  <button
                    type="button"
                    class="mixer-param-load"
                    :disabled="context.pluginSlot(track, slot.id).type === 'empty'"
                    @click.stop="context.loadPluginParameters(track.id, slot.id)"
                  >
                    Params
                  </button>
                  <div
                    v-if="context.pluginParameterRows(track.id, slot.id).length"
                    class="mixer-params"
                    @click.stop
                  >
                    <div
                      v-for="param in context.pluginParameterRows(track.id, slot.id)"
                      :key="`${track.id}-${slot.id}-${param.index}`"
                      class="mixer-param-row"
                      @contextmenu.prevent="context.openAutomationMenu($event, context.automationTargetForPluginParameter(track, slot.id, param), `${track.name} ${param.name}`)"
                    >
                      <span>{{ param.name }}</span>
                      <input
                        type="range"
                        min="0"
                        max="1"
                        step="0.001"
                        :value="param.value"
                        :disabled="param.automatable === false"
                        @change="context.setLivePluginParameter(track.id, slot.id, param.index, Number($event.target.value))"
                      >
                      <small>{{ context.parameterValueLabel(param) }}</small>
                    </div>
                  </div>
                </label>
              </template>
            </div>

            <div class="mixer-section mixer-sends">
              <span class="mixer-section-label">Send</span>
              <div
                v-for="(send, index) in context.mixerSendRows(track)"
                :key="send.id || `${track.id}-send-${index}`"
                class="mixer-send-row"
              >
                <select
                  :value="send.target_bus_id"
                  @change="context.updateTrackSend(track, index, { target_bus_id: Number($event.target.value), id: `send_${$event.target.value}` })"
                >
                  <option
                    v-for="bus in context.availableOutputBuses(track.id)"
                    :key="`send-${track.id}-${bus.id}`"
                    :value="bus.id"
                  >
                    {{ bus.name }}
                  </option>
                </select>
                <input
                  type="range"
                  min="0"
                  max="2"
                  step="0.01"
                  :value="send.level ?? 1"
                  @change="context.updateTrackSend(track, index, { level: Number($event.target.value) })"
                >
                <button
                  type="button"
                  :class="{ active: send.enabled !== false }"
                  @click.stop="context.updateTrackSend(track, index, { enabled: send.enabled === false })"
                >
                  S
                </button>
                <button
                  type="button"
                  @click.stop="context.removeTrackSend(track, index)"
                >
                  x
                </button>
              </div>
              <select
                class="mixer-send-add"
                value=""
                @change="context.addTrackSendChange(track, $event)"
              >
                <option value="">
                  Add Send
                </option>
                <option
                  v-for="bus in context.availableOutputBuses(track.id)"
                  :key="`add-send-${track.id}-${bus.id}`"
                  :value="bus.id"
                >
                  {{ bus.name }}
                </option>
              </select>
            </div>

            <label class="mixer-pan">
              <span>L</span>
              <span class="mixer-pan-control">
                <span class="mixer-pan-center-line" />
                <input
                  type="range"
                  min="-1"
                  max="1"
                  step="0.01"
                  :value="track.pan"
                  @contextmenu.prevent="context.openAutomationMenu($event, context.automationTargetForTrackPan(track), `${track.name} Pan`)"
                  @change="context.updateTrack(track.id, { pan: Number($event.target.value) })"
                >
              </span>
              <span>R</span>
              <strong>{{ Number(track.pan || 0).toFixed(2) }}</strong>
            </label>

            <div class="mixer-strip-buttons">
              <button
                :class="['track-flag', { on: track.mute }]"
                title="Mute"
                @click.stop="context.updateTrack(track.id, { mute: !track.mute })"
              >
                M
              </button>
              <button
                :class="['track-flag', { on: track.solo }]"
                title="Solo"
                @click.stop="context.updateTrack(track.id, { solo: !track.solo })"
              >
                S
              </button>
            </div>

            <label class="mixer-fader">
              <span>{{ context.volumeDbLabel(track.volume) }}</span>
              <input
                type="range"
                min="0"
                max="1.4"
                step="0.01"
                orient="vertical"
                :value="track.volume"
                @contextmenu.prevent="context.openAutomationMenu($event, context.automationTargetForTrackVolume(track), `${track.name} Volume`)"
                @change="context.updateTrack(track.id, { volume: Number($event.target.value) })"
              >
            </label>
          </section>
        </div>
      </div>
      <div class="mixer-master-dock">
        <section class="mixer-strip master-strip">
          <div class="mixer-strip-head">
            <span
              class="master-strip-color"
              :style="{ background: context.masterBus.color }"
            />
            <strong>{{ context.masterBus.name }}</strong>
            <small>Main Output</small>
          </div>

          <div class="mixer-section mixer-inserts">
            <span class="mixer-section-label">Insert</span>
            <label
              v-for="slot in context.mixerInsertSlots(context.masterBus)"
              :key="`master-${slot.id}`"
              :class="['mixer-insert-slot', { empty: context.pluginSlot(context.masterBus, slot.id).type === 'empty' }]"
            >
              <span>{{ context.uniqueMixerPluginLabel(context.masterBus, slot) }}</span>
              <select
                :value="context.pluginSlotValue(context.masterBus, slot.id)"
                :title="context.pluginSlotLabel(context.masterBus, slot.id)"
                @change="context.masterBusPluginSelect(slot.id, $event.target.value)"
              >
                <option value="empty::">
                  Empty
                </option>
                <option
                  v-if="context.selectedPluginMissing(context.masterBus, slot.id)"
                  :value="context.pluginSlotValue(context.masterBus, slot.id)"
                >
                  {{ context.pluginSlot(context.masterBus, slot.id).name }}
                </option>
                <option
                  v-for="plugin in context.pluginOptions.vst3"
                  :key="`master-${slot.id}-vst3-${plugin.path}`"
                  :value="`vst3::${plugin.path}`"
                >
                  {{ plugin.name }}
                </option>
                <option
                  v-for="plugin in context.pluginOptions.vst2"
                  :key="`master-${slot.id}-vst2-${plugin.path}`"
                  :value="`vst2::${plugin.path}`"
                  disabled
                >
                  {{ plugin.name }} (VST2)
                </option>
              </select>
            </label>
          </div>

          <label class="mixer-pan">
            <span>L</span>
            <span class="mixer-pan-control">
              <span class="mixer-pan-center-line" />
              <input
                type="range"
                min="-1"
                max="1"
                step="0.01"
                :value="context.masterBus.pan"
                @change="context.updateMasterBus({ pan: Number($event.target.value) })"
              >
            </span>
            <span>R</span>
            <strong>{{ Number(context.masterBus.pan || 0).toFixed(2) }}</strong>
          </label>

          <div class="mixer-strip-buttons">
            <button
              :class="['track-flag', { on: context.masterBus.mute }]"
              title="Mute"
              @click.stop="context.updateMasterBus({ mute: !context.masterBus.mute })"
            >
              M
            </button>
            <button
              :class="['track-flag', { on: context.masterBus.solo }]"
              title="Solo"
              @click.stop="context.updateMasterBus({ solo: !context.masterBus.solo })"
            >
              S
            </button>
          </div>

          <label class="mixer-fader">
            <span>{{ context.volumeDbLabel(context.masterBus.volume) }}</span>
            <input
              type="range"
              min="0"
              max="1.4"
              step="0.01"
              orient="vertical"
              :value="context.masterBus.volume"
              @change="context.updateMasterBus({ volume: Number($event.target.value) })"
            >
          </label>
        </section>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import './MixerPanel.css'

defineProps({
  context: { type: Object, required: true },
})

const panel = ref(null)

defineExpose({
  getBoundingClientRect: () => panel.value?.getBoundingClientRect(),
})
</script>
