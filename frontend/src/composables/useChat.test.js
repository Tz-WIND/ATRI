import assert from 'node:assert/strict'

import { clearChatInstance, useChat } from './useChat.js'

const originalLocalStorage = globalThis.localStorage
const originalFetch = globalThis.fetch
const originalRequestAnimationFrame = globalThis.requestAnimationFrame
const originalCancelAnimationFrame = globalThis.cancelAnimationFrame
const storage = new Map()
globalThis.localStorage = {
  getItem(key) {
    return storage.get(key) || null
  },
  setItem(key, value) {
    storage.set(key, String(value))
  },
  removeItem(key) {
    storage.delete(key)
  },
}

try {
  clearChatInstance()

  const first = useChat()
  first.addMessage('user', 'chat surface')

  assert.equal(useChat(), first)
  assert.equal(first.messages.value.length, 1)

  clearChatInstance()

  const second = useChat()
  assert.notEqual(second, first)
  assert.deepEqual(second.messages.value, [])

  second.handleWsEvent({ type: 'research_started', phase: 'created' })
  second.handleWsEvent({ type: 'research_phase', phase: 'gathering' })
  second.handleWsEvent({
    type: 'research_budget',
    research_tool_calls: 7,
    web_fetches: 2,
    active_subagents: 1,
    total_subagents: 2,
  })
  second.handleWsEvent({ type: 'research_evidence', evidence_count: 4 })

  assert.deepEqual(second.researchStatus.value, {
    visible: true,
    active: true,
    phase: 'gathering',
    state: 'researching',
    evidenceCount: 4,
    toolCalls: 7,
    webFetches: 2,
    activeSubagents: 1,
    totalSubagents: 2,
  })

  second.handleWsEvent({
    type: 'research_budget',
    active_subagents: 2,
    total_subagents: 2,
  })
  second.handleWsEvent({ type: 'research_subagent_started', branch_id: 'branch-a' })
  second.handleWsEvent({ type: 'research_subagent_started', branch_id: 'branch-b' })
  assert.equal(second.researchStatus.value.activeSubagents, 2)
  assert.equal(second.researchStatus.value.totalSubagents, 2)
  second.handleWsEvent({ type: 'research_subagent_finished', branch_id: 'branch-a' })
  second.handleWsEvent({ type: 'research_subagent_finished', branch_id: 'branch-b' })
  assert.equal(second.researchStatus.value.activeSubagents, 2)
  second.handleWsEvent({
    type: 'research_budget',
    active_subagents: 0,
    total_subagents: 2,
  })
  assert.equal(second.researchStatus.value.activeSubagents, 0)

  second.handleWsEvent({ type: 'research_completed', phase: 'completed' })
  assert.equal(second.researchStatus.value.active, false)
  assert.equal(second.researchStatus.value.visible, true)

  second.beginTranscriptTurn()
  assert.equal(second.researchStatus.value.visible, false)

  second.handleWsEvent({ type: 'research_started', phase: 'created' })
  second.handleWsEvent({ type: 'research_phase', phase: 'verifying' })
  second.handleWsEvent({ type: 'research_cancelled', phase: 'verifying' })
  assert.equal(second.researchStatus.value.phase, 'cancelled')
  assert.equal(second.researchStatus.value.state, 'cancelled')

  second.beginTranscriptTurn()

  second.loadTranscript({
    messages: [
      {
        role: 'user',
        content: 'Summarize\n\n[File: brief.docx]\nPortfolio',
        _atri_display_content: 'Summarize',
        _atri_attachments: [
          {
            kind: 'file',
            name: 'brief.docx',
            type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            size: 42,
          },
        ],
      },
    ],
  })

  assert.equal(second.messages.value.length, 1)
  assert.equal(second.messages.value[0].content, 'Summarize')
  assert.deepEqual(second.messages.value[0].attachments, [
    {
      id: second.messages.value[0].attachments[0].id,
      kind: 'file',
      name: 'brief.docx',
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      size: 42,
    },
  ])

  clearChatInstance()

  const disposeFrames = []
  const disposeCancelledFrames = []
  globalThis.requestAnimationFrame = (callback) => {
    const id = disposeFrames.length + 1
    disposeFrames.push({ id, callback })
    return id
  }
  globalThis.cancelAnimationFrame = (id) => {
    disposeCancelledFrames.push(id)
  }

  const disposable = useChat()
  disposable.handleWsEvent({ type: 'response_start' })
  disposable.handleWsEvent({ type: 'response_delta', content: 'stale' })

  assert.equal(disposeFrames.length, 1)
  assert.equal(disposable.messages.value[0].content, '')

  clearChatInstance()
  disposeFrames[0].callback()

  assert.deepEqual(disposeCancelledFrames, [1])
  assert.deepEqual(disposable.messages.value, [])
  assert.equal(disposable.canRetry(), false)

  globalThis.requestAnimationFrame = originalRequestAnimationFrame
  globalThis.cancelAnimationFrame = originalCancelAnimationFrame

  clearChatInstance()

  let resolveDisposedSend
  globalThis.fetch = async () => new Promise((resolve) => {
    resolveDisposedSend = resolve
  })

  const pendingSend = useChat()
  const pendingSendPromise = pendingSend.sendMessage('will dispose')

  assert.equal(pendingSend.messages.value.filter((message) => message.role === 'user').length, 1)

  clearChatInstance()
  resolveDisposedSend({
    ok: true,
    json: async () => ({
      response: 'late response',
      token_usage: { total_tokens: 9 },
    }),
  })
  await pendingSendPromise

  assert.deepEqual(pendingSend.messages.value, [])
  assert.equal(pendingSend.tokenInfo.value, null)

  globalThis.fetch = originalFetch

  clearChatInstance()

  const streaming = useChat()
  streaming.handleWsEvent({ type: 'response_start' })
  streaming.handleWsEvent({ type: 'response_delta', content: 'Hel' })
  streaming.handleWsEvent({ type: 'response_delta', content: 'lo' })

  assert.equal(streaming.messages.value.length, 1)
  assert.equal(streaming.messages.value[0].content, '')
  assert.equal(streaming.messages.value[0].streaming, true)

  streaming.handleWsEvent({ type: 'response_done' })

  assert.equal(streaming.messages.value[0].content, 'Hello')
  assert.equal(streaming.messages.value[0].streaming, false)

  clearChatInstance()

  const toolOnlyStream = useChat()
  toolOnlyStream.handleWsEvent({ type: 'response_start' })
  toolOnlyStream.handleWsEvent({
    type: 'tool_start',
    data: { id: 'tool-1', tool: 'search', args: { query: 'copy gap' } },
  })

  assert.equal(toolOnlyStream.messages.value.length, 1)
  assert.equal(toolOnlyStream.messages.value[0].role, 'tool')

  clearChatInstance()

  const shiftedToolIndex = useChat()
  shiftedToolIndex.addMessage('user', 'remove me')
  shiftedToolIndex.addToolMessage('tool-1', {
    tool: 'search',
    args: {},
    status: 'executing',
    result: null,
  })
  shiftedToolIndex.addToolMessage('tool-2', {
    tool: 'read_file',
    args: {},
    status: 'executing',
    result: null,
  })
  shiftedToolIndex.messages.value.splice(0, 1)
  shiftedToolIndex.updateToolMessage('tool-1', {
    status: 'success',
    result: 'first result',
  })

  assert.equal(shiftedToolIndex.messages.value[0].toolCallId, 'tool-1')
  assert.equal(shiftedToolIndex.messages.value[0].toolData.status, 'success')
  assert.equal(shiftedToolIndex.messages.value[0].toolData.result, 'first result')
  assert.equal(shiftedToolIndex.messages.value[1].toolCallId, 'tool-2')
  assert.equal(shiftedToolIndex.messages.value[1].toolData.status, 'executing')
  assert.equal(shiftedToolIndex.messages.value[1].toolData.result, null)

  clearChatInstance()

  const transcript = useChat()
  transcript.loadTranscript({
    messages: [
      { role: 'user', content: 'Search this' },
      {
        role: 'assistant',
        content: '\n\n',
        tool_calls: [
          {
            id: 'call-1',
            function: {
              name: 'search',
              arguments: '{"query":"copy gap"}',
            },
          },
        ],
      },
      { role: 'tool', tool_call_id: 'call-1', content: 'result' },
      { role: 'assistant', content: 'Done.' },
    ],
  })

  assert.deepEqual(
    transcript.messages.value.map((message) => message.role),
    ['user', 'tool', 'assistant'],
  )
  assert.equal(transcript.messages.value.at(-1).content, 'Done.')

  clearChatInstance()

  const interleavedTranscript = useChat()
  interleavedTranscript.loadTranscript({
    messages: [
      { role: 'user', content: 'Investigate' },
      {
        role: 'assistant',
        content: 'Searching...',
        tool_calls: [
          {
            id: 'call-1',
            function: {
              name: 'search',
              arguments: '{"query":"first"}',
            },
          },
        ],
      },
      { role: 'tool', tool_call_id: 'call-1', content: 'first result' },
      {
        role: 'assistant',
        content: 'Reading...',
        tool_calls: [
          {
            id: 'call-2',
            function: {
              name: 'read_file',
              arguments: '{"file_path":"notes.md"}',
            },
          },
        ],
      },
      { role: 'tool', tool_call_id: 'call-2', content: 'second result' },
      { role: 'assistant', content: 'Done.' },
    ],
    runtimeTurns: [
      { id: 'turn-1' },
    ],
    runtimeItems: [
      {
        id: 'reason-1',
        turn_id: 'turn-1',
        kind: 'agent_reasoning',
        detail: 'think 1',
        created_at: '2026-07-07T01:00:00.000Z',
        started_at: '2026-07-07T01:00:00.000Z',
        ended_at: '2026-07-07T01:00:01.000Z',
      },
      {
        id: 'tool-1',
        turn_id: 'turn-1',
        kind: 'tool_call',
        status: 'completed',
        detail: 'first result',
        created_at: '2026-07-07T01:00:02.000Z',
        started_at: '2026-07-07T01:00:02.000Z',
        ended_at: '2026-07-07T01:00:03.000Z',
        metadata: {
          tool_call_id: 'call-1',
          tool: 'search',
          args: { query: 'first' },
          success: true,
        },
      },
      {
        id: 'reason-2',
        turn_id: 'turn-1',
        kind: 'agent_reasoning',
        detail: 'think 2',
        created_at: '2026-07-07T01:00:04.000Z',
        started_at: '2026-07-07T01:00:04.000Z',
        ended_at: '2026-07-07T01:00:05.000Z',
      },
      {
        id: 'tool-2',
        turn_id: 'turn-1',
        kind: 'tool_call',
        status: 'completed',
        detail: 'second result',
        created_at: '2026-07-07T01:00:06.000Z',
        started_at: '2026-07-07T01:00:06.000Z',
        ended_at: '2026-07-07T01:00:07.000Z',
        metadata: {
          tool_call_id: 'call-2',
          tool: 'read_file',
          args: { file_path: 'notes.md' },
          success: true,
        },
      },
    ],
  })

  assert.deepEqual(
    interleavedTranscript.messages.value.map((message) => (
      message.role === 'thinking'
        ? `thinking:${message.content}`
        : message.role === 'tool'
          ? `tool:${message.toolData.tool}:${message.toolData.result}`
          : `${message.role}:${message.content}`
    )),
    [
      'user:Investigate',
      'thinking:think 1',
      'assistant:Searching...',
      'tool:search:first result',
      'thinking:think 2',
      'assistant:Reading...',
      'tool:read_file:second result',
      'assistant:Done.',
    ],
  )

  clearChatInstance()

  const parallelTranscript = useChat()
  parallelTranscript.loadTranscript({
    messages: [
      { role: 'user', content: 'Run both' },
      {
        role: 'assistant',
        content: 'Running both...',
        tool_calls: [
          {
            id: 'call-a',
            function: { name: 'search', arguments: '{"query":"a"}' },
          },
          {
            id: 'call-b',
            function: { name: 'read_file', arguments: '{"file_path":"b.md"}' },
          },
        ],
      },
      { role: 'tool', tool_call_id: 'call-a', content: 'result a' },
      { role: 'tool', tool_call_id: 'call-b', content: 'result b' },
      { role: 'assistant', content: 'Both done.' },
    ],
    runtimeTurns: [{ id: 'turn-parallel' }],
    runtimeItems: [
      {
        id: 'reason-parallel',
        turn_id: 'turn-parallel',
        kind: 'agent_reasoning',
        detail: 'prepare both',
        created_at: '2026-07-07T03:00:00.000Z',
      },
      {
        id: 'tool-b',
        turn_id: 'turn-parallel',
        kind: 'tool_call',
        status: 'completed',
        detail: 'result b',
        created_at: '2026-07-07T03:00:01.000Z',
        metadata: {
          tool_call_id: 'call-b',
          tool: 'read_file',
          args: { file_path: 'b.md' },
          success: true,
        },
      },
      {
        id: 'tool-a',
        turn_id: 'turn-parallel',
        kind: 'tool_call',
        status: 'completed',
        detail: 'result a',
        created_at: '2026-07-07T03:00:02.000Z',
        metadata: {
          tool_call_id: 'call-a',
          tool: 'search',
          args: { query: 'a' },
          success: true,
        },
      },
    ],
  })

  assert.deepEqual(
    parallelTranscript.messages.value.map((message) => (
      message.role === 'thinking'
        ? `thinking:${message.content}`
        : message.role === 'tool'
          ? `tool:${message.toolCallId}`
          : `${message.role}:${message.content}`
    )),
    [
      'user:Run both',
      'thinking:prepare both',
      'assistant:Running both...',
      'tool:call-b',
      'tool:call-a',
      'assistant:Both done.',
    ],
  )

  clearChatInstance()

  const malformedRuntimeToolTranscript = useChat()
  malformedRuntimeToolTranscript.loadTranscript({
    messages: [
      { role: 'user', content: 'Run one tool' },
      {
        role: 'assistant',
        content: '',
        tool_calls: [
          {
            id: 'call-raw',
            function: {
              name: 'search',
              arguments: '{"query":"raw"}',
            },
          },
        ],
      },
      { role: 'tool', tool_call_id: 'call-raw', content: 'raw result' },
      { role: 'assistant', content: 'Done.' },
    ],
    runtimeTurns: [
      { id: 'turn-1' },
    ],
    runtimeItems: [
      {
        id: 'tool-without-metadata',
        turn_id: 'turn-1',
        kind: 'tool_call',
        summary: 'search',
        detail: 'runtime result',
        created_at: '2026-07-07T01:00:02.000Z',
      },
    ],
  })

  assert.deepEqual(
    malformedRuntimeToolTranscript.messages.value.map((message) => (
      message.role === 'tool'
        ? `tool:${message.toolCallId}:${message.toolData.tool}:${message.toolData.result}`
        : `${message.role}:${message.content}`
    )),
    [
      'user:Run one tool',
      'tool:call-raw:search:raw result',
      'assistant:Done.',
    ],
  )

  clearChatInstance()

  const emptyTurnTranscript = useChat()
  emptyTurnTranscript.loadTranscript({
    messages: [
      { role: 'user', content: 'First turn' },
      { role: 'assistant', content: 'No tools here.' },
      { role: 'user', content: 'Second turn' },
      { role: 'assistant', content: 'Done.' },
    ],
    runtimeTurns: [
      { id: 'turn-empty' },
      { id: 'turn-with-reasoning' },
    ],
    runtimeItems: [
      {
        id: 'reason-later',
        turn_id: 'turn-with-reasoning',
        kind: 'agent_reasoning',
        detail: 'later thought',
        created_at: '2026-07-07T02:00:00.000Z',
      },
    ],
  })

  assert.deepEqual(
    emptyTurnTranscript.messages.value.map((message) => (
      message.role === 'thinking'
        ? `thinking:${message.content}`
        : `${message.role}:${message.content}`
    )),
    [
      'user:First turn',
      'assistant:No tools here.',
      'user:Second turn',
      'thinking:later thought',
      'assistant:Done.',
    ],
  )

  clearChatInstance()

  // HTTP fallback can win the race before queued WS thinking/response events
  // are applied. The HTTP copy has no thinking; the later WS stream must replace
  // it instead of leaving a duplicate answer.
  globalThis.fetch = async () => ({
    ok: true,
    json: async () => ({ response: 'same answer' }),
  })

  const httpFirst = useChat()
  const httpFirstSend = httpFirst.sendMessage('duplicate race')
  await Promise.resolve()
  await httpFirstSend

  assert.equal(
    httpFirst.messages.value.filter((message) => message.role === 'assistant').length,
    1,
  )
  assert.equal(httpFirst.messages.value.at(-1).content, 'same answer')

  httpFirst.handleWsEvent({ type: 'thinking_delta', content: 'reason' })
  httpFirst.handleWsEvent({ type: 'response_start' })
  httpFirst.handleWsEvent({ type: 'response_delta', content: 'same answer' })
  httpFirst.handleWsEvent({ type: 'response_done', content: 'same answer' })

  assert.deepEqual(
    httpFirst.messages.value.map((message) => (
      message.role === 'thinking'
        ? `thinking:${message.content}`
        : `${message.role}:${message.content}`
    )),
    [
      'user:duplicate race',
      'thinking:reason',
      'assistant:same answer',
    ],
  )

  clearChatInstance()

  globalThis.fetch = async () => ({
    ok: true,
    json: async () => ({ response: 'same answer' }),
  })

  const thinkingBlocksHttp = useChat()
  const thinkingBlocksSend = thinkingBlocksHttp.sendMessage('block http')
  thinkingBlocksHttp.handleWsEvent({ type: 'thinking_delta', content: 'reason first' })
  await thinkingBlocksSend

  assert.equal(
    thinkingBlocksHttp.messages.value.filter((message) => message.role === 'assistant').length,
    0,
  )

  thinkingBlocksHttp.handleWsEvent({ type: 'response_start' })
  thinkingBlocksHttp.handleWsEvent({ type: 'response_done', content: 'same answer' })
  assert.deepEqual(
    thinkingBlocksHttp.messages.value.map((message) => (
      message.role === 'thinking'
        ? `thinking:${message.content}`
        : `${message.role}:${message.content}`
    )),
    [
      'user:block http',
      'thinking:reason first',
      'assistant:same answer',
    ],
  )

  clearChatInstance()

  const frames = []
  const cancelledFrames = new Set()
  globalThis.requestAnimationFrame = (callback) => {
    const id = frames.length + 1
    frames.push({ id, callback })
    return id
  }
  globalThis.cancelAnimationFrame = (id) => {
    cancelledFrames.add(id)
  }
  globalThis.fetch = async () => ({
    ok: true,
    json: async () => ({}),
  })

  const rollover = useChat()
  rollover.handleWsEvent({ type: 'response_start' })
  rollover.handleWsEvent({ type: 'response_delta', content: 'old' })

  assert.equal(frames.length, 1)

  const sendPromise = rollover.sendMessage('next turn')
  rollover.handleWsEvent({ type: 'response_start' })
  rollover.handleWsEvent({ type: 'response_delta', content: 'new' })

  for (const frame of frames) {
    if (!cancelledFrames.has(frame.id)) frame.callback()
  }

  const lastAssistant = rollover.messages.value.filter((message) => message.role === 'assistant').at(-1)
  assert.equal(lastAssistant.content, 'new')

  rollover.handleWsEvent({ type: 'response_done' })
  await sendPromise

  clearChatInstance()

  const retryRequests = []
  const retryResponses = [
    { error: 'upload failed' },
    { response: 'retried ok' },
  ]
  globalThis.fetch = async (_url, options = {}) => {
    retryRequests.push(JSON.parse(options.body || '{}'))
    return {
      ok: true,
      json: async () => retryResponses.shift() || { response: 'unexpected extra retry' },
    }
  }

  const attachmentRetry = useChat()
  const retryImage = {
    dataUrl: 'data:image/png;base64,aW1hZ2U=',
    name: 'photo.png',
    type: 'image/png',
    size: 5,
  }
  const retryFile = {
    dataUrl: 'data:text/plain;base64,ZmlsZQ==',
    name: 'notes.txt',
    type: 'text/plain',
    size: 4,
  }

  await attachmentRetry.sendMessage('', [retryImage], [retryFile])

  assert.equal(attachmentRetry.messages.value.filter((message) => message.role === 'user').length, 1)
  assert.equal(attachmentRetry.messages.value.at(-1).role, 'error')

  await attachmentRetry.retryLastMessage()

  assert.equal(attachmentRetry.messages.value.filter((message) => message.role === 'user').length, 1)
  assert.equal(attachmentRetry.messages.value.at(-1).role, 'assistant')
  assert.deepEqual(retryRequests.map((request) => request.images), [
    [{ dataUrl: retryImage.dataUrl, name: retryImage.name, type: retryImage.type, size: retryImage.size }],
    [{ dataUrl: retryImage.dataUrl, name: retryImage.name, type: retryImage.type, size: retryImage.size }],
  ])
  assert.deepEqual(retryRequests.map((request) => request.files), [
    [{ dataUrl: retryFile.dataUrl, name: retryFile.name, type: retryFile.type, size: retryFile.size }],
    [{ dataUrl: retryFile.dataUrl, name: retryFile.name, type: retryFile.type, size: retryFile.size }],
  ])
} finally {
  clearChatInstance()
  globalThis.localStorage = originalLocalStorage
  globalThis.fetch = originalFetch
  globalThis.requestAnimationFrame = originalRequestAnimationFrame
  globalThis.cancelAnimationFrame = originalCancelAnimationFrame
}
