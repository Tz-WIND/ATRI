# Bus Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit bus routes and output routing while preserving current track-to-master playback by default.

**Architecture:** Existing engine `Route` remains the processing unit and becomes either a `track` or `bus` route. The master stays backed by `Session::master_buf`; every non-master route has an optional output route id that defaults to master. Project normalization validates bus ids and cycles before dashboard sync sends resolved route config to the host.

**Tech Stack:** Python project state and dashboard sync, Vue music studio UI, Rust host IPC, Rust realtime engine session routing, pytest, cargo test.

---

## File Map

- Modify `core/music_project.py`: add `bus` track type, `output_bus_id` normalization, deletion repair, and bus plugin slot preservation.
- Modify `tests/test_music_project.py`: cover bus track creation, output routing persistence, invalid output fallback, cycle repair, and delete repair.
- Modify `dashboard/music.py`: sync route kind and output destination to host; include routing diagnostics in sync result.
- Modify `tests/test_music_studio_sync.py`: cover host route config payloads and invalid/missing output routing diagnostics.
- Modify `atri-host/atri-engine/src/route.rs`: add `RouteKind` and `output_track_id` state.
- Modify `atri-host/atri-engine/src/session.rs`: add route config setters, route graph resolution, bus render order, and bus-aware solo pass behavior.
- Modify `atri-host/atri-host-bin/src/commands.rs`: extend IPC with `set_route_config`, `kind`, and `output_track_id`; expose kind/output in status.
- Modify `frontend/src/components/music/MusicStudio.vue`: add Bus track creation and output bus selector.
- Modify `tests/test_music_studio_component.py`: assert the Bus option and output selector wiring.

## Task 1: Project Bus Track Model

**Files:**
- Modify: `core/music_project.py`
- Test: `tests/test_music_project.py`

- [ ] **Step 1: Write failing project model tests**

Add these tests after `test_create_track_supports_instrument_and_audio_track_types` in `tests/test_music_project.py`:

```python
def test_create_track_supports_bus_tracks_and_output_routing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_project()

    _, bus_track = create_track("Drum Bus", track_type="bus")
    project, routed = create_track("Kick", track_type="audio")
    project, routed = update_track(routed["id"], {"output_bus_id": bus_track["id"]})

    assert bus_track["type"] == "bus"
    assert bus_track["instrument"] == "Bus"
    assert bus_track["plugin_slots"] == []
    assert routed["output_bus_id"] == bus_track["id"]
    assert project["tracks"][-1]["output_bus_id"] == bus_track["id"]


def test_project_repairs_invalid_and_cyclic_output_buses(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    project = save_project(
        {
            "tracks": [
                {"id": 1, "name": "Lead", "type": "instrument", "output_bus_id": 99},
                {"id": 2, "name": "Bus A", "type": "bus", "output_bus_id": 3},
                {"id": 3, "name": "Bus B", "type": "bus", "output_bus_id": 2},
            ]
        }
    )

    tracks = {track["id"]: track for track in project["tracks"]}
    assert tracks[1]["output_bus_id"] is None
    assert tracks[2]["output_bus_id"] is None
    assert tracks[3]["output_bus_id"] is None


def test_delete_bus_track_retargets_dependents_to_master(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_project()
    _, bus_track = create_track("FX Bus", track_type="bus")
    _, lead = create_track("Lead")
    update_track(lead["id"], {"output_bus_id": bus_track["id"]})

    project, deleted = delete_track(bus_track["id"])

    lead_after = next(track for track in project["tracks"] if track["id"] == lead["id"])
    assert deleted["type"] == "bus"
    assert lead_after["output_bus_id"] is None
```

- [ ] **Step 2: Run project model tests and verify failure**

Run:

```powershell
uv run pytest --tb=short -q tests\test_music_project.py::test_create_track_supports_bus_tracks_and_output_routing tests\test_music_project.py::test_project_repairs_invalid_and_cyclic_output_buses tests\test_music_project.py::test_delete_bus_track_retargets_dependents_to_master
```

Expected: failures mention unsupported `bus` type or missing `output_bus_id`.

- [ ] **Step 3: Add bus type normalization helpers**

In `core/music_project.py`, change `_normalize_track_type` to accept `bus`:

