use atri_core::audio::buffer::AudioBuffer;

/// Sums all track outputs into a stereo master bus.
pub struct Mixer;

impl Mixer {
    pub fn new() -> Self {
        Self
    }

    /// Sum the provided track buffers into the master output.
    pub fn sum(&self, track_bufs: &[AudioBuffer], master: &mut AudioBuffer) {
        self.sum_n(track_bufs, master, master.capacity());
    }

    pub fn sum_n(&self, track_bufs: &[AudioBuffer], master: &mut AudioBuffer, nframes: usize) {
        master.silence(nframes);

        for track_buf in track_bufs {
            self.add(track_buf, master, nframes);
        }
    }

    pub fn add(&self, track_buf: &AudioBuffer, master: &mut AudioBuffer, nframes: usize) {
        if track_buf.channels() < 2 || master.channels() < 2 {
            return;
        }

        let n = nframes.min(master.capacity()).min(track_buf.capacity());
        for i in 0..n {
            let left = master.channel(0)[i] + track_buf.channel(0)[i];
            let right = master.channel(1)[i] + track_buf.channel(1)[i];
            master.channel_mut(0)[i] = left;
            master.channel_mut(1)[i] = right;
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
    fn sum_keeps_float_headroom() {
        let mixer = Mixer::new();
        let mut t1 = AudioBuffer::new(2, 4);
        t1.channel_mut(0)[0] = 0.8;
        let mut t2 = AudioBuffer::new(2, 4);
        t2.channel_mut(0)[0] = 0.8;

        let mut master = AudioBuffer::new(2, 4);
        mixer.sum(&[t1, t2], &mut master);

        assert!((master.channel(0)[0] - 1.6).abs() < 0.001);
    }

    #[test]
    fn add_keeps_later_tracks_audible_above_unity() {
        let mixer = Mixer::new();
        let mut master = AudioBuffer::new(2, 4);

        for _ in 0..3 {
            let mut track = AudioBuffer::new(2, 4);
            track.channel_mut(0)[0] = 0.5;
            mixer.add(&track, &mut master, 4);
        }

        assert!((master.channel(0)[0] - 1.5).abs() < 0.001);
    }
}
