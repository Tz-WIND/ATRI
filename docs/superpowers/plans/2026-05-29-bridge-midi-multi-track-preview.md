# Bridge MIDI Multi-Track Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show multi-track MIDI bridge preview metadata in the fixed-size VST bridge preview, with mouse-wheel scrolling inside the preview rectangle.

**Architecture:** Keep bridge export as one MIDI file, add per-track preview summaries to `bridge_preview.tracks`, and make Rust normalize old single-track previews into the new multi-track display model. The editor state owns preview scroll offset, the view model exposes two visible rows, and the native surface emits scroll events only when the mouse wheel occurs inside the preview rectangle.

**Tech Stack:** Python/Quart dashboard export tests, Rust VST3 bridge crate, Windows native editor surface via `windows-sys`, serde JSON contract tests, existing PowerShell test commands.

---

### Task 1: Dashboard Multi-Track Preview Metadata

**Files:**
- Modify: `dashboard/music.py`
- Modify: `tests/test_dashboard_bridge_auth.py`

- [ ] **Step 1: Write the failing dashboard test**

Add this test after `test_bridge_latest_export_preserves_preview_for_instance` in `tests/test_dashboard_bridge_auth.py`:

```python
@pytest.mark.asyncio
async def test_bridge_latest_export_preview_includes_multiple_tracks(monkeypatch, tmp_path):
    monkeypatch.setattr(
        music_routes,
        "load_project",
        lambda: {
            "title": "Bridge MIDI",
            "tempo": 120,
            "time_signature": [4, 4],
            "length_beats": 4,
            "tracks": [
                {
                    "id": 1,
                    "name": "Lead",
                    "type": "instrument",
                    "notes": [
                        {"pitch": 60, "start": 0, "duration": 0.5, "velocity": 96},
                        {"pitch": 67, "start": 0.5, "duration": 0.5, "velocity": 96},
                    ],
                    "midi_events": [],
                },
                {
                    "id": 2,
                    "name": "Bass",
                    "type": "instrument",
                    "notes": [
                        {"pitch": 36, "start": 0, "duration": 1, "velocity": 96},
                    ],
                    "midi_events": [],
                },
            ],
        },
    )
    monkeypatch.setattr(music_routes, "_audio_export_dir", lambda: tmp_path / "exports")
    dashboard = _dashboard(monkeypatch, tmp_path)
    client = dashboard.app.test_client()

    export_response = await client.post(
        "/api/music/studio/export",
        json={
            "format": "midi",
            "target": "selected_tracks",
            "track_ids": [1, 2],
            "consumer": "bridge",
            "start_beat": 0,
            "end_beat": 1,
        },
        scope_base={"client": ("127.0.0.1", 54000)},
    )
    latest_response = await client.get(
        "/api/music/studio/bridge/export/latest",
        scope_base={"client": ("127.0.0.1", 54000)},
    )

    assert export_response.status_code == 200
    assert latest_response.status_code == 200
    latest = await latest_response.get_json()
    assert latest["export"]["bridge_preview"] == {
        "kind": "midi_region",
        "track_id": 1,
        "track_name": "Lead",
        "beat_range": [0.0, 1.0],
        "note_count": 3,
        "pitch_range": [36, 67],
        "tracks": [
            {
                "track_id": 1,
                "track_name": "Lead",
                "note_count": 2,
                "pitch_range": [60, 67],
            },
            {
                "track_id": 2,
                "track_name": "Bass",
                "note_count": 1,
                "pitch_range": [36, 36],
            },
        ],
    }
```

- [ ] **Step 2: Run the failing dashboard test**

Run:

```powershell
uv run pytest tests/test_dashboard_bridge_auth.py::test_bridge_latest_export_preview_includes_multiple_tracks -q
```

Expected: FAIL because `bridge_preview` only describes the first track and has no `tracks` array.

- [ ] **Step 3: Implement dashboard preview track summaries**

Replace `_bridge_preview_for_midi_export()` in `dashboard/music.py` with:

```python
def _bridge_preview_for_midi_export(
    project: dict[str, Any],
    track_ids: list[int] | None,
    beat_range: Any,
) -> dict[str, Any] | None:
    selected_ids = set(track_ids or [])
    tracks: list[dict[str, Any]] = []
    for track in project.get("tracks", []):
        if not isinstance(track, dict) or _is_automation_track(track):
            continue
        try:
            track_id = int(track.get("id"))
        except (TypeError, ValueError):
            continue
        if selected_ids and track_id not in selected_ids:
            continue
        tracks.append(track)
    if not tracks:
        return None

    start, end = _preview_beat_range(project, beat_range)
    track_previews: list[dict[str, Any]] = []
    all_pitches: list[int] = []
    note_count = 0
    for track in tracks:
        notes = [
            note
            for note in _preview_track_notes(track)
            if note["start"] < end and note["start"] + note["duration"] > start
        ]
        pitches = [int(note["pitch"]) for note in notes]
        all_pitches.extend(pitches)
        note_count += len(notes)
        track_previews.append(
            {
                "track_id": int(track["id"]),
                "track_name": str(track.get("name") or f"Track {track['id']}"),
                "note_count": len(notes),
                "pitch_range": [min(pitches), max(pitches)] if pitches else [60, 60],
            }
        )

    if not track_previews:
        return None
    first_track = track_previews[0]
    return {
        "kind": "midi_region",
        "track_id": int(first_track["track_id"]),
        "track_name": str(first_track["track_name"]),
        "beat_range": [float(start), float(end)],
        "note_count": note_count,
        "pitch_range": [min(all_pitches), max(all_pitches)] if all_pitches else [60, 60],
        "tracks": track_previews,
    }
```

- [ ] **Step 4: Preserve existing single-track test expectations**

Update the existing `bridge_preview` expected dictionaries in `tests/test_dashboard_bridge_auth.py` to include a single-item `tracks` array. For the first expected preview:

```python
    assert latest["export"]["bridge_preview"] == {
        "kind": "midi_region",
        "track_id": 1,
        "track_name": "Lead",
        "beat_range": [0.0, 1.0],
        "note_count": 1,
        "pitch_range": [60, 60],
        "tracks": [
            {
                "track_id": 1,
                "track_name": "Lead",
                "note_count": 1,
                "pitch_range": [60, 60],
            },
        ],
    }
```

For the instance-scoped expected preview:

```python
    assert latest["export"]["bridge_preview"] == {
        "kind": "midi_region",
        "track_id": 1,
        "track_name": "Lead",
        "beat_range": [0.0, 1.0],
        "note_count": 2,
        "pitch_range": [60, 67],
        "tracks": [
            {
                "track_id": 1,
                "track_name": "Lead",
                "note_count": 2,
                "pitch_range": [60, 67],
            },
        ],
    }
```

- [ ] **Step 5: Run dashboard tests**

Run:

```powershell
uv run pytest tests/test_dashboard_bridge_auth.py -q
```

Expected: PASS with all tests in that file passing.

- [ ] **Step 6: Commit dashboard metadata**

Run:

```powershell
git add dashboard/music.py tests/test_dashboard_bridge_auth.py
git commit -m "Add multi-track bridge MIDI preview metadata"
```

### Task 2: Rust Contract Parses Multi-Track Preview Payloads

**Files:**
- Modify: `atri-host/atri-bridge-vst3/src/bridge_contract.rs`
- Modify: `atri-host/atri-bridge-vst3/src/tests.rs`

- [ ] **Step 1: Write failing Rust contract assertions**

Modify `bridge_export_response_parses_midi_preview_metadata()` in `atri-host/atri-bridge-vst3/src/tests.rs` so the JSON includes `tracks`:

```rust
"tracks": [
    {
        "track_id": 3,
        "track_name": "Edited Synth",
        "note_count": 12,
        "pitch_range": [48, 72]
    },
    {
        "track_id": 4,
        "track_name": "Bass",
        "note_count": 8,
        "pitch_range": [36, 48]
    }
]
```