```python
def _normalize_track_type(track: dict[str, Any], *, clips: list[dict[str, Any]]) -> str:
    raw_type = str(track.get("type", track.get("track_type", "")) or "").strip().lower()
    if raw_type in {"instrument", "audio", "automation", "bus"}:
        return raw_type
    if str(track.get("instrument") or "").strip().lower() == "audio track":
        return "audio"
    if clips and all(clip.get("type") == "audio" for clip in clips):
        return "audio"
    return "instrument"
```

Change `_normalize_track_channel_type` so only audio can be mono:

```python
def _normalize_track_channel_type(value: Any, *, track_type: str) -> str:
    if track_type != "audio":
        return "multichannel"
    parsed = str(value or "").strip().lower().replace("-", "_")
    if parsed in {"mono", "monophonic"}:
        return "mono"
    if parsed in {"multi", "multichannel", "multi_channel", "stereo"}:
        return "multichannel"
    return "multichannel"
```

- [ ] **Step 4: Preserve bus plugin slots without adding an instrument slot**

Replace `_normalize_plugin_slots` in `core/music_project.py` with:

```python
def _normalize_plugin_slots(
    track: dict[str, Any],
    *,
    track_type: str = "instrument",
) -> list[dict[str, Any]]:
    if track_type not in {"instrument", "bus"}:
        return []

    raw_slots = track.get("plugin_slots")
    slots: list[dict[str, Any]] = []
    if isinstance(raw_slots, list) and raw_slots:
        slot_map: dict[str, dict[str, Any]] = {}
        slot_order: list[str] = []
        for raw_slot in raw_slots:
            if not isinstance(raw_slot, dict):
                continue
            slot = _normalize_plugin_slot(raw_slot)
            if track_type == "bus" and slot["id"] == "instrument":
                continue
            if slot["id"] not in slot_map:
                slot_order.append(slot["id"])
            slot_map[slot["id"]] = slot
        slots = [slot_map[slot_id] for slot_id in slot_order]

    if track_type == "instrument" and not any(slot.get("id") == "instrument" for slot in slots):
        slots.insert(
            0,
            _normalize_plugin_slot(
                {
                    "type": "builtin",
                    "name": track.get("instrument") or "ATRI Basic Synth",
                },
                slot_id="instrument",
            ),
        )
    return _sort_plugin_slots(slots)
```

- [ ] **Step 5: Add `output_bus_id` repair after track normalization**

In the `normalized_track` dict inside `normalize_project`, add this field:

```python
"output_bus_id": _nullable_non_negative_int(raw_track.get("output_bus_id")),
```

After `if not normalized["tracks"]:` and before calculating `max_clip_end`, call:

```python
    _repair_output_bus_routing(normalized["tracks"])
```

Add this helper near the other normalization helpers:

```python
def _repair_output_bus_routing(tracks: list[dict[str, Any]]) -> None:
    bus_ids = {int(track["id"]) for track in tracks if track.get("type") == "bus"}

    for track in tracks:
        output_bus_id = track.get("output_bus_id")
        if output_bus_id is None:
            track["output_bus_id"] = None
            continue
        if int(output_bus_id) not in bus_ids or int(output_bus_id) == int(track["id"]):
            track["output_bus_id"] = None

    outputs = {
        int(track["id"]): track.get("output_bus_id")
        for track in tracks
        if track.get("output_bus_id") is not None
    }

    def has_cycle(start_id: int) -> bool:
        seen: set[int] = set()
        current_id = start_id
        while current_id in outputs:
            if current_id in seen:
                return True
            seen.add(current_id)
            current_id = int(outputs[current_id])
        return False

    for track in tracks:
        if has_cycle(int(track["id"])):
            track["output_bus_id"] = None
```

- [ ] **Step 6: Update create/update/delete track behavior**

In `create_track`, set `instrument` and `plugin_slots` like this:

```python
"instrument": "Bus"
if normalized_type == "bus"
else "Audio Track"
if normalized_type == "audio"
else "ATRI Basic Synth",
"plugin_slots": _normalize_plugin_slots(
    {"plugin_slots": [] if normalized_type == "bus" else None, "instrument": "ATRI Basic Synth"},
    track_type=normalized_type,
),
"output_bus_id": None,
```

In `update_track`, after pan/mute/solo handling, add:

```python
    if "output_bus_id" in updates:
        track["output_bus_id"] = _nullable_non_negative_int(updates.get("output_bus_id"))
```

When `track["type"] == "audio"` in the type update branch, keep `plugin_slots = []`; when `track["type"] == "bus"`, set `instrument = "Bus"` and `plugin_slots = _normalize_plugin_slots(track, track_type="bus")`.

