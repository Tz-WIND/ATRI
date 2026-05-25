<template>
  <div
    :class="[
      'studio-page',
      {
        embedded,
        'inspector-hidden': !inspectorVisible,
        'piano-closed': !lowerEditorVisible,
      },
    ]"
    tabindex="0"
    @keydown="onStudioKeydown"
  >
    <header class="studio-topbar">
      <div class="session-title">
        <span class="session-kicker">ATRI Studio</span>
        <strong>{{ project?.title || 'Session' }}</strong>
      </div>

      <div class="transport">
        <button
          class="tool-btn primary"
          :disabled="loading"
          :title="playing ? 'Pause' : 'Play'"
          @click="togglePlay"
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
          @click="stopPlayback"
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
          @wheel.prevent="onTempoWheel"
          @contextmenu.prevent="openAutomationMenu($event, automationTargetForTempoBpm(), 'Tempo BPM')"
        >
          <input
            v-model.number="tempoInput"
            type="number"
            min="1"
            step="1"
            aria-label="Tempo BPM"
            @focus="tempoInputFocused = true"
            @blur="tempoInputFocused = false; syncTempoField(project)"
            @change="updateTempo"
            @keydown.enter="updateTempo"
          >
          <span>BPM</span>
        </label>
        <div
          ref="timeSignatureRoot"
          class="time-signature-picker mono"
          title="Time signature"
        >
          <button
            class="time-signature-display"
            type="button"
            aria-label="Edit time signature"
            @click.stop="toggleTimeSignaturePopover"
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
                v-model.number="timeSignatureNumerator"
                type="number"
                min="1"
                max="255"
                step="1"
                aria-label="Time signature numerator"
                @focus="timeSignatureNumeratorFocused = true"
                @blur="timeSignatureNumeratorFocused = false; syncTimeSignatureFields(project)"
                @change="updateTimeSignature"
                @keydown.enter="updateTimeSignature"
              >
            </label>
            <div class="time-signature-duration-row">
              <span>节拍时长</span>
              <button
                class="time-signature-denominator-trigger"
                type="button"
                @click.stop="timeSignatureDenominatorPopoverOpen = !timeSignatureDenominatorPopoverOpen"
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
                @click.stop="setTimeSignatureDenominator(denominator)"
              >
                {{ denominatorLabel(denominator) }}
              </button>
            </div>
          </div>
        </div>
      </div>

      <div class="host-controls">
        <span :class="['host-dot', { online: host.running, audio: audioConnected }]" />
        <span class="host-label">{{ host.running ? 'Host Online' : 'Host Offline' }}</span>
        <button
          class="tool-btn text"
          :disabled="syncing"
          @click="syncProject({ broadcast: true })"
        >
          Sync
        </button>
        <button
          class="tool-btn text"
          @click="resetDemo()"
        >
          Demo
        </button>
        <button
          :class="['tool-btn text', { active: mixerVisible }]"
          title="Show mixer rack"
          @click="openMixer"
        >
          Mixer
        </button>
        <button
          :class="['tool-btn text', { active: inspectorVisible }]"
          title="Show or hide inspector"
          @click="inspectorVisible = !inspectorVisible"
        >
          Inspector
        </button>
      </div>
    </header>

    <div
      v-if="hostError"
      class="studio-error"
    >
      {{ hostError }}
    </div>

    <main class="studio-body">
      <section
        ref="editorStack"
        class="editor-stack"
        :style="editorStackStyle"
      >
        <div
          class="arrangement"
          :style="arrangementLayoutStyle"
        >
          <div class="arrangement-head-grid">
            <div class="track-list-head">
              <span>Tracks</span>
              <button
                class="mini-btn track-create-trigger"
                type="button"
                title="Add Track"
                aria-label="Add track"
                @click="openTrackCreateDialog"
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
                <strong>{{ selectedClipIds.size }} selected</strong>
              </div>
              <div class="arrangement-actions">
                <button
                  class="mini-btn text"
                  title="Create MIDI clip at playhead"
                  @click="createClip('midi')"
                >
                  MIDI
                </button>
                <button
                  class="mini-btn text"
                  title="Create audio clip placeholder at playhead"
                  @click="createClip('audio')"
                >
                  Audio
                </button>
                <button
                  class="mini-btn text"
                  title="Copy selected clips"
                  :disabled="selectedClipIds.size === 0"
                  @click="copySelectedClips"
                >
                  Copy
                </button>
                <button
                  class="mini-btn text"
                  title="Paste clips at playhead"
                  :disabled="clipClipboard.length === 0"
                  @click="pasteClips"
                >
                  Paste
                </button>
                <button
                  class="mini-btn text danger"
                  title="Delete selected clips"
                  :disabled="selectedClipIds.size === 0"
                  @click="deleteSelectedClips"
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
            @pointerdown="startTrackListResize"
          />

          <div
            ref="arrangementWrap"
            :class="[
              'arrangement-canvas-wrap',
              {
                'audio-drop-active': audioDropActive,
                'audio-importing': audioImporting,
              },
            ]"
            :style="arrangementWrapStyle"
            @dragenter.prevent="onAudioDragEnter"
            @dragover.prevent="onAudioDragOver"
            @dragleave="onAudioDragLeave"
            @drop.prevent="onAudioDrop"
            @scroll="syncArrangementScroll"
          >
            <div class="arrangement-scroll-inner">
              <aside class="track-list">
                <div
                  class="track-lane-spacer"
                  aria-hidden="true"
                />

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
                    @click="selectTrack(track.id)"
                    @contextmenu.prevent="openTrackContextMenu($event, track)"
                    @dragstart.stop="startTrackReorderDrag($event, track)"
                    @dragover.prevent.stop="onTrackReorderDragOver($event, track)"
                    @drop.prevent.stop="dropTrackReorder($event, track)"
                    @dragend.stop="endTrackReorderDrag"
                    @keydown="onTrackRowKeydown($event, track.id)"
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
                          @change="onPluginSelect(track, 'instrument', $event.target.value)"
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
                          @change.stop="updateTrackOutputBus(track, $event.target.value)"
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
                          @click.stop="togglePluginEditor(track)"
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
                          @change.stop="updateTrack(track.id, { channel_type: $event.target.value })"
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
                          @change.stop="updateTrackOutputBus(track, $event.target.value)"
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
                          @change.stop="updateTrackOutputBus(track, $event.target.value)"
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
                          @click.stop="openAutomationParameterPickerForTrack(track)"
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
                        @click.stop="updateTrack(track.id, { mute: !track.mute })"
                      >M</button>
                      <button
                        :class="['track-flag', { on: track.solo }]"
                        title="Solo"
                        @click.stop="updateTrack(track.id, { solo: !track.solo })"
                      >S</button>
                    </span>
                  </div>
                </template>
              </aside>

              <canvas
                ref="arrangementCanvas"
                class="editor-canvas arrangement-canvas"
                @dblclick="onArrangementDoubleClick"
                @pointerdown="onArrangementPointerDown"
                @wheel="onArrangementWheel"
                @contextmenu.prevent
              />
            </div>
            <div
              v-if="audioDropActive || audioImporting"
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
              v-if="automationMenu.open"
              class="automation-context-menu"
              :style="{ left: `${automationMenu.x}px`, top: `${automationMenu.y}px` }"
              @pointerdown.stop
            >
              <button @click="confirmCreateAutomationFromMenu">
                Create automation track
              </button>
              <small>{{ automationMenu.label }}</small>
            </div>
            <div
              v-if="trackContextMenu.open"
              class="track-context-menu"
              :style="{ left: `${trackContextMenu.x}px`, top: `${trackContextMenu.y}px` }"
              @pointerdown.stop
              @contextmenu.prevent.stop
            >
              <small>{{ trackContextMenu.name }}</small>
              <button
                class="track-context-delete"
                type="button"
                :disabled="tracks.length <= 1 || loading"
                @click="deleteTrackFromContextMenu"
              >
                Delete Track
              </button>
            </div>
          </div>
        </div>

        <div
          v-if="pianoVisible && activeMidiClip"
          ref="pianoPanel"
          class="piano-panel"
        >
          <div
            class="piano-resize-handle"
            role="separator"
            aria-orientation="horizontal"
            title="Resize piano roll"
            @pointerdown="startPianoResize"
          >
            <span />
          </div>
          <div class="piano-head">
            <div>
              <span>Piano Roll</span>
              <strong>{{ activeMidiClip.clip.name }}</strong>
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
                  @click.stop="pianoQuantizeMenuOpen = !pianoQuantizeMenuOpen"
                >
                  <strong>{{ pianoQuantizeLabel }}</strong>
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                  ><path d="m6 9 6 6 6-6" /></svg>
                </button>
                <div
                  v-if="pianoQuantizeMenuOpen"
                  class="piano-quantize-menu"
                >
                  <button
                    v-for="option in pianoQuantizeOptions"
                    :key="option.id"
                    type="button"
                    :class="{ active: pianoQuantizeId === option.id }"
                    @click.stop="setPianoQuantizeOption(option.id)"
                  >
                    {{ option.label }}
                  </button>
                </div>
              </div>
              <button
                :class="['mini-btn text', { active: isPianoSnapActive }]"
                title="音符和控制器拖拽是否吸附到当前量化"
                @click="pianoSnapEnabled = !pianoSnapEnabled"
              >
                吸附 {{ isPianoSnapActive ? '量化' : '关闭' }}
              </button>
              <select
                v-model="pianoSubtrackCreateValue"
                class="piano-subtrack-select"
                title="创建钢琴窗附属小轨道"
                @change="createPianoSubtrack()"
              >
                <option value="">
                  + 小轨道
                </option>
                <option
                  v-for="option in pianoSubtrackOptions"
                  :key="option.id"
                  :value="option.id"
                  :disabled="option.disabled"
                >
                  {{ option.label }}
                </option>
              </select>
              <button
                :class="['mini-btn text', { active: pianoTool === 'select' }]"
                title="Select and move notes"
                @click="pianoTool = 'select'"
              >
                Select
              </button>
              <button
                :class="['mini-btn text', { active: pianoTool === 'draw' }]"
                title="Draw notes by dragging"
                @click="pianoTool = 'draw'"
              >
                Draw
              </button>
              <button
                class="mini-btn text"
                title="Copy selected notes"
                :disabled="selectedNoteIds.size === 0"
                @click="copySelectedNotes"
              >
                Copy
              </button>
              <button
                class="mini-btn text"
                title="Paste copied notes at the playhead"
                :disabled="noteClipboard.length === 0"
                @click="pasteNotes"
              >
                Paste
              </button>
              <button
                class="mini-btn text danger"
                title="Delete selected notes"
                :disabled="selectedNoteIds.size === 0"
                @click="deleteSelectedNotes"
              >
                Del
              </button>
              <button
                class="mini-btn text"
                title="Write C minor figure"
                @click="writeMinorFigure"
              >
                C minor
              </button>
              <button
                class="mini-btn text"
                title="Clear selected MIDI clip"
                @click="clearActiveTrack"
              >
                Clear
              </button>
              <button
                class="mini-btn"
                title="Close piano roll"
                @click="closePiano"
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
          <div
            ref="pianoWorkspace"
            class="piano-workspace"
          >
            <div
              ref="pianoWrap"
              class="piano-canvas-wrap"
              @scroll="syncPianoScroll('piano')"
            >
              <canvas
                ref="pianoCanvas"
                class="editor-canvas"
                @pointerdown="onPianoPointerDown"
                @wheel="onPianoWheel"
                @contextmenu.prevent
              />
            </div>
            <button
              v-if="pianoMeterLaneVisible"
              class="piano-meter-toggle"
              type="button"
              title="收起拍号轨"
              aria-label="收起拍号轨"
              :style="{ top: `${pianoMeterLaneTop + 4}px` }"
              @pointerdown.stop
              @click.stop="togglePianoMeterLane"
            />
            <button
              v-if="pianoHarmonyLaneVisible"
              class="piano-harmony-toggle"
              type="button"
              title="收起和声轨"
              aria-label="收起和声轨"
              :style="{ top: `${pianoHarmonyLaneTop + 4}px` }"
              @pointerdown.stop
              @click.stop="togglePianoHarmonyLane"
            />
            <div
              v-if="controllerLanes.length"
              ref="controllerWrap"
              class="controller-lanes-wrap"
              :style="{ height: `${controllerPanelHeight}px` }"
              @scroll="syncPianoScroll('controller')"
            >
              <div
                class="controller-lanes"
                :style="{ width: `${pianoTimelineWidth}px` }"
              >
                <section
                  v-for="lane in controllerLanes"
                  :key="lane.id"
                  class="controller-lane"
                  :style="{ width: `${pianoTimelineWidth}px` }"
                >
                  <div class="controller-lane-axis">
                    <span>{{ controllerAxisTop(lane) }}</span>
                    <span>{{ controllerAxisMiddle(lane) }}</span>
                    <span>{{ controllerAxisBottom(lane) }}</span>
                  </div>
                  <div
                    class="controller-lane-tabs"
                    :style="{ left: `${pianoKeyW + controllerScrollLeft}px` }"
                  >
                    <button
                      class="controller-menu-btn"
                      title="添加或移除控制器"
                      @click.stop="toggleControllerMenu(lane.id)"
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
                      @click.stop="setLaneController(lane.id, controllerId)"
                    >
                      {{ controllerLabel(controllerId) }}
                    </button>
                    <button
                      v-if="controllerLanes.length > 1"
                      class="controller-close"
                      title="移除控制器窗口"
                      @click.stop="removeControllerLane(lane.id)"
                    >
                      <svg
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        stroke-width="2"
                      ><path d="M18 6 6 18M6 6l12 12" /></svg>
                    </button>
                    <div
                      v-if="controllerMenuLaneId === lane.id"
                      class="controller-menu"
                    >
                      <button
                        v-for="preset in controllerMenuOptions(lane)"
                        :key="`${lane.id}-menu-${preset.id}`"
                        type="button"
                        @click.stop="addControllerToLane(lane.id, preset.id)"
                      >
                        {{ preset.label }}
                      </button>
                      <label>
                        <span>自定义 CC</span>
                        <input
                          v-model="customControllerNumber"
                          inputmode="numeric"
                          maxlength="3"
                          placeholder="0-127"
                          @keydown.enter.stop.prevent="addCustomControllerToLane(lane.id)"
                        >
                      </label>
                      <button
                        type="button"
                        @click.stop="addCustomControllerToLane(lane.id)"
                      >
                        添加
                      </button>
                      <button
                        type="button"
                        :disabled="lane.controllerIds.length <= 1"
                        @click.stop="removeActiveControllerFromLane(lane.id)"
                      >
                        移除当前
                      </button>
                    </div>
                  </div>
                  <canvas
                    :ref="el => setControllerLaneCanvas(lane.id, el)"
                    class="controller-canvas"
                    @pointerdown="event => onControllerLanePointerDown(event, lane)"
                    @contextmenu.prevent
                  />
                </section>
                <div
                  class="controller-lane-footer"
                  :style="{ width: `${pianoTimelineWidth}px` }"
                >
                  <button
                    class="controller-footer-btn"
                    title="增加控制器窗口"
                    @click="addControllerLane"
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
            <div
              v-if="pianoMeterEditor.open"
              ref="pianoMeterEditorRoot"
              class="piano-meter-popover"
              :style="{ left: `${pianoMeterEditor.x}px`, top: `${pianoMeterEditor.y}px` }"
              @pointerdown.stop
              @click.stop
            >
              <label>
                <span>拍号</span>
                <input
                  v-model.number="pianoMeterEditor.numerator"
                  type="number"
                  min="1"
                  max="255"
                  step="1"
                  @change="applyPianoMeterEditor()"
                  @keydown.enter.stop.prevent="applyPianoMeterEditor()"
                >
              </label>
              <label>
                <span>节拍时长</span>
                <select
                  v-model.number="pianoMeterEditor.denominator"
                  @change="applyPianoMeterEditor()"
                >
                  <option
                    v-for="denominator in timeSignatureDenominatorOptions"
                    :key="denominator"
                    :value="denominator"
                  >
                    {{ denominatorLabel(denominator) }}
                  </option>
                </select>
              </label>
            </div>
            <div
              v-if="pianoHarmonyEditor.open"
              ref="pianoHarmonyEditorRoot"
              class="piano-harmony-popover"
              :style="{ left: `${pianoHarmonyEditor.x}px`, top: `${pianoHarmonyEditor.y}px` }"
              @pointerdown.stop
              @click.stop
            >
              <label>
                <span>和声</span>
                <input
                  v-model="pianoHarmonyEditor.text"
                  type="text"
                  maxlength="64"
                  placeholder="Cmaj7"
                  @blur="applyPianoHarmonyEditor()"
                  @change="applyPianoHarmonyEditor()"
                  @keydown.enter.stop.prevent="applyPianoHarmonyEditor()"
                >
              </label>
            </div>
          </div>
        </div>

        <div
          v-if="mixerVisible"
          ref="pianoPanel"
          class="mixer-panel"
        >
          <div
            class="piano-resize-handle"
            role="separator"
            aria-orientation="horizontal"
            title="Resize mixer"
            @pointerdown="startPianoResize"
          >
            <span />
          </div>
          <div class="mixer-head">
            <div>
              <span>Mixer</span>
              <strong>{{ mixerTracks.length }} routes</strong>
            </div>
            <div class="mixer-actions">
              <button
                class="mini-btn text"
                :disabled="pluginsLoading"
                @click="loadPlugins()"
              >
                {{ pluginsLoading ? 'Scanning' : 'Scan' }}
              </button>
              <button
                class="mini-btn"
                title="Close mixer"
                @click="closeMixer"
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
                  v-for="track in mixerTracks"
                  :key="`mixer-${track.id}`"
                  :class="['mixer-strip', { active: activeTrack?.id === track.id }]"
                  @click="selectTrack(track.id)"
                >
                  <div class="mixer-strip-head">
                    <span
                      class="track-color"
                      :style="{ background: track.color }"
                    />
                    <strong :title="track.name">
                      {{ track.name }}
                    </strong>
                    <small>{{ trackTypeLabel(track) }}</small>
                  </div>

                  <div class="mixer-section mixer-inserts">
                    <span class="mixer-section-label">Insert</span>
                    <label
                      v-if="!canUseMixerInserts(track)"
                      class="mixer-insert-slot empty"
                    >
                      <span>No Inserts</span>
                      <select disabled>
                        <option>Unavailable</option>
                      </select>
                    </label>
                    <template v-else>
                      <label
                        v-for="slot in mixerInsertSlots(track)"
                        :key="`${track.id}-${slot.id}`"
                        :class="['mixer-insert-slot', { empty: pluginSlot(track, slot.id).type === 'empty' }]"
                      >
                        <span>{{ uniqueMixerPluginLabel(track, slot) }}</span>
                        <select
                          :value="pluginSlotValue(track, slot.id)"
                          :title="pluginSlotLabel(track, slot.id)"
                          @change="onPluginSelect(track, slot.id, $event.target.value)"
                        >
                          <option value="empty::">
                            Empty
                          </option>
                          <option
                            v-if="selectedPluginMissing(track, slot.id)"
                            :value="pluginSlotValue(track, slot.id)"
                          >
                            {{ pluginSlot(track, slot.id).name }}
                          </option>
                          <option
                            v-for="plugin in pluginOptions.vst3"
                            :key="`${track.id}-${slot.id}-vst3-${plugin.path}`"
                            :value="`vst3::${plugin.path}`"
                          >
                            {{ plugin.name }}
                          </option>
                          <option
                            v-for="plugin in pluginOptions.vst2"
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
                          :disabled="pluginSlot(track, slot.id).type === 'empty'"
                          @click.stop="loadPluginParameters(track.id, slot.id)"
                        >
                          Params
                        </button>
                        <div
                          v-if="pluginParameterRows(track.id, slot.id).length"
                          class="mixer-params"
                          @click.stop
                        >
                          <div
                            v-for="param in pluginParameterRows(track.id, slot.id)"
                            :key="`${track.id}-${slot.id}-${param.index}`"
                            class="mixer-param-row"
                            @contextmenu.prevent="openAutomationMenu($event, automationTargetForPluginParameter(track, slot.id, param), `${track.name} ${param.name}`)"
                          >
                            <span>{{ param.name }}</span>
                            <input
                              type="range"
                              min="0"
                              max="1"
                              step="0.001"
                              :value="param.value"
                              :disabled="param.automatable === false"
                              @change="setLivePluginParameter(track.id, slot.id, param.index, Number($event.target.value))"
                            >
                            <small>{{ parameterValueLabel(param) }}</small>
                          </div>
                        </div>
                      </label>
                    </template>
                  </div>

                  <div class="mixer-section mixer-sends">
                    <span class="mixer-section-label">Send</span>
                    <div
                      v-for="(send, index) in mixerSendRows(track)"
                      :key="send.id || `${track.id}-send-${index}`"
                      class="mixer-send-row"
                    >
                      <select
                        :value="send.target_bus_id"
                        @change="updateTrackSend(track, index, { target_bus_id: Number($event.target.value), id: `send_${$event.target.value}` })"
                      >
                        <option
                          v-for="bus in availableOutputBuses(track.id)"
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
                        @change="updateTrackSend(track, index, { level: Number($event.target.value) })"
                      >
                      <button
                        type="button"
                        :class="{ active: send.enabled !== false }"
                        @click.stop="updateTrackSend(track, index, { enabled: send.enabled === false })"
                      >
                        S
                      </button>
                      <button
                        type="button"
                        @click.stop="removeTrackSend(track, index)"
                      >
                        x
                      </button>
                    </div>
                    <select
                      class="mixer-send-add"
                      value=""
                      @change="onAddTrackSendChange(track, $event)"
                    >
                      <option value="">
                        Add Send
                      </option>
                      <option
                        v-for="bus in availableOutputBuses(track.id)"
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
                        @contextmenu.prevent="openAutomationMenu($event, automationTargetForTrackPan(track), `${track.name} Pan`)"
                        @change="updateTrack(track.id, { pan: Number($event.target.value) })"
                      >
                    </span>
                    <span>R</span>
                    <strong>{{ Number(track.pan || 0).toFixed(2) }}</strong>
                  </label>

                  <div class="mixer-strip-buttons">
                    <button
                      :class="['track-flag', { on: track.mute }]"
                      title="Mute"
                      @click.stop="updateTrack(track.id, { mute: !track.mute })"
                    >
                      M
                    </button>
                    <button
                      :class="['track-flag', { on: track.solo }]"
                      title="Solo"
                      @click.stop="updateTrack(track.id, { solo: !track.solo })"
                    >
                      S
                    </button>
                  </div>

                  <label class="mixer-fader">
                    <span>{{ volumeDbLabel(track.volume) }}</span>
                    <input
                      type="range"
                      min="0"
                      max="1.4"
                      step="0.01"
                      orient="vertical"
                      :value="track.volume"
                      @contextmenu.prevent="openAutomationMenu($event, automationTargetForTrackVolume(track), `${track.name} Volume`)"
                      @change="updateTrack(track.id, { volume: Number($event.target.value) })"
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
                    :style="{ background: masterBus.color }"
                  />
                  <strong>{{ masterBus.name }}</strong>
                  <small>Main Output</small>
                </div>

                <div class="mixer-section mixer-inserts">
                  <span class="mixer-section-label">Insert</span>
                  <label
                    v-for="slot in mixerInsertSlots(masterBus)"
                    :key="`master-${slot.id}`"
                    :class="['mixer-insert-slot', { empty: pluginSlot(masterBus, slot.id).type === 'empty' }]"
                  >
                    <span>{{ uniqueMixerPluginLabel(masterBus, slot) }}</span>
                    <select
                      :value="pluginSlotValue(masterBus, slot.id)"
                      :title="pluginSlotLabel(masterBus, slot.id)"
                      @change="onMasterBusPluginSelect(slot.id, $event.target.value)"
                    >
                      <option value="empty::">
                        Empty
                      </option>
                      <option
                        v-if="selectedPluginMissing(masterBus, slot.id)"
                        :value="pluginSlotValue(masterBus, slot.id)"
                      >
                        {{ pluginSlot(masterBus, slot.id).name }}
                      </option>
                      <option
                        v-for="plugin in pluginOptions.vst3"
                        :key="`master-${slot.id}-vst3-${plugin.path}`"
                        :value="`vst3::${plugin.path}`"
                      >
                        {{ plugin.name }}
                      </option>
                      <option
                        v-for="plugin in pluginOptions.vst2"
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
                      :value="masterBus.pan"
                      @change="updateMasterBus({ pan: Number($event.target.value) })"
                    >
                  </span>
                  <span>R</span>
                  <strong>{{ Number(masterBus.pan || 0).toFixed(2) }}</strong>
                </label>

                <div class="mixer-strip-buttons">
                  <button
                    :class="['track-flag', { on: masterBus.mute }]"
                    title="Mute"
                    @click.stop="updateMasterBus({ mute: !masterBus.mute })"
                  >
                    M
                  </button>
                  <button
                    :class="['track-flag', { on: masterBus.solo }]"
                    title="Solo"
                    @click.stop="updateMasterBus({ solo: !masterBus.solo })"
                  >
                    S
                  </button>
                </div>

                <label class="mixer-fader">
                  <span>{{ volumeDbLabel(masterBus.volume) }}</span>
                  <input
                    type="range"
                    min="0"
                    max="1.4"
                    step="0.01"
                    orient="vertical"
                    :value="masterBus.volume"
                    @change="updateMasterBus({ volume: Number($event.target.value) })"
                  >
                </label>
              </section>
            </div>
          </div>
        </div>
      </section>

      <aside
        v-show="inspectorVisible"
        class="inspector"
      >
        <div class="inspector-section">
          <div class="section-title">
            Engine
          </div>
          <dl class="engine-stats">
            <div>
              <dt>Transport</dt>
              <dd>{{ engine?.transport || 'stopped' }}</dd>
            </div>
            <div>
              <dt>Audio</dt>
              <dd>{{ audioConnected ? 'streaming' : 'idle' }}</dd>
            </div>
            <div>
              <dt>Tracks</dt>
              <dd>{{ tracks.length }}</dd>
            </div>
            <div>
              <dt>Notes</dt>
              <dd>{{ totalNotes }}</dd>
            </div>
          </dl>
        </div>
      </aside>
    </main>

    <div
      v-if="trackCreateDialogOpen"
      class="modal-backdrop track-create-backdrop"
      @click.self="closeTrackCreateDialog"
      @keydown.esc.stop.prevent="closeTrackCreateDialog"
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
            @click="closeTrackCreateDialog"
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
              ref="trackCreateNameInput"
              v-model="trackCreateName"
              type="text"
              autocomplete="off"
              placeholder="Auto name"
              @keydown.enter.stop.prevent="createSelectedTrack"
            >
          </label>
          <label class="track-create-field track-create-color-field">
            <span>Color</span>
            <div class="track-create-color-control">
              <input
                v-model="trackCreateColor"
                type="color"
                title="Track color"
                aria-label="Track color"
              >
              <div class="track-create-swatches">
                <button
                  v-for="color in trackCreatePalette"
                  :key="color"
                  type="button"
                  :class="['track-create-swatch', { active: trackCreateColor === color }]"
                  :style="{ background: color }"
                  :aria-label="`Use ${color}`"
                  @click="trackCreateColor = color"
                />
              </div>
            </div>
          </label>
          <label class="track-create-field">
            <span>Type</span>
            <select v-model="trackCreateType">
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
            v-if="trackCreateType !== 'automation'"
            class="track-create-field"
          >
            <span>Output</span>
            <select v-model="trackCreateOutputBusId">
              <option :value="null">
                Master
              </option>
              <option
                v-for="bus in availableOutputBuses(null)"
                :key="bus.id"
                :value="bus.id"
              >
                {{ bus.name }}
              </option>
            </select>
          </label>
          <label
            v-if="trackCreateType === 'automation'"
            class="track-create-field"
          >
            <span>Parameter</span>
            <button
              class="automation-parameter-button"
              type="button"
              @click="openAutomationParameterPickerForCreate"
            >
              {{ automationTargetLabel(trackCreateAutomationTarget) }}
            </button>
          </label>
          <label
            v-if="trackCreateType === 'audio'"
            class="track-create-field"
          >
            <span>Channels</span>
            <select v-model="trackCreateChannelType">
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
            @click="closeTrackCreateDialog"
          >
            Cancel
          </button>
          <button
            class="mini-btn text active"
            type="button"
            @click="createSelectedTrack"
          >
            Create
          </button>
        </footer>
      </section>
    </div>

    <div
      v-if="automationParameterPicker.open"
      class="modal-backdrop automation-parameter-backdrop"
      @click.self="closeAutomationParameterPicker"
      @keydown.esc.stop.prevent="closeAutomationParameterPicker"
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
            @click="closeAutomationParameterPicker"
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
              v-for="target in defaultAutomationTargets"
              :key="target.key"
              type="button"
              class="automation-parameter-row"
              @click="bindAutomationPickerTarget(target.target)"
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
              @click="pollCapturedPluginParameters"
            >
              Refresh captured
            </button>
            <div
              v-for="item in learnedAutomationTargets"
              :key="item.id"
              class="automation-learned-row"
              role="button"
              tabindex="0"
              @click="bindAutomationPickerTarget(item.target)"
              @keydown.enter.stop.prevent="bindAutomationPickerTarget(item.target)"
              @keydown.space.stop.prevent="bindAutomationPickerTarget(item.target)"
            >
              <input
                :value="item.name"
                @pointerdown.stop
                @click.stop
                @change="renameLearnedAutomationParameter(item.id, $event.target.value)"
              >
              <small>{{ item.detail }}</small>
            </div>
          </section>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useDawHost } from '@/composables/useDawHost.js'
