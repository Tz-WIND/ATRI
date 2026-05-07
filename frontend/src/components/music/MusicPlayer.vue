<template>
  <div v-if="currentSong">
    <!-- Hidden state: invisible hover zone at bottom center, pill appears on hover, click to restore -->
    <div
      v-if="collapsed"
      class="player-peek-zone"
      @mouseenter="peekVisible = true"
      @mouseleave="peekVisible = false"
    >
      <Transition name="peek-fade">
        <div
          v-if="peekVisible"
          class="peek-pill"
          @click="collapsed = false; peekVisible = false"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          ><path d="M18 15l-6-6-6 6" /></svg>
        </div>
      </Transition>
    </div>

    <!-- Player bar -->
    <Transition name="player-slide">
      <div
        v-show="!collapsed"
        class="music-player"
        @click.self="showFullPlayer = true"
      >
        <!-- Collapse button -->
        <button
          class="collapse-btn"
          title="Hide player bar"
          @click.stop="collapsed = true"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
          ><path d="M6 9l6 6 6-6" /></svg>
        </button>

        <!-- Progress bar -->
        <div
          ref="progressTrack"
          class="player-progress-track"
          @click.stop="onProgressClick"
        >
          <div
            class="player-progress-fill"
            :style="{ width: progress + '%' }"
          />
        </div>

        <div class="player-inner">
          <!-- Left: cover + info -->
          <div
            class="player-left"
            @click="showFullPlayer = true"
          >
            <div class="player-cover-wrap">
              <img
                v-if="currentSong.has_cover"
                :src="coverUrl(currentSong.id)"
                class="player-cover"
              >
              <div
                v-else
                class="player-cover placeholder"
              >
                <svg
                  viewBox="0 0 24 24"
                  fill="currentColor"
                  opacity="0.4"
                ><path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55C7.79 13 6 14.79 6 17s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z" /></svg>
              </div>
            </div>
            <div class="player-info">
              <div class="player-song-name">
                {{ currentSong.title }}
              </div>
              <div class="player-artist">
                {{ currentSong.artist }}
              </div>
            </div>
            <div
              v-if="currentSong.lossless"
              class="player-quality"
            >
              <span
                v-if="currentSong.bit_depth >= 24 || currentSong.sample_rate > 48000"
                class="quality-badge"
              >Hi-Res</span>
              <span
                v-else
                class="quality-badge"
              >Lossless</span>
            </div>
          </div>

          <!-- Center: controls -->
          <div
            class="player-center"
            @click.stop
          >
            <button
              class="ctrl-btn small mode-btn"
              :class="{ active: playMode !== 'sequential' }"
              :title="modeLabel"
              @click="cyclePlayMode"
            >
              <!-- sequential -->
              <svg
                v-if="playMode === 'sequential'"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              ><polyline points="17 1 21 5 17 9" /><path d="M3 11V9a4 4 0 014-4h14" /><polyline points="7 23 3 19 7 15" /><path d="M21 13v2a4 4 0 01-4 4H3" /></svg>
              <!-- shuffle -->
              <svg
                v-else-if="playMode === 'shuffle'"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              ><polyline points="16 3 21 3 21 8" /><line
                x1="4"
                y1="20"
                x2="21"
                y2="3"
              /><polyline points="21 16 21 21 16 21" /><line
                x1="15"
                y1="15"
                x2="21"
                y2="21"
              /><line
                x1="4"
                y1="4"
                x2="9"
                y2="9"
              /></svg>
              <!-- repeat-one -->
              <svg
                v-else
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              ><polyline points="17 1 21 5 17 9" /><path d="M3 11V9a4 4 0 014-4h14" /><polyline points="7 23 3 19 7 15" /><path d="M21 13v2a4 4 0 01-4 4H3" /><text
                x="10"
                y="16"
                font-size="9"
                fill="currentColor"
                stroke="none"
                font-weight="bold"
              >1</text></svg>
            </button>
            <button
              class="ctrl-btn"
              title="Previous"
              @click="prev"
            >
              <svg
                viewBox="0 0 24 24"
                fill="currentColor"
              ><path d="M6 6h2v12H6zm3.5 6l8.5 6V6z" /></svg>
            </button>
            <button
              class="ctrl-btn play-btn"
              :title="playing ? 'Pause' : 'Play'"
              @click="togglePlay"
            >
              <svg
                v-if="playing"
                viewBox="0 0 24 24"
                fill="currentColor"
              ><rect
                x="6"
                y="4"
                width="4"
                height="16"
                rx="1"
              /><rect
                x="14"
                y="4"
                width="4"
                height="16"
                rx="1"
              /></svg>
              <svg
                v-else
                viewBox="0 0 24 24"
                fill="currentColor"
              ><polygon points="5,3 19,12 5,21" /></svg>
            </button>
            <button
              class="ctrl-btn"
              title="Next"
              @click="next"
            >
              <svg
                viewBox="0 0 24 24"
                fill="currentColor"
              ><path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z" /></svg>
            </button>
          </div>

          <!-- Right: time + volume -->
          <div
            class="player-right"
            @click.stop
          >
            <span class="player-time">{{ currentTimeStr }} / {{ durationStr }}</span>
            <div class="volume-wrap">
              <button
                class="ctrl-btn small"
                @click="toggleMute"
              >
                <svg
                  v-if="volume > 50"
                  viewBox="0 0 24 24"
                  fill="currentColor"
                ><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z" /></svg>
                <svg
                  v-else-if="volume > 0"
                  viewBox="0 0 24 24"
                  fill="currentColor"
                ><path d="M18.5 12c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM5 9v6h4l5 5V4L9 9H5z" /></svg>
                <svg
                  v-else
                  viewBox="0 0 24 24"
                  fill="currentColor"
                ><path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z" /></svg>
              </button>
              <input
                type="range"
                class="volume-slider"
                min="0"
                max="100"
                :value="volume"
                @input="setVolume(+$event.target.value)"
              >
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useMusic } from '@/composables/useMusic.js'