In `delete_track`, after removing the track from `project["tracks"]`, add:

```python
    for item in project["tracks"]:
        if item.get("output_bus_id") == deleted_id:
            item["output_bus_id"] = None
```

- [ ] **Step 7: Run and commit Task 1**

Run:

```powershell
uv run pytest --tb=short -q tests\test_music_project.py::test_create_track_supports_instrument_and_audio_track_types tests\test_music_project.py::test_create_track_supports_bus_tracks_and_output_routing tests\test_music_project.py::test_project_repairs_invalid_and_cyclic_output_buses tests\test_music_project.py::test_delete_bus_track_retargets_dependents_to_master
```

Expected: all selected tests pass.

Commit:

```powershell
git add core\music_project.py tests\test_music_project.py
git commit -m "Add project bus routing model"
```

## Task 2: Dashboard Host Sync Route Config

**Files:**
- Modify: `dashboard/music.py`
- Test: `tests/test_music_studio_sync.py`

- [ ] **Step 1: Write failing sync tests**

Add this test after `test_sync_project_to_host_skips_automation_routes_and_sends_lanes`:

```python
async def test_sync_project_to_host_sends_route_kind_and_output_bus(monkeypatch):
    host = FakeStudioHost()
    monkeypatch.setattr(music, "_host_manager", lambda: host)

    project = {
        "title": "Bus Sync",
        "tempo": 120,
        "time_signature": [4, 4],
        "length_beats": 16,
        "tracks": [
            {
                "id": 1,
                "host_track_id": 1,
                "type": "instrument",
                "name": "Kick",
                "output_bus_id": 2,
                "volume": 0.8,
                "pan": 0,
                "mute": False,
                "solo": False,
                "notes": [],
                "midi_events": [],
                "clips": [],
                "plugin_slots": [{"id": "instrument", "type": "builtin", "name": "ATRI Basic Synth"}],
            },
            {
                "id": 2,
                "host_track_id": 2,
                "type": "bus",
                "name": "Drum Bus",
                "output_bus_id": None,
                "volume": 0.9,
                "pan": 0,
                "mute": False,
                "solo": False,
                "notes": [],
                "midi_events": [],
                "clips": [],
                "plugin_slots": [],
            },
        ],
    }

    sync = await music._sync_project_to_host(project)

    configs = [params for cmd, params in host.commands if cmd == "set_route_config"]
    assert configs == [
        {"track_id": 1, "kind": "track", "output_track_id": 2},
        {"track_id": 2, "kind": "bus", "output_track_id": None},
    ]
    assert sync["routing"] == {"routes": 2, "skipped": []}
```

- [ ] **Step 2: Run sync test and verify failure**

Run:

```powershell
uv run pytest --tb=short -q tests\test_music_studio_sync.py::test_sync_project_to_host_sends_route_kind_and_output_bus
```

Expected: failure because no `set_route_config` commands are sent.

- [ ] **Step 3: Add host route mapping helpers**

In `dashboard/music.py`, add these helpers near `_host_track_id_for_project_target`:

```python
def _host_track_id_for_project_track(project: dict[str, Any], project_track_id: object) -> int | None:
    try:
        wanted = int(project_track_id)
    except (TypeError, ValueError):
        return None
    for track in project.get("tracks", []):
        if not isinstance(track, dict) or int(track.get("id", -1)) != wanted:
            continue
        host_track_id = track.get("host_track_id")
        if host_track_id is None:
            return None
        return int(host_track_id)
    return None


def _route_kind_for_host(track: dict[str, Any]) -> str:
    return "bus" if str(track.get("type") or "").strip().lower() == "bus" else "track"


def _route_output_for_host(
    project: dict[str, Any],
    track: dict[str, Any],
) -> tuple[int | None, dict[str, Any] | None]:
    output_bus_id = track.get("output_bus_id")
    if output_bus_id is None:
        return None, None
    host_output_id = _host_track_id_for_project_track(project, output_bus_id)
    if host_output_id is not None:
        return host_output_id, None
    return None, {
        "track_id": track.get("id"),
        "output_bus_id": output_bus_id,
        "reason": "output bus is not synced",
    }
```

- [ ] **Step 4: Send route config during host sync**

In `_sync_project_to_host`, before the track loop, initialize:

```python
    routing_skipped: list[dict[str, Any]] = []
    routing_routes = 0
```

Inside the non-automation track loop, after `host_track_id = int(host_track_id)`, add:

