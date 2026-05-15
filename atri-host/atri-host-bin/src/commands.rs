use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use atri_core::midi::note::MidiNote;
use atri_engine::engine::AudioEngine;
use atri_engine::plugin_proc::PluginInsert;
use atri_engine::processor::Processor;
use atri_engine::session::Session;
use atri_engine::synth::BasicSynth;
use atri_vst3::factory::PluginFactory;
use atri_vst3::plugin::Vst3Plugin;
use atri_vst3::scanner::{PluginScanner, vst3_bundle_library_path};
use base64::{Engine as _, engine::general_purpose::STANDARD as BASE64};
use crossbeam::channel::Sender;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::config::HostConfig;
use crate::editor_host::{EditorKey, EditorWindowManager};
use crate::stream::AudioStreamer;

#[derive(Debug, Clone)]
pub enum AppCommand {
    Play,
    Stop,
    Pause,
    Seek(i64),
    SetLoop { start: i64, end: i64 },
    ClearLoop,
    SetTempo { bpm: f64, time_sig: (u8, u8) },
    Shutdown,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "snake_case", tag = "type")]
pub enum CommandResponse {
    Ack {
        cmd: String,
        status: String,
        data: Option<Value>,
    },
    Error {
        cmd: Option<String>,
        message: String,
    },
    Status {
        transport: String,
        position: f64,
        tempo: f64,
        meter: (u8, u8),
        sample_rate: u32,
        buffer_size: usize,
        tracks: Vec<TrackStatus>,
    },
    Shutdown {
        status: String,
    },
}

