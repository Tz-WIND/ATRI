import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import test from 'node:test'

const studioSource = readFileSync(new URL('../MusicStudio.vue', import.meta.url), 'utf8')
const panelUrl = new URL('./ArrangementEditorPanel.vue', import.meta.url)
const panelSource = existsSync(panelUrl) ? readFileSync(panelUrl, 'utf8') : ''
const arrangementUsage = studioSource.slice(
  studioSource.indexOf('<ArrangementEditorPanel'),
  studioSource.indexOf('/>', studioSource.indexOf('<ArrangementEditorPanel')) + 2
)

test('musicStudio_extractsArrangementEditorPanelComponent', () => {
  assert.ok(existsSync(panelUrl), 'ArrangementEditorPanel component should exist')
  assert.match(studioSource, /import ArrangementEditorPanel from '\.\/studio\/ArrangementEditorPanel\.vue'/)
  assert.match(studioSource, /<ArrangementEditorPanel/)
  assert.doesNotMatch(studioSource, /class="arrangement-head-grid"/)
  assert.doesNotMatch(studioSource, /class="arrangement-timeline-stack"/)
})

test('arrangementEditorPanel_ownsTimelineCanvasTrackListAndDropUi', () => {
  assert.match(panelSource, /import TrackListPanel from '\.\/TrackListPanel\.vue'/)
  assert.match(panelSource, /class="arrangement"/)
  assert.match(panelSource, /class="track-list-head"/)
  assert.match(panelSource, /class="arrangement-toolbar"/)
  assert.match(panelSource, /<TrackListPanel/)
  assert.match(panelSource, /class="editor-canvas arrangement-header-canvas"/)
  assert.match(panelSource, /class="editor-canvas arrangement-canvas"/)
  assert.match(panelSource, /class="audio-drop-layer"/)
  assert.match(panelSource, /emit\('arrangement-pointer-down', event\)/)
  assert.match(panelSource, /emit\('arrangement-wheel', event\)/)
  assert.match(panelSource, /emit\('scroll', event\)/)
  assert.match(panelSource, /emit\('start-track-list-resize', event\)/)
  assert.match(panelSource, /emit\('select-track', trackId\)/)
})

test('arrangementEditorPanel_usesBoundedContextProps', () => {
  const propsBlock = panelSource.slice(
    panelSource.indexOf('defineProps({'),
    panelSource.indexOf('const emit = defineEmits')
  )
  const topLevelProps = [...propsBlock.matchAll(/\n  ([a-zA-Z]\w+): /g)].map(match => match[1])

  assert.deepEqual(topLevelProps, ['layout', 'toolbar', 'trackList', 'audioDrop', 'contextMenus'])
  assert.match(arrangementUsage, /:layout="arrangementLayoutContext"/)
  assert.match(arrangementUsage, /:toolbar="arrangementToolbarContext"/)
  assert.match(arrangementUsage, /:track-list="arrangementTrackListContext"/)
  assert.match(arrangementUsage, /:audio-drop="arrangementAudioDropContext"/)
  assert.match(arrangementUsage, /:context-menus="arrangementContextMenuContext"/)
  assert.doesNotMatch(arrangementUsage, /:piano-quantize-label=/)
  assert.doesNotMatch(arrangementUsage, /:can-drag-track-row=/)
  assert.doesNotMatch(arrangementUsage, /:automation-menu=/)
})

test('arrangementEditorPanel_exposesCanvasAndScrollRefsForRendering', () => {
  assert.match(panelSource, /const arrangementWrap = ref\(null\)/)
  assert.match(panelSource, /const arrangementHeaderCanvas = ref\(null\)/)
  assert.match(panelSource, /const arrangementCanvas = ref\(null\)/)
  assert.match(panelSource, /defineExpose\(\{\s*arrangementWrap,\s*arrangementHeaderCanvas,\s*arrangementCanvas,\s*\}\)/)
})

test('arrangementEditorPanel_keepsArrangementStylesWithComponent', () => {
  assert.match(panelSource, /\.arrangement\s*\{/)
  assert.match(panelSource, /\.arrangement-canvas-wrap\s*\{/)
  assert.match(panelSource, /\.arrangement-timeline-stack\s*\{/)
  assert.match(panelSource, /\.track-list-resize-handle\s*\{/)
  assert.doesNotMatch(studioSource, /\.arrangement\s*\{/)
  assert.doesNotMatch(studioSource, /\.arrangement-canvas-wrap\s*\{/)
  assert.doesNotMatch(studioSource, /\.arrangement-timeline-stack\s*\{/)
  assert.doesNotMatch(studioSource, /\.track-list-resize-handle\s*\{/)
})