Then replace the assertion with:

```rust
    let preview = response.midi_preview().unwrap();
    assert_eq!(preview.track_name, "Edited Synth");
    assert_eq!(preview.note_count, 20);
    assert_eq!(preview.pitch_range, [36, 72]);
    assert_eq!(preview.tracks.len(), 2);
    assert_eq!(preview.tracks[0].track_name, "Edited Synth");
    assert_eq!(preview.tracks[1].track_name, "Bass");
```

Add this new legacy compatibility test after it:

```rust
#[test]
fn bridge_export_response_falls_back_to_legacy_single_track_preview() {
    let response: BridgeExportResponse = serde_json::from_str(
        r#"{
            "ok": true,
            "export": {
                "format": "midi",
                "path": "data/music_workstation/exports/region.mid",
                "bridge_preview": {
                    "kind": "midi_region",
                    "track_id": 3,
                    "track_name": "Edited Synth",
                    "beat_range": [4.0, 8.0],
                    "note_count": 12,
                    "pitch_range": [48, 72]
                }
            }
        }"#,
    )
    .unwrap();

    let preview = response.midi_preview().unwrap();

    assert!(preview.tracks.is_empty());
    assert_eq!(preview.display_tracks().len(), 1);
    assert_eq!(preview.display_tracks()[0].track_name, "Edited Synth");
}
```

- [ ] **Step 2: Run contract tests to verify failure**

Run:

```powershell
cargo test -p atri-bridge-vst3 bridge_export_response_parses_midi_preview_metadata bridge_export_response_falls_back_to_legacy_single_track_preview
```

Expected: FAIL because `BridgeMidiPreview` has no `tracks` field and no `display_tracks()` method. If Cargo rejects multiple filters, run the two test names separately.

- [ ] **Step 3: Implement Rust preview track types**

Modify `atri-host/atri-bridge-vst3/src/bridge_contract.rs`:

```rust
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BridgeMidiPreviewTrack {
    pub track_id: i64,
    pub track_name: String,
    pub note_count: u64,
    pub pitch_range: [i32; 2],
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BridgeMidiPreview {
    pub kind: String,
    pub track_id: i64,
    pub track_name: String,
    pub beat_range: [f64; 2],
    pub note_count: u64,
    pub pitch_range: [i32; 2],
    #[serde(default)]
    pub tracks: Vec<BridgeMidiPreviewTrack>,
}

impl BridgeMidiPreview {
    pub fn display_tracks(&self) -> Vec<BridgeMidiPreviewTrack> {
        if self.tracks.is_empty() {
            return vec![BridgeMidiPreviewTrack {
                track_id: self.track_id,
                track_name: self.track_name.clone(),
                note_count: self.note_count,
                pitch_range: self.pitch_range,
            }];
        }
        self.tracks.clone()
    }
}
```

Update the import in `atri-host/atri-bridge-vst3/src/tests.rs` to include `BridgeMidiPreviewTrack` only if the test compares entire structs. Prefer field assertions to avoid the extra import.

- [ ] **Step 4: Run contract tests**

Run:

```powershell
cargo test -p atri-bridge-vst3 bridge_export_response
```

Expected: PASS for bridge export response tests.

- [ ] **Step 5: Commit Rust contract change**

Run:

```powershell
git add atri-host/atri-bridge-vst3/src/bridge_contract.rs atri-host/atri-bridge-vst3/src/tests.rs
git commit -m "Parse multi-track bridge MIDI previews"
```

### Task 3: Editor State And View Model Scroll Rows

**Files:**
- Modify: `atri-host/atri-bridge-vst3/src/editor.rs`
- Modify: `atri-host/atri-bridge-vst3/src/tests.rs`

- [ ] **Step 1: Write failing editor view tests**

Add these tests after `editor_view_model_exposes_midi_preview()` in `atri-host/atri-bridge-vst3/src/tests.rs`:

