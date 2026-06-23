<template>
  <div
    :ref="setWrap"
    class="controller-lanes-wrap"
    :style="{ height: `${panelHeight}px` }"
    @scroll="emit('scroll')"
  >
    <div
      class="controller-lanes"
      :style="{ width: `${timelineWidth}px` }"
    >
      <section
        v-for="lane in lanes"
        :key="lane.id"
        class="controller-lane"
        :style="{ width: `${timelineWidth}px` }"
      >
        <div class="controller-lane-axis">
          <span>{{ axisTop(lane) }}</span>
          <span>{{ axisMiddle(lane) }}</span>
          <span>{{ axisBottom(lane) }}</span>
        </div>
        <div
          class="controller-lane-tabs"
          :style="{ left: `${pianoKeyWidth + scrollLeft}px` }"
        >
          <button
            class="controller-menu-btn"
            title="添加或移除控制器"
            @click.stop="emit('toggle-menu', lane.id)"
          >
            ...
          </button>
          <button
            v-for="controllerId in lane.controllerIds"
            :key="`${lane.id}-${controllerId}`"
            :class="[
              'controller-tab',
              { active: lane.activeControllerId === controllerId },
            ]"
            :title="controllerLabel(controllerId)"
            @click.stop="emit('set-lane-controller', lane.id, controllerId)"
          >
            {{ controllerLabel(controllerId) }}
          </button>
          <button
            v-if="lanes.length > 1"
            class="controller-close"
            title="移除控制器窗口"
            @click.stop="emit('remove-lane', lane.id)"
          >
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            ><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>
          <div
            v-if="menuLaneId === lane.id"
            class="controller-menu"
          >
            <button
              v-for="preset in menuOptions(lane)"
              :key="`${lane.id}-menu-${preset.id}`"
              type="button"
              @click.stop="emit('add-controller', lane.id, preset.id)"
            >
              {{ preset.label }}
            </button>
            <label>
              <span>自定义 CC</span>
              <input
                :value="customControllerNumber"
                inputmode="numeric"
                maxlength="3"
                placeholder="0-127"
                @input="emit('update:customControllerNumber', $event.target.value)"
                @keydown.enter.stop.prevent="emit('add-custom-controller', lane.id)"
              >
            </label>
            <button
              type="button"
              @click.stop="emit('add-custom-controller', lane.id)"
            >
              添加
            </button>
            <button
              type="button"
              :disabled="lane.controllerIds.length <= 1"
              @click.stop="emit('remove-active-controller', lane.id)"
            >
              移除当前
            </button>
          </div>
        </div>
        <canvas
          :ref="el => setCanvas(lane.id, el)"
          class="controller-canvas"
          @pointerdown="event => emit('lane-pointerdown', event, lane)"
          @contextmenu.prevent
        />
      </section>
      <div
        class="controller-lane-footer"
        :style="{ width: `${timelineWidth}px` }"
      >
        <button
          class="controller-footer-btn"
          title="增加控制器窗口"
          @click="emit('add-lane')"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          ><path d="M12 5v14M5 12h14" /></svg>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({
  lanes: { type: Array, required: true },
  panelHeight: { type: Number, required: true },
  timelineWidth: { type: Number, required: true },
  pianoKeyWidth: { type: Number, required: true },
  scrollLeft: { type: Number, required: true },
  menuLaneId: { type: String, default: null },
  customControllerNumber: { type: String, default: '' },
  axisTop: { type: Function, required: true },
  axisMiddle: { type: Function, required: true },
  axisBottom: { type: Function, required: true },
  controllerLabel: { type: Function, required: true },
  menuOptions: { type: Function, required: true },
  setWrap: { type: Function, required: true },
  setCanvas: { type: Function, required: true },
})

const emit = defineEmits([
  'update:customControllerNumber',
  'scroll',
  'toggle-menu',
  'set-lane-controller',
  'remove-lane',
  'add-controller',
  'add-custom-controller',
  'remove-active-controller',
  'lane-pointerdown',
  'add-lane',
])
</script>

