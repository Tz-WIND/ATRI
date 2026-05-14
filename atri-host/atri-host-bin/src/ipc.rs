use std::io::{BufRead, BufReader, Write};
use std::sync::{Arc, Mutex};
use crossbeam::channel::Sender;
use serde_json::Value;
use atri_engine::engine::AudioEngine;

use crate::commands::{handle_command, AppCommand, CommandResponse};
use crate::stream::AudioStreamer;

/// Runs the IPC main loop: reads JSON commands from stdin, dispatches them,
/// and writes JSON responses to stdout.
pub fn run_ipc_loop(
    engine: Arc<Mutex<AudioEngine>>,
    cmd_tx: Sender<AppCommand>,
    streamer: Arc<Mutex<AudioStreamer>>,
) {
    let stdin = std::io::stdin();
    let mut reader = BufReader::new(stdin.lock());
    let mut line = String::new();

    eprintln!("[atri-host] IPC loop started, waiting for commands...");

    loop {
        line.clear();
        match reader.read_line(&mut line) {
            Ok(0) => {
                eprintln!("[atri-host] stdin closed, shutting down");
                let _ = cmd_tx.send(AppCommand::Shutdown);
                break;
            }
            Ok(_) => {
                let trimmed = line.trim();
                if trimmed.is_empty() {
                    continue;
                }

                match serde_json::from_str::<Value>(trimmed) {
                    Ok(raw) => {
                        let cmd_str = raw.get("cmd").and_then(|v| v.as_str()).unwrap_or("");
                        let resp = handle_command(cmd_str, &raw, &engine, &cmd_tx, &streamer);
                        let mut stdout = std::io::stdout();
                        let resp_json = serde_json::to_string(&resp).unwrap_or_else(|e| {
                            format!(r#"{{"type":"error","message":"serialization failed: {}"}}"#, e)
                        });
                        writeln!(stdout, "{}", resp_json).ok();
                        stdout.flush().ok();

                        if matches!(resp, CommandResponse::Shutdown) {
                            break;
                        }
                    }
                    Err(e) => {
                        eprintln!("[atri-host] invalid JSON: {} — input: {}", e, trimmed);
                        let mut stdout = std::io::stdout();
                        let err = serde_json::json!({
                            "type": "error",
                            "message": format!("Invalid JSON: {}", e)
                        });
                        writeln!(stdout, "{}", err).ok();
                        stdout.flush().ok();
                    }
                }
            }
            Err(e) => {
                eprintln!("[atri-host] stdin read error: {}", e);
                break;
            }
        }
    }

    eprintln!("[atri-host] IPC loop ended");
}
