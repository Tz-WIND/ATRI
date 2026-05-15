use std::cmp;
use std::mem;
use std::ptr;

use atri_core::audio::buffer_set::BufferSet;
use atri_core::midi::event::ScheduledMidiEvent;
use atri_core::plugin::{EditorParentHandle, Plugin, PluginEditorContext, PluginEditorHandle};
use vst3::{
    ComPtr, ComWrapper,
    Steinberg::{
        FUnknown, IPlugFrame, IPlugView, IPlugViewTrait, IPluginBaseTrait, TUID, ViewRect,
        Vst::{
            BusDirections_, BusInfo, IComponent, IComponent_iid, IComponentHandler,
            IComponentTrait, IConnectionPoint, IConnectionPointTrait, IEditController,
            IEditController_iid, IEditControllerTrait, MediaTypes_, ParamID, ParameterInfo,
            ViewType,
        },
        kResultFalse, kResultOk,
    },
};

use crate::factory::PluginFactory;
use crate::runtime::{
    AtriComponentHandler, AtriHostApplication, AtriPlugFrameHandle, MemoryStreamHandle,
    Vst3EditorHandle, parent_handle_as_ptr, platform_type,
};

const STATE_MAGIC: &[u8; 8] = b"ATRI3ST\0";
const STATE_VERSION: u32 = 1;
const STATE_HEADER_LEN: usize = 8 + 4 + 8 + 8;

/// A VST3 plugin instance wrapping the component, edit controller, and editor view factory path.
pub struct Vst3Plugin {
    pub name: String,
    pub input_channels: u16,
    pub output_channels: u16,
    pub active: bool,
    pub block_size: usize,
    instance: Option<Vst3Instance>,
    factory: Option<PluginFactory>,
    state_chunk: Vec<u8>,
}

impl Vst3Plugin {
    pub fn new(name: String, input_channels: u16, output_channels: u16) -> Self {
        Self {
            name,
            input_channels,
            output_channels,
            active: false,
            block_size: 256,
            instance: None,
            factory: None,
            state_chunk: Vec::new(),
        }
    }

    pub fn from_factory(name: String, factory: PluginFactory) -> Result<Self, String> {
        let instance = Vst3Instance::new(&factory, "ATRI Host")?;
        let input_channels = instance.channel_count(BusDirections_::kInput);
        let output_channels = instance.channel_count(BusDirections_::kOutput);

        Ok(Self {
            name,
            input_channels,
            output_channels,
            active: false,
            block_size: 256,
            instance: Some(instance),
            factory: Some(factory),
            state_chunk: Vec::new(),
        })
    }

    pub fn from_factory_deferred(name: String, factory: PluginFactory) -> Self {
        Self {
            name,
            input_channels: 2,
            output_channels: 2,
            active: false,
            block_size: 256,
            instance: None,
            factory: Some(factory),
            state_chunk: Vec::new(),
        }
    }

    pub fn is_library_loaded(&self) -> bool {
        self.factory
            .as_ref()
            .map(PluginFactory::is_loaded)
            .unwrap_or(false)
    }
}

impl Plugin for Vst3Plugin {
    fn name(&self) -> &str {
        &self.name
    }

    fn activate(&mut self) {
        self.active = true;
        if let Some(instance) = &self.instance {
            if let Err(err) = instance.set_active(true) {
                log::warn!("failed to activate VST3 plugin '{}': {err}", self.name);
            }
        }
        log::info!("VST3 plugin '{}' activated", self.name);
    }

    fn deactivate(&mut self) {
        self.active = false;
        if let Some(instance) = &self.instance {
            if let Err(err) = instance.set_active(false) {
                log::warn!("failed to deactivate VST3 plugin '{}': {err}", self.name);
            }
        }
        log::info!("VST3 plugin '{}' deactivated", self.name);
    }

    fn set_block_size(&mut self, nframes: usize) {
        self.block_size = nframes;
    }

