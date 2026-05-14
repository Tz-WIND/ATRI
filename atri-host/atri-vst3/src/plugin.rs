use atri_core::audio::buffer_set::BufferSet;
use atri_core::plugin::Plugin;

/// A VST3 plugin instance wrapping the component and audio processor.
pub struct Vst3Plugin {
    pub name: String,
    pub input_channels: u16,
    pub output_channels: u16,
    pub active: bool,
    pub block_size: usize,
}

impl Vst3Plugin {
    pub fn new(name: String, input_channels: u16, output_channels: u16) -> Self {
        Self {
            name,
            input_channels,
            output_channels,
            active: false,
            block_size: 256,
        }
    }
}

impl Plugin for Vst3Plugin {
    fn name(&self) -> &str {
        &self.name
    }

    fn activate(&mut self) {
        self.active = true;
        log::info!("VST3 plugin '{}' activated", self.name);
    }

    fn deactivate(&mut self) {
        self.active = false;
        log::info!("VST3 plugin '{}' deactivated", self.name);
    }

    fn set_block_size(&mut self, nframes: usize) {
        self.block_size = nframes;
    }

    fn connect_and_run(
        &mut self,
        _bufs: &mut BufferSet,
        _start_sample: i64,
        _end_sample: i64,
        _speed: f64,
        _nframes: usize,
    ) {
        // Phase 1 stub: pass audio through unchanged.
        // When real VST3 COM integration is done, this will call
        // IAudioProcessor::process() with the plugin's audio buffers.
    }

    fn get_parameter(&self, _index: u32) -> f32 {
        0.0
    }

    fn set_parameter(&mut self, _index: u32, _value: f32) {}

    fn parameter_count(&self) -> u32 {
        0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_plugin_defaults() {
        let p = Vst3Plugin::new("TestSynth".into(), 2, 2);
        assert_eq!(p.name(), "TestSynth");
        assert_eq!(p.input_channels, 2);
        assert_eq!(p.output_channels, 2);
        assert!(!p.active);
        assert_eq!(p.block_size, 256);
        assert_eq!(p.parameter_count(), 0);
        assert_eq!(p.get_parameter(0), 0.0);
    }

    #[test]
    fn activate_deactivate() {
        let mut p = Vst3Plugin::new("Test".into(), 2, 2);
        assert!(!p.active);

        p.activate();
        assert!(p.active);

        p.deactivate();
        assert!(!p.active);
    }

    #[test]
    fn set_block_size() {
        let mut p = Vst3Plugin::new("Test".into(), 2, 2);
        p.set_block_size(512);
        assert_eq!(p.block_size, 512);
        p.set_block_size(64);
        assert_eq!(p.block_size, 64);
    }

    #[test]
    fn connect_and_run_is_idempotent() {
        let mut p = Vst3Plugin::new("Test".into(), 2, 2);
        let mut bufs = BufferSet::new(1, 2, 256);
        // connect_and_run on a stub should not panic
        p.connect_and_run(&mut bufs, 0, 256, 1.0, 256);
    }

    #[test]
    fn set_parameter_noop() {
        let mut p = Vst3Plugin::new("Test".into(), 2, 2);
        // Should not panic
        p.set_parameter(0, 0.5);
        p.set_parameter(100, 1.0);
        assert_eq!(p.get_parameter(0), 0.0);
    }

    #[test]
    fn plugin_is_send_sync() {
        // Static assertion via usage: Plugin trait requires Send + Sync
        fn assert_send_sync<T: Send + Sync>() {}
        assert_send_sync::<Vst3Plugin>();
    }
}
