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
        <!-- Left: artwork + info + controls -->
        <div class="fp-left">
          <!-- Album artwork -->
          <div class="fp-artwork-wrap">
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
          </div>

          <!-- Song info -->
          <div
            v-if="currentSong"
            class="fp-song-info"
          >
            <h2 class="fp-title">
              {{ currentSong.title || 'No Song' }}
            </h2>
            <p class="fp-artist">
              {{ currentSong.artist || '' }}
            </p>
            <p
              v-if="currentSong.album"
              class="fp-album"
            >
              {{ currentSong.album }}
            </p>
          </div>

          <!-- Progress bar -->
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
            <!-- Quality indicator — minimal, below progress -->
            <div
              v-if="currentSong"
              class="fp-quality"
            >
              <span
                v-if="currentSong.sample_rate > 44100"
                class="fp-quality-hires"
              >HIRES</span>
              <span
                v-else-if="currentSong.lossless"
                class="fp-quality-lossless"
              >Lossless</span>
              <span class="fp-quality-detail">
                {{ currentSong.format }} {{ currentSong.bit_depth }}bit / {{ (currentSong.sample_rate / 1000).toFixed(1) }}kHz
              </span>
            </div>
          </div>

          <!-- Playback controls — 3-zone: mode | transport | playlist -->
          <div class="fp-controls">
            <!-- Zone 1: shuffle / repeat -->
            <div class="fp-ctrl-zone fp-ctrl-zone--left">
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
            </div>

            <!-- Zone 2: prev / play / next — centered -->
            <div class="fp-ctrl-zone fp-ctrl-zone--center">
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
                ><polygon points="6,3 20,12 6,21" /></svg>
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

            <!-- Zone 3: playlist -->
            <div class="fp-ctrl-zone fp-ctrl-zone--right">
              <button
                class="fp-ctrl fp-playlist-btn"
                :class="{ active: showPlaylist }"
                title="Playlist"
                @click.stop="showPlaylist = !showPlaylist"
              >
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                ><line
                  x1="8"
                  y1="6"
                  x2="21"
                  y2="6"
                /><line
                  x1="8"
                  y1="12"
                  x2="21"
                  y2="12"
                /><line
                  x1="8"
                  y1="18"
                  x2="21"
                  y2="18"
                /><line
                  x1="3"
                  y1="6"
                  x2="3.01"
                  y2="6"
                /><line
                  x1="3"
                  y1="12"
                  x2="3.01"
                  y2="12"
                /><line
                  x1="3"
                  y1="18"
                  x2="3.01"
                  y2="18"
                /></svg>
              </button>

              <!-- Playlist dropdown -->
              <Transition name="playlist-drop">
                <div
                  v-if="showPlaylist"
                  class="fp-playlist-drop"
                  @click.stop
                >
                  <div class="fp-playlist-head">
                    <span>Playlist ({{ queue.length }})</span>
                    <button
                      class="fp-playlist-close"
                      @click="showPlaylist = false"
                    >
                      <svg
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="currentColor"
                        stroke-width="2"
                      ><line
                        x1="18"
                        y1="6"
                        x2="6"
                        y2="18"
                      /><line
                        x1="6"
                        y1="6"
                        x2="18"
                        y2="18"
                      /></svg>
                    </button>
                  </div>
                  <div class="fp-playlist-body">
                    <div
                      v-for="(song, i) in queue"
                      :key="song.id"
                      :class="['fp-playlist-item', { active: i === currentIndex }]"
                      @click="playSongAt(i)"
                    >
                      <span class="fp-playlist-idx">{{ i + 1 }}</span>
                      <span class="fp-playlist-title">{{ song.title }}</span>
                      <span class="fp-playlist-artist">{{ song.artist }}</span>
                    </div>
                    <div
                      v-if="queue.length === 0"
                      class="fp-playlist-empty"
                    >
                      Playlist is empty
                    </div>
                  </div>
                </div>
              </Transition>
            </div>
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
  currentSong, playing, progress, volume, lyrics, playMode, queue, currentIndex,
  currentTime, duration, currentTimeStr, showFullPlayer,
  togglePlay, next, prev, seek, setVolume, coverUrl, formatTime, cyclePlayMode, playSongAt,
} = useMusic()