    fn connect_and_run(
        &mut self,
        _bufs: &mut BufferSet,
        _midi: &[ScheduledMidiEvent],
        _start_sample: i64,
        _end_sample: i64,
        _speed: f64,
        _nframes: usize,
    ) {
        // This wrapper now owns real VST3 component/controller/editor objects.
        // Audio process bridging remains isolated behind the Plugin trait.
    }

    fn get_parameter(&self, index: u32) -> f32 {
        let Some(instance) = &self.instance else {
            return 0.0;
        };
        let Some(controller) = instance.controller.as_ref() else {
            return 0.0;
        };
        let Some(id) = instance.parameter_id_at(index) else {
            return 0.0;
        };
        unsafe { controller.getParamNormalized(id) as f32 }
    }

    fn set_parameter(&mut self, index: u32, value: f32) {
        let Some(factory) = &self.factory else {
            return;
        };
        let Some(instance) = &mut self.instance else {
            return;
        };
        if let Err(err) = instance.ensure_controller(factory) {
            log::warn!(
                "failed to create VST3 controller for '{}': {err}",
                self.name
            );
            return;
        }
        let Some(controller) = instance.controller.as_ref() else {
            return;
        };
        let Some(id) = instance.parameter_id_at(index) else {
            return;
        };
        let value = value.clamp(0.0, 1.0) as f64;
        unsafe {
            let _ = controller.setParamNormalized(id, value);
        }
    }

    fn parameter_count(&self) -> u32 {
        self.instance
            .as_ref()
            .and_then(|instance| instance.controller.as_ref())
            .map(|controller| unsafe { controller.getParameterCount().max(0) as u32 })
            .unwrap_or(0)
    }

    fn has_editor(&self) -> bool {
        self.instance.is_some() || self.factory.is_some()
    }

    fn open_editor(
        &mut self,
        parent: EditorParentHandle,
        context: PluginEditorContext,
    ) -> Result<Box<dyn PluginEditorHandle>, String> {
        log::info!(
            "opening VST3 editor for '{}' on platform {}",
            self.name,
            parent.vst3_platform_type()
        );
        self.ensure_instance()?;
        let factory = self
            .factory
            .as_ref()
            .ok_or_else(|| format!("VST3 plugin '{}' factory is not loaded", self.name))?;
        let instance = self
            .instance
            .as_mut()
            .ok_or_else(|| format!("VST3 plugin '{}' is not loaded", self.name))?;
        let pending_state = if self.state_chunk.is_empty() {
            None
        } else {
            Some(decode_state_chunk(&self.state_chunk)?)
        };
        let handle = instance.open_editor(factory, parent, context, pending_state.as_ref())?;
        log::info!("VST3 editor opened for '{}'", self.name);
        Ok(Box::new(handle))
    }

    fn get_state_chunk(&mut self) -> Result<Vec<u8>, String> {
        let Some(instance) = &self.instance else {
            return Ok(self.state_chunk.clone());
        };

        let component = instance.component_state();
        let controller = if instance.controller.is_some() {
            instance.controller_state()
        } else {
            Err("edit controller has not been created yet".to_string())
        };
        let chunk = match (component, controller) {
            (Ok(component), Ok(controller)) => encode_state_chunk(&component, Some(&controller)),
            (Ok(component), Err(err)) => {
                log::debug!(
                    "VST3 controller state for '{}' is unavailable: {err}",
                    self.name
                );
                encode_state_chunk(&component, None)
            }
            (Err(err), Ok(controller)) => {
                log::debug!(
                    "VST3 component state for '{}' is unavailable: {err}",
                    self.name
                );
                encode_state_chunk(&[], Some(&controller))
            }
            (Err(component_err), Err(controller_err)) => {
                return Err(format!(
                    "failed to read VST3 state: component: {component_err}; controller: {controller_err}"
                ));
            }
        };
        self.state_chunk = chunk.clone();
        Ok(chunk)
    }

