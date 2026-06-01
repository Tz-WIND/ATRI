use crate::bridge_contract::{
    BridgeContextPublishRequest, BridgeExportFormat, BridgeExportRequest, BridgeExportResponse,
    BridgeHostContext, BridgeStatus,
};
use crate::dashboard_client::{
    BridgeDashboardClient, DashboardClientError, DashboardEndpoint, DashboardExportWorker,
    DashboardStatusWorker,
};
use crate::daw_agent_surface::{DawAgentSurfaceParams, DawAgentWorkspace};
use crate::drag_drop::BridgeDragPayload;
use crate::editor::{
    BridgeConnectionState, BridgeEditorAction, BridgeEditorState, BridgeEditorViewModel,
    BridgeExportState,
};
use crate::editor_surface::{EditorPlatformType, EditorSurfaceSpec, SurfaceRect};
use crate::identity::{
    BridgePluginIdentity, COMPONENT_CLASS_ID, CONTROLLER_CLASS_ID, PLUGIN_CATEGORY, PLUGIN_NAME,
    VENDOR,
};
use crate::packaging::{
    BridgePackageLayout, BuildProfile, VST3_BUNDLE_NAME, package_from_target_dir,
};
use crate::processor::BridgeProcessorState;
use std::ffi::{CStr, c_char, c_void};
use std::fs;
use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::PathBuf;
use std::ptr;
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use vst3::{
    ComPtr,
    Steinberg::{
        FIDString, IPluginFactory2, IPluginFactory2Trait, IPluginFactory3, IPluginFactory3Trait,
        IPluginFactoryTrait, PClassInfo2, PClassInfoW,
        Vst::{
            IComponent, IComponent_iid, IEditController, IEditController_iid, ProcessContext,
            ProcessContext_,
        },
        kResultOk,
    },
};

#[test]
fn bridge_identity_constants_are_stable_for_host_scans() {
    let identity = BridgePluginIdentity::default();

    assert_eq!(PLUGIN_NAME, "ATRI Bridge");
    assert_eq!(VENDOR, "ATRI");
    assert_eq!(PLUGIN_CATEGORY, "Instrument");
    assert_eq!(identity.name, PLUGIN_NAME);
    assert_eq!(identity.vendor, VENDOR);
    assert_eq!(COMPONENT_CLASS_ID.len(), 16);
    assert_eq!(CONTROLLER_CLASS_ID.len(), 16);
    assert_ne!(COMPONENT_CLASS_ID, CONTROLLER_CLASS_ID);
}

#[test]
fn bridge_status_contract_deserializes_dashboard_response() {
    let json = r#"{
        "ok": true,
        "bridge": {
            "api_version": 1,
            "manifest_schema_version": 1,
            "local_only": true
        },
        "project": {
            "title": "ATRI Session",
            "revision": 7
        },
        "formats": ["midi", "dawproject"]
    }"#;

    let status: BridgeStatus = serde_json::from_str(json).unwrap();

    assert!(status.ok);
    assert_eq!(status.bridge.api_version, 1);
    assert_eq!(status.bridge.manifest_schema_version, 1);
    assert!(status.bridge.local_only);
    assert_eq!(status.project.title, "ATRI Session");
    assert_eq!(status.project.revision, "7");
    assert_eq!(status.formats, vec!["midi", "dawproject"]);
}

#[test]
fn bridge_status_contract_reads_formats_from_exports_object() {
    let json = r#"{
        "ok": true,
        "bridge": {
            "api_version": 1,
            "manifest_schema_version": 1,
            "local_only": true
        },
        "project": {
            "title": "ATRI Session",
            "revision": 7
        },
        "exports": {
            "formats": ["dawproject", "midi", "wav"],
            "hostless_formats": ["dawproject", "midi"],
            "host_required_formats": ["wav"]
        }
    }"#;

    let status: BridgeStatus = serde_json::from_str(json).unwrap();

    assert_eq!(status.formats, vec!["dawproject", "midi", "wav"]);
}

#[test]
fn bridge_status_contract_accepts_compat_formats_and_exports_object() {
    let json = r#"{
        "ok": true,
        "bridge": {
            "api_version": 1,
            "manifest_schema_version": 1,
            "local_only": true
        },
        "project": {
            "title": "ATRI Session",
            "revision": 7
        },
        "formats": ["midi", "dawproject"],
        "exports": {
            "formats": ["dawproject", "midi", "wav"],
            "hostless_formats": ["dawproject", "midi"],
            "host_required_formats": ["wav"]
        }
    }"#;

    let status: BridgeStatus = serde_json::from_str(json).unwrap();

    assert_eq!(status.formats, vec!["midi", "dawproject"]);
}

#[test]
fn bridge_status_contract_accepts_dashboard_revision_hash() {
    let json = r#"{
        "ok": true,
        "bridge": {
            "api_version": 1,
            "manifest_schema_version": 1,
            "local_only": true
        },
        "project": {
            "title": "ATRI Session",
            "revision": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        },
        "formats": ["midi", "dawproject"]
    }"#;

    let status: BridgeStatus = serde_json::from_str(json).unwrap();

    assert_eq!(
        status.project.revision.to_string(),
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    );
}

