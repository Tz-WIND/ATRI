export const AGENT_MODES = Object.freeze(['plan', 'agent', 'deepresearch'])

const SLASH_COMMAND_MODES = Object.freeze({
  '/plan': 'plan',
  '/agent': 'agent',
  '/research': 'deepresearch',
  '/deepresearch': 'deepresearch',
})

export function normalizeAgentMode(value) {
  const mode = String(value || '').trim().toLowerCase()
  return AGENT_MODES.includes(mode) ? mode : 'agent'
}

export function agentModeLabel(value) {
  const mode = normalizeAgentMode(value)
  return mode === 'deepresearch' ? 'DEEP RESEARCH' : mode.toUpperCase()
}

export function agentModeForSlashCommand(value) {
  const command = String(value || '').trim().toLowerCase()
  return SLASH_COMMAND_MODES[command] || null
}
