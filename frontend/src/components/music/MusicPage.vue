<template>
  <div class="music-page">
    <div class="music-header">
      <div class="music-header-left">
        <h1 class="music-title">Music</h1>
        <span class="music-count" v-if="songs.length">{{ songs.length }} songs</span>
      </div>
      <div class="music-header-right">
        <input
          class="music-search"
          type="text"
          placeholder="Search songs..."
          v-model="searchQuery"
        />
        <button class="btn-icon" @click="scanLibrary" :disabled="scanning" title="Scan Library">
          <svg :class="{ spinning: scanning }" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/></svg>
        </button>
        <button class="btn-play-all" @click="handlePlayAll" v-if="filteredSongs.length">
          <svg viewBox="0 0 24 24" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>
          Play All
        </button>
      </div>
    </div>

    <div class="music-table-wrap" v-if="filteredSongs.length">
      <table class="music-table">
        <thead>
          <tr>
            <th class="col-num">#</th>
            <th class="col-title">Title</th>
            <th class="col-artist">Artist</th>
            <th class="col-album">Album</th>
            <th class="col-format">Format</th>
            <th class="col-duration">Duration</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="(song, idx) in filteredSongs"
            :key="song.id"
            :class="{ active: currentSong?.id === song.id, playing: currentSong?.id === song.id && playing }"
            @dblclick="handlePlay(song)"
          >
            <td class="col-num">
              <span class="row-num" v-if="currentSong?.id !== song.id">{{ idx + 1 }}</span>
              <span class="now-playing" v-else>
                <svg v-if="playing" viewBox="0 0 16 16" fill="currentColor">
                  <rect x="1" y="4" width="3" height="8" rx="0.5"><animate attributeName="height" values="8;4;8" dur="0.8s" repeatCount="indefinite"/><animate attributeName="y" values="4;8;4" dur="0.8s" repeatCount="indefinite"/></rect>
                  <rect x="6.5" y="2" width="3" height="12" rx="0.5"><animate attributeName="height" values="12;6;12" dur="0.6s" repeatCount="indefinite"/><animate attributeName="y" values="2;5;2" dur="0.6s" repeatCount="indefinite"/></rect>
                  <rect x="12" y="5" width="3" height="6" rx="0.5"><animate attributeName="height" values="6;10;6" dur="0.7s" repeatCount="indefinite"/><animate attributeName="y" values="5;3;5" dur="0.7s" repeatCount="indefinite"/></rect>
                </svg>
                <svg v-else viewBox="0 0 24 24" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>
              </span>
            </td>
            <td class="col-title">
              <div class="song-title-cell">
                <img
                  v-if="song.has_cover"
                  :src="coverUrl(song.id)"
                  class="song-thumb"
                  loading="lazy"
                />
                <div class="song-thumb placeholder" v-else>
                  <svg viewBox="0 0 24 24" fill="currentColor" opacity="0.3"><path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55C7.79 13 6 14.79 6 17s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/></svg>
                </div>
                <div class="song-info">
                  <span class="song-name">{{ song.title }}</span>
                  <div class="song-tags" v-if="song.lossless || song.bit_depth >= 24 || song.sample_rate > 48000">
                    <span class="tag hires" v-if="song.bit_depth >= 24 || song.sample_rate > 48000">Hi-Res</span>
                    <span class="tag lossless" v-else-if="song.lossless">Lossless</span>
                  </div>
                </div>
              </div>
            </td>
            <td class="col-artist">{{ song.artist }}</td>
            <td class="col-album">{{ song.album }}</td>
            <td class="col-format">
              <span class="format-badge">{{ song.format }}</span>
              <span class="format-detail" v-if="song.sample_rate">{{ (song.sample_rate/1000).toFixed(1) }}kHz</span>
              <span class="format-detail" v-if="song.bit_depth">/{{ song.bit_depth }}bit</span>
            </td>
            <td class="col-duration">{{ formatDuration(song.duration) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="music-empty" v-else-if="!scanning && songs.length === 0">
      <div class="empty-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>
      </div>
      <h3>No Music Found</h3>
      <p>Add music directories in Settings, then scan your library.</p>
    </div>

    <div class="music-empty" v-else-if="!scanning && filteredSongs.length === 0">
      <p>No songs match "{{ searchQuery }}"</p>
    </div>

    <div class="music-scanning" v-if="scanning">
      <div class="scan-spinner"></div>
      <span>Scanning library...</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useMusic } from '@/composables/useMusic.js'
import { useApi } from '@/composables/useApi.js'

const api = useApi()
const { songs, setLibrary, playAll, playSong, currentSong, playing, coverUrl, formatTime } = useMusic()

const searchQuery = ref('')
const scanning = ref(false)

const filteredSongs = computed(() => {
  if (!searchQuery.value) return songs.value
  const q = searchQuery.value.toLowerCase()
  return songs.value.filter(s =>
    s.title.toLowerCase().includes(q) ||
    s.artist.toLowerCase().includes(q) ||
    s.album.toLowerCase().includes(q)
  )
})

function formatDuration(sec) {
  if (!sec) return '--:--'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

function handlePlay(song) {
  playSong(song)
}

function handlePlayAll() {
  const first = filteredSongs.value[0]
  if (!first) return
  const idx = songs.value.findIndex(s => s.id === first.id)
  playAll(idx >= 0 ? idx : 0)
}

async function scanLibrary() {
  scanning.value = true
  try {
    const res = await api.musicScan()
    setLibrary(res.songs || [])
  } catch (e) {
    console.error('Scan failed:', e)
  }
  scanning.value = false
}

async function loadLibrary() {
  try {
    const res = await api.musicLibrary()
    setLibrary(res.songs || [])
  } catch {}
}

onMounted(() => {
  loadLibrary()
})
</script>

<style scoped>
.music-page {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--bg0);
}

.music-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 24px 16px;
  flex-shrink: 0;
}