```python
        output_track_id, routing_skip = _route_output_for_host(project, track)
        if routing_skip is not None:
            routing_skipped.append(routing_skip)
        commands.append(
            await host.send_command(
                "set_route_config",
                {
                    "track_id": host_track_id,
                    "kind": _route_kind_for_host(track),
                    "output_track_id": output_track_id,
                },
            )
        )
        routing_routes += 1
```

In the return value, add:

```python
        "routing": {
            "routes": routing_routes,
            "skipped": routing_skipped,
        },
```

- [ ] **Step 5: Run and commit Task 2**

Run:

```powershell
uv run pytest --tb=short -q tests\test_music_studio_sync.py::test_sync_project_to_host_sends_route_kind_and_output_bus tests\test_music_studio_sync.py::test_sync_project_to_host_skips_automation_routes_and_sends_lanes
```

Expected: selected sync tests pass.

Commit:

```powershell
git add dashboard\music.py tests\test_music_studio_sync.py
git commit -m "Sync route bus configuration to host"
```

## Task 3: Rust Route Kind And IPC Config

**Files:**
- Modify: `atri-host/atri-engine/src/route.rs`
- Modify: `atri-host/atri-engine/src/session.rs`
- Modify: `atri-host/atri-host-bin/src/commands.rs`
- Test: `atri-host/atri-engine/src/session.rs`
- Test: `atri-host/atri-host-bin/src/commands.rs`

- [ ] **Step 1: Write failing Rust tests for route config**

In `atri-host/atri-engine/src/session.rs`, add this test in the test module:

```rust
#[test]
fn route_config_sets_kind_and_output_target() {
    let mut session = Session::new(48_000, 64);
    let track = session.add_track("Lead".to_string());
    let bus = session.add_bus("Lead Bus".to_string());

    assert!(session.set_route_output(track, Some(bus)));
    assert_eq!(session.route_output(track), Some(Some(bus)));
    assert_eq!(session.route_kind(track), Some(RouteKind::Track));
    assert_eq!(session.route_kind(bus), Some(RouteKind::Bus));
}
```

In `atri-host/atri-host-bin/src/commands.rs`, add this test in the test module:

```rust
#[test]
fn set_route_config_updates_kind_and_output() {
    let (engine, cmd_tx, _cmd_rx, streamer, config) = command_context();
    let track = with_session(&engine, |session| session.add_track("Lead".to_string()));
    let bus = with_session(&engine, |session| session.add_bus("Bus".to_string()));

    let response = execute(
        Command::SetRouteConfig {
            track_id: track,
            kind: Some(RouteKindData::Track),
            output_track_id: Some(bus),
        },
        &engine,
        &cmd_tx,
        &streamer,
        &config,
        None,
    );

    assert!(matches!(response, CommandResponse::Ack { cmd, .. } if cmd == "set_route_config"));
    assert_eq!(
        with_session(&engine, |session| session.route_output(track)),
        Some(Some(bus))
    );
}
```

- [ ] **Step 2: Run Rust route tests and verify failure**

Run:

```powershell
cargo test -p atri-engine route_config_sets_kind_and_output_target
cargo test -p atri-host-bin set_route_config_updates_kind_and_output
```

Expected: compile failure for missing `RouteKind`, `add_bus`, `set_route_output`, or `SetRouteConfig`.

- [ ] **Step 3: Add `RouteKind` and route output state**

In `atri-host/atri-engine/src/route.rs`, add:

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RouteKind {
    Track,
    Bus,
}
```

Update `Route` fields:

```rust
pub kind: RouteKind,
pub output_track_id: Option<u32>,
```

Replace `Route::new` with:

```rust
pub fn new(id: u32, name: String) -> Self {
    Self::new_with_kind(id, name, RouteKind::Track)
}

pub fn new_with_kind(id: u32, name: String, kind: RouteKind) -> Self {
    Self {
        id,
        name,
        kind,
        output_track_id: None,
        processors: Vec::new(),
        gain: Gain::new(1.0),
        pan: Pan::new(),
        sequencer: MidiSequencer::new(),
        audio_clips: Vec::new(),
        solo: false,
        mute: false,
    }
}
```

- [ ] **Step 4: Add session route config API**

In `atri-host/atri-engine/src/session.rs`, update imports:

```rust
use super::route::{Route, RouteKind};
```

Add methods near `add_track`:

```rust
pub fn add_bus(&mut self, name: String) -> u32 {
    self.add_route(name, RouteKind::Bus)
}

