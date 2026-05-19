# VST Parameter Control And Automation Tracks Design

## Summary

ATRI will support two separate AI-facing control paths for VST and other automatable
parameters:

1. Realtime parameter tools read and set the currently loaded plugin parameter values.
2. Automation tools create and edit project automation tracks that play back on the
   timeline.

Automation will be represented as first-class project tracks with `type:
"automation"`. These tracks are shown alongside instrument and audio tracks, but
they do not create Rust host audio routes and do not produce audio. Each automation
track targets a parameter on an existing track, such as a VST parameter, track volume,
track pan, or future automatable target.

If an automation target becomes unavailable, usually because the target plugin was
removed after the automation track was created, the automation track remains in the
project as a disabled or missing-target lane. It does not sync active automation to
the host until the target is restored or rebound.

## Goals

- Let AI agents inspect and set live VST parameters through separate tools.
- Let AI agents create, replace, and edit timeline automation through separate tools.
- Let users create automation tracks from the frontend by right-clicking any UI element
  that exposes an automatable parameter.
- Store automation in the same persistent project JSON used by the dashboard and agent
  tools.
- Render automation tracks next to instrument and audio tracks in the arrangement.
- Sync valid automation tracks to the Rust audio host for sample-position playback.
- Preserve missing-target automation tracks instead of deleting user data.

## Non-Goals

- Editing arbitrary opaque VST state chunks directly.
- Guaranteeing automation for plugin internals that are not exposed as VST parameters.
- Building a full plugin parameter browser UI before the first usable automation path.
- Supporting audio-rate modulation. Automation playback is block/sample scheduled, not
  a replacement for synthesizer modulation.

## Project Data Model

The project schema will accept a third track type:

```json
{
  "id": 5,
  "host_track_id": null,
  "type": "automation",
  "name": "Cutoff Automation",
  "color": "#58a7b8",
  "mute": false,
  "solo": false,
  "target": {
    "kind": "plugin_parameter",
    "track_id": 1,
    "slot_id": "instrument",
    "param_index": 74,
    "label": "Cutoff"
  },
  "automation": {
    "value_min": 0.0,
    "value_max": 1.0,
    "default_value": 0.0,
    "points": [
      { "id": "pt_1", "beat": 0.0, "value": 0.2, "curve": "linear" },
      { "id": "pt_2", "beat": 8.0, "value": 0.8, "curve": "linear" }
    ]
  },
  "clips": [],
  "notes": [],
  "midi_events": []
}
```

Automation tracks keep the same common track fields already used by the UI: `id`,
`name`, `color`, `mute`, and `solo`. They do not use `plugin_slots`, audio clips,
MIDI clips, notes, or MIDI events.

Automation targets are explicit:

- `plugin_parameter`: target an instrument or insert plugin parameter.
  - Required fields: `track_id`, `slot_id`, `param_index`.
  - Values are normalized `0.0..1.0`.
- `track_volume`: target a project track's volume.
  - Required fields: `track_id`.
  - Values use the project's existing volume range.
- `track_pan`: target a project track's pan.
  - Required fields: `track_id`.
  - Values use `-1.0..1.0`.

The schema is intentionally open to future `plugin_bypass`, `clip_gain`, send level,
and other automatable target kinds.

## Agent Tools

Realtime VST tools:

- `vst_param_query`
  - Reads live host plugin information for a track and slot.
  - Returns plugin name, slot, parameter count, and available parameter metadata when
    the host can provide it.
- `vst_param_set`
  - Sets one live plugin parameter immediately.
  - Inputs: project or host `track_id`, `slot_id` or `slot_index`, `param_index`,
    normalized `value`.
  - Calls the existing host `set_plugin_parameter` command path.

Automation tools:

- `automation_query`
  - Lists automation tracks, targets, point counts, beat ranges, missing-target state,
    and optionally detailed points.
- `automation_write`
  - Creates or replaces an automation track for a target.
  - Supports explicit points and generated curves.
- `automation_diff`
  - Applies atomic add, update, delete, replace-range, and curve operations to existing
    automation tracks.
- `automation_retarget`
  - Rebinds an existing automation track to a new target after a plugin or parameter
    change.

Realtime tools and automation tools are separate because they have different side
effects. `vst_param_set` changes current sound immediately but does not create timeline
data. `automation_write` changes the project and requests dashboard/host sync.

## Frontend Behavior

The Studio UI will expose "Create Automation Track" on right-click for automatable
controls. Initial targets:

- Instrument plugin parameters, once parameter metadata is available.
- Track volume.
- Track pan.

When selected, the action creates a new automation track directly below or near the
target track. The automation track row is parallel to instrument and audio rows, but
uses a control-lane visual style instead of clip regions.

Arrangement behavior:

- Automation rows show a curve across the timeline.
- Users can click or drag to create and move points.
- Muting an automation track disables automation playback for that lane.
- Missing targets are displayed with a warning state and remain editable enough for
  retargeting or deletion.
- Automation tracks do not allow MIDI or audio clip creation.

The existing MIDI controller-lane editing code provides a useful pattern for drawing
and dragging points, but project automation is stored at track level rather than inside
a MIDI clip.

## Dashboard And Host Sync

Dashboard sync will skip creating Rust host tracks for project tracks with
`type: "automation"`. Instead, it will collect valid automation tracks and send them
to the host with a new automation command, for example:

```json
{
  "cmd": "set_automation",
  "lanes": [
    {
      "target": {
        "kind": "plugin_parameter",
        "track_id": 0,
        "slot_index": 0,
        "param_index": 74
      },
      "points": [
        { "beat": 0.0, "value": 0.2, "curve": "linear" },
        { "beat": 8.0, "value": 0.8, "curve": "linear" }
      ]
    }
  ]
}
```

The dashboard is responsible for translating project `track_id` and `slot_id` to host
`track_id` and `slot_index`. Invalid or missing targets are omitted from active host
sync and reported in the sync result.

The Rust host will store automation lanes in the session. During audio processing, it
will evaluate points for the current block and apply target changes at the appropriate
sample offsets. For VST3 plugin parameters, this reuses the existing queued parameter
change path so changes are delivered through VST3 input parameter changes.

## Parameter Metadata

The current host already supports parameter count, normalized get, and normalized set.
For useful right-click and AI behavior, the host should add a parameter listing command
that exposes:

- `index`
- stable VST `ParamID` when available
- display name
- units when available
- normalized current value
- whether the parameter is automatable when the plugin reports that flag

The first implementation can operate by parameter index if names are unavailable, but
the frontend right-click workflow should prefer named metadata when possible.

## Target Validation States

Automation targets use three validation states:

- `valid`: the referenced project track exists, the referenced slot exists, and the
  target can be translated for host sync.
- `unvalidated`: the project reference exists, but the host is stopped or live plugin
  metadata is unavailable, so the dashboard cannot confirm the parameter index.
- `missing`: the project reference is broken or the host confirms that the target no
  longer exists.

An automation target is missing when:

- The referenced project track no longer exists.
- The referenced plugin slot is empty or changed to a plugin without the requested
  parameter.

Missing-target tracks remain in project JSON. They are visible in the frontend and are
returned by automation query tools. They are not sent as active automation lanes to
the host. Unvalidated tracks are also preserved and can be synced later when the host
starts. This preserves user work after plugin removal and supports later retargeting.

## Error Handling

- Realtime parameter set fails if the host is not running or the target plugin is not
  loaded.
- Automation writes can succeed while the host is stopped because project data is the
  source of truth.
- Automation sync reports skipped lanes instead of deleting or mutating them.
- Values are clamped to the target range.
- Points are sorted by beat and duplicate beat handling is deterministic: later writes
  replace the existing point at the same beat for the same automation track.

## Testing

Python tests:

- Project normalization preserves `automation` tracks.
- Automation creation, write, diff, and retarget operations round-trip through
  `save_project` and `load_project`.
- Sync skips automation tracks when creating host audio routes.
- Sync translates valid automation targets and reports missing targets.
- Agent tool schemas enforce explicit write scope.

Rust tests:

- Host command parsing accepts `set_automation`.
- Session stores automation lanes without creating routes.
- Automation evaluation emits parameter changes for the current processing block.
- Muted automation lanes do not emit changes.
- Missing or invalid target lanes are ignored safely.

Frontend tests:

- Track list renders automation rows as first-class tracks.
- Right-click automatable controls can create automation tracks.
- Arrangement canvas draws automation curves and missing-target state.
- Automation point drag updates project data without creating MIDI or audio clips.

## Rollout Order

1. Add schema support for `automation` tracks and project-level helpers.
2. Add realtime VST parameter agent tools using existing host commands.
3. Add parameter metadata listing in the Rust host and dashboard API.
4. Add automation write/query/diff tools and dashboard routes.
5. Add host automation sync and Rust session playback.
6. Add frontend right-click creation and automation track editing.