.music-header-left {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.music-title {
  font-size: 22px;
  font-weight: 700;
  color: var(--t1);
  font-family: var(--sans);
}

.music-count {
  font-size: 13px;
  color: var(--t3);
}

.music-header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.music-search {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--t1);
  padding: 6px 12px;
  font-size: 13px;
  width: 200px;
  outline: none;
  transition: border-color 0.15s;
}
.music-search:focus {
  border-color: var(--acc);
}
.music-search::placeholder {
  color: var(--t3);
}

.btn-icon {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--t2);
  cursor: pointer;
  transition: all 0.15s;
}
.btn-icon:hover { color: var(--t1); background: var(--bg3); }
.btn-icon:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-icon svg { width: 16px; height: 16px; }

.spinning {
  animation: spin 1s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.btn-play-all {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 16px;
  background: rgba(255, 45, 85, 0.12);
  color: #ff2d55;
  border: 1px solid rgba(255, 45, 85, 0.25);
  border-radius: 20px;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
}
.btn-play-all:hover { background: rgba(255, 45, 85, 0.22); }
.btn-play-all svg { width: 12px; height: 12px; }

/* Table */
.music-table-wrap {
  flex: 1;
  overflow-y: auto;
  padding: 0 24px 120px;
}

.music-table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}

.music-table thead th {
  position: sticky;
  top: 0;
  background: var(--bg0);
  text-align: left;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--t3);
  padding: 8px 8px;
  border-bottom: 1px solid var(--border);
  font-weight: 500;
  z-index: 1;
}

.col-num { width: 48px; text-align: center; }
.col-title { width: 35%; }
.col-artist { width: 20%; }
.col-album { width: 20%; }
.col-format { width: 15%; }
.col-duration { width: 64px; text-align: right; }

.music-table tbody tr {
  cursor: default;
  transition: background 0.1s;
}
.music-table tbody tr:hover {
  background: rgba(255, 255, 255, 0.04);
}
.music-table tbody tr.active {
  background: rgba(255, 45, 85, 0.08);
}
.music-table tbody tr.active .song-name,
.music-table tbody tr.active .now-playing {
  color: #ff2d55;
}

.music-table td {
  padding: 6px 8px;
  font-size: 13px;
  color: var(--t2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: middle;
}

.col-num { text-align: center; }
.row-num { color: var(--t3); font-size: 12px; }
.now-playing { color: #ff2d55; }
.now-playing svg { width: 16px; height: 16px; }

.song-title-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}

.song-thumb {
  width: 36px;
  height: 36px;
  border-radius: 4px;
  object-fit: cover;
  flex-shrink: 0;
  background: var(--bg2);
}
.song-thumb.placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
}
.song-thumb.placeholder svg {
  width: 20px;
  height: 20px;
}

.song-info {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.song-name {
  color: var(--t1);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
}

.song-tags {
  display: flex;
  gap: 4px;
  margin-top: 2px;
}

.tag {
  font-size: 9px;
  padding: 1px 5px;
  border-radius: 3px;
  font-weight: 700;
  letter-spacing: 0.3px;
  text-transform: uppercase;
}
.tag.hires {
  background: linear-gradient(135deg, rgba(175, 130, 255, 0.2), rgba(100, 210, 255, 0.2));
  color: #af82ff;
  border: 1px solid rgba(175, 130, 255, 0.3);
}
.tag.lossless {
  background: rgba(50, 215, 75, 0.12);
  color: #32d74b;
  border: 1px solid rgba(50, 215, 75, 0.25);
}

.format-badge {
  font-size: 11px;
  font-weight: 600;
  font-family: var(--mono);
  color: var(--t3);
}
.format-detail {
  font-size: 10px;
  color: var(--t3);
  opacity: 0.7;
  font-family: var(--mono);
}

.col-duration {
  font-family: var(--mono);
  font-size: 12px;
  color: var(--t3);
}

/* Empty & Scanning */
.music-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--t3);
}
.empty-icon svg {
  width: 64px;
  height: 64px;
  opacity: 0.3;
}
.music-empty h3 { font-size: 16px; color: var(--t2); }
.music-empty p { font-size: 13px; }

.music-scanning {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--t2);
  font-size: 14px;
}
.scan-spinner {
  width: 20px;
  height: 20px;
  border: 2px solid var(--border);
  border-top-color: #ff2d55;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
</style>
