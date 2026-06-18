export function useStudioKeyboardShortcuts(context) {
  const {
    activeMidiClip,
    automationMenu,
    cancelAutomationDrag,
    clearPianoLongPressTimer,
    closePianoHarmonyEditor,
    closePianoMeterEditor,
    closeTrackContextMenu,
    controllerMenuLaneId,
    copySelectedClips,
    copySelectedNotes,
    deleteSelectedClips,
    deleteSelectedNotes,
    draftNote,
    drawAll,
    noteClipboard,
    pasteClips,
    pasteNotes,
    pianoQuantizeMenuOpen,
    pianoVisible,
    selectTrack,
    selectedAutomationPoint,
    selectedClipIds,
    selectedControllerEventId,
    selectedNoteIds,
    selectionBox,
    timelineQuantizeMenuOpen,
    togglePlay,
    tracks,
  } = context

  function isInteractiveTarget(event) {
    const target = event.target
    const tag = String(target?.tagName || '').toLowerCase()
    return ['input', 'textarea', 'select', 'button', 'a'].includes(tag)
      || Boolean(target?.closest?.('input, textarea, select, button, a'))
  }

  function onTrackRowKeydown(event, trackId) {
    if (isInteractiveTarget(event)) return
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      selectTrack(trackId)
    }
  }

  function onStudioKeydown(event) {
    const tag = String(event.target?.tagName || '').toLowerCase()
    if (['input', 'textarea', 'select', 'button'].includes(tag)) return
    if (event.code === 'Space' && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault()
      togglePlay()
    } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'c') {
      event.preventDefault()
      if (pianoVisible.value && activeMidiClip.value && selectedNoteIds.value.size) {
        copySelectedNotes()
      } else {
        copySelectedClips()
      }
    } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'v') {
      event.preventDefault()
      if (pianoVisible.value && activeMidiClip.value && noteClipboard.value.length) {
        pasteNotes()
      } else {
        pasteClips()
      }
    } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
      event.preventDefault()
      if (pianoVisible.value && activeMidiClip.value) {
        selectedNoteIds.value = new Set(activeMidiClip.value.clip.notes.map(note => note.id))
      } else {
        selectedClipIds.value = new Set(
          tracks.value.flatMap(track => (track.clips || []).map(clip => clip.id))
        )
      }
      drawAll()
    } else if (event.key === 'Delete' || event.key === 'Backspace') {
      event.preventDefault()
      if (pianoVisible.value && activeMidiClip.value && selectedNoteIds.value.size) {
        deleteSelectedNotes()
      } else {
        deleteSelectedClips()
      }
    } else if (event.key === 'Escape') {
      selectedNoteIds.value = new Set()
      selectedClipIds.value = new Set()
      selectedAutomationPoint.value = { trackId: null, index: -1 }
      selectedControllerEventId.value = null
      selectionBox.value = null
      draftNote.value = null
      clearPianoLongPressTimer()
      closePianoMeterEditor()
      closePianoHarmonyEditor()
      controllerMenuLaneId.value = null
      timelineQuantizeMenuOpen.value = false
      pianoQuantizeMenuOpen.value = false
      cancelAutomationDrag()
      automationMenu.value = { open: false, x: 0, y: 0, target: null, label: '' }
      closeTrackContextMenu()
      drawAll()
    }
  }

  return {
    onStudioKeydown,
    onTrackRowKeydown,
  }
}
