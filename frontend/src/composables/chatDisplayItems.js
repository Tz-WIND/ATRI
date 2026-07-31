import { shallowRef, triggerRef, watchEffect } from 'vue'

const CONTEXT_TOOL_NAMES = new Set(['read_file', 'list_dir', 'tree', 'glob', 'grep', 'search'])

function isContextTool(message) {
  return message?.role === 'tool' && CONTEXT_TOOL_NAMES.has(message.toolData?.tool)
}

function createBuildState() {
  return {
    items: [],
    itemRecords: [],
    messageItemIndexes: [],
    messageRefs: [],
  }
}

function pushMessageItem(state, message, index) {
  const item = {
    id: message.id,
    type: message.role,
    message,
  }
  const itemIndex = state.items.length
  state.items.push(item)
  state.itemRecords.push({ start: index, end: index })
  state.messageItemIndexes[index] = itemIndex
}

function pushContextToolGroup(state, contextTools, start, end) {
  if (!contextTools.length) return
  const first = contextTools[0]
  // Key only on the run start id. Including last.id remounts the card on
  // every appended context tool and makes the list visually jump.
  const item = {
    id: `context-${first.id}`,
    type: 'tool-group',
    tools: contextTools.map((message) => ({
      id: message.id,
      ...(message.toolData || {}),
    })),
  }
  const itemIndex = state.items.length
  state.items.push(item)
  state.itemRecords.push({ start, end })
  for (let index = start; index <= end; index += 1) {
    state.messageItemIndexes[index] = itemIndex
  }
}

function rebuildFrom(messages, start, state) {
  const prefixItemCount = start > 0 ? state.messageItemIndexes[start - 1] + 1 : 0
  state.items.length = prefixItemCount
  state.itemRecords.length = prefixItemCount
  state.messageItemIndexes.length = start

  let contextTools = []
  let contextStart = -1

  function flushContextTools(end) {
    if (!contextTools.length) return
    pushContextToolGroup(state, contextTools, contextStart, end)
    contextTools = []
    contextStart = -1
  }

  for (let index = start; index < messages.length; index += 1) {
    const message = messages[index]
    state.messageRefs[index] = message

    if (isContextTool(message)) {
      if (!contextTools.length) contextStart = index
      contextTools.push(message)
      continue
    }

    flushContextTools(index - 1)
    pushMessageItem(state, message, index)
  }

  flushContextTools(messages.length - 1)
  state.messageRefs.length = messages.length
  state.messageItemIndexes.length = messages.length
}

function findFirstChangedIndex(messages, state) {
  const previousMessages = state.messageRefs
  const previousLength = previousMessages.length
  const nextLength = messages.length

  if (!previousLength) return nextLength ? 0 : -1
  if (!nextLength) return 0

  if (nextLength === previousLength) {
    const lastIndex = nextLength - 1
    const nextLast = messages[lastIndex]
    const previousLast = previousMessages[lastIndex]
    if (nextLast !== previousLast) {
      const previousNeighborStable = lastIndex === 0 || messages[lastIndex - 1] === previousMessages[lastIndex - 1]
      const sameDisplaySlot = nextLast?.id === previousLast?.id && nextLast?.role === previousLast?.role
      if (previousNeighborStable && sameDisplaySlot) return lastIndex
    }
  }

  if (nextLength === previousLength + 1) {
    const appendedAtTail = previousLength === 0 || messages[previousLength - 1] === previousMessages[previousLength - 1]
    if (appendedAtTail) return previousLength
  }

  if (nextLength < previousLength) {
    const removedFromTail = nextLength === 0 || messages[nextLength - 1] === previousMessages[nextLength - 1]
    if (removedFromTail) return nextLength
  }

  const sharedLength = Math.min(nextLength, previousLength)
  for (let index = 0; index < sharedLength; index += 1) {
    if (messages[index] !== previousMessages[index]) return index
  }
  return nextLength === previousLength ? -1 : sharedLength
}

function recordStartForMessage(state, index) {
  const itemIndex = state.messageItemIndexes[index]
  return state.itemRecords[itemIndex]?.start ?? index
}

function contextRunStart(messages, index) {
  let start = index
  while (start > 0 && isContextTool(messages[start - 1])) {
    start -= 1
  }
  return start
}

function adjustedRebuildStart(messages, changedStart, state) {
  if (changedStart <= 0) return 0

  let start = changedStart
  if (changedStart < state.messageRefs.length) {
    start = Math.min(start, recordStartForMessage(state, changedStart))
  }

  if (changedStart < messages.length && isContextTool(messages[changedStart])) {
    start = Math.min(start, contextRunStart(messages, changedStart))
  }

  return start
}

export function buildChatDisplayItems(messages = []) {
  const state = createBuildState()
  rebuildFrom(messages, 0, state)
  return state.items
}

export function createChatDisplayItemsBuilder() {
  const state = createBuildState()

  return {
    build(messages = []) {
      const changedStart = findFirstChangedIndex(messages, state)
      if (changedStart < 0) return state.items

      rebuildFrom(messages, adjustedRebuildStart(messages, changedStart, state), state)
      return state.items
    },
  }
}

export function useChatDisplayItems(messages) {
  const builder = createChatDisplayItemsBuilder()
  const displayItems = shallowRef([])

  watchEffect(() => {
    const nextItems = builder.build(messages.value)
    if (displayItems.value === nextItems) {
      triggerRef(displayItems)
    } else {
      displayItems.value = nextItems
    }
  })

  return displayItems
}