import {
  CONTROLLER_PRESETS,
  DEFAULT_CONTROLLER_IDS,
  DEFAULT_NOTE_VELOCITY,
  applyCurveAmount,
  controllerCurveValueAtBeat,
  controllerDefinitionFromId,
  controllerDisplayRange,
  controllerLaneColorStyles,
  controllerLaneStackHeight,
  controllerRenderPoints,
  controllerUnitToValue,
  controllerValueToUnit,
  curveUnitAtPosition,
  createDefaultControllerLanes,
  eventMatchesController,
  makeControllerEventId,
  makeControllerLaneId,
  normalizeCurveAmount,
  normalizeControllerEvent,
  valueFromControllerEvent,
} from './controllerLanes.js'
import {
  PIANO_QUANTIZE_OPTIONS,
  interpolateControllerValue,
  quantizeStepFromId,
  quantizedBeatsBetween,
  snapBeatToGrid,
} from './pianoQuantize.js'
import {
  beatsToSeconds,
  effectiveTempoAtBeat,
} from './tempoAutomation.js'
import {
  effectiveMeterAtBeat,
  meterBarLinesBetween,
  meterSegments,
  meterPositionAtBeat,
  normalizeMeterEvents,
} from './meterEvents.js'

defineProps({
  embedded: { type: Boolean, default: false },
})

const {
  project,
  host,
  engine,
  tracks,
  activeTrack,
  loading,
  syncing,
  hostError,
  audioConnected,
  playing,
  positionBeats,
  totalNotes,
  plugins,
  pluginsLoading,
  editorWindows,
  pluginParameters,
  learnedAutomationParameters,
  loadProject,
  saveProject,
  syncProject,
  resetDemo,
  transport,
  updateTrack,
  createTrack,
  importAudioFile,
  deleteTrack,
  loadPlugins,
  setTrackPlugin,
  openPluginEditor,
  loadPluginParameters,
  setPluginParameter,
  createAutomationTrack,
  retargetAutomationTrack,
  pollCapturedPluginParameters,
  renameLearnedAutomationParameter,
  selectTrack,
  refreshHostStatus,
} = useDawHost()

const arrangementWrap = ref(null)
const arrangementCanvas = ref(null)
const editorStack = ref(null)
const pianoPanel = ref(null)
const pianoWorkspace = ref(null)
const pianoWrap = ref(null)
const pianoCanvas = ref(null)
const controllerWrap = ref(null)
const timeSignatureRoot = ref(null)
const pianoMeterEditorRoot = ref(null)
const pianoHarmonyEditorRoot = ref(null)
const controllerLaneCanvases = new Map()
const automationMenu = ref({ open: false, x: 0, y: 0, target: null, label: '' })
const trackContextMenu = ref({ open: false, x: 0, y: 0, trackId: null, name: '' })
const trackReorderDrag = ref({ trackId: null, overTrackId: null, placement: 'after' })

const defaultPxPerBeat = 56
const supportedAudioImportExtensions = ['aac', 'flac', 'm4a', 'mp3', 'wav']
const supportedAudioImportMimeTypes = new Set([
  'audio/aac',
  'audio/flac',
  'audio/mp3',
  'audio/mp4',
  'audio/mpeg',
  'audio/wav',
  'audio/wave',
  'audio/x-flac',
  'audio/x-m4a',
  'audio/x-wav',
])
const arrangementPxPerBeat = ref(defaultPxPerBeat)
const arrangementScrollLeft = ref(0)
const pianoPxPerBeat = ref(defaultPxPerBeat)
const pianoTimelineWidth = ref(0)
const minArrangementPxPerBeat = 8
const maxArrangementPxPerBeat = 64
const minPianoPxPerBeat = 8
const maxPianoPxPerBeat = 64
const arrangementEmptyBars = 64
const pianoEmptyBars = 32
const defaultTrackListWidth = 246
const minTrackListWidth = 190
const maxTrackListWidth = 420
const trackReorderMidpoint = 0.5
const rulerBeatLabelMinScale = 30
const rulerMajorTickRatio = 1 / 3
const rulerMinorTickRatio = rulerMajorTickRatio / 2
const rulerFineTickRatio = rulerMinorTickRatio / 2
const rulerBarLabelFont = '12px Cascadia Mono, Consolas, monospace'
const rulerBeatLabelFont = '10px Cascadia Mono, Consolas, monospace'
const rulerLabelGap = 2
const trackListWidth = ref(defaultTrackListWidth)
const arrangementRulerH = 30
const arrangementToolbarH = 34
const arrangementTrackH = 72
const pianoKeyW = 76
const pianoRulerH = 24
const pianoMeterLaneH = 28
const pianoSubtrackH = pianoMeterLaneH
const pianoRowH = 12
const controllerLaneTabH = 24
const controllerLaneBodyH = 72
const controllerLaneH = controllerLaneTabH + controllerLaneBodyH
const controllerLaneFooterH = 28
const automationPointHitRadius = 7
const controllerPointHitRadius = 7
const automationCurveHandleHitRadius = 7
const controllerCurveHandleHitRadius = 7
const curveHandleMinSegmentPx = Math.max(automationPointHitRadius, controllerPointHitRadius) * 2 + 4
const curveHandleDragScale = 2
const pianoDrawLongPressMs = 260
const pianoDrawMoveTolerancePx = 5
const pianoMeterEventHitRadius = 9
const pianoHarmonyEventHitRadius = 9
const minPitch = 36
const maxPitch = 84
const trackCreatePalette = ['#4e79ff', '#d95b55', '#5f916b', '#d7b66f', '#b489d6', '#58a7b8']
const pianoSubtrackIds = ['meter', 'harmony']
const timeSignatureDenominatorOptions = [2, 4, 8, 16, 32]
const visualPositionBeats = ref(0)
const pianoTool = ref('select')
const pianoQuantizeId = ref('1/16')
const pianoSnapEnabled = ref(true)
const pianoQuantizeMenuOpen = ref(false)
const pianoMeterLaneOpen = ref(false)
const pianoHarmonyLaneOpen = ref(false)
const pianoSubtrackSyncKey = ref('')
const pianoSubtrackOrder = ref([])
const pianoSubtrackCreateValue = ref('')
const tempoInput = ref(120)
const tempoInputFocused = ref(false)
const timeSignatureNumerator = ref(4)
const timeSignatureDenominator = ref(4)
const timeSignatureNumeratorFocused = ref(false)
const timeSignaturePopoverOpen = ref(false)
const timeSignatureDenominatorPopoverOpen = ref(false)
const pianoMeterEditor = ref({
  open: false,
  x: 0,
  y: 0,
  eventIndex: -1,
  beat: 0,
  numerator: 4,
  denominator: 4,
})
const pianoHarmonyEditor = ref({
  open: false,
  x: 0,
  y: 0,
  eventIndex: -1,
  beat: 0,
  text: '',
})
const selectedNoteIds = ref(new Set())
const selectedClipIds = ref(new Set())
const selectedAutomationPoint = ref({ trackId: null, index: -1 })
const selectedControllerEventId = ref(null)
const noteClipboard = ref([])
const clipClipboard = ref([])
const draftNote = ref(null)
const selectionBox = ref(null)
const activeClipId = ref(null)
const lowerEditorMode = ref(null)
const pianoVisible = computed(() => lowerEditorMode.value === 'piano')
const mixerVisible = computed(() => lowerEditorMode.value === 'mixer')
const audioDropActive = ref(false)
const audioImporting = ref(false)
const trackCreateDialogOpen = ref(false)
const trackCreateName = ref('')
const trackCreateNameInput = ref(null)
const trackCreateColor = ref(trackCreatePalette[0])
const trackCreateType = ref('instrument')
const trackCreateChannelType = ref('multichannel')
const trackCreateOutputBusId = ref(null)
const trackCreateAutomationTarget = ref(null)
const automationParameterPicker = ref({ open: false, mode: 'create', trackId: null })
const pianoPanelHeight = ref(null)
const inspectorVisible = ref(true)
const controllerLanes = ref(createDefaultControllerLanes())
const controllerMenuLaneId = ref(null)
const customControllerNumber = ref('')
const controllerScrollLeft = ref(0)
const controllerPanelHeight = computed(() => controllerLaneStackHeight(
  controllerLanes.value.length,
  controllerLaneH,
  controllerLaneFooterH
))
const pianoQuantizeOptions = PIANO_QUANTIZE_OPTIONS

let resizeObserver = null
let raf = 0
let lastFrame = 0
let pianoDrag = null
let pianoResizeDrag = null
let trackListResizeDrag = null
let arrangementDrag = null
let controllerDrag = null
let automationDrag = null
let syncingPianoScroll = false
let audioDecodeContext = null
let tempoUpdateTimer = null
let pianoLongPressTimer = null
let learnedParameterPollTimer = null

const snapStep = 0.25
const minFreehandStep = 0.0625
const minArrangementPanelHeight = arrangementToolbarH + arrangementRulerH
const minPianoPanelHeight = 140

const tempo = computed(() => effectiveTempoAtBeat(project.value, visualPositionBeats.value))
const meterBeats = computed(() => {
  const numerator = normalizeTimeSignatureNumerator(project.value?.time_signature?.[0])
  const denominator = normalizeTimeSignatureDenominator(project.value?.time_signature?.[1])
  return numerator * (4 / denominator)
})
const timeSignatureLabel = computed(() => (
  `${timeSignatureNumerator.value} / ${timeSignatureDenominator.value}`
))
const timeSignatureDenominatorLabel = computed(() => (
  denominatorLabel(timeSignatureDenominator.value)
))
const pluginOptions = computed(() => ({
  vst3: Array.isArray(plugins.value?.vst3) ? plugins.value.vst3 : [],
  vst2: Array.isArray(plugins.value?.vst2) ? plugins.value.vst2 : [],
}))
const pianoQuantizeStep = computed(() => quantizeStepFromId(pianoQuantizeId.value))
const pianoQuantizeLabel = computed(() => (
  pianoQuantizeOptions.find(option => option.id === pianoQuantizeId.value)?.label || '1/16'
))
const isPianoSnapActive = computed(() => pianoSnapEnabled.value && pianoQuantizeStep.value !== null)
const activePianoSnapStep = computed(() => (
  isPianoSnapActive.value ? pianoQuantizeStep.value : null
))
const activeNoteStep = computed(() => activePianoSnapStep.value || minFreehandStep)
const hasProjectMeterEvents = computed(() => (
  Array.isArray(project.value?.meter_events) && project.value.meter_events.length > 0
))
const pianoMeterLaneVisible = computed(() => pianoMeterLaneOpen.value)
const pianoHarmonyLaneVisible = computed(() => pianoHarmonyLaneOpen.value)
const pianoVisibleSubtracks = computed(() => (
  pianoSubtrackOrder.value.filter((id) => (
    (id === 'meter' && pianoMeterLaneVisible.value)
    || (id === 'harmony' && pianoHarmonyLaneVisible.value)
  ))
))
const pianoMeterLaneTop = computed(() => pianoSubtrackTop('meter'))
const pianoHarmonyLaneTop = computed(() => pianoSubtrackTop('harmony'))
const pianoNoteTop = computed(() => (
  pianoRulerH + pianoVisibleSubtracks.value.length * pianoSubtrackH
))
const pianoSubtrackOptions = computed(() => [
  { id: 'meter', label: '拍号轨', disabled: pianoMeterLaneVisible.value },
  { id: 'harmony', label: '和声轨', disabled: pianoHarmonyLaneVisible.value },
])
const activeMidiClip = computed(() => {
  for (const track of tracks.value) {
    for (const clip of track.clips || []) {
      if (clip.id === activeClipId.value && clip.type === 'midi') {
        return { track, clip }
      }
    }
  }
  return null
})
const lowerEditorVisible = computed(() => (
  (pianoVisible.value && activeMidiClip.value) || mixerVisible.value
))
const mixerTracks = computed(() => tracks.value.filter(track => !isAutomationTrack(track)))
const masterBus = computed(() => normalizeMasterBus(project.value?.master_bus))
const editorStackStyle = computed(() => {
  if (!lowerEditorVisible.value || !pianoPanelHeight.value) return {}
  return {
    gridTemplateRows: `minmax(${minArrangementPanelHeight}px, 1fr) ${pianoPanelHeight.value}px`,
  }
})
const arrangementLayoutStyle = computed(() => ({
  '--track-list-width': `${trackListWidth.value}px`,
}))
const arrangementWrapStyle = computed(() => ({
  '--arrangement-scroll-left': `${arrangementScrollLeft.value}px`,
}))
const positionLabel = computed(() => {
  const position = meterPositionAtBeat(project.value, visualPositionBeats.value)
  return `${position.bar.toString().padStart(5, '0')}.${position.beat.toString().padStart(2, '0')}.${position.ticks.toString().padStart(3, '0')}`
})
const defaultAutomationTargets = computed(() => {
  const targets = [
    {
      key: "global-tempo-bpm",
      label: 'Tempo BPM',
      detail: 'Session tempo',
      target: automationTargetForTempoBpm(),
    },
  ]
  for (const track of tracks.value) {
    if (isAutomationTrack(track)) continue
    targets.push({
      key: `track-volume-${track.id}`,
      label: `${track.name} Volume`,
      detail: 'Track volume',
      target: automationTargetForTrackVolume(track),
    })
    targets.push({
      key: `track-pan-${track.id}`,
      label: `${track.name} Pan`,
      detail: 'Track pan',
      target: automationTargetForTrackPan(track),
    })
    for (const slot of track.plugin_slots || []) {
      if (!slot || slot.type !== 'vst3') continue
      for (const param of pluginParameterRows(track.id, slot.id).filter(param => param.automatable !== false)) {
        targets.push({
          key: `plugin-${track.id}-${slot.id}-${param.index}`,
          label: `${track.name} / ${slot.name} / ${param.name || `Parameter ${param.index}`}`,
          detail: param.units || 'VST parameter',
          target: automationTargetForPluginParameter(track, slot.id, param),
        })
      }
    }
  }
  return targets
})
const learnedAutomationTargets = computed(() => (
  learnedAutomationParameters.value.map(item => ({
    id: item.id,
    name: item.name,
    detail: learnedAutomationTargetDetail(item),
    target: {
      ...(item.target || {}),
      label: item.name,
    },
  }))
))

function cloneProject() {
  return JSON.parse(JSON.stringify(project.value || {}))
}

function normalizeMasterBus(bus = {}) {
  const source = bus && typeof bus === 'object' ? bus : {}
  const volume = Number(source.volume ?? 1)
  const pan = Number(source.pan ?? 0)
  return {
    id: 'master',
    type: 'bus',
    name: source.name || 'Master Bus',
    color: source.color || '#58a7b8',
    volume: Number.isFinite(volume) ? clamp(volume, 0, 2) : 1,
    pan: Number.isFinite(pan) ? clamp(pan, -1, 1) : 0,
    mute: Boolean(source.mute),
    solo: Boolean(source.solo),
    plugin_slots: Array.isArray(source.plugin_slots) ? source.plugin_slots : [],
  }
}

function findProjectTrack(nextProject, trackId) {
  return (nextProject.tracks || []).find(track => track.id === trackId)
}

function findClipRecord(clipId) {
  for (const track of tracks.value) {
    const clip = (track.clips || []).find(item => item.id === clipId)
    if (clip) return { track, clip }
  }
  return null
}

async function persistProjectUpdate(updater) {
  if (!project.value) return null
  const nextProject = cloneProject()
  updater(nextProject)
  const res = await saveProject(nextProject, { broadcast: true })
  drawAll()
  return res
}

function normalizeTempo(value) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return 120
  return Math.round(Math.max(1, parsed) * 10) / 10
}

function syncTempoField(nextProject) {
  if (tempoInputFocused.value) return
  tempoInput.value = normalizeTempo(effectiveTempoAtBeat(nextProject, visualPositionBeats.value))
}

async function updateTempo() {
  if (!project.value) return
  const nextTempo = normalizeTempo(tempoInput.value)
  tempoInput.value = nextTempo
  await persistProjectUpdate((nextProject) => {
    nextProject.tempo = nextTempo
  })
}

function scheduleTempoUpdate() {
  clearTimeout(tempoUpdateTimer)
  tempoUpdateTimer = setTimeout(() => {
    updateTempo()
  }, 160)
}

function onTempoWheel(event) {
  const direction = event.deltaY < 0 ? 1 : -1
  const step = event.shiftKey ? 0.1 : 1
  tempoInput.value = normalizeTempo(Number(tempoInput.value || tempo.value) + direction * step)
  scheduleTempoUpdate()
}

function normalizeTimeSignatureNumerator(value) {
  const parsed = Number.parseInt(value, 10)
  if (!Number.isFinite(parsed)) return 4
  return clamp(parsed, 1, 255)
}

function normalizeTimeSignatureDenominator(value) {
  const parsed = Number.parseInt(value, 10)
  return timeSignatureDenominatorOptions.includes(parsed) ? parsed : 4
}

function denominatorLabel(denominator) {
  return `1/${normalizeTimeSignatureDenominator(denominator)}`
}

function syncTimeSignatureFields(nextProject) {
  const meter = effectiveMeterAtBeat(nextProject, visualPositionBeats.value)
  if (!timeSignatureNumeratorFocused.value) {
    timeSignatureNumerator.value = normalizeTimeSignatureNumerator(meter.numerator)
  }
  timeSignatureDenominator.value = normalizeTimeSignatureDenominator(meter.denominator)
}

function syncTransportDisplayFields(nextProject = project.value) {
  syncTempoField(nextProject)
  syncTimeSignatureFields(nextProject)
}

// Syncs lane visibility only when persisted subtrack data changes, preserving manual collapse across unrelated saves.
function syncPianoSubtrackLanes(nextProject) {
  if (!nextProject) return
  const syncKey = pianoSubtrackProjectSyncKey(nextProject)
  if (pianoSubtrackSyncKey.value === syncKey) return
  const order = normalizePianoSubtrackOrder(nextProject.piano_subtrack_order)
  if (Array.isArray(nextProject.meter_events) && nextProject.meter_events.length > 0 && !order.includes('meter')) {
    order.push('meter')
  }
  if (Array.isArray(nextProject.harmony_events) && nextProject.harmony_events.length > 0 && !order.includes('harmony')) {
    order.push('harmony')
  }
  pianoSubtrackOrder.value = order
  pianoMeterLaneOpen.value = order.includes('meter')
  pianoHarmonyLaneOpen.value = order.includes('harmony')
  pianoSubtrackSyncKey.value = syncKey
}

function pianoSubtrackProjectSyncKey(nextProject) {
  return JSON.stringify({
    order: normalizePianoSubtrackOrder(nextProject?.piano_subtrack_order),
    meterEvents: normalizeEditableMeterEvents(nextProject?.meter_events || []),
    harmonyEvents: normalizeEditableHarmonyEvents(nextProject?.harmony_events || []),
  })
}

function normalizePianoSubtrackOrder(order) {
  const nextOrder = []
  for (const id of Array.isArray(order) ? order : []) {
    const subtrackId = String(id || '').trim().toLowerCase()
    if (!pianoSubtrackIds.includes(subtrackId) || nextOrder.includes(subtrackId)) continue
    nextOrder.push(subtrackId)
  }
  return nextOrder
}

function pianoSubtrackOrderWith(subtrackId) {
  return normalizePianoSubtrackOrder([...pianoSubtrackOrder.value, subtrackId])
}

function pianoSubtrackTop(subtrackId) {
  const index = pianoVisibleSubtracks.value.indexOf(subtrackId)
  return pianoRulerH + Math.max(0, index) * pianoSubtrackH
}

function toggleTimeSignaturePopover() {
  timeSignaturePopoverOpen.value = !timeSignaturePopoverOpen.value
  if (!timeSignaturePopoverOpen.value) {
    timeSignatureDenominatorPopoverOpen.value = false
  }
}

function closeTimeSignaturePopover() {
  timeSignaturePopoverOpen.value = false
  timeSignatureDenominatorPopoverOpen.value = false
}

function closeTrackContextMenu() {
  trackContextMenu.value = { open: false, x: 0, y: 0, trackId: null, name: '' }
}

// Track reordering persists the shared project order so the sidebar, canvas, and rack stay aligned.
function canDragTrackRow(track) {
  return track?.id != null && tracks.value.length > 1 && !loading.value
}

function isTrackReorderInteractiveTarget(target) {
  return Boolean(target?.closest?.('button, input, select, textarea, a'))
}

function sameTrackId(left, right) {
  return left != null && right != null && String(left) === String(right)
}

function trackOrderKey(trackList) {
  return (trackList || []).map(track => String(track.id)).join('|')
}

function moveTrackInList(trackList, sourceTrackId, targetTrackId, placement) {
  const nextTracks = [...(trackList || [])]
  const sourceIndex = nextTracks.findIndex(track => sameTrackId(track.id, sourceTrackId))
  const targetIndex = nextTracks.findIndex(track => sameTrackId(track.id, targetTrackId))
  if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return nextTracks

  const [movedTrack] = nextTracks.splice(sourceIndex, 1)
  const targetIndexAfterRemoval = nextTracks.findIndex(track => sameTrackId(track.id, targetTrackId))
  if (targetIndexAfterRemoval < 0) return trackList || []

  const insertIndex = placement === 'before'
    ? targetIndexAfterRemoval
    : targetIndexAfterRemoval + 1
  nextTracks.splice(insertIndex, 0, movedTrack)
  return nextTracks
}

function trackReorderPlacementFromEvent(event) {
  const rect = event.currentTarget?.getBoundingClientRect?.()
  if (!rect?.height) return 'after'
  const localY = Number(event.clientY || 0) - rect.top
  return localY < rect.height * trackReorderMidpoint ? 'before' : 'after'
}

function draggedTrackIdFromEvent(event) {
  const rawTrackId = trackReorderDrag.value.trackId ?? event.dataTransfer?.getData('text/plain')
  const track = tracks.value.find(item => sameTrackId(item.id, rawTrackId))
  return track?.id ?? rawTrackId
}

function isTrackReorderDragging(track) {
  return sameTrackId(trackReorderDrag.value.trackId, track?.id)
}

function isTrackReorderDropTarget(track, placement) {
  const drag = trackReorderDrag.value
  return Boolean(
    drag.trackId != null
    && !sameTrackId(drag.trackId, track?.id)
    && sameTrackId(drag.overTrackId, track?.id)
    && drag.placement === placement
  )
}

function startTrackReorderDrag(event, track) {
  if (!canDragTrackRow(track) || isTrackReorderInteractiveTarget(event.target)) {
    event.preventDefault()
    return
  }
  closeTrackContextMenu()
  automationMenu.value = { open: false, x: 0, y: 0, target: null, label: '' }
  selectTrack(track.id)
  trackReorderDrag.value = { trackId: track.id, overTrackId: track.id, placement: 'after' }
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', String(track.id))
  }
}

function onTrackReorderDragOver(event, track) {
  if (trackReorderDrag.value.trackId == null || !canDragTrackRow(track)) return
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
  trackReorderDrag.value = {
    ...trackReorderDrag.value,
    overTrackId: track.id,
    placement: trackReorderPlacementFromEvent(event),
  }
}

function endTrackReorderDrag() {
  trackReorderDrag.value = { trackId: null, overTrackId: null, placement: 'after' }
}

async function dropTrackReorder(event, targetTrack) {
  const sourceTrackId = draggedTrackIdFromEvent(event)
  const placement = trackReorderPlacementFromEvent(event)
  endTrackReorderDrag()
  if (
    sourceTrackId == null
    || targetTrack?.id == null
    || sameTrackId(sourceTrackId, targetTrack.id)
    || loading.value
  ) return

  const reorderedTracks = moveTrackInList(tracks.value, sourceTrackId, targetTrack.id, placement)
  if (trackOrderKey(reorderedTracks) === trackOrderKey(tracks.value)) return

  await persistProjectUpdate((nextProject) => {
    nextProject.tracks = moveTrackInList(nextProject.tracks || [], sourceTrackId, targetTrack.id, placement)
  })
  selectTrack(sourceTrackId)
}

function onDocumentPointerDown(event) {
  if (automationMenu.value.open) {
    automationMenu.value = { open: false, x: 0, y: 0, target: null, label: '' }
  }
  if (trackContextMenu.value.open) {
    closeTrackContextMenu()
  }
  const meterRoot = pianoMeterEditorRoot.value
  if (pianoMeterEditor.value.open && (!meterRoot || !meterRoot.contains(event.target))) {
    closePianoMeterEditor()
  }
  const harmonyRoot = pianoHarmonyEditorRoot.value
  if (pianoHarmonyEditor.value.open && (!harmonyRoot || !harmonyRoot.contains(event.target))) {
    applyPianoHarmonyEditor().finally(() => closePianoHarmonyEditor()).catch(() => null)
  }
  const root = timeSignatureRoot.value
  if (!root || root.contains(event.target)) return
  closeTimeSignaturePopover()
}

async function updateTimeSignature() {
  if (!project.value) return
  const numerator = normalizeTimeSignatureNumerator(timeSignatureNumerator.value)
  const denominator = normalizeTimeSignatureDenominator(timeSignatureDenominator.value)
  timeSignatureNumerator.value = numerator
  timeSignatureDenominator.value = denominator
  await applyTransportTimeSignatureChange()
}

async function setTimeSignatureDenominator(denominator) {
  timeSignatureDenominator.value = normalizeTimeSignatureDenominator(denominator)
  timeSignatureDenominatorPopoverOpen.value = false
  await updateTimeSignature()
}

function isAtTimelineStart(beat) {
  return Math.abs(Number(beat || 0)) < 0.0001
}

async function applyTransportTimeSignatureChange() {
  const numerator = normalizeTimeSignatureNumerator(timeSignatureNumerator.value)
  const denominator = normalizeTimeSignatureDenominator(timeSignatureDenominator.value)
  const changeBeat = Math.max(0, roundPianoBeat(visualPositionBeats.value))
  await persistProjectUpdate((nextProject) => {
    if (isAtTimelineStart(changeBeat)) {
      nextProject.time_signature = [numerator, denominator]
      nextProject.meter_events = normalizeEditableMeterEvents(nextProject.meter_events || [])
        .map(event => (
          isAtTimelineStart(event.beat)
            ? { ...event, numerator, denominator }
            : event
        ))
      return
    }
    ensureBaseMeterEventIfNeeded(nextProject, changeBeat)
    upsertMeterEventInProject(nextProject, changeBeat, numerator, denominator)
  })
}

async function createPianoSubtrack() {
  const value = pianoSubtrackCreateValue.value
  pianoSubtrackCreateValue.value = ''
  if (value === 'meter') {
    await togglePianoMeterLane()
  } else if (value === 'harmony') {
    await togglePianoHarmonyLane()
  }
}

// Toggles the piano meter lane without deleting existing meter events.
async function togglePianoMeterLane() {
  if (!project.value) return
  if (pianoMeterLaneVisible.value) {
    pianoMeterLaneOpen.value = false
    closePianoMeterEditor()
    drawAll()
    return
  }
  await createPianoMeterLane()
  pianoMeterLaneOpen.value = true
  drawAll()
}

// Toggles the harmony marker lane without deleting saved harmony labels.
async function togglePianoHarmonyLane() {
  if (!project.value) return
  if (pianoHarmonyLaneVisible.value) {
    pianoHarmonyLaneOpen.value = false
    closePianoHarmonyEditor()
    drawAll()
    return
  }
  await createPianoHarmonyLane()
  pianoHarmonyLaneOpen.value = true
  drawAll()
}

