use std::cmp;
use std::mem;
use std::ptr;
use std::sync::{Arc, Mutex};

use atri_core::audio::buffer::AudioBuffer;
use atri_core::audio::buffer_set::BufferSet;
use atri_core::midi::event::ScheduledMidiEvent;
use atri_core::plugin::{
    CapturedPluginParameterEdit, EditorParentHandle, Plugin, PluginEditorContext,
    PluginEditorHandle, PluginParameterInfo,
};
use atri_core::time::tempo::{Meter, Tempo, TempoMetric};
use vst3::{
    ComPtr, ComWrapper,
    Steinberg::{
        FUnknown, IPlugFrame, IPlugView, IPlugViewTrait, IPluginBaseTrait, TUID, ViewRect,
        Vst::{
            AudioBusBuffers, AudioBusBuffers__type0, BusDirections_, BusInfo, IAudioProcessor,
            IAudioProcessorTrait, IComponent, IComponent_iid, IComponentHandler, IComponentTrait,
            IConnectionPoint, IConnectionPointTrait, IEditController, IEditController_iid,
            IEditControllerTrait, IoModes_, MediaTypes_, ParamID, ParamValue, ParameterInfo,
            ParameterInfo_, ProcessContext, ProcessContext_, ProcessData, ProcessModes_,
            ProcessSetup, SpeakerArr, SpeakerArrangement, SymbolicSampleSizes_, ViewType,
        },
        kResultFalse, kResultOk,
    },
};

use crate::factory::PluginFactory;
use crate::runtime::{
    AtriComponentHandler, AtriHostApplication, AtriPlugFrameHandle, MemoryStreamHandle,
    ParameterChangePoint, ParameterChangesHandle, Vst3EditorHandle, Vst3EventListHandle,
    parent_handle_as_ptr, platform_type,
};

