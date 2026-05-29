# VST Bridge Mini Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically hand DAW-agent MIDI edits to the current VST bridge instance and show a draggable mini piano preview in the plugin window.

**Architecture:** The DAW agent frontend auto-exports successful MIDI artifacts to the bridge instance. The dashboard enriches bridge MIDI exports with lightweight preview metadata and persists it in the existing latest-export files. The Rust VST bridge parses that metadata into editor state, renders a native mini preview, and treats that preview rectangle as the main drag source.

**Tech Stack:** Vue 3, browser Fetch API, Quart/Python dashboard routes, Rust VST3 bridge, Win32 GDI editor surface, existing native Windows `CF_HDROP` drag service.

---

## Scope Check

The spec spans frontend, dashboard, and plugin code, but all changes serve one sequential handoff flow. Keep this as one implementation plan because each task produces a testable slice of the same feature and preserves the current manual fallback.

## File Structure

- Modify `frontend/src/components/chat/midiArtifact.js`
  - Owns DAW-agent URL detection and stable artifact auto-export keys.
  - Keeps export payload construction in one place.
- Modify `frontend/src/components/chat/midiArtifact.test.js`
  - Covers URL detection and artifact-key stability.
- Modify `frontend/src/components/chat/MidiArtifactCard.vue`
  - Adds auto bridge export state and status text.
  - Reuses the existing manual MIDI export function as fallback.
- Modify `tests/test_daw_agent_frontend.py`
  - Source-level guard that the card performs auto bridge export only for DAW-agent bridge instances.
- Modify `dashboard/music.py`
  - Adds `_bridge_preview_for_midi_export()` and attaches `bridge_preview` to bridge MIDI exports.
  - Leaves non-bridge exports unchanged.
- Modify `tests/test_dashboard_bridge_auth.py`
  - Verifies latest global and per-instance bridge exports include preview metadata.
- Modify `atri-host/atri-bridge-vst3/src/bridge_contract.rs`
  - Adds typed `BridgeMidiPreview` parsing from the existing JSON export object.
- Modify `atri-host/atri-bridge-vst3/src/editor.rs`
  - Stores preview metadata in `BridgeEditorState`.
  - Exposes `BridgeEditorPreview` through `BridgeEditorViewModel`.
- Modify `atri-host/atri-bridge-vst3/src/editor_surface.rs`
  - Carries preview data into `EditorSurfaceSpec`.
  - Draws a compact native preview band on Windows.
  - Makes the preview band the primary drag source.
- Modify `atri-host/atri-bridge-vst3/src/tests.rs`
  - Covers typed preview parsing, editor state/view model behavior, and drag hit testing.
- Modify `atri-host/atri-bridge-vst3/src/factory.rs`
  - Adjusts existing latest-export tests to include preview metadata and prove plugin polling updates the preview.

---

### Task 1: Frontend Auto Bridge Export Helpers

**Files:**
- Modify: `frontend/src/components/chat/midiArtifact.js`
- Test: `frontend/src/components/chat/midiArtifact.test.js`

- [ ] **Step 1: Write failing helper tests**

Add these imports in `frontend/src/components/chat/midiArtifact.test.js`:

```js
import {
  bridgeAutoExportKeyForArtifact,
  bridgeInstanceIdFromLocation,
  buildMidiArtifactPreview,
  buildMidiArtifactView,
  buildMidiArtifactViewFromArgs,
  exportPayloadForMidiArtifact,
  isDawAgentSurfaceLocation,
  isMidiArtifactTool,
} from './midiArtifact.js'
```

Append these assertions near the existing `bridgeInstanceIdFromLocation` assertions:

```js
assert.equal(isDawAgentSurfaceLocation({ search: '?surface=daw-agent&instance_id=bridge-a' }), true)
assert.equal(isDawAgentSurfaceLocation({ search: '?surface=chat&instance_id=bridge-a' }), false)
assert.equal(isDawAgentSurfaceLocation({ search: '?instance_id=bridge-a' }), false)

const autoKey = bridgeAutoExportKeyForArtifact(updateByIdView, {
  tool: 'midi_diff',
  args: {
    track_id: 3,
    operations: [{ op: 'update_note', id: 'inside-b', velocity: 72 }],
  },
})
assert.equal(autoKey, 'midi_diff:3:6:7.5:{"operations":[{"id":"inside-b","op":"update_note","velocity":72}],"track_id":3}')
assert.equal(bridgeAutoExportKeyForArtifact(null, { tool: 'midi_diff', args: {} }), '')
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
node frontend\src\components\chat\midiArtifact.test.js
```

