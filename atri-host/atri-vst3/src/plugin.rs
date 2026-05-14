use atri_core::audio::buffer_set::BufferSet;
use atri_core::plugin::Plugin;

/// A VST3 plugin instance wrapping the component and audio processor.
pub struct Vst3Plugin {
    name: String,
    input_channels: u16,
    output_channels: u16,
    active: bool,
    block_size: usize,
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