// Creates the backing meter event list only when the project does not have one yet.
async function createPianoMeterLane() {
  if (!project.value) return
  const nextOrder = pianoSubtrackOrderWith('meter')
  if (hasProjectMeterEvents.value && pianoSubtrackOrder.value.includes('meter')) return
  await persistProjectUpdate((nextProject) => {
    if (!hasProjectMeterEvents.value) {
      nextProject.meter_events = normalizeMeterEvents(nextProject)
    }
    nextProject.piano_subtrack_order = normalizePianoSubtrackOrder(nextOrder)
  })
  pianoSubtrackOrder.value = nextOrder
}

async function createPianoHarmonyLane() {
  if (!project.value) return
  const nextOrder = pianoSubtrackOrderWith('harmony')
  if (pianoSubtrackOrder.value.includes('harmony')) return
  await persistProjectUpdate((nextProject) => {
    nextProject.harmony_events = normalizeEditableHarmonyEvents(nextProject.harmony_events || [])
    nextProject.piano_subtrack_order = normalizePianoSubtrackOrder(nextOrder)
  })
  pianoSubtrackOrder.value = nextOrder
}

async function upsertMeterEventAtBeat(beat) {
  if (!project.value) return
  const numerator = normalizeTimeSignatureNumerator(timeSignatureNumerator.value)
  const denominator = normalizeTimeSignatureDenominator(timeSignatureDenominator.value)
  const nextBeat = Math.max(0, snapBeatToGrid(beat, activePianoSnapStep.value))
  await persistProjectUpdate((nextProject) => {
    upsertMeterEventInProject(nextProject, nextBeat, numerator, denominator)
  })
}

async function persistMeterEvents(events) {
  if (!project.value) return
  const normalized = normalizeEditableMeterEvents(events)
  await persistProjectUpdate((nextProject) => {
    nextProject.meter_events = normalized
  })
}

async function deleteMeterEventAtIndex(index) {
  const events = editableMeterEvents()
  if (!events[index]) return
  events.splice(index, 1)
  await persistMeterEvents(events)
}

function baseMeterEvent(nextProject) {
  const meter = Array.isArray(nextProject?.time_signature) ? nextProject.time_signature : [4, 4]
  return {
    beat: 0,
    numerator: normalizeTimeSignatureNumerator(meter[0]),
    denominator: normalizeTimeSignatureDenominator(meter[1]),
  }
}

function ensureBaseMeterEventIfNeeded(nextProject, changeBeat) {
  if (isAtTimelineStart(changeBeat)) return
  const events = normalizeEditableMeterEvents(nextProject.meter_events || [])
  const hasPreviousMarker = events.some(event => Number(event.beat || 0) < changeBeat - 0.0001)
  if (!hasPreviousMarker) {
    events.push(baseMeterEvent(nextProject))
  }
  nextProject.meter_events = normalizeEditableMeterEvents(events)
}

function upsertMeterEventInProject(nextProject, beat, numerator, denominator) {
  nextProject.meter_events = normalizeEditableMeterEvents([
    ...(Array.isArray(nextProject.meter_events) ? nextProject.meter_events : []),
    { beat, numerator, denominator },
  ])
}

function defaultTrackNameForType(type) {
  if (type === 'audio') return 'Audio Track'
  if (type === 'bus') return 'Bus'
  return 'Instrument'
}

function defaultTrackCreateColor() {
  return trackCreatePalette[tracks.value.length % trackCreatePalette.length]
}

function openTrackCreateDialog() {
  trackCreateName.value = ''
  trackCreateColor.value = defaultTrackCreateColor()
  trackCreateType.value = 'instrument'
  trackCreateChannelType.value = 'multichannel'
  trackCreateOutputBusId.value = null
  trackCreateAutomationTarget.value = automationUnassignedTarget()
  trackCreateDialogOpen.value = true
  nextTick(() => trackCreateNameInput.value?.focus?.())
}

function closeTrackCreateDialog() {
  trackCreateDialogOpen.value = false
}

function openTrackContextMenu(event, track) {
  if (!track) return
  event.preventDefault()
  event.stopPropagation()
  automationMenu.value = { open: false, x: 0, y: 0, target: null, label: '' }
  selectTrack(track.id)
  const menuWidth = 190
  trackContextMenu.value = {
    open: true,
    x: Math.max(0, Math.min(Number(event.clientX || 0), window.innerWidth - menuWidth)),
    y: Math.max(0, Math.min(Number(event.clientY || 0), window.innerHeight - 80)),
    trackId: track.id,
    name: track.name || `Track ${track.id}`,
  }
}

async function deleteTrackFromContextMenu() {
  const trackId = trackContextMenu.value.trackId
  closeTrackContextMenu()
  if (!trackId || tracks.value.length <= 1 || loading.value) return
  await deleteTrack(trackId)
}

async function createSelectedTrack() {
  if (trackCreateType.value === 'automation') {
    const target = trackCreateAutomationTarget.value || automationUnassignedTarget()
    const name = trackCreateName.value.trim() || target.label || 'Automation'
    const res = await createAutomationTrackForTarget(target, {
      name,
      color: trackCreateColor.value,
    })
    closeTrackCreateDialog()
    return res
  }
  const type = trackCreateType.value === 'audio'
    ? 'audio'
    : trackCreateType.value === 'bus'
      ? 'bus'
      : 'instrument'
  const channelType = trackCreateChannelType.value === 'mono' ? 'mono' : 'multichannel'
  const name = trackCreateName.value.trim() || defaultTrackNameForType(type)
  const res = await createTrack(name, {
    type,
    color: trackCreateColor.value,
    channel_type: type === 'audio' ? channelType : 'multichannel',
    output_bus_id: trackCreateOutputBusId.value,
  })
  closeTrackCreateDialog()
  return res
}

function automationUnassignedTarget() {
  return { kind: 'unassigned', label: 'Unassigned' }
}

function openAutomationParameterPickerForCreate() {
  automationParameterPicker.value = { open: true, mode: 'create', trackId: null }
  loadAutomationPickerPluginParameters()
  pollCapturedPluginParameters().catch(() => null)
}

function openAutomationParameterPickerForTrack(track) {
  automationParameterPicker.value = { open: true, mode: 'track', trackId: track.id }
  loadAutomationPickerPluginParameters()
  pollCapturedPluginParameters().catch(() => null)
}

function closeAutomationParameterPicker() {
  automationParameterPicker.value = { open: false, mode: 'create', trackId: null }
}

async function bindAutomationPickerTarget(target) {
  if (!target) return
  if (automationParameterPicker.value.mode === 'create') {
    trackCreateAutomationTarget.value = target
    closeAutomationParameterPicker()
    return
  }
  const trackId = automationParameterPicker.value.trackId
  if (!trackId) return
  await retargetAutomationTrack(trackId, target)
  closeAutomationParameterPicker()
}

function loadAutomationPickerPluginParameters() {
  for (const track of tracks.value) {
    if (!isInstrumentTrack(track)) continue
    for (const slot of track.plugin_slots || []) {
      if (slot?.type === 'vst3') {
        loadPluginParameters(track.id, slot.id).catch(() => null)
      }
    }
  }
}

function makeClip(type = 'midi', start = 0) {
  const duration = 4
  return {
    id: makeClipId(),
    type,
    name: type === 'midi' ? 'MIDI Clip' : 'Audio Clip',
    start: snapBeat(start),
    duration,
    color: activeTrack.value?.color || '#4e79ff',
    source: '',
    path: '',
    notes: [],
    events: [],
  }
}

async function createClip(type = 'midi') {
  if (!activeTrack.value) return null
  const clip = makeClip(type, visualPositionBeats.value)
  await persistProjectUpdate((nextProject) => {
    const track = findProjectTrack(nextProject, activeTrack.value.id)
    if (!track) return
    track.clips = [...(track.clips || []), clip]
  })
  selectedClipIds.value = new Set([clip.id])
  activeClipId.value = clip.id
  if (type === 'midi') openPiano()
  drawAll()
  return clip
}

function isAudioFile(file) {
  if (!file) return false
  const mimeType = String(file.type || '').toLowerCase()
  if (supportedAudioImportMimeTypes.has(mimeType)) return true
  const name = String(file.name || '').toLowerCase()
  return supportedAudioImportExtensions.some(extension => name.endsWith(`.${extension}`))
}

function hasAudioDrag(event) {
  const items = Array.from(event.dataTransfer?.items || [])
  if (
    items.some(item => (
      item.kind === 'file'
      && supportedAudioImportMimeTypes.has(String(item.type || '').toLowerCase())
    ))
  ) {
    return true
  }
  const files = Array.from(event.dataTransfer?.files || [])
  return files.some(isAudioFile)
}

function onAudioDragEnter(event) {
  if (!hasAudioDrag(event)) return
  audioDropActive.value = true
}

function onAudioDragOver(event) {
  if (!hasAudioDrag(event)) return
  event.dataTransfer.dropEffect = 'copy'
  audioDropActive.value = true
}

function onAudioDragLeave(event) {
  if (!audioDropActive.value) return
  const wrap = arrangementWrap.value
  if (wrap?.contains(event.relatedTarget)) return
  audioDropActive.value = false
}

async function onAudioDrop(event) {
  audioDropActive.value = false
  const files = Array.from(event.dataTransfer?.files || []).filter(isAudioFile)
  if (!files.length) return

  const start = snapBeat(arrangementDropBeat(event))
  audioImporting.value = true
  try {
    for (const file of files) {
      const prepared = await prepareAudioImport(file)
      const res = await importAudioFile(prepared.file, {
        start,
        duration_seconds: prepared.durationSeconds,
        waveform: prepared.waveform,
        original_name: file.name || prepared.file.name,
      })
      if (res?.clip?.id) {
        selectedClipIds.value = new Set([res.clip.id])
        activeClipId.value = res.clip.id
        closePiano()
      }
    }
  } catch (err) {
    hostError.value = err.message || 'Failed to import audio'
  } finally {
    audioImporting.value = false
    drawAll()
  }
}

function arrangementDropBeat(event) {
  const canvas = arrangementCanvas.value
  if (!canvas) return visualPositionBeats.value
  const rect = canvas.getBoundingClientRect()
  const x = event.clientX - rect.left
  if (x < 0) return visualPositionBeats.value
  return Math.max(0, x / arrangementPxPerBeat.value)
}

async function prepareAudioImport(file) {
  let durationSeconds = null
  let waveform = []
  try {
    const buffer = await decodeAudioFile(file)
    durationSeconds = buffer.duration
    waveform = waveformPeaks(buffer, 384)
  } catch {}
  return {
    file,
    durationSeconds,
    waveform,
  }
}

async function decodeAudioFile(file) {
  const AudioContextCtor = window.AudioContext || window.webkitAudioContext
  if (!AudioContextCtor) {
    throw new Error('Audio decoding is not supported by this browser')
  }
  if (!audioDecodeContext) {
    audioDecodeContext = new AudioContextCtor()
  }
  const data = await file.arrayBuffer()
  return audioDecodeContext.decodeAudioData(data.slice(0))
}

function waveformPeaks(buffer, buckets = 384) {
  const channels = Math.max(1, Math.min(2, buffer.numberOfChannels || 1))
  const channelData = Array.from({ length: channels }, (_, index) => buffer.getChannelData(index))
  const bucketCount = Math.max(32, Math.min(512, buckets))
  const peaks = []
  for (let bucket = 0; bucket < bucketCount; bucket += 1) {
    const start = Math.floor((bucket / bucketCount) * buffer.length)
    const end = Math.max(start + 1, Math.floor(((bucket + 1) / bucketCount) * buffer.length))
    let min = 1
    let max = -1
    let peak = 0
    let sumSquares = 0
    let sampleCount = 0
    for (let index = start; index < end; index += 1) {
      let mixed = 0
      for (let channel = 0; channel < channels; channel += 1) {
        const value = channelData[channel][index] || 0
        mixed += value
        peak = Math.max(peak, Math.abs(value))
      }
      mixed /= channels
      min = Math.min(min, mixed)
      max = Math.max(max, mixed)
      peak = Math.max(peak, Math.abs(mixed))
      sumSquares += mixed * mixed
      sampleCount += 1
    }
    if (!sampleCount) {
      for (let index = start; index < end; index += 1) {
        const value = channelData[0][index] || 0
        min = Math.min(min, value)
        max = Math.max(max, value)
        peak = Math.max(peak, Math.abs(value))
        sumSquares += value * value
        sampleCount += 1
      }
    }
    const rms = sampleCount ? Math.sqrt(sumSquares / sampleCount) : 0
    peaks.push({
      min: clamp(Number.isFinite(min) ? min : 0, -1, 1),
      max: clamp(Number.isFinite(max) ? max : 0, -1, 1),
      rms: clamp(rms, 0, 1),
      peak: clamp(peak, 0, 1),
    })
  }
  return peaks
}

function openFirstMidiClip() {
  const preferredTracks = activeTrack.value
    ? [activeTrack.value, ...tracks.value.filter(track => track.id !== activeTrack.value.id)]
    : tracks.value
  for (const track of preferredTracks) {
    const clip = (track.clips || []).find(item => item.type === 'midi')
    if (!clip) continue
    selectTrack(track.id)
    activeClipId.value = clip.id
    selectedClipIds.value = new Set([clip.id])
    openPiano()
    return
  }
}

async function togglePlay() {
  await transport(playing.value ? 'pause' : 'play')
}

async function stopPlayback() {
  await transport('stop')
}

async function seekToBeat(beat) {
  const nextBeat = Math.max(0, Number(beat || 0))
  const previousBeat = visualPositionBeats.value
  visualPositionBeats.value = nextBeat
  try {
    await transport('seek', { position: beatsToSeconds(project.value, nextBeat) })
  } catch {
    visualPositionBeats.value = previousBeat
  }
  drawAll()
}

async function writeMinorFigure() {
  if (!activeMidiClip.value) {
    await createClip('midi')
  }
  if (!activeMidiClip.value) return
  const notes = [60, 63, 67, 72, 70, 67, 63, 60].map((pitch, index) => ({
    pitch,
    start: index * 0.5,
    duration: 0.45,
    velocity: 82 + (index % 3) * 6,
  }))
  await persistActiveClipNotes(notes)
}

async function clearActiveTrack() {
  if (!activeMidiClip.value) return
  const clipId = activeMidiClip.value.clip.id
  await persistProjectUpdate((nextProject) => {
    for (const track of nextProject.tracks || []) {
      const clip = (track.clips || []).find(item => item.id === clipId)
      if (!clip) continue
      clip.notes = []
      clip.events = []
      clip.duration = Math.max(Number(clip.duration || 0.25), snapStep)
    }
  })
  selectedNoteIds.value = new Set()
}

async function onArrangementPointerDown(event) {
  const canvas = arrangementCanvas.value
  if (!canvas || !project.value) return
  automationMenu.value = { open: false, x: 0, y: 0, target: null, label: '' }
  closeTrackContextMenu()
  event.preventDefault()
  const rect = canvas.getBoundingClientRect()
  const x = event.clientX - rect.left
  const y = event.clientY - rect.top
  const beat = Math.max(0, x / arrangementPxPerBeat.value)
  const point = arrangementPoint(event)
  if (y <= arrangementRulerH) {
    arrangementDrag = {
      type: 'pan',
      pointerId: event.pointerId,
      startX: event.clientX,
      startScrollLeft: arrangementWrap.value?.scrollLeft || 0,
      startBeat: beat,
      moved: false,
    }
    bindArrangementDrag()
    return
  }

  const hit = hitTestArrangementClip(x, y)
  if (hit) {
    selectTrack(hit.track.id)
    if (event.ctrlKey || event.metaKey || event.shiftKey) {
      toggleClipSelection(hit.clip.id)
    } else if (!selectedClipIds.value.has(hit.clip.id)) {
      selectedClipIds.value = new Set([hit.clip.id])
    }
    activeClipId.value = hit.clip.id
    const movingIds = selectedClipIds.value.has(hit.clip.id)
      ? [...selectedClipIds.value]
      : [hit.clip.id]
    arrangementDrag = {
      type: hit.edge === 'right' ? 'resize' : 'move',
      pointerId: event.pointerId,
      startBeat: beat,
      startTrackIndex: hit.trackIndex,
      clipId: hit.clip.id,
      originals: cloneClipsByIds(movingIds),
    }
    bindArrangementDrag()
    drawAll()
    return
  }

  const index = Math.floor((y - arrangementRulerH) / arrangementTrackH)
  const track = tracks.value[index]
  if (track) {
    selectTrack(track.id)
    if (isAutomationTrack(track) && point) {
      selectedClipIds.value = new Set()
      const hit = hitTestAutomationPoint(track, point.x, point.y, point.trackIndex)
      if (hit) {
        startAutomationPointDrag(track, hit.index, event.pointerId)
        return
      }
      const curveHit = hitTestAutomationCurveHandle(track, point.x, point.y, point.trackIndex)
      if (curveHit) {
        startAutomationCurveDrag(track, curveHit, point.y, event.pointerId)
        return
      }
      selectedAutomationPoint.value = { trackId: null, index: -1 }
      startAutomationDrag(track, point, event.pointerId)
      return
    }
    selectedAutomationPoint.value = { trackId: null, index: -1 }
    if (!event.ctrlKey && !event.metaKey && !event.shiftKey) {
      selectedClipIds.value = new Set()
    }
    drawAll()
  }
}

function onArrangementDoubleClick(event) {
  const canvas = arrangementCanvas.value
  if (!canvas) return
  const rect = canvas.getBoundingClientRect()
  const hit = hitTestArrangementClip(event.clientX - rect.left, event.clientY - rect.top)
  if (!hit) return
  selectTrack(hit.track.id)
  selectedClipIds.value = new Set([hit.clip.id])
  activeClipId.value = hit.clip.id
  if (hit.clip.type === 'midi') {
    openPiano()
    selectedNoteIds.value = new Set()
  }
  drawAll()
}

function bindArrangementDrag() {
  window.addEventListener('pointermove', onArrangementPointerMove)
  window.addEventListener('pointerup', onArrangementPointerUp)
}

function unbindArrangementDrag() {
  window.removeEventListener('pointermove', onArrangementPointerMove)
  window.removeEventListener('pointerup', onArrangementPointerUp)
}

function onArrangementPointerMove(event) {
  if (!arrangementDrag || !project.value) return
  if (arrangementDrag.type === 'pan') {
    const wrap = arrangementWrap.value
    if (!wrap) return
    const deltaX = event.clientX - arrangementDrag.startX
    if (Math.abs(deltaX) > 3) arrangementDrag.moved = true
    wrap.scrollLeft = arrangementDrag.startScrollLeft - deltaX
    return
  }

  const point = arrangementPoint(event)
  if (!point) return
  const deltaBeat = snapBeat(point.beat - arrangementDrag.startBeat)

  if (arrangementDrag.type === 'resize') {
    applyDraggedClips((original) => {
      if (original.clip.id !== arrangementDrag.clipId) return original
      return {
        ...original,
        clip: {
          ...original.clip,
          duration: Math.max(snapStep, snapBeat(point.beat - original.clip.start)),
        },
      }
    })
  } else {
    const deltaTrack = clamp(
      point.trackIndex - arrangementDrag.startTrackIndex,
      -tracks.value.length,
      tracks.value.length
    )
    applyDraggedClips((original) => ({
      ...original,
      trackIndex: clamp(original.trackIndex + deltaTrack, 0, tracks.value.length - 1),
      clip: {
        ...original.clip,
        start: Math.max(0, snapBeat(original.clip.start + deltaBeat)),
      },
    }))
  }
  drawAll()
}

async function onArrangementPointerUp() {
  if (!arrangementDrag) return
  if (arrangementDrag.type === 'pan') {
    const drag = arrangementDrag
    arrangementDrag = null
    unbindArrangementDrag()
    if (!drag.moved) {
      await seekToBeat(drag.startBeat)
    }
    return
  }

  arrangementDrag = null
  unbindArrangementDrag()
  await saveProject(project.value, { broadcast: true })
  drawAll()
}

function startAutomationDrag(track, point, pointerId) {
  const value = automationValueFromY(track, point.y)
  const beat = snapAutomationBeat(point.beat)
  upsertAutomationPointAt(track, beat, value)
  automationDrag = {
    type: 'automation',
    pointerId,
    trackId: track.id,
    lastBeat: beat,
    lastValue: value,
  }
  bindAutomationDrag()
  drawAll()
}

function startAutomationPointDrag(track, pointIndex, pointerId) {
  const points = ensureAutomationTrackPoints(track)
  const point = points[pointIndex]
  if (!point) return
  selectedAutomationPoint.value = { trackId: track.id, index: pointIndex }
  automationDrag = {
    type: 'automation-point',
    pointerId,
    trackId: track.id,
    pointIndex,
  }
  bindAutomationDrag()
  drawAll()
}

function startAutomationCurveDrag(track, hit, pointerY, pointerId) {
  if (!hit?.point) return
  selectedAutomationPoint.value = { trackId: track.id, index: hit.index }
  automationDrag = {
    type: 'automation-curve',
    pointerId,
    trackId: track.id,
    pointIndex: hit.index,
    startY: pointerY,
    startCurveAmount: Number(hit.point.curve_amount || 0),
  }
  bindAutomationDrag()
  drawAll()
}

function bindAutomationDrag() {
  window.addEventListener('pointermove', onAutomationPointerMove)
  window.addEventListener('pointerup', onAutomationPointerUp)
}

function unbindAutomationDrag() {
  window.removeEventListener('pointermove', onAutomationPointerMove)
  window.removeEventListener('pointerup', onAutomationPointerUp)
}

function onAutomationPointerMove(event) {
  if (!automationDrag || !project.value) return
  const track = tracks.value.find(item => Number(item.id) === Number(automationDrag.trackId))
  const point = arrangementPoint(event)
  if (!track || !point) return
  event.preventDefault()
  if (automationDrag.type === 'automation-curve') {
    const nextCurveAmount = normalizeCurveAmount(
      automationDrag.startCurveAmount
        + ((automationDrag.startY - point.y) / Math.max(1, arrangementTrackH - 24)) * curveHandleDragScale
    )
    updateAutomationPointCurve(track, automationDrag.pointIndex, nextCurveAmount)
    drawAll()
    return
  }
  const beat = snapAutomationBeat(point.beat)
  const value = automationValueFromY(track, point.y)
  if (automationDrag.type === 'automation-point') {
    automationDrag.pointIndex = moveAutomationPoint(track, automationDrag.pointIndex, beat, value)
  } else {
    writeAutomationDragPoints(
      track,
      automationDrag.lastBeat,
      automationDrag.lastValue,
      beat,
      value
    )
    automationDrag.lastBeat = beat
    automationDrag.lastValue = value
  }
  drawAll()
}

async function onAutomationPointerUp() {
  if (!automationDrag) return
  const drag = automationDrag
  automationDrag = null
  unbindAutomationDrag()
  await persistAutomationTrackPoints(drag.trackId, automationTrackPoints(drag.trackId), {
    snapBeats: drag.type !== 'automation-curve',
  })
  drawAll()
}

function automationTrackPoints(trackId) {
  const track = tracks.value.find(item => Number(item.id) === Number(trackId))
  return ensureAutomationTrackPoints(track)
}

function ensureAutomationTrackPoints(track) {
  if (!track) return []
  if (!track.automation || typeof track.automation !== 'object') {
    track.automation = { points: [], value_min: 0, value_max: 1 }
  }
  if (!Array.isArray(track.automation.points)) track.automation.points = []
  return track.automation.points
}

function automationValueRange(track) {
  const min = Number(track?.automation?.value_min ?? 0)
  const max = Number(track?.automation?.value_max ?? 1)
  if (!Number.isFinite(min) || !Number.isFinite(max) || Math.abs(max - min) < 0.000001) {
    return { min: 0, max: 1 }
  }
  return min < max ? { min, max } : { min: max, max: min }
}

function automationValueFromY(track, y) {
  const trackIndex = tracks.value.findIndex(item => Number(item.id) === Number(track?.id))
  const top = arrangementRulerH + Math.max(0, trackIndex) * arrangementTrackH + 12
  const bodyHeight = Math.max(1, arrangementTrackH - 24)
  const unit = 1 - clamp((Number(y || 0) - top) / bodyHeight, 0, 1)
  const { min, max } = automationValueRange(track)
  return roundAutomationValue(min + unit * (max - min))
}

function automationPointY(track, point, trackIndex) {
  const { min, max } = automationValueRange(track)
  const unit = clamp((Number(point?.value ?? min) - min) / Math.max(0.0001, max - min), 0, 1)
  return arrangementRulerH + trackIndex * arrangementTrackH + 12 + (1 - unit) * (arrangementTrackH - 24)
}

function snapAutomationBeat(value) {
  return Math.max(0, snapBeatToGrid(value, activePianoSnapStep.value))
}

function roundAutomationValue(value) {
  return Math.round(Number(value || 0) * 1000000) / 1000000
}

function normalizeAutomationPoint(track, point, options = {}) {
  const { min, max } = automationValueRange(track)
  const value = clamp(roundAutomationValue(point?.value), min, max)
  const normalized = {
    beat: options.snapBeats === false
      ? Math.max(0, roundAutomationValue(point?.beat))
      : snapAutomationBeat(point?.beat),
    value,
    curve: String(point?.curve || 'linear'),
  }
  const curveAmount = normalizeCurveAmount(point?.curve_amount ?? point?.curveAmount)
  if (Math.abs(curveAmount) > 0.000001) {
    normalized.curve_amount = curveAmount
  }
  return normalized
}

function sortAutomationPoints(a, b) {
  return Number(a.beat || 0) - Number(b.beat || 0)
}

function findAutomationPointIndex(track, beat) {
  const points = ensureAutomationTrackPoints(track)
  const snapThreshold = activePianoSnapStep.value
    ? Math.max(0.001, activePianoSnapStep.value / 3)
    : Number.POSITIVE_INFINITY
  const threshold = Math.min(Math.max(0.008, 3 / arrangementPxPerBeat.value), snapThreshold)
  return points.findIndex(point => Math.abs(Number(point.beat || 0) - Number(beat || 0)) <= threshold)
}

function upsertAutomationPointAt(track, beat, value) {
  const points = ensureAutomationTrackPoints(track)
  const point = normalizeAutomationPoint(track, { beat, value })
  const index = findAutomationPointIndex(track, point.beat)
  if (index >= 0) points[index] = { ...points[index], ...point }
  else points.push(point)
  points.sort(sortAutomationPoints)
}

function moveAutomationPoint(track, pointIndex, beat, value) {
  const points = ensureAutomationTrackPoints(track)
  if (!points[pointIndex]) return pointIndex
  const point = normalizeAutomationPoint(track, { ...points[pointIndex], beat, value })
  points.splice(pointIndex, 1, point)
  points.sort(sortAutomationPoints)
  const nextIndex = points.findIndex(item => item === point)
  selectedAutomationPoint.value = { trackId: track.id, index: nextIndex >= 0 ? nextIndex : pointIndex }
  return selectedAutomationPoint.value.index
}

function updateAutomationPointCurve(track, pointIndex, curveAmount) {
  const points = ensureAutomationTrackPoints(track)
  if (!points[pointIndex]) return
  points[pointIndex] = applyCurveAmount(points[pointIndex], curveAmount)
  selectedAutomationPoint.value = { trackId: track.id, index: pointIndex }
}

function hitTestAutomationPoint(track, x, y, trackIndex) {
  const points = ensureAutomationTrackPoints(track)
  for (let index = points.length - 1; index >= 0; index -= 1) {
    const point = points[index]
    const px = Number(point.beat || 0) * arrangementPxPerBeat.value
    const py = automationPointY(track, point, trackIndex)
    if (Math.hypot(x - px, y - py) <= automationPointHitRadius) {
      return { point, index }
    }
  }
  return null
}

function hitTestAutomationCurveHandle(track, x, y, trackIndex) {
  const indexedPoints = ensureAutomationTrackPoints(track)
    .map((point, index) => ({ point, index }))
    .sort((a, b) => sortAutomationPoints(a.point, b.point))
  for (let index = indexedPoints.length - 2; index >= 0; index -= 1) {
    const left = indexedPoints[index]
    const right = indexedPoints[index + 1]
    const handle = automationCurveHandlePoint(track, left.point, right.point, trackIndex)
    if (handle && Math.hypot(x - handle.x, y - handle.y) <= automationCurveHandleHitRadius) {
      return {
        point: left.point,
        index: left.index,
        startBeat: Number(left.point.beat || 0),
        endBeat: Number(right.point.beat || 0),
      }
    }
  }
  return null
}