fn add_route(&mut self, name: String, kind: RouteKind) -> u32 {
    let id = self.next_route_id;
    self.next_route_id = self.next_route_id.saturating_add(1);
    let index = self.routes.len();
    self.routes
        .push(Arc::new(Mutex::new(Route::new_with_kind(id, name, kind))));
    self.route_indices.insert(id, index);
    self.route_bufs.push(BufferSet::new(1, 2, self.buffer_size));
    self.route_delay_lines.push(RouteDelayLine::default());
    self.midi_events.push(Vec::new());
    id
}
```

Change `add_track` to:

```rust
pub fn add_track(&mut self, name: String) -> u32 {
    self.add_route(name, RouteKind::Track)
}
```

Add methods near the track setters:

```rust
pub fn set_route_kind(&mut self, track_id: u32, kind: RouteKind) -> bool {
    self.with_route(track_id, |route| route.kind = kind)
}

pub fn set_route_output(&mut self, track_id: u32, output_track_id: Option<u32>) -> bool {
    if output_track_id == Some(track_id) {
        return false;
    }
    if let Some(output_id) = output_track_id {
        let Some(output_index) = self.route_index(output_id) else {
            return false;
        };
        let Ok(output_route) = self.routes[output_index].lock() else {
            return false;
        };
        if output_route.kind != RouteKind::Bus {
            return false;
        }
    }
    self.with_route(track_id, |route| route.output_track_id = output_track_id)
}

pub fn route_output(&self, track_id: u32) -> Option<Option<u32>> {
    let route = self.route(track_id)?;
    route.lock().ok().map(|route| route.output_track_id)
}

pub fn route_kind(&self, track_id: u32) -> Option<RouteKind> {
    let route = self.route(track_id)?;
    route.lock().ok().map(|route| route.kind)
}
```

- [ ] **Step 5: Add IPC command and status fields**

In `atri-host/atri-host-bin/src/commands.rs`, add this import:

```rust
use atri_engine::route::RouteKind;
```

Then add serializable/deserializable kind:

```rust
#[derive(Debug, Clone, Copy, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum RouteKindData {
    Track,
    Bus,
}

impl From<RouteKindData> for RouteKind {
    fn from(value: RouteKindData) -> Self {
        match value {
            RouteKindData::Track => RouteKind::Track,
            RouteKindData::Bus => RouteKind::Bus,
        }
    }
}
```

Extend `Command`:

```rust
SetRouteConfig {
    track_id: u32,
    kind: Option<RouteKindData>,
    output_track_id: Option<u32>,
},
```

In `handle_command`, add:

```rust
Command::SetRouteConfig {
    track_id,
    kind,
    output_track_id,
} => {
    let ok = with_session(engine, |session| {
        if let Some(kind) = kind {
            if !session.set_route_kind(track_id, kind.into()) {
                return false;
            }
        }
        session.set_route_output(track_id, output_track_id)
    });
    if ok {
        CommandResponse::ack("set_route_config")
    } else {
        CommandResponse::error(Some("set_route_config"), "invalid route config")
    }
}
```

Extend `TrackStatus` with:

```rust
kind: String,
output_track_id: Option<u32>,
```

When building track status, set:

```rust
kind: match route.kind {
    RouteKind::Track => "track".to_string(),
    RouteKind::Bus => "bus".to_string(),
},
output_track_id: route.output_track_id,
```

- [ ] **Step 6: Run and commit Task 3**

Run:

```powershell
cargo test -p atri-engine route_config_sets_kind_and_output_target
cargo test -p atri-host-bin set_route_config_updates_kind_and_output
```

Expected: selected Rust tests pass.

Commit:

```powershell
git add atri-host\atri-engine\src\route.rs atri-host\atri-engine\src\session.rs atri-host\atri-host-bin\src\commands.rs
git commit -m "Add route kind and output config"
```

## Task 4: Engine Bus Graph Rendering

**Files:**
- Modify: `atri-host/atri-engine/src/session.rs`
- Test: `atri-host/atri-engine/src/session.rs`

- [ ] **Step 1: Write failing render tests**

Add these tests to the `session.rs` test module:

```rust
#[test]
fn route_output_bus_sums_to_master() {
    let mut session = Session::new(48_000, 16);
    let track = session.add_track("Tone".to_string());
    let bus = session.add_bus("Bus".to_string());
    assert!(session.set_route_output(track, Some(bus)));
    assert!(session.set_processor_slot(
        track,
        0,
        Some(Arc::new(Mutex::new(PdcImpulseProcessor::new(0, 1.0)))),
    ));

    let mut output = vec![0.0; 16 * 2];
    session.process(&mut output);

    assert!((output[0] - 0.5).abs() < 0.0001);
    assert!((output[1] - 0.5).abs() < 0.0001);
}


