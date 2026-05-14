use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use atri_core::time::beats::Beats;
use atri_core::time::tempo::{Meter, Tempo};
use atri_engine::engine::AudioEngine;
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use crossbeam::channel::{Receiver, Sender};

use crate::commands::AppCommand;

pub struct AudioBlock {
    data: Vec<f32>,
}

impl AudioBlock {
    const CHANNELS: usize = 2;

    pub fn from_stereo(data: Vec<f32>) -> Option<Self> {
        if data.len() % Self::CHANNELS == 0 {
            Some(Self { data })
        } else {
            None
        }
    }

    pub fn data(&self) -> &[f32] {
        &self.data
    }

    pub fn frames(&self) -> usize {
        self.data.len() / Self::CHANNELS
    }
}

pub struct AudioDriver {
    running: Arc<AtomicBool>,
    stream: Option<cpal::Stream>,
    fallback_thread: Option<JoinHandle<()>>,
}

pub struct DriverConfig {
    pub sample_rate: u32,
    pub buffer_size: usize,
    pub output_channels: usize,
}

impl AudioDriver {
    pub fn start(
        engine: Arc<Mutex<AudioEngine>>,
        cmd_rx: Receiver<AppCommand>,
        stream_tx: Sender<AudioBlock>,
        preferred_sample_rate: u32,
        preferred_buffer_size: usize,
    ) -> (Self, DriverConfig) {
        let running = Arc::new(AtomicBool::new(true));

        match start_cpal_driver(
            Arc::clone(&engine),
            cmd_rx.clone(),
            stream_tx.clone(),
            Arc::clone(&running),
            preferred_buffer_size,
        ) {
            Ok((stream, config)) => {
                eprintln!(
                    "[atri-host] cpal output started: sample_rate={}, channels={}",
                    config.sample_rate, config.output_channels
                );
                (
                    Self {
                        running,
                        stream: Some(stream),
                        fallback_thread: None,
                    },
                    config,
                )
            }
            Err(err) => {
                eprintln!("[atri-host] cpal unavailable, using null driver: {err}");
                let config = DriverConfig {
                    sample_rate: preferred_sample_rate,
                    buffer_size: preferred_buffer_size,
                    output_channels: 2,
                };
                let fallback_thread = start_null_driver(
                    engine,
                    cmd_rx,
                    stream_tx,
                    Arc::clone(&running),
                    preferred_sample_rate,
                    preferred_buffer_size,
                );
                (
                    Self {
                        running,
                        stream: None,
                        fallback_thread: Some(fallback_thread),
                    },
                    config,
                )
            }
        }
    }

    pub fn stop(mut self) {
        self.running.store(false, Ordering::Relaxed);
        drop(self.stream.take());
        if let Some(thread) = self.fallback_thread.take() {
            let _ = thread.join();
        }
    }
}

fn start_cpal_driver(
    engine: Arc<Mutex<AudioEngine>>,
    cmd_rx: Receiver<AppCommand>,
    stream_tx: Sender<AudioBlock>,
    running: Arc<AtomicBool>,
    preferred_buffer_size: usize,
) -> Result<(cpal::Stream, DriverConfig), String> {
    let host = cpal::default_host();
    let device = host
        .default_output_device()
        .ok_or("no default output device")?;
    let supported = device
        .default_output_config()
        .map_err(|err| format!("default output config failed: {err}"))?;

    let sample_rate = supported.sample_rate().0;
    let output_channels = supported.channels() as usize;
    let mut config = supported.config();
    config.buffer_size = cpal::BufferSize::Fixed(preferred_buffer_size as u32);
    if let Ok(mut engine) = engine.lock() {
        engine.reconfigure(sample_rate, preferred_buffer_size);
    }

    let stream = match supported.sample_format() {
        cpal::SampleFormat::F32 => build_output_stream::<f32>(
            &device,
            &config,
            engine,
            cmd_rx,
            stream_tx,
            running,
            output_channels,
        ),
        cpal::SampleFormat::I16 => build_output_stream::<i16>(
            &device,
            &config,
            engine,
            cmd_rx,
            stream_tx,
            running,
            output_channels,
        ),
        cpal::SampleFormat::U16 => build_output_stream::<u16>(
            &device,
            &config,
            engine,
            cmd_rx,
            stream_tx,
            running,
            output_channels,
        ),
        format => Err(format!("unsupported output sample format: {format:?}")),
    }?;

    stream
        .play()
        .map_err(|err| format!("stream play failed: {err}"))?;
    Ok((
        stream,
        DriverConfig {
            sample_rate,
            buffer_size: preferred_buffer_size,
            output_channels,
        },
    ))
}

