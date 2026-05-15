use atri_core::audio::buffer_set::BufferSet;
use atri_core::midi::event::ScheduledMidiEvent;
use atri_core::plugin::{EditorParentHandle, PluginEditorContext, PluginEditorHandle};

/// The Processor trait — every signal processing node in a Route chain.
pub trait Processor: Send + Sync {
    /// Stable display/debug name for this processor instance.
    fn name(&self) -> &str;

    /// Process one audio block.
    ///
    /// `bufs` contains the route audio buffers to read and/or write.
    /// `midi` contains events scheduled for this block; each event's `offset`
    /// is a sample offset relative to the start of this call.
    /// `start_sample` and `end_sample` are absolute timeline sample positions.
    /// `speed` is the current transport speed.
    /// `nframes` is the number of valid frames in `bufs`.
    ///
    /// When `result_required` is false, the host only needs state advancement
    /// for this block. Processors should consume MIDI/automation and update
    /// internal state as if the block ran, but may skip expensive audio writes.
    /// Current call sites pass true; this flag exists for future pre-roll,
    /// offline analysis, or side-chain/state-only processing.
    fn run(
        &mut self,
        bufs: &mut BufferSet,
        midi: &[ScheduledMidiEvent],
        start_sample: i64,
        end_sample: i64,
        speed: f64,
        nframes: usize,
        result_required: bool,
    );

    /// Prepare processor resources before audio processing.
    fn activate(&mut self);
    /// Release or suspend resources after audio processing stops.
    fn deactivate(&mut self);
    /// Whether this processor should currently be considered active.
    fn is_active(&self) -> bool;

    /// Number of audio input channels expected by this processor.
    fn input_channels(&self) -> u16;
    /// Number of audio output channels produced by this processor.
    fn output_channels(&self) -> u16;
    /// Reported latency in samples.
    fn signal_latency(&self) -> usize {
        0
    }

    /// Notify the processor that subsequent blocks may use this block size.
    fn set_block_size(&mut self, _nframes: usize) {}

    /// Prepare plugin resources on the host/control thread before the realtime callback sees it.
    fn prepare_for_processing(&mut self) -> Result<(), String> {
        Ok(())
    }

    fn has_plugin_editor(&self) -> bool {
        false
    }

    fn open_plugin_editor(
        &mut self,
        _parent: EditorParentHandle,
        _context: PluginEditorContext,
    ) -> Result<Box<dyn PluginEditorHandle>, String> {
        Err(format!("{} does not expose a native editor", self.name()))
    }

    fn get_state_chunk(&mut self) -> Result<Vec<u8>, String> {
        Ok(Vec::new())
    }

    fn set_state_chunk(&mut self, _chunk: &[u8]) -> Result<(), String> {
        Ok(())
    }

    fn get_parameter(&mut self, _index: u32) -> Option<f32> {
        None
    }

    fn set_parameter(&mut self, _index: u32, _value: f32) -> Result<(), String> {
        Err(format!("{} does not expose plugin parameters", self.name()))
    }

    fn parameter_count(&mut self) -> u32 {
        0
    }
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
        Self {
            value,
            current: value,
            smooth: 0.0,
            target: value,
        }
    }

    pub fn set_value(&mut self, value: f32) {
        self.target = value;
        self.smooth = 0.0;
    }
}

impl Processor for Gain {
    fn name(&self) -> &str {
        "gain"
    }

    fn run(
        &mut self,
        bufs: &mut BufferSet,
        _midi: &[ScheduledMidiEvent],
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
    fn is_active(&self) -> bool {
        true
    }
    fn input_channels(&self) -> u16 {
        2
    }
    fn output_channels(&self) -> u16 {
        2
    }
}

/// Constant-power stereo pan processor.
pub struct Pan {
    pub value: f32, // -1.0 (full L) .. 0.0 (center) .. 1.0 (full R)
}

impl Pan {
    pub fn new() -> Self {
        Self { value: 0.0 }
    }
}

impl Processor for Pan {
    fn name(&self) -> &str {
        "pan"
    }

    fn run(
        &mut self,
        bufs: &mut BufferSet,
        _midi: &[ScheduledMidiEvent],
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
    fn is_active(&self) -> bool {
        true
    }
    fn input_channels(&self) -> u16 {
        2
    }
    fn output_channels(&self) -> u16 {
        2
    }
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

        gain.run(&mut bufs, &[], 0, 4, 1.0, 4, true);

        // Unity gain should not change values
        assert!((bufs.get(0).unwrap().channel(0)[0] - 0.5).abs() < 0.001);
        assert!((bufs.get(0).unwrap().channel(1)[0] + 0.3).abs() < 0.001);
    }

    #[test]
    fn test_gain_half() {
        let mut gain = Gain::new(0.5);
        let mut bufs = BufferSet::new(1, 2, 4);
        bufs.get_mut(0).unwrap().channel_mut(0)[0] = 1.0;

        gain.run(&mut bufs, &[], 0, 4, 1.0, 4, true);

        assert!((bufs.get(0).unwrap().channel(0)[0] - 0.5).abs() < 0.001);
    }

    #[test]
    fn test_pan_center() {
        let mut pan = Pan::new();
        pan.value = 0.0; // center
        let mut bufs = BufferSet::new(1, 2, 4);
        bufs.get_mut(0).unwrap().channel_mut(0)[0] = 0.8;
        bufs.get_mut(0).unwrap().channel_mut(1)[0] = 0.6;

        pan.run(&mut bufs, &[], 0, 4, 1.0, 4, true);

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

        pan.run(&mut bufs, &[], 0, 4, 1.0, 4, true);

        // Hard left: angle = 0, left_gain = 1.0, right_gain = 0.0
        assert!((bufs.get(0).unwrap().channel(0)[0] - 1.0).abs() < 0.001);
        assert!((bufs.get(0).unwrap().channel(1)[0]).abs() < 0.001);
    }
}
