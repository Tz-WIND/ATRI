import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { effect, nextTick, ref } from 'vue'

const module = await import('./chatDisplayItems.js').catch(() => ({}))
const { buildChatDisplayItems, createChatDisplayItemsBuilder, useChatDisplayItems } = module

assert.equal(typeof buildChatDisplayItems, 'function')
assert.equal(typeof createChatDisplayItemsBuilder, 'function')
assert.equal(typeof useChatDisplayItems, 'function')

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
    id: 'context-t1',
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
  assert.match(source, /useChatDisplayItems/)
  assert.equal(source.includes('CONTEXT_TOOL_NAMES'), false)
  assert.equal(source.includes('flushContextTools'), false)
}

const builder = createChatDisplayItemsBuilder()
const trackedMessages = [
  { id: 'u1', role: 'user', content: 'hello' },
  { id: 't1', role: 'tool', toolData: { tool: 'read_file', path: 'a.py' } },
  { id: 't2', role: 'tool', toolData: { tool: 'grep', query: 'TODO' } },
  { id: 'a1', role: 'assistant', content: 'stable' },
  { id: 'a2', role: 'assistant', content: 'Hel', streaming: true },
]
const prefixReads = []
const trackedList = new Proxy(trackedMessages, {
  get(target, prop, receiver) {
    if (String(Number(prop)) === prop) prefixReads.push(Number(prop))
    return Reflect.get(target, prop, receiver)
  },
})

const firstDisplayItems = builder.build(trackedList)
assert.equal(firstDisplayItems[1].id, 'context-t1')
const firstContextItem = firstDisplayItems[1]
prefixReads.length = 0

trackedMessages[4] = { ...trackedMessages[4], content: 'Hello' }
const secondDisplayItems = builder.build(trackedList)

assert.equal(secondDisplayItems, firstDisplayItems)
assert.equal(secondDisplayItems[1], firstContextItem)
assert.equal(secondDisplayItems.at(-1).message.content, 'Hello')
assert.deepEqual(prefixReads.filter((index) => index < 3), [])

const contextBuilder = createChatDisplayItemsBuilder()
const contextMessages = [
  { id: 't1', role: 'tool', toolData: { tool: 'read_file', path: 'a.py' } },
]
const firstContextBuild = contextBuilder.build(contextMessages)
const firstContextId = firstContextBuild[0].id
assert.equal(firstContextId, 'context-t1')
contextMessages.push({ id: 't2', role: 'tool', toolData: { tool: 'grep', query: 'TODO' } })

const secondContextBuild = contextBuilder.build(contextMessages)
assert.deepEqual(secondContextBuild, [
  {
    id: 'context-t1',
    type: 'tool-group',
    tools: [
      { id: 't1', tool: 'read_file', path: 'a.py' },
      { id: 't2', tool: 'grep', query: 'TODO' },
    ],
  },
])
assert.equal(secondContextBuild[0].id, firstContextId)

const reactiveMessages = ref([
  { id: 'a1', role: 'assistant', content: 'Hel', streaming: true },
])
const reactiveDisplayItems = useChatDisplayItems(reactiveMessages)
let renderCount = 0
let renderedContent = ''

effect(() => {
  renderCount += 1
  renderedContent = reactiveDisplayItems.value.at(-1)?.message?.content || ''
})

reactiveMessages.value.splice(0, 1, {
  id: 'a1',
  role: 'assistant',
  content: 'Hello',
  streaming: true,
})
await nextTick()

assert.equal(renderedContent, 'Hello')
assert.equal(renderCount, 2)