#[test]
fn bridge_export_request_always_serializes_bridge_consumer() {
    let request = BridgeExportRequest::new(BridgeExportFormat::Dawproject);
    let json = serde_json::to_value(&request).unwrap();

    assert_eq!(json["format"], "dawproject");
    assert_eq!(json["consumer"], "bridge");
    assert!(json.get("host_context").is_none());
}

#[test]
fn bridge_export_request_serializes_host_context_when_available() {
    let request = BridgeExportRequest::new(BridgeExportFormat::Dawproject).with_host_context(
        BridgeHostContext {
            sample_rate: Some(48_000.0),
            block_size: Some(256),
            is_playing: Some(true),
            tempo_bpm: Some(93.5),
            time_signature: Some([7, 8]),
            ..BridgeHostContext::default()
        },
    );
    let json = serde_json::to_value(&request).unwrap();

    assert_eq!(json["format"], "dawproject");
    assert_eq!(json["consumer"], "bridge");
    assert_eq!(json["host_context"]["sample_rate"], 48_000.0);
    assert_eq!(json["host_context"]["block_size"], 256);
    assert_eq!(json["host_context"]["is_playing"], true);
    assert_eq!(json["host_context"]["tempo_bpm"], 93.5);
    assert_eq!(
        json["host_context"]["time_signature"],
        serde_json::json!([7, 8])
    );
}

#[test]
fn bridge_export_request_serializes_instance_id_when_available() {
    let request =
        BridgeExportRequest::new(BridgeExportFormat::Midi).with_instance_id("bridge 1/left");
    let json = serde_json::to_value(&request).unwrap();

    assert_eq!(json["format"], "midi");
    assert_eq!(json["consumer"], "bridge");
    assert_eq!(json["instance_id"], "bridge 1/left");
}

#[test]
fn bridge_context_publish_request_serializes_instance_host_and_context() {
    let request = BridgeContextPublishRequest::new(
        "bridge 1/left",
        BridgeHostContext {
            sample_rate: Some(48_000.0),
            block_size: Some(256),
            is_playing: Some(true),
            tempo_bpm: Some(128.0),
            time_signature: Some([7, 8]),
            ..BridgeHostContext::default()
        },
    )
    .with_host_name("REAPER");
    let json = serde_json::to_value(&request).unwrap();

    assert_eq!(json["instance_id"], "bridge 1/left");
    assert_eq!(json["host"], "REAPER");
    assert_eq!(json["host_context"]["sample_rate"], 48_000.0);
    assert_eq!(json["host_context"]["block_size"], 256);
    assert_eq!(json["host_context"]["is_playing"], true);
    assert_eq!(json["host_context"]["tempo_bpm"], 128.0);
    assert_eq!(
        json["host_context"]["time_signature"],
        serde_json::json!([7, 8])
    );
}

#[test]
fn dashboard_endpoint_defaults_to_local_bridge_routes() {
    let endpoint = DashboardEndpoint::default();

    assert_eq!(endpoint.base_url(), "http://127.0.0.1:6185");
    assert_eq!(
        endpoint.bridge_status_url(),
        "http://127.0.0.1:6185/api/music/studio/bridge/status"
    );
    assert_eq!(
        endpoint.bridge_export_url(),
        "http://127.0.0.1:6185/api/music/studio/bridge/export"
    );
    assert_eq!(
        endpoint.bridge_latest_export_url(None),
        "http://127.0.0.1:6185/api/music/studio/bridge/export/latest"
    );
    assert_eq!(
        endpoint.bridge_latest_export_url(Some("bridge 1/left")),
        "http://127.0.0.1:6185/api/music/studio/bridge/export/latest?instance_id=bridge%201%2Fleft"
    );
    assert_eq!(
        endpoint.bridge_context_url(),
        "http://127.0.0.1:6185/api/music/studio/bridge/context"
    );
}

#[test]
fn dashboard_endpoint_builds_daw_agent_surface_url() {
    let endpoint = DashboardEndpoint::new("http://127.0.0.1:6185/").unwrap();
    let params =
        DawAgentSurfaceParams::from_project(Some("ATRI Session"), Some("7"), "bridge-instance-3")
            .with_workspace(DawAgentWorkspace::AtriStudio)
            .with_host_name("Studio One");

    assert_eq!(params.project_session_id(), "atri-session");
    assert_eq!(
        endpoint.daw_agent_surface_url(&params),
        "http://127.0.0.1:6185/?surface=daw-agent&project_session_id=atri-session&instance_id=bridge-instance-3&workspace=atri_studio&host=Studio%20One"
    );
}

