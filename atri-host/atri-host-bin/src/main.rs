mod commands;
mod config;
mod driver;
mod editor_host;
mod ipc;
mod stream;

use std::sync::{Arc, Mutex};
use std::thread;

use atri_engine::engine::AudioEngine;
use crossbeam::channel;

use driver::AudioDriver;
use stream::AudioStreamer;

const DEFAULT_SAMPLE_RATE: u32 = 48_000;
const DEFAULT_BUFFER_SIZE: usize = 256;

fn main() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info"))
        .target(env_logger::Target::Stderr)
        .init();

    eprintln!("[atri-host] ATRI Audio Host v0.1.0");
    eprintln!(
        "[atri-host] requested sample_rate={}, buffer_size={}",
        DEFAULT_SAMPLE_RATE, DEFAULT_BUFFER_SIZE
    );

    let stdout = Arc::new(Mutex::new(std::io::stdout()));
    let engine = Arc::new(Mutex::new(AudioEngine::new(
        DEFAULT_SAMPLE_RATE,
        DEFAULT_BUFFER_SIZE,
    )));

    let (cmd_tx, cmd_rx) = channel::unbounded::<commands::AppCommand>();
    let (audio_tx, audio_rx) = channel::bounded::<driver::AudioBlock>(8);
    let host_config = Arc::new(config::HostConfig::load());

    let (driver, config) = AudioDriver::start(
        Arc::clone(&engine),
        cmd_rx,
        audio_tx,
        DEFAULT_SAMPLE_RATE,
        DEFAULT_BUFFER_SIZE,
    );
    eprintln!(
        "[atri-host] streaming sample_rate={}, buffer_size={}",
        config.sample_rate, config.buffer_size
    );
    let streamer = Arc::new(Mutex::new(AudioStreamer::new(config.sample_rate, 2)));
    let (editor_manager, editor_runtime) =
        match editor_host::EditorWindowManager::start_on_main_thread() {
            Ok((manager, runtime)) => (Some(Arc::new(manager)), Some(runtime)),
            Err(err) => {
                log::warn!("plugin editor windows are unavailable: {err}");
                (None, None)
            }
        };

    let streamer_thread = {
        let streamer = Arc::clone(&streamer);
        let stdout = Arc::clone(&stdout);
        thread::spawn(move || {
            while let Ok(block) = audio_rx.recv() {
                if let Ok(mut streamer) = streamer.lock() {
                    let _ = streamer.write_chunk(&stdout, block.data(), block.frames());
                }
            }
        })
    };

    let ipc_thread = {
        let engine = Arc::clone(&engine);
        let streamer = Arc::clone(&streamer);
        let stdout = Arc::clone(&stdout);
        let host_config = Arc::clone(&host_config);
        let editor_manager = editor_manager.clone();
        thread::spawn(move || {
            ipc::run_ipc_loop(
                engine,
                cmd_tx,
                streamer,
                stdout,
                host_config,
                editor_manager.clone(),
            );
            if let Some(manager) = editor_manager {
                manager.shutdown();
            }
        })
    };

    if let Some(runtime) = editor_runtime {
        eprintln!("[atri-host] plugin editor event loop running on main thread");
        if let Err(err) = runtime.run() {
            log::error!("{err}");
        }
    }

    let _ = ipc_thread.join();
    driver.stop();
    let _ = streamer_thread.join();
    eprintln!("[atri-host] Shutdown complete");
}
