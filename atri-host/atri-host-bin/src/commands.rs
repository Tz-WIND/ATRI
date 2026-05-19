use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use atri_core::midi::event::MidiEvent;
use atri_core::midi::message::MidiMessage;
use atri_core::midi::note::MidiNote;
use atri_core::time::beats::PPQN;
use atri_engine::audio_clip::{AudioChannelMode, AudioClip, AudioClipSpec};
use atri_engine::engine::AudioEngine;
use atri_engine::plugin_proc::PluginInsert;
use atri_engine::processor::Processor;
use atri_engine::session::{
    AutomationCurve, AutomationLane, AutomationPoint, AutomationTarget, Session,
};
use atri_engine::synth::BasicSynth;
use atri_vst3::factory::PluginFactory;
use atri_vst3::plugin::Vst3Plugin;
use atri_vst3::scanner::{PluginScanner, vst3_bundle_library_path};
use base64::{Engine as _, engine::general_purpose::STANDARD as BASE64};
use cpal::traits::{DeviceTrait, HostTrait};
use crossbeam::channel::Sender;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::config::HostConfig;
use crate::driver::audio_device_id;
use crate::editor_host::{EditorKey, EditorWindowManager};
use crate::stream::AudioStreamer;

const VALID_BIT_DEPTHS: &[&str] = &["i16", "i24", "f32"];

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
        audio_engine: String,
        bit_depth: String,
        tracks: Vec<TrackStatus>,
        editor_windows: Vec<EditorWindowStatus>,
    },
    DeviceList {
        devices: Vec<AudioDeviceInfo>,
        current: Option<String>,
    },
    AudioConfig {
        sample_rate: u32,
        buffer_size: usize,
        audio_engine: String,
        bit_depth: String,
    },
    Shutdown {
        status: String,
    },
}