    fn set_state_chunk(&mut self, chunk: &[u8]) -> Result<(), String> {
        let parts = decode_state_chunk(chunk)?;
        if let Some(instance) = &mut self.instance {
            instance.apply_state(&parts)?;
        }
        self.state_chunk.clear();
        self.state_chunk.extend_from_slice(chunk);
        Ok(())
    }
}

impl Vst3Plugin {
    fn ensure_instance(&mut self) -> Result<(), String> {
        if self.instance.is_some() {
            return Ok(());
        }

        let factory = self
            .factory
            .as_ref()
            .ok_or_else(|| format!("VST3 plugin '{}' factory is not loaded", self.name))?;
        log::info!(
            "creating VST3 component for '{}' on host UI thread",
            self.name
        );
        let mut instance = Vst3Instance::new(factory, "ATRI Host")?;
        self.input_channels = instance.channel_count(BusDirections_::kInput);
        self.output_channels = instance.channel_count(BusDirections_::kOutput);
        if !self.state_chunk.is_empty() {
            let parts = decode_state_chunk(&self.state_chunk)?;
            instance.apply_state(&parts)?;
        }
        if self.active {
            instance.set_active(true)?;
        }
        self.instance = Some(instance);
        log::info!(
            "created VST3 component for '{}' on host UI thread",
            self.name
        );
        Ok(())
    }
}

struct Vst3Instance {
    component: ComPtr<IComponent>,
    controller: Option<ComPtr<IEditController>>,
    component_connection: Option<ComPtr<IConnectionPoint>>,
    controller_connection: Option<ComPtr<IConnectionPoint>>,
    controller_is_component: bool,
    _host_app: ComWrapper<AtriHostApplication>,
    component_handler: Option<ComWrapper<AtriComponentHandler>>,
}

impl Vst3Instance {
    fn new(factory: &PluginFactory, host_name: &str) -> Result<Self, String> {
        let component_class = factory.first_component_class()?;
        let host_app = ComWrapper::new(AtriHostApplication::new(host_name));
        let host_context = host_app
            .to_com_ptr::<FUnknown>()
            .expect("AtriHostApplication exposes FUnknown");
        factory.set_host_context(host_context.as_ptr());

        let component = factory.create_instance::<IComponent>(
            &component_class.cid,
            &IComponent_iid,
            "component",
        )?;
        check_result("VST3 component initialize", unsafe {
            component.initialize(host_context.as_ptr())
        })?;

        let instance = Self {
            component,
            controller: None,
            component_connection: None,
            controller_connection: None,
            controller_is_component: false,
            _host_app: host_app,
            component_handler: None,
        };
        Ok(instance)
    }

    fn set_active(&self, active: bool) -> Result<(), String> {
        check_result("VST3 component setActive", unsafe {
            self.component.setActive(if active { 1 } else { 0 })
        })
    }

    fn channel_count(&self, direction: i32) -> u16 {
        let count = unsafe { self.component.getBusCount(MediaTypes_::kAudio, direction) };
        if count <= 0 {
            return 0;
        }

        let mut channels = 0_i32;
        for index in 0..count {
            let mut bus = unsafe { mem::zeroed::<BusInfo>() };
            let result = unsafe {
                self.component
                    .getBusInfo(MediaTypes_::kAudio, direction, index, &mut bus)
            };
            if result == kResultOk && bus.channelCount > 0 {
                channels = channels.saturating_add(bus.channelCount);
            }
        }
        cmp::min(channels, u16::MAX as i32) as u16
    }

    fn parameter_id_at(&self, index: u32) -> Option<ParamID> {
        let controller = self.controller.as_ref()?;
        let mut info = unsafe { mem::zeroed::<ParameterInfo>() };
        let result = unsafe { controller.getParameterInfo(index.try_into().ok()?, &mut info) };
        (result == kResultOk).then_some(info.id)
    }