#[test]
fn daw_agent_surface_url_encodes_query_values_and_uses_revision_fallback() {
    let endpoint = DashboardEndpoint::new("http://localhost:7000").unwrap();
    let params = DawAgentSurfaceParams::from_project(None, Some("11"), "bridge 1/left")
        .with_workspace(DawAgentWorkspace::HostProject)
        .with_host_name("Studio One 6");

    assert_eq!(params.project_session_id(), "atri-project-r11");
    assert_eq!(
        endpoint.daw_agent_surface_url(&params),
        "http://localhost:7000/?surface=daw-agent&project_session_id=atri-project-r11&instance_id=bridge%201%2Fleft&workspace=host_project&host=Studio%20One%206"
    );
}

#[test]
fn dashboard_client_fetches_bridge_status_from_local_dashboard() {
    let endpoint = spawn_bridge_status_server();
    let client = BridgeDashboardClient::new(endpoint);

    let status = client.fetch_status(Duration::from_secs(1)).unwrap();

    assert!(status.ok);
    assert_eq!(status.project.title, "ATRI Session");
    assert_eq!(status.project.revision, "11");
}

#[test]
fn dashboard_status_worker_updates_editor_state_from_background_result() {
    let endpoint = spawn_bridge_status_server();
    let client = BridgeDashboardClient::new(endpoint);
    let worker = DashboardStatusWorker::new(client, Duration::from_secs(1));
    let mut state = BridgeEditorState::default();

    state.mark_connecting();
    let result = worker.check_once().join().unwrap().unwrap();
    state.apply_status(result);

    assert_eq!(state.connection(), BridgeConnectionState::Connected);
    assert_eq!(state.project_title(), Some("ATRI Session"));
    assert_eq!(state.project_revision(), Some("11"));
}

#[test]
fn dashboard_client_posts_bridge_export_request_to_local_dashboard() {
    let endpoint = spawn_bridge_export_server(
        r#"{
            "ok": true,
            "bridge": {
                "api_version": 1,
                "manifest_schema_version": 1,
                "local_only": true
            },
            "export": {
                "format": "dawproject",
                "path": "data/music_workstation/exports/session.dawproject"
            }
        }"#,
        200,
    );
    let client = BridgeDashboardClient::new(endpoint);

    let response = client
        .request_export(
            BridgeExportRequest::new(BridgeExportFormat::Dawproject),
            Duration::from_secs(1),
        )
        .unwrap();

    assert!(response.ok);
    assert_eq!(
        response.export_path(),
        Some("data/music_workstation/exports/session.dawproject")
    );
}

#[test]
fn dashboard_client_posts_bridge_context_update_to_local_dashboard() {
    let endpoint = spawn_bridge_context_server(
        r#"{
            "ok": true,
            "bridge": {
                "api_version": 1,
                "manifest_schema_version": 1,
                "local_only": true
            },
            "context": {
                "host": "REAPER",
                "tempo_bpm": 128
            }
        }"#,
        200,
    );
    let client = BridgeDashboardClient::new(endpoint);

    let response = client
        .publish_context(
            BridgeContextPublishRequest::new(
                "bridge-context",
                BridgeHostContext {
                    sample_rate: Some(48_000.0),
                    block_size: Some(256),
                    is_playing: Some(true),
                    tempo_bpm: Some(128.0),
                    time_signature: Some([7, 8]),
                    ..BridgeHostContext::default()
                },
            )
            .with_host_name("REAPER"),
            Duration::from_secs(1),
        )
        .unwrap();

    assert!(response.ok);
}

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
                    "note_count": 20,
                    "pitch_range": [36, 72],
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
                }
            }
        }"#,
    )
    .unwrap();

    let preview = response.midi_preview().unwrap();
    assert_eq!(preview.track_name, "Edited Synth");
    assert_eq!(preview.note_count, 20);
    assert_eq!(preview.pitch_range, [36, 72]);
    assert_eq!(preview.tracks.len(), 2);
    assert_eq!(preview.tracks[0].track_name, "Edited Synth");
    assert_eq!(preview.tracks[1].track_name, "Bass");
}

#[test]
fn bridge_export_response_reads_bridge_scope_instance_id() {
    let response: BridgeExportResponse = serde_json::from_str(
        r#"{
            "ok": true,
            "export": {
                "format": "midi",
                "path": "data/music_workstation/exports/region.mid",
                "bridge_scope": {"instance_id": "bridge-expected"}
            }
        }"#,
    )
    .unwrap();

    assert_eq!(response.bridge_scope_instance_id(), Some("bridge-expected"));
}

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
    assert_eq!(
        state.last_midi_preview().unwrap().track_name,
        "Edited Synth"
    );
    assert_eq!(state.last_midi_preview().unwrap().beat_range, [4.0, 8.0]);
}

