use std::sync::{Arc, Mutex};
use crossbeam::channel::Sender;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use atri_engine::engine::AudioEngine;
use atri_core::midi::note::MidiNote;

use crate::stream::AudioStreamer;

#[derive(Debug, Clone)]
pub enum AppCommand {
    Play,
    Stop,
    Pause,
    Seek(i64),
    SetTempo { bpm: f64, time_sig: (u8, u8) },
    Shutdown,
}

#[derive(Debug, Serialize)]
#[serde(untagged)]
pub enum CommandResponse {
    Ack { r#type: String, cmd: String, status: String, data: Option<Value> },
    Error { r#type: String, cmd: Option<String>, message: String },
    Status { r#type: String, transport: String, position: f64, tempo: f64 },
    Shutdown,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case", tag = "cmd")]
pub enum Command {
    Play,
    Stop,
    Pause,
    Seek { position: f64 },
    AddTrack { name: String },
    RemoveTrack { id: u32 },
    SetMidi { track_id: u32, notes: Vec<MidiNoteData> },
    SetTempo { bpm: f64, time_sig: Option<(u8, u8)> },
    SetVolume { track_id: u32, value: f64 },
    SetPan { track_id: u32, value: f64 },
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
    cmd_str: &str,
    raw: &Value,
    engine: &Arc<Mutex<AudioEngine>>,
    cmd_tx: &Sender<AppCommand>,
    _streamer: &Arc<Mutex<AudioStreamer>>,
) -> CommandResponse {
    match cmd_str {
        "play" => {
            cmd_tx.send(AppCommand::Play).ok();
            CommandResponse::ack("play")
        }
        "stop" => {
            cmd_tx.send(AppCommand::Stop).ok();
            CommandResponse::ack("stop")
        }
        "pause" => {
            cmd_tx.send(AppCommand::Pause).ok();
            CommandResponse::ack("pause")
        }
        "seek" => {
            if let Some(pos) = raw.get("position").and_then(|v| v.as_f64()) {
                let eng = engine.lock().unwrap();
                let samples = (pos * eng.sample_rate() as f64) as i64;
                cmd_tx.send(AppCommand::Seek(samples)).ok();
                CommandResponse::ack("seek")
            } else {
                CommandResponse::error(Some("seek"), "missing 'position' field")
            }
        }
        "add_track" => {
            if let Some(name) = raw.get("name").and_then(|v| v.as_str()) {
                let eng = engine.lock().unwrap();
                let mut session = eng.session.lock().unwrap();
                let id = session.add_track(name.to_string());
                CommandResponse::ack_with("add_track", json!({"track_id": id}))
            } else {
                CommandResponse::error(Some("add_track"), "missing 'name' field")
            }
        }
        "remove_track" => {
            if let Some(_id) = raw.get("id").and_then(|v| v.as_u64()) {
                // Phase 1: mark for removal (full implementation in Step 4)
                CommandResponse::ack("remove_track")
            } else {
                CommandResponse::error(Some("remove_track"), "missing 'id' field")
            }
        }
        "set_midi" => {
            if let (Some(track_id), Some(notes_val)) = (
                raw.get("track_id").and_then(|v| v.as_u64()),
                raw.get("notes"),
            ) {
                if let Ok(notes) = serde_json::from_value::<Vec<MidiNoteData>>(notes_val.clone()) {
                    let eng = engine.lock().unwrap();
                    let mut session = eng.session.lock().unwrap();
                    let midi_notes: Vec<MidiNote> = notes
                        .into_iter()
                        .map(|n| MidiNote::new(n.pitch, n.start, n.duration, n.velocity))
                        .collect();
                    session.set_track_notes(track_id as u32, midi_notes);
                    CommandResponse::ack("set_midi")
                } else {
                    CommandResponse::error(Some("set_midi"), "invalid 'notes' format")
                }
            } else {
                CommandResponse::error(Some("set_midi"), "missing 'track_id' or 'notes'")
            }
        }
        "set_tempo" => {
            if let Some(bpm) = raw.get("bpm").and_then(|v| v.as_f64()) {
                let time_sig = raw.get("time_sig")
                    .and_then(|v| {
                        let arr = v.as_array()?;
                        Some((arr.first()?.as_u64()? as u8, arr.get(1)?.as_u64()? as u8))
                    })
                    .unwrap_or((4, 4));
                cmd_tx.send(AppCommand::SetTempo { bpm, time_sig }).ok();
                CommandResponse::ack("set_tempo")
            } else {
                CommandResponse::error(Some("set_tempo"), "missing 'bpm' field")
            }
        }
        "set_volume" => {
            if let (Some(track_id), Some(value)) = (
                raw.get("track_id").and_then(|v| v.as_u64()),
                raw.get("value").and_then(|v| v.as_f64()),
            ) {
                let eng = engine.lock().unwrap();
                let mut session = eng.session.lock().unwrap();
                session.set_track_volume(track_id as u32, value as f32);
                CommandResponse::ack("set_volume")
            } else {
                CommandResponse::error(Some("set_volume"), "missing 'track_id' or 'value'")
            }
        }
        "set_pan" => {
            if let (Some(track_id), Some(value)) = (
                raw.get("track_id").and_then(|v| v.as_u64()),
                raw.get("value").and_then(|v| v.as_f64()),
            ) {
                let eng = engine.lock().unwrap();
                let mut session = eng.session.lock().unwrap();
                session.set_track_pan(track_id as u32, value as f32);
                CommandResponse::ack("set_pan")
            } else {
                CommandResponse::error(Some("set_pan"), "missing 'track_id' or 'value'")
            }
        }
        "get_status" => {
            let eng = engine.lock().unwrap();
            let session = eng.session.lock().unwrap();
            let transport = &session.transport;
            let tempo_map = session.tempo_map.read();
            CommandResponse::Status {
                r#type: "status".to_string(),
                transport: format!("{:?}", transport.state).to_lowercase(),
                position: transport.position as f64 / session.sample_rate as f64,
                tempo: tempo_map.current_tempo().bpm,
            }
        }
        "shutdown" => {
            cmd_tx.send(AppCommand::Shutdown).ok();
            CommandResponse::Shutdown
        }
        "" => CommandResponse::error(None, "missing 'cmd' field"),
        _ => CommandResponse::error(Some(cmd_str), &format!("unknown command: {}", cmd_str)),
    }
}

impl CommandResponse {
    pub fn ack(cmd: &str) -> Self {
        CommandResponse::Ack {
            r#type: "ack".to_string(),
            cmd: cmd.to_string(),
            status: "ok".to_string(),
            data: None,
        }
    }

    pub fn ack_with(cmd: &str, data: Value) -> Self {
        CommandResponse::Ack {
            r#type: "ack".to_string(),
            cmd: cmd.to_string(),
            status: "ok".to_string(),
            data: Some(data),
        }
    }

    pub fn error(cmd: Option<&str>, message: &str) -> Self {
        CommandResponse::Error {
            r#type: "error".to_string(),
            cmd: cmd.map(|s| s.to_string()),
            message: message.to_string(),
        }
    }
}
