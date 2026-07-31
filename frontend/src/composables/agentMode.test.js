import assert from 'node:assert/strict'
import test from 'node:test'

import {
  AGENT_MODES,
  agentModeForSlashCommand,
  agentModeLabel,
  normalizeAgentMode,
} from './agentMode.js'

test('normalizeAgentMode preserves all three supported modes', () => {
  assert.deepEqual(AGENT_MODES, ['plan', 'agent', 'deepresearch'])
  assert.equal(normalizeAgentMode('deepresearch'), 'deepresearch')
  assert.equal(normalizeAgentMode('DEEPRESEARCH'), 'deepresearch')
  assert.equal(agentModeLabel('deepresearch'), 'DEEP RESEARCH')
  assert.equal(normalizeAgentMode('unknown'), 'agent')
})

test('agentModeForSlashCommand maps direct research aliases without sending chat', () => {
  assert.equal(agentModeForSlashCommand('/plan'), 'plan')
  assert.equal(agentModeForSlashCommand('/agent'), 'agent')
  assert.equal(agentModeForSlashCommand('/research'), 'deepresearch')
  assert.equal(agentModeForSlashCommand('/deepresearch'), 'deepresearch')
  assert.equal(agentModeForSlashCommand('/research explain this'), null)
  assert.equal(agentModeForSlashCommand('/unknown'), null)
})
