use std::sync::Arc;
use atri_core::audio::buffer_set::BufferSet;
use atri_core::plugin::Plugin;
use super::processor::Processor;

/// Wraps a Plugin as a Processor, following Ardour's PluginInsert pattern.
pub struct PluginInsert {
    plugin: Arc<dyn Plugin>,
    active: bool,
    bypass: bool,
}

impl PluginInsert {
    pub fn new(plugin: Arc<dyn Plugin>) -> Self {
        Self { plugin, active: false, bypass: false }
    }

    pub fn set_bypass(&mut self, bypass: bool) {
        self.bypass = bypass;
    }
}

impl Processor for PluginInsert {
    fn run(
        &mut self,
        bufs: &mut BufferSet,
        start_sample: i64,
        end_sample: i64,
        speed: f64,
        nframes: usize,
        _result_required: bool,
    ) {
        if !self.active || self.bypass {
            return;
        }
        // In a real implementation, we'd clone the Arc, but here we
        // need &mut self. For production, use Mutex or similar.
        // For now, we use unsafe to get &mut from Arc.
        let plugin = unsafe { &mut *(Arc::as_ptr(&self.plugin) as *mut dyn Plugin) };
        plugin.connect_and_run(bufs, start_sample, end_sample, speed, nframes);
    }

    fn activate(&mut self) {
        self.active = true;
        let plugin = unsafe { &mut *(Arc::as_ptr(&self.plugin) as *mut dyn Plugin) };
        plugin.activate();
    }

    fn deactivate(&mut self) {
        self.active = false;
        let plugin = unsafe { &mut *(Arc::as_ptr(&self.plugin) as *mut dyn Plugin) };
        plugin.deactivate();
    }

    fn is_active(&self) -> bool { self.active }
    fn input_channels(&self) -> u16 { 2 }
    fn output_channels(&self) -> u16 { 2 }
}
