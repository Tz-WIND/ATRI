use std::io::{BufRead, BufReader};
use std::sync::{Arc, Mutex};

use atri_engine::engine::AudioEngine;
use crossbeam::channel::Sender;
use serde_json::Value;

use crate::commands::{AppCommand, handle_command};
use crate::config::HostConfig;
use crate::editor_host::EditorWindowManager;
use crate::stream::{AudioStreamer, SharedStdout, write_json};

/// Runs the IPC main loop: reads JSON commands from stdin, dispatches them,
/// and writes JSON responses to stdout.
pub fn run_ipc_loop(
    engine: Arc<Mutex<AudioEngine>>,
    cmd_tx: Sender<AppCommand>,
    streamer: Arc<Mutex<AudioStreamer>>,
    stdout: SharedStdout,
    host_config: Arc<HostConfig>,
    editor_manager: Option<Arc<EditorWindowManager>>,
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
                        let resp = handle_command(
                            &raw,
                            &engine,
                            &cmd_tx,
                            &streamer,
                            &host_config,
                            editor_manager.as_deref(),
                        );
                        write_json(&stdout, &resp).ok();

                        if resp.is_shutdown() {
                            break;
                        }
                    }
                    Err(e) => {
                        eprintln!("[atri-host] invalid JSON: {} — input: {}", e, trimmed);
                        let err = serde_json::json!({
                            "type": "error",
                            "message": format!("Invalid JSON: {}", e)
                        });
                        write_json(&stdout, &err).ok();
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