function writeAutomationDragPoints(track, startBeat, startValue, endBeat, endValue) {
  const beats = quantizedBeatsBetween(startBeat, endBeat, activePianoSnapStep.value)
  for (const beat of beats) {
    const value = interpolateAutomationValue(startBeat, startValue, endBeat, endValue, beat)
    upsertAutomationPointAt(track, beat, value)
  }
}

function interpolateAutomationValue(startBeat, startValue, endBeat, endValue, beat) {
  const distance = Number(endBeat || 0) - Number(startBeat || 0)
  if (Math.abs(distance) < 0.000001) return roundAutomationValue(endValue)
  const unit = (Number(beat || 0) - Number(startBeat || 0)) / distance
  return roundAutomationValue(Number(startValue || 0) + (Number(endValue || 0) - Number(startValue || 0)) * unit)
}

function automationCurveValueAtBeat(track, left, right, beat) {
  if (!left || !right) return 0
  if (String(left.curve || 'linear') === 'hold') return roundAutomationValue(left.value)
  const startBeat = Number(left.beat || 0)
  const endBeat = Number(right.beat || 0)
  const span = endBeat - startBeat
  if (span <= 0.000001) return roundAutomationValue(right.value)
  const position = clamp((Number(beat || 0) - startBeat) / span, 0, 1)
  const { min, max } = automationValueRange(track)
  const range = Math.max(0.000001, max - min)
  const startUnit = clamp((Number(left.value ?? min) - min) / range, 0, 1)
  const endUnit = clamp((Number(right.value ?? min) - min) / range, 0, 1)
  return roundAutomationValue(min + curveUnitAtPosition(
    startUnit,
    endUnit,
    position,
    left.curve_amount
  ) * range)
}

async function persistAutomationTrackPoints(trackId, points, options = {}) {
  const track = tracks.value.find(item => Number(item.id) === Number(trackId))
  const normalized = (points || [])
    .map(point => normalizeAutomationPoint(track, point, options))
    .sort(sortAutomationPoints)
  await persistProjectUpdate((nextProject) => {
    const nextTrack = findProjectTrack(nextProject, trackId)
    if (!nextTrack) return
    nextTrack.automation = {
      ...(nextTrack.automation || {}),
      points: normalized,
    }
  })
}

function syncArrangementScroll(event) {
  arrangementScrollLeft.value = Math.max(0, Number(event.currentTarget?.scrollLeft || 0))
  closeTrackContextMenu()
}

function onArrangementWheel(event) {
  const canvas = arrangementCanvas.value
  const wrap = arrangementWrap.value
  if (!canvas || !wrap) return
  const rect = canvas.getBoundingClientRect()
  const y = event.clientY - rect.top
  if (y > arrangementRulerH) {
    if (event.shiftKey && !event.ctrlKey && !event.metaKey) {
      scrollArrangementHorizontallyFromWheel(event, wrap)
    }
    if (!event.ctrlKey && !event.metaKey) return
  }

  event.preventDefault()
  const oldScale = arrangementPxPerBeat.value
  const zoom = event.deltaY < 0 ? 1.12 : 1 / 1.12
  const nextScale = clamp(oldScale * zoom, minArrangementPxPerBeat, maxArrangementPxPerBeat)
  if (nextScale === oldScale) return

  const contentX = event.clientX - rect.left
  const wrapRect = wrap.getBoundingClientRect()
  const viewportX = event.clientX - wrapRect.left
  const canvasOffsetX = arrangementCanvasOffsetX()
  const beatAtCursor = Math.max(0, contentX / oldScale)
  arrangementPxPerBeat.value = nextScale
  drawAll()
  requestAnimationFrame(() => {
    const maxScroll = Math.max(0, wrap.scrollWidth - wrap.clientWidth)
    wrap.scrollLeft = clamp(
      canvasOffsetX + (beatAtCursor * nextScale) - viewportX,
      0,
      maxScroll
    )
  })
}

function scrollArrangementHorizontallyFromWheel(event, wrap) {
  const maxScroll = Math.max(0, wrap.scrollWidth - wrap.clientWidth)
  if (maxScroll <= 0) return
  const wheelDelta = event.deltaX || event.deltaY
  if (!wheelDelta) return
  event.preventDefault()
  wrap.scrollLeft = clamp(wrap.scrollLeft + wheelDelta, 0, maxScroll)
  arrangementScrollLeft.value = wrap.scrollLeft
}

function arrangementCanvasOffsetX() {
  return arrangementCanvas.value?.offsetLeft ?? currentTrackListWidth()
}

function arrangementPoint(event) {
  const canvas = arrangementCanvas.value
  if (!canvas) return null
  const rect = canvas.getBoundingClientRect()
  const x = event.clientX - rect.left
  const y = event.clientY - rect.top
  const beat = Math.max(0, (x) / arrangementPxPerBeat.value)
  const trackIndex = clamp(
    Math.floor((y - arrangementRulerH) / arrangementTrackH),
    0,
    Math.max(0, tracks.value.length - 1)
  )
  return { x, y, beat, trackIndex }
}

function hitTestArrangementClip(x, y) {
  if (y <= arrangementRulerH) return null
  const trackIndex = Math.floor((y - arrangementRulerH) / arrangementTrackH)
  const track = tracks.value[trackIndex]
  if (!track) return null
  const clips = [...(track.clips || [])].reverse()
  for (const clip of clips) {
    const rect = clipRect(clip, trackIndex)
    if (x >= rect.x && x <= rect.x + rect.w && y >= rect.y && y <= rect.y + rect.h) {
      return {
        track,
        trackIndex,
        clip,
        edge: x >= rect.x + rect.w - 8 ? 'right' : 'body',
      }
    }
  }
  return null
}

function clipRect(clip, trackIndex) {
  const scale = arrangementPxPerBeat.value
  return {
    x: Number(clip.start || 0) * scale + 2,
    y: arrangementRulerH + trackIndex * arrangementTrackH + 10,
    w: Math.max(18, Number(clip.duration || 0.25) * scale - 4),
    h: arrangementTrackH - 20,
  }
}

function cloneClipsByIds(ids) {
  const idSet = new Set(ids)
  return tracks.value.flatMap((track, trackIndex) => (
    (track.clips || [])
      .filter(clip => idSet.has(clip.id))
      .map(clip => ({
        trackId: track.id,
        trackIndex,
        clip: {
          ...clip,
          notes: cloneNotes(clip.notes),
          events: cloneEvents(clip.events),
        },
      }))
  ))
}

function applyDraggedClips(mapper) {
  if (!arrangementDrag || !project.value) return
  const nextRecords = arrangementDrag.originals.map(mapper)
  const movedIds = new Set(arrangementDrag.originals.map(record => record.clip.id))
  for (const track of tracks.value) {
    track.clips = (track.clips || []).filter(clip => !movedIds.has(clip.id))
  }
  for (const record of nextRecords) {
    const track = tracks.value[record.trackIndex]
    if (!track) continue
    track.clips = [...(track.clips || []), record.clip].sort(sortClips)
  }
}

function sortClips(a, b) {
  return Number(a.start || 0) - Number(b.start || 0)
    || String(a.type).localeCompare(String(b.type))
    || String(a.name).localeCompare(String(b.name))
}

function cloneNotes(notes = []) {
  return (notes || []).map(note => ({ ...note }))
}

function cloneEvents(events = []) {
  return (events || []).map(event => ({ ...event }))
}

function toggleClipSelection(clipId) {
  const next = new Set(selectedClipIds.value)
  if (next.has(clipId)) next.delete(clipId)
  else next.add(clipId)
  selectedClipIds.value = next
}

function copySelectedClips() {
  const records = cloneClipsByIds([...selectedClipIds.value])
  if (!records.length) return
  const baseStart = Math.min(...records.map(record => Number(record.clip.start || 0)))
  clipClipboard.value = records.map(record => ({
    trackId: record.trackId,
    startOffset: Number(record.clip.start || 0) - baseStart,
    clip: {
      ...record.clip,
      notes: cloneNotes(record.clip.notes),
      events: cloneEvents(record.clip.events),
    },
  }))
}

async function pasteClips() {
  if (!clipClipboard.value.length || !activeTrack.value) return
  const pasteStart = snapBeat(Math.max(0, visualPositionBeats.value))
  const pastedIds = []
  await persistProjectUpdate((nextProject) => {
    for (const item of clipClipboard.value) {
      const track = findProjectTrack(nextProject, item.trackId)
        || findProjectTrack(nextProject, activeTrack.value.id)
      if (!track) continue
      const clip = {
        ...item.clip,
        id: makeClipId(),
        start: pasteStart + item.startOffset,
        notes: cloneNotes(item.clip.notes),
        events: cloneEvents(item.clip.events),
      }
      pastedIds.push(clip.id)
      track.clips = [...(track.clips || []), clip].sort(sortClips)
    }
  })
  selectedClipIds.value = new Set(pastedIds)
  const first = findClipRecord(pastedIds[0])
  if (first) {
    activeClipId.value = first.clip.id
    selectTrack(first.track.id)
    if (first.clip.type === 'midi') openPiano()
    else closePiano()
  }
}

async function deleteSelectedClips() {
  if (!selectedClipIds.value.size) return
  const deleting = new Set(selectedClipIds.value)
  await persistProjectUpdate((nextProject) => {
    for (const track of nextProject.tracks || []) {
      track.clips = (track.clips || []).filter(clip => !deleting.has(clip.id))
    }
  })
  if (deleting.has(activeClipId.value)) {
    activeClipId.value = null
    closePiano()
    selectedNoteIds.value = new Set()
  }
  selectedClipIds.value = new Set()
}

async function onPianoPointerDown(event) {
  if (!activeMidiClip.value) return
  const canvas = pianoCanvas.value
  if (!canvas) return
  event.preventDefault()
  const point = pianoPoint(event)
  if (!point || point.x < pianoKeyW) return
  closePianoMeterEditor()
  closePianoHarmonyEditor()
  if (point.ruler) {
    pianoDrag = {
      type: 'pan',
      pointerId: event.pointerId,
      startX: event.clientX,
      startScrollLeft: pianoWrap.value?.scrollLeft || 0,
      startBeat: point.beat,
      moved: false,
    }
    bindPianoDrag()
    return
  }
  if (point.meterLane) {
    const meterHit = hitTestMeterEvent(point)
    if (meterHit && isMeterEventLabelHit(point, meterHit)) {
      openPianoMeterEditor(event, meterHit)
      return
    }
    if (pianoTool.value === 'draw' && meterHit) {
      startDrawMeterEventPress(event, point, meterHit)
      return
    }
    if (meterHit) {
      openPianoMeterEditor(event, meterHit)
      return
    }
    if (pianoTool.value === 'draw') {
      await upsertMeterEventAtBeat(point.beat)
    }
    return
  }
  if (point.harmonyLane) {
    const harmonyHit = hitTestHarmonyEvent(point)
    openPianoHarmonyEditor(event, harmonyHit || {
      event: { beat: point.beat, text: '' },
      index: -1,
    })
    return
  }
  const hit = hitTestPianoNote(point.x, point.y)

  if (hit) {
    if (pianoTool.value === 'draw') {
      startDrawNotePress(event, point, hit)
      return
    }
    const noteId = hit.note.id
    if (event.ctrlKey || event.metaKey || event.shiftKey) {
      toggleNoteSelection(noteId)
    } else if (!selectedNoteIds.value.has(noteId)) {
      selectedNoteIds.value = new Set([noteId])
    }
    const movingIds = selectedNoteIds.value.has(noteId)
      ? [...selectedNoteIds.value]
      : [noteId]
    pianoDrag = {
      type: hit.edge === 'right' ? 'resize' : 'move',
      pointerId: event.pointerId,
      startBeat: point.beat,
      startPitch: point.pitch,
      noteId,
      noteStart: hit.note.start,
      originals: cloneNotesByIds(movingIds),
    }
    bindPianoDrag()
    drawAll()
    return
  }

  if (pianoTool.value === 'draw') {
    const start = snapPianoBeat(point.beat)
    const note = {
      id: makeNoteId(),
      pitch: point.pitch,
      start,
      duration: activeNoteStep.value,
      velocity: DEFAULT_NOTE_VELOCITY,
    }
    draftNote.value = note
    pianoDrag = {
      type: 'draw',
      pointerId: event.pointerId,
      startBeat: note.start,
      pitch: note.pitch,
    }
  } else {
    if (!event.ctrlKey && !event.metaKey && !event.shiftKey) {
      selectedNoteIds.value = new Set()
    }
    selectionBox.value = {
      x1: point.x,
      y1: point.y,
      x2: point.x,
      y2: point.y,
      append: event.ctrlKey || event.metaKey || event.shiftKey,
    }
    pianoDrag = {
      type: 'select',
      pointerId: event.pointerId,
    }
  }
  bindPianoDrag()
  drawAll()
}

function bindPianoDrag() {
  window.addEventListener('pointermove', onPianoPointerMove)
  window.addEventListener('pointerup', onPianoPointerUp)
  window.addEventListener('pointercancel', onPianoPointerCancel)
}

function unbindPianoDrag() {
  window.removeEventListener('pointermove', onPianoPointerMove)
  window.removeEventListener('pointerup', onPianoPointerUp)
  window.removeEventListener('pointercancel', onPianoPointerCancel)
}

function schedulePianoLongPress(callback) {
  clearPianoLongPressTimer()
  pianoLongPressTimer = setTimeout(() => {
    pianoLongPressTimer = null
    callback()
  }, pianoDrawLongPressMs)
}

function clearPianoLongPressTimer() {
  if (!pianoLongPressTimer) return
  clearTimeout(pianoLongPressTimer)
  pianoLongPressTimer = null
}

function drawPressMoved(event, drag) {
  return Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) > pianoDrawMoveTolerancePx
}

function currentPianoPanelHeight() {
  const panelHeight = pianoPanel.value?.getBoundingClientRect?.().height
  if (panelHeight) return clampPianoPanelHeight(panelHeight)
  const stackHeight = editorStack.value?.clientHeight || 0
  return clampPianoPanelHeight(stackHeight * 0.42)
}

function clampPianoPanelHeight(height) {
  const stackHeight = editorStack.value?.clientHeight || 0
  if (!stackHeight) {
    return Math.round(Math.max(minPianoPanelHeight, Number(height || 0)))
  }
  const maxHeight = Math.max(1, stackHeight - minArrangementPanelHeight)
  const minHeight = Math.min(minPianoPanelHeight, maxHeight)
  return Math.round(clamp(Number(height || minHeight), minHeight, maxHeight))
}

function clampTrackListWidth(width) {
  return Math.round(clamp(Number(width || defaultTrackListWidth), minTrackListWidth, maxTrackListWidth))
}

function currentTrackListWidth() {
  return clampTrackListWidth(trackListWidth.value)
}

function startTrackListResize(event) {
  event.preventDefault()
  event.stopPropagation()
  const startWidth = currentTrackListWidth()
  trackListWidth.value = startWidth
  trackListResizeDrag = {
    pointerId: event.pointerId,
    startX: event.clientX,
    startWidth,
  }
  bindTrackListResize()
}

function bindTrackListResize() {
  window.addEventListener('pointermove', onTrackListResizeMove)
  window.addEventListener('pointerup', onTrackListResizeEnd)
  window.addEventListener('pointercancel', onTrackListResizeEnd)
}

function unbindTrackListResize() {
  window.removeEventListener('pointermove', onTrackListResizeMove)
  window.removeEventListener('pointerup', onTrackListResizeEnd)
  window.removeEventListener('pointercancel', onTrackListResizeEnd)
}

function onTrackListResizeMove(event) {
  if (!trackListResizeDrag) return
  event.preventDefault()
  const deltaX = event.clientX - trackListResizeDrag.startX
  trackListWidth.value = clampTrackListWidth(trackListResizeDrag.startWidth + deltaX)
  drawAll()
}

function onTrackListResizeEnd() {
  if (!trackListResizeDrag) return
  trackListResizeDrag = null
  unbindTrackListResize()
  nextTick(drawAll)
}

function startPianoResize(event) {
  event.preventDefault()
  event.stopPropagation()
  const startHeight = currentPianoPanelHeight()
  pianoPanelHeight.value = startHeight
  pianoResizeDrag = {
    pointerId: event.pointerId,
    startY: event.clientY,
    startHeight,
  }
  bindPianoResize()
}

function bindPianoResize() {
  window.addEventListener('pointermove', onPianoResizeMove)
  window.addEventListener('pointerup', onPianoResizeEnd)
}

function unbindPianoResize() {
  window.removeEventListener('pointermove', onPianoResizeMove)
  window.removeEventListener('pointerup', onPianoResizeEnd)
}

function onPianoResizeMove(event) {
  if (!pianoResizeDrag) return
  event.preventDefault()
  const deltaY = event.clientY - pianoResizeDrag.startY
  pianoPanelHeight.value = clampPianoPanelHeight(pianoResizeDrag.startHeight - deltaY)
  drawAll()
}

function onPianoResizeEnd() {
  if (!pianoResizeDrag) return
  pianoResizeDrag = null
  unbindPianoResize()
  nextTick(drawAll)
}

function startDrawNotePress(event, point, hit) {
  const noteId = hit.note.id
  const movingIds = selectedNoteIds.value.has(noteId) ? [...selectedNoteIds.value] : [noteId]
  pianoDrag = {
    type: 'draw-note-press',
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    startBeat: point.beat,
    startPitch: point.pitch,
    noteId,
    noteStart: hit.note.start,
    editType: hit.edge === 'right' ? 'resize' : 'move',
    movingIds,
    originals: cloneNotesByIds(movingIds),
    moved: false,
    lastPoint: null,
  }
  schedulePianoLongPress(() => activateDrawNotePressDrag())
  bindPianoDrag()
  drawAll()
}

function activateDrawNotePressDrag() {
  if (!pianoDrag || pianoDrag.type !== 'draw-note-press') return
  const drag = pianoDrag
  selectedNoteIds.value = new Set(drag.movingIds)
  pianoDrag = {
    type: drag.editType,
    pointerId: drag.pointerId,
    startBeat: drag.startBeat,
    startPitch: drag.startPitch,
    noteId: drag.noteId,
    noteStart: drag.noteStart,
    originals: drag.originals,
  }
  if (drag.lastPoint) applyPianoDragAtPoint(pianoDrag, drag.lastPoint)
  drawAll()
}

function startDrawMeterEventPress(event, point, meterHit) {
  const originalEvents = editableMeterEvents()
  pianoDrag = {
    type: 'draw-meter-event-press',
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    startBeat: point.beat,
    eventIndex: meterHit.index,
    originalEvent: { ...meterHit.event },
    originalEvents,
    moved: false,
    lastPoint: null,
  }
  schedulePianoLongPress(() => activateDrawMeterEventPressDrag())
  bindPianoDrag()
  drawAll()
}

function activateDrawMeterEventPressDrag() {
  if (!pianoDrag || pianoDrag.type !== 'draw-meter-event-press') return
  const drag = pianoDrag
  pianoDrag = {
    type: 'meter-event-move',
    pointerId: drag.pointerId,
    startBeat: drag.startBeat,
    eventIndex: drag.eventIndex,
    originalEvent: drag.originalEvent,
    originalEvents: drag.originalEvents,
  }
  if (drag.lastPoint) applyPianoDragAtPoint(pianoDrag, drag.lastPoint)
  drawAll()
}

function onPianoPointerMove(event) {
  if (!pianoDrag) return
  if (pianoDrag.type === 'pan') {
    const wrap = pianoWrap.value
    if (!wrap) return
    event.preventDefault()
    const deltaX = event.clientX - pianoDrag.startX
    if (Math.abs(deltaX) > 3) pianoDrag.moved = true
    wrap.scrollLeft = pianoDrag.startScrollLeft - deltaX
    return
  }

  const point = pianoPoint(event)
  if (!point) return

  if (pianoDrag.type === 'draw-note-press' || pianoDrag.type === 'draw-meter-event-press') {
    pianoDrag.lastPoint = point
    if (drawPressMoved(event, pianoDrag)) pianoDrag.moved = true
    return
  }

  applyPianoDragAtPoint(pianoDrag, point)
  drawAll()
}

function applyPianoDragAtPoint(drag, point) {
  if (drag.type === 'draw' && draftNote.value) {
    const end = Math.max(drag.startBeat + activeNoteStep.value, snapPianoBeat(point.beat))
    draftNote.value = {
      ...draftNote.value,
      pitch: point.pitch,
      duration: Math.max(activeNoteStep.value, end - drag.startBeat),
    }
  } else if (drag.type === 'select' && selectionBox.value) {
    selectionBox.value = {
      ...selectionBox.value,
      x2: point.x,
      y2: point.y,
    }
  } else if (drag.type === 'move') {
    const deltaBeat = snapPianoBeatDelta(point.beat - drag.startBeat)
    const deltaPitch = point.pitch - drag.startPitch
    applyDraggedNotes((note) => ({
      ...note,
      start: snapPianoBeat(Math.max(0, note.start + deltaBeat)),
      pitch: clamp(note.pitch + deltaPitch, minPitch, maxPitch),
    }))
  } else if (drag.type === 'resize') {
    applyDraggedNotes((note) => {
      if (note.id !== drag.noteId) return note
      const duration = snapPianoDuration(point.beat - drag.noteStart)
      return {
        ...note,
        duration,
      }
    })
  } else if (drag.type === 'meter-event-move') {
    moveMeterEventDrag(drag, point)
  }
}

async function onPianoPointerUp() {
  if (!pianoDrag || !activeMidiClip.value) return
  const drag = pianoDrag
  pianoDrag = null
  clearPianoLongPressTimer()
  unbindPianoDrag()

  if (drag.type === 'pan') {
    if (!drag.moved) {
      await seekToBeat(Number(activeMidiClip.value.clip.start || 0) + drag.startBeat)
    }
  } else if (drag.type === 'draw' && draftNote.value) {
    const note = { ...draftNote.value }
    draftNote.value = null
    selectedNoteIds.value = new Set([note.id])
    await persistActiveClipNotes([...activeMidiClip.value.clip.notes, note])
  } else if (drag.type === 'select' && selectionBox.value) {
    const ids = notesInSelection(selectionBox.value)
    selectedNoteIds.value = selectionBox.value.append
      ? new Set([...selectedNoteIds.value, ...ids])
      : new Set(ids)
    selectionBox.value = null
  } else if (drag.type === 'move' || drag.type === 'resize') {
    await persistActiveClipNotes(activeMidiClip.value.clip.notes)
  } else if (drag.type === 'draw-note-press') {
    if (!drag.moved) await deletePianoNoteById(drag.noteId)
  } else if (drag.type === 'draw-meter-event-press') {
    if (!drag.moved) await deleteMeterEventAtIndex(drag.eventIndex)
  } else if (drag.type === 'meter-event-move') {
    await persistMeterEvents(project.value.meter_events || [])
  }
  drawAll()
}

function onPianoPointerCancel() {
  if (!pianoDrag) return
  pianoDrag = null
  draftNote.value = null
  selectionBox.value = null
  clearPianoLongPressTimer()
  unbindPianoDrag()
  drawAll()
}

function onPianoWheel(event) {
  const canvas = pianoCanvas.value
  const wrap = pianoWrap.value
  if (!canvas || !wrap) return
  const rect = canvas.getBoundingClientRect()
  const x = event.clientX - rect.left
  const y = event.clientY - rect.top
  if (y > pianoRulerH || x < pianoKeyW) return

  event.preventDefault()
  const oldScale = pianoPxPerBeat.value
  const zoom = event.deltaY < 0 ? 1.12 : 1 / 1.12
  const nextScale = clamp(oldScale * zoom, minPianoPxPerBeat, maxPianoPxPerBeat)
  if (nextScale === oldScale) return

  const beatAtCursor = Math.max(0, (x - pianoKeyW) / oldScale)
  const wrapRect = wrap.getBoundingClientRect()
  const viewportX = event.clientX - wrapRect.left
  pianoPxPerBeat.value = nextScale
  drawAll()
  requestAnimationFrame(() => {
    const maxScroll = Math.max(0, wrap.scrollWidth - wrap.clientWidth)
    wrap.scrollLeft = clamp((pianoKeyW + beatAtCursor * nextScale) - viewportX, 0, maxScroll)
  })
}

function pianoPoint(event) {
  const canvas = pianoCanvas.value
  if (!canvas) return null
  const rect = canvas.getBoundingClientRect()
  const x = event.clientX - rect.left
  const y = event.clientY - rect.top
  const localBeat = Math.max(0, (x - pianoKeyW) / pianoPxPerBeat.value)
  const ruler = y < pianoRulerH
  const meterLane = pianoMeterLaneVisible.value
    && y >= pianoMeterLaneTop.value
    && y < pianoMeterLaneTop.value + pianoSubtrackH
  const harmonyLane = pianoHarmonyLaneVisible.value
    && y >= pianoHarmonyLaneTop.value
    && y < pianoHarmonyLaneTop.value + pianoSubtrackH
  const clipStart = Number(activeMidiClip.value?.clip?.start || 0)
  const beat = meterLane || harmonyLane ? clipStart + localBeat : localBeat
  const row = Math.floor((y - pianoNoteTop.value) / pianoRowH)
  const pitch = clamp(maxPitch - row, minPitch, maxPitch)
  return { x, y, beat, localBeat, pitch, ruler, meterLane, harmonyLane }
}

function hitTestPianoNote(x, y) {
  const notes = [...(activeMidiClip.value?.clip.notes || [])].reverse()
  for (const note of notes) {
    if (note.pitch < minPitch || note.pitch > maxPitch) continue
    const rect = noteRect(note)
    if (x >= rect.x && x <= rect.x + rect.w && y >= rect.y && y <= rect.y + rect.h) {
      return {
        note,
        edge: x >= rect.x + rect.w - 7 ? 'right' : 'body',
      }
    }
  }
  return null
}

function hitTestMeterEvent(point) {
  if (!point?.meterLane) return null
  const clipStart = Number(activeMidiClip.value?.clip?.start || 0)
  const events = editableMeterEvents()
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    const x = pianoKeyW + (Number(event.beat || 0) - clipStart) * pianoPxPerBeat.value
    const label = `${event.numerator}/${event.denominator}`
    const labelLeft = x + 4
    const labelRight = labelLeft + 8 + label.length * 7
    const markerHit = Math.abs(point.x - x) <= pianoMeterEventHitRadius
    const labelHit = point.x >= labelLeft - 2 && point.x <= labelRight
    if (markerHit || labelHit) {
      return { event, index }
    }
  }
  return null
}

function isMeterEventLabelHit(point, meterHit) {
  if (!point?.meterLane || !meterHit?.event) return false
  const clipStart = Number(activeMidiClip.value?.clip?.start || 0)
  const x = pianoKeyW + (Number(meterHit.event.beat || 0) - clipStart) * pianoPxPerBeat.value
  const label = `${meterHit.event.numerator}/${meterHit.event.denominator}`
  const left = x + 4
  const right = left + 8 + label.length * 7
  return point.x >= left - 2
    && point.x <= right
    && point.y >= pianoMeterLaneTop.value
    && point.y <= pianoMeterLaneTop.value + pianoMeterLaneH
}

function hitTestHarmonyEvent(point) {
  if (!point?.harmonyLane) return null
  const clipStart = Number(activeMidiClip.value?.clip?.start || 0)
  const events = editableHarmonyEvents()
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    const x = pianoKeyW + (Number(event.beat || 0) - clipStart) * pianoPxPerBeat.value
    const label = String(event.text || '')
    const labelLeft = x + 5
    const labelRight = labelLeft + 10 + label.length * 7
    const markerHit = Math.abs(point.x - x) <= pianoHarmonyEventHitRadius
    const labelHit = point.x >= labelLeft - 2 && point.x <= labelRight
    if (markerHit || labelHit) {
      return { event, index }
    }
  }
  return null
}

function openPianoMeterEditor(event, meterHit) {
  event.preventDefault()
  event.stopPropagation()
  const workspaceRect = pianoWorkspace.value?.getBoundingClientRect?.()
  const x = workspaceRect
    ? clamp(event.clientX - workspaceRect.left + 8, 8, Math.max(8, workspaceRect.width - 174))
    : 8
  const y = workspaceRect
    ? clamp(event.clientY - workspaceRect.top + 8, 8, Math.max(8, workspaceRect.height - 92))
    : pianoRulerH + 8
  pianoMeterEditor.value = {
    open: true,
    x,
    y,
    eventIndex: meterHit.index,
    beat: Number(meterHit.event.beat || 0),
    numerator: normalizeTimeSignatureNumerator(meterHit.event.numerator),
    denominator: normalizeTimeSignatureDenominator(meterHit.event.denominator),
  }
}

