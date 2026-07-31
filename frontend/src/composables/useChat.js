import { ref } from 'vue'
import {
  normalizeFileAttachments,
  normalizeFilePayload,
  normalizeImageAttachments,
  normalizeImagePayload,
} from './chatAttachments.js'
import { normalizeAssistantChain } from './chatAssistantChain.js'
import {
  hasActiveAssistantStream,
  hasAssistantResponse,
  shouldAppendHttpAssistantResponse,
} from './chatHttpResponse.js'
import { createChatRetryState } from './chatRetryState.js'
import { createStreamingDeltaBuffer } from './streamingDeltaBuffer.js'
import { useApi } from './useApi.js'
import { useSession } from './useSession.js'

let instance = null

// Sentinel id for the most recent retryable error. Keeping a stable id lets the
// next retryable error replace the previous one instead of stacking, and makes
// it cheap to clear on the next successful send.
const LAST_ERROR_MESSAGE_ID = '__atri_last_error__'

function emptyResearchStatus() {
  return {
    visible: false,
    active: false,
    phase: '',
    state: '',
    evidenceCount: 0,
    toolCalls: 0,
    webFetches: 0,
    activeSubagents: 0,
    totalSubagents: 0,
  }
}

function safeCount(value, fallback = 0) {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed >= 0 ? Math.trunc(parsed) : fallback
}

