mod commands;
mod config;
mod driver;
mod editor_host;
mod ipc;
mod stream;

use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex};
use std::thread;

use atri_engine::engine::AudioEngine;
use crossbeam::channel;

use driver::AudioDriver;
use stream::AudioStreamer;

fn main() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info"))
        .target(env_logger::Target::Stderr)
        .init();

    eprintln!("[atri-host] ATRI Audio Host v0.1.0");

    let mut host_config = config::HostConfig::load();
    let sample_rate = host_config.audio_host.sample_rate;
    let buffer_size = host_config.audio_host.buffer_size;
    eprintln!(
        "[atri-host] config sample_rate={}, buffer_size={}, engine={}, bit_depth={}",
        sample_rate,
        buffer_size,
        host_config.audio_host.audio_engine,
        host_config.audio_host.bit_depth
    );

    let stdout = Arc::new(Mutex::new(std::io::stdout()));
    let engine = Arc::new(Mutex::new(AudioEngine::new(sample_rate, buffer_size)));

    let (cmd_tx, cmd_rx) = channel::unbounded::<commands::AppCommand>();
    let (audio_tx, audio_rx) =
        channel::bounded::<driver::AudioBlock>(driver::AUDIO_BLOCK_POOL_SIZE);
    let streaming_enabled = Arc::new(AtomicBool::new(false));
    let audio_block_pool =
        driver::AudioBlockPool::new(driver::AUDIO_BLOCK_POOL_SIZE, buffer_size * 2);

    let (driver, config) = AudioDriver::start(
        Arc::clone(&engine),
        cmd_rx,
        audio_tx,
        Arc::clone(&streaming_enabled),
        audio_block_pool.clone(),
        sample_rate,
        buffer_size,
        host_config.audio_host.audio_engine.clone(),
        host_config.audio_host.bit_depth.clone(),
    );
    eprintln!(
        "[atri-host] streaming sample_rate={}, buffer_size={}",
        config.sample_rate, config.buffer_size
    );
    host_config.audio_host.sample_rate = config.sample_rate;
    host_config.audio_host.buffer_size = config.buffer_size;
    host_config.audio_host.audio_engine = config.audio_engine.clone();
    host_config.audio_host.bit_depth = config.bit_depth.clone();
    let host_config = Arc::new(host_config);
    let streamer = Arc::new(Mutex::new(AudioStreamer::with_enabled_flag(
        config.sample_rate,
        2,
        Arc::clone(&streaming_enabled),
    )));
    let (editor_manager, editor_runtime) =
        match editor_host::EditorWindowManager::start_on_main_thread() {
            Ok((manager, runtime)) => (Some(Arc::new(manager)), Some(runtime)),
            Err(err) => {
                log::warn!("plugin editor windows are unavailable: {err}");
                (None, None)
            }
        };

    let streamer_thread = {
        let audio_block_pool = audio_block_pool.clone();
        let pool_resize_rx = audio_block_pool.resize_requests();
        let streamer = Arc::clone(&streamer);
        let stdout = Arc::clone(&stdout);
        thread::spawn(move || {
            loop {
                channel::select! {
                    recv(audio_rx) -> block => {
                        let Ok(block) = block else {
                            break;
                        };
                        if let Ok(mut streamer) = streamer.lock() {
                            let _ = streamer.write_chunk(&stdout, block.data(), block.frames());
                        }
                        audio_block_pool.recycle_block(block);
                        audio_block_pool.grow_to_requested_capacity();
                    }
                    recv(pool_resize_rx) -> resize => {
                        if resize.is_err() {
                            break;
                        }
                        audio_block_pool.grow_to_requested_capacity();
                    }
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