Expected: FAIL with an ESM import error saying `bridgeAutoExportKeyForArtifact` or `isDawAgentSurfaceLocation` is not exported.

- [ ] **Step 3: Add helper implementation**

In `frontend/src/components/chat/midiArtifact.js`, add these exports after `bridgeInstanceIdFromLocation()`:

```js
export function isDawAgentSurfaceLocation(location = globalThis.window?.location) {
  const search = typeof location?.search === 'string' ? location.search : ''
  return new URLSearchParams(search).get('surface') === 'daw-agent'
}

export function bridgeAutoExportKeyForArtifact(view, toolData) {
  const trackId = Number(view?.track?.id)
  const start = Number(view?.range?.start)
  const end = Number(view?.range?.end)
  if (!Number.isFinite(trackId) || !Number.isFinite(start) || !Number.isFinite(end)) {
    return ''
  }
  const tool = String(toolData?.tool || '')
  const args = stableJson(toolData?.args || {})
  return `${tool}:${trackId}:${start}:${end}:${args}`
}
```

Add this helper near the bottom of the same file, before `defaultPreviewProject()`:

```js
function stableJson(value) {
  return JSON.stringify(sortStable(value))
}

function sortStable(value) {
  if (Array.isArray(value)) return value.map(sortStable)
  if (!value || typeof value !== 'object') return value
  return Object.fromEntries(
    Object.keys(value)
      .sort()
      .map(key => [key, sortStable(value[key])])
  )
}
```

- [ ] **Step 4: Run helper test**

Run:

```powershell
node frontend\src\components\chat\midiArtifact.test.js
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add frontend/src/components/chat/midiArtifact.js frontend/src/components/chat/midiArtifact.test.js
git commit -m "Add bridge MIDI auto-export helpers"
```

---

### Task 2: Frontend MIDI Artifact Auto Export

**Files:**
- Modify: `frontend/src/components/chat/MidiArtifactCard.vue`
- Modify: `tests/test_daw_agent_frontend.py`

- [ ] **Step 1: Write failing source-level frontend test**

Append this test to `tests/test_daw_agent_frontend.py`:

```python
def test_midi_artifact_card_auto_exports_bridge_midi_for_daw_agent_surface():
    source = (ROOT / "frontend" / "src" / "components" / "chat" / "MidiArtifactCard.vue").read_text(
        encoding="utf-8"
    )

    assert "isDawAgentSurfaceLocation" in source
    assert "bridgeAutoExportKeyForArtifact" in source
    assert "autoExportBridgeMidi" in source
    assert "lastAutoExportKey" in source
    assert "bridgeStatusLabel" in source
    assert "bridgeInstanceIdFromLocation()" in source
```

- [ ] **Step 2: Run test to verify failure**

Run:

```powershell
uv run pytest tests/test_daw_agent_frontend.py::test_midi_artifact_card_auto_exports_bridge_midi_for_daw_agent_surface -q
```

Expected: FAIL because the new identifiers do not exist in `MidiArtifactCard.vue`.

- [ ] **Step 3: Implement auto export in the Vue component**

In the import from `./midiArtifact.js`, include the new helpers:

```js
import {
  bridgeAutoExportKeyForArtifact,
  bridgeInstanceIdFromLocation,
  buildMidiArtifactPreview,
  exportPayloadForMidiArtifact,
  isDawAgentSurfaceLocation,
} from './midiArtifact.js'
```

After `const exporting = ref(false)`, add:

```js
const autoExporting = ref(false)
const bridgeExportError = ref('')
const lastAutoExportKey = ref('')
```

After `rangeLabel`, add:

```js
const bridgeStatusLabel = computed(() => {
  if (!isDawAgentSurfaceLocation() || !bridgeInstanceIdFromLocation()) return ''
  if (autoExporting.value) return 'Sending to bridge'
  if (bridgeExportError.value) return 'Bridge export failed'
  if (midiExport.value?.path) return 'Bridge ready'
  return ''
})
```

Inside the template header, directly after `<span class="midi-range">{{ rangeLabel }}</span>`, add:

```vue
      <span
        v-if="bridgeStatusLabel"
        class="midi-bridge-status"
      >{{ bridgeStatusLabel }}</span>
```

Update the `.midi-artifact-head` grid to reserve a status column:

```css
.midi-artifact-head {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto auto;
  gap: 10px;
  align-items: center;
  margin-bottom: 8px;
}
```

Add this style next to `.midi-kicker, .midi-range`:

```css
.midi-bridge-status {
  color: var(--acc2);
  font-family: var(--mono);
  font-size: 11px;
  white-space: nowrap;
}
```

In the watcher that resets `midiExport`, also reset bridge state:

```js
watch(
  () => `${props.toolData?.tool}:${JSON.stringify(props.toolData?.args || {})}`,
  () => {
    midiExport.value = null
    bridgeExportError.value = ''
    lastAutoExportKey.value = ''
    ensureHostProject()
  }
)
```

Replace `watch(artifact, drawArtifact, { immediate: true })` with:

```js
watch(artifact, () => {
  drawArtifact()
  autoExportBridgeMidi()
}, { immediate: true })
```

Update the `props.toolData?.status` watcher:

```js
watch(
  () => props.toolData?.status,
  async (status) => {
    if (status === 'success') {
      await refreshHostProject()
      await autoExportBridgeMidi()
    }
  }
)
```

Add the auto-export function before `exportMidi()`:

```js
async function autoExportBridgeMidi() {
  const instanceId = bridgeInstanceIdFromLocation()
  if (!isDawAgentSurfaceLocation() || !instanceId) return
  if (props.toolData?.status && props.toolData.status !== 'success') return
  if (!artifact.value || autoExporting.value) return

  const key = bridgeAutoExportKeyForArtifact(artifact.value, props.toolData)
  if (!key || key === lastAutoExportKey.value) return

  const payload = exportPayloadForMidiArtifact(artifact.value, 'midi', { instanceId })
  if (!payload) return

  autoExporting.value = true
  bridgeExportError.value = ''
  try {
    const res = await api.studioExportAudio(payload)
    midiExport.value = res.export || null
    lastAutoExportKey.value = key
  } catch (err) {
    bridgeExportError.value = err.message || 'MIDI bridge export failed'
  } finally {
    autoExporting.value = false
  }
}
```

In `exportMidi()`, set `lastAutoExportKey` on success so a manual click does not immediately re-export the same artifact:

```js
    const res = await api.studioExportAudio(payload)
    midiExport.value = res.export || null
    lastAutoExportKey.value = bridgeAutoExportKeyForArtifact(artifact.value, props.toolData)
```

- [ ] **Step 4: Run frontend tests**

Run:

```powershell
node frontend\src\components\chat\midiArtifact.test.js
uv run pytest tests/test_daw_agent_frontend.py::test_midi_artifact_card_auto_exports_bridge_midi_for_daw_agent_surface -q
```

Expected: both PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add frontend/src/components/chat/MidiArtifactCard.vue tests/test_daw_agent_frontend.py
git commit -m "Auto export DAW agent MIDI artifacts to bridge"
```

---

### Task 3: Dashboard Bridge Preview Metadata

**Files:**
- Modify: `dashboard/music.py`
- Modify: `tests/test_dashboard_bridge_auth.py`

- [ ] **Step 1: Write failing dashboard tests**

In `tests/test_dashboard_bridge_auth.py`, extend `test_bridge_latest_export_tracks_daw_agent_midi_export()` after the existing `beat_range` assertion:

```python
    assert latest["export"]["bridge_preview"] == {
        "kind": "midi_region",
        "track_id": 1,
        "track_name": "Lead",
        "beat_range": [0.0, 1.0],
        "note_count": 1,
        "pitch_range": [60, 60],
    }