    fn ensure_controller(&mut self, factory: &PluginFactory) -> Result<bool, String> {
        if self.controller.is_some() {
            return Ok(false);
        }

        log::info!("creating VST3 edit controller on plugin editor thread");
        let host_context = self
            ._host_app
            .to_com_ptr::<FUnknown>()
            .expect("AtriHostApplication exposes FUnknown");
        let (controller, controller_is_component) =
            create_edit_controller(factory, &self.component, host_context.as_ptr())?;
        let component_connection = self.component.cast::<IConnectionPoint>();
        let controller_connection = controller.cast::<IConnectionPoint>();
        connect_component_and_controller(&component_connection, &controller_connection);

        let component_handler = ComWrapper::new(AtriComponentHandler);
        let handler = component_handler
            .to_com_ptr::<IComponentHandler>()
            .expect("AtriComponentHandler exposes IComponentHandler");
        let handler_result = unsafe { controller.setComponentHandler(handler.as_ptr()) };
        if handler_result != kResultOk {
            log::debug!("VST3 controller setComponentHandler returned {handler_result}");
        }

        self.controller = Some(controller);
        self.component_connection = component_connection;
        self.controller_connection = controller_connection;
        self.controller_is_component = controller_is_component;
        self.component_handler = Some(component_handler);
        self.sync_component_state_to_controller();
        log::info!("created VST3 edit controller on plugin editor thread");
        Ok(true)
    }

    fn open_editor(
        &mut self,
        factory: &PluginFactory,
        parent: EditorParentHandle,
        context: PluginEditorContext,
        pending_state: Option<&StateParts>,
    ) -> Result<Vst3EditorHandle, String> {
        let created_controller = self.ensure_controller(factory)?;
        if created_controller {
            if let Some(parts) = pending_state {
                self.apply_controller_state(parts);
            }
        }
        let controller = self
            .controller
            .as_ref()
            .ok_or_else(|| "VST3 edit controller is not available".to_string())?;

        log::info!("VST3 createView(editor) start");
        let view = unsafe { controller.createView(ViewType::kEditor) };
        log::info!("VST3 createView(editor) returned");
        let view = unsafe { ComPtr::from_raw(view) }
            .ok_or_else(|| "VST3 controller returned no editor IPlugView".to_string())?;
        let platform = platform_type(parent);
        log::info!(
            "VST3 isPlatformTypeSupported({}) start",
            parent.vst3_platform_type()
        );
        let supported = unsafe { view.isPlatformTypeSupported(platform) };
        log::info!(
            "VST3 isPlatformTypeSupported({}) returned {supported}",
            parent.vst3_platform_type()
        );
        if supported == kResultFalse {
            return Err(format!(
                "VST3 editor does not support platform type {}",
                parent.vst3_platform_type()
            ));
        }
        if supported != kResultOk {
            return Err(format!(
                "VST3 editor platform check for {} failed with tresult {supported}",
                parent.vst3_platform_type()
            ));
        }

        log::info!("VST3 editor getSize start");
        let initial_rect = view_rect(&view);
        match initial_rect {
            Some(rect) => log::info!(
                "VST3 editor getSize returned: left={}, top={}, right={}, bottom={}",
                rect.left,
                rect.top,
                rect.right,
                rect.bottom
            ),
            None => log::info!("VST3 editor getSize returned no usable size"),
        }
        if let Some(rect) = initial_rect {
            context.request_resize(rect_width(rect), rect_height(rect));
        }

        let frame = AtriPlugFrameHandle::new(context);
        let frame_ptr = frame
            .as_com_ptr::<IPlugFrame>()
            .expect("AtriPlugFrame exposes IPlugFrame");
        log::info!("VST3 editor setFrame start");
        check_result("VST3 editor setFrame", unsafe {
            view.setFrame(frame_ptr.as_ptr())
        })?;
        log::info!("VST3 editor setFrame returned ok");

        log::info!("VST3 editor attached(HWND) start");
        let attached = unsafe { view.attached(parent_handle_as_ptr(parent), platform) };
        log::info!("VST3 editor attached(HWND) returned {attached}");
        if attached != kResultOk {
            unsafe {
                let _ = view.setFrame(ptr::null_mut());
                let _ = view.removed();
            }
            return Err(format!("VST3 editor attach failed with tresult {attached}"));
        }
        let _ = frame.mark_attached();

        Ok(Vst3EditorHandle::new(view, frame))
    }

