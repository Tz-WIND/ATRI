import assert from 'node:assert/strict'

import {
  projectUpdatesFromResponse,
  responseRevision,
} from './dawHostProjectResponse.js'

assert.equal(responseRevision({ revision: 'top-rev' }), 'top-rev')
assert.equal(responseRevision({ sync: { revision: 'sync-rev' } }), 'sync-rev')
assert.equal(responseRevision({}, 'fallback-rev'), 'fallback-rev')

assert.deepEqual(
  projectUpdatesFromResponse({
    project: { title: 'Before host sync' },
    revision: 'top-rev',
    sync: {
      project: { title: 'After host sync', tracks: [{ id: 1, host_track_id: 41 }] },
      revision: 'sync-rev',
    },
    active_project_id: 'project-1',
    projects: [{ id: 'project-1' }],
  }),
  {
    updates: [
      { project: { title: 'Before host sync' }, revision: 'top-rev' },
      {
        project: { title: 'After host sync', tracks: [{ id: 1, host_track_id: 41 }] },
        revision: 'sync-rev',
      },
    ],
    activeProjectId: 'project-1',
    projectArchives: [{ id: 'project-1' }],
  },
)
