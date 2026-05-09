<template>
  <Transition name="fullplayer">
    <div
      v-if="showFullPlayer"
      class="full-player"
      @click.self="showFullPlayer = false"
    >
      <!-- Cover-driven glass background -->
      <div
        class="fp-bg"
        :class="{ 'no-cover': !currentSong?.has_cover }"
        aria-hidden="true"
      >
        <img
          v-if="currentSong?.has_cover"
          :src="coverUrl(currentSong.id)"
          class="fp-bg-img fp-bg-img-soft"
          alt=""
        >
        <img
          v-if="currentSong?.has_cover"
          :src="coverUrl(currentSong.id)"
          class="fp-bg-img fp-bg-img-main"
          alt=""
        >
        <div class="fp-bg-frost" />
      </div>
      <div class="fp-overlay" />

      <!-- Close button -->
      <button
        class="fp-close"
        @click="showFullPlayer = false"
      >
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
        ><path d="M6 9l6 6 6-6" /></svg>
      </button>

      <div class="fp-content">
        <!-- Left: artwork + controls -->
        <div class="fp-left">
          <div class="fp-cover-wrap">
            <img
              v-if="currentSong?.has_cover"
              :src="coverUrl(currentSong.id)"
              class="fp-cover"
              :class="{ playing }"
            >
            <div
              v-else
              class="fp-cover placeholder"
              :class="{ playing }"
            >
              <svg
                viewBox="0 0 24 24"
                fill="currentColor"
                opacity="0.25"
              ><path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55C7.79 13 6 14.79 6 17s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z" /></svg>
            </div>
          </div>

          <div class="fp-song-info">
            <h2 class="fp-title">
              {{ currentSong?.title || 'No Song' }}
            </h2>
            <p class="fp-artist">
              {{ currentSong?.artist || '' }}
            </p>
            <p class="fp-album">
              {{ currentSong?.album || '' }}
            </p>
          </div>

          <div
            v-if="currentSong"
            class="fp-quality-row"
          >
            <span class="fp-format">{{ currentSong.format }}</span>
            <span
              v-if="currentSong.sample_rate"
              class="fp-spec"
            >{{ (currentSong.sample_rate/1000).toFixed(1) }}kHz</span>
            <span
              v-if="currentSong.bit_depth"
              class="fp-spec"
            >{{ currentSong.bit_depth }}bit</span>
            <span
              v-if="currentSong.channels"
              class="fp-spec"
            >{{ currentSong.channels }}ch</span>
            <span
              v-if="currentSong.bit_depth >= 24 || currentSong.sample_rate > 48000"
              class="fp-hires-badge"
            >Hi-Res Lossless</span>
            <span
              v-else-if="currentSong.lossless"
              class="fp-lossless-badge"
            >Lossless</span>
          </div>

          <!-- Progress -->
          <div class="fp-progress-wrap">
            <div
              ref="fpProgressTrack"
              class="fp-progress-track"
              @click="onProgressClick"
            >
              <div
                class="fp-progress-fill"
                :style="{ width: progress + '%' }"
              >
                <div class="fp-progress-knob" />
              </div>
            </div>
            <div class="fp-time-row">
              <span>{{ currentTimeStr }}</span>
              <span>-{{ remainingStr }}</span>
            </div>
          </div>

          <!-- Controls -->
          <div class="fp-controls">
            <button
              class="fp-ctrl fp-mode"
              :class="{ active: playMode !== 'sequential' }"
              :title="modeLabel"
              @click="cyclePlayMode"
            >
              <svg
                v-if="playMode === 'sequential'"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="2"
              ><polyline points="17 1 21 5 17 9" /><path d="M3 11V9a4 4 0 014-4h14" /><polyline points="7 23 3 19 7 15" /><path d="M21 13v2a4 4 0 01-4 4H3" /></svg>
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
              class="fp-ctrl"
              @click="prev"
            >
              <svg
                viewBox="0 0 24 24"
                fill="currentColor"
              ><path d="M6 6h2v12H6zm3.5 6l8.5 6V6z" /></svg>
            </button>
            <button
              class="fp-ctrl fp-play"
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
              class="fp-ctrl"
              @click="next"
            >
              <svg
                viewBox="0 0 24 24"
                fill="currentColor"
              ><path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z" /></svg>
            </button>
          </div>

          <!-- Volume -->
          <div class="fp-volume">
            <svg
              viewBox="0 0 24 24"
              fill="currentColor"
              class="vol-icon"
            ><path d="M5 9v6h4l5 5V4L9 9H5z" /></svg>
            <input
              type="range"
              class="fp-vol-slider"
              min="0"
              max="100"
              :value="volume"
              @input="setVolume(+$event.target.value)"
            >
            <svg
              viewBox="0 0 24 24"
              fill="currentColor"
              class="vol-icon"
            ><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z" /></svg>
          </div>
        </div>

        <!-- Right: lyrics -->
        <div class="fp-right">
          <div
            ref="lyricsContainer"
            class="lyrics-container"
          >
            <div
              v-if="lyrics && lyrics.length"
              class="lyrics-scroll"
            >
              <div
                v-for="(line, i) in lyrics"
                :key="i"
                :ref="el => { if (i === activeLyricIndex) activeLyricEl = el }"
                :class="['lyric-line', { active: i === activeLyricIndex, past: i < activeLyricIndex }]"
                @click="seekToLyric(line.time)"
              >
                {{ line.text || '···' }}
              </div>
            </div>
            <div
              v-else
              class="lyrics-empty"
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.5"
                opacity="0.3"
              ><path d="M9 18V5l12-2v13" /><circle
                cx="6"
                cy="18"
                r="3"
              /><circle
                cx="18"
                cy="16"
                r="3"
              /></svg>
              <p>No lyrics available</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup>