<style scoped>
.controller-lanes-wrap {
  flex: 0 0 auto;
  height: 124px;
  min-width: 0;
  overflow-x: auto;
  overflow-y: hidden;
  border-top: 1px solid rgba(229, 236, 245, 0.13);
  background: #15181b;
  scrollbar-width: thin;
}

.controller-lanes {
  min-width: 100%;
}

.controller-lane {
  position: relative;
  height: 96px;
  min-width: 100%;
  overflow: visible;
  border-bottom: 1px solid rgba(229, 236, 245, 0.11);
  background: #17191c;
}

.controller-canvas {
  position: absolute;
  inset: 0;
  z-index: 1;
  display: block;
  min-width: 100%;
  cursor: crosshair;
}

.controller-lane-axis {
  position: sticky;
  left: 0;
  z-index: 4;
  width: 76px;
  height: 96px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  align-items: flex-end;
  padding: 28px 8px 6px;
  border-right: 1px solid rgba(229, 236, 245, 0.16);
  background: #2b3035;
  color: #b7c2cf;
  font-family: var(--mono);
  font-size: 11px;
  pointer-events: none;
}

.controller-lane-tabs {
  position: absolute;
  top: 0;
  z-index: 5;
  width: max-content;
  height: 24px;
  display: flex;
  align-items: stretch;
  overflow: visible;
  background: rgba(32, 36, 40, 0.96);
}

.controller-menu-btn,
.controller-tab,
.controller-close,
.controller-footer-btn {
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 0;
  border-right: 1px solid rgba(229, 236, 245, 0.08);
  background: #24282c;
  color: #b9c3cf;
  cursor: pointer;
  font-size: 12px;
}

.controller-menu-btn {
  width: 28px;
  font-weight: 800;
}

.controller-tab {
  min-width: 76px;
  padding: 0 12px;
  white-space: nowrap;
}

.controller-tab.active {
  background: #0d74c9;
  color: #fff;
}

.controller-close {
  width: 26px;
  color: var(--t4);
}

.controller-close svg,
.controller-footer-btn svg {
  width: 13px;
  height: 13px;
}

.controller-menu-btn:hover,
.controller-tab:hover,
.controller-close:hover,
.controller-footer-btn:hover {
  color: var(--t1);
  background: #343b42;
}

.controller-tab.active:hover {
  background: #0d74c9;
}

.controller-menu {
  position: absolute;
  top: 25px;
  left: 0;
  z-index: 8;
  width: 188px;
  padding: 6px;
  border: 1px solid rgba(229, 236, 245, 0.2);
  border-radius: 6px;
  background: #30353a;
  box-shadow: 0 10px 26px rgba(0, 0, 0, 0.36);
}

.controller-menu button,
.controller-menu label {
  width: 100%;
  min-height: 26px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  border: 0;
  border-radius: 4px;
  padding: 4px 7px;
  background: transparent;
  color: #e1e7ee;
  font-size: 12px;
  text-align: left;
}

.controller-menu button {
  cursor: pointer;
}

.controller-menu button:disabled {
  cursor: not-allowed;
  opacity: 0.48;
}

.controller-menu button:hover:not(:disabled) {
  background: rgba(13, 116, 201, 0.28);
}

.controller-menu span {
  color: #b7c2cf;
}

.controller-menu input {
  width: 64px;
  height: 22px;
  border: 1px solid rgba(229, 236, 245, 0.18);
  border-radius: 4px;
  background: #15181b;
  color: #f4f6f8;
  font-family: var(--mono);
  font-size: 11px;
}

.controller-lane-footer {
  height: 28px;
  min-width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  border-bottom: 1px solid rgba(229, 236, 245, 0.09);
  background: #202428;
}

.controller-footer-btn {
  position: sticky;
  left: 10px;
  width: 26px;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  color: #b9c3cf;
}
</style>