#[test]
fn route_render_order_places_nested_buses_after_sources() {
    let mut session = Session::new(48_000, 4);
    let track = session.add_track("Track".to_string());
    let child_bus = session.add_bus("Child".to_string());
    let parent_bus = session.add_bus("Parent".to_string());
    assert!(session.set_route_output(track, Some(child_bus)));
    assert!(session.set_route_output(child_bus, Some(parent_bus)));

    let names: Vec<String> = session
        .route_render_order()
        .into_iter()
        .map(|idx| session.routes[idx].lock().unwrap().name.clone())
        .collect();

    assert_eq!(names, vec!["Track", "Child", "Parent"]);
}
```

- [ ] **Step 2: Run render tests and verify failure**

Run:

```powershell
cargo test -p atri-engine route_render_order_places_nested_buses_after_sources
```

Expected: compile failure for missing `route_render_order`.

- [ ] **Step 3: Add route graph helpers**

In `session.rs`, add:

```rust
fn route_output_index(&self, route_index: usize) -> Option<usize> {
    let route = self.routes.get(route_index)?.lock().ok()?;
    route
        .output_track_id
        .and_then(|track_id| self.route_index(track_id))
}

fn route_depth_to_master(&self, route_index: usize) -> usize {
    let mut depth = 0usize;
    let mut seen = HashSet::new();
    let mut current = Some(route_index);
    while let Some(index) = current {
        if !seen.insert(index) {
            break;
        }
        current = self.route_output_index(index);
        if current.is_some() {
            depth = depth.saturating_add(1);
        }
    }
    depth
}

fn route_render_order(&self) -> Vec<usize> {
    let mut indices = (0..self.routes.len()).collect::<Vec<_>>();
    indices.sort_by(|left, right| {
        self.route_depth_to_master(*right)
            .cmp(&self.route_depth_to_master(*left))
            .then_with(|| left.cmp(right))
    });
    indices
}
```

Add `HashSet` to the `std::collections` import.

- [ ] **Step 4: Split source rendering from route processing**

Add helper methods in `session.rs`:

```rust
fn prepare_route_source(
    &mut self,
    idx: usize,
    start_sample: i64,
    end_sample: i64,
    tempo_map: &TempoMap,
    nframes: usize,
) {
    let Ok(mut route) = self.routes[idx].lock() else {
        return;
    };
    if route.kind == RouteKind::Bus {
        return;
    }
    if self.transport.is_rolling() {
        route.render_audio_clips(
            &mut self.route_bufs[idx],
            start_sample,
            end_sample,
            tempo_map,
            nframes,
        );
        route.sequencer.collect_events_in_samples(
            start_sample,
            end_sample,
            tempo_map,
            &mut self.midi_events[idx],
        );
    } else {
        self.midi_events[idx].clear();
        self.midi_events[idx].push(ScheduledMidiEvent::new(
            MidiEvent::new(0, MidiMessage::AllNotesOff { channel: 0 }),
            0,
        ));
    }
}

fn process_route_buffer(
    &mut self,
    idx: usize,
    start_sample: i64,
    end_sample: i64,
    speed: f64,
    nframes: usize,
    compensation: usize,
) {
    let Ok(mut route) = self.routes[idx].lock() else {
        return;
    };
    route.process(
        &mut self.route_bufs[idx],
        &self.midi_events[idx],
        start_sample,
        end_sample,
        speed,
        nframes,
    );
    if let Some(buf) = self.route_bufs[idx].get_mut(0) {
        if let Some(delay_line) = self.route_delay_lines.get_mut(idx) {
            delay_line.process(buf, nframes, compensation);
        }
    }
}