function openPianoHarmonyEditor(event, harmonyHit) {
  event.preventDefault()
  event.stopPropagation()
  const workspaceRect = pianoWorkspace.value?.getBoundingClientRect?.()
  const x = workspaceRect
    ? clamp(event.clientX - workspaceRect.left + 8, 8, Math.max(8, workspaceRect.width - 218))
    : 8
  const y = workspaceRect
    ? clamp(event.clientY - workspaceRect.top + 8, 8, Math.max(8, workspaceRect.height - 54))
    : pianoHarmonyLaneTop.value + 8
  pianoHarmonyEditor.value = {
    open: true,
    x,
    y,
    eventIndex: harmonyHit?.index ?? -1,
    beat: Number(harmonyHit?.event?.beat ?? 0),
    text: String(harmonyHit?.event?.text || ''),
  }
}

function closePianoMeterEditor() {
  if (!pianoMeterEditor.value.open) return
  pianoMeterEditor.value = {
    ...pianoMeterEditor.value,
    open: false,
  }
}

function closePianoHarmonyEditor() {
  if (!pianoHarmonyEditor.value.open) return
  pianoHarmonyEditor.value = {
    ...pianoHarmonyEditor.value,
    open: false,
  }
}

async function applyPianoMeterEditor() {
  const editor = pianoMeterEditor.value
  if (!editor.open || !project.value) return
  const numerator = normalizeTimeSignatureNumerator(editor.numerator)
  const denominator = normalizeTimeSignatureDenominator(editor.denominator)
  pianoMeterEditor.value = {
    ...editor,
    numerator,
    denominator,
  }
  await persistProjectUpdate((nextProject) => {
    if (isAtTimelineStart(editor.beat)) {
      nextProject.time_signature = [numerator, denominator]
    }
    const events = normalizeEditableMeterEvents(nextProject.meter_events || [])
    if (events[editor.eventIndex]) {
      events[editor.eventIndex] = {
        ...events[editor.eventIndex],
        numerator,
        denominator,
      }
      nextProject.meter_events = normalizeEditableMeterEvents(events)
    } else {
      upsertMeterEventInProject(nextProject, editor.beat, numerator, denominator)
    }
  })
}

async function applyPianoHarmonyEditor() {
  const editor = pianoHarmonyEditor.value
  if (!editor.open || !project.value) return
  const text = normalizeHarmonyText(editor.text)
  pianoHarmonyEditor.value = {
    ...editor,
    text,
  }
  if (!text && editor.eventIndex < 0) {
    closePianoHarmonyEditor()
    return
  }
  await persistProjectUpdate((nextProject) => {
    const events = normalizeEditableHarmonyEvents(nextProject.harmony_events || [])
    if (events[editor.eventIndex]) {
      if (text) {
        events[editor.eventIndex] = {
          ...events[editor.eventIndex],
          text,
        }
      } else {
        events.splice(editor.eventIndex, 1)
      }
    } else if (text) {
      events.push({
        beat: editor.beat,
        text,
      })
    }
    nextProject.harmony_events = normalizeEditableHarmonyEvents(events)
  })
  if (!text) closePianoHarmonyEditor()
}

function noteRect(note) {
  const scale = pianoPxPerBeat.value
  return {
    x: pianoKeyW + Number(note.start) * scale,
    y: pianoNoteTop.value + (maxPitch - Number(note.pitch)) * pianoRowH + 1,
    w: Math.max(8, Number(note.duration) * scale),
    h: pianoRowH - 2,
  }
}

function editableMeterEvents() {
  return normalizeEditableMeterEvents(project.value?.meter_events || [])
}

function editableHarmonyEvents() {
  return normalizeEditableHarmonyEvents(project.value?.harmony_events || [])
}

function normalizeEditableMeterEvents(events) {
  const byBeat = new Map()
  for (const rawEvent of Array.isArray(events) ? events : []) {
    const beat = Math.max(0, roundPianoBeat(rawEvent?.beat ?? rawEvent?.start ?? 0))
    byBeat.set(beat, {
      beat,
      numerator: normalizeTimeSignatureNumerator(rawEvent?.numerator),
      denominator: normalizeTimeSignatureDenominator(rawEvent?.denominator),
    })
  }
  return [...byBeat.values()].sort((a, b) => a.beat - b.beat)
}

function normalizeEditableHarmonyEvents(events) {
  const byBeat = new Map()
  for (const rawEvent of Array.isArray(events) ? events : []) {
    const text = normalizeHarmonyText(rawEvent?.text ?? rawEvent?.label ?? rawEvent?.chord)
    if (!text) continue
    const beat = Math.max(0, roundPianoBeat(rawEvent?.beat ?? rawEvent?.start ?? 0))
    byBeat.set(beat, { beat, text })
  }
  return [...byBeat.values()].sort((a, b) => a.beat - b.beat)
}

function normalizeHarmonyText(value) {
  return String(value || '').trim().slice(0, 64)
}

function roundPianoBeat(value) {
  return Math.round(Number(value || 0) * 1000000) / 1000000
}

function moveMeterEventDrag(drag, point) {
  if (!project.value) return
  const deltaBeat = snapPianoBeatDelta(point.beat - drag.startBeat)
  const nextBeat = Math.max(0, snapBeatToGrid(Number(drag.originalEvent.beat || 0) + deltaBeat, activePianoSnapStep.value))
  const nextEvents = drag.originalEvents.map((event, index) => (
    index === drag.eventIndex ? { ...event, beat: nextBeat } : { ...event }
  ))
  project.value.meter_events = normalizeEditableMeterEvents(nextEvents)
}

function cloneNotesByIds(ids) {
  const idSet = new Set(ids)
  return (activeMidiClip.value?.clip.notes || [])
    .filter(note => idSet.has(note.id))
    .map(note => ({ ...note }))
}

function applyDraggedNotes(mapper) {
  if (!activeMidiClip.value || !pianoDrag) return
  const originals = new Map(pianoDrag.originals.map(note => [note.id, note]))
  activeMidiClip.value.clip.notes = activeMidiClip.value.clip.notes
    .map(note => originals.has(note.id) ? mapper({ ...originals.get(note.id) }) : note)
    .sort(sortNotes)
}

async function persistActiveClipNotes(notes) {
  if (!activeMidiClip.value) return
  const clipId = activeMidiClip.value.clip.id
  const normalized = notes.map(normalizeClientNote).sort(sortNotes)
  await persistProjectUpdate((nextProject) => {
    for (const track of nextProject.tracks || []) {
      const clip = (track.clips || []).find(item => item.id === clipId)
      if (!clip) continue
      clip.notes = normalized
      const noteEnd = Math.max(
        0,
        ...normalized.map(note => Number(note.start || 0) + Number(note.duration || 0))
      )
      clip.duration = Math.max(Number(clip.duration || 0.25), noteEnd, activeNoteStep.value)
    }
  })
}

function normalizeClientNote(note) {
  return {
    id: note.id || makeNoteId(),
    pitch: clamp(Math.round(Number(note.pitch || 60)), 0, 127),
    start: Math.max(0, snapBeatToGrid(Number(note.start || 0), null)),
    duration: Math.max(minFreehandStep, snapBeatToGrid(Number(note.duration || minFreehandStep), null)),
    velocity: clamp(Math.round(Number(note.velocity || DEFAULT_NOTE_VELOCITY)), 1, 127),
  }
}

function sortNotes(a, b) {
  return a.start - b.start || a.pitch - b.pitch || a.duration - b.duration
}

function notesInSelection(box) {
  const x1 = Math.min(box.x1, box.x2)
  const x2 = Math.max(box.x1, box.x2)
  const y1 = Math.min(box.y1, box.y2)
  const y2 = Math.max(box.y1, box.y2)
  return (activeMidiClip.value?.clip.notes || [])
    .filter((note) => {
      const rect = noteRect(note)
      return rect.x < x2 && rect.x + rect.w > x1 && rect.y < y2 && rect.y + rect.h > y1
    })
    .map(note => note.id)
}

function toggleNoteSelection(noteId) {
  const next = new Set(selectedNoteIds.value)
  if (next.has(noteId)) next.delete(noteId)
  else next.add(noteId)
  selectedNoteIds.value = next
}

function copySelectedNotes() {
  const selected = (activeMidiClip.value?.clip.notes || [])
    .filter(note => selectedNoteIds.value.has(note.id))
    .map(note => ({ ...note }))
  if (!selected.length) return
  const baseStart = Math.min(...selected.map(note => note.start))
  noteClipboard.value = selected.map(note => ({
    ...note,
    start: note.start - baseStart,
  }))
}

async function pasteNotes() {
  if (!activeMidiClip.value || !noteClipboard.value.length) return
  const pasteStart = snapPianoBeat(Math.max(0, visualPositionBeats.value))
  const clipStart = Number(activeMidiClip.value.clip.start || 0)
  const pasted = noteClipboard.value.map(note => ({
    ...note,
    id: makeNoteId(),
    start: Math.max(0, pasteStart - clipStart + note.start),
  }))
  selectedNoteIds.value = new Set(pasted.map(note => note.id))
  await persistActiveClipNotes([...activeMidiClip.value.clip.notes, ...pasted])
}

async function deleteSelectedNotes() {
  if (!activeMidiClip.value || selectedNoteIds.value.size === 0) return
  const selected = selectedNoteIds.value
  const remaining = activeMidiClip.value.clip.notes.filter(note => !selected.has(note.id))
  selectedNoteIds.value = new Set()
  await persistActiveClipNotes(remaining)
}

async function deletePianoNoteById(noteId) {
  if (!activeMidiClip.value) return
  const remaining = activeMidiClip.value.clip.notes.filter(note => note.id !== noteId)
  const nextSelection = new Set(selectedNoteIds.value)
  nextSelection.delete(noteId)
  selectedNoteIds.value = nextSelection
  await persistActiveClipNotes(remaining)
}

function setControllerLaneCanvas(laneId, el) {
  if (el) controllerLaneCanvases.set(laneId, el)
  else controllerLaneCanvases.delete(laneId)
}

function syncPianoScroll(source) {
  if (syncingPianoScroll) return
  const from = source === 'piano' ? pianoWrap.value : controllerWrap.value
  const to = source === 'piano' ? controllerWrap.value : pianoWrap.value
  if (!from || !to) return
  controllerScrollLeft.value = source === 'controller' ? from.scrollLeft : to.scrollLeft
  if (to.scrollLeft === from.scrollLeft) return
  syncingPianoScroll = true
  to.scrollLeft = from.scrollLeft
  controllerScrollLeft.value = source === 'controller' ? from.scrollLeft : to.scrollLeft
  requestAnimationFrame(() => {
    syncingPianoScroll = false
  })
}

function controllerDefinitionForLane(lane) {
  return controllerDefinitionFromId(lane?.activeControllerId)
}

function controllerLabel(controllerId) {
  return controllerDefinitionFromId(controllerId).label
}

function controllerAxisTop(lane) {
  const definition = controllerDefinitionForLane(lane)
  return String(controllerDisplayRange(definition).max)
}

function controllerAxisMiddle(lane) {
  const definition = controllerDefinitionForLane(lane)
  return String(controllerDisplayRange(definition).middle)
}

function controllerAxisBottom(lane) {
  const definition = controllerDefinitionForLane(lane)
  return String(controllerDisplayRange(definition).min)
}

function controllerMenuOptions(lane) {
  const existing = new Set(lane.controllerIds || [])
  return CONTROLLER_PRESETS.filter(preset => !existing.has(preset.id))
}

function toggleControllerMenu(laneId) {
  controllerMenuLaneId.value = controllerMenuLaneId.value === laneId ? null : laneId
  customControllerNumber.value = ''
}

function setLaneController(laneId, controllerId) {
  controllerLanes.value = controllerLanes.value.map((lane) => {
    if (lane.id !== laneId) return lane
    const controllerIds = lane.controllerIds.includes(controllerId)
      ? lane.controllerIds
      : [...lane.controllerIds, controllerId]
    return {
      ...lane,
      activeControllerId: controllerId,
      controllerIds,
    }
  })
  controllerMenuLaneId.value = null
  nextTick(drawAll)
}

function addControllerLane() {
  controllerLanes.value = [
    ...controllerLanes.value,
    {
      id: makeControllerLaneId(),
      activeControllerId: 'cc:1',
      controllerIds: [...DEFAULT_CONTROLLER_IDS],
    },
  ]
  nextTick(drawAll)
}

function removeControllerLane(laneId) {
  if (controllerLanes.value.length <= 1) return
  controllerLanes.value = controllerLanes.value.filter(lane => lane.id !== laneId)
  if (controllerMenuLaneId.value === laneId) controllerMenuLaneId.value = null
  nextTick(drawAll)
}

function addControllerToLane(laneId, controllerId) {
  const definition = controllerDefinitionFromId(controllerId)
  setLaneController(laneId, definition.id)
}

function addCustomControllerToLane(laneId) {
  const controller = Number(customControllerNumber.value)
  if (!Number.isFinite(controller)) return
  addControllerToLane(laneId, `cc:${clamp(Math.round(controller), 0, 127)}`)
  customControllerNumber.value = ''
}

function removeActiveControllerFromLane(laneId) {
  controllerLanes.value = controllerLanes.value.map((lane) => {
    if (lane.id !== laneId || lane.controllerIds.length <= 1) return lane
    const controllerIds = lane.controllerIds.filter(id => id !== lane.activeControllerId)
    return {
      ...lane,
      controllerIds,
      activeControllerId: controllerIds[0] || 'velocity',
    }
  })
  controllerMenuLaneId.value = null
  nextTick(drawAll)
}

function controllerDefinitionFromEvent(event) {
  if (event?.type === 'control_change') {
    return controllerDefinitionFromId(`cc:${Number(event.controller || 0)}`)
  }
  if (event?.type === 'pitch_bend') return controllerDefinitionFromId('pitch_bend')
  if (event?.type === 'channel_pressure') return controllerDefinitionFromId('after_touch')
  return null
}

function normalizeEditableControllerEvent(event) {
  const definition = controllerDefinitionFromEvent(event)
  if (!definition) return { ...event }
  return normalizeControllerEvent(definition, event, null)
}

async function persistActiveClipEvents(events) {
  if (!activeMidiClip.value) return
  const clipId = activeMidiClip.value.clip.id
  const normalized = events
    .map(normalizeEditableControllerEvent)
    .sort(sortControllerEvents)
  await persistProjectUpdate((nextProject) => {
    for (const track of nextProject.tracks || []) {
      const clip = (track.clips || []).find(item => item.id === clipId)
      if (!clip) continue
      clip.events = normalized
      const noteEnd = Math.max(
        0,
        ...(clip.notes || []).map(note => Number(note.start || 0) + Number(note.duration || 0))
      )
      const eventEnd = Math.max(0, ...normalized.map(event => Number(event.start || 0)))
      clip.duration = Math.max(Number(clip.duration || 0.25), noteEnd, eventEnd, activeNoteStep.value)
    }
  })
}

function sortControllerEvents(a, b) {
  return Number(a.start || 0) - Number(b.start || 0)
    || String(a.type || '').localeCompare(String(b.type || ''))
    || Number(a.controller ?? a.pitch ?? -1) - Number(b.controller ?? b.pitch ?? -1)
    || String(a.id || '').localeCompare(String(b.id || ''))
}

function onControllerLanePointerDown(event, lane) {
  if (!activeMidiClip.value) return
  const point = controllerLanePoint(event)
  if (!point || point.x < pianoKeyW || point.y < controllerLaneTabH) return
  event.preventDefault()
  const definition = controllerDefinitionForLane(lane)
  const value = controllerValueFromY(point.y, definition)

  if (definition.type === 'velocity') {
    selectedControllerEventId.value = null
    const note = findControllerVelocityNote(point.beat)
    if (!note) return
    updateNoteVelocity(note.id, value)
    controllerDrag = {
      type: 'velocity',
      laneId: lane.id,
      noteId: note.id,
      definition,
    }
  } else {
    const hit = hitTestControllerEvent(definition, point.x, point.y)
    if (hit) {
      selectedControllerEventId.value = hit.id
      controllerDrag = {
        type: 'event-point',
        laneId: lane.id,
        eventId: hit.id,
        definition,
      }
      bindControllerDrag()
      drawAll()
      return
    }
    const curveHit = hitTestControllerCurveHandle(definition, point.x, point.y)
    if (curveHit) {
      selectedControllerEventId.value = curveHit.eventId
      controllerDrag = {
        type: 'event-curve',
        laneId: lane.id,
        eventId: curveHit.eventId,
        definition,
        startY: point.y,
        startCurveAmount: Number(curveHit.event.curve_amount || 0),
      }
      bindControllerDrag()
      drawAll()
      return
    }
    const beat = snapControllerBeat(point.beat)
    const eventId = upsertControllerEventAtPoint(definition, beat, value)
    selectedControllerEventId.value = eventId
    controllerDrag = {
      type: 'event',
      laneId: lane.id,
      eventId,
      definition,
      lastBeat: beat,
      lastValue: value,
    }
  }

  bindControllerDrag()
  drawAll()
}

function bindControllerDrag() {
  window.addEventListener('pointermove', onControllerPointerMove)
  window.addEventListener('pointerup', onControllerPointerUp)
}

function unbindControllerDrag() {
  window.removeEventListener('pointermove', onControllerPointerMove)
  window.removeEventListener('pointerup', onControllerPointerUp)
}

function onControllerPointerMove(event) {
  if (!controllerDrag || !activeMidiClip.value) return
  const point = controllerLanePoint(event)
  if (!point) return
  event.preventDefault()
  const value = controllerValueFromY(point.y, controllerDrag.definition)
  if (controllerDrag.type === 'velocity') {
    updateNoteVelocity(controllerDrag.noteId, value)
  } else if (controllerDrag.type === 'event-curve') {
    const nextCurveAmount = normalizeCurveAmount(
      controllerDrag.startCurveAmount
        + ((controllerDrag.startY - point.y) / controllerLaneBodyH) * curveHandleDragScale
    )
    updateControllerEventCurve(controllerDrag.eventId, nextCurveAmount)
  } else if (controllerDrag.type === 'event-point') {
    const beat = snapControllerBeat(point.beat)
    updateControllerEvent(controllerDrag.definition, controllerDrag.eventId, { beat, value })
  } else {
    const beat = snapControllerBeat(point.beat)
    controllerDrag.eventId = writeControllerDragPoints(
      controllerDrag.definition,
      controllerDrag.lastBeat,
      controllerDrag.lastValue,
      beat,
      value
    )
    controllerDrag.lastBeat = beat
    controllerDrag.lastValue = value
  }
  drawAll()
}

async function onControllerPointerUp() {
  if (!controllerDrag || !activeMidiClip.value) return
  const drag = controllerDrag
  controllerDrag = null
  unbindControllerDrag()
  if (drag.type === 'velocity') {
    await persistActiveClipNotes(activeMidiClip.value.clip.notes)
  } else {
    await persistActiveClipEvents(activeMidiClip.value.clip.events || [])
  }
  drawAll()
}

function controllerLanePoint(event) {
  const target = event.target?.classList?.contains('controller-canvas')
    ? event.target
    : controllerLaneCanvases.get(controllerDrag?.laneId)
  const canvas = target || event.target
  if (!canvas?.getBoundingClientRect) return null
  const rect = canvas.getBoundingClientRect()
  const x = event.clientX - rect.left
  const y = event.clientY - rect.top
  return {
    x,
    y,
    beat: Math.max(0, (x - pianoKeyW) / pianoPxPerBeat.value),
  }
}

function controllerValueFromY(y, definition) {
  const bodyY = clamp(y - controllerLaneTabH, 0, controllerLaneBodyH)
  const unit = 1 - bodyY / controllerLaneBodyH
  return controllerUnitToValue(definition, unit)
}

function findControllerVelocityNote(beat) {
  const notes = activeMidiClip.value?.clip.notes || []
  const snapped = snapPianoBeat(beat)
  let closest = null
  let closestDistance = Number.POSITIVE_INFINITY
  for (const note of notes) {
    const start = Number(note.start || 0)
    const duration = Math.max(activeNoteStep.value, Number(note.duration || activeNoteStep.value))
    const distance = Math.min(Math.abs(start - beat), Math.abs(start + duration - beat))
    const inside = beat >= start - 0.05 && beat <= start + duration + 0.05
    if ((inside || Math.abs(start - snapped) < 0.001) && distance < closestDistance) {
      closest = note
      closestDistance = distance
    }
  }
  return closest
}

function updateNoteVelocity(noteId, value) {
  if (!activeMidiClip.value) return
  activeMidiClip.value.clip.notes = activeMidiClip.value.clip.notes
    .map(note => note.id === noteId
      ? { ...note, velocity: clamp(Math.round(value), 1, 127) }
      : note)
    .sort(sortNotes)
}

function upsertControllerEventAtPoint(definition, beat, value) {
  const events = activeMidiClip.value?.clip.events || []
  const start = snapControllerBeat(beat)
  const hit = findControllerEvent(definition, start)
  if (hit) {
    updateControllerEvent(definition, hit.id, { start, value })
    return hit.id
  }
  const event = normalizeControllerEvent(definition, {
    id: makeControllerEventId(),
    start,
    value,
  }, activePianoSnapStep.value)
  activeMidiClip.value.clip.events = [...events, event].sort(sortControllerEvents)
  return event.id
}

function writeControllerDragPoints(definition, startBeat, startValue, endBeat, endValue) {
  const beats = quantizedBeatsBetween(startBeat, endBeat, activePianoSnapStep.value)
  let lastEventId = controllerDrag?.eventId || null
  for (const beat of beats) {
    const value = interpolateControllerValue(startBeat, startValue, endBeat, endValue, beat)
    lastEventId = upsertControllerEventAtPoint(definition, beat, value)
  }
  return lastEventId
}

function findControllerEvent(definition, beat) {
  const events = activeMidiClip.value?.clip.events || []
  const snapThreshold = activePianoSnapStep.value
    ? Math.max(0.001, activePianoSnapStep.value / 3)
    : Number.POSITIVE_INFINITY
  const threshold = Math.min(Math.max(0.008, 3 / pianoPxPerBeat.value), snapThreshold)
  return events
    .filter(event => eventMatchesController(event, definition))
    .find(event => Math.abs(Number(event.start || 0) - beat) <= threshold)
}

function hitTestControllerEvent(definition, x, y) {
  const events = activeMidiClip.value?.clip.events || []
  for (const event of [...events].reverse()) {
    if (!eventMatchesController(event, definition)) continue
    const px = pianoKeyW + Number(event.start || 0) * pianoPxPerBeat.value
    const py = controllerValueToY(valueFromControllerEvent(event, definition), definition)
    if (Math.hypot(x - px, y - py) <= controllerPointHitRadius) {
      return event
    }
  }
  return null
}

function hitTestControllerCurveHandle(definition, x, y) {
  const points = controllerEditablePoints(definition)
  for (let index = points.length - 2; index >= 0; index -= 1) {
    const left = points[index]
    const right = points[index + 1]
    const handle = controllerCurveHandlePoint(left, right, definition)
    if (handle && Math.hypot(x - handle.x, y - handle.y) <= controllerCurveHandleHitRadius) {
      return {
        event: left.event,
        eventId: left.event.id,
        startBeat: left.start,
        endBeat: right.start,
      }
    }
  }
  return null
}

function controllerEditablePoints(definition) {
  const clip = activeMidiClip.value?.clip
  if (!clip) return []
  return controllerRenderPoints(clip.events || [], definition, 0)
    .filter(point => !point.synthetic)
}

function updateControllerEvent(definition, eventId, patch) {
  if (!activeMidiClip.value) return
  activeMidiClip.value.clip.events = (activeMidiClip.value.clip.events || [])
    .map((event) => {
      if (event.id !== eventId) return event
      const value = patch.value ?? valueFromControllerEvent(event, definition)
      const start = patch.beat ?? patch.start ?? event.start
      return normalizeControllerEvent(definition, { ...event, ...patch, start, value }, activePianoSnapStep.value)
    })
    .sort(sortControllerEvents)
}

function updateControllerEventCurve(eventId, curveAmount) {
  if (!activeMidiClip.value) return
  activeMidiClip.value.clip.events = (activeMidiClip.value.clip.events || [])
    .map((event) => {
      if (event.id !== eventId) return event
      return applyCurveAmount(event, curveAmount)
    })
    .sort(sortControllerEvents)
}

function setPianoQuantizeOption(optionId) {
  pianoQuantizeId.value = optionId
  pianoQuantizeMenuOpen.value = false
  drawAll()
}

function isInteractiveTarget(event) {
  const target = event.target
  const tag = String(target?.tagName || '').toLowerCase()
  return ['input', 'textarea', 'select', 'button', 'a'].includes(tag)
    || Boolean(target?.closest?.('input, textarea, select, button, a'))
}

function onTrackRowKeydown(event, trackId) {
  if (isInteractiveTarget(event)) return
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    selectTrack(trackId)
  }
}

function onStudioKeydown(event) {
  const tag = String(event.target?.tagName || '').toLowerCase()
  if (['input', 'textarea', 'select', 'button'].includes(tag)) return
  if (event.code === 'Space' && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault()
    togglePlay()
  } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'c') {
    event.preventDefault()
    if (pianoVisible.value && activeMidiClip.value && selectedNoteIds.value.size) {
      copySelectedNotes()
    } else {
      copySelectedClips()
    }
  } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'v') {
    event.preventDefault()
    if (pianoVisible.value && activeMidiClip.value && noteClipboard.value.length) {
      pasteNotes()
    } else {
      pasteClips()
    }
  } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
    event.preventDefault()
    if (pianoVisible.value && activeMidiClip.value) {
      selectedNoteIds.value = new Set(activeMidiClip.value.clip.notes.map(note => note.id))
    } else {
      selectedClipIds.value = new Set(
        tracks.value.flatMap(track => (track.clips || []).map(clip => clip.id))
      )
    }
    drawAll()
  } else if (event.key === 'Delete' || event.key === 'Backspace') {
    event.preventDefault()
    if (pianoVisible.value && activeMidiClip.value && selectedNoteIds.value.size) {
      deleteSelectedNotes()
    } else {
      deleteSelectedClips()
    }
  } else if (event.key === 'Escape') {
    selectedNoteIds.value = new Set()
    selectedClipIds.value = new Set()
    selectedAutomationPoint.value = { trackId: null, index: -1 }
    selectedControllerEventId.value = null
    selectionBox.value = null
    draftNote.value = null
    clearPianoLongPressTimer()
    closePianoMeterEditor()
    closePianoHarmonyEditor()
    controllerMenuLaneId.value = null
    pianoQuantizeMenuOpen.value = false
    automationDrag = null
    unbindAutomationDrag()
    automationMenu.value = { open: false, x: 0, y: 0, target: null, label: '' }
    closeTrackContextMenu()
    drawAll()
  }
}

function snapBeat(value) {
  return Math.round(Number(value || 0) / snapStep) * snapStep
}

function snapPianoBeat(value) {
  return Math.max(0, snapBeatToGrid(value, activePianoSnapStep.value))
}

function snapPianoBeatDelta(value) {
  return snapBeatToGrid(value, activePianoSnapStep.value)
}

function snapPianoDuration(value) {
  return Math.max(activeNoteStep.value, snapBeatToGrid(value, activePianoSnapStep.value))
}

function snapControllerBeat(value) {
  return Math.max(0, snapBeatToGrid(value, activePianoSnapStep.value))
}

function makeNoteId() {
  return `ui_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
}

function makeClipId() {
  return `clip_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
}

function openPiano() {
  lowerEditorMode.value = 'piano'
}

function closePiano() {
  if (lowerEditorMode.value === 'piano') lowerEditorMode.value = null
  selectedNoteIds.value = new Set()
  selectedControllerEventId.value = null
  draftNote.value = null
  selectionBox.value = null
  pianoResizeDrag = null
  controllerDrag = null
  automationDrag = null
  controllerMenuLaneId.value = null
  clearPianoLongPressTimer()
  closePianoMeterEditor()
  closePianoHarmonyEditor()
  unbindPianoDrag()
  unbindPianoResize()
  unbindControllerDrag()
  unbindAutomationDrag()
  drawAll()
}

function openMixer() {
  lowerEditorMode.value = 'mixer'
  selectedNoteIds.value = new Set()
  selectedControllerEventId.value = null
  draftNote.value = null
  selectionBox.value = null
  pianoQuantizeMenuOpen.value = false
  controllerMenuLaneId.value = null
  clearPianoLongPressTimer()
  closePianoMeterEditor()
  closePianoHarmonyEditor()
  unbindPianoDrag()
  unbindPianoResize()
  unbindControllerDrag()
  drawAll()
}