const DEFAULT_SAMPLE_RATE: f64 = 48_000.0;
const MIN_REALTIME_PROCESSING_MAX_SAMPLES: usize = 4096;
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
    sample_rate: f64,
    tempo_metric: TempoMetric,
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
            sample_rate: DEFAULT_SAMPLE_RATE,
            tempo_metric: default_tempo_metric(),
            instance: None,
            factory: None,
            state_chunk: Vec::new(),
        }
    }

    pub fn from_factory(name: String, factory: PluginFactory) -> Result<Self, String> {
        let instance = Vst3Instance::new(&factory, "ATRI Host")?;
        let input_channels = instance.input_channels;
        let output_channels = instance.output_channels;

        Ok(Self {
            name,
            input_channels,
            output_channels,
            active: false,
            block_size: 256,
            sample_rate: DEFAULT_SAMPLE_RATE,
            tempo_metric: default_tempo_metric(),
            instance: Some(instance),
            factory: Some(factory),
            state_chunk: Vec::new(),
        })
    }

    pub fn from_factory_deferred(name: String, factory: PluginFactory) -> Self {
        Self::from_factory_deferred_with_sample_rate(name, factory, DEFAULT_SAMPLE_RATE)
    }

    pub fn from_factory_deferred_with_sample_rate(
        name: String,
        factory: PluginFactory,
        sample_rate: f64,
    ) -> Self {
        Self {
            name,
            input_channels: 2,
            output_channels: 2,
            active: false,
            block_size: 256,
            sample_rate: sample_rate.max(1.0),
            tempo_metric: default_tempo_metric(),
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
        if let Some(instance) = &mut self.instance {
            if let Err(err) = instance.set_active(true) {
                log::warn!("failed to activate VST3 plugin '{}': {err}", self.name);
            }
        }
        log::info!("VST3 plugin '{}' activated", self.name);
    }

    fn deactivate(&mut self) {
        self.active = false;
        if let Some(instance) = &mut self.instance {
            if let Err(err) = instance.set_active(false) {
                log::warn!("failed to deactivate VST3 plugin '{}': {err}", self.name);
            }
        }
        log::info!("VST3 plugin '{}' deactivated", self.name);
    }

    fn set_block_size(&mut self, nframes: usize) {
        self.block_size = nframes;
        if let Some(instance) = &mut self.instance {
            if instance.processing_active {
                let max_samples = processing_max_samples_for_block_size(self.block_size);
                if let Err(err) = instance.ensure_processing(self.sample_rate, max_samples) {
                    log::warn!(
                        "failed to update VST3 block size for '{}': {err}",
                        self.name
                    );
                }
            }
        }
    }

    fn set_sample_rate(&mut self, sample_rate: f64) {
        self.sample_rate = sample_rate.max(1.0);
        if let Some(instance) = &mut self.instance {
            if instance.processing_active {
                let max_samples = processing_max_samples_for_block_size(self.block_size);
                if let Err(err) = instance.ensure_processing(self.sample_rate, max_samples) {
                    log::warn!(
                        "failed to update VST3 sample rate for '{}': {err}",
                        self.name
                    );
                }
            }
        }
    }

    fn set_tempo_context(&mut self, metric: TempoMetric) {
        self.tempo_metric = metric;
    }

    fn signal_latency(&self) -> usize {
        self.instance
            .as_ref()
            .map(Vst3Instance::signal_latency)
            .unwrap_or(0)
    }

    fn prepare_for_processing(&mut self) -> Result<(), String> {
        self.ensure_instance()?;
        let pending_state = if self.state_chunk.is_empty() {
            None
        } else {
            Some(decode_state_chunk(&self.state_chunk)?)
        };
        let factory = self.factory.as_ref();
        if let Some(instance) = &mut self.instance {
            let created_controller = instance.ensure_controller(factory)?;
            if created_controller {
                if let Some(parts) = pending_state.as_ref() {
                    instance.apply_controller_state(parts);
                }
            }
            let max_samples = processing_max_samples_for_block_size(self.block_size);
            instance.ensure_processing(self.sample_rate, max_samples)?;
        }
        Ok(())
    }

    fn connect_and_run(
        &mut self,
        bufs: &mut BufferSet,
        midi: &[ScheduledMidiEvent],
        start_sample: i64,
        _end_sample: i64,
        speed: f64,
        nframes: usize,
    ) {
        if !self.active {
            return;
        }

        let Some(instance) = &mut self.instance else {
            return;
        };

        // Diagnostic: flag buffer size / sample rate mismatches at runtime.
        if nframes != self.block_size {
            log::debug!(
                "[{}] block size mismatch: plugin.block_size={}, actual nframes={}, sample_rate={}",
                self.name,
                self.block_size,
                nframes,
                self.sample_rate
            );
        }
        if !midi.is_empty() {
            log::trace!(
                "[{}] midi events: nframes={}, events={}, start_sample={}",
                self.name,
                nframes,
                midi.len(),
                start_sample
            );
        }

        if let Err(err) = instance.process_audio(
            bufs,
            midi,
            start_sample,
            speed,
            nframes,
            self.sample_rate,
            self.block_size,
            self.tempo_metric,
        ) {
            log::warn!("VST3 process failed for '{}': {err}", self.name);
        }
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
        if let Err(err) = self.ensure_instance() {
            log::warn!(
                "failed to create VST3 instance for parameter edit '{}': {err}",
                self.name
            );
            return;
        };
        let factory = self.factory.as_ref();
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
        if let Err(err) = instance.set_parameter_normalized(index, value) {
            log::warn!("failed to set VST3 parameter for '{}': {err}", self.name);
        }
    }

    fn set_parameter_at_sample(&mut self, index: u32, sample_offset: usize, value: f32) {
        if let Err(err) = self.ensure_instance() {
            log::warn!(
                "failed to create VST3 instance for automated parameter edit '{}': {err}",
                self.name
            );
            return;
        };
        let factory = self.factory.as_ref();
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
        if let Err(err) = instance.set_parameter_normalized_at_sample(index, sample_offset, value) {
            log::warn!(
                "failed to automate VST3 parameter for '{}': {err}",
                self.name
            );
        }
    }

    fn parameter_count(&self) -> u32 {
        self.instance
            .as_ref()
            .and_then(|instance| instance.controller.as_ref())
            .map(|controller| unsafe { controller.getParameterCount().max(0) as u32 })
            .unwrap_or(0)
    }

    fn parameter_info(&self) -> Vec<PluginParameterInfo> {
        let Some(instance) = &self.instance else {
            return Vec::new();
        };
        instance.parameter_info()
    }

    fn drain_captured_parameter_edits(&mut self) -> Vec<CapturedPluginParameterEdit> {
        self.instance
            .as_mut()
            .map(Vst3Instance::drain_captured_parameter_edits)
            .unwrap_or_default()
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
            .as_mut()
            .ok_or_else(|| format!("VST3 plugin '{}' factory is not loaded", self.name))?;
        // Phase 2 of plugin loading: InitDll + GetPluginFactory on the
        // current thread (main/editor thread), so Qt-based plugins
        // initialize QApplication on the correct thread.
        factory.initialize()?;
        log::info!(
            "creating VST3 component for '{}' on host UI thread",
            self.name
        );
        let mut instance = Vst3Instance::new(factory, "ATRI Host")?;
        self.input_channels = instance.input_channels;
        self.output_channels = instance.output_channels;
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
    audio_processor: Option<ComPtr<IAudioProcessor>>,
    input_channels: u16,
    output_channels: u16,
    audio_input_bus_count: i32,
    audio_output_bus_count: i32,
    controller: Option<ComPtr<IEditController>>,
    component_connection: Option<ComPtr<IConnectionPoint>>,
    controller_connection: Option<ComPtr<IConnectionPoint>>,
    controller_is_component: bool,
    _host_app: ComWrapper<AtriHostApplication>,
    component_handler: Option<ComWrapper<AtriComponentHandler>>,
    processing_active: bool,
    processing_sample_rate: f64,
    processing_max_samples: usize,
    queued_parameter_changes: Vec<ParameterChangePoint>,
    captured_parameter_edits: Arc<Mutex<Vec<CapturedPluginParameterEdit>>>,
    process_scratch: ProcessScratch,
}

struct ProcessScratch {
    input_channel_ptrs: Vec<*mut f32>,
    output_channel_ptrs: Vec<*mut f32>,
    input_events: Vst3EventListHandle,
    output_events: Vst3EventListHandle,
    input_params: ParameterChangesHandle,
    output_params: ParameterChangesHandle,
    parameter_changes: Vec<ParameterChangePoint>,
}

// The cached raw channel pointers are refreshed for each process call and are
// only dereferenced by the VST3 processor during that call.
unsafe impl Send for ProcessScratch {}
unsafe impl Sync for ProcessScratch {}

impl Default for ProcessScratch {
    fn default() -> Self {
        Self {
            input_channel_ptrs: Vec::new(),
            output_channel_ptrs: Vec::new(),
            input_events: Vst3EventListHandle::empty(),
            output_events: Vst3EventListHandle::empty(),
            input_params: ParameterChangesHandle::empty(),
            output_params: ParameterChangesHandle::empty(),
            parameter_changes: Vec::new(),
        }
    }
}

impl ProcessScratch {
    fn reserve_audio_buffers(&mut self, input_channels: usize, output_channels: usize) {
        self.input_channel_ptrs.reserve(input_channels);
        self.output_channel_ptrs.reserve(output_channels);
    }

    fn prepare_audio_buffers(
        &mut self,
        buffer: &mut AudioBuffer,
        input_channels: usize,
        output_channels: usize,
    ) {
        self.input_channel_ptrs.clear();
        self.output_channel_ptrs.clear();
        self.reserve_audio_buffers(input_channels, output_channels);

        let shared_channel_count = input_channels.max(output_channels);
        for channel in 0..shared_channel_count {
            let channel_ptr = buffer.channel_mut(channel as u16).as_mut_ptr();
            if channel < input_channels {
                self.input_channel_ptrs.push(channel_ptr);
            }
            if channel < output_channels {
                self.output_channel_ptrs.push(channel_ptr);
            }
        }
    }

    fn prepare_events_and_parameters(&mut self, midi: &[ScheduledMidiEvent], nframes: usize) {
        self.input_events.set_midi(midi, nframes);
        self.output_events.clear();
        self.input_params.set_changes(&self.parameter_changes);
        self.output_params.clear();
    }
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
        let audio_processor = component.cast::<IAudioProcessor>();
        let input_channels = component_channel_count(&component, BusDirections_::kInput);
        let output_channels = component_channel_count(&component, BusDirections_::kOutput);
        let audio_input_bus_count =
            component_bus_count(&component, MediaTypes_::kAudio, BusDirections_::kInput);
        let audio_output_bus_count =
            component_bus_count(&component, MediaTypes_::kAudio, BusDirections_::kOutput);

        let instance = Self {
            component,
            audio_processor,
            input_channels,
            output_channels,
            audio_input_bus_count,
            audio_output_bus_count,
            controller: None,
            component_connection: None,
            controller_connection: None,
            controller_is_component: false,
            _host_app: host_app,
            component_handler: None,
            processing_active: false,
            processing_sample_rate: 0.0,
            processing_max_samples: 0,
            queued_parameter_changes: Vec::new(),
            captured_parameter_edits: Arc::new(Mutex::new(Vec::new())),
            process_scratch: ProcessScratch::default(),
        };
        Ok(instance)
    }

    fn set_active(&mut self, active: bool) -> Result<(), String> {
        if active {
            let _ = unsafe { self.component.setIoMode(IoModes_::kSimple) };
            self.configure_bus_arrangements();
            self.activate_buses(true);
            check_result("VST3 component setActive", unsafe {
                self.component.setActive(1)
            })
        } else {
            self.set_processing(false);
            check_result("VST3 component setActive", unsafe {
                self.component.setActive(0)
            })?;
            self.activate_buses(false);
            Ok(())
        }
    }

    fn parameter_id_at(&self, index: u32) -> Option<ParamID> {
        let controller = self.controller.as_ref()?;
        let mut info = unsafe { mem::zeroed::<ParameterInfo>() };
        let result = unsafe { controller.getParameterInfo(index.try_into().ok()?, &mut info) };
        (result == kResultOk).then_some(info.id)
    }

    fn parameter_info(&self) -> Vec<PluginParameterInfo> {
        let Some(controller) = self.controller.as_ref() else {
            return Vec::new();
        };
        let count = unsafe { controller.getParameterCount().max(0) as u32 };
        (0..count)
            .filter_map(|index| {
                let mut info = unsafe { mem::zeroed::<ParameterInfo>() };
                let result =
                    unsafe { controller.getParameterInfo(index.try_into().ok()?, &mut info) };
                if result != kResultOk {
                    return None;
                }
                let value = unsafe { controller.getParamNormalized(info.id) as f32 };
                Some(PluginParameterInfo {
                    index,
                    param_id: Some(info.id),
                    name: vst3_utf16_string(&info.title),
                    units: vst3_utf16_string(&info.units),
                    value,
                    automatable: info.flags & ParameterInfo_::ParameterFlags_::kCanAutomate != 0,
                })
            })
            .collect()
    }

    fn set_parameter_normalized(&mut self, index: u32, value: f32) -> Result<(), String> {
        let Some(id) = self.parameter_id_at(index) else {
            return Err(format!("parameter index {index} is out of range"));
        };
        let value = value.clamp(0.0, 1.0) as ParamValue;
        if let Some(controller) = self.controller.as_ref() {
            let result = unsafe { controller.setParamNormalized(id, value) };
            if result != kResultOk {
                log::debug!("VST3 controller setParamNormalized returned {result}");
            }
        }
        self.queue_parameter_change(id, 0, value);
        Ok(())
    }

    fn set_parameter_normalized_at_sample(
        &mut self,
        index: u32,
        sample_offset: usize,
        value: f32,
    ) -> Result<(), String> {
        let Some(id) = self.parameter_id_at(index) else {
            return Err(format!("parameter index {index} is out of range"));
        };
        let value = value.clamp(0.0, 1.0) as ParamValue;
        if let Some(controller) = self.controller.as_ref() {
            let result = unsafe { controller.setParamNormalized(id, value) };
            if result != kResultOk {
                log::debug!("VST3 controller setParamNormalized returned {result}");
            }
        }
        self.queue_parameter_change(id, sample_offset, value);
        Ok(())
    }

    fn queue_parameter_change(&mut self, id: ParamID, sample_offset: usize, value: ParamValue) {
        self.queued_parameter_changes.push(ParameterChangePoint {
            id,
            sample_offset,
            value,
        });
    }

    fn drain_parameter_changes_into(&mut self, nframes: usize) {
        let changes = &mut self.process_scratch.parameter_changes;
        changes.clear();
        let max_offset = nframes.saturating_sub(1);
        for mut change in self.queued_parameter_changes.drain(..) {
            change.sample_offset = change.sample_offset.min(max_offset);
            changes.push(change);
        }
    }

    fn drain_captured_parameter_edits(&mut self) -> Vec<CapturedPluginParameterEdit> {
        let mut edits = self
            .captured_parameter_edits
            .lock()
            .map(|mut edits| std::mem::take(&mut *edits))
            .unwrap_or_default();
        edits.sort_by_key(|edit| edit.captured_at_millis);
        edits
    }

    fn ensure_controller(&mut self, factory: Option<&PluginFactory>) -> Result<bool, String> {
        if self.controller.is_some() {
            return Ok(false);
        }

        log::info!("creating VST3 edit controller");
        let host_context = self
            ._host_app
            .to_com_ptr::<FUnknown>()
            .expect("AtriHostApplication exposes FUnknown");
        let (controller, controller_is_component) =
            create_edit_controller(factory, &self.component, host_context.as_ptr())?;
        let component_connection = self.component.cast::<IConnectionPoint>();
        let controller_connection = controller.cast::<IConnectionPoint>();
        connect_component_and_controller(&component_connection, &controller_connection);

        let component_handler = ComWrapper::new(AtriComponentHandler::with_capture_queue(
            Arc::clone(&self.captured_parameter_edits),
        ));
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
        log::info!("created VST3 edit controller");
        Ok(true)
    }

    fn open_editor(
        &mut self,
        factory: &PluginFactory,
        parent: EditorParentHandle,
        context: PluginEditorContext,
        pending_state: Option<&StateParts>,
    ) -> Result<Vst3EditorHandle, String> {
        let created_controller = self.ensure_controller(Some(factory))?;
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

    fn process_audio(
        &mut self,
        bufs: &mut BufferSet,
        midi: &[ScheduledMidiEvent],
        start_sample: i64,
        speed: f64,
        nframes: usize,
        sample_rate: f64,
        configured_block_size: usize,
        tempo_metric: TempoMetric,
    ) -> Result<(), String> {
        if self.audio_processor.is_none() {
            return Err("VST3 component does not expose IAudioProcessor".to_string());
        }
        let Some(buffer) = bufs.get_mut(0) else {
            return Ok(());
        };
        let nframes = nframes.min(buffer.capacity()).max(1);
        let output_channels =
            usize::from(buffer.channels()).min(usize::from(self.output_channels.max(1)));
        if output_channels == 0 {
            return Ok(());
        }

        if !processing_is_prepared_for_block(
            self.processing_active,
            self.processing_sample_rate,
            self.processing_max_samples,
            sample_rate,
            nframes,
        ) {
            return Err(format!(
                "VST3 processor is not prepared for realtime block: sample_rate={sample_rate}, nframes={nframes}, prepared_sample_rate={}, prepared_max_samples={}, configured_block_size={configured_block_size}",
                self.processing_sample_rate, self.processing_max_samples
            ));
        }
        let audio_processor = self
            .audio_processor
            .as_ref()
            .ok_or_else(|| "VST3 component does not expose IAudioProcessor".to_string())?
            .clone();

        let input_channels = if self.audio_input_bus_count > 0 && self.input_channels > 0 {
            usize::from(buffer.channels()).min(usize::from(self.input_channels))
        } else {
            0
        };
        self.drain_parameter_changes_into(nframes);
        let mut context = build_process_context(start_sample, speed, sample_rate, tempo_metric);
        let scratch = &mut self.process_scratch;
        scratch.prepare_audio_buffers(buffer, input_channels, output_channels);
        scratch.prepare_events_and_parameters(midi, nframes);

        let mut output_bus = AudioBusBuffers {
            numChannels: output_channels as i32,
            silenceFlags: 0,
            __field0: AudioBusBuffers__type0 {
                channelBuffers32: scratch.output_channel_ptrs.as_mut_ptr(),
            },
        };
        let mut input_bus = AudioBusBuffers {
            numChannels: input_channels as i32,
            silenceFlags: 0,
            __field0: AudioBusBuffers__type0 {
                channelBuffers32: scratch.input_channel_ptrs.as_mut_ptr(),
            },
        };
        let input_buses = if input_channels > 0 {
            std::slice::from_mut(&mut input_bus)
        } else {
            &mut []
        };
        let output_buses = std::slice::from_mut(&mut output_bus);
        let mut data = ProcessData {
            processMode: ProcessModes_::kRealtime,
            symbolicSampleSize: SymbolicSampleSizes_::kSample32,
            numSamples: nframes as i32,
            numInputs: input_buses.len() as i32,
            numOutputs: output_buses.len() as i32,
            inputs: input_buses.as_mut_ptr(),
            outputs: output_buses.as_mut_ptr(),
            inputParameterChanges: scratch.input_params.as_ptr(),
            outputParameterChanges: scratch.output_params.as_ptr(),
            inputEvents: scratch.input_events.as_ptr(),
            outputEvents: scratch.output_events.as_ptr(),
            processContext: &mut context,
        };

        let result = unsafe { audio_processor.process(&mut data) };
        check_result("VST3 audio process", result)?;

        if output_channels == 1 && buffer.channels() >= 2 {
            for frame in 0..nframes {
                let sample = buffer.channel(0)[frame];
                buffer.channel_mut(1)[frame] = sample;
            }
        }
        Ok(())
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

    fn configure_bus_arrangements(&self) {
        let Some(audio_processor) = self.audio_processor.as_ref() else {
            return;
        };
        let mut input_arrangement = speaker_arrangement_for_channels(self.input_channels());
        let mut output_arrangement = speaker_arrangement_for_channels(self.output_channels());
        let input_count = i32::from(self.audio_input_bus_count > 0 && self.input_channels > 0);
        let output_count = i32::from(self.audio_output_bus_count > 0 && self.output_channels > 0);
        let result = unsafe {
            audio_processor.setBusArrangements(
                if input_count > 0 {
                    &mut input_arrangement
                } else {
                    ptr::null_mut()
                },
                input_count,
                if output_count > 0 {
                    &mut output_arrangement
                } else {
                    ptr::null_mut()
                },
                output_count,
            )
        };
        if result != kResultOk {
            log::debug!("VST3 setBusArrangements returned {result}");
        }
    }

    fn ensure_processing(&mut self, sample_rate: f64, max_samples: usize) -> Result<(), String> {
        if self.audio_processor.is_none() {
            return Err("VST3 component does not expose IAudioProcessor".to_string());
        }
        let max_samples = max_samples.max(1).min(i32::MAX as usize);
        if self.processing_active
            && self.processing_sample_rate == sample_rate
            && self.processing_max_samples >= max_samples
        {
            return Ok(());
        }

        log::info!(
            "[VST3] ensure_processing: sample_rate={} (was {}), max_samples={} (was {}), active={}",
            sample_rate,
            self.processing_sample_rate,
            max_samples,
            self.processing_max_samples,
            self.processing_active
        );

        self.set_processing(false);
        let audio_processor = self
            .audio_processor
            .as_ref()
            .ok_or_else(|| "VST3 component does not expose IAudioProcessor".to_string())?;
        let sample_size =
            unsafe { audio_processor.canProcessSampleSize(SymbolicSampleSizes_::kSample32) };
        if sample_size == kResultFalse {
            return Err("VST3 processor does not support 32-bit float processing".to_string());
        }
        let mut setup = ProcessSetup {
            processMode: ProcessModes_::kRealtime,
            symbolicSampleSize: SymbolicSampleSizes_::kSample32,
            maxSamplesPerBlock: max_samples as i32,
            sampleRate: sample_rate,
        };
        check_result("VST3 setupProcessing", unsafe {
            audio_processor.setupProcessing(&mut setup)
        })?;
        // setProcessing may return kNotImplemented (0x80004001) for plugins
        // that don't require explicit processing state toggling (e.g. Vienna Synchron Player).
        // Treat non-ok as non-fatal here, consistent with set_processing().
        let sp_result = unsafe { audio_processor.setProcessing(1) };
        if sp_result != kResultOk {
            log::debug!("VST3 setProcessing(true) returned {sp_result} (non-fatal)");
        }
        self.processing_active = true;
        self.processing_sample_rate = sample_rate;
        self.processing_max_samples = max_samples;
        let input_channels = if self.audio_input_bus_count > 0 && self.input_channels > 0 {
            usize::from(self.input_channels)
        } else {
            0
        };
        let output_channels = if self.audio_output_bus_count > 0 && self.output_channels > 0 {
            usize::from(self.output_channels)
        } else {
            0
        };
        self.process_scratch
            .reserve_audio_buffers(input_channels, output_channels);
        self.process_scratch.parameter_changes.reserve(16);
        Ok(())
    }

    fn set_processing(&mut self, active: bool) {
        let Some(audio_processor) = self.audio_processor.as_ref() else {
            self.processing_active = false;
            return;
        };
        if self.processing_active == active {
            return;
        }
        let result = unsafe { audio_processor.setProcessing(if active { 1 } else { 0 }) };
        if result != kResultOk {
            log::debug!("VST3 setProcessing({active}) returned {result}");
        }
        self.processing_active = active && result == kResultOk;
        if !active {
            self.processing_max_samples = 0;
        }
    }

    fn signal_latency(&self) -> usize {
        self.audio_processor
            .as_ref()
            .map(|audio_processor| unsafe { audio_processor.getLatencySamples() as usize })
            .unwrap_or(0)
    }

    fn activate_buses(&self, active: bool) {
        for media_type in [MediaTypes_::kAudio, MediaTypes_::kEvent] {
            for direction in [BusDirections_::kInput, BusDirections_::kOutput] {
                let count = unsafe { self.component.getBusCount(media_type, direction) };
                for index in 0..count.max(0) {
                    let result = unsafe {
                        self.component.activateBus(
                            media_type,
                            direction,
                            index,
                            if active { 1 } else { 0 },
                        )
                    };
                    if result != kResultOk {
                        log::debug!(
                            "VST3 activateBus(media={media_type}, dir={direction}, index={index}, active={active}) returned {result}"
                        );
                    }
                }
            }
        }
    }

    fn input_channels(&self) -> u16 {
        self.input_channels
    }

    fn output_channels(&self) -> u16 {
        self.output_channels
    }
}

fn processing_is_prepared_for_block(
    processing_active: bool,
    processing_sample_rate: f64,
    processing_max_samples: usize,
    sample_rate: f64,
    nframes: usize,
) -> bool {
    processing_active && processing_sample_rate == sample_rate && processing_max_samples >= nframes
}

fn processing_max_samples_for_block_size(block_size: usize) -> usize {
    block_size
        .max(MIN_REALTIME_PROCESSING_MAX_SAMPLES)
        .min(i32::MAX as usize)
}

fn default_tempo_metric() -> TempoMetric {
    TempoMetric::new(Tempo::new(120.0, 4), Meter::new(4, 4))
}

fn build_process_context(
    start_sample: i64,
    speed: f64,
    sample_rate: f64,
    tempo_metric: TempoMetric,
) -> ProcessContext {
    let mut context = unsafe { mem::zeroed::<ProcessContext>() };
    context.state = (ProcessContext_::StatesAndFlags_::kPlaying
        | ProcessContext_::StatesAndFlags_::kSystemTimeValid
        | ProcessContext_::StatesAndFlags_::kTempoValid
        | ProcessContext_::StatesAndFlags_::kTimeSigValid) as u32;
    context.sampleRate = sample_rate;
    context.projectTimeSamples = start_sample;
    context.continousTimeSamples = start_sample;
    context.systemTime = current_system_time_nanos();
    context.tempo = tempo_metric.tempo.bpm;
    context.timeSigNumerator = i32::from(tempo_metric.meter.num);
    context.timeSigDenominator = i32::from(tempo_metric.meter.denom);
    if speed == 0.0 {
        context.state &= !(ProcessContext_::StatesAndFlags_::kPlaying as u32);
    }
    context
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
            self.set_processing(false);
            let _ = self.component.setActive(0);
            let _ = self.component.terminate();
        }
    }
}