import { ref, computed, watch, nextTick } from 'vue'
import { useMusic } from '@/composables/useMusic.js'

const {
  currentSong, playing, progress, volume, lyrics, playMode,
  currentTime, duration, currentTimeStr, showFullPlayer,
  togglePlay, next, prev, seek, setVolume, coverUrl, formatTime, cyclePlayMode,
} = useMusic()

const modeLabel = computed(() => {
  const labels = { sequential: 'Sequential', shuffle: 'Shuffle', 'repeat-one': 'Repeat One' }
  return labels[playMode.value] || 'Sequential'
})

const fpProgressTrack = ref(null)
const lyricsContainer = ref(null)
const activeLyricEl = ref(null)

const remainingStr = computed(() => {
  const rem = (duration.value || 0) - (currentTime.value || 0)
  return formatTime(Math.max(0, rem))
})

const activeLyricIndex = computed(() => {
  if (!lyrics.value || !lyrics.value.length) return -1
  const t = currentTime.value
  let idx = -1
  for (let i = 0; i < lyrics.value.length; i++) {
    if (lyrics.value[i].time <= t) idx = i
    else break
  }
  return idx
})

watch(activeLyricIndex, () => {
  nextTick(() => {
    if (activeLyricEl.value && lyricsContainer.value) {
      const container = lyricsContainer.value
      const el = activeLyricEl.value
      const containerH = container.clientHeight
      const targetScroll = el.offsetTop - containerH / 2 + el.clientHeight / 2
      container.scrollTo({ top: targetScroll, behavior: 'smooth' })
    }
  })
})

function onProgressClick(e) {
  if (!fpProgressTrack.value) return
  const rect = fpProgressTrack.value.getBoundingClientRect()
  const pct = ((e.clientX - rect.left) / rect.width) * 100
  seek(pct)
}

function seekToLyric(time) {
  if (duration.value > 0) {
    seek((time / duration.value) * 100)
  }
}
</script>

<style scoped>
.full-player {
  position: fixed;
  inset: 0;
  z-index: 200;
  display: flex;
  flex-direction: column;
}

.fp-bg {
  position: absolute;
  inset: 0;
  z-index: 0;
  overflow: hidden;
  background:
    linear-gradient(180deg, rgba(24, 24, 24, 0.18), rgba(24, 24, 24, 0.76)),
    var(--app-bg);
}

.fp-bg.no-cover {
  background:
    linear-gradient(180deg, rgba(24, 24, 24, 0.96), rgba(24, 24, 24, 0.98)),
    repeating-linear-gradient(135deg, rgba(255, 255, 255, 0.018) 0 1px, transparent 1px 16px),
    var(--bg0);
}