const modeLabel = computed(() => {
  const labels = { sequential: 'Sequential', shuffle: 'Shuffle', 'repeat-one': 'Repeat One' }
  return labels[playMode.value] || 'Sequential'
})

const fpProgressTrack = ref(null)
const lyricsContainer = ref(null)
const showPlaylist = ref(false)

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

watch(activeLyricIndex, async () => {
  await nextTick()
  if (!lyricsContainer.value) return
  const activeEl = lyricsContainer.value.querySelector('.lyric-line.active')
  if (!activeEl) return
  const container = lyricsContainer.value
  const containerH = container.clientHeight
  // Offset the active line slightly above true center for visual balance
  const targetScroll = activeEl.offsetTop - containerH * 0.42 + activeEl.clientHeight / 2
  container.scrollTo({ top: targetScroll, behavior: 'smooth' })
})

// Close playlist when changing song
watch(currentIndex, () => {
  showPlaylist.value = false
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
/* ============================================
   Apple Music–style full player
   ============================================ */

.full-player {
  position: fixed;
  inset: 0;
  z-index: 200;
  display: flex;
  flex-direction: column;
  isolation: isolate;
  background: #121212;
}

/* ---- Background layers ---- */

.fp-bg {
  position: absolute;
  inset: 0;
  z-index: 0;
  overflow: hidden;
  background:
    radial-gradient(circle at 28% 22%, rgba(210, 162, 255, 0.28), transparent 34%),
    radial-gradient(circle at 82% 32%, rgba(255, 173, 209, 0.18), transparent 34%),
    linear-gradient(135deg, #1a1525 0%, #2d1f33 46%, #241a29 100%);
}

.fp-bg.no-cover {
  background:
    radial-gradient(circle at 30% 20%, rgba(158, 191, 255, 0.12), transparent 32%),
    linear-gradient(135deg, #15141a 0%, #1f1a26 52%, #1a151e 100%);
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
  filter: blur(92px) saturate(1.5) brightness(0.78);
  opacity: 0.72;
  transform: scale(1.08);
}

.fp-bg-img-main {
  inset: -7%;
  width: 114%;
  height: 114%;
  filter: blur(42px) saturate(1.24) brightness(0.38) contrast(1.06);
  opacity: 0.32;
  transform: scale(1.14);
  mix-blend-mode: soft-light;
}

.fp-bg-frost {
  position: absolute;
  inset: 0;
  background: rgba(18, 16, 22, 0.36);
  backdrop-filter: blur(24px) saturate(1.2);
  -webkit-backdrop-filter: blur(24px) saturate(1.2);
}

.fp-overlay {
  position: absolute;
  inset: 0;
  z-index: 1;
  background:
    radial-gradient(ellipse at 38% 20%, rgba(255, 255, 255, 0.06), transparent 42%),
    linear-gradient(180deg, rgba(10, 9, 15, 0.1), rgba(8, 7, 12, 0.5));
  box-shadow: inset 0 0 160px rgba(0, 0, 0, 0.5);
}

/* ---- Close button ---- */

.fp-close {
  position: absolute;
  top: 20px;
  right: 24px;
  z-index: 10;
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.08);
  border: none;
  border-radius: 50%;
  color: rgba(255, 255, 255, 0.55);
  cursor: pointer;
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  transition: all 0.2s;
}
.fp-close:hover {
  background: rgba(255, 255, 255, 0.14);
  color: white;
  transform: scale(1.05);
}
.fp-close svg { width: 18px; height: 18px; }

/* ---- Content grid ---- */

.fp-content {
  position: relative;
  z-index: 5;
  flex: 1;
  display: grid;
  grid-template-columns: minmax(300px, 400px) minmax(360px, 600px);
  grid-template-rows: 1fr;
  align-items: stretch;
  justify-content: center;
  gap: clamp(56px, 7vw, 100px);
  padding: clamp(50px, 7vh, 90px) clamp(32px, 6vw, 100px);
  overflow: hidden;
}

/* ---- Left panel ---- */

.fp-left {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 18px;
  min-width: 0;
  height: 100%;
}

/* ---- Album artwork ---- */

.fp-artwork-wrap {
  width: 100%;
  max-width: 340px;
  padding: 10px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.08);
  box-shadow:
    0 28px 60px rgba(0, 0, 0, 0.4),
    inset 0 1px 0 rgba(255, 255, 255, 0.12);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
}

.fp-cover-wrap {
  aspect-ratio: 1;
  border-radius: 10px;
  overflow: hidden;
  box-shadow:
    0 12px 40px rgba(0, 0, 0, 0.45),
    0 0 0 1px rgba(255, 255, 255, 0.06);
}

.fp-cover {
  width: 100%;
  height: 100%;
  object-fit: cover;
  transition: transform 0.4s cubic-bezier(0.4, 0, 0.2, 1);
}
.fp-cover.playing {
  transform: scale(1.02);
}
.fp-cover.placeholder {
  width: 100%;
  height: 100%;
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.1), rgba(255, 255, 255, 0.02)),
    rgba(255, 255, 255, 0.04);
  display: flex;
  align-items: center;
  justify-content: center;
}
.fp-cover.placeholder svg { width: 80px; height: 80px; }

