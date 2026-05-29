import assert from 'node:assert/strict'

import {
  bridgeAutoExportKeyForArtifact,
  bridgeInstanceIdFromLocation,
  buildMidiArtifactPreview,
  buildMidiArtifactView,
  buildMidiArtifactViewFromArgs,
  exportPayloadForMidiArtifact,
  isDawAgentSurfaceLocation,
  isMidiArtifactTool,
} from './midiArtifact.js'

const project = {
  tempo: 120,
  length_beats: 16,
  tracks: [
    {
      id: 3,
      name: 'Edited Synth',
      color: '#4e79ff',
      notes: [
        { id: 'before', pitch: 48, start: 1, duration: 0.5, velocity: 80 },
        { id: 'inside-a', pitch: 60, start: 4, duration: 1, velocity: 96 },
        { id: 'inside-b', pitch: 64, start: 6, duration: 1.5, velocity: 88 },
        { id: 'after', pitch: 72, start: 12, duration: 0.5, velocity: 90 },
      ],
    },
  ],
}

assert.equal(isMidiArtifactTool('midi_write'), true)
assert.equal(isMidiArtifactTool('midi_diff'), true)
assert.equal(isMidiArtifactTool('read_file'), false)

const writeView = buildMidiArtifactView(
  {
    tool: 'midi_write',
    args: {
      track_id: 3,
      start: 4,
      end: 8,
      notes: [
        { pitch: 60, start: 4, duration: 1, velocity: 96 },
        { pitch: 64, start: 6, duration: 1.5, velocity: 88 },
      ],
    },
  },
  project
)

assert.equal(writeView.track.id, 3)
assert.deepEqual(writeView.range, { start: 4, end: 8 })
assert.deepEqual(writeView.notes.map(note => note.id), ['inside-a', 'inside-b'])

const updateByIdView = buildMidiArtifactView(
  {
    tool: 'midi_diff',
    args: {
      track_id: 3,
      operations: [{ op: 'update_note', id: 'inside-b', velocity: 72 }],
    },
  },
  project
)

assert.deepEqual(updateByIdView.range, { start: 6, end: 7.5 })
assert.deepEqual(updateByIdView.notes.map(note => note.id), ['inside-b'])

const payload = exportPayloadForMidiArtifact(updateByIdView, 'midi', { instanceId: 'bridge 1/left' })
assert.deepEqual(payload, {
  target: 'selected_tracks',
  track_ids: [3],
  format: 'midi',
  consumer: 'bridge',
  instance_id: 'bridge 1/left',
  start_beat: 6,
  end_beat: 7.5,
})

const mutableLocation = { search: '?instance_id=bridge-a' }
assert.equal(bridgeInstanceIdFromLocation(mutableLocation), 'bridge-a')
mutableLocation.search = '?instance_id=bridge-b'
assert.equal(bridgeInstanceIdFromLocation(mutableLocation), 'bridge-b')

assert.equal(isDawAgentSurfaceLocation({ search: '?surface=daw-agent&instance_id=bridge-a' }), true)
assert.equal(isDawAgentSurfaceLocation({ search: '?surface=chat&instance_id=bridge-a' }), false)
assert.equal(isDawAgentSurfaceLocation({ search: '?instance_id=bridge-a' }), false)

const autoKey = bridgeAutoExportKeyForArtifact(updateByIdView, {
  tool: 'midi_diff',
  args: {
    track_id: 3,
    operations: [{ op: 'update_note', id: 'inside-b', velocity: 72 }],
  },
})
assert.equal(autoKey, 'midi_diff:3:6:7.5:{"operations":[{"id":"inside-b","op":"update_note","velocity":72}],"track_id":3}')
assert.notEqual(
  bridgeAutoExportKeyForArtifact(updateByIdView, {
    tool: 'midi_diff',
    args: {
      track_id: 3,
      operations: [{ op: 'update_note', id: 'inside-b', velocity: 72 }],
    },
  }, 'revision-a'),
  bridgeAutoExportKeyForArtifact(updateByIdView, {
    tool: 'midi_diff',
    args: {
      track_id: 3,
      operations: [{ op: 'update_note', id: 'inside-b', velocity: 72 }],
    },
  }, 'revision-b')
)
assert.equal(bridgeAutoExportKeyForArtifact(null, { tool: 'midi_diff', args: {} }), '')

const previewPayload = exportPayloadForMidiArtifact(writeView, 'wav')
assert.deepEqual(previewPayload, {
  target: 'selected_tracks',
  track_ids: [3],
  mode: 'mixdown',
  format: 'wav',
  sample_rate: 48000,
  bit_depth: 'i24',
  start: 2,
  end: 4,
})

const staleProject = {
  tempo: 120,
  length_beats: 16,
  tracks: [
    {
      id: 3,
      name: 'Edited Synth',
      notes: [{ id: 'stale', pitch: 48, start: 4, duration: 1, velocity: 80 }],
    },
  ],
}

const argsPreview = buildMidiArtifactViewFromArgs(
  {
    tool: 'midi_write',
    args: {
      track_id: 3,
      start: 4,
      end: 8,
      notes: [
        { pitch: 60, start: 4, duration: 1, velocity: 96 },
        { pitch: 64, start: 6, duration: 1.5, velocity: 88 },
      ],
    },
  },
  staleProject
)

assert.deepEqual(argsPreview.notes.map(note => note.pitch), [60, 64])

const mergedPreview = buildMidiArtifactPreview(
  {
    tool: 'midi_write',
    args: {
      track_id: 3,
      start: 4,
      end: 8,
      notes: [
        { pitch: 60, start: 4, duration: 1, velocity: 96 },
        { pitch: 64, start: 6, duration: 1.5, velocity: 88 },
      ],
    },
  },
  staleProject
)

assert.deepEqual(mergedPreview.notes.map(note => note.pitch), [60, 64])
assert.equal(mergedPreview.track.name, 'Edited Synth')

const diffPreview = buildMidiArtifactPreview(
  {
    tool: 'midi_diff',
    args: {
      track_id: 3,
      operations: [{ op: 'update_note', id: 'inside-b', velocity: 72 }],
    },
  },
  project
)

assert.deepEqual(diffPreview.notes.map(note => note.id), ['inside-b'])