```rust
#[test]
fn editor_view_model_limits_midi_preview_to_two_visible_track_rows() {
    let mut state = BridgeEditorState::default();
    state.apply_export_response(BridgeExportResponse {
        ok: true,
        bridge: None,
        export: Some(serde_json::json!({
            "format": "midi",
            "path": "data/music_workstation/exports/region.mid",
            "bridge_preview": {
                "kind": "midi_region",
                "track_id": 1,
                "track_name": "Piano",
                "beat_range": [4.0, 8.0],
                "note_count": 20,
                "pitch_range": [36, 84],
                "tracks": [
                    {"track_id": 1, "track_name": "Piano", "note_count": 8, "pitch_range": [60, 84]},
                    {"track_id": 2, "track_name": "Bass", "note_count": 6, "pitch_range": [36, 48]},
                    {"track_id": 3, "track_name": "Pad", "note_count": 6, "pitch_range": [52, 72]}
                ]
            }
        })),
    });

    let view = BridgeEditorViewModel::from_state(&state, 640, 320);
    let preview = view.preview().expect("preview should render");

    assert_eq!(preview.track_rows().len(), 2);
    assert_eq!(preview.track_rows()[0].title, "Piano");
    assert_eq!(preview.track_rows()[1].title, "Bass");
    assert!(!preview.can_scroll_up);
    assert!(preview.can_scroll_down);
}

#[test]
fn editor_state_scrolls_midi_preview_rows_and_clamps_at_bounds() {
    let mut state = BridgeEditorState::default();
    state.apply_export_response(BridgeExportResponse {
        ok: true,
        bridge: None,
        export: Some(serde_json::json!({
            "format": "midi",
            "path": "data/music_workstation/exports/region.mid",
            "bridge_preview": {
                "kind": "midi_region",
                "track_id": 1,
                "track_name": "Piano",
                "beat_range": [4.0, 8.0],
                "note_count": 20,
                "pitch_range": [36, 84],
                "tracks": [
                    {"track_id": 1, "track_name": "Piano", "note_count": 8, "pitch_range": [60, 84]},
                    {"track_id": 2, "track_name": "Bass", "note_count": 6, "pitch_range": [36, 48]},
                    {"track_id": 3, "track_name": "Pad", "note_count": 6, "pitch_range": [52, 72]}
                ]
            }
        })),
    });

    assert!(state.scroll_midi_preview(1));
    let view = BridgeEditorViewModel::from_state(&state, 640, 320);
    let preview = view.preview().expect("preview should render");
    assert_eq!(preview.track_rows()[0].title, "Bass");
    assert_eq!(preview.track_rows()[1].title, "Pad");
    assert!(preview.can_scroll_up);
    assert!(!preview.can_scroll_down);

    assert!(!state.scroll_midi_preview(1));
    assert!(state.scroll_midi_preview(-1));
    let view = BridgeEditorViewModel::from_state(&state, 640, 320);
    assert_eq!(view.preview().unwrap().track_rows()[0].title, "Piano");
}
```

- [ ] **Step 2: Run editor tests to verify failure**

Run:

```powershell
cargo test -p atri-bridge-vst3 editor_view_model_limits_midi_preview_to_two_visible_track_rows
cargo test -p atri-bridge-vst3 editor_state_scrolls_midi_preview_rows_and_clamps_at_bounds
```

Expected: FAIL because the view model has no track rows and state has no `scroll_midi_preview()`.

- [ ] **Step 3: Add preview row types and scroll state**

Modify `atri-host/atri-bridge-vst3/src/editor.rs`:

```rust
const MIDI_PREVIEW_VISIBLE_ROWS: usize = 2;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BridgeEditorPreviewTrackRow {
    pub title: String,
    pub detail: String,
    pub pitch_low: i32,
    pub pitch_high: i32,
}
```

Extend `BridgeEditorPreview`:

```rust
pub struct BridgeEditorPreview {
    pub title: String,
    pub detail: String,
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
    pub pitch_low: i32,
    pub pitch_high: i32,
    pub can_scroll_up: bool,
    pub can_scroll_down: bool,
    track_rows: Vec<BridgeEditorPreviewTrackRow>,
}

impl BridgeEditorPreview {
    pub fn track_rows(&self) -> &[BridgeEditorPreviewTrackRow] {
        &self.track_rows
    }
}
```