#[test]
fn editor_state_clears_midi_preview_when_export_response_fails() {
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

    state.apply_export_response(BridgeExportResponse {
        ok: false,
        bridge: None,
        export: None,
    });

    assert_eq!(state.export_state(), BridgeExportState::Error);
    assert_eq!(state.last_midi_preview(), None);

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

    assert!(view.preview().is_none());
    assert!(
        !view
            .render_lines()
            .iter()
            .any(|line| line == "Drag MIDI preview into DAW")
    );
    assert!(!spec.drag_export_hit_test(48, 84));
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
    assert_eq!(
        preview.detail,
        "4.00-8.00 beat | 1 track | 12 notes | C3-C5"
    );
    assert_eq!(preview.track_rows()[0].title, "Edited Synth");
    assert!(
        view.render_lines()
            .iter()
            .any(|line| line == "Drag MIDI preview into DAW")
    );
}

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

#[test]
fn dashboard_client_reports_bridge_export_http_errors() {
    let endpoint = spawn_bridge_export_server(
        r#"{
            "ok": false,
            "error": "host is required for wav export"
        }"#,
        400,
    );
    let client = BridgeDashboardClient::new(endpoint);

    let err = client
        .request_export(
            BridgeExportRequest::new(BridgeExportFormat::Wav),
            Duration::from_secs(1),
        )
        .unwrap_err();

    assert!(matches!(
        err,
        DashboardClientError::HttpStatus { status: 400, .. }
    ));
}

#[test]
fn dashboard_client_preserves_bridge_export_error_message() {
    let endpoint = spawn_bridge_export_server(
        r#"{
            "ok": false,
            "error": "host is required for wav export"
        }"#,
        400,
    );
    let client = BridgeDashboardClient::new(endpoint);

    let err = client
        .request_export(
            BridgeExportRequest::new(BridgeExportFormat::Wav),
            Duration::from_secs(1),
        )
        .unwrap_err();

    assert_eq!(err.user_message(), "host is required for wav export");
}

#[test]
fn dashboard_export_worker_runs_export_request_in_background() {
    let endpoint = spawn_bridge_export_server(
        r#"{
            "ok": true,
            "bridge": {
                "api_version": 1,
                "manifest_schema_version": 1,
                "local_only": true
            },
            "export": {
                "format": "midi",
                "path": "data/music_workstation/exports/session.mid"
            }
        }"#,
        200,
    );
    let client = BridgeDashboardClient::new(endpoint);
    let worker = DashboardExportWorker::new(client, Duration::from_secs(1));

    let response = worker
        .export_once(BridgeExportRequest::new(BridgeExportFormat::Midi))
        .join()
        .unwrap()
        .unwrap();

    assert_eq!(
        response.export_path(),
        Some("data/music_workstation/exports/session.mid")
    );
}

#[test]
fn editor_state_records_dashboard_connection_without_export_io() {
    let mut state = BridgeEditorState::default();

    assert_eq!(state.connection(), BridgeConnectionState::Disconnected);

    state.apply_status(BridgeStatus::connected_for_test("ATRI Session", 3));

    assert_eq!(state.connection(), BridgeConnectionState::Connected);
    assert_eq!(state.project_title(), Some("ATRI Session"));
    assert_eq!(state.project_revision(), Some("3"));
}

#[test]
fn editor_state_builds_daw_agent_surface_url_from_project_status() {
    let _guard = crate::host_context::test_host_context_guard();
    crate::host_context::clear_latest_host_application_name_for_test();
    let endpoint = DashboardEndpoint::new("http://127.0.0.1:7001").unwrap();
    let mut state = BridgeEditorState::from(endpoint);
    state.apply_status(BridgeStatus::connected_for_test("ATRI Session", 7));

    assert_eq!(
        state.daw_agent_surface_url("bridge-instance-3"),
        "http://127.0.0.1:7001/?surface=daw-agent&project_session_id=atri-session&instance_id=bridge-instance-3&workspace=atri_studio&host=Studio%20One"
    );
    crate::host_context::clear_latest_host_application_name_for_test();
}

#[test]
fn editor_state_daw_agent_surface_url_uses_published_host_application_name() {
    let _guard = crate::host_context::test_host_context_guard();
    crate::host_context::clear_latest_host_application_name_for_test();
    crate::host_context::publish_host_application_name("REAPER");

    let endpoint = DashboardEndpoint::new("http://127.0.0.1:7001").unwrap();
    let mut state = BridgeEditorState::from(endpoint);
    state.apply_status(BridgeStatus::connected_for_test("ATRI Session", 7));

    assert_eq!(
        state.daw_agent_surface_url("bridge-instance-3"),
        "http://127.0.0.1:7001/?surface=daw-agent&project_session_id=atri-session&instance_id=bridge-instance-3&workspace=atri_studio&host=REAPER"
    );

    crate::host_context::clear_latest_host_application_name_for_test();
}

#[test]
fn editor_state_open_atri_url_is_legacy_dashboard_base_url() {
    let endpoint = DashboardEndpoint::new("http://127.0.0.1:7001").unwrap();
    let mut state = BridgeEditorState::from(endpoint);
    state.apply_status(BridgeStatus::connected_for_test("ATRI Session", 7));

    #[allow(deprecated)]
    let legacy_url = state.open_atri_url();
    assert_eq!(legacy_url, "http://127.0.0.1:7001");
    assert_ne!(state.daw_agent_surface_url("bridge-instance-3"), legacy_url);
}

