# VST Bridge Mini Preview Design

## Context

The VST bridge already has the core handoff pieces:

- The plugin opens the DAW agent surface with a stable `instance_id`.
- MIDI artifact cards can export selected-track MIDI with `consumer: "bridge"` and that `instance_id`.
- The dashboard records the latest bridge export globally and per plugin instance.
- The plugin polls `/api/music/studio/bridge/export/latest?instance_id=...`.
- The plugin can start a native Windows file drag for the latest export path.

The remaining friction is that the user must manually export a MIDI artifact, switch back to the plugin, and infer that the latest export line is draggable.

## Goal

Make the bridge feel like a direct DAW handoff:

1. When the DAW agent creates or edits MIDI, the DAW agent surface automatically exports that MIDI region for the current bridge instance.
2. The plugin window notices the new export and shows a compact mini piano preview instead of only a text path.
3. The user drags the mini preview area from the plugin window into the DAW track.

## Non-Goals

- No realtime audio or MIDI streaming.
- No embedded browser or Vue canvas inside the VST plugin view.
- No full MIDI file parser in the plugin for this first version.
- No cross-platform native drag implementation beyond the existing Windows path.

## User Flow

1. User opens ATRI Bridge in the DAW and clicks `Open ATRI`.
2. The DAW agent page opens with `surface=daw-agent` and the bridge `instance_id`.
3. User asks the agent to write or edit MIDI.
4. A MIDI artifact card appears and automatically exports bridge MIDI once the artifact is available.
5. The dashboard stores the export as the latest bridge export for that `instance_id`.
6. The plugin polling loop receives the export and updates the editor state.
7. The plugin window renders a mini piano preview region.
8. User drags that preview region into the DAW.

Manual `MIDI` export remains available as a fallback.

## Architecture

### Frontend

`MidiArtifactCard.vue` becomes responsible for automatic bridge export when all of these are true:

- It is running inside the DAW agent surface.
- The current URL has a non-empty `instance_id`.
- The tool status is successful.
- A valid MIDI artifact preview can be built.
- The same artifact has not already been auto-exported in this component lifetime.

The component will reuse `exportPayloadForMidiArtifact(..., "midi", { instanceId })`. The payload continues to use `consumer: "bridge"`.

The card should show a small status label such as `Bridge ready`, `Sending to bridge`, or `Bridge export failed`. This is not instructional copy; it is state feedback.

Auto bridge export runs only from the `artifact` watcher (after `status === 'success'` and a valid preview). The `status` watcher refreshes the host project only; `projectRevision` updates redraw the canvas. `autoExporting` and `lastAutoExportKey` still suppress duplicate API calls if the artifact recomputes.

### Dashboard

Bridge MIDI exports should include lightweight preview metadata in the returned export object before `_remember_bridge_export()` writes latest export state.

The preview metadata should be derived from the export request and current project data, not from parsing the written MIDI file. The minimum shape is:

```json
{
  "bridge_preview": {
    "kind": "midi_region",
    "track_id": 3,
    "track_name": "Edited Synth",
    "beat_range": [4.0, 8.0],
    "note_count": 12,
    "pitch_range": [48, 72]
  }
}
```

This metadata is optional. Older exports without it still work as draggable files.

### Plugin

`BridgeEditorState` stores optional preview metadata from `BridgeExportResponse.export.bridge_preview`.

`BridgeEditorViewModel` exposes a preview model with:

- label text,
- beat range,
- note count,
- pitch range,
- drag hit rectangle.

The native Windows editor draws a compact piano-roll style band when preview metadata exists. It does not need exact note rectangles in v1. A simple keyboard strip plus region bar is enough to make the drag target obvious.

`EditorSurfaceSpec::drag_export_hit_test()` should treat the mini preview rectangle as the primary drag source. The existing completed-export text drag area can remain as a fallback.

## Error Handling

- If automatic export fails, the MIDI artifact card shows the error and leaves the manual `MIDI` button enabled.
- If the plugin latest-export poll fails, it silently preserves the last good export, as it does today.
- If preview metadata is missing or invalid, the plugin falls back to the current `Last export: <path>` line and drag behavior.
- If native drag fails, the plugin keeps reporting the existing drag error in the export state.

## Tests

Frontend:

- Unit-test that auto-export only builds bridge payloads when an `instance_id` exists.
- Component/source test that `MidiArtifactCard.vue` performs auto bridge export and preserves manual export fallback.

Dashboard:

- Test that a selected-track MIDI bridge export records latest export with `bridge_preview`.
- Test that per-instance latest export preserves the same preview metadata.

Rust plugin:

- Test `BridgeExportResponse` preview metadata parsing.
- Test `BridgeEditorState` stores preview metadata on direct and latest export responses.
- Test `BridgeEditorViewModel` renders preview text and exposes a drag hit target.
- Test missing preview metadata keeps the existing path-only behavior.

## Acceptance Criteria

- After a DAW agent MIDI write/edit succeeds, the bridge instance receives a latest MIDI export without a manual click.
- The plugin window displays a visible mini preview for that export.
- Dragging the mini preview starts the existing native file drag for the exported MIDI file.
- Existing manual export buttons continue to work.
- Existing dashboard loopback-only bridge route protections remain unchanged.
