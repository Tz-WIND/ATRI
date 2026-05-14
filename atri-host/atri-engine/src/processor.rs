use atri_core::audio::buffer_set::BufferSet;

/// The Processor trait — every signal processing node in a Route chain.
pub trait Processor: Send + Sync {
    fn run(
        &mut self,
        bufs: &mut BufferSet,
        start_sample: i64,
        end_sample: i64,
        speed: f64,
        nframes: usize,
        result_required: bool,
    );

    fn activate(&mut self);
    fn deactivate(&mut self);
    fn is_active(&self) -> bool;

    fn input_channels(&self) -> u16;
    fn output_channels(&self) -> u16;
    fn signal_latency(&self) -> usize { 0 }

    fn set_block_size(&mut self, _nframes: usize) {}
}

/// Simple gain processor with smooth ramping.
pub struct Gain {
    pub value: f32,
    current: f32,
    smooth: f32,
    pub target: f32,
}

impl Gain {
    pub fn new(value: f32) -> Self {
        Self { value, current: value, smooth: 0.0, target: value }
    }

    pub fn set_value(&mut self, value: f32) {
        self.target = value;
        self.smooth = 0.0;
    }
}

impl Processor for Gain {
    fn run(
        &mut self,
        bufs: &mut BufferSet,
        _start_sample: i64,
        _end_sample: i64,
        _speed: f64,
        nframes: usize,
        _result_required: bool,
    ) {
        for buf_idx in 0..bufs.len() {
            let buf = bufs.get_mut(buf_idx).unwrap();
            for ch in 0..buf.channels() {
                let channel = buf.channel_mut(ch);
                for i in 0..nframes {
                    let t = i as f32 / nframes as f32;
                    self.current = self.target * t + self.value * (1.0 - t);
                    channel[i] *= self.current;
                }
            }
        }
        self.value = self.target;
        self.current = self.target;
    }

    fn activate(&mut self) {}
    fn deactivate(&mut self) {}
    fn is_active(&self) -> bool { true }
    fn input_channels(&self) -> u16 { 2 }
    fn output_channels(&self) -> u16 { 2 }
}

/// Constant-power stereo pan processor.
pub struct Pan {
    pub value: f32,  // -1.0 (full L) .. 0.0 (center) .. 1.0 (full R)
}

impl Pan {
    pub fn new() -> Self {
        Self { value: 0.0 }
    }
}

impl Processor for Pan {
    fn run(
        &mut self,
        bufs: &mut BufferSet,
        _start_sample: i64,
        _end_sample: i64,
        _speed: f64,
        nframes: usize,
        _result_required: bool,
    ) {
        // Constant-power pan law
        use std::f32::consts::FRAC_PI_2;
        let angle = (self.value + 1.0) * 0.5 * FRAC_PI_2; // map [-1,1] → [0, π/2]
        let left_gain = angle.cos();
        let right_gain = angle.sin();

        for buf_idx in 0..bufs.len() {
            let buf = bufs.get_mut(buf_idx).unwrap();
            if buf.channels() >= 2 {
                for i in 0..nframes {
                    let l = buf.channel(0)[i];
                    let r = buf.channel(1)[i];
                    buf.channel_mut(0)[i] = l * left_gain;
                    buf.channel_mut(1)[i] = r * right_gain;
                }
            }
        }
    }

    fn activate(&mut self) {}
    fn deactivate(&mut self) {}
    fn is_active(&self) -> bool { true }
    fn input_channels(&self) -> u16 { 2 }
    fn output_channels(&self) -> u16 { 2 }
}

impl Default for Pan {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use atri_core::audio::buffer_set::BufferSet;

    #[test]
    fn test_gain_unity() {
        let mut gain = Gain::new(1.0);
        let mut bufs = BufferSet::new(1, 2, 4);
        // Fill with known values
        bufs.get_mut(0).unwrap().channel_mut(0)[0] = 0.5;
        bufs.get_mut(0).unwrap().channel_mut(1)[0] = -0.3;

        gain.run(&mut bufs, 0, 4, 1.0, 4, true);

        // Unity gain should not change values
        assert!((bufs.get(0).unwrap().channel(0)[0] - 0.5).abs() < 0.001);
        assert!((bufs.get(0).unwrap().channel(1)[0] + 0.3).abs() < 0.001);
    }

    #[test]
    fn test_gain_half() {
        let mut gain = Gain::new(0.5);
        let mut bufs = BufferSet::new(1, 2, 4);
        bufs.get_mut(0).unwrap().channel_mut(0)[0] = 1.0;

        gain.run(&mut bufs, 0, 4, 1.0, 4, true);

        assert!((bufs.get(0).unwrap().channel(0)[0] - 0.5).abs() < 0.001);
    }

    #[test]
    fn test_pan_center() {
        let mut pan = Pan::new();
        pan.value = 0.0; // center
        let mut bufs = BufferSet::new(1, 2, 4);
        bufs.get_mut(0).unwrap().channel_mut(0)[0] = 0.8;
        bufs.get_mut(0).unwrap().channel_mut(1)[0] = 0.6;

        pan.run(&mut bufs, 0, 4, 1.0, 4, true);

        // Center: angle = (0+1)*0.5*π/2 = π/4
        // left_gain = cos(π/4) = √2/2 ≈ 0.707
        let expected = 0.8 * std::f32::consts::FRAC_PI_4.cos();
        assert!((bufs.get(0).unwrap().channel(0)[0] - expected).abs() < 0.001);
    }

    #[test]
    fn test_pan_hard_left() {
        let mut pan = Pan::new();
        pan.value = -1.0; // hard left
        let mut bufs = BufferSet::new(1, 2, 4);
        bufs.get_mut(0).unwrap().channel_mut(0)[0] = 1.0;
        bufs.get_mut(0).unwrap().channel_mut(1)[0] = 1.0;

        pan.run(&mut bufs, 0, 4, 1.0, 4, true);

        // Hard left: angle = 0, left_gain = 1.0, right_gain = 0.0
        assert!((bufs.get(0).unwrap().channel(0)[0] - 1.0).abs() < 0.001);
        assert!((bufs.get(0).unwrap().channel(1)[0]).abs() < 0.001);
    }
}