#[derive(Debug, Serialize)]
pub struct TrackStatus {
    id: u32,
    name: String,
    volume: f32,
    pan: f32,
    mute: bool,
    solo: bool,
    note_count: usize,
    processors: Vec<String>,
    processor_slots: Vec<Option<String>>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case", tag = "cmd")]
pub enum Command {
    Play,
    Stop,
    Pause,
    Seek {
        position: f64,
    },
    AddTrack {
        name: String,
    },
    RemoveTrack {
        id: u32,
    },
    SetMidi {
        track_id: u32,
        notes: Vec<MidiNoteData>,
    },
    SetTempo {
        bpm: f64,
        time_sig: Option<(u8, u8)>,
    },
    SetLoop {
        start: f64,
        end: f64,
    },
    ClearLoop,
    SetVolume {
        track_id: u32,
        value: f64,
    },
    SetPan {
        track_id: u32,
        value: f64,
    },
    SetMute {
        track_id: u32,
        value: bool,
    },
    SetSolo {
        track_id: u32,
        value: bool,
    },
    LoadBuiltinSynth {
        track_id: u32,
        slot_index: Option<u8>,
    },
    LoadVst3 {
        track_id: u32,
        path: String,
        name: Option<String>,
        slot_index: Option<u8>,
    },
    ClearProcessorSlot {
        track_id: u32,
        slot_index: u8,
    },
    OpenPluginEditor {
        track_id: u32,
        slot_index: Option<u8>,
    },
    GetPluginState {
        track_id: u32,
        slot_index: Option<u8>,
    },
    SetPluginState {
        track_id: u32,
        slot_index: Option<u8>,
        state_b64: String,
    },
    ScanPlugins {
        paths: Option<Vec<String>>,
        vst2_paths: Option<Vec<String>>,
    },
    SetStreaming {
        enabled: bool,
    },
    GetStatus,
    Shutdown,
}

#[derive(Debug, Deserialize)]
pub struct MidiNoteData {
    pub pitch: u8,
    pub start: f64,
    pub duration: f64,
    pub velocity: u8,
}

pub fn handle_command(
    raw: &Value,
    engine: &Arc<Mutex<AudioEngine>>,
    cmd_tx: &Sender<AppCommand>,
    streamer: &Arc<Mutex<AudioStreamer>>,
    host_config: &HostConfig,
    editor_manager: Option<&EditorWindowManager>,
) -> CommandResponse {
    let cmd_name = raw.get("cmd").and_then(Value::as_str).map(str::to_string);
    let command = match serde_json::from_value::<Command>(raw.clone()) {
        Ok(command) => command,
        Err(err) => {
            return CommandResponse::error(cmd_name.as_deref(), &format!("invalid command: {err}"));
        }
    };

    execute(
        command,
        engine,
        cmd_tx,
        streamer,
        host_config,
        editor_manager,
    )
}

fn execute(
    command: Command,
    engine: &Arc<Mutex<AudioEngine>>,
    cmd_tx: &Sender<AppCommand>,
    streamer: &Arc<Mutex<AudioStreamer>>,
    host_config: &HostConfig,
    editor_manager: Option<&EditorWindowManager>,
) -> CommandResponse {
    match command {
        Command::Play => {
            let _ = cmd_tx.send(AppCommand::Play);
            CommandResponse::ack("play")
        }
        Command::Stop => {
            let _ = cmd_tx.send(AppCommand::Stop);
            CommandResponse::ack("stop")
        }
        Command::Pause => {
            let _ = cmd_tx.send(AppCommand::Pause);
            CommandResponse::ack("pause")
        }
        Command::Seek { position } => {
            let sample_rate = engine.lock().unwrap().sample_rate();
            let samples = match seconds_to_samples(sample_rate, position) {
                Ok(samples) => samples,
                Err(err) => return CommandResponse::error(Some("seek"), err),
            };
            let _ = cmd_tx.send(AppCommand::Seek(samples));
            CommandResponse::ack("seek")
        }
        Command::AddTrack { name } => {
            let id = with_session(engine, |session| session.add_track(name));
            CommandResponse::ack_with("add_track", json!({ "track_id": id }))
        }
        Command::RemoveTrack { id } => {
            if with_session(engine, |session| session.remove_track(id)) {
                CommandResponse::ack("remove_track")
            } else {
                CommandResponse::error(Some("remove_track"), "track not found")
            }
        }
        Command::SetMidi { track_id, notes } => {
            let midi_notes = notes
                .into_iter()
                .map(|note| MidiNote::new(note.pitch, note.start, note.duration, note.velocity))
                .collect();
            if with_session(engine, |session| {
                session.set_track_notes(track_id, midi_notes)
            }) {
                CommandResponse::ack("set_midi")
            } else {
                CommandResponse::error(Some("set_midi"), "track not found")
            }
        }
        Command::SetTempo { bpm, time_sig } => {
            if bpm <= 0.0 {
                return CommandResponse::error(Some("set_tempo"), "bpm must be positive");
            }
            let _ = cmd_tx.send(AppCommand::SetTempo {
                bpm,
                time_sig: time_sig.unwrap_or((4, 4)),
            });
            CommandResponse::ack("set_tempo")
        }
        Command::SetLoop { start, end } => {
            let sample_rate = engine.lock().unwrap().sample_rate();
            let start = match seconds_to_samples(sample_rate, start) {
                Ok(samples) => samples,
                Err(err) => return CommandResponse::error(Some("set_loop"), err),
            };
            let end = match seconds_to_samples(sample_rate, end) {
                Ok(samples) => samples,
                Err(err) => return CommandResponse::error(Some("set_loop"), err),
            };
            if end <= start {
                return CommandResponse::error(Some("set_loop"), "loop end must be after start");
            }
            let _ = cmd_tx.send(AppCommand::SetLoop { start, end });
            CommandResponse::ack("set_loop")
        }
        Command::ClearLoop => {
            let _ = cmd_tx.send(AppCommand::ClearLoop);
            CommandResponse::ack("clear_loop")
        }
        Command::SetVolume { track_id, value } => {
            if with_session(engine, |session| {
                session.set_track_volume(track_id, value.max(0.0) as f32)
            }) {
                CommandResponse::ack("set_volume")
            } else {
                CommandResponse::error(Some("set_volume"), "track not found")
            }
        }
        Command::SetPan { track_id, value } => {
            if with_session(engine, |session| {
                session.set_track_pan(track_id, value as f32)
            }) {
                CommandResponse::ack("set_pan")
            } else {
                CommandResponse::error(Some("set_pan"), "track not found")
            }
        }
        Command::SetMute { track_id, value } => {
            if with_session(engine, |session| session.set_track_mute(track_id, value)) {
                CommandResponse::ack("set_mute")
            } else {
                CommandResponse::error(Some("set_mute"), "track not found")
            }
        }
        Command::SetSolo { track_id, value } => {
            if with_session(engine, |session| session.set_track_solo(track_id, value)) {
                CommandResponse::ack("set_solo")
            } else {
                CommandResponse::error(Some("set_solo"), "track not found")
            }
        }
        Command::LoadBuiltinSynth {
            track_id,
            slot_index,
        } => {
            let slot_index = usize::from(slot_index.unwrap_or(0));
            let sample_rate = engine.lock().unwrap().sample_rate();
            let synth = BasicSynth::new(sample_rate);
            let mut insert = PluginInsert::new(Box::new(synth));
            insert.activate();

            if with_session(engine, |session| {
                session.set_processor_slot(track_id, slot_index, Some(Arc::new(Mutex::new(insert))))
            }) {
                CommandResponse::ack_with(
                    "load_builtin_synth",
                    json!({ "name": "ATRI Basic Synth", "slot_index": slot_index }),
                )
            } else {
                CommandResponse::error(Some("load_builtin_synth"), "track not found")
            }
        }
        Command::LoadVst3 {
            track_id,
            path,
            name,
            slot_index,
        } => {
            let slot_index = usize::from(slot_index.unwrap_or(0));
            let path = resolve_vst3_library_path(PathBuf::from(path));
            let factory = match PluginFactory::load(&path) {
                Ok(factory) => factory,
                Err(err) => return CommandResponse::error(Some("load_vst3"), &err),
            };
            let plugin_name = name.unwrap_or_else(|| factory.plugin_name.clone());
            let plugin = Vst3Plugin::from_factory_deferred(plugin_name.clone(), factory);
            let mut insert = PluginInsert::new(Box::new(plugin));
            insert.activate();

            if with_session(engine, |session| {
                session.set_processor_slot(track_id, slot_index, Some(Arc::new(Mutex::new(insert))))
            }) {
                CommandResponse::ack_with(
                    "load_vst3",
                    json!({ "name": plugin_name, "slot_index": slot_index }),
                )
            } else {
                CommandResponse::error(Some("load_vst3"), "track not found")
            }
        }
        Command::ClearProcessorSlot {
            track_id,
            slot_index,
        } => {
            let slot_index = usize::from(slot_index);
            if with_session(engine, |session| {
                session.clear_processor_slot(track_id, slot_index)
            }) {
                CommandResponse::ack_with(
                    "clear_processor_slot",
                    json!({ "slot_index": slot_index }),
                )
            } else {
                CommandResponse::error(Some("clear_processor_slot"), "track not found")
            }
        }
        Command::OpenPluginEditor {
            track_id,
            slot_index,
        } => open_plugin_editor(engine, editor_manager, track_id, slot_index),
        Command::GetPluginState {
            track_id,
            slot_index,
        } => get_plugin_state(engine, track_id, slot_index),
        Command::SetPluginState {
            track_id,
            slot_index,
            state_b64,
        } => set_plugin_state(engine, track_id, slot_index, &state_b64),
        Command::ScanPlugins { paths, vst2_paths } => {
            let scanner = configure_scanner(host_config, paths, vst2_paths);
            let vst3 = scanner.scan();
            let vst2 = scanner.scan_vst2();
            CommandResponse::ack_with(
                "scan_plugins",
                json!({
                    "vst3": vst3,
                    "vst2": vst2,
                    "priority": ["vst3", "vst2"]
                }),
            )
        }
        Command::SetStreaming { enabled } => {
            if let Ok(mut streamer) = streamer.lock() {
                streamer.set_enabled(enabled);
            }
            CommandResponse::ack("set_streaming")
        }
        Command::GetStatus => status(engine),
        Command::Shutdown => {
            let _ = cmd_tx.send(AppCommand::Shutdown);
            CommandResponse::Shutdown {
                status: "ok".to_string(),
            }
        }
    }
}

fn configure_scanner(
    host_config: &HostConfig,
    paths: Option<Vec<String>>,
    vst2_paths: Option<Vec<String>>,
) -> PluginScanner {
    PluginScanner::new()
        .with_paths(host_config.vst3_plugin_paths.iter().cloned())
        .with_vst2_paths(host_config.vst2_plugin_paths.iter().cloned())
        .with_paths(paths.unwrap_or_default())
        .with_vst2_paths(vst2_paths.unwrap_or_default())
}

fn open_plugin_editor(
    engine: &Arc<Mutex<AudioEngine>>,
    editor_manager: Option<&EditorWindowManager>,
    track_id: u32,
    slot_index: Option<u8>,
) -> CommandResponse {
    let slot_index = usize::from(slot_index.unwrap_or(0));
    log::info!("open_plugin_editor requested: track_id={track_id}, slot_index={slot_index}");
    let Some(manager) = editor_manager else {
        return CommandResponse::error(
            Some("open_plugin_editor"),
            "plugin editor window manager is unavailable",
        );
    };
    let Some(processor) = processor_slot(engine, track_id, slot_index) else {
        return CommandResponse::error(Some("open_plugin_editor"), "plugin instance not found");
    };

    let title = match processor.lock() {
        Ok(processor) => format!("{} - ATRI", processor.name()),
        Err(_) => {
            return CommandResponse::error(
                Some("open_plugin_editor"),
                "plugin instance is unavailable",
            );
        }
    };
    let key = EditorKey {
        track_id,
        slot_index,
    };

    let window = match manager.open_and_attach(key, title, processor) {
        Ok(window) => window,
        Err(err) => {
            log::warn!("open_plugin_editor failed: {err}");
            return CommandResponse::error(Some("open_plugin_editor"), &err);
        }
    };
    log::info!(
        "open_plugin_editor completed: track_id={track_id}, slot_index={slot_index}, already_open={}",
        window.already_open
    );

    CommandResponse::ack_with(
        "open_plugin_editor",
        json!({
            "track_id": track_id,
            "slot_index": slot_index,
            "title": window.title,
            "already_open": window.already_open
        }),
    )
}

fn get_plugin_state(
    engine: &Arc<Mutex<AudioEngine>>,
    track_id: u32,
    slot_index: Option<u8>,
) -> CommandResponse {
    let slot_index = usize::from(slot_index.unwrap_or(0));
    let Some(processor) = processor_slot(engine, track_id, slot_index) else {
        return CommandResponse::error(Some("get_plugin_state"), "plugin instance not found");
    };

    let chunk = match processor.lock() {
        Ok(mut processor) => match processor.get_state_chunk() {
            Ok(chunk) => chunk,
            Err(err) => return CommandResponse::error(Some("get_plugin_state"), &err),
        },
        Err(_) => {
            return CommandResponse::error(
                Some("get_plugin_state"),
                "plugin instance is unavailable",
            );
        }
    };

    CommandResponse::ack_with(
        "get_plugin_state",
        json!({
            "track_id": track_id,
            "slot_index": slot_index,
            "state_b64": BASE64.encode(&chunk),
            "bytes": chunk.len()
        }),
    )
}

fn set_plugin_state(
    engine: &Arc<Mutex<AudioEngine>>,
    track_id: u32,
    slot_index: Option<u8>,
    state_b64: &str,
) -> CommandResponse {
    let slot_index = usize::from(slot_index.unwrap_or(0));
    let chunk = match BASE64.decode(state_b64) {
        Ok(chunk) => chunk,
        Err(err) => {
            return CommandResponse::error(
                Some("set_plugin_state"),
                &format!("invalid state_b64: {err}"),
            );
        }
    };
    let Some(processor) = processor_slot(engine, track_id, slot_index) else {
        return CommandResponse::error(Some("set_plugin_state"), "plugin instance not found");
    };

    match processor.lock() {
        Ok(mut processor) => {
            if let Err(err) = processor.set_state_chunk(&chunk) {
                return CommandResponse::error(Some("set_plugin_state"), &err);
            }
        }
        Err(_) => {
            return CommandResponse::error(
                Some("set_plugin_state"),
                "plugin instance is unavailable",
            );
        }
    }

    CommandResponse::ack_with(
        "set_plugin_state",
        json!({
            "track_id": track_id,
            "slot_index": slot_index,
            "bytes": chunk.len()
        }),
    )
}

fn processor_slot(
    engine: &Arc<Mutex<AudioEngine>>,
    track_id: u32,
    slot_index: usize,
) -> Option<Arc<Mutex<dyn Processor>>> {
    let engine = engine.lock().ok()?;
    engine.with_session(|session| session.processor_slot(track_id, slot_index))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn command_context() -> (
        Arc<Mutex<AudioEngine>>,
        Sender<AppCommand>,
        crossbeam::channel::Receiver<AppCommand>,
        Arc<Mutex<AudioStreamer>>,
        HostConfig,
    ) {
        let engine = Arc::new(Mutex::new(AudioEngine::new(48_000, 128)));
        let (cmd_tx, cmd_rx) = crossbeam::channel::unbounded();
        let streamer = Arc::new(Mutex::new(AudioStreamer::new(48_000, 2)));
        (engine, cmd_tx, cmd_rx, streamer, HostConfig::default())
    }

    #[test]
    fn configure_scanner_adds_configured_vst_paths() {
        let config = HostConfig {
            vst3_plugin_paths: vec![PathBuf::from(r"D:\ConfiguredVst3")],
            vst2_plugin_paths: vec![PathBuf::from(r"D:\ConfiguredVst2")],
        };

        let scanner = configure_scanner(
            &config,
            Some(vec![r"E:\RequestVst3".to_string()]),
            Some(vec![r"E:\RequestVst2".to_string()]),
        );

        assert!(
            scanner
                .search_paths()
                .contains(&PathBuf::from(r"D:\ConfiguredVst3"))
        );
        assert!(
            scanner
                .search_paths()
                .contains(&PathBuf::from(r"E:\RequestVst3"))
        );
        assert!(
            scanner
                .vst2_search_paths()
                .contains(&PathBuf::from(r"D:\ConfiguredVst2"))
        );
        assert!(
            scanner
                .vst2_search_paths()
                .contains(&PathBuf::from(r"E:\RequestVst2"))
        );
    }

    #[test]
    fn seek_converts_seconds_to_samples() {
        let (engine, cmd_tx, cmd_rx, streamer, config) = command_context();

        let response = execute(
            Command::Seek { position: 1.5 },
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );

        assert!(matches!(response, CommandResponse::Ack { cmd, .. } if cmd == "seek"));
        match cmd_rx.try_recv().unwrap() {
            AppCommand::Seek(samples) => assert_eq!(samples, 72_000),
            other => panic!("unexpected command: {other:?}"),
        }
    }

    #[test]
    fn seek_rejects_non_finite_position_without_sending_command() {
        let (engine, cmd_tx, cmd_rx, streamer, config) = command_context();

        let response = execute(
            Command::Seek { position: f64::NAN },
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );

        assert!(
            matches!(response, CommandResponse::Error { cmd: Some(cmd), message }
                if cmd == "seek" && message == "position must be finite")
        );
        assert!(cmd_rx.try_recv().is_err());
    }

    #[test]
    fn load_builtin_synth_replaces_processor_slot() {
        let (engine, cmd_tx, _cmd_rx, streamer, config) = command_context();
        let track_id = with_session(&engine, |session| session.add_track("Keys".to_string()));

        for _ in 0..2 {
            let response = execute(
                Command::LoadBuiltinSynth {
                    track_id,
                    slot_index: None,
                },
                &engine,
                &cmd_tx,
                &streamer,
                &config,
                None,
            );
            assert!(
                matches!(response, CommandResponse::Ack { cmd, .. } if cmd == "load_builtin_synth")
            );
        }

        let response = execute(
            Command::GetStatus,
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );
        let CommandResponse::Status { tracks, .. } = response else {
            panic!("expected status response");
        };

        assert_eq!(tracks[0].processors, vec!["ATRI Basic Synth".to_string()]);
        assert_eq!(
            tracks[0].processor_slots,
            vec![Some("ATRI Basic Synth".to_string())]
        );
    }

    #[test]
    fn clear_processor_slot_removes_only_target_slot() {
        let (engine, cmd_tx, _cmd_rx, streamer, config) = command_context();
        let track_id = with_session(&engine, |session| session.add_track("Keys".to_string()));

        let response = execute(
            Command::LoadBuiltinSynth {
                track_id,
                slot_index: Some(2),
            },
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );
        assert!(
            matches!(response, CommandResponse::Ack { cmd, .. } if cmd == "load_builtin_synth")
        );

        let response = execute(
            Command::ClearProcessorSlot {
                track_id,
                slot_index: 2,
            },
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );
        assert!(
            matches!(response, CommandResponse::Ack { cmd, .. } if cmd == "clear_processor_slot")
        );

        let response = execute(
            Command::GetStatus,
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );
        let CommandResponse::Status { tracks, .. } = response else {
            panic!("expected status response");
        };

        assert!(tracks[0].processors.is_empty());
        assert_eq!(tracks[0].processor_slots, vec![None, None, None]);
    }
}

fn resolve_vst3_library_path(path: PathBuf) -> PathBuf {
    if !path.is_dir() {
        return path;
    }

    vst3_bundle_library_path(&path).unwrap_or(path)
}

fn status(engine: &Arc<Mutex<AudioEngine>>) -> CommandResponse {
    with_session(engine, |session| {
        let tempo_map = session.tempo_map.read();
        let tracks = session
            .routes
            .iter()
            .filter_map(|route| {
                let route = route.lock().ok()?;
                let processor_slots = route
                    .processors
                    .iter()
                    .map(|processor| {
                        processor.as_ref().and_then(|processor| {
                            processor
                                .lock()
                                .ok()
                                .map(|processor| processor.name().to_string())
                        })
                    })
                    .collect::<Vec<_>>();
                Some(TrackStatus {
                    id: route.id,
                    name: route.name.clone(),
                    volume: route.gain.value,
                    pan: route.pan.value,
                    mute: route.mute,
                    solo: route.solo,
                    note_count: route.sequencer.note_count(),
                    processors: route
                        .processors
                        .iter()
                        .filter_map(|processor| {
                            processor.as_ref().and_then(|processor| {
                                processor
                                    .lock()
                                    .ok()
                                    .map(|processor| processor.name().to_string())
                            })
                        })
                        .collect(),
                    processor_slots,
                })
            })
            .collect();

        CommandResponse::Status {
            transport: format!("{:?}", session.transport.state).to_lowercase(),
            position: session.transport.position as f64 / session.sample_rate as f64,
            tempo: tempo_map.current_tempo().bpm,
            meter: (
                tempo_map.current_meter().num,
                tempo_map.current_meter().denom,
            ),
            sample_rate: session.sample_rate,
            buffer_size: session.buffer_size,
            tracks,
        }
    })
}

fn with_session<T>(engine: &Arc<Mutex<AudioEngine>>, f: impl FnOnce(&mut Session) -> T) -> T {
    let engine = engine.lock().unwrap();
    engine.with_session(f)
}

fn seconds_to_samples(sample_rate: u32, seconds: f64) -> Result<i64, &'static str> {
    if !seconds.is_finite() {
        return Err("position must be finite");
    }
    if seconds < 0.0 {
        return Err("position must be non-negative");
    }

    let samples = seconds * sample_rate as f64;
    if samples > i64::MAX as f64 {
        return Err("position is too large");
    }

    Ok(samples as i64)
}

impl CommandResponse {
    pub fn ack(cmd: &str) -> Self {
        Self::Ack {
            cmd: cmd.to_string(),
            status: "ok".to_string(),
            data: None,
        }
    }

    pub fn ack_with(cmd: &str, data: Value) -> Self {
        Self::Ack {
            cmd: cmd.to_string(),
            status: "ok".to_string(),
            data: Some(data),
        }
    }

    pub fn error(cmd: Option<&str>, message: &str) -> Self {
        Self::Error {
            cmd: cmd.map(str::to_string),
            message: message.to_string(),
        }
    }

    pub fn is_shutdown(&self) -> bool {
        matches!(self, Self::Shutdown { .. })
    }
}
