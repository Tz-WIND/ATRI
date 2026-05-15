use crate::audio::buffer_set::BufferSet;
use crate::midi::event::ScheduledMidiEvent;

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

/// Core abstraction for loadable audio processors (VST3, VST2, LV2, etc.).
/// Defined in atri-core so atri-engine and plugin backends can share the
/// contract without circular dependencies.
pub trait Plugin: Send + Sync {
    fn name(&self) -> &str;
    fn activate(&mut self);
    fn deactivate(&mut self);
    fn set_block_size(&mut self, nframes: usize);
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
    fn parameter_count(&self) -> u32;

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