    fn component_state(&self) -> Result<Vec<u8>, String> {
        let stream = MemoryStreamHandle::empty();
        let result = stream.with_stream(|ptr| unsafe { self.component.getState(ptr) });
        check_result("VST3 component getState", result)?;
        Ok(stream.bytes())
    }

    fn controller_state(&self) -> Result<Vec<u8>, String> {
        let controller = self
            .controller
            .as_ref()
            .ok_or_else(|| "edit controller has not been created yet".to_string())?;
        let stream = MemoryStreamHandle::empty();
        let result = stream.with_stream(|ptr| unsafe { controller.getState(ptr) });
        check_result("VST3 controller getState", result)?;
        Ok(stream.bytes())
    }

    fn apply_state(&mut self, parts: &StateParts) -> Result<(), String> {
        if !parts.component.is_empty() {
            self.set_component_state(&parts.component)?;
            if self.controller.is_some() {
                self.set_component_state_on_controller(&parts.component);
            }
        }
        if self.controller.is_some() {
            self.apply_controller_state(parts);
        }
        Ok(())
    }

    fn apply_controller_state(&self, parts: &StateParts) {
        if !parts.component.is_empty() {
            self.set_component_state_on_controller(&parts.component);
        }
        if let Some(controller) = parts
            .controller
            .as_deref()
            .filter(|state| !state.is_empty())
        {
            self.set_controller_state(controller);
        }
    }

    fn set_component_state(&self, state: &[u8]) -> Result<(), String> {
        let stream = MemoryStreamHandle::from_bytes(state);
        let result = stream.with_stream(|ptr| unsafe { self.component.setState(ptr) });
        check_result("VST3 component setState", result)
    }

    fn set_component_state_on_controller(&self, state: &[u8]) {
        let Some(controller) = self.controller.as_ref() else {
            return;
        };
        let stream = MemoryStreamHandle::from_bytes(state);
        let result = stream.with_stream(|ptr| unsafe { controller.setComponentState(ptr) });
        if result != kResultOk {
            log::debug!("VST3 controller setComponentState returned {result}");
        }
    }

    fn set_controller_state(&self, state: &[u8]) {
        let Some(controller) = self.controller.as_ref() else {
            return;
        };
        let stream = MemoryStreamHandle::from_bytes(state);
        let result = stream.with_stream(|ptr| unsafe { controller.setState(ptr) });
        if result != kResultOk {
            log::debug!("VST3 controller setState returned {result}");
        }
    }

    fn sync_component_state_to_controller(&self) {
        match self.component_state() {
            Ok(state) if !state.is_empty() => {
                self.set_component_state_on_controller(&state);
            }
            Ok(_) => {}
            Err(err) => {
                log::debug!("VST3 component default state unavailable: {err}");
            }
        }
    }
}

impl Drop for Vst3Instance {
    fn drop(&mut self) {
        unsafe {
            disconnect_component_and_controller(
                &self.component_connection,
                &self.controller_connection,
            );
            if let Some(controller) = &self.controller {
                let _ = controller.setComponentHandler(ptr::null_mut());
                if !self.controller_is_component {
                    let _ = controller.terminate();
                }
            }
            let _ = self.component.setActive(0);
            let _ = self.component.terminate();
        }
    }
}

