import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import test from 'node:test'

const studioSource = readFileSync(new URL('../MusicStudio.vue', import.meta.url), 'utf8')
const arrangementSource = readFileSync(new URL('./ArrangementEditorPanel.vue', import.meta.url), 'utf8')
const panelUrl = new URL('./TrackListPanel.vue', import.meta.url)
const panelSource = existsSync(panelUrl) ? readFileSync(panelUrl, 'utf8') : ''

test('musicStudio_extractsTrackListPanelComponent', () => {
  assert.ok(existsSync(panelUrl), 'TrackListPanel component should exist')
  assert.match(arrangementSource, /import TrackListPanel from '\.\/TrackListPanel\.vue'/)
  assert.match(arrangementSource, /<TrackListPanel/)
  assert.doesNotMatch(studioSource, /<aside class="track-list">/)
})

test('trackListPanel_ownsTrackRowsAndRoutingControls', () => {
  assert.match(panelSource, /class="track-list"/)
  assert.match(panelSource, /v-for="track in tracks"/)
  assert.match(panelSource, /class="track-plugin-select"/)
  assert.match(panelSource, /v-for="plugin in pluginOptions\.vst3"/)
  assert.match(panelSource, /v-for="bus in availableOutputBuses\(track\.id\)"/)
  assert.match(panelSource, /class="automation-target-select"/)
  assert.match(panelSource, /emit\('select-track', track\.id\)/)
  assert.match(panelSource, /emit\('update-track', track\.id, \{ mute: !track\.mute \}\)/)
  assert.match(panelSource, /emit\('open-automation-picker', track\)/)
})

test('trackListPanel_keepsTrackListStylesWithComponent', () => {
  assert.match(panelSource, /\.track-list\s*\{/)
  assert.match(panelSource, /\.track-row\s*\{/)
  assert.match(panelSource, /\.track-plugin-bar\s*\{/)
  assert.doesNotMatch(studioSource, /\.track-list\s*\{/)
  assert.doesNotMatch(studioSource, /\.track-row\s*\{/)
  assert.doesNotMatch(studioSource, /\.track-plugin-bar\s*\{/)
})
