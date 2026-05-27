import assert from 'node:assert/strict'
import {
  applyProjectPatch,
  mergeProjectBroadcast,
} from './studioProjectPatch.js'

const baseProject = {
  title: 'Patch Broadcast',
  tempo: 120,
  tracks: [
    {
      id: 1,
      name: 'Lead',
      notes: [{ id: 'n1', pitch: 60, start: 0, duration: 1, velocity: 96 }],
      clips: [],
    },
  ],
}

const patchedProject = applyProjectPatch(baseProject, [
  { op: 'replace', path: '/tracks/0/notes/0/start', value: 2 },
  {
    op: 'add',
    path: '/tracks/0/notes/1',
    value: { id: 'n2', pitch: 64, start: 1, duration: 1, velocity: 90 },
  },
])

assert.equal(patchedProject.tracks[0].notes[0].start, 2)
assert.equal(patchedProject.tracks[0].notes[1].id, 'n2')
assert.equal(baseProject.tracks[0].notes.length, 1)
assert.notEqual(patchedProject, baseProject)
assert.notEqual(patchedProject.tracks, baseProject.tracks)

assert.deepEqual(
  mergeProjectBroadcast(baseProject, 'rev-a', {
    type: 'music_project',
    base_revision: 'rev-a',
    revision: 'rev-b',
    patch: [{ op: 'replace', path: '/tracks/0/name', value: 'Bass' }],
  }),
  {
    project: {
      ...baseProject,
      tracks: [{ ...baseProject.tracks[0], name: 'Bass' }],
    },
    revision: 'rev-b',
    needsReload: false,
  }
)

assert.deepEqual(
  mergeProjectBroadcast(baseProject, 'rev-b', {
    type: 'music_project',
    base_revision: 'rev-a',
    revision: 'rev-b',
    patch: [{ op: 'replace', path: '/tracks/0/name', value: 'Bass' }],
  }),
  { project: null, revision: 'rev-b', needsReload: false }
)

assert.deepEqual(
  mergeProjectBroadcast(baseProject, 'other-rev', {
    type: 'music_project',
    base_revision: 'rev-a',
    revision: 'rev-b',
    patch: [{ op: 'replace', path: '/tracks/0/name', value: 'Bass' }],
  }),
  { project: null, revision: 'other-rev', needsReload: true }
)

assert.deepEqual(
  mergeProjectBroadcast(baseProject, 'rev-a', {
    type: 'music_project',
    project: { title: 'Full Project', tracks: [] },
    revision: 'rev-full',
  }),
  {
    project: { title: 'Full Project', tracks: [] },
    revision: 'rev-full',
    needsReload: false,
  }
)
