import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const module = await import('./chatDisplayItems.js').catch(() => ({}))
const { buildChatDisplayItems } = module

assert.equal(typeof buildChatDisplayItems, 'function')

const messages = [
  { id: 'u1', role: 'user', content: 'hello' },
  { id: 't1', role: 'tool', toolData: { tool: 'read_file', path: 'a.py' } },
  { id: 't2', role: 'tool', toolData: { tool: 'grep', query: 'TODO' } },
  { id: 'a1', role: 'assistant', content: 'result' },
  { id: 't3', role: 'tool', toolData: { tool: 'midi_write', args: {} } },
]

assert.deepEqual(buildChatDisplayItems(messages), [
  {
    id: 'u1',
    type: 'user',
    message: messages[0],
  },
  {
    id: 'context-t1-t2',
    type: 'tool-group',
    tools: [
      { id: 't1', tool: 'read_file', path: 'a.py' },
      { id: 't2', tool: 'grep', query: 'TODO' },
    ],
  },
  {
    id: 'a1',
    type: 'assistant',
    message: messages[3],
  },
  {
    id: 't3',
    type: 'tool',
    message: messages[4],
  },
])

const here = dirname(fileURLToPath(import.meta.url))
const chatPage = readFileSync(resolve(here, '../components/chat/ChatPage.vue'), 'utf8')
const dawAgentPage = readFileSync(resolve(here, '../components/chat/DawAgentPage.vue'), 'utf8')

for (const source of [chatPage, dawAgentPage]) {
  assert.match(source, /buildChatDisplayItems/)
  assert.equal(source.includes('CONTEXT_TOOL_NAMES'), false)
  assert.equal(source.includes('flushContextTools'), false)
}
