# Studio Piano Lane Agent Tools Design

## Goal

Allow the AI agent to edit the Music Studio piano roll meter and harmony lanes
directly through dedicated tools. The tools write the existing project-level
`meter_events` and `harmony_events` arrays, save the project, and request the
same dashboard/host sync path used by other Music Studio write tools.

Also keep time signature edits out of automation tools. Automation may still
support tempo BPM, track volume, track pan, and plugin parameters, but old
`time_signature_numerator` requests must be rejected with guidance to use the
new piano lane tools.

## Public Tools

Add two agent tools:

- `studio_piano_lane_write`
- `studio_piano_lane_diff`

Both tools use capability `music.studio.piano_lane` and are high-privilege
write tools because they mutate persistent Music Studio project state.

`studio_piano_lane_write` replaces or appends events for one lane:

- `lane`: `meter` or `harmony`
- `mode`: `replace` or `append`
- optional `start` / `end` beat range for replace mode
- `events`: meter events use `beat`, `numerator`, and `denominator`; harmony
  events use `beat` and `text`

`studio_piano_lane_diff` applies small atomic edits:

- `lane`: `meter` or `harmony`
- `operations`: `add_event`, `update_event`, `delete_event`, or `replace_range`
- event identity is the normalized beat; update/delete can target by `beat`

## Data Flow

The tools call new core project helpers instead of sending raw full-project
PUT requests. The helpers load the project, normalize events using the existing
project normalization rules, save it, and return an operation summary plus
`project_summary`.

After each write, the tool requests dashboard sync through the existing
`_request_dashboard_sync()` helper. If the dashboard is unavailable, the write
still persists and returns the existing sync-unavailable message.

## Validation

Meter events accept non-negative beats, numerator `1..255`, and denominator
limited to `2, 4, 8, 16, 32`. Duplicate beats collapse to the latest event.

Harmony events accept non-negative beats and non-empty text. Empty text is
ignored during normalization. Duplicate beats collapse to the latest event.

Unknown lanes or operations return clear errors from the tool layer.

## Legacy Automation Cleanup

Agent automation schemas already omit `time_signature_numerator`. Keep that.
Additionally, reject `time_signature_numerator` in the shared project
automation write path and the dashboard `/studio/automation` route, with an
error message pointing to `studio_piano_lane_write` or `studio_piano_lane_diff`.

Legacy project files containing old time signature automation tracks should not
be synced to the host as automation. Existing normalization may keep them
unassigned for compatibility.

## Tests

Add tests that prove:

- `studio_piano_lane_write` writes normalized meter events and requests sync.
- `studio_piano_lane_write` writes normalized harmony events and updates
  `piano_subtrack_order` as needed.
- `studio_piano_lane_diff` can add, update, delete, and replace range events.
- the new tools are registered with capability metadata.
- high-privilege filtering blocks the new mutating tools when file/state writes
  are not allowed.
- automation write paths reject `time_signature_numerator` and no schema
  exposes it as a writable automation target.