#[test]
fn editor_state_tracks_export_progress_and_result_path() {
    let mut state = BridgeEditorState::default();

    state.begin_export(BridgeExportFormat::Dawproject);
    assert_eq!(state.export_state(), BridgeExportState::InProgress);
    assert_eq!(
        state.pending_export_format(),
        Some(BridgeExportFormat::Dawproject)
    );

    state.apply_export_response(BridgeExportResponse {
        ok: true,
        bridge: None,
        export: Some(serde_json::json!({
            "format": "dawproject",
            "path": "data/music_workstation/exports/session.dawproject"
        })),
    });

    assert_eq!(state.export_state(), BridgeExportState::Completed);
    assert_eq!(
        state.last_export_path(),
        Some("data/music_workstation/exports/session.dawproject")
    );
    assert_eq!(state.pending_export_format(), None);
}

#[test]
fn editor_state_applies_external_export_response_while_export_in_progress() {
    let mut state = BridgeEditorState::default();

    state.begin_export(BridgeExportFormat::Dawproject);
    let changed = state.apply_external_export_response(BridgeExportResponse {
        ok: true,
        bridge: None,
        export: Some(serde_json::json!({
            "format": "midi",
            "path": "data/music_workstation/exports/daw-agent-region.mid"
        })),
    });

    assert!(changed);
    assert_eq!(state.export_state(), BridgeExportState::Completed);
    assert_eq!(
        state.last_export_path(),
        Some("data/music_workstation/exports/daw-agent-region.mid")
    );
    assert_eq!(state.pending_export_format(), None);
}

#[test]
fn editor_action_starts_export_and_builds_bridge_request() {
    let mut state = BridgeEditorState::default();

    let request = state
        .handle_action(BridgeEditorAction::ExportMidi)
        .expect("export action should create a request");

    assert_eq!(request.format, BridgeExportFormat::Midi);
    assert_eq!(request.consumer, "bridge");
    assert_eq!(state.export_state(), BridgeExportState::InProgress);
    assert_eq!(
        state.pending_export_format(),
        Some(BridgeExportFormat::Midi)
    );
}

#[test]
fn editor_action_includes_latest_host_context_in_export_request() {
    let mut state = BridgeEditorState::default();
    let host_context = BridgeHostContext {
        sample_rate: Some(44_100.0),
        block_size: Some(512),
        is_playing: Some(false),
        tempo_bpm: Some(101.0),
        time_signature: Some([5, 4]),
        ..BridgeHostContext::default()
    };

    state.apply_host_context(host_context.clone());
    let request = state
        .handle_action(BridgeEditorAction::ExportDawproject)
        .expect("export action should create a request");

    assert_eq!(request.host_context, Some(host_context));
}

#[test]
fn editor_actions_cover_supported_phase_five_export_formats() {
    assert_eq!(
        BridgeEditorAction::ExportDawproject.export_format(),
        Some(BridgeExportFormat::Dawproject)
    );
    assert_eq!(
        BridgeEditorAction::ExportMixdownWav.export_format(),
        Some(BridgeExportFormat::Wav)
    );
    assert_eq!(
        BridgeEditorAction::ExportStems.export_format(),
        Some(BridgeExportFormat::Stems)
    );
    assert_eq!(BridgeEditorAction::OpenAtri.export_format(), None);
}

#[test]
fn editor_view_model_renders_connection_export_and_action_labels() {
    let mut state = BridgeEditorState::default();
    state.apply_status(BridgeStatus::connected_for_test("ATRI Session", 3));
    state.begin_export(BridgeExportFormat::Midi);

    let view = BridgeEditorViewModel::from_state(&state, 420, 220);
    let lines = view.render_lines();

    assert!(lines.iter().any(|line| line == "ATRI Bridge"));
    assert!(lines.iter().any(|line| line.contains("Connected")));
    assert!(lines.iter().any(|line| line.contains("ATRI Session")));
    assert!(lines.iter().any(|line| line.contains("Exporting midi")));
    assert_eq!(
        view.button_labels(),
        vec!["Open ATRI", "MIDI", "DAWproject", "WAV", "Stems"]
    );
}

#[test]
fn editor_view_model_renders_host_context_when_available() {
    let mut state = BridgeEditorState::default();
    state.apply_host_context(BridgeHostContext {
        sample_rate: Some(44_100.0),
        block_size: Some(512),
        is_playing: Some(false),
        tempo_bpm: Some(101.0),
        time_signature: Some([5, 4]),
        ..BridgeHostContext::default()
    });

    let view = BridgeEditorViewModel::from_state(&state, 420, 220);
    let lines = view.render_lines();

    assert!(
        lines
            .iter()
            .any(|line| { line == "Host: 101.0 BPM 5/4 stopped @ 44100 Hz" })
    );
}