Add `midi_preview_scroll_offset: usize` to `BridgeEditorState`, initialize it to `0` in `Default`, reset it to `0` in `apply_export_response()` success, `apply_external_export_response()` success, and `mark_export_error()`.

Add this method to `BridgeEditorState`:

```rust
pub fn scroll_midi_preview(&mut self, rows: i32) -> bool {
    let Some(preview) = self.last_midi_preview.as_ref() else {
        return false;
    };
    let track_count = preview.display_tracks().len();
    let max_offset = track_count.saturating_sub(MIDI_PREVIEW_VISIBLE_ROWS);
    let next = if rows < 0 {
        self.midi_preview_scroll_offset
            .saturating_sub(rows.unsigned_abs() as usize)
    } else {
        self.midi_preview_scroll_offset.saturating_add(rows as usize)
    }
    .min(max_offset);
    if next == self.midi_preview_scroll_offset {
        return false;
    }
    self.midi_preview_scroll_offset = next;
    true
}
```

- [ ] **Step 4: Build preview rows in the view model**

Replace `preview_from_state()` in `atri-host/atri-bridge-vst3/src/editor.rs` with:

```rust
fn preview_from_state(state: &BridgeEditorState, width: i32) -> Option<BridgeEditorPreview> {
    let preview = state.last_midi_preview()?;
    let [start, end] = preview.beat_range;
    let [pitch_low, pitch_high] = preview.pitch_range;
    let tracks = preview.display_tracks();
    if tracks.is_empty() {
        return None;
    }
    let max_offset = tracks.len().saturating_sub(MIDI_PREVIEW_VISIBLE_ROWS);
    let offset = state.midi_preview_scroll_offset.min(max_offset);
    let track_rows = tracks
        .iter()
        .skip(offset)
        .take(MIDI_PREVIEW_VISIBLE_ROWS)
        .map(|track| {
            let [track_low, track_high] = track.pitch_range;
            BridgeEditorPreviewTrackRow {
                title: track.track_name.clone(),
                detail: format!(
                    "{} notes | {}-{}",
                    track.note_count,
                    midi_note_name(track_low),
                    midi_note_name(track_high)
                ),
                pitch_low: track_low,
                pitch_high: track_high,
            }
        })
        .collect();

    Some(BridgeEditorPreview {
        title: preview.track_name.clone(),
        detail: format!(
            "{start:.2}-{end:.2} beat | {} tracks | {} notes | {}-{}",
            tracks.len(),
            preview.note_count,
            midi_note_name(pitch_low),
            midi_note_name(pitch_high)
        ),
        x: 24,
        y: 68,
        width: width.saturating_sub(48).max(120),
        height: 44,
        pitch_low,
        pitch_high,
        can_scroll_up: offset > 0,
        can_scroll_down: offset < max_offset,
        track_rows,
    })
}
```

Update `editor_view_model_exposes_midi_preview()` expected detail from:

```rust
assert_eq!(preview.detail, "4.00-8.00 beat | 12 notes | C3-C5");
```

to:

```rust
assert_eq!(preview.detail, "4.00-8.00 beat | 1 tracks | 12 notes | C3-C5");
assert_eq!(preview.track_rows()[0].title, "Edited Synth");
```

- [ ] **Step 5: Run editor tests**

Run:

```powershell
cargo test -p atri-bridge-vst3 editor_view_model
cargo test -p atri-bridge-vst3 editor_state_scrolls_midi_preview_rows_and_clamps_at_bounds
```

Expected: PASS for editor view model and scroll state tests.

- [ ] **Step 6: Commit editor scroll model**

Run:

```powershell
git add atri-host/atri-bridge-vst3/src/editor.rs atri-host/atri-bridge-vst3/src/tests.rs
git commit -m "Add scrollable bridge MIDI preview rows"
```

### Task 4: Surface Wheel Events And Rendering