.fp-bg-img {
  position: absolute;
  inset: -12%;
  width: 100%;
  height: 100%;
  object-fit: cover;
  transform-origin: center;
  user-select: none;
  pointer-events: none;
}

.fp-bg-img-soft {
  inset: -22%;
  width: 144%;
  height: 144%;
  filter: blur(86px) saturate(1.9) brightness(0.84) contrast(1.08);
  opacity: 0.92;
  transform: scale(1.08);
}

.fp-bg-img-main {
  inset: -7%;
  width: 114%;
  height: 114%;
  filter: blur(34px) saturate(1.36) brightness(0.48) contrast(1.08);
  opacity: 0.54;
  transform: scale(1.14);
  mix-blend-mode: screen;
}

.fp-bg::before,
.fp-bg::after {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.fp-bg::before {
  background:
    linear-gradient(90deg, rgba(24, 24, 24, 0.72) 0%, rgba(24, 24, 24, 0.16) 42%, rgba(24, 24, 24, 0.64) 100%),
    linear-gradient(180deg, rgba(24, 24, 24, 0.28) 0%, rgba(24, 24, 24, 0.12) 38%, rgba(24, 24, 24, 0.68) 100%);
}

.fp-bg::after {
  opacity: 0.18;
  background-image:
    linear-gradient(rgba(255, 255, 255, 0.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255, 255, 255, 0.035) 1px, transparent 1px);
  background-size: 3px 3px;
  mix-blend-mode: overlay;
}

.fp-bg-frost {
  position: absolute;
  inset: 0;
  background: rgba(24, 24, 24, 0.22);
  backdrop-filter: blur(24px) saturate(1.28);
  -webkit-backdrop-filter: blur(24px) saturate(1.28);
}

.fp-overlay {
  position: absolute;
  inset: 0;
  z-index: 1;
  background:
    linear-gradient(180deg, rgba(24, 24, 24, 0.34), rgba(24, 24, 24, 0.72)),
    radial-gradient(ellipse at 50% 24%, rgba(255, 255, 255, 0.14), transparent 44%);
  box-shadow: inset 0 0 180px rgba(0, 0, 0, 0.82);
}

.fp-close {
  position: absolute;
  top: 16px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 10;
  width: 40px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.1);
  border: none;
  border-radius: 12px;
  color: rgba(212, 212, 212, 0.62);
  cursor: pointer;
  backdrop-filter: blur(10px);
  transition: all 0.15s;
}
.fp-close:hover { background: rgba(255, 255, 255, 0.2); color: var(--t1); }
.fp-close svg { width: 20px; height: 20px; }

.fp-content {
  position: relative;
  z-index: 5;
  flex: 1;
  display: flex;
  padding: 60px 40px 40px;
  gap: 40px;
  overflow: hidden;
}

.fp-left {
  width: 380px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 20px;
}

.fp-cover-wrap {
  width: 300px;
  height: 300px;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 8px 40px rgba(0,0,0,0.5);
}

.fp-cover {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.3s;
}
.fp-cover.playing {
  animation: none;
}
.fp-cover.placeholder {
  width: 100%;
  height: 100%;
  background: rgba(255,255,255,0.05);
  display: flex;
  align-items: center;
  justify-content: center;
}
.fp-cover.placeholder svg { width: 80px; height: 80px; }

.fp-song-info {
  text-align: center;
  max-width: 340px;
}
.fp-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--t1);
  margin-bottom: 4px;
  line-height: 1.3;
}
.fp-artist {
  font-size: 14px;
  color: rgba(212, 212, 212, 0.62);
  margin-bottom: 2px;
}
.fp-album {
  font-size: 12px;
  color: rgba(212, 212, 212, 0.42);
}

.fp-quality-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: center;
}
.fp-format {
  font-size: 11px;
  font-weight: 600;
  font-family: var(--mono);
  color: rgba(212, 212, 212, 0.5);
}
.fp-spec {
  font-size: 10px;
  font-family: var(--mono);
  color: rgba(212, 212, 212, 0.35);
}
.fp-hires-badge {
  font-size: 9px;
  padding: 2px 8px;
  border-radius: 4px;
  font-weight: 700;
  letter-spacing: 0.5px;
  background: linear-gradient(135deg, rgba(175, 130, 255, 0.25), rgba(100, 210, 255, 0.25));
  color: #c4a0ff;
  border: 1px solid rgba(175, 130, 255, 0.35);
}
.fp-lossless-badge {
  font-size: 9px;
  padding: 2px 8px;
  border-radius: 4px;
  font-weight: 700;
  letter-spacing: 0.5px;
  background: rgba(50, 215, 75, 0.15);
  color: #6eff88;
  border: 1px solid rgba(50, 215, 75, 0.3);
}

