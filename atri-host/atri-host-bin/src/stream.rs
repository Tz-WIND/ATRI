use std::io::{self, Write};

/// Handles streaming PCM audio from the ring buffer to stdout.
pub struct AudioStreamer {
    sample_rate: u32,
    channels: u16,
    enabled: bool,
}

impl AudioStreamer {
    pub fn new(sample_rate: u32, channels: u16) -> Self {
        Self { sample_rate, channels, enabled: true }
    }

    pub fn set_enabled(&mut self, enabled: bool) {
        self.enabled = enabled;
    }

    /// Write an audio chunk header + raw PCM data to stdout.
    pub fn write_chunk(&self, pcm: &[f32], nframes: usize) -> io::Result<()> {
        if !self.enabled || pcm.is_empty() {
            return Ok(());
        }

        let header = serde_json::json!({
            "type": "audio",
            "samples": nframes,
            "channels": self.channels,
            "sample_rate": self.sample_rate,
        });

        let mut stdout = std::io::stdout();
        writeln!(stdout, "{}", header)?;

        // Convert f32 to little-endian bytes
        let byte_data: Vec<u8> = pcm[..nframes * self.channels as usize]
            .iter()
            .flat_map(|s| s.to_le_bytes())
            .collect();
        stdout.write_all(&byte_data)?;
        stdout.flush()?;
        Ok(())
    }
}