fn create_edit_controller(
    factory: &PluginFactory,
    component: &ComPtr<IComponent>,
    host_context: *mut FUnknown,
) -> Result<(ComPtr<IEditController>, bool), String> {
    if let Some(controller) = component.cast::<IEditController>() {
        return Ok((controller, true));
    }

    let controller_cid = controller_class_id(factory, component)?;
    let controller = factory.create_instance::<IEditController>(
        &controller_cid,
        &IEditController_iid,
        "edit controller",
    )?;
    check_result("VST3 edit controller initialize", unsafe {
        controller.initialize(host_context)
    })?;
    Ok((controller, false))
}

fn connect_component_and_controller(
    component_connection: &Option<ComPtr<IConnectionPoint>>,
    controller_connection: &Option<ComPtr<IConnectionPoint>>,
) {
    let (Some(component_connection), Some(controller_connection)) =
        (component_connection, controller_connection)
    else {
        return;
    };

    let component_result = unsafe { component_connection.connect(controller_connection.as_ptr()) };
    if component_result != kResultOk {
        log::debug!("VST3 component connection returned {component_result}");
    }
    let controller_result = unsafe { controller_connection.connect(component_connection.as_ptr()) };
    if controller_result != kResultOk {
        log::debug!("VST3 controller connection returned {controller_result}");
    }
}

unsafe fn disconnect_component_and_controller(
    component_connection: &Option<ComPtr<IConnectionPoint>>,
    controller_connection: &Option<ComPtr<IConnectionPoint>>,
) {
    let (Some(component_connection), Some(controller_connection)) =
        (component_connection, controller_connection)
    else {
        return;
    };

    unsafe {
        let _ = component_connection.disconnect(controller_connection.as_ptr());
        let _ = controller_connection.disconnect(component_connection.as_ptr());
    }
}

fn controller_class_id(
    factory: &PluginFactory,
    component: &ComPtr<IComponent>,
) -> Result<TUID, String> {
    let mut controller_cid: TUID = [0; 16];
    let result = unsafe { component.getControllerClassId(&mut controller_cid) };
    if result == kResultOk && controller_cid != [0; 16] {
        return Ok(controller_cid);
    }

    factory
        .controller_class()?
        .map(|class| class.cid)
        .ok_or_else(|| {
            format!("VST3 component did not report an edit-controller class (tresult {result})")
        })
}

fn view_rect(view: &ComPtr<IPlugView>) -> Option<ViewRect> {
    let mut rect = ViewRect {
        left: 0,
        top: 0,
        right: 0,
        bottom: 0,
    };
    let result = unsafe { view.getSize(&mut rect) };
    if result != kResultOk {
        return None;
    }

    let width = rect.right.saturating_sub(rect.left);
    let height = rect.bottom.saturating_sub(rect.top);
    (width > 0 && height > 0).then_some(rect)
}

fn rect_width(rect: ViewRect) -> u32 {
    rect.right.saturating_sub(rect.left).max(1) as u32
}

fn rect_height(rect: ViewRect) -> u32 {
    rect.bottom.saturating_sub(rect.top).max(1) as u32
}

fn check_result(label: &str, result: i32) -> Result<(), String> {
    if result == kResultOk {
        Ok(())
    } else {
        Err(format!("{label} failed with tresult {result}"))
    }
}

struct StateParts {
    component: Vec<u8>,
    controller: Option<Vec<u8>>,
}

fn encode_state_chunk(component: &[u8], controller: Option<&[u8]>) -> Vec<u8> {
    let controller_len = controller
        .map(|state| state.len() as u64)
        .unwrap_or(u64::MAX);
    let mut chunk = Vec::with_capacity(
        STATE_HEADER_LEN
            .saturating_add(component.len())
            .saturating_add(controller.map(|state| state.len()).unwrap_or(0)),
    );
    chunk.extend_from_slice(STATE_MAGIC);
    chunk.extend_from_slice(&STATE_VERSION.to_le_bytes());
    chunk.extend_from_slice(&(component.len() as u64).to_le_bytes());
    chunk.extend_from_slice(&controller_len.to_le_bytes());
    chunk.extend_from_slice(component);
    if let Some(controller) = controller {
        chunk.extend_from_slice(controller);
    }
    chunk
}