#[test]
fn editor_view_model_hit_tests_export_buttons_to_actions() {
    let state = BridgeEditorState::default();
    let view = BridgeEditorViewModel::from_state(&state, 420, 220);

    assert_eq!(view.hit_test(28, 138), Some(BridgeEditorAction::OpenAtri));
    assert_eq!(
        view.hit_test(116, 138),
        Some(BridgeEditorAction::ExportMidi)
    );
    assert_eq!(
        view.hit_test(206, 138),
        Some(BridgeEditorAction::ExportDawproject)
    );
    assert_eq!(
        view.hit_test(316, 138),
        Some(BridgeEditorAction::ExportMixdownWav)
    );
    assert_eq!(
        view.hit_test(380, 138),
        Some(BridgeEditorAction::ExportStems)
    );
    assert_eq!(view.hit_test(10, 10), None);
}

#[test]
fn editor_surface_spec_captures_parent_bounds_text_and_buttons() {
    let mut state = BridgeEditorState::default();
    state.apply_status(BridgeStatus::connected_for_test("ATRI Session", 5));
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

    assert_eq!(spec.parent_handle(), 0x1234);
    assert_eq!(spec.platform(), EditorPlatformType::WindowsHwnd);
    assert_eq!(spec.rect().width, 640);
    assert_eq!(spec.rect().height, 320);
    assert!(
        spec.lines()
            .iter()
            .any(|line| line.contains("ATRI Session"))
    );
    assert_eq!(
        spec.hit_test(206, 138),
        Some(BridgeEditorAction::ExportDawproject)
    );
}