#[derive(Debug, Serialize)]
pub struct AudioDeviceInfo {
    pub id: String,
    pub name: String,
    pub host_api: String,
    pub channels: u16,
    pub default: bool,
    pub supported_sample_rates: Vec<u32>,
    pub supported_bit_depths: Vec<String>,
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
    audio_clip_count: usize,
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
    SetAudioClips {
        track_id: u32,
        clips: Vec<AudioClipData>,
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
    ListPluginParameters {
        track_id: u32,
        slot_index: Option<u8>,
    },
    PollCapturedPluginParameters,
    SetAutomation {
        lanes: Vec<AutomationLaneData>,
    },
    ScanPlugins {
        paths: Option<Vec<String>>,
        vst2_paths: Option<Vec<String>>,
    },
    SetStreaming {
        enabled: bool,
    },
    ListAudioDevices,
    SetAudioConfig {
        sample_rate: Option<u32>,
        buffer_size: Option<usize>,
        audio_engine: Option<String>,
        bit_depth: Option<String>,
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

#[derive(Debug, Deserialize)]
pub struct AudioClipData {
    pub path: String,
    pub start: f64,
    pub duration: f64,
    #[serde(default)]
    pub source_offset: Option<f64>,
    #[serde(default)]
    pub gain: Option<f32>,
    #[serde(default)]
    pub channel_type: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct AutomationLaneData {
    pub target: AutomationTargetData,
    #[serde(default)]
    pub points: Vec<AutomationPointData>,
    #[serde(default)]
    pub muted: bool,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case", tag = "kind")]
pub enum AutomationTargetData {
    PluginParameter {
        track_id: u32,
        slot_index: Option<u8>,
        param_index: u32,
    },
    TrackVolume {
        track_id: u32,
    },
    TrackPan {
        track_id: u32,
    },
    TempoBpm,
    TimeSignatureNumerator,
}

#[derive(Debug, Deserialize)]
pub struct AutomationPointData {
    pub beat: f64,
    pub value: f32,
    #[serde(default)]
    pub curve: Option<String>,
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
        Command::SetAudioClips { track_id, clips } => {
            let audio_clips = match audio_clips_from_data(clips) {
                Ok(clips) => clips,
                Err(err) => return CommandResponse::error(Some("set_audio_clips"), &err),
            };
            let count = audio_clips.len();
            if with_session(engine, |session| {
                session.set_track_audio_clips(track_id, audio_clips)
            }) {
                CommandResponse::ack_with("set_audio_clips", json!({ "clips": count }))
            } else {
                CommandResponse::error(Some("set_audio_clips"), "track not found")
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
        Command::ListPluginParameters {
            track_id,
            slot_index,
        } => list_plugin_parameters(engine, track_id, slot_index),
        Command::PollCapturedPluginParameters => poll_captured_plugin_parameters(engine),
        Command::SetAutomation { lanes } => set_automation(engine, lanes),
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
        Command::ListAudioDevices => list_audio_devices(engine, host_config),
        Command::SetAudioConfig {
            sample_rate,
            buffer_size,
            audio_engine,
            bit_depth,
        } => {
            let audio_engine = match audio_engine {
                Some(value) => Some(normalize_audio_engine_choice(value)),
                None => None,
            };
            let bit_depth = match bit_depth {
                Some(value) => Some(normalize_audio_config_choice(
                    "bit_depth",
                    value,
                    VALID_BIT_DEPTHS,
                )),
                None => None,
            };
            let audio_engine = match audio_engine {
                Some(Ok(value)) => Some(value),
                Some(Err(err)) => return CommandResponse::error(Some("set_audio_config"), &err),
                None => None,
            };
            let bit_depth = match bit_depth {
                Some(Ok(value)) => Some(value),
                Some(Err(err)) => return CommandResponse::error(Some("set_audio_config"), &err),
                None => None,
            };
            if let Err(err) = validate_audio_config_numbers(sample_rate, buffer_size) {
                return CommandResponse::error(Some("set_audio_config"), err);
            }
            if audio_engine.is_some() || bit_depth.is_some() {
                return CommandResponse::error(
                    Some("set_audio_config"),
                    "audio device and bit depth changes require restarting the audio host",
                );
            }

            let current = {
                let eng = engine.lock().unwrap();
                (eng.sample_rate(), eng.buffer_size())
            };
            let new_sample_rate = sample_rate.unwrap_or(current.0);
            let new_buffer_size = buffer_size.unwrap_or(current.1);

            if new_sample_rate != current.0 || new_buffer_size != current.1 {
                return CommandResponse::error(
                    Some("set_audio_config"),
                    "sample_rate and buffer_size changes require restarting the audio host",
                );
            }

            eprintln!(
                "[atri-host] audio config unchanged at runtime: sample_rate={new_sample_rate}, buffer_size={new_buffer_size}"
            );

            CommandResponse::AudioConfig {
                sample_rate: new_sample_rate,
                buffer_size: new_buffer_size,
                audio_engine: host_config.audio_host.audio_engine.clone(),
                bit_depth: host_config.audio_host.bit_depth.clone(),
            }
        }
        Command::GetStatus => status(engine, host_config, editor_manager),
        Command::Shutdown => {
            let _ = cmd_tx.send(AppCommand::Shutdown);
            CommandResponse::Shutdown {
                status: "ok".to_string(),
            }
        }
    }
}

fn validate_audio_config_numbers(
    sample_rate: Option<u32>,
    buffer_size: Option<usize>,
) -> Result<(), &'static str> {
    if matches!(sample_rate, Some(0)) {
        return Err("sample_rate must be positive");
    }
    if matches!(buffer_size, Some(0)) {
        return Err("buffer_size must be positive");
    }
    Ok(())
}

fn normalize_audio_config_choice(
    field: &str,
    value: String,
    allowed_values: &[&str],
) -> Result<String, String> {
    let normalized = value.trim().to_ascii_lowercase();
    if allowed_values.iter().any(|allowed| *allowed == normalized) {
        return Ok(normalized);
    }
    Err(format!("{field} is not supported"))
}

fn normalize_audio_engine_choice(value: String) -> Result<String, String> {
    let value = value.trim();
    if value.is_empty() || value.eq_ignore_ascii_case("default") {
        return Ok("default".to_string());
    }

    let (host_key, device_name) = value
        .split_once("::")
        .map(|(host, name)| (host, Some(name)))
        .unwrap_or((value, None));
    let host_id = cpal::available_hosts()
        .into_iter()
        .find(|host_id| normalize_audio_key(host_id.name()) == normalize_audio_key(host_key))
        .ok_or_else(|| "audio_engine is not supported".to_string())?;
    let host =
        cpal::host_from_id(host_id).map_err(|_| "audio_engine is not supported".to_string())?;

    let Some(device_name) = device_name else {
        let default_device = host
            .default_output_device()
            .ok_or_else(|| "audio_engine is not supported".to_string())?;
        default_device
            .default_output_config()
            .map_err(|_| "audio_engine is not supported".to_string())?;
        return Ok(normalize_audio_key(host_id.name()));
    };
    if device_name.trim().is_empty() {
        return Err("audio_engine is not supported".to_string());
    }

    for device in host
        .output_devices()
        .map_err(|_| "audio_engine is not supported".to_string())?
    {
        let Ok(name) = device.name() else {
            continue;
        };
        if name == device_name {
            device
                .default_output_config()
                .map_err(|_| "audio_engine is not supported".to_string())?;
            return Ok(audio_device_id(host_id.name(), device_name));
        }
    }

    Err("audio_engine is not supported".to_string())
}

fn normalize_audio_key(value: &str) -> String {
    value.trim().to_ascii_lowercase().replace(' ', "_")
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

fn audio_clips_from_data(clips: Vec<AudioClipData>) -> Result<Vec<AudioClip>, String> {
    clips
        .into_iter()
        .map(|clip| {
            AudioClip::load(AudioClipSpec {
                path: PathBuf::from(clip.path),
                start_beats: clip.start,
                duration_beats: clip.duration,
                source_offset_seconds: clip.source_offset.unwrap_or(0.0),
                gain: clip.gain.unwrap_or(1.0),
                channel_mode: audio_channel_mode_from_data(clip.channel_type.as_deref()),
            })
        })
        .collect()
}

fn audio_channel_mode_from_data(value: Option<&str>) -> AudioChannelMode {
    match value
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase()
        .as_str()
    {
        "mono" | "monophonic" => AudioChannelMode::Mono,
        _ => AudioChannelMode::Multichannel,
    }
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

fn list_plugin_parameters(
    engine: &Arc<Mutex<AudioEngine>>,
    track_id: u32,
    slot_index: Option<u8>,
) -> CommandResponse {
    let slot_index = usize::from(slot_index.unwrap_or(0));
    let Some(processor) = processor_slot(engine, track_id, slot_index) else {
        return CommandResponse::error(Some("list_plugin_parameters"), "plugin instance not found");
    };

    let parameters = match processor.lock() {
        Ok(mut processor) => processor.parameter_info(),
        Err(_) => {
            return CommandResponse::error(
                Some("list_plugin_parameters"),
                "plugin instance is unavailable",
            );
        }
    };

    CommandResponse::ack_with(
        "list_plugin_parameters",
        json!({
            "track_id": track_id,
            "slot_index": slot_index,
            "parameter_count": parameters.len(),
            "parameters": parameters
        }),
    )
}

fn poll_captured_plugin_parameters(engine: &Arc<Mutex<AudioEngine>>) -> CommandResponse {
    let parameters = with_session(engine, |session| {
        let mut captured = Vec::new();
        for route_arc in &session.routes {
            let Ok(route) = route_arc.lock() else {
                continue;
            };
            let track_id = route.id;
            for (slot_index, processor) in route.processors.iter().enumerate() {
                let Some(processor) = processor else {
                    continue;
                };
                let Ok(mut processor) = processor.lock() else {
                    continue;
                };
                let plugin_name = processor.name().to_string();
                let parameter_info = processor.parameter_info();
                for edit in processor.drain_captured_parameter_edits() {
                    let info = parameter_info
                        .iter()
                        .find(|info| info.param_id == Some(edit.param_id));
                    captured.push(json!({
                        "track_id": track_id,
                        "slot_index": slot_index,
                        "param_index": info.map(|info| info.index).unwrap_or(0),
                        "param_id": edit.param_id,
                        "name": info
                            .map(|info| info.name.clone())
                            .unwrap_or_else(|| format!("Parameter {}", edit.param_id)),
                        "units": info.map(|info| info.units.clone()).unwrap_or_default(),
                        "value": edit.value,
                        "automatable": info.map(|info| info.automatable).unwrap_or(true),
                        "plugin_name": plugin_name,
                        "captured_at_millis": edit.captured_at_millis,
                    }));
                }
            }
        }
        captured
    });
    CommandResponse::ack_with(
        "poll_captured_plugin_parameters",
        json!({ "parameters": parameters }),
    )
}

fn set_automation(
    engine: &Arc<Mutex<AudioEngine>>,
    lanes: Vec<AutomationLaneData>,
) -> CommandResponse {
    let lanes = lanes
        .into_iter()
        .map(automation_lane_from_data)
        .collect::<Vec<_>>();
    with_session(engine, |session| session.set_automation_lanes(lanes));
    CommandResponse::ack_with(
        "set_automation",
        json!({
            "lanes": with_session(engine, |session| session.automation_lane_count())
        }),
    )
}

fn automation_lane_from_data(data: AutomationLaneData) -> AutomationLane {
    AutomationLane {
        target: match data.target {
            AutomationTargetData::PluginParameter {
                track_id,
                slot_index,
                param_index,
            } => AutomationTarget::PluginParameter {
                track_id,
                slot_index: usize::from(slot_index.unwrap_or(0)),
                param_index,
            },
            AutomationTargetData::TrackVolume { track_id } => {
                AutomationTarget::TrackVolume { track_id }
            }
            AutomationTargetData::TrackPan { track_id } => AutomationTarget::TrackPan { track_id },
            AutomationTargetData::TempoBpm => AutomationTarget::TempoBpm,
            AutomationTargetData::TimeSignatureNumerator => {
                AutomationTarget::TimeSignatureNumerator
            }
        },
        points: data
            .points
            .into_iter()
            .map(|point| AutomationPoint {
                beat: point.beat.max(0.0),
                value: point.value,
                curve: match point.curve.as_deref() {
                    Some("hold") => AutomationCurve::Hold,
                    _ => AutomationCurve::Linear,
                },
            })
            .collect(),
        muted: data.muted,
    }
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

    struct MetadataProcessor;

    impl Processor for MetadataProcessor {
        fn name(&self) -> &str {
            "metadata-processor"
        }

        fn run(
            &mut self,
            _bufs: &mut atri_core::audio::buffer_set::BufferSet,
            _midi: &[atri_core::midi::event::ScheduledMidiEvent],
            _start_sample: i64,
            _end_sample: i64,
            _speed: f64,
            _nframes: usize,
            _result_required: bool,
        ) {
        }

        fn activate(&mut self) {}
        fn deactivate(&mut self) {}
        fn is_active(&self) -> bool {
            true
        }
        fn input_channels(&self) -> u16 {
            2
        }
        fn output_channels(&self) -> u16 {
            2
        }
        fn parameter_count(&mut self) -> u32 {
            1
        }
        fn get_parameter(&mut self, index: u32) -> Option<f32> {
            (index == 0).then_some(0.42)
        }
        fn parameter_info(&mut self) -> Vec<atri_core::plugin::PluginParameterInfo> {
            vec![atri_core::plugin::PluginParameterInfo {
                index: 0,
                param_id: Some(100),
                name: "Cutoff".to_string(),
                units: "Hz".to_string(),
                value: 0.42,
                automatable: true,
            }]
        }
    }

    struct CapturingProcessor {
        edits: Vec<atri_core::plugin::CapturedPluginParameterEdit>,
    }

    impl Processor for CapturingProcessor {
        fn name(&self) -> &str {
            "capturing-processor"
        }

        fn run(
            &mut self,
            _bufs: &mut atri_core::audio::buffer_set::BufferSet,
            _midi: &[atri_core::midi::event::ScheduledMidiEvent],
            _start_sample: i64,
            _end_sample: i64,
            _speed: f64,
            _nframes: usize,
            _result_required: bool,
        ) {
        }

        fn activate(&mut self) {}
        fn deactivate(&mut self) {}
        fn is_active(&self) -> bool {
            true
        }
        fn input_channels(&self) -> u16 {
            2
        }
        fn output_channels(&self) -> u16 {
            2
        }
        fn parameter_info(&mut self) -> Vec<atri_core::plugin::PluginParameterInfo> {
            vec![atri_core::plugin::PluginParameterInfo {
                index: 2,
                param_id: Some(900),
                name: "Resonance".to_string(),
                units: "%".to_string(),
                value: 0.77,
                automatable: true,
            }]
        }
        fn drain_captured_parameter_edits(
            &mut self,
        ) -> Vec<atri_core::plugin::CapturedPluginParameterEdit> {
            std::mem::take(&mut self.edits)
        }
    }

    #[test]
    fn configure_scanner_adds_configured_vst_paths() {
        let config = HostConfig {
            vst3_plugin_paths: vec![PathBuf::from(r"D:\ConfiguredVst3")],
            vst2_plugin_paths: vec![PathBuf::from(r"D:\ConfiguredVst2")],
            ..Default::default()
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
    fn list_plugin_parameters_reports_metadata() {
        let (engine, cmd_tx, _cmd_rx, streamer, config) = command_context();
        let track_id = with_session(&engine, |session| {
            let track_id = session.add_track("Keys".to_string());
            assert!(session.set_processor_slot(
                track_id,
                0,
                Some(Arc::new(Mutex::new(MetadataProcessor))),
            ));
            track_id
        });

        let response = execute(
            Command::ListPluginParameters {
                track_id,
                slot_index: Some(0),
            },
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );

        let CommandResponse::Ack { cmd, data, .. } = response else {
            panic!("expected ack response");
        };
        assert_eq!(cmd, "list_plugin_parameters");
        let data = data.expect("metadata response data");
        assert_eq!(data["parameter_count"], 1);
        assert_eq!(data["parameters"][0]["name"], "Cutoff");
        assert_eq!(data["parameters"][0]["units"], "Hz");
        assert_eq!(data["parameters"][0]["automatable"], true);
        let value = data["parameters"][0]["value"].as_f64().unwrap();
        assert!((value - 0.42).abs() < 0.0001);
    }

    #[test]
    fn poll_captured_plugin_parameters_drains_and_enriches_edits() {
        let (engine, cmd_tx, _cmd_rx, streamer, config) = command_context();
        let track_id = with_session(&engine, |session| {
            let track_id = session.add_track("Keys".to_string());
            assert!(session.set_processor_slot(
                track_id,
                1,
                Some(Arc::new(Mutex::new(CapturingProcessor {
                    edits: vec![atri_core::plugin::CapturedPluginParameterEdit {
                        param_id: 900,
                        value: 0.77,
                        captured_at_millis: 1234,
                    }],
                }))),
            ));
            track_id
        });

        let response = execute(
            Command::PollCapturedPluginParameters,
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );

        let CommandResponse::Ack { cmd, data, .. } = response else {
            panic!("expected ack response");
        };
        assert_eq!(cmd, "poll_captured_plugin_parameters");
        let data = data.expect("captured response data");
        assert_eq!(data["parameters"][0]["track_id"], track_id);
        assert_eq!(data["parameters"][0]["slot_index"], 1);
        assert_eq!(data["parameters"][0]["param_index"], 2);
        assert_eq!(data["parameters"][0]["param_id"], 900);
        assert_eq!(data["parameters"][0]["name"], "Resonance");
        assert_eq!(data["parameters"][0]["value"], 0.77);

        let drained = execute(
            Command::PollCapturedPluginParameters,
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );
        let CommandResponse::Ack { data, .. } = drained else {
            panic!("expected ack response");
        };
        assert_eq!(data.unwrap()["parameters"].as_array().unwrap().len(), 0);
    }

    #[test]
    fn set_automation_stores_lanes_without_adding_routes() {
        let (engine, cmd_tx, _cmd_rx, streamer, config) = command_context();
        let track_id = with_session(&engine, |session| session.add_track("Keys".to_string()));

        let response = execute(
            Command::SetAutomation {
                lanes: vec![AutomationLaneData {
                    target: AutomationTargetData::TrackVolume { track_id },
                    points: vec![AutomationPointData {
                        beat: 0.0,
                        value: 0.5,
                        curve: Some("linear".to_string()),
                    }],
                    muted: false,
                }],
            },
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );

        assert!(matches!(response, CommandResponse::Ack { cmd, .. } if cmd == "set_automation"));
        let (route_count, lane_count) = with_session(&engine, |session| {
            (session.routes.len(), session.automation_lane_count())
        });
        assert_eq!(route_count, 1);
        assert_eq!(lane_count, 1);
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

    #[test]
    fn set_audio_config_rejects_zero_values() {
        let (engine, cmd_tx, _cmd_rx, streamer, config) = command_context();

        let response = execute(
            Command::SetAudioConfig {
                sample_rate: Some(0),
                buffer_size: Some(128),
                audio_engine: None,
                bit_depth: None,
            },
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );

        assert!(
            matches!(response, CommandResponse::Error { cmd: Some(cmd), message }
                if cmd == "set_audio_config" && message == "sample_rate must be positive")
        );
    }

    #[test]
    fn set_audio_config_rejects_invalid_choices() {
        let (engine, cmd_tx, _cmd_rx, streamer, config) = command_context();

        let response = execute(
            Command::SetAudioConfig {
                sample_rate: None,
                buffer_size: None,
                audio_engine: Some("not-a-host".to_string()),
                bit_depth: None,
            },
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );
        assert!(
            matches!(response, CommandResponse::Error { cmd: Some(cmd), message }
                if cmd == "set_audio_config" && message == "audio_engine is not supported")
        );

        let response = execute(
            Command::SetAudioConfig {
                sample_rate: None,
                buffer_size: None,
                audio_engine: None,
                bit_depth: Some("u8".to_string()),
            },
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );
        assert!(
            matches!(response, CommandResponse::Error { cmd: Some(cmd), message }
                if cmd == "set_audio_config" && message == "bit_depth is not supported")
        );
    }

    #[test]
    fn set_audio_config_requires_restart_for_rate_or_buffer_change() {
        let (engine, cmd_tx, _cmd_rx, streamer, config) = command_context();

        let response = execute(
            Command::SetAudioConfig {
                sample_rate: Some(96_000),
                buffer_size: Some(256),
                audio_engine: None,
                bit_depth: None,
            },
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );

        assert!(
            matches!(response, CommandResponse::Error { cmd: Some(cmd), message }
                if cmd == "set_audio_config"
                    && message == "sample_rate and buffer_size changes require restarting the audio host")
        );
        assert_eq!(engine.lock().unwrap().sample_rate(), 48_000);
        assert_eq!(engine.lock().unwrap().buffer_size(), 128);
    }

    #[test]
    fn set_audio_config_noop_reports_configured_device_settings() {
        let (engine, cmd_tx, _cmd_rx, streamer, mut config) = command_context();
        config.audio_host.audio_engine = "wasapi::Speakers".to_string();
        config.audio_host.bit_depth = "i24".to_string();

        let response = execute(
            Command::SetAudioConfig {
                sample_rate: Some(48_000),
                buffer_size: Some(128),
                audio_engine: None,
                bit_depth: None,
            },
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );

        assert!(matches!(response, CommandResponse::AudioConfig {
                sample_rate: 48_000,
                buffer_size: 128,
                audio_engine,
                bit_depth,
            } if audio_engine == "wasapi::Speakers" && bit_depth == "i24"));
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
    host_config: &HostConfig,
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
                    audio_clip_count: route.audio_clip_count(),
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
            audio_engine: host_config.audio_host.audio_engine.clone(),
            bit_depth: host_config.audio_host.bit_depth.clone(),
            tracks,
            editor_windows,
        }
    })
}

fn list_audio_devices(
    engine: &Arc<Mutex<AudioEngine>>,
    host_config: &HostConfig,
) -> CommandResponse {
    let _current_sample_rate = engine.lock().map(|eng| eng.sample_rate()).unwrap_or(48_000);
    let default_host = cpal::default_host();
    let default_name = default_host
        .default_output_device()
        .as_ref()
        .and_then(|device| device.name().ok())
        .unwrap_or_default();

    let mut devices = Vec::new();
    for host_id in cpal::available_hosts() {
        let Ok(host) = cpal::host_from_id(host_id) else {
            continue;
        };
        let host_api = host_id.name().to_string();
        let Ok(output_devices) = host.output_devices() else {
            continue;
        };

        for device in output_devices {
            let Some(info) = audio_device_info(&host_api, &default_name, device) else {
                continue;
            };
            devices.push(info);
        }
    }
    devices.sort_by(|a, b| {
        b.default
            .cmp(&a.default)
            .then_with(|| a.host_api.cmp(&b.host_api))
            .then_with(|| a.name.cmp(&b.name))
    });

    CommandResponse::DeviceList {
        devices,
        current: Some(host_config.audio_host.audio_engine.clone()),
    }
}

fn audio_device_info(
    host_api: &str,
    default_name: &str,
    device: cpal::Device,
) -> Option<AudioDeviceInfo> {
    let name = device.name().ok()?;
    let is_default = normalize_audio_key(host_api)
        == normalize_audio_key(cpal::default_host().id().name())
        && name == default_name;
    let config = device.default_output_config().ok()?;
    let (supported_rates, supported_bit_depths) = supported_audio_settings(&device, &config);

    Some(AudioDeviceInfo {
        id: audio_device_id(host_api, &name),
        name,
        host_api: host_api.to_string(),
        channels: config.channels(),
        default: is_default,
        supported_sample_rates: supported_rates,
        supported_bit_depths,
    })
}

fn supported_audio_settings(
    device: &cpal::Device,
    default_config: &cpal::SupportedStreamConfig,
) -> (Vec<u32>, Vec<String>) {
    let mut rates = Vec::new();
    let mut bit_depths = Vec::new();
    let configs = device.supported_output_configs();
    if let Ok(configs) = configs {
        let configs = configs.collect::<Vec<_>>();
        rates = [44_100, 48_000, 96_000, 192_000]
            .into_iter()
            .filter(|rate| {
                configs.iter().any(|config| {
                    config.min_sample_rate().0 <= *rate && config.max_sample_rate().0 >= *rate
                })
            })
            .collect();
        for config in configs {
            let bit_depth = sample_format_bit_depth(config.sample_format()).to_string();
            if !bit_depths.contains(&bit_depth) {
                bit_depths.push(bit_depth);
            }
        }
    }

    if rates.is_empty() {
        rates.push(default_config.sample_rate().0);
    }
    if bit_depths.is_empty() {
        bit_depths.push(sample_format_bit_depth(default_config.sample_format()).to_string());
    }
    bit_depths.sort();
    (rates, bit_depths)
}

fn sample_format_bit_depth(format: cpal::SampleFormat) -> &'static str {
    match format {
        cpal::SampleFormat::I16 | cpal::SampleFormat::U16 => "i16",
        cpal::SampleFormat::I32 | cpal::SampleFormat::U32 => "i24",
        cpal::SampleFormat::F32 | cpal::SampleFormat::F64 => "f32",
        _ => "f32",
    }
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
