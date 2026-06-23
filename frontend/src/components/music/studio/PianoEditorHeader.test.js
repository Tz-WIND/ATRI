import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const studioSource = readFileSync(new URL('../MusicStudio.vue', import.meta.url), 'utf8')
const headerUrl = new URL('./PianoEditorHeader.vue', import.meta.url)
const headerSource = existsSync(headerUrl) ? readFileSync(headerUrl, 'utf8') : ''

test('musicStudio_extractsPianoEditorHeaderComponent', () => {
  assert.ok(existsSync(headerUrl), 'PianoEditorHeader component should exist')
  assert.match(studioSource, /import PianoEditorHeader from '\.\/studio\/PianoEditorHeader\.vue'/)
  assert.match(studioSource, /<PianoEditorHeader/)
  assert.doesNotMatch(studioSource, /<div class="piano-head">/)
})

test('pianoEditorHeader_ownsPianoToolbarMarkupAndEvents', () => {
  assert.match(headerSource, /class="piano-head"/)
  assert.match(headerSource, /class="piano-actions"/)
  assert.match(headerSource, /v-for="option in quantizeOptions"/)
  assert.match(headerSource, /emit\('set-quantize-option', option\.id\)/)
  assert.match(headerSource, /emit\('toggle-snap'\)/)
  assert.match(headerSource, /emit\('set-tool', 'select'\)/)
  assert.match(headerSource, /emit\('set-tool', 'draw'\)/)
  assert.match(headerSource, /emit\('delete-selected-notes'\)/)
  assert.match(headerSource, /emit\('close'\)/)
})

test('pianoEditorHeader_keepsHeaderStylesWithComponent', () => {
  assert.match(headerSource, /\.piano-head\s*\{/)
  assert.match(headerSource, /\.piano-quantize-menu\s*\{/)
  assert.match(headerSource, /studio-page\.embedded\).*\.piano-head/)
  assert.doesNotMatch(studioSource, /\.piano-head\s*\{/)
  assert.doesNotMatch(studioSource, /\.piano-quantize-menu\s*\{/)
})