```

Then add this new test below it:

```python
@pytest.mark.asyncio
async def test_bridge_latest_export_preserves_preview_for_instance(monkeypatch, tmp_path):
    monkeypatch.setattr(
        music_routes,
        "load_project",
        lambda: {
            "title": "Bridge MIDI",
            "tempo": 120,
            "time_signature": [4, 4],
            "tracks": [
                {
                    "id": 1,
                    "type": "instrument",
                    "name": "Lead",
                    "notes": [
                        {"pitch": 60, "start": 0, "duration": 0.5, "velocity": 96},
                        {"pitch": 67, "start": 0.5, "duration": 0.5, "velocity": 96},
                    ],
                    "midi_events": [],
                }
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
            "track_ids": [1],
            "consumer": "bridge",
            "instance_id": "bridge-preview",
            "start_beat": 0,
            "end_beat": 1,
        },
        scope_base={"client": ("127.0.0.1", 54000)},
    )
    latest_response = await client.get(
        "/api/music/studio/bridge/export/latest?instance_id=bridge-preview",
        scope_base={"client": ("127.0.0.1", 54000)},
    )

    assert export_response.status_code == 200
    assert latest_response.status_code == 200
    latest = await latest_response.get_json()
    assert latest["export"]["bridge_scope"] == {"instance_id": "bridge-preview"}
    assert latest["export"]["bridge_preview"] == {
        "kind": "midi_region",
        "track_id": 1,
        "track_name": "Lead",
        "beat_range": [0.0, 1.0],
        "note_count": 2,
        "pitch_range": [60, 67],
    }
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
uv run pytest tests/test_dashboard_bridge_auth.py::test_bridge_latest_export_tracks_daw_agent_midi_export tests/test_dashboard_bridge_auth.py::test_bridge_latest_export_preserves_preview_for_instance -q
```

Expected: FAIL with missing `bridge_preview`.

- [ ] **Step 3: Implement preview metadata**

In `dashboard/music.py`, inside `_perform_midi_export()` after the `if "beat_range" in summary:` block and before `manifest = build_export_manifest(...)`, add:

```python
    if consumer == "bridge":
        preview = _bridge_preview_for_midi_export(project, track_ids, summary.get("beat_range"))
        if preview:
            export["bridge_preview"] = preview
```

Add these helper functions after `_perform_midi_export()` and before `_perform_dawproject_export()`:

```python
def _bridge_preview_for_midi_export(
    project: dict[str, Any],
    track_ids: list[int] | None,
    beat_range: Any,
) -> dict[str, Any] | None:
    selected_ids = set(track_ids or [])
    tracks = [
        track
        for track in project.get("tracks", [])
        if isinstance(track, dict)
        and not _is_automation_track(track)
        and (not selected_ids or int(track.get("id") or -1) in selected_ids)
    ]
    if not tracks:
        return None

    track = tracks[0]
    start, end = _preview_beat_range(project, beat_range)
    notes = [
        note
        for note in _preview_track_notes(track)
        if note["start"] < end and note["start"] + note["duration"] > start
    ]
    pitches = [note["pitch"] for note in notes]
    pitch_range = [min(pitches), max(pitches)] if pitches else [60, 60]
    return {
        "kind": "midi_region",
        "track_id": int(track["id"]),
        "track_name": str(track.get("name") or f"Track {track['id']}"),
        "beat_range": [float(start), float(end)],
        "note_count": len(notes),
        "pitch_range": pitch_range,
    }


def _preview_beat_range(project: dict[str, Any], beat_range: Any) -> tuple[float, float]:
    if isinstance(beat_range, (list, tuple)) and len(beat_range) >= 2:
        try:
            start = max(0.0, float(beat_range[0]))
            end = max(start + 0.25, float(beat_range[1]))
            return start, end
        except (TypeError, ValueError):
            pass
    length = project.get("length_beats", 16)
    try:
        return 0.0, max(0.25, float(length or 16))
    except (TypeError, ValueError):
        return 0.0, 16.0


def _preview_track_notes(track: dict[str, Any]) -> list[dict[str, float | int]]:
    notes: list[dict[str, float | int]] = []
    for note in track.get("notes", []) if isinstance(track.get("notes"), list) else []:
        normalized = _preview_note(note, clip_start=0.0)
        if normalized:
            notes.append(normalized)
    for clip in track.get("clips", []) if isinstance(track.get("clips"), list) else []:
        if not isinstance(clip, dict) or clip.get("type") != "midi":
            continue
        try:
            clip_start = float(clip.get("start") or 0.0)
        except (TypeError, ValueError):
            clip_start = 0.0
        for note in clip.get("notes", []) if isinstance(clip.get("notes"), list) else []:
            normalized = _preview_note(note, clip_start=clip_start)
            if normalized:
                notes.append(normalized)
    return notes


def _preview_note(note: Any, *, clip_start: float) -> dict[str, float | int] | None:
    if not isinstance(note, dict):
        return None
    try:
        start = clip_start + float(note.get("start", note.get("beat", 0.0)) or 0.0)
        duration = max(0.001, float(note.get("duration", 0.25) or 0.25))
        pitch = max(0, min(127, int(round(float(note.get("pitch", 60) or 60)))))
    except (TypeError, ValueError):
        return None
    return {"start": start, "duration": duration, "pitch": pitch}
```

- [ ] **Step 4: Run dashboard tests**

Run:

```powershell
uv run pytest tests/test_dashboard_bridge_auth.py::test_bridge_latest_export_tracks_daw_agent_midi_export tests/test_dashboard_bridge_auth.py::test_bridge_latest_export_preserves_preview_for_instance -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```powershell
git add dashboard/music.py tests/test_dashboard_bridge_auth.py
git commit -m "Add bridge MIDI preview metadata"
```

---

### Task 4: Rust Bridge Preview Contract and Editor State

**Files:**
- Modify: `atri-host/atri-bridge-vst3/src/bridge_contract.rs`
- Modify: `atri-host/atri-bridge-vst3/src/editor.rs`
- Modify: `atri-host/atri-bridge-vst3/src/tests.rs`

- [ ] **Step 1: Write failing Rust tests**

In `atri-host/atri-bridge-vst3/src/tests.rs`, add `BridgeMidiPreview` to the bridge contract import:

```rust
use crate::bridge_contract::{
    BridgeExportFormat, BridgeExportRequest, BridgeExportResponse, BridgeHostContext,
    BridgeMidiPreview, BridgeStatus,
};
```

Append these tests after `dashboard_client_posts_bridge_export_request_to_local_dashboard()`:

```rust
#[test]
fn bridge_export_response_parses_midi_preview_metadata() {
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

    assert_eq!(
        response.midi_preview(),
        Some(BridgeMidiPreview {
            kind: "midi_region".to_string(),
            track_id: 3,
            track_name: "Edited Synth".to_string(),
            beat_range: [4.0, 8.0],
            note_count: 12,
            pitch_range: [48, 72],
        })
    );
}

#[test]
fn editor_state_tracks_midi_preview_from_latest_export() {
    let mut state = BridgeEditorState::default();

    let changed = state.apply_external_export_response(BridgeExportResponse {
        ok: true,
        bridge: None,
        export: Some(serde_json::json!({
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
        })),
    });

    assert!(changed);
    assert_eq!(state.last_midi_preview().unwrap().track_name, "Edited Synth");
    assert_eq!(state.last_midi_preview().unwrap().beat_range, [4.0, 8.0]);
}

#[test]
fn editor_view_model_exposes_midi_preview() {
    let mut state = BridgeEditorState::default();
    state.apply_export_response(BridgeExportResponse {
        ok: true,
        bridge: None,
        export: Some(serde_json::json!({
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
        })),
    });

    let view = BridgeEditorViewModel::from_state(&state, 640, 320);
    let preview = view.preview().expect("preview should render");

    assert_eq!(preview.title, "Edited Synth");
    assert_eq!(preview.detail, "4.00-8.00 beat | 12 notes | C3-C5");
    assert!(view.render_lines().iter().any(|line| line == "Drag MIDI preview into DAW"));
}
```

- [ ] **Step 2: Run Rust tests to verify failure**

Run:

```powershell
cargo test -p atri-bridge-vst3 bridge_export_response_parses_midi_preview_metadata editor_state_tracks_midi_preview_from_latest_export editor_view_model_exposes_midi_preview
```

Expected: FAIL because `BridgeMidiPreview`, `midi_preview()`, `last_midi_preview()`, and `preview()` are not defined.

- [ ] **Step 3: Implement typed preview parsing**

In `atri-host/atri-bridge-vst3/src/bridge_contract.rs`, add this struct after `BridgeExportResponse`:

```rust
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BridgeMidiPreview {
    pub kind: String,
    pub track_id: i64,
    pub track_name: String,
    pub beat_range: [f64; 2],
    pub note_count: u64,
    pub pitch_range: [i32; 2],
}
```

Add this method inside `impl BridgeExportResponse`:

```rust
    pub fn midi_preview(&self) -> Option<BridgeMidiPreview> {
        let preview = self
            .export
            .as_ref()
            .and_then(|export| export.get("bridge_preview"))?;
        serde_json::from_value(preview.clone()).ok()
    }
```

- [ ] **Step 4: Implement editor state and view model preview**

In `atri-host/atri-bridge-vst3/src/editor.rs`, add `BridgeMidiPreview` to the import:

```rust
use crate::bridge_contract::{
    BridgeExportFormat, BridgeExportRequest, BridgeExportResponse, BridgeHostContext,
    BridgeMidiPreview, BridgeStatus,
};
```

Add this view-model struct after `BridgeEditorButton`:

```rust
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BridgeEditorPreview {
    pub title: String,
    pub detail: String,
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
    pub pitch_low: i32,
    pub pitch_high: i32,
}

impl BridgeEditorPreview {
    pub fn contains(&self, x: i32, y: i32) -> bool {
        x >= self.x && x < self.x + self.width && y >= self.y && y < self.y + self.height
    }
}
```

Add a `preview` field to `BridgeEditorViewModel`:

```rust
    preview: Option<BridgeEditorPreview>,
```

Set it in `BridgeEditorViewModel::from_state`:

```rust
            preview: preview_from_state(state, width),
```

Add this method to `impl BridgeEditorViewModel`:

```rust
    pub fn preview(&self) -> Option<&BridgeEditorPreview> {
        self.preview.as_ref()
    }
```

Add a state field to `BridgeEditorState`:

```rust
    last_midi_preview: Option<BridgeMidiPreview>,
```

Add this getter:

```rust
    pub fn last_midi_preview(&self) -> Option<&BridgeMidiPreview> {
        self.last_midi_preview.as_ref()
    }
```

In both `apply_export_response()` and `apply_external_export_response()`, store the preview when the response succeeds:

```rust
            self.last_midi_preview = response.midi_preview();
```

Place that line next to `self.last_export_path = ...` before clearing errors.

In `mark_export_error()`, do not clear `last_midi_preview`; preserving the last good drag target matches the existing path behavior.

Initialize `last_midi_preview: None` in `Default`.

Add these helpers near `render_host_context_line()`:

```rust
fn preview_from_state(state: &BridgeEditorState, width: i32) -> Option<BridgeEditorPreview> {
    let preview = state.last_midi_preview()?;
    let [start, end] = preview.beat_range;
    let [pitch_low, pitch_high] = preview.pitch_range;
    Some(BridgeEditorPreview {
        title: preview.track_name.clone(),
        detail: format!(
            "{start:.2}-{end:.2} beat | {} notes | {}-{}",
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
    })
}

fn midi_note_name(pitch: i32) -> String {
    const NAMES: [&str; 12] = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];
    let clamped = pitch.clamp(0, 127);
    let octave = clamped / 12 - 1;
    format!("{}{}", NAMES[(clamped % 12) as usize], octave)
}
```

In `render_state_lines()`, after the export-state line, add:

```rust
    if state.last_midi_preview().is_some() {
        lines.push("Drag MIDI preview into DAW".to_string());
    }
```

- [ ] **Step 5: Run Rust contract/editor tests**

Run:

```powershell
cargo test -p atri-bridge-vst3 bridge_export_response_parses_midi_preview_metadata editor_state_tracks_midi_preview_from_latest_export editor_view_model_exposes_midi_preview
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add atri-host/atri-bridge-vst3/src/bridge_contract.rs atri-host/atri-bridge-vst3/src/editor.rs atri-host/atri-bridge-vst3/src/tests.rs
git commit -m "Track bridge MIDI preview in plugin state"
```

---

### Task 5: Rust Native Preview Drag Surface

**Files:**
- Modify: `atri-host/atri-bridge-vst3/src/editor_surface.rs`
- Modify: `atri-host/atri-bridge-vst3/src/tests.rs`
- Modify: `atri-host/atri-bridge-vst3/src/factory.rs`

- [ ] **Step 1: Write failing surface tests**

In `atri-host/atri-bridge-vst3/src/tests.rs`, add this test after `editor_surface_spec_marks_completed_export_line_as_drag_source()`:

```rust
#[test]
fn editor_surface_spec_marks_midi_preview_as_primary_drag_source() {
    let mut state = BridgeEditorState::default();
    state.apply_export_response(BridgeExportResponse {
        ok: true,
        bridge: None,
        export: Some(serde_json::json!({
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

    assert_eq!(spec.preview().unwrap().title, "Edited Synth");
    assert!(spec.drag_export_hit_test(48, 84));
    assert!(!spec.drag_export_hit_test(116, 138));
}
```

In `atri-host/atri-bridge-vst3/src/factory.rs`, update `bridge_plug_view_surface_tick_applies_latest_daw_agent_export()` response JSON so `export` contains preview metadata:

```json
"bridge_preview": {
    "kind": "midi_region",
    "track_id": 3,
    "track_name": "Edited Synth",
    "beat_range": [4.0, 8.0],
    "note_count": 12,
    "pitch_range": [48, 72]
}
```

Then add this assertion after the existing `last_export_path()` assertion:

```rust
        assert!(
            view.render_lines()
                .iter()
                .any(|line| line == "Drag MIDI preview into DAW")
        );
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
cargo test -p atri-bridge-vst3 editor_surface_spec_marks_midi_preview_as_primary_drag_source bridge_plug_view_surface_tick_applies_latest_daw_agent_export
```

Expected: FAIL because `EditorSurfaceSpec::preview()` does not exist and the spec does not carry preview metadata.

- [ ] **Step 3: Carry preview through `EditorSurfaceSpec`**

In `atri-host/atri-bridge-vst3/src/editor_surface.rs`, update the import:

```rust
use crate::editor::{
    BridgeEditorAction, BridgeEditorButton, BridgeEditorPreview, BridgeEditorViewModel,
};
```

Add a preview field to `EditorSurfaceSpec`:

```rust
    preview: Option<BridgeEditorPreview>,
```

Set the field in `from_view_model()`:

```rust
            preview: view_model.preview().cloned(),
```

Add this getter:

```rust
    pub fn preview(&self) -> Option<&BridgeEditorPreview> {
        self.preview.as_ref()
    }
```

Update `drag_export_hit_test()` so preview comes first:

```rust
    pub fn drag_export_hit_test(&self, x: i32, y: i32) -> bool {
        if self.preview.as_ref().is_some_and(|preview| preview.contains(x, y)) {
            return self.hit_test(x, y).is_none();
        }
        let has_completed_export = self
            .lines
            .iter()
            .any(|line| line.starts_with("Last export:"));
        has_completed_export
            && self.hit_test(x, y).is_none()
            && x >= 24
            && x < self.rect.width.saturating_sub(24)
            && (68..100).contains(&y)
    }
```

- [ ] **Step 4: Draw native Windows preview**

In the Windows `paint()` function in `editor_surface.rs`, after drawing text lines and before drawing buttons, insert:

```rust
        if let Some(preview) = spec.preview() {
            paint_preview(hdc, preview);
        }
```

Add this helper in the Windows module near `paint()`:

```rust
    unsafe fn paint_preview(hdc: isize, preview: &BridgeEditorPreview) {
        let rect = RECT {
            left: preview.x,
            top: preview.y,
            right: preview.x + preview.width,
            bottom: preview.y + preview.height,
        };
        fill_rect(hdc, &rect, rgb(18, 22, 28));
        stroke_rect(hdc, &rect, rgb(92, 142, 218));

        let key_rect = RECT {
            left: preview.x + 8,
            top: preview.y + 8,
            right: preview.x + 54,
            bottom: preview.y + preview.height - 8,
        };
        fill_rect(hdc, &key_rect, rgb(34, 39, 47));
        for i in 0..4 {
            let y = key_rect.top + i * ((key_rect.bottom - key_rect.top) / 4);
            let row = RECT {
                left: key_rect.left,
                top: y,
                right: key_rect.right,
                bottom: y + 1,
            };
            fill_rect(hdc, &row, rgb(69, 78, 92));
        }

        let lane = RECT {
            left: preview.x + 64,
            top: preview.y + 11,
            right: preview.x + preview.width - 12,
            bottom: preview.y + preview.height - 11,
        };
        fill_rect(hdc, &lane, rgb(37, 63, 99));
        stroke_rect(hdc, &lane, rgb(112, 166, 242));

        unsafe {
            SetTextColor(hdc, rgb(244, 247, 250));
        }
        text_out(hdc, preview.x + 72, preview.y + 8, &preview.title);
        unsafe {
            SetTextColor(hdc, rgb(166, 178, 194));
        }
        text_out(hdc, preview.x + 72, preview.y + 25, &preview.detail);
    }
```

- [ ] **Step 5: Run Rust surface tests**

Run:

```powershell
cargo test -p atri-bridge-vst3 editor_surface_spec_marks_midi_preview_as_primary_drag_source bridge_plug_view_surface_tick_applies_latest_daw_agent_export
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add atri-host/atri-bridge-vst3/src/editor_surface.rs atri-host/atri-bridge-vst3/src/tests.rs atri-host/atri-bridge-vst3/src/factory.rs
git commit -m "Render draggable bridge MIDI preview"
```

---

### Task 6: Full Verification

**Files:**
- Verify all modified files.

- [ ] **Step 1: Format Rust**

Run:

```powershell
cargo fmt
```

Expected: command exits 0.

- [ ] **Step 2: Run focused frontend tests**

Run:

```powershell
node frontend\src\components\chat\midiArtifact.test.js
uv run pytest tests/test_daw_agent_frontend.py tests/test_dashboard_bridge_auth.py -q
```

Expected: PASS.

- [ ] **Step 3: Run Rust bridge tests**

Run:

```powershell
cargo test -p atri-bridge-vst3
```

Expected: PASS.

- [ ] **Step 4: Run project lint/build checks**

Run:

```powershell
npm.cmd run lint
npm.cmd run build
```

Expected: both commands exit 0.

- [ ] **Step 5: Inspect final diff**

Run:

```powershell
git diff --stat HEAD
git diff --check
```

Expected: `git diff --check` exits 0 and the diff only includes files from this plan.

- [ ] **Step 6: Commit verification fixes**

If formatting changed files in Step 1 or Step 4, run:

```powershell
git add atri-host/atri-bridge-vst3/src/bridge_contract.rs atri-host/atri-bridge-vst3/src/editor.rs atri-host/atri-bridge-vst3/src/editor_surface.rs atri-host/atri-bridge-vst3/src/factory.rs atri-host/atri-bridge-vst3/src/tests.rs frontend/src/components/chat/MidiArtifactCard.vue frontend/src/components/chat/midiArtifact.js frontend/src/components/chat/midiArtifact.test.js dashboard/music.py tests/test_dashboard_bridge_auth.py tests/test_daw_agent_frontend.py
git commit -m "Verify bridge mini preview flow"
```

Expected: commit is created only when there are verification or formatting changes not already committed by earlier tasks.

## Plan Self-Review

Spec coverage:

- Automatic bridge export: Task 1 and Task 2.
- Dashboard preview metadata: Task 3.
- Plugin preview parsing and state: Task 4.
- Native mini preview and drag target: Task 5.
- Manual fallback and existing route protections: Tasks 2, 3, and full regression tests in Task 6.

Placeholder scan:

- No deferred implementation steps.
- Every code-changing step includes concrete code.

Type consistency:

- `bridge_preview` is the dashboard JSON key.
- Rust uses `BridgeMidiPreview` for contract parsing and `BridgeEditorPreview` for view rendering.
- Frontend uses `bridgeAutoExportKeyForArtifact()` for duplicate suppression and existing `exportPayloadForMidiArtifact()` for payload construction.
