# Bridge MIDI Multi-Track Preview Design

## Goal

The VST bridge MIDI preview should represent multi-track MIDI exports without increasing the plugin editor preview size. Users should be able to scroll inside the existing preview area with the mouse wheel to inspect tracks from the exported MIDI region. Dragging the preview still drags the single latest exported MIDI file.

## Current Behavior

- Frontend DAW agent artifact export usually sends a single `track_ids` entry.
- The dashboard MIDI bridge export path already accepts a list of track ids.
- `bridge_preview` currently describes only one track, because `_bridge_preview_for_midi_export()` selects `tracks[0]`.
- The Rust bridge contract stores one `BridgeMidiPreview`.
- The native editor preview has a fixed rectangle and does not handle mouse wheel events.

## Data Contract

Keep the existing `bridge_preview` top-level fields for compatibility:

```json
{
  "kind": "midi_region",
  "track_id": 1,
  "track_name": "Piano",
  "beat_range": [4.0, 8.0],
  "note_count": 12,
  "pitch_range": [48, 72]
}
```

Add an optional `tracks` array to the same object:

```json
{
  "kind": "midi_region",
  "track_id": 1,
  "track_name": "Piano",
  "beat_range": [4.0, 8.0],
  "note_count": 20,
  "pitch_range": [36, 84],
  "tracks": [
    {
      "track_id": 1,
      "track_name": "Piano",
      "note_count": 12,
      "pitch_range": [48, 72]
    },
    {
      "track_id": 2,
      "track_name": "Bass",
      "note_count": 8,
      "pitch_range": [36, 48]
    }
  ]
}
```

The top-level `note_count` and `pitch_range` are aggregate values across all preview tracks. `track_id` and `track_name` remain the first preview track for older clients.

## Dashboard Behavior

`_bridge_preview_for_midi_export()` should build one track summary per selected non-automation track. Each summary includes track id, name, visible note count, and visible pitch range for the export beat range.

If multiple tracks are selected, the returned preview includes all track summaries in `tracks`. If there is one track, `tracks` still contains one item so the Rust side can use one path consistently.

## Rust Contract And State

`BridgeMidiPreview` gains a `tracks: Vec<BridgeMidiPreviewTrack>` field with serde defaulting to an empty vec for old payloads. When deserializing old single-track previews, editor state/view model should treat the top-level fields as a single display track.

The editor state stores the preview and a scroll offset. Applying a new preview resets the scroll offset to 0. Clearing export errors still clears the preview and should also reset the scroll offset.

## Native Editor UI

The preview rectangle keeps its current size. The view model exposes visible track rows derived from the preview tracks and scroll offset.

The preview renders up to two compact track rows at a time:

- Track name on the left
- Beat range, note count, and pitch range in the detail text
- A subtle scroll indicator when there are tracks above or below the current view

Mouse wheel events inside the preview rectangle scroll the preview by one row per wheel notch. Wheel events outside the preview are ignored by the bridge editor.

Dragging remains unchanged: any drag from the preview starts a drag for the latest completed MIDI export path.

## Error Handling

Invalid or missing `tracks` falls back to the legacy top-level preview fields. Empty previews are not rendered.

When an export error occurs, `last_midi_preview` is cleared and the scroll offset is reset, so no stale multi-track rows remain draggable.

## Tests

Add focused tests for:

- Dashboard bridge preview metadata includes multiple track summaries and aggregate top-level values.
- Rust contract parses `tracks` while preserving legacy single-track payload compatibility.
- Editor view model exposes only the visible track rows for a fixed-size preview.
- Wheel scrolling changes visible rows and clamps at bounds.
- Native surface emits a scroll event only for mouse wheel inside the preview rectangle.
- Existing drag behavior still uses the latest export path.
