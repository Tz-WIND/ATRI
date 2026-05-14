use crate::audio::buffer_set::BufferSet;

/// The Plugin trait — the core abstraction for loadable audio processors
/// (VST3, VST2, LV2, etc.). Defined in atri-core so both atri-engine
/// (PluginInsert) and atri-vst3 (Vst3Plugin impl) can depend on it
/// without circular dependencies.
pub trait Plugin: Send + Sync {
    fn name(&self) -> &str;
    fn activate(&mut self);
    fn deactivate(&mut self);
    fn set_block_size(&mut self, nframes: usize);
    fn connect_and_run(
        &mut self,
        bufs: &mut BufferSet,
        start_sample: i64,
        end_sample: i64,
        speed: f64,
        nframes: usize,
    );
    fn get_parameter(&self, index: u32) -> f32;
    fn set_parameter(&mut self, index: u32, value: f32);
    fn parameter_count(&self) -> u32;
}