function closeMixer() {
  if (lowerEditorMode.value === 'mixer') lowerEditorMode.value = null
  drawAll()
}

function isInstrumentTrack(track) {
  return (track?.type || 'instrument') === 'instrument'
}

function isAudioTrack(track) {
  return track?.type === 'audio'
}

function isBusTrack(track) {
  return track?.type === 'bus'
}

function isAutomationTrack(track) {
  return track?.type === 'automation'
}

function outputChainContainsTrack(bus, trackId) {
  if (trackId == null) return false
  const wantedId = Number(trackId)
  const seen = new Set()
  let outputId = bus?.output_bus_id
  while (outputId != null) {
    const numericOutputId = Number(outputId)
    if (numericOutputId === wantedId) return true
    if (seen.has(numericOutputId)) return false
    seen.add(numericOutputId)
    const nextBus = tracks.value.find(track => Number(track.id) === numericOutputId)
    outputId = nextBus?.output_bus_id
  }
  return false
}

function availableOutputBuses(trackId = null) {
  return tracks.value.filter(track => (
    isBusTrack(track)
    && (trackId == null || Number(track.id) !== Number(trackId))
    && !outputChainContainsTrack(track, trackId)
  ))
}

function canUseMixerInserts(track) {
  return isInstrumentTrack(track) || isBusTrack(track)
}

function insertSlotNumber(slotId) {
  const match = String(slotId || '').match(/^insert_(\d+)$/)
  return match ? Number(match[1]) : null
}

function nextInsertSlotId(track) {
  const used = new Set(
    (track?.plugin_slots || [])
      .map(slot => insertSlotNumber(slot.id))
      .filter(number => number !== null)
  )
  let nextNumber = 1
  while (used.has(nextNumber)) nextNumber += 1
  return `insert_${nextNumber}`
}

function mixerInsertSlots(track) {
  if (!canUseMixerInserts(track)) return []
  const slots = (track?.plugin_slots || [])
    .filter(slot => insertSlotNumber(slot.id) !== null)
    .sort((a, b) => insertSlotNumber(a.id) - insertSlotNumber(b.id))
  return [
    ...slots,
    { id: nextInsertSlotId(track), type: 'empty', name: 'Empty', addSlot: true },
  ]
}

function mixerPluginDisplayLabel(track, slot) {
  const current = pluginSlot(track, slot.id)
  if (current.type === 'empty') return slot.addSlot ? 'Add Insert' : 'Empty'
  return current.name || 'Plugin'
}

function mixerRouteKey(track) {
  return String(track?.id ?? 'master')
}

function mixerDuplicateRoutes() {
  return [...mixerTracks.value, masterBus.value]
}

function uniqueMixerPluginLabel(track, slot) {
  const baseLabel = mixerPluginDisplayLabel(track, slot)
  if (baseLabel === 'Add Insert' || baseLabel === 'Empty') return baseLabel
  const duplicates = []
  for (const mixerTrack of mixerDuplicateRoutes()) {
    for (const insertSlot of mixerInsertSlots(mixerTrack)) {
      if (insertSlot.addSlot) continue
      if (mixerPluginDisplayLabel(mixerTrack, insertSlot) === baseLabel) {
        duplicates.push(`${mixerRouteKey(mixerTrack)}:${insertSlot.id}`)
      }
    }
  }
  const duplicateIndex = duplicates.indexOf(`${mixerRouteKey(track)}:${slot.id}`)
  return duplicateIndex === 0 ? baseLabel : `${baseLabel} (${duplicateIndex})`
}

function mixerSendRows(track) {
  return Array.isArray(track?.sends) ? track.sends : []
}

function normalizedTrackSends(track) {
  return mixerSendRows(track).map(send => ({
    id: send.id || `send_${send.target_bus_id}`,
    target_bus_id: Number(send.target_bus_id),
    level: Number(send.level ?? 1),
    enabled: send.enabled !== false,
  }))
}

function updateTrackSend(track, index, patch) {
  const sends = normalizedTrackSends(track)
  if (!sends[index]) return null
  sends[index] = { ...sends[index], ...patch }
  if (sends[index].target_bus_id == null || Number.isNaN(Number(sends[index].target_bus_id))) {
    sends.splice(index, 1)
  }
  return updateTrack(track.id, { sends })
}

function addTrackSend(track, targetBusId) {
  const target = Number(targetBusId)
  if (!Number.isFinite(target)) return null
  const sends = normalizedTrackSends(track)
  if (sends.some(send => Number(send.target_bus_id) === target)) return null
  sends.push({ id: `send_${target}`, target_bus_id: target, level: 1, enabled: true })
  return updateTrack(track.id, { sends })
}

function removeTrackSend(track, index) {
  const sends = normalizedTrackSends(track)
  sends.splice(index, 1)
  return updateTrack(track.id, { sends })
}

function onAddTrackSendChange(track, event) {
  addTrackSend(track, event.target.value)
  event.target.value = ''
}

function updateMasterBus(patch) {
  return persistProjectUpdate(nextProject => {
    nextProject.master_bus = {
      ...normalizeMasterBus(nextProject.master_bus),
      ...patch,
    }
  })
}

function setMasterBusPlugin(plugin, slotId) {
  const slot = {
    ...(plugin || {}),
    id: slotId,
  }
  const pluginSlots = masterBus.value.plugin_slots
    .filter(existing => existing?.id !== slotId)
  return updateMasterBus({ plugin_slots: [slot, ...pluginSlots] })
}

function volumeDbLabel(volume) {
  const value = Number(volume ?? 0)
  if (value <= 0.0001) return '-inf dB'
  const db = 20 * Math.log10(value)
  return `${db >= 0 ? '+' : ''}${db.toFixed(1)} dB`
}

function updateTrackOutputBus(track, value) {
  return updateTrack(track.id, { output_bus_id: value ? Number(value) : null })
}

function trackChannelLabel(track) {
  return track?.channel_type === 'mono' ? 'Mono' : 'Multi-channel'
}

function trackTypeLabel(track) {
  if (isAutomationTrack(track)) return 'Automation'
  if (isBusTrack(track)) return 'Bus'
  return isAudioTrack(track) ? `Audio ${trackChannelLabel(track)}` : 'Instrument'
}

function trackRowMetaLabel(track) {
  if (isAutomationTrack(track)) {
    return `${trackTypeLabel(track)} / ${automationTargetLabel(track.target)}`
  }
  return `${trackTypeLabel(track)} / ${track.clips?.length || 0} clips`
}

function automationPointCount(track) {
  return Array.isArray(track?.automation?.points) ? track.automation.points.length : 0
}

function automationTargetLabel(target) {
  if (!target) return 'Unassigned'
  if (target.kind === 'unassigned') return target.label || 'Unassigned'
  if (target.kind === 'tempo_bpm') return target.label || 'Tempo BPM'
  if (target.kind === 'track_volume') return target.label || `Track ${target.track_id} Volume`
  if (target.kind === 'track_pan') return target.label || `Track ${target.track_id} Pan`
  return target.label || `Param ${target.param_index ?? 0}`
}

function learnedAutomationTargetDetail(item) {
  const source = item?.source || {}
  return [
    source.track_name,
    source.slot_label || source.slot_id,
    source.plugin_name,
    source.param_name,
  ].filter(Boolean).join(' / ')
}

function automationTargetForTrackVolume(track) {
  return {
    kind: 'track_volume',
    track_id: track.id,
    label: `${track.name} Volume`,
  }
}

function automationTargetForTrackPan(track) {
  return {
    kind: 'track_pan',
    track_id: track.id,
    label: `${track.name} Pan`,
  }
}

function automationTargetForTempoBpm() {
  return {
    kind: 'tempo_bpm',
    label: 'Tempo BPM',
  }
}

function automationTargetForPluginParameter(track, slotId, param) {
  return {
    kind: 'plugin_parameter',
    track_id: track.id,
    slot_id: slotId,
    param_index: Number(param.index || 0),
    param_id: param.param_id,
    label: param.name || `Parameter ${param.index || 0}`,
  }
}

function openAutomationMenu(event, target, label = '') {
  closeTrackContextMenu()
  automationMenu.value = {
    open: true,
    x: Number(event.clientX ?? 0),
    y: Number(event.clientY ?? 0),
    target,
    label: label || target?.label || 'Automation',
  }
}

async function confirmCreateAutomationFromMenu() {
  const target = automationMenu.value.target
  if (!target) return
  await createAutomationTrackForTarget(target)
  automationMenu.value = { open: false, x: 0, y: 0, target: null, label: '' }
}

async function createAutomationTrackForTarget(target, options = {}) {
  const value = automationInitialValue(target)
  return createAutomationTrack(target, {
    name: options.name || target.label || 'Automation',
    color: options.color,
    value,
    points: [
      { beat: Math.max(0, positionBeats.value), value },
      { beat: Math.max(1, positionBeats.value + 4), value },
    ],
  })
}

function automationInitialValue(target) {
  const track = tracks.value.find(item => Number(item.id) === Number(target?.track_id))
  if (target?.kind === 'tempo_bpm') {
    return Number(effectiveTempoAtBeat(project.value, visualPositionBeats.value) || 120)
  }
  if (target?.kind === 'track_volume') return Number(track?.volume ?? 0.8)
  if (target?.kind === 'track_pan') return Number(track?.pan ?? 0)
  if (target?.kind === 'plugin_parameter') {
    const param = pluginParameterRows(target.track_id, target.slot_id)
      .find(item => Number(item.index) === Number(target.param_index))
    return Number(param?.value ?? 0)
  }
  return 0
}

function pluginParameterRows(trackId, slotId) {
  return pluginParameters.value?.[`${trackId}:${slotId}`] || []
}

function parameterValueLabel(param) {
  const value = Number(param?.value ?? 0)
  const unit = param?.units ? ` ${param.units}` : ''
  return `${value.toFixed(3)}${unit}`
}

async function setLivePluginParameter(trackId, slotId, paramIndex, value) {
  await setPluginParameter(trackId, slotId, paramIndex, value)
}

function isPluginEditorOpen(trackId) {
  return Boolean(editorWindows.value?.[`${trackId}:instrument`]?.open)
}

function canOpenPluginEditor(track) {
  if (!isInstrumentTrack(track)) return false
  return pluginSlot(track, 'instrument').type === 'vst3'
}

async function togglePluginEditor(track) {
  selectTrack(track.id)
  if (!canOpenPluginEditor(track)) return
  try {
    await openPluginEditor(track.id, 'instrument')
  } catch {}
}

function pluginSlot(track, slotId = 'instrument') {
  const found = (track.plugin_slots || []).find(slot => slot.id === slotId)
  if (found) return found
  if (!isInstrumentTrack(track)) {
    return {
      id: slotId,
      type: 'empty',
      name: 'Empty',
    }
  }
  if (slotId !== 'instrument') {
    return {
      id: slotId,
      type: 'empty',
      name: 'Empty',
    }
  }
  return {
    id: 'instrument',
    type: 'builtin',
    name: track.instrument || 'ATRI Basic Synth',
  }
}

function pluginSlotValue(track, slotId = 'instrument') {
  const slot = pluginSlot(track, slotId)
  if (slot.type === 'vst3' && slot.path) return `vst3::${slot.path}`
  if (slot.type === 'vst2' && slot.path) return `vst2::${slot.path}`
  if (slot.type === 'empty') return 'empty::'
  return 'builtin::ATRI Basic Synth'
}

function selectedPluginMissing(track, slotId = 'instrument') {
  const slot = pluginSlot(track, slotId)
  if (!['vst3', 'vst2'].includes(slot.type) || !slot.path) return false
  const list = slot.type === 'vst3' ? pluginOptions.value.vst3 : pluginOptions.value.vst2
  return !list.some(plugin => plugin.path === slot.path)
}

function pluginSlotLabel(track, slotId = 'instrument') {
  const slot = pluginSlot(track, slotId)
  if (slot.type === 'vst3' && slotId !== 'instrument') {
    return `${slot.vendor || 'VST3'} / ${slot.category || 'Processor'}`
  }
  if (slot.type === 'vst3') return `${slot.vendor || 'VST3'} · ${slot.category || 'Instrument'}`
  if (slot.type === 'vst2') return 'VST2 scanned, loading pending'
  if (slot.type === 'empty') return 'No processor'
  return 'Internal test instrument'
}

function parsePluginValue(value) {
  const raw = String(value)
  const separator = raw.indexOf('::')
  if (separator === -1) return { type: 'empty', path: '' }
  return {
    type: raw.slice(0, separator),
    path: raw.slice(separator + 2),
  }
}

async function onPluginSelect(track, slotId, value) {
  const { type, path } = parsePluginValue(value)
  if (type === 'empty') {
    await setTrackPlugin(track.id, { id: slotId, type: 'empty', name: 'Empty' }, slotId)
    return
  }
  if (type === 'builtin') {
    await setTrackPlugin(
      track.id,
      { id: slotId, type: 'builtin', name: 'ATRI Basic Synth' },
      slotId
    )
    return
  }
  const plugin = [...pluginOptions.value.vst3, ...pluginOptions.value.vst2]
    .find(item => item.path === path)
  if (!plugin) return
  await setTrackPlugin(track.id, {
    ...plugin,
    id: slotId,
    type,
  }, slotId)
}

async function onMasterBusPluginSelect(slotId, value) {
  const { type, path } = parsePluginValue(value)
  if (type === 'empty' || type === 'builtin') {
    await setMasterBusPlugin({ id: slotId, type: 'empty', name: 'Empty' }, slotId)
    return
  }
  const plugin = [...pluginOptions.value.vst3, ...pluginOptions.value.vst2]
    .find(item => item.path === path)
  if (!plugin) return
  await setMasterBusPlugin({
    ...plugin,
    id: slotId,
    type,
  }, slotId)
}

function animationLoop(now) {
  if (!lastFrame) lastFrame = now
  const delta = (now - lastFrame) / 1000
  lastFrame = now
  if (playing.value) {
    visualPositionBeats.value += delta * (effectiveTempoAtBeat(project.value, visualPositionBeats.value) / 60)
  } else {
    visualPositionBeats.value = positionBeats.value
  }
  syncTransportDisplayFields(project.value)
  drawAll()
  raf = requestAnimationFrame(animationLoop)
}

function drawAll() {
  drawArrangement()
  drawPiano()
  drawControllerLanes()
}

function setupCanvas(canvas, width, height) {
  const dpr = window.devicePixelRatio || 1
  canvas.width = Math.max(1, Math.floor(width * dpr))
  canvas.height = Math.max(1, Math.floor(height * dpr))
  canvas.style.width = `${width}px`
  canvas.style.height = `${height}px`
  const ctx = canvas.getContext('2d')
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  return ctx
}

function drawArrangement() {
  const canvas = arrangementCanvas.value
  const wrap = arrangementWrap.value
  if (!canvas || !wrap) return
  const timelineViewportWidth = Math.max(
    0,
    arrangementWrap.value.clientWidth - currentTrackListWidth()
  )
  const width = Math.max(
    timelineViewportWidth,
    arrangementLengthBeats() * arrangementPxPerBeat.value + 40
  )
  const height = Math.max(
    220,
    arrangementRulerH + Math.max(1, tracks.value.length) * arrangementTrackH
  )
  const ctx = setupCanvas(canvas, width, height)
  ctx.fillStyle = '#17191c'
  ctx.fillRect(0, 0, width, height)

  tracks.value.forEach((track, index) => {
    const y = arrangementRulerH + index * arrangementTrackH
    ctx.fillStyle = activeTrack.value?.id === track.id ? 'rgba(158, 191, 255, 0.08)' : '#1b1d20'
    ctx.fillRect(0, y, width, arrangementTrackH)
  })

  paintGrid(ctx, width, height, 0, arrangementRulerH)

  tracks.value.forEach((track, index) => {
    const y = arrangementRulerH + index * arrangementTrackH
    ctx.strokeStyle = 'rgba(229, 236, 245, 0.11)'
    ctx.beginPath()
    ctx.moveTo(0, y + arrangementTrackH)
    ctx.lineTo(width, y + arrangementTrackH)
    ctx.stroke()
  })

  ctx.fillStyle = '#202326'
  ctx.fillRect(0, 0, width, arrangementRulerH)
  drawRuler(ctx, width)

  tracks.value.forEach((track, index) => {
    if (isAutomationTrack(track)) {
      drawAutomationTrack(ctx, track, index)
      return
    }
    for (const clip of track.clips || []) {
      drawArrangementClip(ctx, track, clip, index)
    }
  })
  drawPlayhead(ctx, height)
}

function arrangementLengthBeats() {
  const emptyTailBeats = arrangementEmptyBars * Math.max(1, meterBeats.value)
  const clipEnd = Math.max(
    0,
    ...tracks.value.flatMap(track => (track.clips || []).map((clip) => (
      Number(clip.start || 0) + Number(clip.duration || 0)
    )))
  )
  const automationEnd = Math.max(
    0,
    ...tracks.value.flatMap(track => (track.automation?.points || []).map(point => Number(point.beat || 0)))
  )
  const harmonyEnd = Math.max(
    0,
    ...(project.value?.harmony_events || []).map(event => Number(event.beat || 0))
  )
  return Math.max(
    Number(project.value?.length_beats || 16),
    emptyTailBeats,
    clipEnd + 2,
    automationEnd + 2,
    harmonyEnd + 2
  )
}

function pianoLengthBeats(clip) {
  const emptyTailBeats = pianoEmptyBars * Math.max(1, meterBeats.value)
  const clipStart = Number(clip.start || 0)
  const noteEnd = Math.max(
    0,
    ...(clip.notes || []).map((note) => Number(note.start || 0) + Number(note.duration || 0))
  )
  const meterEventEnd = Math.max(
    0,
    ...(project.value?.meter_events || []).map(event => Number(event.beat || 0) - clipStart)
  )
  const harmonyEventEnd = Math.max(
    0,
    ...(project.value?.harmony_events || []).map(event => Number(event.beat || 0) - clipStart)
  )
  return Math.max(
    Number(clip.duration || 4),
    emptyTailBeats,
    noteEnd + 2,
    meterEventEnd + 2,
    harmonyEventEnd + 2
  )
}

function drawArrangementClip(ctx, track, clip, trackIndex) {
  const rect = clipRect(clip, trackIndex)
  const selected = selectedClipIds.value.has(clip.id)
  const active = activeClipId.value === clip.id
  if (clip.type === 'audio') {
    drawZrythmAudioRegionFrame(ctx, clip, rect, track, selected, active)
    drawClipAudioPreview(ctx, clip, rect, track)
    drawZrythmAudioResizeHandle(ctx, rect, active)
    return
  }

  ctx.fillStyle = hexToRgba(clip.color || track.color, track.mute ? 0.22 : 0.78)
  roundRect(ctx, rect.x, rect.y, rect.w, rect.h, 5)
  ctx.fill()
  ctx.strokeStyle = active
    ? 'rgba(240, 209, 122, 0.95)'
    : selected ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.26)'
  ctx.lineWidth = active ? 2 : 1
  ctx.stroke()

  ctx.fillStyle = 'rgba(15,17,19,0.76)'
  ctx.fillRect(rect.x, rect.y, rect.w, 16)
  ctx.fillStyle = '#f4f6f8'
  ctx.font = '10px Cascadia Mono, Consolas, monospace'
  ctx.fillText(
    `${clip.type === 'audio' ? 'AUDIO' : 'MIDI'}  ${clip.name || 'Clip'}`,
    rect.x + 7,
    rect.y + 11
  )

  if (clip.type === 'midi') {
    drawClipMidiPreview(ctx, clip, rect, track)
  }

  ctx.fillStyle = 'rgba(255,255,255,0.35)'
  ctx.fillRect(rect.x + rect.w - 5, rect.y + 18, 2, rect.h - 24)
}

function drawAutomationTrack(ctx, track, trackIndex) {
  const points = Array.isArray(track?.automation?.points)
    ? [...track.automation.points].sort(sortAutomationPoints)
    : []
  const y = arrangementRulerH + trackIndex * arrangementTrackH
  const midY = y + arrangementTrackH * 0.5
  const left = 0
  const right = arrangementLengthBeats() * arrangementPxPerBeat.value
  ctx.strokeStyle = hexToRgba(track.color, track.mute ? 0.22 : 0.74)
  ctx.lineWidth = 2
  ctx.beginPath()
  if (!points.length) {
    ctx.moveTo(left, midY)
    ctx.lineTo(right, midY)
  } else {
    drawAutomationHoldLine(ctx, track, points, trackIndex, right)
  }
  ctx.stroke()
  drawAutomationCurveHandles(ctx, track, points, trackIndex)
  for (const [index, point] of points.entries()) {
    const x = Number(point.beat || 0) * arrangementPxPerBeat.value
    const py = automationPointY(track, point, trackIndex)
    const selected = Number(selectedAutomationPoint.value.trackId) === Number(track.id)
      && selectedAutomationPoint.value.index === index
    ctx.fillStyle = selected ? '#f0d17a' : hexToRgba(track.color, track.mute ? 0.28 : 0.95)
    ctx.beginPath()
    ctx.arc(x, py, selected ? 5 : 4, 0, Math.PI * 2)
    ctx.fill()
    if (selected) {
      ctx.strokeStyle = 'rgba(255,255,255,0.72)'
      ctx.lineWidth = 1.2
      ctx.stroke()
    }
  }
  ctx.fillStyle = 'rgba(244,246,248,0.72)'
  ctx.font = '10px Cascadia Mono, Consolas, monospace'
  ctx.fillText(automationTargetLabel(track.target), 8, y + 16)
}

function drawAutomationSegmentPath(ctx, track, points, trackIndex, right) {
  const first = points[0]
  const last = points[points.length - 1]
  const firstX = Number(first.beat || 0) * arrangementPxPerBeat.value
  const firstY = automationPointY(track, first, trackIndex)
  ctx.moveTo(0, firstY)
  ctx.lineTo(firstX, firstY)
  for (let index = 0; index < points.length - 1; index += 1) {
    const left = points[index]
    const rightPoint = points[index + 1]
    drawAutomationSegment(ctx, track, left, rightPoint, trackIndex)
  }
  ctx.lineTo(right, automationPointY(track, last, trackIndex))
}

function drawAutomationSegment(ctx, track, left, right, trackIndex) {
  const startBeat = Number(left.beat || 0)
  const endBeat = Number(right.beat || 0)
  const rightX = endBeat * arrangementPxPerBeat.value
  const rightY = automationPointY(track, right, trackIndex)
  if (endBeat <= startBeat) {
    ctx.lineTo(rightX, rightY)
    return
  }
  if (String(left.curve || 'linear') === 'hold') {
    ctx.lineTo(rightX, automationPointY(track, left, trackIndex))
    ctx.lineTo(rightX, rightY)
    return
  }
  const sampleCount = curveRenderSampleCount(startBeat, endBeat, arrangementPxPerBeat.value)
  for (let sample = 1; sample <= sampleCount; sample += 1) {
    const position = sample / sampleCount
    const sampleBeat = startBeat + (endBeat - startBeat) * position
    const value = automationCurveValueAtBeat(track, left, right, sampleBeat)
    ctx.lineTo(
      sampleBeat * arrangementPxPerBeat.value,
      automationPointY(track, { beat: sampleBeat, value }, trackIndex)
    )
  }
}

function drawAutomationCurveHandles(ctx, track, points, trackIndex) {
  ctx.save()
  for (let index = 0; index < points.length - 1; index += 1) {
    const handle = automationCurveHandlePoint(track, points[index], points[index + 1], trackIndex)
    if (!handle) continue
    const selected = Number(selectedAutomationPoint.value.trackId) === Number(track.id)
      && selectedAutomationPoint.value.index === index
    ctx.beginPath()
    ctx.arc(handle.x, handle.y, selected ? 4 : 3.5, 0, Math.PI * 2)
    ctx.fillStyle = '#181b1f'
    ctx.strokeStyle = selected ? '#f0d17a' : hexToRgba(track.color, track.mute ? 0.3 : 0.84)
    ctx.lineWidth = selected ? 1.5 : 1.2
    ctx.fill()
    ctx.stroke()
  }
  ctx.restore()
}

function automationCurveHandlePoint(track, left, right, trackIndex) {
  if (!left || !right || String(left.curve || 'linear') === 'hold') return null
  const startBeat = Number(left.beat || 0)
  const endBeat = Number(right.beat || 0)
  if (endBeat <= startBeat) return null
  if ((endBeat - startBeat) * arrangementPxPerBeat.value < curveHandleMinSegmentPx) return null
  const beat = startBeat + (endBeat - startBeat) * 0.5
  const value = automationCurveValueAtBeat(track, left, right, beat)
  return {
    x: beat * arrangementPxPerBeat.value,
    y: automationPointY(track, { beat, value }, trackIndex),
  }
}

function drawAutomationHoldLine(ctx, track, points, trackIndex, right) {
  drawAutomationSegmentPath(ctx, track, points, trackIndex, right)
}

function drawZrythmAudioRegionFrame(ctx, clip, rect, track, selected, active) {
  const trackColor = clip.color || track.color
  const headerHeight = audioRegionHeaderHeight(rect)
  const radius = 5

  ctx.save()
  ctx.beginPath()
  roundRect(ctx, rect.x, rect.y, rect.w, rect.h, radius)
  ctx.clip()

  ctx.fillStyle = hexToRgba(clip.color || track.color, track.mute ? 0.22 : 0.72)
  ctx.fillRect(rect.x, rect.y, rect.w, rect.h)

  ctx.fillStyle = hexToRgba(trackColor, track.mute ? 0.18 : 0.78)
  ctx.fillRect(rect.x, rect.y + headerHeight, rect.w, Math.max(1, rect.h - headerHeight))

  ctx.fillStyle = hexToRgba(mixHexColor(trackColor, '#ffffff', 0.32), track.mute ? 0.24 : 0.72)
  ctx.fillRect(rect.x, rect.y, rect.w, headerHeight)

  ctx.strokeStyle = 'rgba(255,255,255,0.18)'
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(rect.x, rect.y + headerHeight + 0.5)
  ctx.lineTo(rect.x + rect.w, rect.y + headerHeight + 0.5)
  ctx.stroke()
  ctx.restore()

  ctx.strokeStyle = active
    ? 'rgba(240, 209, 122, 0.95)'
    : selected ? 'rgba(255,255,255,0.62)' : 'rgba(0,0,0,0.32)'
  ctx.lineWidth = active ? 2 : 1
  roundRect(ctx, rect.x, rect.y, rect.w, rect.h, radius)
  ctx.stroke()

  ctx.fillStyle = zrythmRegionContentColor()
  ctx.font = '10px Cascadia Mono, Consolas, monospace'
  ctx.fillText(`AUDIO  ${clip.name || 'Clip'}`, rect.x + 7, rect.y + headerHeight - 5)
}

function drawZrythmAudioResizeHandle(ctx, rect, active) {
  const headerHeight = audioRegionHeaderHeight(rect)
  ctx.fillStyle = active ? 'rgba(240, 209, 122, 0.78)' : 'rgba(255,255,255,0.42)'
  ctx.fillRect(rect.x + rect.w - 5, rect.y + headerHeight + 3, 2, Math.max(1, rect.h - headerHeight - 8))
}

function audioRegionHeaderHeight(rect) {
  return Math.min(18, Math.max(14, Math.floor(rect.h * 0.24)))
}

function drawClipMidiPreview(ctx, clip, rect, track) {
  const notes = clip.notes || []
  const minNote = Math.min(...notes.map(note => Number(note.pitch || 60)), 48)
  const maxNote = Math.max(...notes.map(note => Number(note.pitch || 60)), 72)
  const range = Math.max(1, maxNote - minNote)
  ctx.fillStyle = hexToRgba(track.color, 0.96)
  for (const note of notes) {
    const x = rect.x + (Number(note.start || 0) / Number(clip.duration || 1)) * rect.w
    const w = Math.max(3, (Number(note.duration || 0.25) / Number(clip.duration || 1)) * rect.w)
    const y = rect.y + 22 + (1 - (Number(note.pitch || 60) - minNote) / range) * (rect.h - 30)
    roundRect(ctx, x, y, Math.max(2, Math.min(w, rect.x + rect.w - x - 3)), 4, 2)
    ctx.fill()
  }
}