/* Progress */
.fp-progress-wrap {
  width: 100%;
  max-width: 340px;
}
.fp-progress-track {
  height: 4px;
  background: rgba(255,255,255,0.12);
  border-radius: 2px;
  cursor: pointer;
  position: relative;
}
.fp-progress-track:hover { height: 6px; margin-top: -1px; }
.fp-progress-fill {
  height: 100%;
  background: var(--acc2);
  border-radius: 2px;
  position: relative;
  transition: width 0.3s linear;
}
.fp-progress-knob {
  position: absolute;
  right: -5px;
  top: 50%;
  transform: translateY(-50%);
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: white;
  box-shadow: 0 1px 4px rgba(0,0,0,0.4);
  opacity: 0;
  transition: opacity 0.15s;
}
.fp-progress-track:hover .fp-progress-knob { opacity: 1; }

.fp-time-row {
  display: flex;
  justify-content: space-between;
  margin-top: 6px;
  font-size: 11px;
  font-family: var(--mono);
  color: rgba(212, 212, 212, 0.42);
}

/* Controls */
.fp-controls {
  display: flex;
  align-items: center;
  gap: 24px;
}
.fp-ctrl {
  width: 44px;
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  color: rgba(212, 212, 212, 0.82);
  cursor: pointer;
  border-radius: 50%;
  transition: all 0.12s;
}
.fp-ctrl:hover { color: var(--t1); background: rgba(255,255,255,0.08); }
.fp-ctrl svg { width: 24px; height: 24px; }

.fp-mode {
  width: 36px;
  height: 36px;
}
.fp-mode svg { width: 18px; height: 18px; }
.fp-mode.active { color: var(--acc2); }

.fp-play {
  width: 56px;
  height: 56px;
  background: var(--acc-bg);
  color: var(--acc2);
}
.fp-play:hover { background: var(--acc-bg-strong); color: var(--acc2); }
.fp-play svg { width: 28px; height: 28px; }

/* Volume */
.fp-volume {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  max-width: 300px;
}
.vol-icon {
  width: 16px;
  height: 16px;
  color: rgba(212, 212, 212, 0.42);
  flex-shrink: 0;
}
.fp-vol-slider {
  flex: 1;
  height: 4px;
  -webkit-appearance: none;
  appearance: none;
  background: rgba(255,255,255,0.12);
  border-radius: 2px;
  outline: none;
}
.fp-vol-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: white;
  cursor: pointer;
}

/* Lyrics */
.fp-right {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.lyrics-container {
  flex: 1;
  overflow-y: auto;
  padding: 80px 20px;
  mask-image: linear-gradient(transparent, black 15%, black 85%, transparent);
  -webkit-mask-image: linear-gradient(transparent, black 15%, black 85%, transparent);
}

.lyrics-scroll {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.lyric-line {
  font-size: 22px;
  font-weight: 600;
  color: rgba(212, 212, 212, 0.25);
  line-height: 1.5;
  cursor: pointer;
  transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
  padding: 4px 0;
}
.lyric-line:hover {
  color: rgba(212, 212, 212, 0.45);
}
.lyric-line.active {
  color: var(--t1);
  font-size: 26px;
  transform: scale(1.02);
  transform-origin: left center;
}
.lyric-line.past {
  color: rgba(212, 212, 212, 0.35);
}

.lyrics-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: rgba(212, 212, 212, 0.25);
}
.lyrics-empty svg { width: 48px; height: 48px; }
.lyrics-empty p { font-size: 14px; }

/* Transition */
.fullplayer-enter-active,
.fullplayer-leave-active {
  transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
}
.fullplayer-enter-from,
.fullplayer-leave-to {
  opacity: 0;
  transform: translateY(40px);
}
</style>
