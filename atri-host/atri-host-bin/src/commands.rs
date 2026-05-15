use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use atri_core::midi::event::MidiEvent;
use atri_core::midi::message::MidiMessage;
use atri_core::midi::note::MidiNote;
use atri_core::time::beats::PPQN;
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
        editor_windows: Vec<EditorWindowStatus>,
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
    midi_event_count: usize,
    processors: Vec<String>,
    processor_slots: Vec<Option<String>>,
}

#[derive(Debug, Serialize)]
pub struct EditorWindowStatus {
    track_id: u32,
    slot_index: usize,
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
        #[serde(default)]
        events: Vec<MidiEventData>,
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
    GetPluginParameter {
        track_id: u32,
        slot_index: Option<u8>,
        index: u32,
    },
    SetPluginParameter {
        track_id: u32,
        slot_index: Option<u8>,
        index: u32,
        value: f32,
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

#[derive(Debug, Deserialize)]
pub struct MidiEventData {
    #[serde(default)]
    pub start: Option<f64>,
    #[serde(default)]
    pub beat: Option<f64>,
    #[serde(default)]
    pub tick: Option<i64>,
    #[serde(default, rename = "type", alias = "kind", alias = "message")]
    pub message_type: String,
    #[serde(default)]
    pub channel: Option<u8>,
    #[serde(default)]
    pub pitch: Option<u8>,
    #[serde(default)]
    pub velocity: Option<u8>,
    #[serde(default)]
    pub controller: Option<u8>,
    #[serde(default)]
    pub value: Option<i32>,
    #[serde(default)]
    pub program: Option<u8>,
    #[serde(default)]
    pub pressure: Option<u8>,
    #[serde(default)]
    pub data_b64: Option<String>,
    #[serde(default)]
    pub data: Option<Vec<u8>>,
    #[serde(default)]
    pub bytes: Option<Vec<u8>>,
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
        Command::SetMidi {
            track_id,
            notes,
            events,
        } => {
            let midi_notes = notes
                .into_iter()
                .map(|note| MidiNote::new(note.pitch, note.start, note.duration, note.velocity))
                .collect();
            let midi_events = match midi_events_from_data(events) {
                Ok(events) => events,
                Err(err) => return CommandResponse::error(Some("set_midi"), &err),
            };
            if with_session(engine, |session| {
                session.set_track_midi(track_id, midi_notes, midi_events)
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
            let sample_rate = engine.lock().unwrap().sample_rate();
            let plugin = Vst3Plugin::from_factory_deferred_with_sample_rate(
                plugin_name.clone(),
                factory,
                f64::from(sample_rate),
            );
            let mut insert = PluginInsert::new(Box::new(plugin));
            insert.activate();
            let processor: Arc<Mutex<dyn Processor>> = Arc::new(Mutex::new(insert));
            if let Ok(mut processor) = processor.lock() {
                processor.set_block_size(engine.lock().unwrap().buffer_size());
            }

            let key = EditorKey {
                track_id,
                slot_index,
            };
            let prepare_result = if let Some(manager) = editor_manager {
                manager.prepare_processor(key, Arc::clone(&processor))
            } else {
                processor
                    .lock()
                    .map_err(|_| "plugin instance is unavailable".to_string())
                    .and_then(|mut processor| processor.prepare_for_processing())
            };
            if let Err(err) = prepare_result {
                return CommandResponse::error(Some("load_vst3"), &err);
            }

            if with_session(engine, |session| {
                session.set_processor_slot(track_id, slot_index, Some(processor))
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
        Command::GetPluginParameter {
            track_id,
            slot_index,
            index,
        } => get_plugin_parameter(engine, track_id, slot_index, index),
        Command::SetPluginParameter {
            track_id,
            slot_index,
            index,
            value,
        } => set_plugin_parameter(engine, track_id, slot_index, index, value),
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
        Command::GetStatus => status(engine, editor_manager),
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

fn midi_events_from_data(events: Vec<MidiEventData>) -> Result<Vec<MidiEvent>, String> {
    events.into_iter().map(midi_event_from_data).collect()
}

fn midi_event_from_data(event: MidiEventData) -> Result<MidiEvent, String> {
    let tick = midi_event_tick(&event)?;
    let channel = event.channel.unwrap_or(0).min(15);
    let message_type = event
        .message_type
        .trim()
        .to_ascii_lowercase()
        .replace(['-', ' '], "_");
    let message = match message_type.as_str() {
        "note_on" | "noteon" => MidiMessage::NoteOn {
            channel,
            pitch: event.pitch.unwrap_or(60).min(127),
            velocity: event.velocity.unwrap_or(96).min(127),
        },
        "note_off" | "noteoff" => MidiMessage::NoteOff {
            channel,
            pitch: event.pitch.unwrap_or(60).min(127),
            velocity: event.velocity.unwrap_or(0).min(127),
        },
        "control_change" | "cc" | "controller" => MidiMessage::ControlChange {
            channel,
            controller: event.controller.unwrap_or(0).min(127),
            value: midi_value_7bit(event.value, 0),
        },
        "pitch_bend" | "pitchbend" => MidiMessage::PitchBend {
            channel,
            value: event.value.unwrap_or(0).clamp(-8192, 8191) as i16,
        },
        "program_change" | "programchange" => MidiMessage::ProgramChange {
            channel,
            program: event
                .program
                .unwrap_or_else(|| midi_value_7bit(event.value, 0))
                .min(127),
        },
        "channel_pressure" | "channelpressure" | "aftertouch" => MidiMessage::ChannelPressure {
            channel,
            pressure: event
                .pressure
                .unwrap_or_else(|| midi_value_7bit(event.value, 0))
                .min(127),
        },
        "polyphonic_key_pressure" | "poly_key_pressure" | "poly_pressure" | "poly_aftertouch" => {
            MidiMessage::PolyphonicKeyPressure {
                channel,
                pitch: event.pitch.unwrap_or(60).min(127),
                pressure: event
                    .pressure
                    .unwrap_or_else(|| midi_value_7bit(event.value, 0))
                    .min(127),
            }
        }
        "all_notes_off" | "allnotesoff" => MidiMessage::AllNotesOff { channel },
        "sysex" | "system_exclusive" | "systemexclusive" => {
            MidiMessage::SystemExclusive(midi_sysex_bytes(&event)?)
        }
        "" => return Err("MIDI event type is required".to_string()),
        other => return Err(format!("unsupported MIDI event type: {other}")),
    };

    Ok(MidiEvent::new(tick, message))
}

fn midi_event_tick(event: &MidiEventData) -> Result<i64, String> {
    if let Some(tick) = event.tick {
        return Ok(tick.max(0));
    }
    let beat = event.start.or(event.beat).unwrap_or(0.0);
    if !beat.is_finite() || beat < 0.0 {
        return Err("MIDI event start must be a non-negative beat position".to_string());
    }
    let tick = (beat * f64::from(PPQN)).round();
    if tick > i64::MAX as f64 {
        return Err("MIDI event start is too large".to_string());
    }
    Ok(tick as i64)
}

fn midi_sysex_bytes(event: &MidiEventData) -> Result<Vec<u8>, String> {
    if let Some(encoded) = event.data_b64.as_deref().filter(|data| !data.is_empty()) {
        return BASE64
            .decode(encoded)
            .map_err(|err| format!("invalid MIDI SysEx data_b64: {err}"));
    }
    Ok(event
        .data
        .clone()
        .or_else(|| event.bytes.clone())
        .unwrap_or_default())
}

fn midi_value_7bit(value: Option<i32>, default: u8) -> u8 {
    value.unwrap_or(i32::from(default)).clamp(0, 127) as u8
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

fn get_plugin_parameter(
    engine: &Arc<Mutex<AudioEngine>>,
    track_id: u32,
    slot_index: Option<u8>,
    index: u32,
) -> CommandResponse {
    let slot_index = usize::from(slot_index.unwrap_or(0));
    let Some(processor) = processor_slot(engine, track_id, slot_index) else {
        return CommandResponse::error(Some("get_plugin_parameter"), "plugin instance not found");
    };

    let (value, count) = match processor.lock() {
        Ok(mut processor) => {
            let count = processor.parameter_count();
            let value = processor.get_parameter(index).unwrap_or(0.0);
            (value, count)
        }
        Err(_) => {
            return CommandResponse::error(
                Some("get_plugin_parameter"),
                "plugin instance is unavailable",
            );
        }
    };

    CommandResponse::ack_with(
        "get_plugin_parameter",
        json!({
            "track_id": track_id,
            "slot_index": slot_index,
            "index": index,
            "value": value,
            "parameter_count": count
        }),
    )
}

fn set_plugin_parameter(
    engine: &Arc<Mutex<AudioEngine>>,
    track_id: u32,
    slot_index: Option<u8>,
    index: u32,
    value: f32,
) -> CommandResponse {
    let slot_index = usize::from(slot_index.unwrap_or(0));
    let Some(processor) = processor_slot(engine, track_id, slot_index) else {
        return CommandResponse::error(Some("set_plugin_parameter"), "plugin instance not found");
    };

    match processor.lock() {
        Ok(mut processor) => {
            if let Err(err) = processor.set_parameter(index, value.clamp(0.0, 1.0)) {
                return CommandResponse::error(Some("set_plugin_parameter"), &err);
            }
        }
        Err(_) => {
            return CommandResponse::error(
                Some("set_plugin_parameter"),
                "plugin instance is unavailable",
            );
        }
    }

    CommandResponse::ack_with(
        "set_plugin_parameter",
        json!({
            "track_id": track_id,
            "slot_index": slot_index,
            "index": index,
            "value": value.clamp(0.0, 1.0)
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

    #[test]
    fn set_midi_accepts_controller_events() {
        let (engine, cmd_tx, _cmd_rx, streamer, config) = command_context();
        let track_id = with_session(&engine, |session| session.add_track("Keys".to_string()));

        let response = execute(
            Command::SetMidi {
                track_id,
                notes: Vec::new(),
                events: vec![MidiEventData {
                    start: Some(1.0),
                    beat: None,
                    tick: None,
                    message_type: "cc".to_string(),
                    channel: Some(3),
                    pitch: None,
                    velocity: None,
                    controller: Some(74),
                    value: Some(100),
                    program: None,
                    pressure: None,
                    data_b64: None,
                    data: None,
                    bytes: None,
                }],
            },
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );

        assert!(matches!(response, CommandResponse::Ack { cmd, .. } if cmd == "set_midi"));
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
        assert_eq!(tracks[0].midi_event_count, 1);
    }
}

fn resolve_vst3_library_path(path: PathBuf) -> PathBuf {
    if !path.is_dir() {
        return path;
    }

    vst3_bundle_library_path(&path).unwrap_or(path)
}

fn status(
    engine: &Arc<Mutex<AudioEngine>>,
    editor_manager: Option<&EditorWindowManager>,
) -> CommandResponse {
    let editor_windows = editor_manager
        .map(|manager| {
            manager
                .open_editor_keys()
                .into_iter()
                .map(|key| EditorWindowStatus {
                    track_id: key.track_id,
                    slot_index: key.slot_index,
                })
                .collect()
        })
        .unwrap_or_default();

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
                    midi_event_count: route.sequencer.midi_event_count(),
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
            editor_windows,
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