**Files:**
- Modify: `atri-host/atri-bridge-vst3/src/editor_surface.rs`
- Modify: `atri-host/atri-bridge-vst3/src/factory.rs`
- Modify: `atri-host/atri-bridge-vst3/src/tests.rs`

- [ ] **Step 1: Write failing surface tests**

Add this test after `editor_surface_spec_marks_midi_preview_as_primary_drag_source()` in `atri-host/atri-bridge-vst3/src/tests.rs`:

```rust
#[test]
fn editor_surface_spec_scrolls_only_when_wheel_is_inside_midi_preview() {
    let mut state = BridgeEditorState::default();
    state.apply_export_response(BridgeExportResponse {
        ok: true,
        bridge: None,
        export: Some(serde_json::json!({
            "format": "midi",
            "path": "data/music_workstation/exports/region.mid",
            "bridge_preview": {
                "kind": "midi_region",
                "track_id": 1,
                "track_name": "Piano",
                "beat_range": [4.0, 8.0],
                "note_count": 20,
                "pitch_range": [36, 84],
                "tracks": [
                    {"track_id": 1, "track_name": "Piano", "note_count": 8, "pitch_range": [60, 84]},
                    {"track_id": 2, "track_name": "Bass", "note_count": 6, "pitch_range": [36, 48]},
                    {"track_id": 3, "track_name": "Pad", "note_count": 6, "pitch_range": [52, 72]}
                ]
            }
        })),
    });
    let view = BridgeEditorViewModel::from_state(&state, 640, 320);
    let spec = EditorSurfaceSpec::from_view_model(
        0x1234,
        EditorPlatformType::WindowsHwnd,
        SurfaceRect {
            left: 0,
            top: 0,
            width: 640,
            height: 320,
        },
        &view,
    )
    .unwrap();

    assert_eq!(spec.preview_scroll_rows(48, 84, -120), Some(1));
    assert_eq!(spec.preview_scroll_rows(48, 84, 120), Some(-1));
    assert_eq!(spec.preview_scroll_rows(48, 124, -120), None);
    assert_eq!(spec.preview_scroll_rows(48, 84, 0), None);
}
```

Add a factory callback test in `atri-host/atri-bridge-vst3/src/factory.rs` near the existing surface event callback tests:

```rust
#[test]
fn bridge_surface_event_callback_scrolls_midi_preview_rows() {
    let view = BridgePlugView::default();
    view.apply_export_response(BridgeExportResponse {
        ok: true,
        bridge: None,
        export: Some(serde_json::json!({
            "format": "midi",
            "path": "data/music_workstation/exports/region.mid",
            "bridge_preview": {
                "kind": "midi_region",
                "track_id": 1,
                "track_name": "Piano",
                "beat_range": [4.0, 8.0],
                "note_count": 20,
                "pitch_range": [36, 84],
                "tracks": [
                    {"track_id": 1, "track_name": "Piano", "note_count": 8, "pitch_range": [60, 84]},
                    {"track_id": 2, "track_name": "Bass", "note_count": 6, "pitch_range": [36, 48]},
                    {"track_id": 3, "track_name": "Pad", "note_count": 6, "pitch_range": [52, 72]}
                ]
            }
        })),
    });
    view.refresh_view_model();

    unsafe {
        bridge_surface_event_callback(
            &view as *const BridgePlugView as *mut c_void,
            NativeEditorSurfaceEvent::ScrollPreview(1),
        );
    }

    let preview = lock_recover(&view.view_model).preview().cloned().unwrap();
    assert_eq!(preview.track_rows()[0].title, "Bass");
}
```

- [ ] **Step 2: Run surface tests to verify failure**

Run:

```powershell
cargo test -p atri-bridge-vst3 editor_surface_spec_scrolls_only_when_wheel_is_inside_midi_preview
cargo test -p atri-bridge-vst3 bridge_surface_event_callback_scrolls_midi_preview_rows
```

Expected: FAIL because `preview_scroll_rows()` and `ScrollPreview` do not exist.