/* ---- Song info ---- */

.fp-song-info {
  text-align: center;
  min-width: 0;
  max-width: 340px;
}

.fp-title {
  overflow: hidden;
  color: rgba(255, 255, 255, 0.92);
  font-size: 18px;
  font-weight: 650;
  line-height: 1.35;
  letter-spacing: -0.01em;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin: 0 0 4px;
}

.fp-artist {
  overflow: hidden;
  color: rgba(255, 255, 255, 0.58);
  font-size: 13px;
  font-weight: 500;
  line-height: 1.4;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin: 0;
}

.fp-album {
  overflow: hidden;
  color: rgba(255, 255, 255, 0.32);
  font-size: 12px;
  line-height: 1.4;
  text-overflow: ellipsis;
  white-space: nowrap;
  margin: 2px 0 0;
}

/* ---- Progress bar ---- */

.fp-progress-wrap {
  width: 100%;
  max-width: 340px;
}

.fp-progress-track {
  height: 4px;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 2px;
  cursor: pointer;
  position: relative;
  transition: height 0.15s;
}
.fp-progress-track:hover { height: 6px; margin: -1px 0; }

.fp-progress-fill {
  height: 100%;
  background: rgba(255, 255, 255, 0.82);
  border-radius: 2px;
  position: relative;
  transition: width 0.3s linear;
}

.fp-progress-knob {
  position: absolute;
  right: -6px;
  top: 50%;
  transform: translateY(-50%);
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: white;
  box-shadow: 0 1px 6px rgba(0, 0, 0, 0.5);
  opacity: 0;
  transition: opacity 0.15s;
}
.fp-progress-track:hover .fp-progress-knob { opacity: 1; }

.fp-time-row {
  display: flex;
  justify-content: space-between;
  margin-top: 8px;
  font-size: 11px;
  font-family: var(--mono, 'SF Mono', monospace);
  color: rgba(255, 255, 255, 0.38);
  font-weight: 500;
}

/* ---- Quality indicator (below progress) ---- */

.fp-quality {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  margin-top: 6px;
  min-height: 16px;
  font-size: 10px;
  font-family: var(--mono, 'SF Mono', monospace);
  color: rgba(255, 255, 255, 0.28);
  font-weight: 500;
  letter-spacing: 0.03em;
}

.fp-quality-hires {
  color: rgba(255, 255, 255, 0.5);
  font-weight: 650;
  letter-spacing: 0.06em;
}