export function useChat() {
  if (instance) return instance

  const api = useApi()
  const { currentId: sessionId, switchSession, normalizeSessionId } = useSession()

  const messages = ref([])
  const sending = ref(false)
  const tokenInfo = ref(null)
  const todoSnapshot = ref(emptyTodoSnapshot())
  const researchStatus = ref(emptyResearchStatus())

  // Tracks ids of error messages currently in the list so we can wipe them on
  // the next send (errors shouldn't linger once the user retries or moves on).
  let errorIds = new Set()
  let disposed = false

  // Thinking state
  const thinkingText = ref('')
  const thinkingStart = ref(0)
  const thinkingBlock = ref(null) // { content, startTime, done }
  // Tool cards
  const toolCards = ref({}) // id -> { tool, args, status: 'executing'|'success'|'failed', result }
  let streamingAssistantId = null
  let streamingMessage = null
  // HTTP /api/chat can resolve before queued WS thinking/response events are
  // applied. Track live WS transcript activity so the HTTP fallback does not
  // leave a duplicate answer (first without thinking, then with thinking).
  let liveTranscriptSeen = false
  const httpFallbackMessageIds = new Set()
  const assistantDeltaBuffer = createStreamingDeltaBuffer({
    apply: applyAssistantDelta,
  })

  function beginTranscriptTurn() {
    liveTranscriptSeen = false
    httpFallbackMessageIds.clear()
    researchStatus.value = emptyResearchStatus()
  }

  function discardHttpFallbackMessages() {
    if (!httpFallbackMessageIds.size) return
    for (const id of [...httpFallbackMessageIds]) {
      removeMessage(id)
    }
    httpFallbackMessageIds.clear()
  }

  function noteLiveTranscript(msgType) {
    if (
      msgType === 'thinking'
      || msgType === 'thinking_delta'
      || msgType === 'thinking_done'
      || msgType === 'response_start'
      || msgType === 'response_delta'
      || msgType === 'response_done'
    ) {
      liveTranscriptSeen = true
    }
    if (
      msgType === 'response_start'
      || msgType === 'response_delta'
      || msgType === 'response_done'
    ) {
      discardHttpFallbackMessages()
    }
  }

  function trackHttpFallbackMessage(message) {
    if (message?.id) httpFallbackMessageIds.add(message.id)
  }

  // WebSocket event handler — called from ChatPage
  function handleWsEvent(msg) {
    if (disposed) return
    updateResearchStatus(msg)
    noteLiveTranscript(msg.type)
    if (msg.type === 'thinking') {
      startThinkingBlock()
    }
    if (msg.type === 'thinking_delta') {
      if (!thinkingBlock.value) {
        startThinkingBlock()
      } else if (thinkingBlock.value.done && (thinkingBlock.value.content || '').trim()) {
        startThinkingBlock()
      }
      thinkingBlock.value.content += msg.content || ''
    }
    if (msg.type === 'thinking_done') {
      finishThinkingBlock()
    }
    if (msg.type === 'response_start') {
      finishThinkingBlock()
      ensureAssistantStream()
    }
    if (msg.type === 'response_delta') {
      finishThinkingBlock()
      appendAssistantDelta(msg.content || '')
    }
    if (msg.type === 'response_done') {
      finishThinkingBlock()
      finishAssistantStream(msg.content || '')
    }
    if (msg.type === 'todo_snapshot') {
      todoSnapshot.value = normalizeTodoSnapshot(msg.todo || msg.todo_snapshot || msg)
      finishThinkingBlock()
      finishAssistantStream()
      addTodoMessage(todoSnapshot.value)
    }
    if (msg.type === 'tool_start') {
      finishThinkingBlock()
      finishAssistantStream()
      if (msg.data.tool === 'todo') return
      addToolMessage(msg.data.id, {
        tool: msg.data.tool,
        args: msg.data.args,
        status: 'executing',
        result: null,
      })
      toolCards.value = {
        ...toolCards.value,
        [msg.data.id]: {
          tool: msg.data.tool,
          args: msg.data.args,
          status: 'executing',
          result: null,
        },
      }
    }
    if (msg.type === 'tool_end') {
      finishThinkingBlock()
      if (msg.data.tool === 'todo') return
      updateToolMessage(msg.data.id, {
        tool: msg.data.tool,
        args: msg.data.args,
        status: msg.data.success ? 'success' : 'failed',
        result: msg.data.result_preview || null,
        resultCompressed: Boolean(msg.data.result_compressed),
        resultId: msg.data.result_id || '',
      })
      toolCards.value = {
        ...toolCards.value,
        [msg.data.id]: {
          ...toolCards.value[msg.data.id],
          tool: msg.data.tool,
          args: msg.data.args,
          status: msg.data.success ? 'success' : 'failed',
          result: msg.data.result_preview || null,
          resultCompressed: Boolean(msg.data.result_compressed),
          resultId: msg.data.result_id || '',
        },
      }
    }
  }

  function updateResearchStatus(msg) {
    const type = String(msg?.type || '')
    if (type === 'research_started') {
      researchStatus.value = {
        ...emptyResearchStatus(),
        visible: true,
        active: true,
        phase: String(msg.phase || 'created'),
        state: 'researching',
      }
      return
    }
    if (!type.startsWith('research_')) return
    const current = researchStatus.value.visible
      ? researchStatus.value
      : { ...emptyResearchStatus(), visible: true, active: true }
    if (type === 'research_phase') {
      researchStatus.value = { ...current, phase: String(msg.phase || current.phase) }
      return
    }
    if (type === 'research_budget') {
      researchStatus.value = {
        ...current,
        state: String(msg.state || current.state || 'researching'),
        toolCalls: safeCount(msg.research_tool_calls, current.toolCalls),
        webFetches: safeCount(msg.web_fetches, current.webFetches),
        activeSubagents: safeCount(msg.active_subagents, current.activeSubagents),
        totalSubagents: safeCount(msg.total_subagents, current.totalSubagents),
      }
      return
    }
    if (type === 'research_evidence') {
      const fallback = current.evidenceCount + (msg.created === false ? 0 : 1)
      researchStatus.value = {
        ...current,
        evidenceCount: safeCount(msg.evidence_count, fallback),
      }
      return
    }
    if (type === 'research_subagent_started') {
      // research_budget is authoritative and is emitted before branch events.
      return
    }
    if (type === 'research_subagent_finished') {
      // The following research_budget event releases the authoritative slot count.
      return
    }
    if (type === 'research_completed' || type === 'research_cancelled') {
      researchStatus.value = {
        ...current,
        active: false,
        phase: type === 'research_completed' ? 'completed' : 'cancelled',
        state: type === 'research_completed' ? 'completed' : 'cancelled',
        activeSubagents: 0,
      }
    }
  }

  function clearThinking() {
    if (disposed) return
    finishThinkingBlock()
    thinkingText.value = ''
    thinkingStart.value = 0
    thinkingBlock.value = null
  }

  function clearToolCards() {
    if (disposed) return
    toolCards.value = {}
  }

  function makeId() {
    return Date.now().toString(36) + Math.random().toString(36).slice(2)
  }

  function addMessage(role, content, md = false, extra = {}) {
    if (disposed) return null
    const message = {
      id: makeId(),
      role,
      content,
      md,
      time: new Date(),
      ...extra,
    }
    messages.value.push(message)
    return message
  }

  function hasVisibleText(content) {
    if (typeof content === 'string') return content.trim().length > 0
    if (content == null) return false
    return String(content).trim().length > 0
  }

  function dismissErrorMessages() {
    if (disposed) return
    if (!errorIds.size) return
    const doomed = errorIds
    errorIds = new Set()
    messages.value = messages.value.filter((message) => !doomed.has(message.id))
  }

  // Adds (or replaces) a single error message in the transcript. replaceLast
  // collapses repeated failures into one entry instead of stacking them.
  function addErrorMessage({ title, detail = '', retriable = false, kind = 'error' }) {
    if (disposed) return
    const indexForReplace = messages.value.findIndex((m) => m.id === LAST_ERROR_MESSAGE_ID)
    const payload = {
      id: LAST_ERROR_MESSAGE_ID,
      role: 'error',
      errorKind: kind,
      title: typeof title === 'string' && title.trim() ? title : 'Something went wrong',
      detail: typeof detail === 'string' ? detail : '',
      retriable: Boolean(retriable),
      time: new Date(),
    }
    errorIds.add(payload.id)
    if (indexForReplace >= 0) {
      messages.value.splice(indexForReplace, 1, payload)
    } else {
      messages.value.push(payload)
    }
  }

  const retryState = createChatRetryState({
    getMessages: () => messages.value,
    isSending: () => sending.value,
    clearErrors: dismissErrorMessages,
    addUserMessage: ({ messageText, imagePayload, filePayload }) => addMessage('user', messageText, false, {
      attachments: [
        ...normalizeImageAttachments(imagePayload),
        ...normalizeFileAttachments(filePayload),
      ],
    }),
  })

  function addOrPatchAssistantMessage(content, attachments = []) {
    if (disposed) return null
    const text = String(content || '')
    const lastIndexFromEnd = [...messages.value].reverse().findIndex((m) =>
      m.role === 'assistant' && !m.streaming && String(m.content || '') === text
    )
    if (lastIndexFromEnd >= 0) {
      const index = messages.value.length - 1 - lastIndexFromEnd
      const current = messages.value[index]
      const next = {
        ...current,
        attachments: mergeAttachments(current.attachments, attachments),
      }
      messages.value.splice(index, 1, next)
      return next
    }
    return addMessage('assistant', text, true, { attachments })
  }

  async function addAssistantHttpResponse(result) {
    if (disposed || liveTranscriptSeen) return false
    const response = String(result?.response || '')
    if (Array.isArray(result?.chain)) {
      const parsed = normalizeAssistantChain(result.chain, response, makeId)
      const hasAttachments = parsed.attachments.length > 0
      if (!parsed.text && hasAttachments) {
        if (liveTranscriptSeen || hasActiveAssistantStream(messages)) return false
        trackHttpFallbackMessage(
          addMessage('assistant', '', true, { attachments: parsed.attachments }),
        )
        return true
      }
      if (await shouldAppendHttpAssistantResponse(messages, parsed.text)) {
        if (disposed || liveTranscriptSeen) return false
        trackHttpFallbackMessage(addOrPatchAssistantMessage(parsed.text, parsed.attachments))
        return true
      }
      if (hasAttachments && hasAssistantResponse(messages, parsed.text)) {
        addOrPatchAssistantMessage(parsed.text, parsed.attachments)
        return true
      }
      return false
    }

    if (await shouldAppendHttpAssistantResponse(messages, response)) {
      if (disposed || liveTranscriptSeen) return false
      trackHttpFallbackMessage(addMessage('assistant', response, true))
      return true
    }
    return false
  }

  function startThinkingBlock() {
    if (disposed) return
    finishThinkingBlock()
    const now = Date.now()
    const block = {
      id: makeId(),
      role: 'thinking',
      content: '',
      startTime: now,
      endTime: null,
      done: false,
      time: new Date(),
    }
    thinkingBlock.value = block
    messages.value.push(block)
  }

  function findMessageIndex(id) {
    return messages.value.findIndex((m) => m.id === id)
  }

  function patchMessage(id, patch) {
    if (disposed) return null
    const index = findMessageIndex(id)
    if (index < 0) return null
    const next = { ...messages.value[index], ...patch }
    messages.value.splice(index, 1, next)
    return next
  }

  function removeMessage(id) {
    if (disposed) return
    const index = findMessageIndex(id)
    if (index >= 0) {
      messages.value.splice(index, 1)
    }
  }

  function finishThinkingBlock() {
    if (disposed) return
    const current = thinkingBlock.value
    if (!current || current.done) return
    const endTime = Date.now()
    const patched = patchMessage(current.id, { done: true, endTime })
    thinkingBlock.value = patched || { ...current, done: true, endTime }
  }

  function ensureAssistantStream() {
    if (disposed) return null
    if (streamingAssistantId && streamingMessage) {
      return streamingMessage
    }

    streamingAssistantId = makeId()
    const message = {
      id: streamingAssistantId,
      role: 'assistant',
      content: '',
      md: true,
      streaming: true,
      time: new Date(),
    }
    messages.value.push(message)
    streamingMessage = messages.value[messages.value.length - 1]
    return streamingMessage
  }

  function appendAssistantDelta(delta) {
    if (disposed) return
    if (!delta) return
    ensureAssistantStream()
    assistantDeltaBuffer.append(delta)
  }

  function applyAssistantDelta(delta) {
    if (disposed) return
    if (!delta || !streamingAssistantId || !streamingMessage) return
    const msg = streamingMessage
    streamingMessage = patchMessage(msg.id, {
      content: (msg.content || '') + delta,
      streaming: true,
    })
  }

  function finishAssistantStream(finalContent = '') {
    if (disposed) return
    assistantDeltaBuffer.flush()
    if (!streamingAssistantId || !streamingMessage) {
      const text = typeof finalContent === 'string' ? finalContent : String(finalContent || '')
      if (hasVisibleText(text) && !hasAssistantResponse(messages, text)) {
        addMessage('assistant', text, true)
      }
      return
    }

    const nextContent = hasVisibleText(finalContent)
      ? String(finalContent)
      : streamingMessage.content
    if (!hasVisibleText(nextContent)) {
      removeMessage(streamingAssistantId)
      streamingMessage = null
      streamingAssistantId = null
      return
    }

    streamingMessage = patchMessage(streamingAssistantId, {
      content: nextContent,
      streaming: false,
    })
    streamingMessage = null
    streamingAssistantId = null
  }

  function findToolMessageIndex(toolCallId) {
    return messages.value.findIndex((message) =>
      message.role === 'tool' && message.toolCallId === toolCallId
    )
  }

  function patchToolMessage(index, patch) {
    if (disposed) return
    const current = messages.value[index]
    if (!current) return
    messages.value.splice(index, 1, {
      ...current,
      toolData: {
        ...current.toolData,
        ...patch,
      },
    })
  }

  function addToolMessage(toolCallId, toolData) {
    if (disposed) return
    const existing = findToolMessageIndex(toolCallId)
    if (existing >= 0) {
      patchToolMessage(existing, toolData)
      return
    }

    messages.value.push({
      id: toolCallId || makeId(),
      role: 'tool',
      toolCallId,
      toolData,
      time: new Date(),
    })
  }

  function updateToolMessage(toolCallId, patch) {
    if (disposed) return
    const existing = findToolMessageIndex(toolCallId)
    if (existing < 0) {
      addToolMessage(toolCallId, patch)
      return
    }

    patchToolMessage(existing, patch)
  }

  function resetMessages() {
    if (disposed) return
    assistantDeltaBuffer.clear()
    messages.value = []
    todoSnapshot.value = emptyTodoSnapshot()
    researchStatus.value = emptyResearchStatus()
    streamingAssistantId = null
    streamingMessage = null
    errorIds = new Set()
    retryState.reset()
  }

  function parseToolArgs(raw) {
    if (!raw) return {}
    if (typeof raw === 'object') return raw
    try {
      return JSON.parse(raw)
    } catch {
      return {}
    }
  }

  function loadTranscript(transcript) {
    if (disposed) return
    resetMessages()
    const callsById = new Map()
    const rawMessages = Array.isArray(transcript) ? transcript : transcript?.messages || []
    const runtimeTurns = Array.isArray(transcript?.runtimeTurns) ? transcript.runtimeTurns : []
    const runtimeItems = Array.isArray(transcript?.runtimeItems) ? transcript.runtimeItems : []
    todoSnapshot.value = normalizeTodoSnapshot(transcript?.todoSnapshot)
    const replayItemsByTurn = new Map()
    const renderedToolCallIds = new Set()
    const replayItems = runtimeItems
      .map((item, index) => ({ item, index }))
      .filter(({ item }) => isReplayRuntimeItem(item))
      .sort(compareRuntimeItemOrder)

    replayItems.forEach(({ item }) => {
      const list = replayItemsByTurn.get(item.turn_id) || []
      list.push(item)
      replayItemsByTurn.set(item.turn_id, list)
    })

    const orderedTurnIds = runtimeTurns
      .map((turn) => turn?.id)
      .filter(Boolean)
    const fallbackReplayItems = replayItems
      .map(({ item }) => item)
      .filter((item) => !item.turn_id || !orderedTurnIds.includes(item.turn_id))
    let turnIndex = 0
    let activeReplayItems = []
    let activeReplayIndex = 0

    function runtimeToolCallId(item) {
      if (!isRuntimeToolItem(item)) return ''
      return String(runtimeItemMetadata(item).tool_call_id || '')
    }

    function renderReplayItem(item) {
      const toolCallId = runtimeToolCallId(item)
      if (toolCallId && renderedToolCallIds.has(toolCallId)) return
      addRuntimeTimelineMessage(item)
      if (toolCallId) renderedToolCallIds.add(toolCallId)
    }

    function flushActiveReplayItems() {
      while (activeReplayIndex < activeReplayItems.length) {
        renderReplayItem(activeReplayItems[activeReplayIndex])
        activeReplayIndex += 1
      }
      activeReplayItems = []
      activeReplayIndex = 0
    }

    function openNextRuntimeTurn() {
      activeReplayItems = []
      activeReplayIndex = 0
      if (turnIndex < orderedTurnIds.length) {
        const turnId = orderedTurnIds[turnIndex]
        turnIndex += 1
        activeReplayItems = replayItemsByTurn.get(turnId) || []
      } else if (fallbackReplayItems.length) {
        activeReplayItems = [fallbackReplayItems.shift()]
      }
    }

    function takeReplaySegment(toolCalls) {
      const toolCallIds = new Set(
        toolCalls
          .map((call) => String(call?.id || ''))
          .filter(Boolean),
      )
      if (!toolCallIds.size) return null

      let firstMatch = -1
      let lastMatch = -1
      for (let index = activeReplayIndex; index < activeReplayItems.length; index += 1) {
        if (!toolCallIds.has(runtimeToolCallId(activeReplayItems[index]))) continue
        if (firstMatch < 0) firstMatch = index
        lastMatch = index
      }
      if (firstMatch < 0) return null

      const beforeAssistant = activeReplayItems.slice(activeReplayIndex, firstMatch)
      const afterAssistant = activeReplayItems.slice(firstMatch, lastMatch + 1)
      activeReplayIndex = lastMatch + 1
      return { beforeAssistant, afterAssistant }
    }

    rawMessages.forEach((m) => {
      if (m.tool_calls?.length) {
        m.tool_calls.forEach((call) => {
          callsById.set(call.id, {
            tool: call.function?.name || call.name || 'tool',
            args: parseToolArgs(call.function?.arguments || call.arguments),
          })
        })
      }

      if (m.role === 'user' && m.content) {
        // Tool-result user messages continue the active turn instead of opening
        // the next Runtime turn.
        const isToolResult = Array.isArray(m.content)
          && m.content.some(part => part?.type === 'tool_result')
        if (!isToolResult) flushActiveReplayItems()

        const userContent = stripInternalUserContext(m._atri_display_content || m.content)
        const parsed = parseUserContent(userContent)
        addMessage('user', parsed.text, false, {
          attachments: mergeAttachments(
            parsed.attachments,
            normalizeStoredAttachments(m._atri_attachments),
          ),
        })
        if (!isToolResult) openNextRuntimeTurn()
      } else if (m.role === 'assistant') {
        const toolCalls = Array.isArray(m.tool_calls) ? m.tool_calls : []
        const replaySegment = toolCalls.length ? takeReplaySegment(toolCalls) : null
        replaySegment?.beforeAssistant.forEach(renderReplayItem)
        if (!toolCalls.length) flushActiveReplayItems()

        const attachments = normalizeStoredAttachments(m._atri_attachments)
        if (hasVisibleText(m.content) || attachments.length) {
          addMessage('assistant', hasVisibleText(m.content) ? String(m.content) : '', true, {
            attachments,
          })
        }
        replaySegment?.afterAssistant.forEach(renderReplayItem)
      } else if (m.role === 'tool') {
        const call = callsById.get(m.tool_call_id) || {}
        if (call.tool === 'todo') {
          return
        }

        const toolCallId = String(m.tool_call_id || '')
        if (toolCallId && renderedToolCallIds.has(toolCallId)) return
        const result = m.content || ''
        addToolMessage(m.tool_call_id, {
          tool: call.tool || 'tool',
          args: call.args || {},
          status: result.startsWith('Error') ? 'failed' : 'success',
          result,
          resultCompressed: result.startsWith('<persisted-output>'),
          resultId: extractToolResultId(result),
        })
        if (toolCallId) renderedToolCallIds.add(toolCallId)
      }
    })

    flushActiveReplayItems()
    while (turnIndex < orderedTurnIds.length) {
      openNextRuntimeTurn()
      flushActiveReplayItems()
    }
    fallbackReplayItems.forEach(renderReplayItem)
    addTodoMessage(todoSnapshot.value)
  }

  function isReplayRuntimeItem(item) {
    if (!item || typeof item !== 'object') return false
    if (item.kind === 'agent_reasoning') {
      return hasVisibleText(item.detail)
    }
    return isRuntimeToolItem(item) && hasRuntimeToolMetadata(item)
  }

  function isRuntimeToolItem(item) {
    return item?.kind === 'tool_call' || item?.kind === 'command_execution'
  }

  function hasRuntimeToolMetadata(item) {
    const metadata = runtimeItemMetadata(item)
    return Boolean(metadata.tool || metadata.tool_call_id)
  }

  function runtimeItemMetadata(item) {
    return item?.metadata && typeof item.metadata === 'object' ? item.metadata : {}
  }

  function runtimeItemTime(item) {
    return parseRuntimeTime(item?.started_at || item?.created_at || item?.ended_at)
  }

  function compareRuntimeItemOrder(left, right) {
    const leftTime = runtimeItemTime(left.item)
    const rightTime = runtimeItemTime(right.item)
    if (leftTime !== rightTime) return leftTime - rightTime
    return left.index - right.index
  }

  function addRuntimeTimelineMessage(item) {
    if (item?.kind === 'agent_reasoning') {
      addRuntimeThinkingMessage(item)
      return
    }
    if (isRuntimeToolItem(item)) {
      addRuntimeToolMessage(item)
    }
  }

  function addRuntimeToolMessage(item) {
    const metadata = runtimeItemMetadata(item)
    const toolCallId = String(metadata.tool_call_id || item.id || makeId())
    const result = String(item.detail || '')
    const failed = item.status === 'failed' || metadata.success === false || result.startsWith('Error')
    addToolMessage(toolCallId, {
      tool: String(metadata.tool || item.summary || 'tool'),
      args: metadata.args && typeof metadata.args === 'object' ? metadata.args : {},
      status: failed ? 'failed' : 'success',
      result,
      resultCompressed: Boolean(metadata.result_compressed) || result.startsWith('<persisted-output>'),
      resultId: String(metadata.result_id || extractToolResultId(result)),
    })
  }

  function addRuntimeThinkingMessage(item) {
    const content = String(item.detail || '').trim()
    if (!content) return
    const startTime = parseRuntimeTime(item.started_at || item.created_at) || Date.now()
    const endTime = parseRuntimeTime(item.ended_at) || startTime
    messages.value.push({
      id: item.id || makeId(),
      role: 'thinking',
      content,
      startTime,
      endTime,
      done: true,
      time: new Date(startTime),
    })
  }

  function parseRuntimeTime(value) {
    if (!value) return 0
    const parsed = Date.parse(String(value))
    return Number.isNaN(parsed) ? 0 : parsed
  }

  function extractToolResultId(result) {
    if (!result) return ''
    const match = String(result).match(/^(?:tool_result_id|Tool result id):\s*(\S+)/m)
    return match ? match[1] : ''
  }

  function parseUserContent(content) {
    if (typeof content === 'string') {
      return { text: content, attachments: [] }
    }
    if (!Array.isArray(content)) {
      return { text: String(content || ''), attachments: [] }
    }

    const textParts = []
    const attachments = []
    content.forEach((part, index) => {
      if (typeof part === 'string') {
        textParts.push(part)
        return
      }
      if (!part || typeof part !== 'object') return
      if (part.type === 'text' && typeof part.text === 'string') {
        textParts.push(part.text)
        return
      }
      if (part.type === 'image_url') {
        const src = typeof part.image_url === 'string' ? part.image_url : part.image_url?.url
        if (src) {
          attachments.push({
            id: makeId(),
            name: part.name || `image-${index + 1}`,
            type: mimeFromDataUrl(src),
            size: 0,
            src,
          })
        }
        return
      }
      if (part.type === 'image' && part.source?.type === 'base64') {
        const mediaType = part.source.media_type || 'image/png'
        attachments.push({
          id: makeId(),
          name: part.name || `image-${index + 1}`,
          type: mediaType,
          size: 0,
          src: `data:${mediaType};base64,${part.source.data || ''}`,
        })
      }
    })

    return { text: textParts.join('').trim(), attachments }
  }

  function stripInternalUserContext(content) {
    if (typeof content === 'string') {
      return stripInternalContextText(content)
    }
    if (!Array.isArray(content)) return content

    const index = content.findIndex((part) => part?.type === 'text' && typeof part.text === 'string')
    if (index < 0) return content

    const stripped = stripInternalContextText(content[index].text)
    if (stripped === content[index].text) return content

    const next = [...content]
    next[index] = { ...content[index], text: stripped }
    return next
  }

  function stripInternalContextText(text) {
    const source = String(text || '')
    const trimmed = source.trimStart()
    const internalHeader = '[ATRI internal context]'
    const marker = '[Current request]\n'
    const markerIndex = trimmed.lastIndexOf(marker)
    if (markerIndex < 0) return source
    const prefix = trimmed.slice(0, markerIndex)
    if (!trimmed.startsWith(internalHeader) && !looksLikeLegacyInternalContext(prefix)) {
      return source
    }
    return trimmed.slice(markerIndex + marker.length)
  }

  function looksLikeLegacyInternalContext(prefix) {
    const firstLine = String(prefix || '').trimStart().split(/\r?\n/, 1)[0].trim().toLowerCase()
    return firstLine.startsWith('[')
      && firstLine.endsWith(']')
      && (firstLine.includes('context') || firstLine.includes('before this request'))
  }

  function mimeFromDataUrl(src) {
    const match = String(src || '').match(/^data:([^;,]+)[;,]/)
    return match ? match[1] : ''
  }

  function normalizeStoredAttachments(rawAttachments) {
    if (!Array.isArray(rawAttachments)) return []
    return rawAttachments
      .map((attachment, index) => {
        if (attachment?.kind === 'file') {
          return {
            id: makeId(),
            kind: 'file',
            name: attachment.name || `file-${index + 1}`,
            type: attachment.type || '',
            size: Number(attachment.size || 0),
          }
        }
        return {
          id: makeId(),
          name: attachment.name || `generated-${index + 1}`,
          type: attachment.type || mimeFromDataUrl(attachment.src),
          size: Number(attachment.size || 0),
          src: attachment.src || attachment.url || '',
        }
      })
      .filter((attachment) => attachment.kind === 'file' || attachment.src)
  }

  function mergeAttachments(existing = [], incoming = []) {
    const seen = new Set()
    return [...existing, ...incoming].filter((image) => {
      const key = image.src || image.id || image.name
      if (!key || seen.has(key)) return false
      seen.add(key)
      return true
    })
  }

  function emptyTodoSnapshot() {
    return {
      items: [],
      total: 0,
      completed: 0,
      all_completed: false,
      updated_at: '',
      session_id: '',
    }
  }

  function normalizeTodoSnapshot(raw) {
    if (!raw || typeof raw !== 'object') return emptyTodoSnapshot()
    const items = Array.isArray(raw.items)
      ? raw.items
        .map((item, index) => ({
          id: String(item?.id || `todo-${index + 1}`),
          content: String(item?.content || item?.title || item?.text || '').trim(),
          status: item?.status === 'completed' ? 'completed' : 'pending',
        }))
        .filter((item) => item.content)
      : []
    const completed = items.filter((item) => item.status === 'completed').length
    return {
      items,
      total: Number(raw.total ?? items.length),
      completed: Number(raw.completed ?? completed),
      all_completed: Boolean(raw.all_completed ?? (items.length > 0 && completed === items.length)),
      updated_at: String(raw.updated_at || ''),
      session_id: String(raw.session_id || ''),
    }
  }

  function addTodoMessage(snapshot) {
    if (!snapshot?.items?.length) return
    const last = messages.value[messages.value.length - 1]
    if (last?.role === 'todo') {
      patchMessage(last.id, {
        todoSnapshot: snapshot,
        time: new Date(),
      })
      return
    }
    messages.value.push({
      id: makeId(),
      role: 'todo',
      todoSnapshot: snapshot,
      time: new Date(),
    })
  }

  async function cancelMessage() {
    if (disposed) return
    if (!sending.value) return
    try {
      await api.cancelChat(sessionId.value)
    } catch {
      // best-effort
    }
  }

  async function performSend({ messageText, imagePayload, filePayload }) {
    if (disposed) return
    sending.value = true
    clearThinking()
    clearToolCards()
    assistantDeltaBuffer.clear()
    streamingAssistantId = null
    streamingMessage = null
    beginTranscriptTurn()

    try {
      const result = await api.sendMessage(messageText, sessionId.value, imagePayload, filePayload)
      if (disposed) return

      if (result.session_id) {
        const newId = normalizeSessionId(result.session_id)
        if (newId !== sessionId.value) {
          await switchSession(newId)
          if (disposed) return
        }
      }

      if (result.error) {
        addErrorMessage({
          title: 'Request failed',
          detail: String(result.error || ''),
          retriable: true,
          kind: 'request',
        })
      } else {
        await addAssistantHttpResponse(result)
        if (disposed) return
        if (result.token_usage) {
          tokenInfo.value = result.token_usage
        }
      }
    } catch (e) {
      // Network drop / aborted request / server unreachable — offer retry.
      addErrorMessage({
        title: 'Connection error',
        detail: e?.message ? String(e.message) : 'Could not reach the server.',
        retriable: true,
        kind: 'connection',
      })
    } finally {
      sending.value = false
      clearThinking()
      clearToolCards()
    }
  }

  async function sendMessage(text, images = [], files = []) {
    if (disposed) return
    const messageText = String(text || '')
    const imagePayload = normalizeImagePayload(images)
    const filePayload = normalizeFilePayload(files)
    if ((!messageText.trim() && !imagePayload.length && !filePayload.length) || sending.value) return

    const payload = { messageText, imagePayload, filePayload }
    retryState.beginFreshSend(payload)
    await performSend(payload)
  }

  async function retryLastMessage() {
    if (disposed) return
    const payload = retryState.beginRetry()
    if (!payload) return
    await performSend(payload)
  }

  function canRetry() {
    return !disposed && retryState.canRetry()
  }

  function dispose() {
    if (disposed) return
    disposed = true
    assistantDeltaBuffer.cancel()
    messages.value = []
    sending.value = false
    tokenInfo.value = null
    todoSnapshot.value = emptyTodoSnapshot()
    researchStatus.value = emptyResearchStatus()
    thinkingText.value = ''
    thinkingStart.value = 0
    thinkingBlock.value = null
    toolCards.value = {}
    errorIds = new Set()
    retryState.reset()
    streamingAssistantId = null
    streamingMessage = null
    beginTranscriptTurn()
    if (instance === chatInstance) instance = null
  }

  const chatInstance = {
    messages,
    sending,
    tokenInfo,
    todoSnapshot,
    researchStatus,
    thinkingBlock,
    toolCards,
    handleWsEvent,
    beginTranscriptTurn,
    clearThinking,
    clearToolCards,
    addMessage,
    addErrorMessage,
    dismissErrorMessages,
    addAssistantHttpResponse,
    addToolMessage,
    updateToolMessage,
    resetMessages,
    loadTranscript,
    sendMessage,
    retryLastMessage,
    canRetry,
    cancelMessage,
    flushAssistantDeltas: assistantDeltaBuffer.flush,
    dispose,
  }
  instance = chatInstance
  return instance
}

export function clearChatInstance() {
  instance?.dispose()
  instance = null
}

if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    clearChatInstance()
  })
}