- [ ] **Step 3: Add surface scroll event plumbing**

In `atri-host/atri-bridge-vst3/src/editor_surface.rs`, extend `NativeEditorSurfaceEvent`:

```rust
pub enum NativeEditorSurfaceEvent {
    Action(BridgeEditorAction),
    DragExport,
    ScrollPreview(i32),
    Tick,
}
```

Add this method to `EditorSurfaceSpec`:

```rust
pub fn preview_scroll_rows(&self, x: i32, y: i32, wheel_delta: i32) -> Option<i32> {
    if wheel_delta == 0 {
        return None;
    }
    let preview = self.preview.as_ref()?;
    if !preview.contains(x, y) {
        return None;
    }
    Some(if wheel_delta < 0 { 1 } else { -1 })
}
```

In `atri-host/atri-bridge-vst3/src/factory.rs`, add:

```rust
fn scroll_midi_preview(&self, rows: i32) {
    let changed = lock_recover(&self.editor_state).scroll_midi_preview(rows);
    if changed {
        self.refresh_view_model();
        self.sync_native_surface();
    }
}
```

Update `bridge_surface_event_callback()`:

```rust
NativeEditorSurfaceEvent::ScrollPreview(rows) => view.scroll_midi_preview(rows),
```

- [ ] **Step 4: Add Windows mouse wheel handling**

In `atri-host/atri-bridge-vst3/src/editor_surface.rs`, update the Windows imports:

```rust
use windows_sys::Win32::Foundation::{
    COLORREF, HINSTANCE, HWND, LPARAM, LRESULT, POINT, RECT, WPARAM,
};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    CREATESTRUCTW, CS_HREDRAW, CS_VREDRAW, CreateWindowExW, DefWindowProcW, DestroyWindow,
    GWLP_USERDATA, GetWindowLongPtrW, KillTimer, RegisterClassW, SW_SHOW, SWP_NOACTIVATE,
    SWP_NOZORDER, ScreenToClient, SetTimer, SetWindowLongPtrW, SetWindowPos, ShowWindow,
    WM_ERASEBKGND, WM_LBUTTONDOWN, WM_MOUSEWHEEL, WM_NCCREATE, WM_NCDESTROY, WM_PAINT,
    WM_TIMER, WNDCLASSW, WS_CHILD, WS_VISIBLE,
};
```

Add this match arm in `window_proc()`:

```rust
WM_MOUSEWHEEL => {
    if let Some(state) = window_state(hwnd) {
        let mut point = POINT {
            x: signed_low_word(lparam),
            y: signed_high_word(lparam),
        };
        unsafe {
            ScreenToClient(hwnd, &mut point);
        }
        let wheel_delta = ((wparam >> 16) & 0xffff) as i16 as i32;
        let dispatch = unsafe {
            let state_ref = &*state;
            state_ref
                .spec
                .preview_scroll_rows(point.x, point.y, wheel_delta)
                .map(|rows| {
                    (
                        NativeEditorSurfaceEvent::ScrollPreview(rows),
                        state_ref.callback_context,
                        state_ref.callback,
                    )
                })
        };
        if let Some((event, context, callback)) = dispatch {
            unsafe {
                callback(context, event);
            }
            return 0;
        }
    }
    unsafe { DefWindowProcW(hwnd, msg, wparam, lparam) }
}
```

- [ ] **Step 5: Render compact multi-track rows**

Replace `paint_preview()` in `atri-host/atri-bridge-vst3/src/editor_surface.rs` with a version that draws `preview.detail` and up to two rows:

