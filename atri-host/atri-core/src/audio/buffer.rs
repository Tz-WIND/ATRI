/// A multi-channel audio buffer with flat storage.
#[derive(Debug, Clone)]
pub struct AudioBuffer {
    data: Vec<f32>,
    channels: u16,
    capacity: usize,
}

impl AudioBuffer {
    pub fn new(channels: u16, capacity: usize) -> Self {
        Self {
            data: vec![0.0f32; channels as usize * capacity],
            channels,
            capacity,
        }
    }

    pub fn channels(&self) -> u16 {
        self.channels
    }

    pub fn capacity(&self) -> usize {
        self.capacity
    }

    /// Get a slice for a specific channel (non-interleaved access).
    pub fn channel(&self, ch: u16) -> &[f32] {
        let offset = ch as usize * self.capacity;
        &self.data[offset..offset + self.capacity]
    }

    /// Get a mutable slice for a specific channel.
    pub fn channel_mut(&mut self, ch: u16) -> &mut [f32] {
        let offset = ch as usize * self.capacity;
        &mut self.data[offset..offset + self.capacity]
    }

    /// Fill all channels with zeros for the given number of frames.
    pub fn silence(&mut self, nframes: usize) {
        let n = nframes.min(self.capacity);
        for ch in 0..self.channels {
            self.channel_mut(ch)[..n].fill(0.0);
        }
    }

    /// Fill from interleaved stereo input.
    pub fn from_interleaved(&mut self, interleaved: &[f32], nframes: usize) {
        let n = nframes.min(self.capacity);
        if self.channels == 2 {
            for i in 0..n {
                self.channel_mut(0)[i] = interleaved[i * 2];
                self.channel_mut(1)[i] = interleaved[i * 2 + 1];
            }
        }
    }

    /// Write to interleaved stereo output.
    pub fn to_interleaved(&self, output: &mut [f32], nframes: usize) {
        let n = nframes.min(self.capacity);
        if self.channels == 2 {
            for i in 0..n {
                output[i * 2] = self.channel(0)[i];
                output[i * 2 + 1] = self.channel(1)[i];
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_buffer_silence() {
        let mut buf = AudioBuffer::new(2, 256);
        buf.channel_mut(0)[0] = 0.5;
        buf.silence(256);
        assert_eq!(buf.channel(0)[0], 0.0);
    }

    #[test]
    fn test_interleaved_round_trip() {
        let mut buf = AudioBuffer::new(2, 4);
        let input: Vec<f32> = vec![0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8];
        buf.from_interleaved(&input, 4);
        let mut output = vec![0.0f32; 8];
        buf.to_interleaved(&mut output, 4);
        assert_eq!(input, output);
    }
}