fn accumulate_route_output(&mut self, idx: usize, nframes: usize) {
    let Some(source) = self.route_bufs[idx].get(0).cloned() else {
        return;
    };
    if let Some(output_idx) = self.route_output_index(idx) {
        if let Some(dest) = self.route_bufs[output_idx].get_mut(0) {
            self.mixer.add(&source, dest, nframes);
        }
    } else {
        self.mixer.add(&source, &mut self.master_buf, nframes);
    }
}
```

- [ ] **Step 5: Rewrite the render loop to use the graph order**

In `process`, replace the existing `for (idx, route_arc) in self.routes.iter().enumerate()` block with:

```rust
for idx in 0..self.route_bufs.len() {
    self.route_bufs[idx].silence(nframes);
    self.midi_events[idx].clear();
}

let render_order = self.route_render_order();
for idx in render_order {
    let muted_or_unsoloed = self.routes[idx]
        .lock()
        .map(|route| route.mute || (any_solo && !route.solo))
        .unwrap_or(true);
    if muted_or_unsoloed {
        if let Some(delay_line) = self.route_delay_lines.get_mut(idx) {
            delay_line.clear();
        }
        continue;
    }

    self.prepare_route_source(idx, start_sample, end_sample, &tempo_map, nframes);
    let compensation =
        max_route_latency.saturating_sub(route_latencies.get(idx).copied().unwrap_or(0));
    self.process_route_buffer(idx, start_sample, end_sample, speed, nframes, compensation);
    self.accumulate_route_output(idx, nframes);
}
```

This first pass keeps the existing solo behavior. Task 4 Step 6 adds bus-aware solo path behavior.

- [ ] **Step 6: Add bus-aware solo path behavior**

Add:

```rust
fn route_feeds_solo_path(&self, route_index: usize, solo_indices: &HashSet<usize>) -> bool {
    if solo_indices.contains(&route_index) {
        return true;
    }
    let mut current = Some(route_index);
    let mut seen = HashSet::new();
    while let Some(index) = current {
        if !seen.insert(index) {
            return false;
        }
        if solo_indices.contains(&index) {
            return true;
        }
        current = self.route_output_index(index);
    }
    false
}

fn solo_route_indices(&self) -> HashSet<usize> {
    self.routes
        .iter()
        .enumerate()
        .filter_map(|(idx, route)| {
            route
                .lock()
                .ok()
                .and_then(|route| if route.solo { Some(idx) } else { None })
        })
        .collect()
}
```

In `process`, replace the old `any_solo` computation with:

```rust
let solo_indices = self.solo_route_indices();
let any_solo = !solo_indices.is_empty();
```

Replace `route.mute || (any_solo && !route.solo)` with:

```rust
route.mute || (any_solo && !self.route_feeds_solo_path(idx, &solo_indices))
```

- [ ] **Step 7: Run and commit Task 4**

Run:

```powershell
cargo test -p atri-engine route_render_order_places_nested_buses_after_sources
cargo test -p atri-engine route_output_bus_sums_to_master
cargo test -p atri-engine automation_lanes_emit_plugin_parameter_changes_at_sample_offsets
```

Expected: selected tests pass.

Commit:

```powershell
git add atri-host\atri-engine\src\session.rs
git commit -m "Render routes through bus graph"
```

## Task 5: Automation And Dashboard Affordances For Bus Routes

**Files:**
- Modify: `core/music_project.py`
- Modify: `frontend/src/components/music/MusicStudio.vue`
- Test: `tests/test_music_project.py`
- Test: `tests/test_music_studio_component.py`

- [ ] **Step 1: Write failing tests**

In `tests/test_music_project.py`, add:

```python
def test_automation_targets_can_address_bus_tracks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    load_project()
    _, bus_track = create_track("Vocal Bus", track_type="bus")

    project, summary = automation_write(
        {"kind": "track_volume", "track_id": bus_track["id"], "label": "Vocal Bus Volume"},
        points=[{"beat": 0, "value": 0.6}, {"beat": 4, "value": 1.0}],
    )

    automation_track = project["tracks"][-1]
    assert summary["target_status"] == "valid"
    assert automation_track["target"]["track_id"] == bus_track["id"]
```

In `tests/test_music_studio_component.py`, add:

```python
def test_music_studio_exposes_bus_track_creation_and_output_selector():
    studio_text = MUSIC_STUDIO.read_text(encoding="utf-8")

    assert '<option value="bus">' in studio_text
    assert "trackCreateType === 'bus'" in studio_text
    assert "output_bus_id" in studio_text
    assert "availableOutputBuses" in studio_text