fn decode_state_chunk(chunk: &[u8]) -> Result<StateParts, String> {
    if chunk.is_empty() {
        return Ok(StateParts {
            component: Vec::new(),
            controller: None,
        });
    }
    if !chunk.starts_with(STATE_MAGIC) {
        return Ok(StateParts {
            component: chunk.to_vec(),
            controller: None,
        });
    }
    if chunk.len() < STATE_HEADER_LEN {
        return Err("VST3 state chunk header is truncated".to_string());
    }

    let version = read_u32_le(chunk, 8).ok_or("VST3 state chunk version is truncated")?;
    if version != STATE_VERSION {
        return Err(format!("unsupported VST3 state chunk version {version}"));
    }

    let component_len = read_u64_le(chunk, 12).ok_or("VST3 component state length is truncated")?;
    let controller_len =
        read_u64_le(chunk, 20).ok_or("VST3 controller state length is truncated")?;
    let component_len = usize::try_from(component_len)
        .map_err(|_| "VST3 component state is too large".to_string())?;
    let has_controller = controller_len != u64::MAX;
    let controller_len = if has_controller {
        usize::try_from(controller_len)
            .map_err(|_| "VST3 controller state is too large".to_string())?
    } else {
        0
    };
    let total_len = STATE_HEADER_LEN
        .checked_add(component_len)
        .and_then(|len| len.checked_add(controller_len))
        .ok_or_else(|| "VST3 state chunk length overflow".to_string())?;
    if chunk.len() != total_len {
        return Err(format!(
            "VST3 state chunk length mismatch: expected {total_len}, got {}",
            chunk.len()
        ));
    }

    let component_start = STATE_HEADER_LEN;
    let controller_start = component_start + component_len;
    Ok(StateParts {
        component: chunk[component_start..controller_start].to_vec(),
        controller: has_controller
            .then(|| chunk[controller_start..controller_start + controller_len].to_vec()),
    })
}

fn read_u32_le(bytes: &[u8], offset: usize) -> Option<u32> {
    let data = bytes.get(offset..offset + 4)?;
    Some(u32::from_le_bytes(data.try_into().ok()?))
}

fn read_u64_le(bytes: &[u8], offset: usize) -> Option<u64> {
    let data = bytes.get(offset..offset + 8)?;
    Some(u64::from_le_bytes(data.try_into().ok()?))
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
        assert!(!p.is_library_loaded());
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
        p.connect_and_run(&mut bufs, &[], 0, 256, 1.0, 256);
    }

    #[test]
    fn set_parameter_noop_without_controller() {
        let mut p = Vst3Plugin::new("Test".into(), 2, 2);
        p.set_parameter(0, 0.5);
        p.set_parameter(100, 1.0);
        assert_eq!(p.get_parameter(0), 0.0);
    }

    #[test]
    fn state_chunk_envelope_round_trips() {
        let component = vec![1, 2, 3];
        let controller = vec![4, 5];
        let chunk = encode_state_chunk(&component, Some(&controller));
        let parts = decode_state_chunk(&chunk).unwrap();
        assert_eq!(parts.component, component);
        assert_eq!(parts.controller, Some(controller));
    }

    #[test]
    fn legacy_state_chunk_is_component_state() {
        let chunk = vec![9, 8, 7];
        let parts = decode_state_chunk(&chunk).unwrap();
        assert_eq!(parts.component, chunk);
        assert!(parts.controller.is_none());
    }

    #[test]
    fn plugin_is_send_sync() {
        fn assert_send_sync<T: Send + Sync>() {}
        assert_send_sync::<Vst3Plugin>();
    }
}
