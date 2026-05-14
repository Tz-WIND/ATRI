mod commands;
mod ipc;
mod stream;

use std::sync::{Arc, Mutex};
use std::thread;
use crossbeam::channel;
use atri_engine::engine::AudioEngine;
use stream::AudioStreamer;

const DEFAULT_SAMPLE_RATE: u32 = 48000;
const DEFAULT_BUFFER_SIZE: usize = 256;

fn main() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info"))
        .target(env_logger::Target::Stderr)
        .init();

    eprintln!("[atri-host] ATRI Audio Host v0.1.0");
    eprintln!("[atri-host] sample_rate={}, buffer_size={}", DEFAULT_SAMPLE_RATE, DEFAULT_BUFFER_SIZE);

    let engine = Arc::new(Mutex::new(AudioEngine::new(
        DEFAULT_SAMPLE_RATE,
        DEFAULT_BUFFER_SIZE,
    )));

    let streamer = Arc::new(Mutex::new(AudioStreamer::new(
        DEFAULT_SAMPLE_RATE,
        2,
    )));

    let (cmd_tx, cmd_rx) = channel::unbounded::<commands::AppCommand>();

    // Audio processing thread
    let engine_clone = Arc::clone(&engine);
    let streamer_clone = Arc::clone(&streamer);
    let audio_running = Arc::new(std::sync::atomic::AtomicBool::new(true));
    let audio_running_clone = Arc::clone(&audio_running);

    let audio_thread = thread::spawn(move || {
        eprintln!("[atri-host] Audio thread started (stub — no cpal output yet)");

        let buffer_size = DEFAULT_BUFFER_SIZE;
        let mut output = vec![0.0f32; buffer_size * 2];

        while audio_running_clone.load(std::sync::atomic::Ordering::Relaxed) {
            // Process pending commands
            while let Ok(cmd) = cmd_rx.try_recv() {
                if matches!(cmd, commands::AppCommand::Shutdown) {
                    audio_running_clone.store(false, std::sync::atomic::Ordering::Relaxed);
                    break;
                }
                let eng = engine_clone.lock().unwrap();
                let mut session = eng.session.lock().unwrap();
                match cmd {
                    commands::AppCommand::Play => session.transport.play(),
                    commands::AppCommand::Stop => session.transport.stop(),
                    commands::AppCommand::Pause => session.transport.pause(),
                    commands::AppCommand::Seek(pos) => session.transport.seek(pos),
                    commands::AppCommand::SetTempo { bpm, time_sig } => {
                        use atri_core::time::beats::Beats;
                        use atri_core::time::tempo::{Meter, Tempo};
                        let new_map = session.tempo_map.read().with_tempo(
                            Tempo::new(bpm, 4),
                            Beats::from_beats(0.0),
                        );
                        let new_map = new_map.with_meter(
                            Meter::new(time_sig.0, time_sig.1),
                            Beats::from_beats(0.0),
                        );
                        session.tempo_map.update(|_| new_map);
                    }
                    commands::AppCommand::Shutdown => {}
                }
            }

            // Process audio
            {
                let eng = engine_clone.lock().unwrap();
                let mut session = eng.session.lock().unwrap();
                session.process(&mut output);
            }

            // Stream audio to stdout
            {
                let s = streamer_clone.lock().unwrap();
                s.write_chunk(&output, buffer_size).ok();
            }

            // Simulate real-time buffer period (~5.3ms at 48kHz/256)
            thread::sleep(std::time::Duration::from_micros(
                (buffer_size as f64 / DEFAULT_SAMPLE_RATE as f64 * 1_000_000.0) as u64,
            ));
        }

        eprintln!("[atri-host] Audio thread stopped");
    });

    // Run IPC loop on main thread
    ipc::run_ipc_loop(engine, cmd_tx, streamer);

    // Signal audio thread to stop and wait for it
    audio_running.store(false, std::sync::atomic::Ordering::Relaxed);
    let _ = audio_thread.join();
    eprintln!("[atri-host] Shutdown complete");
}