function drawClipAudioPreview(ctx, clip, rect, track) {
  const waveform = Array.isArray(clip.waveform) ? clip.waveform : []
  const points = waveform.map(waveformPointMetrics).filter(Boolean)
  const trackColor = clip.color || track.color
  const bodyTop = rect.y + audioRegionHeaderHeight(rect) + 2
  const bodyBottom = rect.y + rect.h - 5
  const bodyHeight = Math.max(12, bodyBottom - bodyTop)
  const mid = bodyTop + bodyHeight * 0.5
  const maxAmp = Math.max(4, bodyHeight * 0.46)
  const left = rect.x + 4
  const right = Math.max(left + 1, rect.x + rect.w - 7)
  const bounds = {
    left,
    right,
    top: bodyTop,
    bottom: bodyBottom,
    height: bodyHeight,
    mid,
    maxAmp,
    width: Math.max(1, right - left),
  }

  ctx.save()
  ctx.beginPath()
  roundRect(ctx, rect.x + 3, bodyTop, Math.max(1, rect.w - 9), bodyHeight, 3)
  ctx.clip()

  ctx.fillStyle = hexToRgba(trackColor, track.mute ? 0.08 : 0.18)
  ctx.fillRect(rect.x + 3, bodyTop, Math.max(1, rect.w - 9), bodyHeight)

  if (points.length) {
    drawZrythmWaveformEnvelope(ctx, points, bounds)
  } else {
    drawZrythmFallbackWaveform(ctx, bounds)
  }

  ctx.restore()
}

function waveformPointMetrics(point) {
  if (typeof point === 'number') {
    const peak = clamp(Math.abs(point), 0, 1)
    return { min: -peak, max: peak, rms: peak * 0.58, peak }
  }
  if (!point || typeof point !== 'object') return null

  let min = waveformFiniteNumber(point.min)
  let max = waveformFiniteNumber(point.max)
  const rawPeak = waveformFiniteNumber(point.peak)
  const rawRms = waveformFiniteNumber(point.rms)
  let peak = rawPeak === null ? null : clamp(Math.abs(rawPeak), 0, 1)
  let rms = rawRms === null ? null : clamp(Math.abs(rawRms), 0, 1)

  if (min === null && max === null) {
    if (peak === null) return null
    min = -peak
    max = peak
  } else {
    const fallback = peak || 0
    min = min === null ? -Math.max(fallback, Math.abs(max || 0)) : clamp(min, -1, 1)
    max = max === null ? Math.max(fallback, Math.abs(min || 0)) : clamp(max, -1, 1)
    if (min > max) {
      const nextMin = max
      max = min
      min = nextMin
    }
  }

  const envelopePeak = Math.max(Math.abs(min), Math.abs(max))
  rms = rms === null ? envelopePeak * 0.58 : rms
  peak = peak === null ? envelopePeak : peak
  peak = clamp(Math.max(peak, envelopePeak, rms), 0, 1)
  rms = Math.min(rms, peak)
  return { min, max, rms, peak }
}

function waveformFiniteNumber(value) {
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

function drawZrythmWaveformEnvelope(ctx, points, bounds) {
  if (!points.length) return
  ctx.save()
  ctx.fillStyle = zrythmRegionContentColor()
  ctx.strokeStyle = zrythmRegionOutlineColor()
  ctx.lineWidth = 1
  ctx.lineJoin = 'round'
  ctx.beginPath()
  points.forEach((point, index) => {
    const x = waveformX(bounds, points.length, index)
    const y = waveformY(bounds, point.min)
    if (index === 0) ctx.moveTo(x, y)
    else ctx.lineTo(x, y)
  })
  for (let index = points.length - 1; index >= 0; index -= 1) {
    ctx.lineTo(waveformX(bounds, points.length, index), waveformY(bounds, points[index].max))
  }
  ctx.closePath()
  ctx.fill()
  ctx.stroke()
  ctx.restore()
}

function drawZrythmFallbackWaveform(ctx, bounds) {
  const count = Math.max(32, Math.floor(bounds.width))
  const points = Array.from({ length: count }, (_, index) => {
    const unit = index / Math.max(1, count - 1)
    const peak = clamp(
      0.18 + Math.abs(Math.sin(unit * 31.4)) * 0.36 + Math.abs(Math.sin(unit * 91.7)) * 0.2,
      0,
      1
    )
    return { min: -peak, max: peak, rms: peak * 0.54, peak }
  })
  drawZrythmWaveformEnvelope(ctx, points, bounds)
}

function waveformX(bounds, count, index) {
  return bounds.left + (index / Math.max(1, count - 1)) * bounds.width
}

function waveformY(bounds, value) {
  return clamp(bounds.mid + value * bounds.maxAmp, bounds.top + 1, bounds.bottom - 1)
}

function drawPiano() {
  const canvas = pianoCanvas.value
  const wrap = pianoWrap.value
  if (!canvas || !wrap || !activeMidiClip.value || !pianoVisible.value) return
  const clip = activeMidiClip.value.clip
  const width = Math.max(
    wrap.clientWidth,
    pianoKeyW + pianoLengthBeats(clip) * pianoPxPerBeat.value
  )
  pianoTimelineWidth.value = width
  const height = pianoNoteTop.value + (maxPitch - minPitch + 1) * pianoRowH
  const ctx = setupCanvas(canvas, width, height)
  ctx.fillStyle = '#17191c'
  ctx.fillRect(0, 0, width, height)
  drawPianoRuler(ctx, width, clip)
  for (const subtrackId of pianoVisibleSubtracks.value) {
    if (subtrackId === 'meter') {
      drawPianoMeterLane(ctx, width, clip)
    } else if (subtrackId === 'harmony') {
      drawPianoHarmonyLane(ctx, width, clip, pianoHarmonyLaneTop.value)
    }
  }

  for (let pitch = maxPitch; pitch >= minPitch; pitch -= 1) {
    const row = maxPitch - pitch
    const y = pianoNoteTop.value + row * pianoRowH
    const black = [1, 3, 6, 8, 10].includes(pitch % 12)
    ctx.fillStyle = black ? '#111316' : '#202326'
    ctx.fillRect(0, y, pianoKeyW, pianoRowH)
    ctx.fillStyle = black ? 'rgba(255,255,255,0.035)' : 'rgba(255,255,255,0.018)'
    ctx.fillRect(pianoKeyW, y, width - pianoKeyW, pianoRowH)
    ctx.strokeStyle = black ? 'rgba(0,0,0,0.38)' : 'rgba(229,236,245,0.08)'
    ctx.beginPath()
    ctx.moveTo(0, y + pianoRowH)
    ctx.lineTo(width, y + pianoRowH)
    ctx.stroke()
    if (pitch % 12 === 0) {
      ctx.fillStyle = '#9aa3ad'
      ctx.font = '10px Cascadia Mono, Consolas, monospace'
      ctx.fillText(pitchName(pitch), 10, y + 9)
    }
  }
  paintPianoGrid(ctx, width, height, clip)

  const track = activeMidiClip.value.track
  if (track) {
    for (const note of clip.notes || []) {
      if (note.pitch < minPitch || note.pitch > maxPitch) continue
      const rect = noteRect(note)
      const selected = selectedNoteIds.value.has(note.id)
      ctx.fillStyle = selected ? '#f0d17a' : hexToRgba(track.color, 0.82)
      roundRect(ctx, rect.x, rect.y, rect.w, rect.h, 3)
      ctx.fill()
      ctx.strokeStyle = selected ? 'rgba(255, 255, 255, 0.64)' : 'rgba(0,0,0,0.24)'
      ctx.stroke()
      if (rect.w > 34) {
        ctx.fillStyle = selected ? 'rgba(20,22,24,0.9)' : 'rgba(255,255,255,0.82)'
        ctx.font = '10px Cascadia Mono, Consolas, monospace'
        ctx.fillText(pitchName(note.pitch), rect.x + 5, rect.y + 9)
      }
    }
  }
  if (draftNote.value) {
    const rect = noteRect(draftNote.value)
    ctx.fillStyle = 'rgba(240, 209, 122, 0.52)'
    ctx.strokeStyle = 'rgba(240, 209, 122, 0.96)'
    roundRect(ctx, rect.x, rect.y, rect.w, rect.h, 3)
    ctx.fill()
    ctx.stroke()
  }
  if (selectionBox.value) {
    const box = selectionBox.value
    const x = Math.min(box.x1, box.x2)
    const y = Math.min(box.y1, box.y2)
    const w = Math.abs(box.x2 - box.x1)
    const h = Math.abs(box.y2 - box.y1)
    ctx.fillStyle = 'rgba(125, 168, 232, 0.12)'
    ctx.strokeStyle = 'rgba(125, 168, 232, 0.72)'
    ctx.setLineDash([4, 3])
    ctx.strokeRect(x, y, w, h)
    ctx.fillRect(x, y, w, h)
    ctx.setLineDash([])
  }
  drawPianoPlayhead(ctx, height, clip)
}

function drawControllerLanes() {
  if (!pianoVisible.value || !activeMidiClip.value || !controllerLanes.value.length) return
  const clip = activeMidiClip.value.clip
  controllerScrollLeft.value = controllerWrap.value?.scrollLeft || 0
  const width = Math.max(
    controllerWrap.value?.clientWidth || 0,
    pianoTimelineWidth.value,
    pianoKeyW + pianoLengthBeats(clip) * pianoPxPerBeat.value
  )
  pianoTimelineWidth.value = width
  for (const lane of controllerLanes.value) {
    const canvas = controllerLaneCanvases.get(lane.id)
    if (!canvas) continue
    const ctx = setupCanvas(canvas, width, controllerLaneH)
    drawControllerLane(ctx, lane, width, clip)
  }
}

function drawControllerLane(ctx, lane, width, clip) {
  const definition = controllerDefinitionForLane(lane)
  const colorStyles = controllerLaneColorStyles(activeMidiClip.value?.track?.color)
  ctx.fillStyle = '#17191c'
  ctx.fillRect(0, 0, width, controllerLaneH)
  ctx.fillStyle = '#202428'
  ctx.fillRect(0, 0, pianoKeyW, controllerLaneH)
  ctx.fillStyle = '#202326'
  ctx.fillRect(pianoKeyW, 0, width - pianoKeyW, controllerLaneTabH)
  ctx.fillStyle = '#181b1f'
  ctx.fillRect(pianoKeyW, controllerLaneTabH, width - pianoKeyW, controllerLaneBodyH)
  paintControllerGrid(ctx, width)

  if (definition.type === 'velocity') {
    drawVelocityLane(ctx, clip, definition, colorStyles)
  } else {
    drawEventLane(ctx, clip, definition, colorStyles)
  }
  drawControllerPlayhead(ctx, controllerLaneH, clip)
}

function paintControllerGrid(ctx, width) {
  const bodyTop = controllerLaneTabH
  const bodyBottom = controllerLaneTabH + controllerLaneBodyH
  const scale = pianoPxPerBeat.value
  const snapStepWidth = activePianoSnapStep.value ? activePianoSnapStep.value * scale : 0
  ctx.strokeStyle = 'rgba(229,236,245,0.12)'
  ctx.beginPath()
  ctx.moveTo(0, bodyTop + 0.5)
  ctx.lineTo(width, bodyTop + 0.5)
  ctx.moveTo(0, bodyBottom - 0.5)
  ctx.lineTo(width, bodyBottom - 0.5)
  ctx.stroke()

  for (const unit of [0.25, 0.5, 0.75]) {
    const y = bodyTop + controllerLaneBodyH * unit
    ctx.strokeStyle = unit === 0.5 ? 'rgba(229,236,245,0.11)' : 'rgba(229,236,245,0.055)'
    ctx.beginPath()
    ctx.moveTo(pianoKeyW, y)
    ctx.lineTo(width, y)
    ctx.stroke()
  }

  const visibleBeats = Math.ceil((width - pianoKeyW) / pianoPxPerBeat.value)
  for (let beat = 0; beat <= visibleBeats; beat += 1) {
    const x = pianoKeyW + beat * pianoPxPerBeat.value
    ctx.strokeStyle = 'rgba(229,236,245,0.06)'
    ctx.beginPath()
    ctx.moveTo(x, bodyTop)
    ctx.lineTo(x, bodyBottom)
    ctx.stroke()

    if (snapStepWidth >= 4 && activePianoSnapStep.value && activePianoSnapStep.value < 1) {
      for (let subBeat = activePianoSnapStep.value; subBeat < 1; subBeat += activePianoSnapStep.value) {
        const subX = x + subBeat * scale
        ctx.strokeStyle = 'rgba(229,236,245,0.035)'
        ctx.beginPath()
        ctx.moveTo(subX, bodyTop)
        ctx.lineTo(subX, bodyBottom)
        ctx.stroke()
      }
    }
  }

  // Bar lines overlaid at bar boundaries (handles fractional barLen like 3/8=1.5)
  const barLen = meterBeats.value
  for (let bar = 0; bar * barLen <= visibleBeats; bar++) {
    const barX = pianoKeyW + bar * barLen * pianoPxPerBeat.value
    ctx.strokeStyle = 'rgba(229,236,245,0.14)'
    ctx.beginPath()
    ctx.moveTo(barX, bodyTop)
    ctx.lineTo(barX, bodyBottom)
    ctx.stroke()
  }
}

function drawVelocityLane(ctx, clip, definition, colorStyles) {
  const notes = clip.notes || []
  for (const note of notes) {
    const x = pianoKeyW + Number(note.start || 0) * pianoPxPerBeat.value
    const value = clamp(Math.round(Number(note.velocity || definition.defaultValue)), 1, 127)
    const y = controllerValueToY(value, definition)
    const selected = selectedNoteIds.value.has(note.id)
    ctx.strokeStyle = selected ? colorStyles.selectedVelocityStroke : colorStyles.velocityStroke
    ctx.lineWidth = selected ? 3 : 2
    ctx.beginPath()
    ctx.moveTo(x, controllerLaneTabH + controllerLaneBodyH)
    ctx.lineTo(x, y)
    ctx.stroke()
    ctx.fillStyle = selected ? colorStyles.selectedVelocityFill : colorStyles.velocityFill
    ctx.fillRect(x - 2, y - 2, 4, 4)
  }
  ctx.lineWidth = 1
}

function drawEventLane(ctx, clip, definition, colorStyles) {
  const tailBeat = Math.max(0, (pianoTimelineWidth.value - pianoKeyW) / pianoPxPerBeat.value)
  const points = controllerRenderPoints(clip.events || [], definition, tailBeat)
  if (!points.length) return

  ctx.strokeStyle = colorStyles.eventStroke
  ctx.fillStyle = colorStyles.eventFill
  ctx.lineWidth = 1.4
  ctx.beginPath()
  drawControllerCurvePath(ctx, points, definition)
  ctx.stroke()
  drawControllerCurveHandles(ctx, points, definition, colorStyles)

  for (const point of points) {
    if (point.synthetic) continue
    const x = pianoKeyW + Number(point.start || 0) * pianoPxPerBeat.value
    const y = controllerValueToY(point.value, definition)
    const selected = point.event?.id && point.event.id === selectedControllerEventId.value
    ctx.beginPath()
    ctx.arc(x, y, selected ? 5 : 4, 0, Math.PI * 2)
    ctx.fillStyle = selected ? '#f0d17a' : colorStyles.eventFill
    ctx.fill()
    ctx.strokeStyle = selected ? 'rgba(255,255,255,0.72)' : colorStyles.eventPointStroke
    ctx.stroke()
  }
  ctx.lineWidth = 1
}

function drawControllerCurvePath(ctx, points, definition) {
  points.forEach((point, index) => {
    const x = pianoKeyW + Number(point.start || 0) * pianoPxPerBeat.value
    const y = controllerValueToY(point.value, definition)
    if (index === 0) {
      ctx.moveTo(x, y)
      return
    }
    const left = points[index - 1]
    if (left.event && point.event && Number(point.start || 0) > Number(left.start || 0)) {
      drawControllerCurveSegment(ctx, left, point, definition)
    } else {
      ctx.lineTo(x, y)
    }
  })
}

function drawControllerCurveSegment(ctx, left, right, definition) {
  const startBeat = Number(left.start || 0)
  const endBeat = Number(right.start || 0)
  const sampleCount = curveRenderSampleCount(startBeat, endBeat, pianoPxPerBeat.value)
  for (let sample = 1; sample <= sampleCount; sample += 1) {
    const position = sample / sampleCount
    const sampleBeat = startBeat + (endBeat - startBeat) * position
    const value = controllerCurveValueAtBeat(left.event, right.event, sampleBeat, definition)
    ctx.lineTo(
      pianoKeyW + sampleBeat * pianoPxPerBeat.value,
      controllerValueToY(value, definition)
    )
  }
}

function drawControllerCurveHandles(ctx, points, definition, colorStyles) {
  ctx.save()
  for (let index = 0; index < points.length - 1; index += 1) {
    const handle = controllerCurveHandlePoint(points[index], points[index + 1], definition)
    if (!handle) continue
    const selected = points[index].event?.id && points[index].event.id === selectedControllerEventId.value
    ctx.beginPath()
    ctx.arc(handle.x, handle.y, selected ? 4 : 3.5, 0, Math.PI * 2)
    ctx.fillStyle = '#181b1f'
    ctx.strokeStyle = selected ? '#f0d17a' : colorStyles.eventStroke
    ctx.lineWidth = selected ? 1.5 : 1.2
    ctx.fill()
    ctx.stroke()
  }
  ctx.restore()
}

function controllerCurveHandlePoint(left, right, definition) {
  if (!left?.event || !right?.event) return null
  const startBeat = Number(left.start || 0)
  const endBeat = Number(right.start || 0)
  if (endBeat <= startBeat) return null
  if ((endBeat - startBeat) * pianoPxPerBeat.value < curveHandleMinSegmentPx) return null
  const beat = startBeat + (endBeat - startBeat) * 0.5
  const value = controllerCurveValueAtBeat(left.event, right.event, beat, definition)
  return {
    x: pianoKeyW + beat * pianoPxPerBeat.value,
    y: controllerValueToY(value, definition),
  }
}

function curveRenderSampleCount(startBeat, endBeat, pxPerBeat) {
  const width = Math.abs(Number(endBeat || 0) - Number(startBeat || 0)) * Math.max(1, pxPerBeat)
  return Math.round(clamp(Math.ceil(width / 10), 4, 64))
}

function controllerValueToY(value, definition) {
  const unit = controllerValueToUnit(definition, value)
  return controllerLaneTabH + (1 - unit) * controllerLaneBodyH
}

function drawControllerPlayhead(ctx, height, clip) {
  const localBeat = visualPositionBeats.value - Number(clip.start || 0)
  if (localBeat < 0 || localBeat > Number(clip.duration || 0)) return
  const x = pianoKeyW + localBeat * pianoPxPerBeat.value
  ctx.strokeStyle = 'rgba(240, 209, 122, 0.8)'
  ctx.lineWidth = 1.3
  ctx.beginPath()
  ctx.moveTo(x, controllerLaneTabH)
  ctx.lineTo(x, height)
  ctx.stroke()
}

function drawRuler(ctx, width) {
  const scale = arrangementPxPerBeat.value
  const endBeat = Math.ceil(width / scale)
  drawBeatRulerLabels(ctx, {
    startBeat: 0,
    endBeat,
    originX: 0,
    scale,
    height: arrangementRulerH,
    labelY: 19,
  })
}

function isRulerBarBeat(absoluteBeat) {
  const beat = Number(absoluteBeat || 0)
  return meterBarLinesBetween(project.value, Math.max(0, beat - 0.0001), beat + 0.0001)
    .some(line => Math.abs(line.beat - beat) < 0.0001)
}

function rulerBeatLabel(absoluteBeat) {
  const position = meterPositionAtBeat(project.value, absoluteBeat)
  return position.beat === 1 ? String(position.bar) : `${position.bar}.${position.beat}`
}

function rulerTickStep() {
  return activePianoSnapStep.value || snapStep
}

function isRulerBeatUnitTick(absoluteBeat) {
  const meter = effectiveMeterAtBeat(project.value, absoluteBeat)
  const unit = 4 / meter.denominator
  if (!Number.isFinite(unit) || unit <= 0) return false
  const beat = Number(absoluteBeat || 0) / unit
  return Math.abs(beat - Math.round(beat)) < 0.0001
}

function rulerTickMetrics(absoluteBeat) {
  const isBar = isRulerBarBeat(absoluteBeat)
  if (isBar) {
    return {
      isBar,
      shouldLabel: true,
      heightRatio: rulerMajorTickRatio,
      lineWidth: 1.4,
      strokeStyle: 'rgba(229, 236, 245, 0.58)',
      fillStyle: '#d9e2ec',
    }
  }
  const isBeat = isRulerBeatUnitTick(absoluteBeat)
  if (isBeat) {
    return {
      isBar,
      shouldLabel: true,
      heightRatio: rulerMinorTickRatio,
      lineWidth: 1,
      strokeStyle: 'rgba(229, 236, 245, 0.38)',
      fillStyle: '#b9c8d8',
    }
  }
  return {
    isBar,
    shouldLabel: false,
    heightRatio: rulerFineTickRatio,
    lineWidth: 0.7,
    strokeStyle: 'rgba(229, 236, 245, 0.2)',
    fillStyle: '#95b6d8',
  }
}

function drawBeatRulerLabels(ctx, {
  startBeat,
  endBeat,
  originX,
  scale,
  height,
  labelY,
}) {
  const tickStep = rulerTickStep()
  if (!Number.isFinite(tickStep) || tickStep <= 0 || !Number.isFinite(scale) || scale <= 0) return

  ctx.save()
  ctx.textBaseline = 'middle'
  ctx.fillStyle = 'rgba(32, 35, 38, 0.94)'
  ctx.fillRect(originX, 0, Math.max(0, (endBeat - startBeat) * scale), height)
  ctx.strokeStyle = 'rgba(229, 236, 245, 0.16)'
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(originX, height - 0.5)
  ctx.lineTo(originX + Math.max(0, (endBeat - startBeat) * scale), height - 0.5)
  ctx.stroke()

  for (
    let absoluteBeat = firstMultipleAtOrAfter(startBeat, tickStep);
    absoluteBeat <= endBeat + 0.001;
    absoluteBeat += tickStep
  ) {
    const metrics = rulerTickMetrics(absoluteBeat)
    const shouldDrawBeatLabel = metrics.shouldLabel && (metrics.isBar || scale >= rulerBeatLabelMinScale)

    const x = originX + (absoluteBeat - startBeat) * scale
    const tickBottom = height - 1
    const tickHeight = Math.max(4, Math.round(height * metrics.heightRatio))
    ctx.strokeStyle = metrics.strokeStyle
    ctx.lineWidth = metrics.lineWidth
    ctx.beginPath()
    ctx.moveTo(x, tickBottom - tickHeight)
    ctx.lineTo(x, tickBottom)
    ctx.stroke()

    if (!shouldDrawBeatLabel) continue
    const labelX = Math.max(originX + rulerLabelGap, x + rulerLabelGap)
    ctx.font = metrics.isBar ? rulerBarLabelFont : rulerBeatLabelFont
    ctx.fillStyle = metrics.fillStyle
    ctx.fillText(rulerBeatLabel(absoluteBeat), labelX, Math.min(labelY, tickBottom - tickHeight - 4))
  }

  ctx.restore()
}

function drawPianoRuler(ctx, width, clip) {
  const scale = pianoPxPerBeat.value
  const clipStart = Number(clip.start || 0)
  const visibleBeats = Math.ceil((width - pianoKeyW) / scale)
  const endBeat = clipStart + visibleBeats
  ctx.fillStyle = '#202326'
  ctx.fillRect(0, 0, width, pianoRulerH)
  ctx.fillStyle = '#181b1f'
  ctx.fillRect(0, 0, pianoKeyW, pianoRulerH)
  ctx.strokeStyle = 'rgba(229,236,245,0.11)'
  ctx.beginPath()
  ctx.moveTo(0, pianoRulerH - 0.5)
  ctx.lineTo(width, pianoRulerH - 0.5)
  ctx.stroke()

  ctx.font = '10px Cascadia Mono, Consolas, monospace'

  // Quarter-note grid lines
  const firstBeat = Math.ceil(clipStart - 0.000001)
  for (let absoluteBeat = firstBeat; absoluteBeat <= endBeat + 0.001; absoluteBeat += 1) {
    const x = pianoKeyW + (absoluteBeat - clipStart) * scale
    ctx.strokeStyle = 'rgba(229,236,245,0.1)'
    ctx.beginPath()
    ctx.moveTo(x, 0)
    ctx.lineTo(x, pianoRulerH)
    ctx.stroke()
  }

  // Beat-unit tick marks (only when they fall at non-integer positions and there's room)
  for (const segment of meterSegments(project.value, clipStart, endBeat)) {
    const unit = segment.beatUnit
    if (!Number.isFinite(unit) || unit <= 0 || Math.abs(unit - 1) < 0.0001 || unit * scale < 12) continue
    for (
      let absoluteBeat = firstMultipleAtOrAfter(segment.start, unit, segment.anchor);
      absoluteBeat <= segment.end + 0.001;
      absoluteBeat += unit
    ) {
      if (Math.abs(absoluteBeat - Math.round(absoluteBeat)) < 0.0001) continue // already drawn as quarter-note line
      const x = pianoKeyW + (absoluteBeat - clipStart) * scale
      ctx.strokeStyle = 'rgba(229,236,245,0.06)'
      ctx.beginPath()
      ctx.moveTo(x, 0)
      ctx.lineTo(x, pianoRulerH)
      ctx.stroke()
    }
  }

  for (const line of meterBarLinesBetween(project.value, clipStart, endBeat)) {
    const barBeat = line.beat
    const x = pianoKeyW + (barBeat - clipStart) * scale
    ctx.strokeStyle = 'rgba(240, 209, 122, 0.28)'
    ctx.beginPath()
    ctx.moveTo(x, 0)
    ctx.lineTo(x, pianoRulerH)
    ctx.stroke()
  }

  drawBeatRulerLabels(ctx, {
    startBeat: clipStart,
    endBeat,
    originX: pianoKeyW,
    scale,
    height: pianoRulerH,
    labelY: 16,
  })
}

function drawPianoMeterLane(ctx, width, clip) {
  if (!pianoMeterLaneVisible.value) return
  const scale = pianoPxPerBeat.value
  const clipStart = Number(clip.start || 0)
  const visibleBeats = Math.ceil((width - pianoKeyW) / scale)
  const endBeat = clipStart + visibleBeats
  const top = pianoMeterLaneTop.value
  const bottom = top + pianoMeterLaneH

  ctx.fillStyle = '#191d21'
  ctx.fillRect(0, top, width, pianoMeterLaneH)
  ctx.fillStyle = '#252b30'
  ctx.fillRect(0, top, pianoKeyW, pianoMeterLaneH)
  ctx.strokeStyle = 'rgba(229,236,245,0.12)'
  ctx.beginPath()
  ctx.moveTo(0, top + 0.5)
  ctx.lineTo(width, top + 0.5)
  ctx.moveTo(0, bottom - 0.5)
  ctx.lineTo(width, bottom - 0.5)
  ctx.stroke()

  ctx.fillStyle = '#aeb8c5'
  ctx.font = '10px Cascadia Mono, Consolas, monospace'
  ctx.fillText('Meter', 10, top + 17)

  for (const line of meterBarLinesBetween(project.value, clipStart, endBeat)) {
    const x = pianoKeyW + (line.beat - clipStart) * scale
    ctx.strokeStyle = 'rgba(240, 209, 122, 0.16)'
    ctx.beginPath()
    ctx.moveTo(x, top)
    ctx.lineTo(x, bottom)
    ctx.stroke()
  }

  for (const event of normalizeMeterEvents(project.value)) {
    if (event.beat < clipStart - 0.001 || event.beat > endBeat + 0.001) continue
    const x = pianoKeyW + (event.beat - clipStart) * scale
    ctx.fillStyle = 'rgba(240, 209, 122, 0.18)'
    roundRect(ctx, x - 3, top + 4, 6, pianoMeterLaneH - 8, 3)
    ctx.fill()
    ctx.fillStyle = '#f0d17a'
    ctx.font = '11px Cascadia Mono, Consolas, monospace'
    ctx.fillText(`${event.numerator}/${event.denominator}`, x + 5, top + 18)
  }
}

function drawPianoHarmonyLane(ctx, width, clip, top) {
  if (!pianoHarmonyLaneVisible.value) return
  const scale = pianoPxPerBeat.value
  const clipStart = Number(clip.start || 0)
  const visibleBeats = Math.ceil((width - pianoKeyW) / scale)
  const endBeat = clipStart + visibleBeats
  const bottom = top + pianoSubtrackH

  ctx.fillStyle = '#181c22'
  ctx.fillRect(0, top, width, pianoSubtrackH)
  ctx.fillStyle = '#252b30'
  ctx.fillRect(0, top, pianoKeyW, pianoSubtrackH)
  ctx.strokeStyle = 'rgba(229,236,245,0.12)'
  ctx.beginPath()
  ctx.moveTo(0, top + 0.5)
  ctx.lineTo(width, top + 0.5)
  ctx.moveTo(0, bottom - 0.5)
  ctx.lineTo(width, bottom - 0.5)
  ctx.stroke()

  ctx.fillStyle = '#aeb8c5'
  ctx.font = '10px Cascadia Mono, Consolas, monospace'
  ctx.fillText('Harmony', 10, top + 17)

  for (const line of meterBarLinesBetween(project.value, clipStart, endBeat)) {
    const x = pianoKeyW + (line.beat - clipStart) * scale
    ctx.strokeStyle = 'rgba(125, 168, 232, 0.12)'
    ctx.beginPath()
    ctx.moveTo(x, top)
    ctx.lineTo(x, bottom)
    ctx.stroke()
  }

  for (const event of editableHarmonyEvents()) {
    if (event.beat < clipStart - 0.001 || event.beat > endBeat + 0.001) continue
    const x = pianoKeyW + (event.beat - clipStart) * scale
    ctx.fillStyle = 'rgba(125, 168, 232, 0.18)'
    roundRect(ctx, x - 3, top + 4, 6, pianoSubtrackH - 8, 3)
    ctx.fill()
    ctx.fillStyle = '#b8d0ff'
    ctx.font = '11px Cascadia Mono, Consolas, monospace'
    ctx.fillText(event.text, x + 5, top + 18)
  }
}

function paintPianoGrid(ctx, width, height, clip) {
  const scale = pianoPxPerBeat.value
  const clipStart = Number(clip.start || 0)
  const visibleBeats = Math.ceil((width - pianoKeyW) / scale)
  const snapStepWidth = activePianoSnapStep.value ? activePianoSnapStep.value * scale : 0

  for (let beat = 0; beat <= visibleBeats; beat += 1) {
    const x = pianoKeyW + beat * scale
    ctx.strokeStyle = 'rgba(229,236,245,0.075)'
    ctx.lineWidth = 0.5
    ctx.beginPath()
    ctx.moveTo(x, pianoNoteTop.value)
    ctx.lineTo(x, height)
    ctx.stroke()

    if (snapStepWidth >= 4 && activePianoSnapStep.value && activePianoSnapStep.value < 1) {
      ctx.strokeStyle = 'rgba(229,236,245,0.035)'
      for (let subBeat = activePianoSnapStep.value; subBeat < 1; subBeat += activePianoSnapStep.value) {
        const subX = x + subBeat * scale
        ctx.beginPath()
        ctx.moveTo(subX, pianoNoteTop.value)
        ctx.lineTo(subX, height)
        ctx.stroke()
      }
    }
  }

  const endBeat = clipStart + visibleBeats
  for (const line of meterBarLinesBetween(project.value, clipStart, endBeat)) {
    const barBeat = line.beat
    const x = pianoKeyW + (barBeat - clipStart) * scale
    ctx.strokeStyle = 'rgba(240, 209, 122, 0.24)'
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(x, pianoNoteTop.value)
    ctx.lineTo(x, height)
    ctx.stroke()
  }
}

function paintGrid(ctx, width, height, offsetX, offsetY) {
  const scale = arrangementPxPerBeat.value
  const beats = Math.ceil((width - offsetX) / scale)

  for (let beat = 0; beat <= beats; beat += 1) {
    const x = offsetX + beat * scale
    ctx.strokeStyle = 'rgba(229,236,245,0.07)'
    ctx.lineWidth = 0.5
    ctx.beginPath()
    ctx.moveTo(x, offsetY)
    ctx.lineTo(x, height)
    ctx.stroke()

    ctx.strokeStyle = 'rgba(229,236,245,0.035)'
    for (let div = 1; div < 4; div += 1) {
      const subX = x + (div * scale) / 4
      ctx.beginPath()
      ctx.moveTo(subX, offsetY)
      ctx.lineTo(subX, height)
      ctx.stroke()
    }
  }

  // Bar lines follow the project meter map so piano-roll meter changes affect the arrangement grid.
  for (const line of meterBarLinesBetween(project.value, 0, beats)) {
    const barX = offsetX + line.beat * scale
    ctx.strokeStyle = 'rgba(229,236,245,0.18)'
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(barX, offsetY)
    ctx.lineTo(barX, height)
    ctx.stroke()
  }
}

function firstMultipleAtOrAfter(value, step, origin = 0) {
  return origin + Math.ceil(((Number(value || 0) - origin) - 0.000001) / step) * step
}

function drawPlayhead(ctx, height, offsetX = 0) {
  const x = offsetX + visualPositionBeats.value * arrangementPxPerBeat.value
  ctx.strokeStyle = '#d7b66f'
  ctx.lineWidth = 1.5
  ctx.beginPath()
  ctx.moveTo(x, 0)
  ctx.lineTo(x, height)
  ctx.stroke()
  ctx.fillStyle = '#d7b66f'
  ctx.beginPath()
  ctx.moveTo(x, arrangementRulerH)
  ctx.lineTo(x - 5, arrangementRulerH - 8)
  ctx.lineTo(x + 5, arrangementRulerH - 8)
  ctx.closePath()
  ctx.fill()
}

function drawPianoPlayhead(ctx, height, clip) {
  const localBeat = visualPositionBeats.value - Number(clip.start || 0)
  const visibleLength = pianoLengthBeats(clip)
  if (localBeat < 0 || localBeat > visibleLength) return
  const x = pianoKeyW + localBeat * pianoPxPerBeat.value
  ctx.strokeStyle = '#f0d17a'
  ctx.lineWidth = 1.6
  ctx.beginPath()
  ctx.moveTo(x, 0)
  ctx.lineTo(x, height)
  ctx.stroke()
  ctx.fillStyle = '#f0d17a'
  ctx.beginPath()
  ctx.moveTo(x, pianoRulerH)
  ctx.lineTo(x - 5, pianoRulerH - 8)
  ctx.lineTo(x + 5, pianoRulerH - 8)
  ctx.closePath()
  ctx.fill()
}

function roundRect(ctx, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2)
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.arcTo(x + width, y, x + width, y + height, r)
  ctx.arcTo(x + width, y + height, x, y + height, r)
  ctx.arcTo(x, y + height, x, y, r)
  ctx.arcTo(x, y, x + width, y, r)
  ctx.closePath()
}