#[test]
fn editor_surface_spec_marks_completed_export_line_as_drag_source() {
    let mut state = BridgeEditorState::default();
    state.apply_export_response(BridgeExportResponse {
        ok: true,
        bridge: None,
        export: Some(serde_json::json!({
            "format": "midi",
            "path": "data/music_workstation/exports/session.mid"
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

    assert!(spec.drag_export_hit_test(48, 82));
    assert!(!spec.drag_export_hit_test(116, 138));
}

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

#[test]
fn drag_payload_uses_last_export_path_as_single_file() {
    let payload =
        BridgeDragPayload::from_export_path("data/music_workstation/exports/session.dawproject")
            .unwrap();

    assert_eq!(payload.files().len(), 1);
    assert_eq!(
        payload.files()[0].to_string_lossy(),
        "data/music_workstation/exports/session.dawproject"
    );
    assert!(payload.metadata_json().is_none());
}

#[test]
fn drag_payload_from_export_carries_primary_file_and_compact_metadata() {
    let export = serde_json::json!({
        "id": "export123",
        "format": "midi",
        "path": "data/music_workstation/exports/session.mid",
        "filename": "session.mid",
        "bridge_scope": {"instance_id": "bridge-drag"},
        "bridge_export": {"range_source": "selection"}
    });

    let payload = BridgeDragPayload::from_export(&export).unwrap();

    assert_eq!(
        payload.files()[0].to_string_lossy(),
        "data/music_workstation/exports/session.mid"
    );
    assert_eq!(
        payload.metadata_json(),
        Some(
            r#"{"bridge_export":{"range_source":"selection"},"bridge_scope":{"instance_id":"bridge-drag"},"filename":"session.mid","format":"midi","id":"export123","path":"data/music_workstation/exports/session.mid"}"#
        )
    );
}

#[test]
fn editor_state_records_export_error_message() {
    let mut state = BridgeEditorState::default();

    state.begin_export(BridgeExportFormat::Midi);
    state.mark_export_error("dashboard unavailable");

    assert_eq!(state.export_state(), BridgeExportState::Error);
    assert_eq!(state.last_export_error(), Some("dashboard unavailable"));
    assert_eq!(state.pending_export_format(), None);
}

#[test]
fn editor_state_records_dashboard_export_error_message() {
    let mut state = BridgeEditorState::default();

    state.begin_export(BridgeExportFormat::Wav);
    state.apply_export_error(DashboardClientError::HttpStatus {
        status: 400,
        message: Some("host is required for wav export".to_string()),
    });

    assert_eq!(state.export_state(), BridgeExportState::Error);
    assert_eq!(
        state.last_export_error(),
        Some("host is required for wav export")
    );
}

#[test]
fn processor_state_is_send_sync_and_does_not_start_dashboard_io() {
    fn assert_send_sync<T: Send + Sync>() {}
    assert_send_sync::<BridgeProcessorState>();

    let mut processor = BridgeProcessorState::default();
    processor.prepare(48_000.0, 256);
    processor.set_transport(true, 128.0, 4, 4);

    assert_eq!(processor.sample_rate(), 48_000.0);
    assert_eq!(processor.block_size(), 256);
    assert!(processor.transport().is_playing);
    assert!(!processor.can_perform_dashboard_io());
}

#[test]
fn processor_state_captures_valid_vst3_host_context() {
    let mut processor = BridgeProcessorState::default();
    processor.prepare(44_100.0, 512);
    let mut context = unsafe { std::mem::zeroed::<ProcessContext>() };
    context.state = (ProcessContext_::StatesAndFlags_::kPlaying
        | ProcessContext_::StatesAndFlags_::kTempoValid
        | ProcessContext_::StatesAndFlags_::kTimeSigValid) as u32;
    context.sampleRate = 48_000.0;
    context.tempo = 93.5;
    context.projectTimeMusic = 12.5;
    context.barPositionMusic = 8.0;
    context.cycleStartMusic = 4.0;
    context.cycleEndMusic = 8.0;
    context.timeSigNumerator = 7;
    context.timeSigDenominator = 8;
    context.state |= (ProcessContext_::StatesAndFlags_::kProjectTimeMusicValid
        | ProcessContext_::StatesAndFlags_::kBarPositionValid
        | ProcessContext_::StatesAndFlags_::kCycleValid
        | ProcessContext_::StatesAndFlags_::kCycleActive) as u32;

    processor.apply_process_context(&context);

    assert_eq!(processor.sample_rate(), 48_000.0);
    assert_eq!(processor.transport().is_playing, true);
    assert_eq!(processor.transport().tempo_bpm, 93.5);
    assert_eq!(processor.transport().meter_numerator, 7);
    assert_eq!(processor.transport().meter_denominator, 8);
    assert_eq!(
        processor.host_context(),
        Some(BridgeHostContext {
            sample_rate: Some(48_000.0),
            block_size: Some(512),
            is_playing: Some(true),
            tempo_bpm: Some(93.5),
            time_signature: Some([7, 8]),
            project_time_beats: Some(12.5),
            bar_position_beats: Some(8.0),
            loop_active: Some(true),
            loop_range_beats: Some([4.0, 8.0]),
            selection: None,
        })
    );
}

#[test]
fn exported_vst3_factory_exposes_component_and_controller_classes() {
    let factory = crate::GetPluginFactory();

    assert!(!factory.is_null());

    let factory = unsafe { vst3::ComPtr::from_raw(factory) }.unwrap();
    assert_eq!(unsafe { factory.countClasses() }, 2);
}

#[test]
fn exported_vst3_factory_exposes_extended_factory3_metadata() {
    let factory = unsafe { ComPtr::from_raw(crate::GetPluginFactory()) }.unwrap();
    let factory2 = factory
        .cast::<IPluginFactory2>()
        .expect("factory should expose IPluginFactory2");
    let factory3 = factory
        .cast::<IPluginFactory3>()
        .expect("factory should expose IPluginFactory3");
    let mut info2 = unsafe { std::mem::zeroed::<PClassInfo2>() };
    let mut info_w = unsafe { std::mem::zeroed::<PClassInfoW>() };

    let result2 = unsafe { factory2.getClassInfo2(0, &mut info2) };
    let result_w = unsafe { factory3.getClassInfoUnicode(0, &mut info_w) };

    assert_eq!(result2, kResultOk);
    assert_eq!(result_w, kResultOk);
    assert_eq!(fixed_cstr(&info2.name), PLUGIN_NAME);
    assert_eq!(fixed_cstr(&info2.vendor), VENDOR);
    assert!(!fixed_cstr(&info2.version).is_empty());
    assert!(fixed_cstr(&info2.subCategories).contains(PLUGIN_CATEGORY));
    assert_eq!(fixed_wstr(&info_w.name), PLUGIN_NAME);
    assert_eq!(fixed_wstr(&info_w.vendor), VENDOR);
    assert!(!fixed_wstr(&info_w.version).is_empty());
    assert!(fixed_cstr(&info_w.subCategories).contains(PLUGIN_CATEGORY));
}

#[test]
fn exported_vst3_factory_creates_component_and_controller_instances() {
    let factory = unsafe { vst3::ComPtr::from_raw(crate::GetPluginFactory()) }.unwrap();

    let mut component: *mut c_void = ptr::null_mut();
    let component_result = unsafe {
        factory.createInstance(
            COMPONENT_CLASS_ID.as_ptr() as FIDString,
            IComponent_iid.as_ptr() as FIDString,
            &mut component,
        )
    };

    let mut controller: *mut c_void = ptr::null_mut();
    let controller_result = unsafe {
        factory.createInstance(
            CONTROLLER_CLASS_ID.as_ptr() as FIDString,
            IEditController_iid.as_ptr() as FIDString,
            &mut controller,
        )
    };

    assert_eq!(component_result, kResultOk);
    assert!(!component.is_null());
    assert_eq!(controller_result, kResultOk);
    assert!(!controller.is_null());

    let _component = unsafe { vst3::ComPtr::from_raw(component.cast::<IComponent>()) }.unwrap();
    let _controller =
        unsafe { vst3::ComPtr::from_raw(controller.cast::<IEditController>()) }.unwrap();
}

#[test]
fn package_layout_names_atri_bridge_vst3_bundle() {
    let layout = BridgePackageLayout::for_current_target();

    assert_eq!(VST3_BUNDLE_NAME, "ATRI Bridge.vst3");
    assert!(layout.bundle_root.ends_with(VST3_BUNDLE_NAME));
    assert!(layout.binary_path.starts_with(&layout.bundle_root));
    assert!(layout.binary_path.to_string_lossy().contains("Contents"));
}

#[test]
fn package_layout_materializes_bundle_from_compiled_binary() {
    let temp_dir = unique_temp_dir("atri-bridge-package");
    fs::create_dir_all(&temp_dir).unwrap();
    let source = temp_dir.join("atri_bridge_vst3.dll");
    fs::write(&source, b"compiled bridge binary").unwrap();
    let layout = BridgePackageLayout::for_root(temp_dir.join("dist"));

    let copied = layout.materialize_from_binary(&source).unwrap();

    assert_eq!(copied, layout.binary_path);
    assert_eq!(
        fs::read(&layout.binary_path).unwrap(),
        b"compiled bridge binary"
    );
    let module_info = fs::read_to_string(layout.moduleinfo_path).unwrap();
    assert!(module_info.contains("\"Name\":\"ATRI Bridge\""));
    assert!(module_info.contains("\"Classes\""));

    let _ = fs::remove_dir_all(temp_dir);
}

#[test]
fn package_from_target_dir_uses_profile_binary_and_output_root() {
    let temp_dir = unique_temp_dir("atri-bridge-target-package");
    let target_dir = temp_dir.join("target");
    let source_dir = target_dir.join("debug");
    fs::create_dir_all(&source_dir).unwrap();
    let source = source_dir.join(crate::packaging::compiled_cdylib_name());
    fs::write(&source, b"debug bridge binary").unwrap();

    let bundle =
        package_from_target_dir(&target_dir, BuildProfile::Debug, temp_dir.join("dist")).unwrap();

    assert!(bundle.ends_with(VST3_BUNDLE_NAME));
    assert_eq!(
        fs::read(
            bundle
                .join("Contents")
                .join(crate::packaging::vst3_platform_dir())
                .join(crate::packaging::vst3_binary_name())
        )
        .unwrap(),
        b"debug bridge binary"
    );

    let _ = fs::remove_dir_all(temp_dir);
}

fn spawn_bridge_status_server() -> DashboardEndpoint {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = [0_u8; 2048];
        let bytes = stream.read(&mut request).unwrap();
        let request = String::from_utf8_lossy(&request[..bytes]);
        assert!(request.starts_with("GET /api/music/studio/bridge/status "));

        let body = r#"{
            "ok": true,
            "bridge": {
                "api_version": 1,
                "manifest_schema_version": 1,
                "local_only": true
            },
            "project": {
                "title": "ATRI Session",
                "revision": 11
            },
            "formats": ["midi", "dawproject"]
        }"#;
        let response = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        );
        stream.write_all(response.as_bytes()).unwrap();
    });
    DashboardEndpoint::new(format!("http://127.0.0.1:{port}")).unwrap()
}

fn spawn_bridge_export_server(body: &'static str, status: u16) -> DashboardEndpoint {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = [0_u8; 4096];
        let bytes = stream.read(&mut request).unwrap();
        let request = String::from_utf8_lossy(&request[..bytes]);
        assert!(request.starts_with("POST /api/music/studio/bridge/export "));
        assert!(request.contains("Content-Type: application/json"));
        assert!(request.contains("\"consumer\":\"bridge\""));

        let response = format!(
            "HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        );
        stream.write_all(response.as_bytes()).unwrap();
    });
    DashboardEndpoint::new(format!("http://127.0.0.1:{port}")).unwrap()
}

