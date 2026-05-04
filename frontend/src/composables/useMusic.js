/**
 * Global music player state — singleton shared across all components.
 * Handles playback, queue, and WebSocket control messages from the AI agent.
 */
import { ref, computed, watch } from 'vue'

const songs = ref([])
const queue = ref([])
const currentIndex = ref(-1)
const playing = ref(false)
const currentTime = ref(0)
const duration = ref(0)
const volume = ref(80)
const showFullPlayer = ref(false)
const playerCollapsed = ref(false)
const lyrics = ref(null)
const loading = ref(false)
const coverCache = {}

let audio = null
let animFrame = null

function getAudio() {
  if (!audio) {
    audio = new Audio()
    audio.preload = 'auto'
    audio.volume = volume.value / 100

    audio.addEventListener('timeupdate', () => {
      currentTime.value = audio.currentTime
    })
    audio.addEventListener('durationchange', () => {
      duration.value = audio.duration || 0
    })
    audio.addEventListener('ended', () => {
      next()
    })
    audio.addEventListener('play', () => { playing.value = true })
    audio.addEventListener('pause', () => { playing.value = false })
    audio.addEventListener('error', (e) => {
      console.error('Audio error:', e)
      playing.value = false
    })
  }
  return audio
}

const currentSong = computed(() => {
  if (currentIndex.value >= 0 && currentIndex.value < queue.value.length) {
    return queue.value[currentIndex.value]
  }
  return null
})

const progress = computed(() => {
  if (duration.value <= 0) return 0
  return (currentTime.value / duration.value) * 100
})

const currentTimeStr = computed(() => formatTime(currentTime.value))
const durationStr = computed(() => formatTime(duration.value))

function formatTime(sec) {
  if (!sec || isNaN(sec)) return '0:00'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

function setLibrary(list) {
  songs.value = list
}

function playAll(startIndex = 0) {
  queue.value = [...songs.value]
  playSongAt(startIndex)
}

function playSong(song) {
  const idx = queue.value.findIndex(s => s.id === song.id)
  if (idx >= 0) {
    playSongAt(idx)
  } else {
    queue.value = [...songs.value]
    const newIdx = queue.value.findIndex(s => s.id === song.id)
    playSongAt(newIdx >= 0 ? newIdx : 0)
  }
}

function playSongDirect(song) {
  const idx = queue.value.findIndex(s => s.id === song.id)
  if (idx >= 0) {
    playSongAt(idx)
  } else {
    queue.value.push(song)
    playSongAt(queue.value.length - 1)
  }
}

async function playSongAt(index) {
  if (index < 0 || index >= queue.value.length) return
  currentIndex.value = index
  const song = queue.value[index]
  const a = getAudio()
  a.src = `/api/music/stream/${song.id}`
  loading.value = true
  try {
    await a.play()
  } catch (e) {
    console.error('Playback failed:', e)
  }
  loading.value = false
  loadLyrics(song.id)
}

function togglePlay() {
  const a = getAudio()
  if (playing.value) {
    a.pause()
  } else {
    if (a.src) {
      a.play().catch(() => {})
    } else if (queue.value.length > 0) {
      playSongAt(currentIndex.value >= 0 ? currentIndex.value : 0)
    }
  }
}

function pause() {
  getAudio().pause()
}

function resume() {
  const a = getAudio()
  if (a.src) a.play().catch(() => {})
}

function next() {
  if (queue.value.length === 0) return
  const idx = (currentIndex.value + 1) % queue.value.length
  playSongAt(idx)
}

function prev() {
  if (queue.value.length === 0) return
  const a = getAudio()
  if (a.currentTime > 3) {
    a.currentTime = 0
    return
  }
  const idx = (currentIndex.value - 1 + queue.value.length) % queue.value.length
  playSongAt(idx)
}

function stop() {
  const a = getAudio()
  a.pause()
  a.currentTime = 0
  playing.value = false
}

function seek(pct) {
  const a = getAudio()
  if (a.duration) {
    a.currentTime = (pct / 100) * a.duration
  }
}

function setVolume(val) {
  volume.value = Math.max(0, Math.min(100, val))
  if (audio) audio.volume = volume.value / 100
}

function coverUrl(songId) {
  return songId ? `/api/music/cover/${songId}` : ''
}

async function loadLyrics(songId) {
  lyrics.value = null
  if (!songId) return
  try {
    const res = await fetch(`/api/music/lyrics/${songId}`)
    const data = await res.json()
    if (data.lyrics) {
      lyrics.value = parseLrc(data.lyrics)
    }
  } catch {}
}

function parseLrc(text) {
  if (!text) return null
  const lines = text.split('\n')
  const result = []
  const timeRegex = /\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]/g

  for (const line of lines) {
    const times = []
    let match
    while ((match = timeRegex.exec(line)) !== null) {
      const min = parseInt(match[1])
      const sec = parseInt(match[2])
      const ms = match[3] ? parseInt(match[3].padEnd(3, '0')) : 0
      times.push(min * 60 + sec + ms / 1000)
    }
    const text = line.replace(/\[\d{1,2}:\d{2}(?:\.\d{1,3})?\]/g, '').trim()
    for (const t of times) {
      result.push({ time: t, text })
    }
  }

  result.sort((a, b) => a.time - b.time)
  return result.length > 0 ? result : null
}

function handleWsControl(msg) {
  const { action, payload } = msg
  switch (action) {
    case 'play':
      if (payload?.song) {
        playSongDirect(payload.song)
      } else {
        resume()
      }
      break
    case 'pause': pause(); break
    case 'resume': resume(); break
    case 'next': next(); break
    case 'prev': prev(); break
    case 'stop': stop(); break
    case 'volume':
      if (payload?.volume !== undefined) setVolume(payload.volume)
      break
  }
}

export function useMusic() {
  return {
    songs, queue, currentIndex, currentSong, playing, currentTime, duration,
    volume, showFullPlayer, playerCollapsed, lyrics, loading, progress,
    currentTimeStr, durationStr,
    setLibrary, playAll, playSong, playSongDirect, playSongAt,
    togglePlay, pause, resume, next, prev, stop,
    seek, setVolume, coverUrl, loadLyrics,
    handleWsControl, formatTime,
  }
}