```

- [ ] **Step 2: Run frontend/project tests and verify failure**

Run:

```powershell
uv run pytest --tb=short -q tests\test_music_project.py::test_automation_targets_can_address_bus_tracks tests\test_music_studio_component.py::test_music_studio_exposes_bus_track_creation_and_output_selector
```

Expected: frontend component test fails until UI strings are added. Project test should pass after Task 1; if it fails, repair `_automation_target_status` so `track_volume` and `track_pan` accept bus tracks.

- [ ] **Step 3: Add Bus to the track creation dialog**

In `MusicStudio.vue`, add the option:

```vue
<option value="bus">
  Bus
</option>
```

Add a bus output selector under the type field:

```vue
<label
  v-if="trackCreateType !== 'automation'"
  class="track-create-field"
>
  <span>Output</span>
  <select v-model="trackCreateOutputBusId">
    <option :value="null">
      Master
    </option>
    <option
      v-for="bus in availableOutputBuses(null)"
      :key="bus.id"
      :value="bus.id"
    >
      {{ bus.name }}
    </option>
  </select>
</label>
```

Add state near `trackCreateType`:

```js
const trackCreateOutputBusId = ref(null)
```

Reset it in `openTrackCreateDialog`:

```js
trackCreateOutputBusId.value = null
```

Add helper:

```js
function isBusTrack(track) {
  return track?.type === 'bus'
}

function availableOutputBuses(trackId = null) {
  return tracks.value.filter(track => isBusTrack(track) && track.id !== trackId)
}
```

- [ ] **Step 4: Include bus type and output id when creating tracks**

In `createSelectedTrack`, replace the type selection with:

```js
const type = trackCreateType.value === 'audio'
  ? 'audio'
  : trackCreateType.value === 'bus'
    ? 'bus'
    : 'instrument'
const channelType = trackCreateChannelType.value === 'mono' ? 'mono' : 'multichannel'
const name = trackCreateName.value.trim() || defaultTrackNameForType(type)
const res = await createTrack(name, {
  type,
  color: trackCreateColor.value,
  channel_type: type === 'audio' ? channelType : 'multichannel',
  output_bus_id: trackCreateOutputBusId.value,
})
```

Update `defaultTrackNameForType` so bus tracks default to `Bus`:

```js
function defaultTrackNameForType(type) {
  if (type === 'audio') return 'Audio Track'
  if (type === 'bus') return 'Bus'
  return 'Instrument'
}
```

- [ ] **Step 5: Add per-track output selector in the mixer or rack strip**

In the existing track strip controls, add:

```vue
<label v-if="!isAutomationTrack(track)">
  <span>Out</span>
  <select
    :value="track.output_bus_id ?? ''"
    @change="updateTrack(track.id, { output_bus_id: $event.target.value ? Number($event.target.value) : null })"
  >
    <option value="">
      Master
    </option>
    <option
      v-for="bus in availableOutputBuses(track.id)"
      :key="`out-${track.id}-${bus.id}`"
      :value="bus.id"
    >
      {{ bus.name }}
    </option>
  </select>
</label>
```

- [ ] **Step 6: Run and commit Task 5**

Run:

```powershell
uv run pytest --tb=short -q tests\test_music_project.py::test_automation_targets_can_address_bus_tracks tests\test_music_studio_component.py::test_music_studio_exposes_bus_track_creation_and_output_selector
npm.cmd run lint
```

Expected: selected Python tests pass and frontend lint passes.

Commit:

```powershell
git add core\music_project.py frontend\src\components\music\MusicStudio.vue tests\test_music_project.py tests\test_music_studio_component.py
git commit -m "Expose bus routing in music studio"
```

## Task 6: Full Verification

**Files:**
- No new files.
- Verify all files changed in Tasks 1-5.

- [ ] **Step 1: Run Python tests**

Run:

```powershell
uv run pytest --tb=short -q tests\test_music_project.py tests\test_music_studio_sync.py tests\test_music_studio_component.py tests\test_tool_metadata.py
```

Expected: all selected Python tests pass.

- [ ] **Step 2: Run Rust engine and host tests**

Run:

```powershell
cargo test -p atri-engine
cargo test -p atri-host-bin
```

Expected: all selected Rust package tests pass.

- [ ] **Step 3: Run frontend lint and build**

Run:

```powershell
npm.cmd run lint
npm.cmd run build
```

Expected: lint and build pass.

- [ ] **Step 4: Final status check**

Run:

```powershell
git status --short
```

Expected: no uncommitted files.

If verification finds a bug, fix the bug in the smallest owning task area, rerun the failing command, then rerun this full verification task.