fn create_edit_controller(
    factory: Option<&PluginFactory>,
    component: &ComPtr<IComponent>,
    host_context: *mut FUnknown,
) -> Result<(ComPtr<IEditController>, bool), String> {
    if let Some(controller) = component.cast::<IEditController>() {
        return Ok((controller, true));
    }

    let factory = factory.ok_or_else(|| "VST3 plugin factory is not loaded".to_string())?;
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

fn component_bus_count(component: &ComPtr<IComponent>, media_type: i32, direction: i32) -> i32 {
    unsafe { component.getBusCount(media_type, direction).max(0) }
}

fn component_channel_count(component: &ComPtr<IComponent>, direction: i32) -> u16 {
    let count = component_bus_count(component, MediaTypes_::kAudio, direction);
    let mut channels = 0_i32;
    for index in 0..count {
        let mut bus = unsafe { mem::zeroed::<BusInfo>() };
        let result =
            unsafe { component.getBusInfo(MediaTypes_::kAudio, direction, index, &mut bus) };
        if result == kResultOk && bus.channelCount > 0 {
            channels = channels.saturating_add(bus.channelCount);
        }
    }
    cmp::min(channels, u16::MAX as i32) as u16
}

fn speaker_arrangement_for_channels(channels: u16) -> SpeakerArrangement {
    match channels {
        0 => SpeakerArr::kEmpty,
        1 => SpeakerArr::kMono,
        _ => SpeakerArr::kStereo,
    }
}

fn current_system_time_nanos() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|duration| duration.as_nanos().min(i64::MAX as u128) as i64)
        .unwrap_or(0)
}

