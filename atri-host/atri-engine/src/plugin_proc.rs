use atri_core::audio::buffer_set::BufferSet;
use atri_core::midi::event::ScheduledMidiEvent;
use atri_core::plugin::{
    CapturedPluginParameterEdit, EditorParentHandle, Plugin, PluginEditorContext,
    PluginEditorHandle, PluginParameterInfo,
};
use atri_core::time::tempo::TempoMetric;

use super::processor::Processor;

/// Wraps a Plugin as a Processor, following Ardour's PluginInsert pattern.
pub struct PluginInsert {
    plugin: Box<dyn Plugin>,
    name: String,
    active: bool,
    bypass: bool,
}

impl PluginInsert {
    pub fn new(plugin: Box<dyn Plugin>) -> Self {
        let name = plugin.name().to_string();
        Self {
            plugin,
            name,
            active: false,
            bypass: false,
        }
    }

    pub fn set_bypass(&mut self, bypass: bool) {
        self.bypass = bypass;
    }
}

impl Processor for PluginInsert {
    fn name(&self) -> &str {
        &self.name
    }

    fn run(
        &mut self,
        bufs: &mut BufferSet,
        midi: &[ScheduledMidiEvent],
        start_sample: i64,
        end_sample: i64,
        speed: f64,
        nframes: usize,
        _result_required: bool,
    ) {
        if !self.active || self.bypass {
            return;
        }
        self.plugin
            .connect_and_run(bufs, midi, start_sample, end_sample, speed, nframes);
    }

    fn activate(&mut self) {
        self.active = true;
        self.plugin.activate();
    }

    fn deactivate(&mut self) {
        self.active = false;
        self.plugin.deactivate();
    }

    fn is_active(&self) -> bool {
        self.active
    }
    fn input_channels(&self) -> u16 {
        2
    }
    fn output_channels(&self) -> u16 {
        2
    }

    fn signal_latency(&self) -> usize {
        if !self.active || self.bypass {
            return 0;
        }
        self.plugin.signal_latency()
    }

    fn set_block_size(&mut self, nframes: usize) {
        self.plugin.set_block_size(nframes);
    }

    fn set_sample_rate(&mut self, sample_rate: f64) {
        self.plugin.set_sample_rate(sample_rate);
    }

    fn set_tempo_context(&mut self, metric: TempoMetric) {
        self.plugin.set_tempo_context(metric);
    }

    fn prepare_for_processing(&mut self) -> Result<(), String> {
        self.plugin.prepare_for_processing()
    }

    fn has_plugin_editor(&self) -> bool {
        self.plugin.has_editor()
    }

    fn open_plugin_editor(
        &mut self,
        parent: EditorParentHandle,
        context: PluginEditorContext,
    ) -> Result<Box<dyn PluginEditorHandle>, String> {
        self.plugin.open_editor(parent, context)
    }

    fn get_state_chunk(&mut self) -> Result<Vec<u8>, String> {
        self.plugin.get_state_chunk()
    }

    fn set_state_chunk(&mut self, chunk: &[u8]) -> Result<(), String> {
        self.plugin.set_state_chunk(chunk)
    }

    fn get_parameter(&mut self, index: u32) -> Option<f32> {
        Some(self.plugin.get_parameter(index))
    }

    fn set_parameter(&mut self, index: u32, value: f32) -> Result<(), String> {
        self.plugin.set_parameter(index, value);
        Ok(())
    }

    fn set_parameter_at_sample(
        &mut self,
        index: u32,
        sample_offset: usize,
        value: f32,
    ) -> Result<(), String> {
        self.plugin
            .set_parameter_at_sample(index, sample_offset, value);
        Ok(())
    }

    fn parameter_count(&mut self) -> u32 {
        self.plugin.parameter_count()
    }

    fn parameter_info(&mut self) -> Vec<PluginParameterInfo> {
        self.plugin.parameter_info()
    }

    fn drain_captured_parameter_edits(&mut self) -> Vec<CapturedPluginParameterEdit> {
        self.plugin.drain_captured_parameter_edits()
    }
}