fn build_output_stream<T>(
    device: &cpal::Device,
    config: &cpal::StreamConfig,
    engine: Arc<Mutex<AudioEngine>>,
    cmd_rx: Receiver<AppCommand>,
    stream_tx: Sender<AudioBlock>,
    running: Arc<AtomicBool>,
    output_channels: usize,
) -> Result<cpal::Stream, String>
where
    T: cpal::Sample + cpal::SizedSample + cpal::FromSample<f32>,
{
    let mut stereo = Vec::<f32>::new();
    let err_fn = |err| eprintln!("[atri-host] cpal stream error: {err}");

    device
        .build_output_stream(
            config,
            move |output: &mut [T], _| {
                if !running.load(Ordering::Relaxed) {
                    silence_output(output);
                    return;
                }

                let frames = output.len() / output_channels;
                let needed = frames * 2;
                if stereo.len() != needed {
                    stereo.resize(needed, 0.0);
                }

                render_cycle(&engine, &cmd_rx, &mut stereo[..needed]);
                copy_stereo_to_device::<T>(&stereo[..needed], output, output_channels);
                let Some(block) = AudioBlock::from_stereo(stereo[..needed].to_vec()) else {
                    return;
                };
                let _ = stream_tx.try_send(block);
            },
            err_fn,
            None,
        )
        .map_err(|err| format!("build output stream failed: {err}"))
}

fn start_null_driver(
    engine: Arc<Mutex<AudioEngine>>,
    cmd_rx: Receiver<AppCommand>,
    stream_tx: Sender<AudioBlock>,
    running: Arc<AtomicBool>,
    sample_rate: u32,
    buffer_size: usize,
) -> JoinHandle<()> {
    thread::spawn(move || {
        let mut output = vec![0.0f32; buffer_size * 2];
        let sleep = Duration::from_secs_f64(buffer_size as f64 / sample_rate as f64);

        while running.load(Ordering::Relaxed) {
            render_cycle(&engine, &cmd_rx, &mut output);
            let Some(block) = AudioBlock::from_stereo(output.clone()) else {
                continue;
            };
            let _ = stream_tx.try_send(block);
            thread::sleep(sleep);
        }
    })
}

fn render_cycle(
    engine: &Arc<Mutex<AudioEngine>>,
    cmd_rx: &Receiver<AppCommand>,
    output: &mut [f32],
) {
    drain_commands(engine, cmd_rx);
    if let Ok(eng) = engine.lock() {
        eng.with_session(|session| session.process(output));
        return;
    }
    output.fill(0.0);
}

fn drain_commands(engine: &Arc<Mutex<AudioEngine>>, cmd_rx: &Receiver<AppCommand>) {
    while let Ok(cmd) = cmd_rx.try_recv() {
        if let Ok(eng) = engine.lock() {
            eng.with_session(|session| apply_command(session, cmd));
        }
    }
}

fn apply_command(session: &mut atri_engine::session::Session, cmd: AppCommand) {
    match cmd {
        AppCommand::Play => session.transport.play(),
        AppCommand::Stop => session.transport.stop(),
        AppCommand::Pause => session.transport.pause(),
        AppCommand::Seek(pos) => session.transport.seek(pos),
        AppCommand::SetLoop { start, end } => {
            session.transport.loop_start = Some(start);
            session.transport.loop_end = Some(end);
        }
        AppCommand::ClearLoop => {
            session.transport.loop_start = None;
            session.transport.loop_end = None;
        }
        AppCommand::SetTempo { bpm, time_sig } => {
            let new_map = session
                .tempo_map
                .read()
                .with_tempo(Tempo::new(bpm, 4), Beats::from_beats(0.0));
            let new_map =
                new_map.with_meter(Meter::new(time_sig.0, time_sig.1), Beats::from_beats(0.0));
            session.tempo_map.update(|_| new_map);
        }
        AppCommand::Shutdown => {}
    }
}

fn copy_stereo_to_device<T>(stereo: &[f32], output: &mut [T], output_channels: usize)
where
    T: cpal::Sample + cpal::FromSample<f32>,
{
    for (frame, out_frame) in output.chunks_mut(output_channels).enumerate() {
        let left = stereo[frame * 2];
        let right = stereo[frame * 2 + 1];
        for (channel, sample) in out_frame.iter_mut().enumerate() {
            let value = match channel {
                0 => left,
                1 => right,
                _ => 0.0,
            };
            *sample = T::from_sample(value);
        }
    }
}

fn silence_output<T>(output: &mut [T])
where
    T: cpal::Sample + cpal::FromSample<f32>,
{
    for sample in output {
        *sample = T::from_sample(0.0);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn audio_block_derives_frames_from_stereo_data() {
        let block = AudioBlock::from_stereo(vec![0.0, 0.1, 0.2, 0.3]).unwrap();

        assert_eq!(block.frames(), 2);
        assert_eq!(block.data(), &[0.0, 0.1, 0.2, 0.3]);
    }

    #[test]
    fn audio_block_rejects_incomplete_stereo_frame() {
        assert!(AudioBlock::from_stereo(vec![0.0, 0.1, 0.2]).is_none());
    }
}