fn vst3_utf16_string(value: &[u16]) -> String {
    let end = value.iter().position(|ch| *ch == 0).unwrap_or(value.len());
    String::from_utf16_lossy(&value[..end])
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
    use vst3::{Class, Steinberg::Vst::*, Steinberg::*};

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
    fn process_scratch_reuses_in_place_audio_channel_pointers() {
        let mut scratch = ProcessScratch::default();
        let mut bufs = BufferSet::new(1, 2, 128);
        let buffer = bufs.get_mut(0).unwrap();

        scratch.prepare_audio_buffers(buffer, 2, 2);
        let first_input_ptr = scratch.input_channel_ptrs[0];
        let first_output_ptr = scratch.output_channel_ptrs[0];
        let input_capacity = scratch.input_channel_ptrs.capacity();
        let output_capacity = scratch.output_channel_ptrs.capacity();

        scratch.prepare_audio_buffers(buffer, 2, 2);

        assert_eq!(scratch.input_channel_ptrs[0], first_input_ptr);
        assert_eq!(scratch.output_channel_ptrs[0], first_output_ptr);
        assert_eq!(
            scratch.input_channel_ptrs[0],
            scratch.output_channel_ptrs[0]
        );
        assert_eq!(scratch.input_channel_ptrs.capacity(), input_capacity);
        assert_eq!(scratch.output_channel_ptrs.capacity(), output_capacity);
    }

    #[test]
    fn process_scratch_reuses_event_and_parameter_handles() {
        let mut scratch = ProcessScratch::default();
        let input_events_ptr = scratch.input_events.as_ptr();
        let output_events_ptr = scratch.output_events.as_ptr();
        let input_params_ptr = scratch.input_params.as_ptr();
        let output_params_ptr = scratch.output_params.as_ptr();

        scratch.prepare_events_and_parameters(&[], 128);
        scratch.prepare_events_and_parameters(&[], 128);

        assert_eq!(scratch.input_events.as_ptr(), input_events_ptr);
        assert_eq!(scratch.output_events.as_ptr(), output_events_ptr);
        assert_eq!(scratch.input_params.as_ptr(), input_params_ptr);
        assert_eq!(scratch.output_params.as_ptr(), output_params_ptr);
    }

    #[test]
    fn realtime_processing_requires_prepared_block_capacity() {
        assert!(processing_is_prepared_for_block(
            true, 48_000.0, 256, 48_000.0, 128
        ));
        assert!(!processing_is_prepared_for_block(
            true, 48_000.0, 128, 48_000.0, 256
        ));
        assert!(!processing_is_prepared_for_block(
            false, 48_000.0, 256, 48_000.0, 128
        ));
        assert!(!processing_is_prepared_for_block(
            true, 44_100.0, 256, 48_000.0, 128
        ));
    }

    #[test]
    fn processing_setup_uses_realtime_headroom_above_configured_block_size() {
        assert_eq!(processing_max_samples_for_block_size(128), 4096);
        assert_eq!(processing_max_samples_for_block_size(8192), 8192);
    }

    #[test]
    fn prepare_for_processing_creates_controller_for_parameter_metadata() {
        let mut plugin = Vst3Plugin {
            name: "Fake VST3".to_string(),
            input_channels: 2,
            output_channels: 2,
            active: false,
            block_size: 128,
            sample_rate: DEFAULT_SAMPLE_RATE,
            tempo_metric: default_tempo_metric(),
            instance: Some(fake_component_controller_instance()),
            factory: None,
            state_chunk: Vec::new(),
        };

        assert!(plugin.parameter_info().is_empty());

        plugin.prepare_for_processing().unwrap();

        let parameters = plugin.parameter_info();
        assert_eq!(parameters.len(), 1);
        assert_eq!(parameters[0].index, 0);
        assert_eq!(parameters[0].param_id, Some(42));
        assert_eq!(parameters[0].name, "Cutoff");
        assert_eq!(parameters[0].units, "Hz");
        assert!((parameters[0].value - 0.5).abs() < 0.0001);
        assert!(parameters[0].automatable);
    }

    #[test]
    fn process_context_uses_host_tempo_metric() {
        let metric = atri_core::time::tempo::TempoMetric::new(
            atri_core::time::tempo::Tempo::new(93.5, 4),
            atri_core::time::tempo::Meter::new(7, 8),
        );

        let context = build_process_context(12_345, 1.0, 48_000.0, metric);

        assert_eq!(context.tempo, 93.5);
        assert_eq!(context.timeSigNumerator, 7);
        assert_eq!(context.timeSigDenominator, 8);
        assert!(context.state & (ProcessContext_::StatesAndFlags_::kTempoValid as u32) != 0);
        assert!(context.state & (ProcessContext_::StatesAndFlags_::kTimeSigValid as u32) != 0);
    }

    #[test]
    fn plugin_is_send_sync() {
        fn assert_send_sync<T: Send + Sync>() {}
        assert_send_sync::<Vst3Plugin>();
    }

    fn fake_component_controller_instance() -> Vst3Instance {
        let wrapper = ComWrapper::new(FakeComponentController);
        let component = wrapper
            .to_com_ptr::<IComponent>()
            .expect("fake component exposes IComponent");
        let audio_processor = wrapper.to_com_ptr::<IAudioProcessor>();
        std::mem::forget(wrapper);

        Vst3Instance {
            component,
            audio_processor,
            input_channels: 2,
            output_channels: 2,
            audio_input_bus_count: 1,
            audio_output_bus_count: 1,
            controller: None,
            component_connection: None,
            controller_connection: None,
            controller_is_component: false,
            _host_app: ComWrapper::new(AtriHostApplication::new("ATRI Test Host")),
            component_handler: None,
            processing_active: false,
            processing_sample_rate: 0.0,
            processing_max_samples: 0,
            queued_parameter_changes: Vec::new(),
            captured_parameter_edits: Arc::new(Mutex::new(Vec::new())),
            process_scratch: ProcessScratch::default(),
        }
    }

    struct FakeComponentController;

    impl Class for FakeComponentController {
        type Interfaces = (IComponent, IAudioProcessor, IEditController);
    }

    impl IPluginBaseTrait for FakeComponentController {
        unsafe fn initialize(&self, _context: *mut FUnknown) -> tresult {
            kResultOk
        }

        unsafe fn terminate(&self) -> tresult {
            kResultOk
        }
    }

    impl IComponentTrait for FakeComponentController {
        unsafe fn getControllerClassId(&self, class_id: *mut TUID) -> tresult {
            if !class_id.is_null() {
                unsafe {
                    *class_id = [0; 16];
                }
            }
            kResultOk
        }

        unsafe fn setIoMode(&self, _mode: IoMode) -> tresult {
            kResultOk
        }

        unsafe fn getBusCount(&self, r#type: MediaType, _dir: BusDirection) -> int32 {
            if r#type == MediaTypes_::kAudio { 1 } else { 0 }
        }

        unsafe fn getBusInfo(
            &self,
            r#type: MediaType,
            _dir: BusDirection,
            index: int32,
            bus: *mut BusInfo,
        ) -> tresult {
            if r#type != MediaTypes_::kAudio || index != 0 || bus.is_null() {
                return kInvalidArgument;
            }
            unsafe {
                (*bus).channelCount = 2;
            }
            kResultOk
        }

        unsafe fn getRoutingInfo(
            &self,
            _in_info: *mut RoutingInfo,
            _out_info: *mut RoutingInfo,
        ) -> tresult {
            kResultFalse
        }

        unsafe fn activateBus(
            &self,
            _type: MediaType,
            _dir: BusDirection,
            _index: int32,
            _state: TBool,
        ) -> tresult {
            kResultOk
        }

        unsafe fn setActive(&self, _state: TBool) -> tresult {
            kResultOk
        }

        unsafe fn setState(&self, _state: *mut IBStream) -> tresult {
            kResultOk
        }

        unsafe fn getState(&self, _state: *mut IBStream) -> tresult {
            kResultOk
        }
    }

    impl IAudioProcessorTrait for FakeComponentController {
        unsafe fn setBusArrangements(
            &self,
            _inputs: *mut SpeakerArrangement,
            _num_ins: int32,
            _outputs: *mut SpeakerArrangement,
            _num_outs: int32,
        ) -> tresult {
            kResultOk
        }

        unsafe fn getBusArrangement(
            &self,
            _dir: BusDirection,
            _index: int32,
            arr: *mut SpeakerArrangement,
        ) -> tresult {
            if !arr.is_null() {
                unsafe {
                    *arr = SpeakerArr::kStereo;
                }
            }
            kResultOk
        }

        unsafe fn canProcessSampleSize(&self, symbolic_sample_size: int32) -> tresult {
            if symbolic_sample_size == SymbolicSampleSizes_::kSample32 {
                kResultOk
            } else {
                kResultFalse
            }
        }

        unsafe fn getLatencySamples(&self) -> uint32 {
            0
        }

        unsafe fn setupProcessing(&self, _setup: *mut ProcessSetup) -> tresult {
            kResultOk
        }

        unsafe fn setProcessing(&self, _state: TBool) -> tresult {
            kResultOk
        }

        unsafe fn process(&self, _data: *mut ProcessData) -> tresult {
            kResultOk
        }

        unsafe fn getTailSamples(&self) -> uint32 {
            0
        }
    }

    impl IEditControllerTrait for FakeComponentController {
        unsafe fn setComponentState(&self, _state: *mut IBStream) -> tresult {
            kResultOk
        }

        unsafe fn setState(&self, _state: *mut IBStream) -> tresult {
            kResultOk
        }

        unsafe fn getState(&self, _state: *mut IBStream) -> tresult {
            kResultOk
        }

        unsafe fn getParameterCount(&self) -> int32 {
            1
        }

        unsafe fn getParameterInfo(&self, param_index: int32, info: *mut ParameterInfo) -> tresult {
            if param_index != 0 || info.is_null() {
                return kInvalidArgument;
            }
            unsafe {
                (*info).id = 42;
                (*info).flags = ParameterInfo_::ParameterFlags_::kCanAutomate;
                write_test_string128(&mut (*info).title, "Cutoff");
                write_test_string128(&mut (*info).units, "Hz");
            }
            kResultOk
        }

        unsafe fn getParamStringByValue(
            &self,
            _id: ParamID,
            _value_normalized: ParamValue,
            string: *mut String128,
        ) -> tresult {
            if !string.is_null() {
                unsafe {
                    write_test_string128(&mut *string, "0.50");
                }
            }
            kResultOk
        }

        unsafe fn getParamValueByString(
            &self,
            _id: ParamID,
            _string: *mut TChar,
            value_normalized: *mut ParamValue,
        ) -> tresult {
            if !value_normalized.is_null() {
                unsafe {
                    *value_normalized = 0.5;
                }
            }
            kResultOk
        }

        unsafe fn normalizedParamToPlain(
            &self,
            _id: ParamID,
            value_normalized: ParamValue,
        ) -> ParamValue {
            value_normalized
        }

        unsafe fn plainParamToNormalized(
            &self,
            _id: ParamID,
            plain_value: ParamValue,
        ) -> ParamValue {
            plain_value
        }

        unsafe fn getParamNormalized(&self, id: ParamID) -> ParamValue {
            if id == 42 { 0.5 } else { 0.0 }
        }

        unsafe fn setParamNormalized(&self, _id: ParamID, _value: ParamValue) -> tresult {
            kResultOk
        }

        unsafe fn setComponentHandler(&self, _handler: *mut IComponentHandler) -> tresult {
            kResultOk
        }

        unsafe fn createView(&self, _name: FIDString) -> *mut IPlugView {
            ptr::null_mut()
        }
    }

    fn write_test_string128(dst: &mut String128, value: &str) {
        dst.fill(0);
        for (slot, unit) in dst.iter_mut().zip(value.encode_utf16()) {
            *slot = unit as TChar;
        }
    }
}
