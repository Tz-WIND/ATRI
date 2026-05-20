# Bus Routing Design

## Context

ATRI currently treats each engine `Route` as a playable track with processors, gain, pan,
mute, solo, MIDI sequencing, and audio clips. The mixer sums every route directly into the
stereo master buffer. This is simple and works for the current automation targets, but it
does not provide an explicit routing graph for group buses or future sends.

The agreed direction is to treat the existing track `Route` as the track's private bus,
then add explicit bus routes and a master bus as routing destinations. Sends are deliberately
out of scope for this step; they should be added after bus routing has a stable data model
and render order.

## Goals

- Preserve current project playback behavior by default.
- Make every track routeable to an output bus, with `master` as the default.
- Add explicit bus routes that can host processors, gain, pan, mute, solo, and automation.
- Keep the model ready for later send automation without implementing sends now.
- Prevent invalid routing loops and missing output targets from reaching the realtime path.

## Non-Goals

- No send levels, send panning, or pre/post-fader sends in this step.
- No sidechain routing.
- No multi-output hardware routing.
- No UI-heavy mixer redesign beyond the minimum needed to represent bus tracks.

## Architecture

The engine should continue to use `Route` as the processing unit, but routes gain a kind:
`track`, `bus`, or `master`. Existing instrument and audio tracks remain `track` routes.
New bus tracks are `bus` routes. The master output remains a stable master node backed by
the existing `master_buf` in the first rollout, not a normal project track. That keeps the
current audio output ownership intact while still giving every route a clear destination.

Each non-master route gets an optional `output_bus_id` that stores the project id of a bus
track. If omitted or null, it resolves to `master`. During rendering, the session processes
source routes first, then accumulates their post-fader audio into their output bus, processes
bus routes in topological order, and finally writes the master bus to the audio output.

Routing validation lives outside the realtime render loop. Sync commands and project
normalization reject or repair missing buses, self-routing, and cycles. The render loop
receives a resolved, acyclic routing plan.

## Project Data Model

Project tracks should add:

- `type`: existing values continue; add or reuse a distinct bus representation such as
  `type: "bus"`.
- `output_bus_id`: optional project track id for a destination bus track. Missing or null
  means master.
- Bus tracks keep the same core mix fields as normal tracks: `volume`, `pan`, `mute`,
  `solo`, `plugin_slots`, and automation-compatible ids.

Automation targets should include bus equivalents by reusing track-like targets where
possible:

- `track_volume` and `track_pan` can apply to bus tracks as well as normal tracks.
- `plugin_parameter` can apply to bus plugin slots.
- Separate target names such as `bus_volume` are only needed if the UI or agent needs a
  stricter distinction.

## Host Sync And IPC

Dashboard sync should send route kind and output destination to the host. Existing projects
without bus metadata sync exactly as today: every route outputs to master.

The host command layer should validate that the destination ids refer to synced routes. If
a target output cannot be resolved, it falls back to master and reports the skipped or
repaired route in the sync summary.

## Rendering Behavior

The render order is:

1. Clear all route buffers.
2. Render and process source track routes.
3. Accumulate each track's post-fader output into its destination bus.
4. Process bus routes after their inputs have been accumulated.
5. Accumulate bus outputs into their destination bus or master.
6. Write master to the audio device.

Solo behavior should remain predictable: if any route is soloed, only soloed routes and the
bus ancestry needed to hear them should pass audio. Muted routes produce no output. Bus mute
silences the whole bus subtree.

## Error Handling

Project normalization should keep old projects valid. Invalid output ids fall back to
master. Cycles are broken by resetting the offending route's output to master. Deleted bus
routes cause dependent routes to fall back to master.

Host sync should include routing diagnostics so the dashboard and agent can explain repairs
instead of silently changing the mix.

## Testing

Python tests should cover project normalization, bus track creation, routing persistence,
and host sync payloads. Rust tests should cover mix summing through one bus, nested buses,
default master fallback, cycle rejection or repair, mute/solo behavior, and automation on
bus gain or pan.

## Rollout

1. Add project-level bus track and output routing fields while preserving existing files.
2. Extend host sync and command payloads for route kind and output destination.
3. Update engine session rendering to resolve and process the bus graph.
4. Extend automation query/write validation to allow existing track-like automation targets
   on bus routes.
5. Add minimal dashboard affordances for creating a bus and selecting track output.