const {
  currentSong, playing, progress, volume, playMode,
  currentTimeStr, durationStr, showFullPlayer, playerCollapsed: collapsed,
  togglePlay, next, prev, seek, setVolume, coverUrl, cyclePlayMode,
} = useMusic()

const modeLabel = computed(() => {
  const labels = { sequential: 'Sequential', shuffle: 'Shuffle', 'repeat-one': 'Repeat One' }
  return labels[playMode.value] || 'Sequential'
})

const progressTrack = ref(null)
const peekVisible = ref(false)
let prevVolume = 80

function onProgressClick(e) {
  if (!progressTrack.value) return
  const rect = progressTrack.value.getBoundingClientRect()
  const pct = ((e.clientX - rect.left) / rect.width) * 100
  seek(pct)
}

function toggleMute() {
  if (volume.value > 0) {
    prevVolume = volume.value
    setVolume(0)
  } else {
    setVolume(prevVolume || 80)
  }
}
</script>

<style scoped>
.music-player {
  position: fixed;
  bottom: 0;
  left: var(--activity-w);
  right: 0;
  background: rgba(30, 30, 30, 0.85);
  backdrop-filter: blur(30px) saturate(180%);
  -webkit-backdrop-filter: blur(30px) saturate(180%);
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  z-index: 100;
  cursor: pointer;
}

/* Collapse chevron button */
.collapse-btn {
  position: absolute;
  top: -14px;
  right: 16px;
  width: 28px;
  height: 14px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(30, 30, 30, 0.85);
  backdrop-filter: blur(20px);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-bottom: none;
  border-radius: 6px 6px 0 0;
  color: var(--t3);
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.2s, color 0.15s;
  z-index: 2;
}
.music-player:hover .collapse-btn {
  opacity: 1;
}
.collapse-btn:hover {
  color: var(--t1);
}
.collapse-btn svg {
  width: 14px;
  height: 14px;
}

