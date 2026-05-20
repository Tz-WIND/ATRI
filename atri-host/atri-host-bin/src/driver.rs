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

const MAX_COMMANDS_PER_RENDER_CYCLE: usize = 64;

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
    pub audio_engine: String,
    pub bit_depth: String,
}

impl AudioDriver {
    pub fn start(
        engine: Arc<Mutex<AudioEngine>>,
        cmd_rx: Receiver<AppCommand>,
        stream_tx: Sender<AudioBlock>,
        preferred_sample_rate: u32,
        preferred_buffer_size: usize,
        audio_engine: String,
        bit_depth: String,
    ) -> (Self, DriverConfig) {
        let running = Arc::new(AtomicBool::new(true));

        match start_cpal_driver(
            Arc::clone(&engine),
            cmd_rx.clone(),
            stream_tx.clone(),
            Arc::clone(&running),
            preferred_sample_rate,
            preferred_buffer_size,
            audio_engine.clone(),
            bit_depth.clone(),
        ) {
            Ok((stream, config)) => {
                eprintln!(
                    "[atri-host] cpal output started: device={}, sample_rate={}, channels={}, bit_depth={}",
                    config.audio_engine,
                    config.sample_rate,
                    config.output_channels,
                    config.bit_depth
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
                    audio_engine,
                    bit_depth,
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
    preferred_sample_rate: u32,
    preferred_buffer_size: usize,
    audio_engine: String,
    bit_depth: String,
) -> Result<(cpal::Stream, DriverConfig), String> {
    let selected = select_output_device(&audio_engine)?;
    let supported = select_output_config(&selected.device, preferred_sample_rate, &bit_depth)?;

    let sample_rate = supported.sample_rate().0;
    let output_channels = supported.channels() as usize;
    let mut config = supported.config();
    config.buffer_size = cpal::BufferSize::Fixed(preferred_buffer_size as u32);

    let stream = build_output_stream_for_format(
        supported.sample_format(),
        &selected.device,
        &config,
        Arc::clone(&engine),
        cmd_rx.clone(),
        stream_tx.clone(),
        Arc::clone(&running),
        output_channels,
    )
    .or_else(|err| {
        log::warn!(
            "[atri-host] fixed buffer_size={} rejected by device: {}; retrying default buffer",
            preferred_buffer_size,
            err
        );
        config.buffer_size = cpal::BufferSize::Default;
        build_output_stream_for_format(
            supported.sample_format(),
            &selected.device,
            &config,
            Arc::clone(&engine),
            cmd_rx,
            stream_tx,
            Arc::clone(&running),
            output_channels,
        )
    })?;

    if let Ok(mut engine) = engine.lock() {
        engine.reconfigure(sample_rate, preferred_buffer_size);
    }

    stream
        .play()
        .map_err(|err| format!("stream play failed: {err}"))?;
    Ok((
        stream,
        DriverConfig {
            sample_rate,
            buffer_size: preferred_buffer_size,
            output_channels,
            audio_engine: selected.id,
            bit_depth: sample_format_bit_depth(supported.sample_format()).to_string(),
        },
    ))
}

struct SelectedOutputDevice {
    id: String,
    device: cpal::Device,
}

fn select_output_device(selection: &str) -> Result<SelectedOutputDevice, String> {
    let selection = selection.trim();
    if selection.is_empty() || selection.eq_ignore_ascii_case("default") {
        let host = cpal::default_host();
        let device = host
            .default_output_device()
            .ok_or("no default output device")?;
        let name = device
            .name()
            .unwrap_or_else(|_| "Default Output".to_string());
        return Ok(SelectedOutputDevice {
            id: audio_device_id(host.id().name(), &name),
            device,
        });
    }

    let (host_key, device_name) = selection
        .split_once("::")
        .map(|(host, name)| (host, Some(name)))
        .unwrap_or((selection, None));
    let host_id = host_id_from_key(host_key)
        .ok_or_else(|| format!("audio device host is not available: {host_key}"))?;
    let host = cpal::host_from_id(host_id)
        .map_err(|err| format!("audio device host unavailable: {err}"))?;

    let device = if let Some(device_name) = device_name {
        host.output_devices()
            .map_err(|err| format!("audio output device query failed: {err}"))?
            .find(|device| {
                device
                    .name()
                    .map(|name| name == device_name)
                    .unwrap_or(false)
            })
            .ok_or_else(|| format!("audio output device not found: {selection}"))?
    } else {
        host.default_output_device()
            .ok_or_else(|| format!("no default output device for {}", host_id.name()))?
    };
    let name = device.name().unwrap_or_else(|_| "Output".to_string());

    Ok(SelectedOutputDevice {
        id: audio_device_id(host_id.name(), &name),
        device,
    })
}

fn host_id_from_key(key: &str) -> Option<cpal::HostId> {
    let key = normalize_audio_key(key);
    cpal::available_hosts()
        .into_iter()
        .find(|host_id| normalize_audio_key(host_id.name()) == key)
}

pub(crate) fn audio_device_id(host_api: &str, device_name: &str) -> String {
    format!("{}::{}", normalize_audio_key(host_api), device_name)
}

fn normalize_audio_key(value: &str) -> String {
    value.trim().to_ascii_lowercase().replace(' ', "_")
}

fn select_output_config(
    device: &cpal::Device,
    preferred_sample_rate: u32,
    preferred_bit_depth: &str,
) -> Result<cpal::SupportedStreamConfig, String> {
    let default_config = device
        .default_output_config()
        .map_err(|err| format!("default output config failed: {err}"))?;
    if default_config.sample_rate().0 == preferred_sample_rate
        && sample_format_matches_bit_depth(default_config.sample_format(), preferred_bit_depth)
    {
        return Ok(default_config);
    }

    let preferred_rate = cpal::SampleRate(preferred_sample_rate);
    let Ok(supported_configs) = device.supported_output_configs() else {
        log::warn!(
            "[atri-host] supported output config query failed; using default sample_rate={}",
            default_config.sample_rate().0
        );
        return Ok(default_config);
    };
    let supported = supported_configs
        .filter(|range| {
            range.min_sample_rate() <= preferred_rate && preferred_rate <= range.max_sample_rate()
        })
        .max_by_key(|range| output_config_score(range, &default_config, preferred_bit_depth))
        .map(|range| range.with_sample_rate(preferred_rate));

    match supported {
        Some(config) => Ok(config),
        None => {
            log::warn!(
                "[atri-host] requested sample_rate={} is not supported by the default device; using {}",
                preferred_sample_rate,
                default_config.sample_rate().0
            );
            Ok(default_config)
        }
    }
}

fn output_config_score(
    range: &cpal::SupportedStreamConfigRange,
    default_config: &cpal::SupportedStreamConfig,
    preferred_bit_depth: &str,
) -> u16 {
    let mut score = 0;
    if sample_format_matches_bit_depth(range.sample_format(), preferred_bit_depth) {
        score += 16;
    }
    if range.sample_format() == default_config.sample_format() {
        score += 8;
    }
    if range.channels() == default_config.channels() {
        score += 4;
    }
    if range.channels() >= 2 {
        score += 2;
    }
    score
}

fn sample_format_matches_bit_depth(format: cpal::SampleFormat, bit_depth: &str) -> bool {
    matches!(
        (bit_depth.trim().to_ascii_lowercase().as_str(), format),
        ("f32", cpal::SampleFormat::F32)
            | ("i16", cpal::SampleFormat::I16)
            | ("i16", cpal::SampleFormat::U16)
            | ("i24", cpal::SampleFormat::I32)
            | ("i24", cpal::SampleFormat::U32)
    )
}

fn sample_format_bit_depth(format: cpal::SampleFormat) -> &'static str {
    match format {
        cpal::SampleFormat::I16 | cpal::SampleFormat::U16 => "i16",
        cpal::SampleFormat::I32 | cpal::SampleFormat::U32 => "i24",
        cpal::SampleFormat::F32 | cpal::SampleFormat::F64 => "f32",
        _ => "f32",
    }
}

fn build_output_stream_for_format(
    sample_format: cpal::SampleFormat,
    device: &cpal::Device,
    config: &cpal::StreamConfig,
    engine: Arc<Mutex<AudioEngine>>,
    cmd_rx: Receiver<AppCommand>,
    stream_tx: Sender<AudioBlock>,
    running: Arc<AtomicBool>,
    output_channels: usize,
) -> Result<cpal::Stream, String> {
    match sample_format {
        cpal::SampleFormat::I8 => build_output_stream::<i8>(
            device,
            config,
            engine,
            cmd_rx,
            stream_tx,
            running,
            output_channels,
        ),
        cpal::SampleFormat::F32 => build_output_stream::<f32>(
            device,
            config,
            engine,
            cmd_rx,
            stream_tx,
            running,
            output_channels,
        ),
        cpal::SampleFormat::F64 => build_output_stream::<f64>(
            device,
            config,
            engine,
            cmd_rx,
            stream_tx,
            running,
            output_channels,
        ),
        cpal::SampleFormat::I16 => build_output_stream::<i16>(
            device,
            config,
            engine,
            cmd_rx,
            stream_tx,
            running,
            output_channels,
        ),
        cpal::SampleFormat::I32 => build_output_stream::<i32>(
            device,
            config,
            engine,
            cmd_rx,
            stream_tx,
            running,
            output_channels,
        ),
        cpal::SampleFormat::I64 => build_output_stream::<i64>(
            device,
            config,
            engine,
            cmd_rx,
            stream_tx,
            running,
            output_channels,
        ),
        cpal::SampleFormat::U8 => build_output_stream::<u8>(
            device,
            config,
            engine,
            cmd_rx,
            stream_tx,
            running,
            output_channels,
        ),
        cpal::SampleFormat::U16 => build_output_stream::<u16>(
            device,
            config,
            engine,
            cmd_rx,
            stream_tx,
            running,
            output_channels,
        ),
        cpal::SampleFormat::U32 => build_output_stream::<u32>(
            device,
            config,
            engine,
            cmd_rx,
            stream_tx,
            running,
            output_channels,
        ),
        cpal::SampleFormat::U64 => build_output_stream::<u64>(
            device,
            config,
            engine,
            cmd_rx,
            stream_tx,
            running,
            output_channels,
        ),
        format => Err(format!("unsupported output sample format: {format:?}")),
    }
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
                    log::debug!(
                        "[cpal] buffer size changed: old_stereo={}, new_needed={}, frames={}, channels={}",
                        stereo.len(),
                        needed,
                        frames,
                        output_channels
                    );
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
    let Ok(eng) = engine.try_lock() else {
        output.fill(0.0);
        return;
    };

    if eng
        .try_with_session(|session| {
            drain_commands(session, cmd_rx);
            session.process(output);
        })
        .is_some()
    {
        return;
    }
    output.fill(0.0);
}

fn drain_commands(session: &mut atri_engine::session::Session, cmd_rx: &Receiver<AppCommand>) {
    for _ in 0..MAX_COMMANDS_PER_RENDER_CYCLE {
        let Ok(cmd) = cmd_rx.try_recv() else {
            return;
        };
        apply_command(session, cmd);
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
    use std::time::Duration;

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

    #[test]
    fn render_cycle_when_engine_locked_should_return_silence_without_waiting() {
        let engine = Arc::new(Mutex::new(AudioEngine::new(48_000, 128)));
        let _engine_guard = engine.lock().unwrap();
        let (cmd_tx, cmd_rx) = crossbeam::channel::unbounded();
        let (done_tx, done_rx) = crossbeam::channel::bounded(1);
        cmd_tx.send(AppCommand::Play).unwrap();

        let render_engine = Arc::clone(&engine);
        let handle = thread::spawn(move || {
            let mut output = vec![1.0; 16];
            render_cycle(&render_engine, &cmd_rx, &mut output);
            done_tx.send(output).unwrap();
        });

        let output = done_rx
            .recv_timeout(Duration::from_millis(50))
            .expect("render_cycle blocked while the control thread held the engine lock");

        assert!(output.iter().all(|sample| *sample == 0.0));
        drop(_engine_guard);
        handle.join().unwrap();
    }

    #[test]
    fn render_cycle_limits_command_drain_per_callback() {
        let engine = Arc::new(Mutex::new(AudioEngine::new(48_000, 128)));
        let (cmd_tx, cmd_rx) = crossbeam::channel::unbounded();
        for _ in 0..66 {
            cmd_tx.send(AppCommand::Play).unwrap();
        }

        let mut output = vec![0.0; 16];
        render_cycle(&engine, &cmd_rx, &mut output);

        assert_eq!(cmd_rx.len(), 2);
    }
}