```rust
fn paint_preview(hdc: isize, preview: &BridgeEditorPreview) {
    let rect = RECT {
        left: preview.x,
        top: preview.y,
        right: preview.x + preview.width,
        bottom: preview.y + preview.height,
    };
    fill_rect(hdc, &rect, rgb(18, 22, 28));
    stroke_rect(hdc, &rect, rgb(92, 142, 218));

    unsafe {
        SetTextColor(hdc, rgb(244, 247, 250));
    }
    text_out(hdc, preview.x + 10, preview.y + 5, &preview.detail);

    let mut row_y = preview.y + 22;
    for row in preview.track_rows() {
        let key_rect = RECT {
            left: preview.x + 10,
            top: row_y,
            right: preview.x + 48,
            bottom: row_y + 14,
        };
        fill_rect(hdc, &key_rect, rgb(34, 39, 47));
        stroke_rect(hdc, &key_rect, rgb(69, 78, 92));

        let lane = RECT {
            left: preview.x + 56,
            top: row_y,
            right: preview.x + preview.width - 12,
            bottom: row_y + 14,
        };
        fill_rect(hdc, &lane, rgb(37, 63, 99));
        stroke_rect(hdc, &lane, rgb(112, 166, 242));

        unsafe {
            SetTextColor(hdc, rgb(244, 247, 250));
        }
        text_out(hdc, preview.x + 62, row_y - 1, &row.title);
        unsafe {
            SetTextColor(hdc, rgb(166, 178, 194));
        }
        text_out(hdc, preview.x + 190, row_y - 1, &row.detail);
        row_y += 16;
    }

    unsafe {
        SetTextColor(hdc, rgb(166, 178, 194));
    }
    if preview.can_scroll_up {
        text_out(hdc, preview.x + preview.width - 22, preview.y + 5, "^");
    }
    if preview.can_scroll_down {
        text_out(hdc, preview.x + preview.width - 22, preview.y + 26, "v");
    }
}
```

- [ ] **Step 6: Run surface tests**

Run:

```powershell
cargo test -p atri-bridge-vst3 editor_surface_spec_scrolls_only_when_wheel_is_inside_midi_preview
cargo test -p atri-bridge-vst3 bridge_surface_event_callback_scrolls_midi_preview_rows
```

Expected: PASS for both tests.

- [ ] **Step 7: Commit surface wheel support**

Run:

```powershell
git add atri-host/atri-bridge-vst3/src/editor_surface.rs atri-host/atri-bridge-vst3/src/factory.rs atri-host/atri-bridge-vst3/src/tests.rs
git commit -m "Scroll bridge MIDI preview tracks with mouse wheel"
```

### Task 5: Full Verification

**Files:**
- No code changes expected.

- [ ] **Step 1: Format Rust code**

Run:

```powershell
cargo fmt
```

Expected: command exits 0.

- [ ] **Step 2: Run focused frontend-free bridge verification**

Run:

```powershell
uv run pytest tests/test_dashboard_bridge_auth.py -q
cargo test -p atri-bridge-vst3
```

Expected: Python bridge auth/export tests pass; Rust crate reports all unit tests and package bridge tests passed.

- [ ] **Step 3: Run broader project checks used for this feature area**

Run:

```powershell
npm.cmd run lint
npm.cmd run build
git diff --check
```

Use working directory `D:\Users\E-VPN1\ATRI\frontend` for the two npm commands and `D:\Users\E-VPN1\ATRI` for `git diff --check`.

Expected: lint exits 0, build exits 0 with only the existing Vite large chunk warning if it appears, and diff check exits 0.

- [ ] **Step 4: Commit formatting-only changes if Cargo fmt changed files**

If `git status --short` shows only formatting changes, commit them:

```powershell
git add atri-host/atri-bridge-vst3/src
git commit -m "Format bridge multi-track MIDI preview"
```

If `git status --short` is clean, skip this step.

## Self-Review

- Spec coverage: dashboard metadata, Rust contract compatibility, fixed preview size, two visible rows, wheel scrolling inside preview, error clearing behavior, and drag behavior are all mapped to tasks.
- Placeholder scan: no open implementation placeholders remain; each test and code step includes concrete content.
- Type consistency: `BridgeMidiPreview.tracks`, `BridgeMidiPreviewTrack`, `BridgeEditorPreviewTrackRow`, `scroll_midi_preview()`, `preview_scroll_rows()`, and `NativeEditorSurfaceEvent::ScrollPreview(i32)` are named consistently across tasks.
