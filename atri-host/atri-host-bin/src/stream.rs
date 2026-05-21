use std::io::{self, Stdout, Write};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use serde::Serialize;

pub type SharedStdout = Arc<Mutex<Stdout>>;

/// Handles JSON-header + raw PCM audio streaming to stdout.
pub struct AudioStreamer {
    sample_rate: u32,
    channels: u16,
    enabled: Arc<AtomicBool>,
    #[cfg(target_endian = "big")]
    scratch: Vec<u8>,
}

#[derive(Serialize)]
struct AudioHeader {
    #[serde(rename = "type")]
    kind: &'static str,
    samples: usize,
    channels: u16,
    sample_rate: u32,
    format: &'static str,
}

impl AudioStreamer {
    pub fn with_enabled_flag(sample_rate: u32, channels: u16, enabled: Arc<AtomicBool>) -> Self {
        Self {
            sample_rate,
            channels,
            enabled,
            #[cfg(target_endian = "big")]
            scratch: Vec::new(),
        }
    }

    pub fn set_enabled(&mut self, enabled: bool) {
        self.enabled.store(enabled, Ordering::Relaxed);
    }

    pub fn is_enabled(&self) -> bool {
        self.enabled.load(Ordering::Relaxed)
    }

    pub fn write_chunk(
        &mut self,
        stdout: &SharedStdout,
        pcm: &[f32],
        nframes: usize,
    ) -> io::Result<()> {
        if !self.is_enabled() || pcm.is_empty() {
            return Ok(());
        }

        let frames = nframes.min(pcm.len() / self.channels as usize);
        let samples = frames * self.channels as usize;
        let header = AudioHeader {
            kind: "audio",
            samples: frames,
            channels: self.channels,
            sample_rate: self.sample_rate,
            format: "f32_le_interleaved",
        };

        let pcm = &pcm[..samples];
        #[cfg(target_endian = "little")]
        let pcm_bytes = f32_slice_as_le_bytes(pcm);
        #[cfg(target_endian = "big")]
        let pcm_bytes = copy_f32_slice_as_le_bytes(pcm, &mut self.scratch);

        write_audio_chunk(stdout, &header, pcm_bytes)
    }
}

#[cfg(target_endian = "little")]
fn f32_slice_as_le_bytes(samples: &[f32]) -> &[u8] {
    let byte_len = std::mem::size_of_val(samples);
    // Safety: `samples` is initialized memory, `u8` has alignment 1, and the
    // returned slice is tied to the input lifetime. On little-endian targets,
    // native f32 representation matches the stream's f32_le_interleaved format.
    unsafe { std::slice::from_raw_parts(samples.as_ptr().cast::<u8>(), byte_len) }
}

#[cfg(target_endian = "big")]
fn copy_f32_slice_as_le_bytes<'a>(samples: &[f32], scratch: &'a mut Vec<u8>) -> &'a [u8] {
    scratch.clear();
    scratch.reserve(std::mem::size_of_val(samples));
    for sample in samples {
        scratch.extend_from_slice(&sample.to_le_bytes());
    }
    scratch
}

pub fn write_json<T: Serialize>(stdout: &SharedStdout, value: &T) -> io::Result<()> {
    let mut stdout = stdout.lock().unwrap();
    serde_json::to_writer(&mut *stdout, value)?;
    stdout.write_all(b"\n")?;
    stdout.flush()
}

fn write_audio_chunk(
    stdout: &SharedStdout,
    header: &AudioHeader,
    pcm_bytes: &[u8],
) -> io::Result<()> {
    let mut stdout = stdout.lock().unwrap();
    write_audio_chunk_to(&mut *stdout, header, pcm_bytes)
}

fn write_audio_chunk_to<W: Write>(
    writer: &mut W,
    header: &AudioHeader,
    pcm_bytes: &[u8],
) -> io::Result<()> {
    serde_json::to_writer(&mut *writer, header)?;
    writer.write_all(b"\n")?;
    writer.write_all(pcm_bytes)?;
    writer.flush()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[derive(Default)]
    struct WriteSink {
        bytes: Vec<u8>,
    }

    impl Write for WriteSink {
        fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
            self.bytes.extend_from_slice(buf);
            Ok(buf.len())
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    #[test]
    #[cfg(target_endian = "little")]
    fn f32_slice_as_le_bytes_matches_le_encoding() {
        let samples = [0.5f32, -0.25, 1.0, 0.0];
        let expected: Vec<u8> = samples.into_iter().flat_map(f32::to_le_bytes).collect();

        assert_eq!(f32_slice_as_le_bytes(&samples), expected.as_slice());
    }

    #[test]
    fn audio_chunk_writes_json_header_then_raw_f32_bytes() {
        let header = AudioHeader {
            kind: "audio",
            samples: 2,
            channels: 2,
            sample_rate: 48_000,
            format: "f32_le_interleaved",
        };
        let pcm_bytes: Vec<u8> = [0.5f32, -0.25, 1.0, 0.0]
            .into_iter()
            .flat_map(f32::to_le_bytes)
            .collect();
        let mut sink = WriteSink::default();
        write_audio_chunk_to(&mut sink, &header, &pcm_bytes).unwrap();

        let newline = sink
            .bytes
            .iter()
            .position(|byte| *byte == b'\n')
            .expect("header newline");
        let header_json = std::str::from_utf8(&sink.bytes[..newline]).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(header_json).unwrap();

        assert_eq!(parsed["type"], "audio");
        assert_eq!(parsed["samples"], 2);
        assert!(parsed.get("data").is_none());
        assert_eq!(
            sink.bytes.len() - newline - 1,
            4 * std::mem::size_of::<f32>()
        );
        assert_eq!(&sink.bytes[newline + 1..newline + 5], &0.5f32.to_le_bytes());
    }

    #[test]
    fn set_enabled_updates_shared_flag() {
        let enabled = Arc::new(AtomicBool::new(false));
        let mut streamer = AudioStreamer::with_enabled_flag(48_000, 2, Arc::clone(&enabled));

        assert!(!enabled.load(Ordering::Relaxed));
        streamer.set_enabled(true);
        assert!(enabled.load(Ordering::Relaxed));
        streamer.set_enabled(false);
        assert!(!enabled.load(Ordering::Relaxed));
    }
}
