use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use atri_core::midi::event::MidiEvent;
use atri_core::midi::message::MidiMessage;
use atri_core::midi::note::MidiNote;
use atri_core::time::beats::PPQN;
use atri_core::time::tempo::{Meter, Tempo};
use atri_engine::audio_clip::{AudioChannelMode, AudioClip, AudioClipSpec};
use atri_engine::engine::AudioEngine;
use atri_engine::plugin_proc::PluginInsert;
use atri_engine::processor::Processor;
use atri_engine::route::{RouteKind, RouteSend};
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
const SET_TEMPO_BPM_ERROR: &str = "bpm must be between 1 and 999";
const SET_TEMPO_TIME_SIG_ERROR: &str =
    "time_sig must have a positive numerator and a denominator of 1, 2, 4, 8, 16, 32, or 64";

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
        streaming_enabled: bool,
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
    kind: String,
    output_track_id: Option<u32>,
    sends: Vec<RouteSendStatus>,
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
pub struct RouteSendStatus {
    target_track_id: u32,
    level: f32,
    enabled: bool,
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
    SetRouteConfig {
        track_id: u32,
        kind: Option<RouteKindData>,
        output_track_id: Option<u32>,
    },
    SetRouteSends {
        track_id: u32,
        #[serde(default)]
        sends: Vec<RouteSendData>,
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
    /// Offline-render a WAV segment without permanently changing the live session.
    ///
    /// While this command runs, the host temporarily adjusts transport position, disables
    /// loop playback, sets solo on the listed tracks (when `track_ids` is provided), and
    /// may change engine sample rate / buffer size. Those changes are reverted when the
    /// command finishes.
    ///
    /// **Captured in the pre-bounce snapshot (restored afterward):** transport
    /// state/position/speed/loop, tempo map, per-route gain smoothing state, pan, solo/mute,
    /// route delay-line buffers, and processor state chunks plus parameter values.
    ///
    /// **Not snapshotted:** automation lane definitions (lanes are not modified), sequencer
    /// or clip timeline positions, route topology, or send levels. Automation and transport
    /// only affect audio during the offline pass; a successful bounce leaves session state
    /// matching the pre-export snapshot.
    RenderWav {
        path: String,
        start: f64,
        end: f64,
        track_ids: Option<Vec<u32>>,
        sample_rate: Option<u32>,
        bit_depth: Option<String>,
        buffer_size: Option<usize>,
    },
    /// General bounce/export entry point (currently WAV only). Uses the same temporary
    /// transport/solo behaviour and pre-bounce session snapshot as `RenderWav`; see that
    /// variant's documentation for what is and is not restored.
    Bounce {
        path: String,
        format: Option<String>,
        start: f64,
        end: f64,
        track_ids: Option<Vec<u32>>,
        sample_rate: Option<u32>,
        bit_depth: Option<String>,
        buffer_size: Option<usize>,
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

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RouteSendData {
    pub target_track_id: u32,
    pub level: f32,
    #[serde(default = "default_true")]
    pub enabled: bool,
}

impl From<RouteSendData> for RouteSend {
    fn from(value: RouteSendData) -> Self {
        Self {
            target_track_id: value.target_track_id,
            level: value.level,
            enabled: value.enabled,
        }
    }
}

fn default_true() -> bool {
    true
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
            if !Tempo::is_valid_bpm(bpm) {
                return CommandResponse::error(Some("set_tempo"), SET_TEMPO_BPM_ERROR);
            }
            let time_sig = time_sig.unwrap_or((4, 4));
            if !Meter::is_valid(time_sig.0, time_sig.1) {
                return CommandResponse::error(Some("set_tempo"), SET_TEMPO_TIME_SIG_ERROR);
            }
            let _ = cmd_tx.send(AppCommand::SetTempo { bpm, time_sig });
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
        Command::SetRouteConfig {
            track_id,
            kind,
            output_track_id,
        } => {
            let kind = kind.map(RouteKind::from);
            let ok = with_session(engine, |session| {
                session.set_route_config(track_id, kind, output_track_id)
            });
            if ok {
                CommandResponse::ack("set_route_config")
            } else {
                CommandResponse::error(Some("set_route_config"), "invalid route config")
            }
        }
        Command::SetRouteSends { track_id, sends } => {
            let sends = sends.into_iter().map(RouteSend::from).collect::<Vec<_>>();
            if with_session(engine, |session| session.set_route_sends(track_id, sends)) {
                CommandResponse::ack("set_route_sends")
            } else {
                CommandResponse::error(Some("set_route_sends"), "invalid route sends")
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
            if !with_session(engine, |session| session.has_route(track_id)) {
                return CommandResponse::error(Some("load_vst3"), "track not found");
            }
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
        Command::RenderWav {
            path,
            start,
            end,
            track_ids,
            sample_rate,
            bit_depth,
            buffer_size,
        } => bounce_command(
            "render_wav",
            engine,
            host_config,
            BounceCommandParams {
                path,
                format: Some("wav".to_string()),
                start,
                end,
                track_ids,
                sample_rate,
                bit_depth,
                buffer_size,
            },
        ),
        Command::Bounce {
            path,
            format,
            start,
            end,
            track_ids,
            sample_rate,
            bit_depth,
            buffer_size,
        } => bounce_command(
            "bounce",
            engine,
            host_config,
            BounceCommandParams {
                path,
                format,
                start,
                end,
                track_ids,
                sample_rate,
                bit_depth,
                buffer_size,
            },
        ),
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
        Command::GetStatus => status(engine, streamer, host_config, editor_manager),
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
        session
            .drain_captured_plugin_parameter_edits()
            .into_iter()
            .map(|captured| {
                let info = captured.parameter.as_ref();
                json!({
                    "track_id": captured.track_id,
                    "slot_index": captured.slot_index,
                    "param_index": info.map(|info| info.index).unwrap_or(0),
                    "param_id": captured.edit.param_id,
                    "name": info
                        .map(|info| info.name.clone())
                        .unwrap_or_else(|| format!("Parameter {}", captured.edit.param_id)),
                    "units": info.map(|info| info.units.clone()).unwrap_or_default(),
                    "value": captured.edit.value,
                    "automatable": info.map(|info| info.automatable).unwrap_or(true),
                    "plugin_name": captured.plugin_name,
                    "captured_at_millis": captured.edit.captured_at_millis,
                })
            })
            .collect::<Vec<_>>()
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

struct BounceCommandParams {
    path: String,
    format: Option<String>,
    start: f64,
    end: f64,
    track_ids: Option<Vec<u32>>,
    sample_rate: Option<u32>,
    bit_depth: Option<String>,
    buffer_size: Option<usize>,
}

struct BounceRequest {
    path: PathBuf,
    format: String,
    start_sample: i64,
    end_sample: i64,
    track_ids: Option<Vec<u32>>,
    sample_rate: u32,
    bit_depth: WavBitDepth,
    buffer_size: usize,
}

struct BounceStats {
    path: PathBuf,
    format: String,
    sample_rate: u32,
    bit_depth: WavBitDepth,
    frames: u64,
    channels: u16,
    bytes: u64,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum WavBitDepth {
    I16,
    I24,
    F32,
}

impl WavBitDepth {
    fn parse(value: &str) -> Option<Self> {
        match value {
            "i16" => Some(Self::I16),
            "i24" => Some(Self::I24),
            "f32" => Some(Self::F32),
            _ => None,
        }
    }

    fn as_str(self) -> &'static str {
        match self {
            Self::I16 => "i16",
            Self::I24 => "i24",
            Self::F32 => "f32",
        }
    }

    fn audio_format(self) -> u16 {
        match self {
            Self::F32 => 3,
            Self::I16 | Self::I24 => 1,
        }
    }

    fn bits_per_sample(self) -> u16 {
        match self {
            Self::I16 => 16,
            Self::I24 => 24,
            Self::F32 => 32,
        }
    }

    fn bytes_per_sample(self) -> u16 {
        self.bits_per_sample() / 8
    }
}

struct WavWriter {
    writer: BufWriter<File>,
    bit_depth: WavBitDepth,
}

impl WavWriter {
    fn create(
        path: &Path,
        sample_rate: u32,
        channels: u16,
        bit_depth: WavBitDepth,
        frames: u64,
    ) -> Result<Self, String> {
        let bytes_per_sample = u64::from(bit_depth.bytes_per_sample());
        let data_bytes = frames
            .checked_mul(u64::from(channels))
            .and_then(|value| value.checked_mul(bytes_per_sample))
            .ok_or_else(|| "wav file is too large".to_string())?;
        let riff_size = 36u64
            .checked_add(data_bytes)
            .ok_or_else(|| "wav file is too large".to_string())?;
        if riff_size > u64::from(u32::MAX) || data_bytes > u64::from(u32::MAX) {
            return Err("wav file is too large".to_string());
        }

        if let Some(parent) = path.parent()
            && !parent.as_os_str().is_empty()
        {
            std::fs::create_dir_all(parent)
                .map_err(|err| format!("failed to create output directory: {err}"))?;
        }

        let file = File::create(path).map_err(|err| format!("failed to create wav file: {err}"))?;
        let mut writer = BufWriter::new(file);
        writer
            .write_all(b"RIFF")
            .and_then(|_| writer.write_all(&(riff_size as u32).to_le_bytes()))
            .and_then(|_| writer.write_all(b"WAVE"))
            .and_then(|_| writer.write_all(b"fmt "))
            .and_then(|_| writer.write_all(&16u32.to_le_bytes()))
            .and_then(|_| writer.write_all(&bit_depth.audio_format().to_le_bytes()))
            .and_then(|_| writer.write_all(&channels.to_le_bytes()))
            .and_then(|_| writer.write_all(&sample_rate.to_le_bytes()))
            .and_then(|_| {
                let byte_rate =
                    sample_rate * u32::from(channels) * u32::from(bit_depth.bytes_per_sample());
                writer.write_all(&byte_rate.to_le_bytes())
            })
            .and_then(|_| {
                let block_align = channels * bit_depth.bytes_per_sample();
                writer.write_all(&block_align.to_le_bytes())
            })
            .and_then(|_| writer.write_all(&bit_depth.bits_per_sample().to_le_bytes()))
            .and_then(|_| writer.write_all(b"data"))
            .and_then(|_| writer.write_all(&(data_bytes as u32).to_le_bytes()))
            .map_err(|err| format!("failed to write wav header: {err}"))?;

        Ok(Self { writer, bit_depth })
    }

    fn write_samples(&mut self, samples: &[f32]) -> Result<(), String> {
        for sample in samples {
            let sample = finite_clamped_sample(*sample);
            match self.bit_depth {
                WavBitDepth::I16 => {
                    let value = if sample <= -1.0 {
                        i16::MIN
                    } else {
                        (sample * f32::from(i16::MAX)).round() as i16
                    };
                    self.writer
                        .write_all(&value.to_le_bytes())
                        .map_err(|err| format!("failed to write wav samples: {err}"))?;
                }
                WavBitDepth::I24 => {
                    let value = if sample <= -1.0 {
                        -8_388_608
                    } else {
                        (sample * 8_388_607.0).round() as i32
                    };
                    let bytes = value.to_le_bytes();
                    self.writer
                        .write_all(&bytes[..3])
                        .map_err(|err| format!("failed to write wav samples: {err}"))?;
                }
                WavBitDepth::F32 => {
                    self.writer
                        .write_all(&sample.to_le_bytes())
                        .map_err(|err| format!("failed to write wav samples: {err}"))?;
                }
            }
        }
        Ok(())
    }

    fn finish(mut self) -> Result<(), String> {
        self.writer
            .flush()
            .map_err(|err| format!("failed to finish wav file: {err}"))
    }
}

fn finite_clamped_sample(sample: f32) -> f32 {
    if sample.is_finite() {
        sample.clamp(-1.0, 1.0)
    } else {
        0.0
    }
}

fn bounce_command(
    cmd_name: &str,
    engine: &Arc<Mutex<AudioEngine>>,
    host_config: &HostConfig,
    params: BounceCommandParams,
) -> CommandResponse {
    let format = params
        .format
        .unwrap_or_else(|| "wav".to_string())
        .trim()
        .to_ascii_lowercase();
    if format != "wav" {
        return CommandResponse::error(Some(cmd_name), "format is not supported");
    }

    let path = params.path.trim();
    if path.is_empty() {
        return CommandResponse::error(Some(cmd_name), "path is required");
    }

    let (current_sample_rate, current_buffer_size) = {
        let eng = engine.lock().unwrap();
        (eng.sample_rate(), eng.buffer_size())
    };
    let sample_rate = params.sample_rate.unwrap_or(current_sample_rate);
    let buffer_size = params.buffer_size.unwrap_or(current_buffer_size);
    if sample_rate == 0 {
        return CommandResponse::error(Some(cmd_name), "sample_rate must be positive");
    }
    if buffer_size == 0 {
        return CommandResponse::error(Some(cmd_name), "buffer_size must be positive");
    }

    let bit_depth_value = params
        .bit_depth
        .unwrap_or_else(|| host_config.audio_host.bit_depth.clone())
        .trim()
        .to_ascii_lowercase();
    let Some(bit_depth) = WavBitDepth::parse(&bit_depth_value) else {
        return CommandResponse::error(Some(cmd_name), "bit_depth is not supported");
    };

    let start_sample = match seconds_to_samples(sample_rate, params.start) {
        Ok(samples) => samples,
        Err(err) => return CommandResponse::error(Some(cmd_name), err),
    };
    let end_sample = match seconds_to_samples(sample_rate, params.end) {
        Ok(samples) => samples,
        Err(err) => return CommandResponse::error(Some(cmd_name), err),
    };
    if end_sample <= start_sample {
        return CommandResponse::error(Some(cmd_name), "end must be after start");
    }
    if matches!(params.track_ids.as_ref(), Some(track_ids) if track_ids.is_empty()) {
        return CommandResponse::error(Some(cmd_name), "track_ids must not be empty");
    }

    let request = BounceRequest {
        path: PathBuf::from(path),
        format,
        start_sample,
        end_sample,
        track_ids: params.track_ids,
        sample_rate,
        bit_depth,
        buffer_size,
    };

    match render_bounce_wav(engine, &request) {
        Ok(stats) => CommandResponse::ack_with(
            cmd_name,
            json!({
                "path": stats.path,
                "format": stats.format,
                "sample_rate": stats.sample_rate,
                "bit_depth": stats.bit_depth.as_str(),
                "frames": stats.frames,
                "duration_seconds": stats.frames as f64 / f64::from(stats.sample_rate),
                "channels": stats.channels,
                "bytes": stats.bytes,
            }),
        ),
        Err(err) => CommandResponse::error(Some(cmd_name), &err),
    }
}

fn render_bounce_wav(
    engine: &Arc<Mutex<AudioEngine>>,
    request: &BounceRequest,
) -> Result<BounceStats, String> {
    let frames = u64::try_from(request.end_sample - request.start_sample)
        .map_err(|_| "end must be after start".to_string())?;
    let mut engine = engine.lock().unwrap();
    let original_sample_rate = engine.sample_rate();
    let original_buffer_size = engine.buffer_size();
    let original_session = engine.with_session(|session| session.capture_render_state())?;

    if let Some(track_ids) = &request.track_ids {
        let missing = engine.with_session(|session| {
            track_ids
                .iter()
                .copied()
                .find(|track_id| !session.has_route(*track_id))
        });
        if let Some(track_id) = missing {
            return Err(format!("track not found: {track_id}"));
        }
    }

    let temp_path = bounce_temp_path(&request.path);
    let writer = match WavWriter::create(
        &temp_path,
        request.sample_rate,
        2,
        request.bit_depth,
        frames,
    ) {
        Ok(writer) => writer,
        Err(err) => {
            let _ = std::fs::remove_file(&temp_path);
            return Err(err);
        }
    };

    if original_sample_rate != request.sample_rate || original_buffer_size != request.buffer_size {
        engine.reconfigure(request.sample_rate, request.buffer_size);
    }

    let render_result =
        engine.with_session(|session| render_session_to_wav(session, request, writer));

    if engine.sample_rate() != original_sample_rate || engine.buffer_size() != original_buffer_size
    {
        engine.reconfigure(original_sample_rate, original_buffer_size);
    }
    engine.with_session(|session| session.restore_render_state(&original_session))?;

    match cleanup_failed_bounce_temp_file(&temp_path, render_result) {
        Ok(stats) => {
            publish_bounce_temp_file(&temp_path, &request.path)?;
            Ok(stats)
        }
        Err(err) => Err(err),
    }
}

fn bounce_temp_path(path: &Path) -> PathBuf {
    let parent = path.parent().unwrap_or_else(|| Path::new(""));
    let file_name = path
        .file_name()
        .map(|value| value.to_string_lossy())
        .unwrap_or_else(|| "bounce.wav".into());
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    parent.join(format!(".{file_name}.tmp-{}-{nanos}", std::process::id()))
}

fn cleanup_failed_bounce_temp_file<T>(
    temp_path: &Path,
    result: Result<T, String>,
) -> Result<T, String> {
    if result.is_err() {
        let _ = std::fs::remove_file(temp_path);
    }
    result
}

fn publish_bounce_temp_file(temp_path: &Path, path: &Path) -> Result<(), String> {
    match std::fs::rename(temp_path, path) {
        Ok(()) => Ok(()),
        Err(_first_err) if path.exists() => {
            if let Err(err) = std::fs::remove_file(path) {
                let _ = std::fs::remove_file(temp_path);
                return Err(format!("failed to replace wav file: {err}"));
            }
            if let Err(err) = std::fs::rename(temp_path, path) {
                let _ = std::fs::remove_file(temp_path);
                return Err(format!("failed to publish wav file: {err}"));
            }
            Ok(())
        }
        Err(err) => {
            let _ = std::fs::remove_file(temp_path);
            Err(format!("failed to publish wav file: {err}"))
        }
    }
}

fn render_session_to_wav(
    session: &mut Session,
    request: &BounceRequest,
    mut writer: WavWriter,
) -> Result<BounceStats, String> {
    if let Some(track_ids) = &request.track_ids {
        let snapshots = session.route_snapshots();
        for route in snapshots {
            let _ = session.set_track_solo(route.id, track_ids.contains(&route.id));
        }
    }

    session.transport.loop_start = None;
    session.transport.loop_end = None;
    session.transport.seek(request.start_sample);
    session.transport.play();

    let mut remaining = u64::try_from(request.end_sample - request.start_sample)
        .map_err(|_| "end must be after start".to_string())?;
    let frames = remaining;
    let mut buffer = Vec::new();
    while remaining > 0 {
        let nframes = remaining.min(session.buffer_size.max(1) as u64) as usize;
        buffer.resize(nframes * 2, 0.0);
        session.process(&mut buffer);
        writer.write_samples(&buffer)?;
        remaining -= nframes as u64;
    }
    writer.finish()?;

    let bytes = frames
        .checked_mul(2)
        .and_then(|value| value.checked_mul(u64::from(request.bit_depth.bytes_per_sample())))
        .ok_or_else(|| "wav file is too large".to_string())?;

    Ok(BounceStats {
        path: request.path.clone(),
        format: request.format.clone(),
        sample_rate: request.sample_rate,
        bit_depth: request.bit_depth,
        frames,
        channels: 2,
        bytes,
    })
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
        let streamer = Arc::new(Mutex::new(AudioStreamer::with_enabled_flag(
            48_000,
            2,
            Arc::new(std::sync::atomic::AtomicBool::new(true)),
        )));
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

    struct RenderStateProcessor {
        parameter: f32,
        run_count: u32,
    }

    impl Processor for RenderStateProcessor {
        fn name(&self) -> &str {
            "render-state-processor"
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
            self.run_count += 1;
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
        fn get_state_chunk(&mut self) -> Result<Vec<u8>, String> {
            Ok(self.run_count.to_le_bytes().to_vec())
        }
        fn set_state_chunk(&mut self, chunk: &[u8]) -> Result<(), String> {
            if chunk.len() != 4 {
                return Err("invalid state chunk".to_string());
            }
            self.run_count = u32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]);
            Ok(())
        }
        fn get_parameter(&mut self, index: u32) -> Option<f32> {
            (index == 0).then_some(self.parameter)
        }
        fn set_parameter(&mut self, index: u32, value: f32) -> Result<(), String> {
            if index != 0 {
                return Err("parameter out of range".to_string());
            }
            self.parameter = value;
            Ok(())
        }
        fn parameter_count(&mut self) -> u32 {
            1
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
    fn set_streaming_updates_shared_enabled_flag() {
        let engine = Arc::new(Mutex::new(AudioEngine::new(48_000, 128)));
        let (cmd_tx, _cmd_rx) = crossbeam::channel::unbounded();
        let enabled = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let streamer = Arc::new(Mutex::new(AudioStreamer::with_enabled_flag(
            48_000,
            2,
            Arc::clone(&enabled),
        )));
        let config = HostConfig::default();

        let response = execute(
            Command::SetStreaming { enabled: true },
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );

        assert!(matches!(response, CommandResponse::Ack { cmd, .. } if cmd == "set_streaming"));
        assert!(enabled.load(std::sync::atomic::Ordering::Relaxed));
    }

    #[test]
    fn status_reports_streaming_enabled_flag() {
        let (engine, cmd_tx, _cmd_rx, streamer, config) = command_context();

        execute(
            Command::SetStreaming { enabled: true },
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );
        let response = execute(
            Command::GetStatus,
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );

        assert!(matches!(
            response,
            CommandResponse::Status {
                streaming_enabled: true,
                ..
            }
        ));
    }

    #[test]
    fn set_tempo_rejects_invalid_bpm_without_sending_command() {
        for bpm in [
            -1.0,
            0.0,
            0.999,
            1000.0,
            f64::NAN,
            f64::INFINITY,
            f64::NEG_INFINITY,
        ] {
            let (engine, cmd_tx, cmd_rx, streamer, config) = command_context();

            let response = execute(
                Command::SetTempo {
                    bpm,
                    time_sig: Some((4, 4)),
                },
                &engine,
                &cmd_tx,
                &streamer,
                &config,
                None,
            );

            assert!(
                matches!(response, CommandResponse::Error { cmd: Some(cmd), message }
                    if cmd == "set_tempo" && message == "bpm must be between 1 and 999")
            );
            assert!(cmd_rx.try_recv().is_err());
        }
    }

    #[test]
    fn set_tempo_rejects_invalid_time_signature_without_sending_command() {
        for time_sig in [(0, 4), (4, 0), (4, 3), (4, 128)] {
            let (engine, cmd_tx, cmd_rx, streamer, config) = command_context();

            let response = execute(
                Command::SetTempo {
                    bpm: 120.0,
                    time_sig: Some(time_sig),
                },
                &engine,
                &cmd_tx,
                &streamer,
                &config,
                None,
            );

            assert!(
                matches!(response, CommandResponse::Error { cmd: Some(cmd), message }
                    if cmd == "set_tempo"
                        && message == "time_sig must have a positive numerator and a denominator of 1, 2, 4, 8, 16, 32, or 64")
            );
            assert!(cmd_rx.try_recv().is_err());
        }
    }

    #[test]
    fn set_tempo_accepts_valid_bounds_and_time_signature() {
        let (engine, cmd_tx, cmd_rx, streamer, config) = command_context();

        let response = execute(
            Command::SetTempo {
                bpm: 999.0,
                time_sig: Some((7, 8)),
            },
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );

        assert!(matches!(response, CommandResponse::Ack { cmd, .. } if cmd == "set_tempo"));
        match cmd_rx.try_recv().unwrap() {
            AppCommand::SetTempo { bpm, time_sig } => {
                assert_eq!(bpm, 999.0);
                assert_eq!(time_sig, (7, 8));
            }
            other => panic!("unexpected command: {other:?}"),
        }
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
    fn load_vst3_rejects_missing_track_before_loading_plugin() {
        let (engine, cmd_tx, _cmd_rx, streamer, config) = command_context();

        let response = execute(
            Command::LoadVst3 {
                track_id: u32::MAX,
                path: "missing-plugin.vst3".to_string(),
                name: None,
                slot_index: None,
            },
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );

        assert!(
            matches!(&response, CommandResponse::Error { cmd: Some(cmd), message }
                if cmd == "load_vst3" && message == "track not found"),
            "unexpected response: {response:?}"
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
            (session.route_count(), session.automation_lane_count())
        });
        assert_eq!(route_count, 1);
        assert_eq!(lane_count, 1);
    }

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

    #[test]
    fn set_route_config_error_does_not_partially_update_kind() {
        let (engine, cmd_tx, _cmd_rx, streamer, config) = command_context();
        let track = with_session(&engine, |session| session.add_track("Lead".to_string()));
        let non_bus_target =
            with_session(&engine, |session| session.add_track("Audio".to_string()));

        let response = execute(
            Command::SetRouteConfig {
                track_id: track,
                kind: Some(RouteKindData::Bus),
                output_track_id: Some(non_bus_target),
            },
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );

        assert!(
            matches!(response, CommandResponse::Error { cmd: Some(cmd), .. } if cmd == "set_route_config")
        );
        assert_eq!(
            with_session(&engine, |session| session.route_kind(track)),
            Some(RouteKind::Track)
        );
        assert_eq!(
            with_session(&engine, |session| session.route_output(track)),
            Some(None)
        );
    }

    #[test]
    fn set_route_sends_updates_send_targets() {
        let (engine, cmd_tx, _cmd_rx, streamer, config) = command_context();
        let track = with_session(&engine, |session| session.add_track("Lead".to_string()));
        let bus = with_session(&engine, |session| session.add_bus("FX".to_string()));

        let response = execute(
            Command::SetRouteSends {
                track_id: track,
                sends: vec![RouteSendData {
                    target_track_id: bus,
                    level: 0.5,
                    enabled: true,
                }],
            },
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );

        assert!(matches!(response, CommandResponse::Ack { cmd, .. } if cmd == "set_route_sends"));
        assert_eq!(
            with_session(&engine, |session| session.route_sends(track)),
            Some(vec![RouteSend {
                target_track_id: bus,
                level: 0.5,
                enabled: true,
            }])
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

    fn unique_test_wav(name: &str) -> PathBuf {
        let mut path = std::env::temp_dir();
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        path.push(format!("atri-{name}-{}-{nanos}.wav", std::process::id()));
        path
    }

    #[test]
    fn render_wav_writes_pcm_file_and_restores_session_state() {
        let (engine, cmd_tx, _cmd_rx, streamer, config) = command_context();
        let (track_a, track_b) = with_session(&engine, |session| {
            let track_a = session.add_track("Lead".to_string());
            let track_b = session.add_track("Pad".to_string());
            assert!(session.set_track_solo(track_b, true));
            session.transport.state = atri_engine::transport::TransportState::Playing;
            session.transport.position = 1_234;
            session.transport.speed = 1.0;
            session.transport.loop_start = Some(128);
            session.transport.loop_end = Some(2_048);
            (track_a, track_b)
        });
        let path = unique_test_wav("render-wav");

        let response = handle_command(
            &json!({
                "cmd": "render_wav",
                "path": path.to_string_lossy(),
                "start": 0.0,
                "end": 0.01,
                "track_ids": [track_a],
                "sample_rate": 44_100,
                "bit_depth": "i24"
            }),
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );

        assert!(matches!(
            response,
            CommandResponse::Ack {
                cmd,
                data: Some(data),
                ..
            } if cmd == "render_wav"
                && data["format"] == "wav"
                && data["sample_rate"] == 44_100
                && data["bit_depth"] == "i24"
                && data["frames"] == 441
                && data["channels"] == 2
        ));
        let bytes = std::fs::read(&path).expect("rendered wav should exist");
        let _ = std::fs::remove_file(&path);
        assert_eq!(&bytes[0..4], b"RIFF");
        assert_eq!(&bytes[8..12], b"WAVE");
        assert_eq!(&bytes[12..16], b"fmt ");
        assert_eq!(u16::from_le_bytes([bytes[20], bytes[21]]), 1);
        assert_eq!(u16::from_le_bytes([bytes[22], bytes[23]]), 2);
        assert_eq!(
            u32::from_le_bytes([bytes[24], bytes[25], bytes[26], bytes[27]]),
            44_100
        );
        assert_eq!(u16::from_le_bytes([bytes[34], bytes[35]]), 24);
        assert_eq!(&bytes[36..40], b"data");
        assert!(bytes.len() > 44);

        let restored = with_session(&engine, |session| {
            let solos = session
                .route_snapshots()
                .into_iter()
                .map(|route| (route.id, route.solo))
                .collect::<Vec<_>>();
            (
                session.sample_rate,
                session.buffer_size,
                session.transport.state,
                session.transport.position,
                session.transport.speed,
                session.transport.loop_start,
                session.transport.loop_end,
                solos,
            )
        });
        assert_eq!(engine.lock().unwrap().sample_rate(), 48_000);
        assert_eq!(engine.lock().unwrap().buffer_size(), 128);
        assert_eq!(restored.0, 48_000);
        assert_eq!(restored.1, 128);
        assert_eq!(restored.2, atri_engine::transport::TransportState::Playing);
        assert_eq!(restored.3, 1_234);
        assert_eq!(restored.4, 1.0);
        assert_eq!(restored.5, Some(128));
        assert_eq!(restored.6, Some(2_048));
        assert_eq!(restored.7, vec![(track_a, false), (track_b, true)]);
    }

    #[test]
    fn render_wav_restores_automation_and_processor_render_state() {
        let (engine, cmd_tx, _cmd_rx, streamer, config) = command_context();
        let track_id = with_session(&engine, |session| {
            let track_id = session.add_track("Automated".to_string());
            assert!(session.set_track_volume(track_id, 0.25));
            assert!(session.set_track_pan(track_id, -0.5));
            assert!(session.set_processor_slot(
                track_id,
                0,
                Some(Arc::new(Mutex::new(RenderStateProcessor {
                    parameter: 0.2,
                    run_count: 0,
                }))),
            ));
            session.set_automation_lanes(vec![
                AutomationLane {
                    target: AutomationTarget::TrackVolume { track_id },
                    points: vec![AutomationPoint {
                        beat: 0.0,
                        value: 0.9,
                        curve: AutomationCurve::Hold,
                    }],
                    muted: false,
                },
                AutomationLane {
                    target: AutomationTarget::TrackPan { track_id },
                    points: vec![AutomationPoint {
                        beat: 0.0,
                        value: 0.45,
                        curve: AutomationCurve::Hold,
                    }],
                    muted: false,
                },
                AutomationLane {
                    target: AutomationTarget::PluginParameter {
                        track_id,
                        slot_index: 0,
                        param_index: 0,
                    },
                    points: vec![AutomationPoint {
                        beat: 0.0,
                        value: 0.75,
                        curve: AutomationCurve::Hold,
                    }],
                    muted: false,
                },
                AutomationLane {
                    target: AutomationTarget::TempoBpm,
                    points: vec![AutomationPoint {
                        beat: 0.0,
                        value: 150.0,
                        curve: AutomationCurve::Hold,
                    }],
                    muted: false,
                },
                AutomationLane {
                    target: AutomationTarget::TimeSignatureNumerator,
                    points: vec![AutomationPoint {
                        beat: 0.0,
                        value: 7.0,
                        curve: AutomationCurve::Hold,
                    }],
                    muted: false,
                },
            ]);
            track_id
        });
        let path = unique_test_wav("render-state");

        let response = handle_command(
            &json!({
                "cmd": "render_wav",
                "path": path.to_string_lossy(),
                "start": 0.0,
                "end": 0.01,
                "sample_rate": 48_000,
                "bit_depth": "f32"
            }),
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );
        let _ = std::fs::remove_file(&path);

        assert!(matches!(response, CommandResponse::Ack { cmd, .. } if cmd == "render_wav"));
        with_session(&engine, |session| {
            let route = session
                .route_snapshots()
                .into_iter()
                .find(|route| route.id == track_id)
                .unwrap();
            assert_eq!(route.volume, 1.0);
            assert_eq!(route.volume_target, 0.25);
            assert_eq!(route.pan, -0.5);
            let tempo_map = session.tempo_map.read();
            assert_eq!(tempo_map.current_tempo().bpm, 120.0);
            assert_eq!(tempo_map.current_meter().num, 4);
        });
        let processor =
            processor_slot(&engine, track_id, 0).expect("processor should remain loaded");
        let mut processor = processor.lock().unwrap();
        assert_eq!(processor.get_parameter(0), Some(0.2));
        assert_eq!(processor.get_state_chunk().unwrap(), 0u32.to_le_bytes());
    }

    #[test]
    fn bounce_rejects_invalid_format_range_and_track_ids() {
        let (engine, cmd_tx, _cmd_rx, streamer, config) = command_context();
        let path = unique_test_wav("bounce-invalid");

        let unsupported = handle_command(
            &json!({
                "cmd": "bounce",
                "path": path.to_string_lossy(),
                "format": "ogg",
                "start": 0.0,
                "end": 1.0
            }),
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );
        assert!(
            matches!(unsupported, CommandResponse::Error { cmd: Some(cmd), message }
                if cmd == "bounce" && message == "format is not supported")
        );

        let bad_range = handle_command(
            &json!({
                "cmd": "bounce",
                "path": path.to_string_lossy(),
                "format": "wav",
                "start": 2.0,
                "end": 1.0
            }),
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );
        assert!(
            matches!(bad_range, CommandResponse::Error { cmd: Some(cmd), message }
                if cmd == "bounce" && message == "end must be after start")
        );

        let missing_track = handle_command(
            &json!({
                "cmd": "bounce",
                "path": path.to_string_lossy(),
                "format": "wav",
                "start": 0.0,
                "end": 1.0,
                "track_ids": [999]
            }),
            &engine,
            &cmd_tx,
            &streamer,
            &config,
            None,
        );
        assert!(
            matches!(missing_track, CommandResponse::Error { cmd: Some(cmd), message }
                if cmd == "bounce" && message == "track not found: 999")
        );
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn failed_bounce_render_removes_temp_file_and_preserves_existing_output() {
        let path = unique_test_wav("bounce-failed-cleanup");
        let temp_path = bounce_temp_path(&path);
        std::fs::write(&path, b"previous valid wav").unwrap();
        std::fs::write(&temp_path, b"RIFF partial render").unwrap();

        let result =
            cleanup_failed_bounce_temp_file(&temp_path, Err::<(), _>("render failed".to_string()));

        assert_eq!(result.unwrap_err(), "render failed");
        assert_eq!(std::fs::read(&path).unwrap(), b"previous valid wav");
        assert!(!temp_path.exists());
        let _ = std::fs::remove_file(&path);
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
    streamer: &Arc<Mutex<AudioStreamer>>,
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

    let streaming_enabled = streamer
        .lock()
        .map(|streamer| streamer.is_enabled())
        .unwrap_or(false);

    with_session(engine, |session| {
        let tempo_map = session.tempo_map.read();
        let tracks = session
            .route_snapshots()
            .into_iter()
            .map(|route| TrackStatus {
                id: route.id,
                name: route.name,
                kind: match route.kind {
                    RouteKind::Track => "track".to_string(),
                    RouteKind::Bus => "bus".to_string(),
                },
                output_track_id: route.output_track_id,
                sends: route
                    .sends
                    .into_iter()
                    .map(|send| RouteSendStatus {
                        target_track_id: send.target_track_id,
                        level: send.level,
                        enabled: send.enabled,
                    })
                    .collect(),
                volume: route.volume,
                pan: route.pan,
                mute: route.mute,
                solo: route.solo,
                note_count: route.note_count,
                midi_event_count: route.midi_event_count,
                audio_clip_count: route.audio_clip_count,
                processors: route.processors,
                processor_slots: route.processor_slots,
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
            streaming_enabled,
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