fn spawn_bridge_context_server(body: &'static str, status: u16) -> DashboardEndpoint {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    thread::spawn(move || {
        let (mut stream, _) = listener.accept().unwrap();
        let mut request = [0_u8; 4096];
        let bytes = stream.read(&mut request).unwrap();
        let request = String::from_utf8_lossy(&request[..bytes]);
        assert!(request.starts_with("POST /api/music/studio/bridge/context "));
        assert!(request.contains("Content-Type: application/json"));
        assert!(request.contains("\"instance_id\":\"bridge-context\""));
        assert!(request.contains("\"host\":\"REAPER\""));
        assert!(request.contains("\"tempo_bpm\":128.0"));

        let response = format!(
            "HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        );
        stream.write_all(response.as_bytes()).unwrap();
    });
    DashboardEndpoint::new(format!("http://127.0.0.1:{port}")).unwrap()
}

fn unique_temp_dir(name: &str) -> PathBuf {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    std::env::temp_dir().join(format!("{name}-{}-{nanos}", std::process::id()))
}

fn fixed_cstr(bytes: &[c_char]) -> String {
    let ptr = bytes.as_ptr();
    unsafe { CStr::from_ptr(ptr) }
        .to_string_lossy()
        .into_owned()
}

fn fixed_wstr(units: &[vst3::Steinberg::char16]) -> String {
    let len = units
        .iter()
        .position(|unit| *unit == 0)
        .unwrap_or(units.len());
    String::from_utf16_lossy(&units[..len])
}