function zrythmRegionContentColor() {
  return 'rgba(246, 250, 255, 0.84)'
}

function zrythmRegionOutlineColor() {
  return 'rgba(255, 255, 255, 0.96)'
}

function hexToRgba(hex, alpha) {
  const { r, g, b } = hexToRgb(hex)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

function hexToRgb(hex) {
  const safe = /^#[0-9a-f]{6}$/i.test(hex) ? hex : '#4e79ff'
  const value = parseInt(safe.slice(1), 16)
  const r = (value >> 16) & 255
  const g = (value >> 8) & 255
  const b = value & 255
  return { r, g, b }
}

function mixHexColor(hex, targetHex, amount) {
  const source = hexToRgb(hex)
  const target = hexToRgb(targetHex)
  const unit = clamp(amount, 0, 1)
  const mixed = {
    r: Math.round(source.r + (target.r - source.r) * unit),
    g: Math.round(source.g + (target.g - source.g) * unit),
    b: Math.round(source.b + (target.b - source.b) * unit),
  }
  return rgbToHex(mixed)
}

function rgbToHex({ r, g, b }) {
  return `#${[r, g, b].map(value => value.toString(16).padStart(2, '0')).join('')}`
}

function pitchName(pitch) {
  const names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
  return `${names[pitch % 12]}${Math.floor(pitch / 12) - 1}`
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value))
}

onMounted(async () => {
  await loadProject()
  openFirstMidiClip()
  await refreshHostStatus()
  await loadPlugins()
  await nextTick()
  resizeObserver = new ResizeObserver(() => {
    if (pianoPanelHeight.value) {
      pianoPanelHeight.value = clampPianoPanelHeight(pianoPanelHeight.value)
    }
    drawAll()
  })
  if (editorStack.value) resizeObserver.observe(editorStack.value)
  if (arrangementWrap.value) resizeObserver.observe(arrangementWrap.value)
  if (pianoWrap.value) resizeObserver.observe(pianoWrap.value)
  if (controllerWrap.value) resizeObserver.observe(controllerWrap.value)
  raf = requestAnimationFrame(animationLoop)
  document.addEventListener('pointerdown', onDocumentPointerDown)
  learnedParameterPollTimer = setInterval(() => {
    if (host.value?.running) pollCapturedPluginParameters().catch(() => null)
  }, 1500)
})

onUnmounted(() => {
  document.removeEventListener('pointerdown', onDocumentPointerDown)
  clearTimeout(tempoUpdateTimer)
  clearPianoLongPressTimer()
  clearInterval(learnedParameterPollTimer)
  if (resizeObserver) resizeObserver.disconnect()
  unbindPianoDrag()
  unbindPianoResize()
  unbindTrackListResize()
  unbindArrangementDrag()
  unbindControllerDrag()
  unbindAutomationDrag()
  if (audioDecodeContext?.close) audioDecodeContext.close()
  cancelAnimationFrame(raf)
})

watch(project, (nextProject) => {
  syncPianoSubtrackLanes(nextProject)
  syncTransportDisplayFields(nextProject)
  if (activeClipId.value && !findClipRecord(activeClipId.value)) {
    activeClipId.value = null
    if (lowerEditorMode.value === 'piano') lowerEditorMode.value = null
    selectedNoteIds.value = new Set()
  }
  drawAll()
}, { immediate: true })
watch(activeTrack, () => {
  selectedNoteIds.value = new Set()
  drawAll()
})
watch(positionBeats, (value) => {
  visualPositionBeats.value = value
  syncTransportDisplayFields(project.value)
  drawAll()
})
</script>

<style scoped>
.studio-page {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  width: 100%;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
  color: var(--t1);
  background: #17191c;
}

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

.transport,
.host-controls,
.piano-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.host-controls {
  justify-content: flex-end;
}

.tool-btn,
.mini-btn {
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

.mini-btn {
  width: 28px;
  height: 28px;
  min-width: 28px;
}

.tool-btn:hover,
.mini-btn:hover {
  color: var(--t1);
  background: #343b42;
  border-color: rgba(229, 236, 245, 0.22);
}

.tool-btn:disabled,
.mini-btn:disabled {
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

.tool-btn.text,
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

.tool-btn svg,
.mini-btn svg {
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

.host-dot.audio {
  background: #f0d17a;
  box-shadow: 0 0 0 3px rgba(240, 209, 122, 0.14);
}

.host-label {
  color: var(--t3);
  font-size: 12px;
}

.studio-error {
  padding: 8px 14px;
  background: rgba(255, 141, 127, 0.12);
  border-bottom: 1px solid rgba(255, 141, 127, 0.24);
  color: var(--red);
  font-family: var(--mono);
  font-size: 12px;
}

.studio-body {
  flex: 1;
  min-height: 0;
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 286px;
  overflow: hidden;
}

.studio-page.inspector-hidden .studio-body {
  grid-template-columns: minmax(0, 1fr);
}

.inspector {
  min-height: 0;
  overflow: auto;
  background: #202428;
}

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

.inspector {
  border-left: 1px solid rgba(229, 236, 245, 0.12);
}

.track-list-head,
.arrangement-toolbar,
.piano-head,
.section-title {
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

.arrangement-head-grid {
  flex: 0 0 auto;
  min-width: 0;
  display: grid;
  grid-template-columns: var(--track-list-width) minmax(0, 1fr);
}

.track-list-head {
  gap: 6px;
}

.track-list-head span {
  flex: 1 1 auto;
}

.modal-backdrop {
  position: absolute;
  inset: 0;
  z-index: 40;
  display: grid;
  place-items: center;
  padding: 18px;
  background: rgba(9, 11, 13, 0.66);
  backdrop-filter: blur(7px);
}

.track-create-dialog {
  width: min(420px, 100%);
  max-height: calc(100% - 24px);
  overflow: auto;
  border: 1px solid rgba(229, 236, 245, 0.16);
  border-radius: 8px;
  background: #202428;
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.46);
}

.track-create-dialog-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px 16px 12px;
  border-bottom: 1px solid rgba(229, 236, 245, 0.1);
}

.track-create-dialog-head span {
  color: var(--orange);
  font-family: var(--mono);
  font-size: 10px;
  text-transform: uppercase;
}

.track-create-dialog-head h2 {
  margin: 2px 0 0;
  color: var(--t1);
  font-size: 17px;
  line-height: 1.2;
}

.track-create-form {
  display: grid;
  gap: 12px;
  padding: 16px;
}

.track-create-field {
  display: grid;
  grid-template-columns: 86px minmax(0, 1fr);
  align-items: center;
  gap: 10px;
}

.track-create-field span {
  color: var(--t4);
  font-size: 10px;
  text-transform: uppercase;
}

.track-create-field input,
.track-create-field select,
.automation-parameter-button {
  min-width: 0;
  width: 100%;
  height: 32px;
  border: 1px solid rgba(229, 236, 245, 0.14);
  border-radius: 6px;
  background: #101215;
  color: var(--t2);
  font-size: 12px;
}

.automation-parameter-button {
  padding: 0 10px;
  text-align: left;
  cursor: pointer;
}

.track-create-field input {
  padding: 0 10px;
}

.track-create-color-control {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 10px;
}

.track-create-color-field input[type='color'] {
  flex: 0 0 40px;
  width: 40px;
  padding: 2px;
  cursor: pointer;
}

.track-create-swatches {
  min-width: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.track-create-swatch {
  width: 22px;
  height: 22px;
  border: 1px solid rgba(255, 255, 255, 0.24);
  border-radius: 5px;
  cursor: pointer;
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, 0.24);
}

.track-create-swatch.active {
  border-color: rgba(240, 209, 122, 0.92);
  box-shadow:
    0 0 0 2px rgba(240, 209, 122, 0.16),
    inset 0 0 0 1px rgba(0, 0, 0, 0.26);
}

.track-create-field input:focus,
.track-create-field select:focus,
.automation-parameter-button:focus {
  outline: none;
  border-color: rgba(240, 209, 122, 0.5);
  box-shadow: 0 0 0 2px rgba(240, 209, 122, 0.12);
}

.track-create-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 16px 16px;
  border-top: 1px solid rgba(229, 236, 245, 0.08);
}

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
}

.track-lane-spacer {
  height: 30px;
  border-bottom: 1px solid rgba(229, 236, 245, 0.08);
  background: #1b1f23;
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
  font-size: 13px;
}

.track-title-text {
  color: var(--t1);
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

.editor-stack {
  min-width: 0;
  min-height: 0;
  display: grid;
  grid-template-rows: minmax(220px, 1fr) minmax(180px, 42%);
  overflow: hidden;
  background: #17191c;
}

.studio-page.piano-closed .editor-stack {
  grid-template-rows: minmax(0, 1fr);
}

.arrangement-canvas-wrap,
.piano-canvas-wrap {
  min-width: 0;
  min-height: 0;
  overflow: auto;
}

.arrangement {
  --track-list-width: 246px;
  position: relative;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border-bottom: 1px solid rgba(229, 236, 245, 0.14);
}

.arrangement-canvas-wrap {
  flex: 1 1 auto;
  position: relative;
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

.arrangement-canvas {
  grid-column: 2;
  grid-row: 1;
  min-width: 0;
}

.editor-canvas {
  display: block;
  min-width: 100%;
}

.piano-panel {
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.piano-resize-handle {
  position: relative;
  flex: 0 0 8px;
  border-top: 1px solid rgba(229, 236, 245, 0.14);
  border-bottom: 1px solid rgba(229, 236, 245, 0.08);
  background: #202428;
  cursor: ns-resize;
  touch-action: none;
}

.piano-resize-handle::before {
  content: '';
  position: absolute;
  inset: -5px 0;
}

.piano-resize-handle span {
  position: absolute;
  left: 50%;
  top: 3px;
  width: 42px;
  height: 2px;
  border-radius: 999px;
  background: rgba(229, 236, 245, 0.22);
  transform: translateX(-50%);
}

.piano-resize-handle:hover span {
  background: rgba(240, 209, 122, 0.72);
}

.piano-canvas-wrap {
  flex: 1 1 auto;
  cursor: crosshair;
}

.piano-workspace {
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.piano-meter-popover {
  position: absolute;
  z-index: 24;
  width: 166px;
  display: grid;
  gap: 7px;
  padding: 8px;
  border: 1px solid rgba(229, 236, 245, 0.18);
  border-radius: 7px;
  background: #24282c;
  box-shadow: 0 16px 38px rgba(0, 0, 0, 0.42);
}

.piano-meter-popover label {
  display: grid;
  grid-template-columns: 56px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
}

.piano-meter-popover span {
  color: var(--t4);
  font-size: 10px;
  text-transform: uppercase;
}

.piano-meter-popover input,
.piano-meter-popover select {
  height: 26px;
  min-width: 0;
  border: 1px solid rgba(229, 236, 245, 0.12);
  border-radius: 4px;
  background: #101215;
  color: var(--t1);
  font-family: var(--mono);
  font-size: 11px;
}

.piano-meter-popover input {
  width: 100%;
  padding: 0 7px;
}

.piano-meter-popover select {
  width: 100%;
  padding: 0 6px;
}

.piano-meter-popover input:focus,
.piano-meter-popover select:focus {
  outline: none;
  border-color: rgba(240, 209, 122, 0.5);
  box-shadow: 0 0 0 2px rgba(240, 209, 122, 0.12);
}

.piano-harmony-popover {
  position: absolute;
  z-index: 24;
  width: 210px;
  display: grid;
  gap: 7px;
  padding: 8px;
  border: 1px solid rgba(229, 236, 245, 0.18);
  border-radius: 7px;
  background: #24282c;
  box-shadow: 0 16px 38px rgba(0, 0, 0, 0.42);
}

.piano-harmony-popover label {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
}

.piano-harmony-popover span {
  color: var(--t4);
  font-size: 10px;
  text-transform: uppercase;
}

.piano-harmony-popover input {
  width: 100%;
  height: 26px;
  min-width: 0;
  padding: 0 7px;
  border: 1px solid rgba(229, 236, 245, 0.12);
  border-radius: 4px;
  background: #101215;
  color: var(--t1);
  font-family: var(--mono);
  font-size: 11px;
}

.piano-harmony-popover input:focus {
  outline: none;
  border-color: rgba(125, 168, 232, 0.5);
  box-shadow: 0 0 0 2px rgba(125, 168, 232, 0.12);
}

.piano-meter-toggle {
  position: absolute;
  right: 10px;
  z-index: 22;
  width: 20px;
  height: 20px;
  padding: 0;
  border: 1px solid rgba(240, 209, 122, 0.28);
  border-radius: 4px;
  background: rgba(25, 29, 33, 0.82);
  color: #f0d17a;
  cursor: pointer;
}

.piano-meter-toggle::before {
  content: '';
  position: absolute;
  left: 7px;
  top: 5px;
  width: 0;
  height: 0;
  border-top: 5px solid transparent;
  border-bottom: 5px solid transparent;
  border-left: 7px solid currentColor;
}

.piano-meter-toggle:hover,
.piano-meter-toggle:focus-visible {
  border-color: rgba(240, 209, 122, 0.62);
  background: rgba(40, 45, 50, 0.96);
  outline: none;
}

.piano-harmony-toggle {
  position: absolute;
  right: 10px;
  z-index: 22;
  width: 20px;
  height: 20px;
  padding: 0;
  border: 1px solid rgba(125, 168, 232, 0.28);
  border-radius: 4px;
  background: rgba(25, 29, 33, 0.82);
  color: #b8d0ff;
  cursor: pointer;
}

.piano-harmony-toggle::before {
  content: '';
  position: absolute;
  left: 7px;
  top: 5px;
  width: 0;
  height: 0;
  border-top: 5px solid transparent;
  border-bottom: 5px solid transparent;
  border-left: 7px solid currentColor;
}

.piano-harmony-toggle:hover,
.piano-harmony-toggle:focus-visible {
  border-color: rgba(125, 168, 232, 0.62);
  background: rgba(40, 45, 50, 0.96);
  outline: none;
}

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

.piano-head {
  flex: 0 0 auto;
  text-transform: none;
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
  flex-wrap: wrap;
  justify-content: flex-end;
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

.inspector-section {
  border-bottom: 1px solid rgba(229, 236, 245, 0.1);
}

.engine-stats {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1px;
  background: rgba(229, 236, 245, 0.06);
}

.engine-stats div {
  padding: 9px 10px;
  background: #202428;
}

.engine-stats dt {
  color: var(--t4);
  font-size: 10px;
  text-transform: uppercase;
}

.engine-stats dd {
  margin-top: 2px;
  color: var(--t2);
  font-family: var(--mono);
  font-size: 12px;
}

.mixer-panel {
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: #17191c;
}

.mixer-head {
  flex: 0 0 auto;
  min-height: 40px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 7px 10px;
  border-bottom: 1px solid rgba(229, 236, 245, 0.12);
  background: #202428;
}

.mixer-head div:first-child,
.mixer-actions {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.mixer-head span {
  color: var(--t3);
  font-size: 11px;
  text-transform: uppercase;
}

.mixer-head strong {
  color: var(--t1);
  font-size: 12px;
}

.mixer-actions {
  align-items: center;
}

.mixer-strip-body {
  flex: 1 1 auto;
  min-height: 0;
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 154px;
  overflow: hidden;
  background: #14171a;
}

.mixer-track-strip-scroll {
  min-height: 0;
  min-width: 0;
  overflow: auto;
  background: #14171a;
}

.mixer-strip-row {
  height: 100%;
  width: max-content;
  min-width: 100%;
  display: flex;
  align-items: stretch;
}

.mixer-strip {
  width: 154px;
  min-width: 154px;
  height: 100%;
  min-height: 0;
  display: grid;
  grid-template-rows: auto minmax(48px, 1fr) auto minmax(40px, auto) auto 132px;
  gap: 8px;
  padding: 8px;
  border-right: 1px solid rgba(229, 236, 245, 0.1);
  background: #202428;
  color: var(--t2);
}

.mixer-strip.active {
  background: #252a2f;
  box-shadow: inset 0 2px 0 rgba(240, 209, 122, 0.7);
}

.mixer-strip-head {
  min-width: 0;
  display: grid;
  grid-template-columns: 4px minmax(0, 1fr);
  gap: 3px 7px;
  align-items: center;
}

.mixer-strip-head .track-color,
.master-strip-color {
  grid-row: 1 / 3;
  width: 4px;
  height: 100%;
  min-height: 28px;
  border-radius: 999px;
}

.master-strip-color {
  display: block;
  background: #58a7b8;
}

.mixer-strip-head strong,
.mixer-strip-head small {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mixer-strip-head strong {
  color: var(--t1);
  font-size: 12px;
}

.mixer-strip-head small {
  color: var(--t4);
  font-size: 10px;
}

.mixer-section {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.mixer-section-label {
  color: var(--t4);
  font-family: var(--mono);
  font-size: 10px;
  text-transform: uppercase;
}

.mixer-inserts {
  min-height: 0;
  overflow-y: auto;
}

.mixer-sends {
  max-height: 92px;
  overflow-y: auto;
}

.mixer-insert-slot {
  min-width: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 4px;
  padding: 5px;
  border: 1px solid rgba(229, 236, 245, 0.09);
  border-radius: 5px;
  background: #181b1f;
}

.mixer-insert-slot span {
  min-width: 0;
  overflow: hidden;
  color: var(--t3);
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mixer-insert-slot select,
.mixer-send-row select,
.mixer-send-add {
  min-width: 0;
  width: 100%;
  height: 24px;
  overflow: hidden;
  border: 1px solid rgba(229, 236, 245, 0.12);
  border-radius: 4px;
  background: #101215;
  color: var(--t2);
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mixer-insert-slot.empty select {
  color: var(--t4);
}

.mixer-param-load,
.mixer-send-row button {
  height: 22px;
  border: 1px solid rgba(229, 236, 245, 0.12);
  border-radius: 4px;
  background: #15181b;
  color: var(--t3);
  cursor: pointer;
  font-size: 10px;
}

.mixer-param-load:disabled {
  cursor: not-allowed;
  opacity: 0.52;
}

.mixer-params {
  display: grid;
  gap: 4px;
  padding: 5px;
  border: 1px solid rgba(229, 236, 245, 0.08);
  border-radius: 5px;
  background: rgba(13, 15, 18, 0.56);
}

.mixer-param-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 3px;
}

.mixer-param-row span,
.mixer-param-row small {
  min-width: 0;
  overflow: hidden;
  color: var(--t3);
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mixer-param-row input {
  min-width: 0;
}

.mixer-param-row small {
  color: var(--t4);
  font-family: var(--mono);
}

.mixer-send-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 42px 22px 22px;
  gap: 4px;
  align-items: center;
}

.mixer-send-row input {
  min-width: 0;
  accent-color: #58a7b8;
}

.mixer-send-row button.active {
  color: #fff;
  border-color: rgba(88, 167, 184, 0.56);
  background: #0d74c9;
}

.mixer-pan {
  min-height: 40px;
  min-width: 0;
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 4px 6px;
  align-items: center;
  color: var(--t4);
  font-family: var(--mono);
  font-size: 10px;
}

.mixer-pan-control {
  position: relative;
  min-width: 0;
  display: flex;
  align-items: center;
}

.mixer-pan-center-line {
  position: absolute;
  left: 50%;
  top: 2px;
  bottom: 2px;
  width: 2px;
  border-radius: 999px;
  background: #0d74c9;
  pointer-events: none;
  transform: translateX(-50%);
}

.mixer-pan input {
  width: 100%;
  min-width: 0;
  accent-color: #9ebfff;
}

.mixer-pan strong {
  grid-column: 1 / -1;
  color: var(--t2);
  font-size: 10px;
  text-align: center;
}

.mixer-strip-buttons {
  display: flex;
  justify-content: center;
  gap: 6px;
}

.mixer-fader {
  min-height: 132px;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  justify-items: center;
  gap: 6px;
}

.mixer-fader span {
  color: var(--t3);
  font-family: var(--mono);
  font-size: 10px;
}

.mixer-fader input {
  width: 108px;
  align-self: center;
  accent-color: #58a7b8;
  transform: rotate(-90deg);
}

.mixer-master-dock {
  min-height: 0;
  min-width: 0;
  overflow: hidden;
}

.master-strip {
  height: 100%;
  grid-template-rows: auto minmax(48px, 1fr) minmax(40px, auto) auto 132px;
  border-left: 1px solid rgba(88, 167, 184, 0.32);
  background: #252b30;
  box-shadow: -12px 0 22px rgba(0, 0, 0, 0.26);
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

  .studio-body {
    grid-template-columns: minmax(0, 1fr);
  }

  .inspector {
    display: none;
  }

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

.studio-page.embedded {
  background: #17191c;
  border: 0;
}

.studio-page.embedded .studio-topbar {
  height: auto;
  min-height: 104px;
  grid-template-columns: 1fr;
  align-items: stretch;
  gap: 8px;
  padding: 9px;
  background: #202428;
}

.studio-page.embedded .session-title {
  min-width: 0;
}

.studio-page.embedded .session-title strong {
  font-size: 13px;
}

.studio-page.embedded .transport {
  justify-content: space-between;
  gap: 6px;
}

.studio-page.embedded .clock {
  min-width: 0;
  flex: 1;
  padding: 0 7px;
  font-size: 12px;
}

.studio-page.embedded .tempo-box,
.studio-page.embedded .time-signature-picker {
  display: none;
}

.studio-page.embedded .host-controls {
  justify-content: space-between;
  gap: 6px;
}

.studio-page.embedded .host-label {
  display: none;
}

.studio-page.embedded .tool-btn.text {
  padding: 0 8px;
  font-size: 11px;
}

.studio-page.embedded .studio-body {
  grid-template-columns: minmax(0, 1fr);
}

.studio-page.embedded .inspector {
  display: none;
}

.studio-page.embedded .editor-stack {
  grid-template-rows: minmax(118px, 44%) minmax(140px, 56%);
}

.studio-page.embedded.piano-closed .editor-stack {
  grid-template-rows: minmax(0, 1fr);
}

.studio-page.embedded .piano-head {
  height: 32px;
  padding: 0 8px;
}

.studio-page.embedded .piano-head div:first-child {
  min-width: 0;
}

.studio-page.embedded .piano-head strong {
  max-width: 112px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.studio-page.embedded .piano-actions {
  gap: 4px;
  overflow: auto;
}

.studio-page.embedded .studio-error {
  padding: 6px 9px;
  font-size: 11px;
}
</style>