.fp-quality-lossless {
  color: rgba(255, 255, 255, 0.32);
  font-weight: 600;
}

.fp-quality-detail {
  opacity: 1;
  white-space: nowrap;
}

/* ---- Controls — 3-zone grid ---- */

.fp-controls {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  width: 100%;
  max-width: 340px;
  margin: 10px 0;
}

.fp-ctrl-zone {
  display: flex;
  align-items: center;
  gap: 4px;
}

.fp-ctrl-zone--left {
  justify-self: start;
}

.fp-ctrl-zone--center {
  justify-self: center;
  gap: 14px;
}

.fp-ctrl-zone--right {
  position: relative;
  justify-self: end;
}

/* Base ctrl button */
.fp-ctrl {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  color: rgba(255, 255, 255, 0.7);
  cursor: pointer;
  border-radius: 50%;
  transition: all 0.15s cubic-bezier(0.4, 0, 0.2, 1);
  flex-shrink: 0;
}
.fp-ctrl:hover {
  color: white;
  background: rgba(255, 255, 255, 0.08);
  transform: scale(1.06);
}
.fp-ctrl svg { width: 22px; height: 22px; }

/* Play mode toggle */
.fp-mode {
  width: 32px;
  height: 32px;
}
.fp-mode svg { width: 17px; height: 17px; }
.fp-mode.active { color: rgba(255, 255, 255, 0.92); }

/* Play/pause — center star */
.fp-play {
  width: 52px;
  height: 52px;
  background: rgba(255, 255, 255, 0.92);
  color: #18141e;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.35);
}
.fp-play:hover {
  background: white;
  color: #100d16;
  transform: scale(1.08);
  box-shadow: 0 6px 28px rgba(0, 0, 0, 0.4);
}
.fp-play svg { width: 26px; height: 26px; }

/* Playlist toggle button */
.fp-playlist-btn {
  width: 32px;
  height: 32px;
}
.fp-playlist-btn svg {
  width: 18px;
  height: 18px;
}
.fp-playlist-btn.active {
  color: rgba(255, 255, 255, 0.92);
  background: rgba(255, 255, 255, 0.1);
}

/* ---- Playlist dropdown ---- */

.fp-playlist-drop {
  position: absolute;
  bottom: calc(100% + 10px);
  right: 0;
  width: 300px;
  max-height: 380px;
  background: rgba(30, 26, 40, 0.96);
  backdrop-filter: blur(30px);
  -webkit-backdrop-filter: blur(30px);
  border-radius: 14px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  box-shadow:
    0 16px 48px rgba(0, 0, 0, 0.55),
    inset 0 1px 0 rgba(255, 255, 255, 0.06);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  z-index: 20;
}

.fp-playlist-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  font-size: 13px;
  font-weight: 650;
  color: rgba(255, 255, 255, 0.7);
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.fp-playlist-close {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: none;
  color: rgba(255, 255, 255, 0.4);
  cursor: pointer;
  border-radius: 50%;
  transition: all 0.15s;
}
.fp-playlist-close:hover { color: white; background: rgba(255, 255, 255, 0.08); }
.fp-playlist-close svg { width: 14px; height: 14px; }

.fp-playlist-body {
  flex: 1;
  overflow-y: auto;
  padding: 6px;
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, 0.12) transparent;
}

.fp-playlist-item {
  display: grid;
  grid-template-columns: 28px 1fr auto;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.12s;
}
.fp-playlist-item:hover {
  background: rgba(255, 255, 255, 0.06);
}
.fp-playlist-item.active {
  background: rgba(255, 255, 255, 0.1);
}
.fp-playlist-item.active .fp-playlist-title {
  color: rgba(255, 255, 255, 0.92);
}

.fp-playlist-idx {
  font-size: 11px;
  font-weight: 600;
  color: rgba(255, 255, 255, 0.25);
  text-align: center;
}

.fp-playlist-title {
  font-size: 13px;
  font-weight: 550;
  color: rgba(255, 255, 255, 0.82);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fp-playlist-artist {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.35);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100px;
}

