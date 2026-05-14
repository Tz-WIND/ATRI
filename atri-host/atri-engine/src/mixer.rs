use atri_core::audio::buffer::{AudioBuffer};

/// Sums all track outputs into a stereo master bus.
pub struct Mixer;

impl Mixer {
    pub fn new() -> Self {
        Self
    }

    /// Sum the provided track buffers into the master output.
    pub fn sum(&self, track_bufs: &[AudioBuffer], master: &mut AudioBuffer) {
        master.silence(master.capacity());

        for track_buf in track_bufs {
            if track_buf.channels() < 2 {
                continue;
            }
            let n = master.capacity().min(track_buf.capacity());
            for i in 0..n {
                master.channel_mut(0)[i] =
                    (master.channel(0)[i] + track_buf.channel(0)[i]).clamp(-1.0, 1.0);
                master.channel_mut(1)[i] =
                    (master.channel(1)[i] + track_buf.channel(1)[i]).clamp(-1.0, 1.0);
            }
        }
    }
}

impl Default for Mixer {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sum_single_track() {
        let mixer = Mixer::new();
        let mut track_buf = AudioBuffer::new(2, 4);
        track_buf.channel_mut(0)[0] = 0.5;
        track_buf.channel_mut(1)[0] = 0.3;

        let mut master = AudioBuffer::new(2, 4);
        mixer.sum(&[track_buf], &mut master);

        assert!((master.channel(0)[0] - 0.5).abs() < 0.001);
        assert!((master.channel(1)[0] - 0.3).abs() < 0.001);
    }

    #[test]
    fn test_sum_multiple_tracks() {
        let mixer = Mixer::new();
        let mut t1 = AudioBuffer::new(2, 4);
        t1.channel_mut(0)[0] = 0.3;
        t1.channel_mut(1)[0] = 0.2;
        let mut t2 = AudioBuffer::new(2, 4);
        t2.channel_mut(0)[0] = 0.3;
        t2.channel_mut(1)[0] = 0.2;

        let mut master = AudioBuffer::new(2, 4);
        mixer.sum(&[t1, t2], &mut master);

        assert!((master.channel(0)[0] - 0.6).abs() < 0.001);
        assert!((master.channel(1)[0] - 0.4).abs() < 0.001);
    }

    #[test]
    fn test_clipping() {
        let mixer = Mixer::new();
        let mut t1 = AudioBuffer::new(2, 4);
        t1.channel_mut(0)[0] = 0.8;
        let mut t2 = AudioBuffer::new(2, 4);
        t2.channel_mut(0)[0] = 0.8;

        let mut master = AudioBuffer::new(2, 4);
        mixer.sum(&[t1, t2], &mut master);

        // 0.8 + 0.8 = 1.6, clamped to 1.0
        assert!((master.channel(0)[0] - 1.0).abs() < 0.001);
    }
}
