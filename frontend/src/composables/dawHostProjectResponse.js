export function responseRevision(res, fallback = '') {
  return String(res?.revision || res?.sync?.revision || fallback || '')
}

export function projectUpdatesFromResponse(res) {
  const revision = responseRevision(res)
  const updates = []
  if (res?.project) {
    updates.push({ project: res.project, revision })
  }
  if (res?.sync?.project) {
    updates.push({
      project: res.sync.project,
      revision: responseRevision(res.sync, revision),
    })
  }
  return {
    updates,
    activeProjectId: res?.active_project_id ? String(res.active_project_id) : '',
    projectArchives: Array.isArray(res?.projects) ? res.projects : null,
  }
}
