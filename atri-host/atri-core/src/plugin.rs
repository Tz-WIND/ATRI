use crate::audio::buffer_set::BufferSet;
use crate::midi::event::ScheduledMidiEvent;
use crate::time::tempo::TempoMetric;
use serde::Serialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EditorParentKind {
    WindowsHwnd,
    MacOsNsView,
    X11Window,
    XcbWindow,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct EditorParentHandle {
    pub kind: EditorParentKind,
    pub raw: u64,
}

impl EditorParentHandle {
    pub fn vst3_platform_type(self) -> &'static str {
        match self.kind {
            EditorParentKind::WindowsHwnd => "HWND",
            EditorParentKind::MacOsNsView => "NSView",
            EditorParentKind::X11Window | EditorParentKind::XcbWindow => "X11EmbedWindowID",
        }
    }
}

#[derive(Clone)]
pub struct PluginEditorContext {
    resize: std::sync::Arc<dyn Fn(u32, u32) + Send + Sync>,
}

impl PluginEditorContext {
    pub fn new(resize: impl Fn(u32, u32) + Send + Sync + 'static) -> Self {
        Self {
            resize: std::sync::Arc::new(resize),
        }
    }

    pub fn request_resize(&self, width: u32, height: u32) {
        (self.resize)(width, height);
    }
}

pub trait PluginEditorHandle: Send {
    fn resize(&mut self, _width: u32, _height: u32) {}

    fn close(&mut self);
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct PluginParameterInfo {
    pub index: u32,
    pub param_id: Option<u32>,
    pub name: String,
    pub units: String,
    pub value: f32,
    pub automatable: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct CapturedPluginParameterEdit {
    pub param_id: u32,
    pub value: f64,
    pub captured_at_millis: u128,
}

/// Core abstraction for loadable audio processors (VST3, VST2, LV2, etc.).
/// Defined in atri-core so atri-engine and plugin backends can share the
/// contract without circular dependencies.
pub trait Plugin: Send + Sync {
    fn name(&self) -> &str;
    fn activate(&mut self);
    fn deactivate(&mut self);
    fn set_block_size(&mut self, nframes: usize);
    fn set_sample_rate(&mut self, _sample_rate: f64) {}
    fn set_tempo_context(&mut self, _metric: TempoMetric) {}
    fn signal_latency(&self) -> usize {
        0
    }
    fn prepare_for_processing(&mut self) -> Result<(), String> {
        Ok(())
    }
    fn connect_and_run(
        &mut self,
        bufs: &mut BufferSet,
        midi: &[ScheduledMidiEvent],
        start_sample: i64,
        end_sample: i64,
        speed: f64,
        nframes: usize,
    );
    fn get_parameter(&self, index: u32) -> f32;
    fn set_parameter(&mut self, index: u32, value: f32);
    fn set_parameter_at_sample(&mut self, index: u32, _sample_offset: usize, value: f32) {
        self.set_parameter(index, value);
    }
    fn parameter_count(&self) -> u32;
    fn parameter_info(&self) -> Vec<PluginParameterInfo> {
        (0..self.parameter_count())
            .map(|index| PluginParameterInfo {
                index,
                param_id: None,
                name: format!("Parameter {index}"),
                units: String::new(),
                value: self.get_parameter(index),
                automatable: true,
            })
            .collect()
    }

    fn drain_captured_parameter_edits(&mut self) -> Vec<CapturedPluginParameterEdit> {
        Vec::new()
    }

    fn has_editor(&self) -> bool {
        false
    }

    fn open_editor(
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
}