.fp-playlist-empty {
  padding: 28px 12px;
  text-align: center;
  font-size: 13px;
  color: rgba(255, 255, 255, 0.28);
}

/* Playlist dropdown transition */
.playlist-drop-enter-active,
.playlist-drop-leave-active {
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}
.playlist-drop-enter-from,
.playlist-drop-leave-to {
  opacity: 0;
  transform: translateY(8px);
}

/* ---- Volume ---- */

.fp-volume {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  max-width: 240px;
}

.vol-icon {
  width: 16px;
  height: 16px;
  color: rgba(255, 255, 255, 0.3);
  flex-shrink: 0;
  transition: color 0.15s;
}
.fp-volume:hover .vol-icon { color: rgba(255, 255, 255, 0.5); }

.fp-vol-slider {
  flex: 1;
  height: 3px;
  -webkit-appearance: none;
  appearance: none;
  background: rgba(255, 255, 255, 0.12);
  border-radius: 2px;
  outline: none;
}
.fp-vol-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: white;
  cursor: pointer;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.3);
  transition: transform 0.12s;
}
.fp-vol-slider::-webkit-slider-thumb:hover {
  transform: scale(1.15);
}

/* ---- Lyrics (right panel) ---- */

.fp-right {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  height: 100%;
}

.lyrics-container {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 12px 0 0;
  mask-image: linear-gradient(transparent, black 14%, black 86%, transparent);
  -webkit-mask-image: linear-gradient(transparent, black 14%, black 86%, transparent);
  scrollbar-width: none;
}

.lyrics-container::-webkit-scrollbar {
  display: none;
}

.lyrics-scroll {
  display: flex;
  flex-direction: column;
  gap: 24px;
  padding: 42% 0;
}

.lyric-line {
  max-width: 620px;
  font-size: clamp(24px, 3vw, 38px);
  font-weight: 720;
  letter-spacing: -0.03em;
  color: rgba(255, 255, 255, 0.14);
  line-height: 1.16;
  cursor: pointer;
  transition: color 0.4s ease, transform 0.4s cubic-bezier(0.4, 0, 0.2, 1);
  padding: 3px 0;
  text-wrap: balance;
}
.lyric-line:hover {
  color: rgba(255, 255, 255, 0.4);
}
.lyric-line.active {
  color: rgba(255, 255, 255, 0.92);
  transform: translateX(6px);
  transform-origin: left center;
}
.lyric-line.past {
  color: rgba(255, 255, 255, 0.18);
}

.lyrics-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  color: rgba(255, 255, 255, 0.28);
}
.lyrics-empty svg { width: 48px; height: 48px; }
.lyrics-empty p { font-size: 14px; font-weight: 500; }

/* ---- Responsive ---- */

@media (max-width: 920px) {
  .fp-content {
    grid-template-columns: minmax(240px, 380px);
    align-content: start;
    gap: 28px;
    overflow-y: auto;
    padding: clamp(36px, 5vh, 56px) clamp(20px, 5vw, 40px);
  }

  .fp-left {
    height: auto;
    gap: 14px;
  }

  .fp-artwork-wrap {
    max-width: 280px;
  }

  .fp-right {
    height: auto;
    flex: 1 1 auto;
  }

  .lyrics-container {
    padding: 0 4px 0;
  }

  .lyric-line {
    font-size: clamp(20px, 6.5vw, 30px);
  }

  .fp-play {
    width: 46px;
    height: 46px;
  }
  .fp-play svg { width: 22px; height: 22px; }

  .fp-playlist-drop {
    width: 260px;
    right: -60px;
  }
}

/* ---- Transitions ---- */

.fullplayer-enter-active,
.fullplayer-leave-active {
  transition: all 0.45s cubic-bezier(0.4, 0, 0.2, 1);
}
.fullplayer-enter-from,
.fullplayer-leave-to {
  opacity: 0;
  transform: translateY(32px);
}
</style>