/* Peek zone — invisible trigger area at the bottom center */
.player-peek-zone {
  position: fixed;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 120px;
  height: 24px;
  z-index: 100;
  display: flex;
  align-items: flex-end;
  justify-content: center;
}

.peek-pill {
  width: 40px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(30, 30, 30, 0.8);
  backdrop-filter: blur(20px);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-bottom: none;
  border-radius: 8px 8px 0 0;
  color: var(--t3);
  transition: color 0.15s, background 0.15s;
  cursor: pointer;
}
.peek-pill:hover {
  color: var(--t1);
  background: rgba(50, 50, 50, 0.9);
}
.peek-pill svg {
  width: 14px;
  height: 14px;
}

.player-progress-track {
  height: 3px;
  background: rgba(255, 255, 255, 0.08);
  cursor: pointer;
  position: relative;
}
.player-progress-track:hover {
  height: 5px;
  margin-top: -2px;
}
.player-progress-fill {
  height: 100%;
  background: #ff2d55;
  border-radius: 0 2px 2px 0;
  transition: width 0.3s linear;
}

.player-inner {
  display: flex;
  align-items: center;
  padding: 8px 16px;
  gap: 16px;
  height: 64px;
}

.player-left {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
  min-width: 0;
  cursor: pointer;
}

.player-cover-wrap {
  flex-shrink: 0;
}

.player-cover {
  width: 44px;
  height: 44px;
  border-radius: 6px;
  object-fit: cover;
  box-shadow: 0 2px 8px rgba(0,0,0,0.3);
}
.player-cover.placeholder {
  width: 44px;
  height: 44px;
  border-radius: 6px;
  background: var(--bg2);
  display: flex;
  align-items: center;
  justify-content: center;
}
.player-cover.placeholder svg { width: 24px; height: 24px; }

.player-info {
  min-width: 0;
  flex: 1;
}

.player-song-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--t1);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.player-artist {
  font-size: 11px;
  color: var(--t3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.player-quality {
  flex-shrink: 0;
}
.quality-badge {
  font-size: 9px;
  padding: 2px 6px;
  border-radius: 3px;
  font-weight: 700;
  letter-spacing: 0.3px;
  text-transform: uppercase;
  background: linear-gradient(135deg, rgba(175, 130, 255, 0.2), rgba(100, 210, 255, 0.2));
  color: #af82ff;
  border: 1px solid rgba(175, 130, 255, 0.3);
}

.player-center {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.ctrl-btn {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  color: var(--t2);
  cursor: pointer;
  border-radius: 50%;
  transition: all 0.12s;
}
.ctrl-btn:hover { color: var(--t1); background: rgba(255,255,255,0.06); }
.ctrl-btn svg { width: 20px; height: 20px; }
.ctrl-btn.small { width: 28px; height: 28px; }
.ctrl-btn.small svg { width: 16px; height: 16px; }

.mode-btn.active {
  color: #ff2d55;
}

.play-btn {
  width: 40px;
  height: 40px;
  background: rgba(255, 45, 85, 0.15);
  color: #ff2d55;
}
.play-btn:hover {
  background: rgba(255, 45, 85, 0.25);
}
.play-btn svg { width: 22px; height: 22px; }

.player-right {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}

.player-time {
  font-size: 11px;
  font-family: var(--mono);
  color: var(--t3);
  white-space: nowrap;
}

.volume-wrap {
  display: flex;
  align-items: center;
  gap: 4px;
}

.volume-slider {
  width: 80px;
  height: 4px;
  -webkit-appearance: none;
  appearance: none;
  background: var(--bg3);
  border-radius: 2px;
  outline: none;
}
.volume-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--t1);
  cursor: pointer;
}

/* Slide animation for the player bar */
.player-slide-enter-active,
.player-slide-leave-active {
  transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}
.player-slide-enter-from,
.player-slide-leave-to {
  transform: translateY(100%);
}

/* Fade for the peek pill */
.peek-fade-enter-active,
.peek-fade-leave-active {
  transition: opacity 0.25s ease;
}
.peek-fade-enter-from,
.peek-fade-leave-to {
  opacity: 0;
}
</style>
