use std::ffi::{CString, c_char, c_void};
use std::slice;
use std::sync::{
    Mutex, MutexGuard, TryLockError,
    atomic::{AtomicU64, Ordering},
};
use std::thread::JoinHandle;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use vst3::{
    Class, ComRef, ComWrapper,
    Steinberg::{Vst::*, *},
};

use crate::bridge_contract::{
    BridgeContextPublishRequest, BridgeContextPublishResponse, BridgeExportRequest,
    BridgeExportResponse, BridgeStatus,
};
use crate::dashboard_client::{
    BridgeDashboardClient, DashboardClientError, DashboardContextPublishWorker, DashboardEndpoint,
    DashboardExportWorker, DashboardLatestExportWorker, DashboardStatusWorker,
};
use crate::daw_agent_surface::{DawAgentSurfaceOpener, NativeDawAgentSurfaceOpener};
use crate::drag_drop::{
    BridgeDragError, BridgeDragPayload, BridgeDragService, NativeBridgeDragService,
};
use crate::editor::{
    BridgeConnectionState, BridgeEditorAction, BridgeEditorState, BridgeEditorViewModel,
};
use crate::editor_surface::{
    EditorPlatformType, EditorSurfaceSpec, NativeEditorSurface, NativeEditorSurfaceEvent,
};
use crate::identity::{
    COMPONENT_CLASS_ID, CONTROLLER_CLASS_ID, PLUGIN_CATEGORY, PLUGIN_NAME, PLUGIN_VERSION, VENDOR,
    VENDOR_EMAIL, VENDOR_URL,
};
use crate::processor::BridgeProcessorState;

const AUDIO_MODULE_CLASS: &str = "Audio Module Class";
const COMPONENT_CONTROLLER_CLASS: &str = "Component Controller Class";
const MAIN_OUTPUT_NAME: &str = "ATRI Bridge";
const EVENT_INPUT_NAME: &str = "ATRI Control";
const EDITOR_WIDTH: i32 = 420;
const EDITOR_HEIGHT: i32 = 220;
const DASHBOARD_STATUS_TIMEOUT: Duration = Duration::from_secs(2);
const DASHBOARD_EXPORT_TIMEOUT: Duration = Duration::from_secs(3);
const DASHBOARD_CONTEXT_TIMEOUT: Duration = Duration::from_secs(2);
const BRIDGE_STATUS_POLL_INTERVAL: Duration = Duration::from_secs(1);
const LATEST_EXPORT_POLL_INTERVAL: Duration = Duration::from_secs(1);
const CONTEXT_PUBLISH_INTERVAL: Duration = Duration::from_secs(1);
static NEXT_DAW_AGENT_INSTANCE_ID: AtomicU64 = AtomicU64::new(1);

const MEDIA_TYPE_AUDIO: MediaType = MediaTypes_::kAudio as MediaType;
const MEDIA_TYPE_EVENT: MediaType = MediaTypes_::kEvent as MediaType;
const BUS_DIRECTION_INPUT: BusDirection = BusDirections_::kInput as BusDirection;
const BUS_DIRECTION_OUTPUT: BusDirection = BusDirections_::kOutput as BusDirection;
const SAMPLE_SIZE_32: int32 = SymbolicSampleSizes_::kSample32 as int32;

fn lock_recover<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn try_lock_recover<T>(mutex: &Mutex<T>) -> Option<MutexGuard<'_, T>> {
    match mutex.try_lock() {
        Ok(guard) => Some(guard),
        Err(TryLockError::Poisoned(poisoned)) => Some(poisoned.into_inner()),
        Err(TryLockError::WouldBlock) => None,
    }
}

pub(crate) fn create_plugin_factory() -> *mut IPluginFactory {
    ComWrapper::new(BridgePluginFactory)
        .to_com_ptr::<IPluginFactory>()
        .expect("BridgePluginFactory exposes IPluginFactory")
        .into_raw()
}

struct BridgePluginFactory;

impl Class for BridgePluginFactory {
    type Interfaces = (IPluginFactory3,);
}

impl IPluginFactoryTrait for BridgePluginFactory {
    unsafe fn getFactoryInfo(&self, info: *mut PFactoryInfo) -> tresult {
        if info.is_null() {
            return kInvalidArgument;
        }

        let info = unsafe { &mut *info };
        copy_cstring(VENDOR, &mut info.vendor);
        copy_cstring(VENDOR_URL, &mut info.url);
        copy_cstring(VENDOR_EMAIL, &mut info.email);
        info.flags = PFactoryInfo_::FactoryFlags_::kUnicode as int32;
        kResultOk
    }

    unsafe fn countClasses(&self) -> int32 {
        2
    }

    unsafe fn getClassInfo(&self, index: int32, info: *mut PClassInfo) -> tresult {
        if info.is_null() {
            return kInvalidArgument;
        }

        let info = unsafe { &mut *info };
        match index {
            0 => {
                info.cid = COMPONENT_CLASS_ID;
                info.cardinality = PClassInfo_::ClassCardinality_::kManyInstances as int32;
                copy_cstring(AUDIO_MODULE_CLASS, &mut info.category);
                copy_cstring(PLUGIN_NAME, &mut info.name);
                kResultOk
            }
            1 => {
                info.cid = CONTROLLER_CLASS_ID;
                info.cardinality = PClassInfo_::ClassCardinality_::kManyInstances as int32;
                copy_cstring(COMPONENT_CONTROLLER_CLASS, &mut info.category);
                copy_cstring(PLUGIN_NAME, &mut info.name);
                kResultOk
            }
            _ => kInvalidArgument,
        }
    }

    unsafe fn createInstance(
        &self,
        cid: FIDString,
        iid: FIDString,
        obj: *mut *mut c_void,
    ) -> tresult {
        if cid.is_null() || iid.is_null() || obj.is_null() {
            return kInvalidArgument;
        }

        let class_id = unsafe { *(cid as *const TUID) };
        let instance = match class_id {
            COMPONENT_CLASS_ID => Some(
                ComWrapper::new(BridgeComponent::new())
                    .to_com_ptr::<FUnknown>()
                    .expect("BridgeComponent exposes FUnknown"),
            ),
            CONTROLLER_CLASS_ID => Some(
                ComWrapper::new(BridgeController::new())
                    .to_com_ptr::<FUnknown>()
                    .expect("BridgeController exposes FUnknown"),
            ),
            _ => None,
        };

        if let Some(instance) = instance {
            let ptr = instance.as_ptr();
            unsafe { ((*(*ptr).vtbl).queryInterface)(ptr, iid as *mut TUID, obj) }
        } else {
            kInvalidArgument
        }
    }
}

impl IPluginFactory2Trait for BridgePluginFactory {
    unsafe fn getClassInfo2(&self, index: int32, info: *mut PClassInfo2) -> tresult {
        if info.is_null() {
            return kInvalidArgument;
        }

        let info = unsafe { &mut *info };
        match bridge_class_info(index) {
            Some(class) => {
                info.cid = class.cid;
                info.cardinality = class.cardinality;
                copy_cstring(class.category, &mut info.category);
                copy_cstring(class.name, &mut info.name);
                info.classFlags = 0;
                copy_cstring(class.sub_categories, &mut info.subCategories);
                copy_cstring(VENDOR, &mut info.vendor);
                copy_cstring(PLUGIN_VERSION, &mut info.version);
                copy_cstring(vst3_sdk_version(), &mut info.sdkVersion);
                kResultOk
            }
            None => kInvalidArgument,
        }
    }
}

impl IPluginFactory3Trait for BridgePluginFactory {
    unsafe fn getClassInfoUnicode(&self, index: int32, info: *mut PClassInfoW) -> tresult {
        if info.is_null() {
            return kInvalidArgument;
        }

        let info = unsafe { &mut *info };
        match bridge_class_info(index) {
            Some(class) => {
                info.cid = class.cid;
                info.cardinality = class.cardinality;
                copy_cstring(class.category, &mut info.category);
                copy_wstring(class.name, &mut info.name);
                info.classFlags = 0;
                copy_cstring(class.sub_categories, &mut info.subCategories);
                copy_wstring(VENDOR, &mut info.vendor);
                copy_wstring(PLUGIN_VERSION, &mut info.version);
                copy_wstring(vst3_sdk_version(), &mut info.sdkVersion);
                kResultOk
            }
            None => kInvalidArgument,
        }
    }

    unsafe fn setHostContext(&self, _context: *mut FUnknown) -> tresult {
        kResultOk
    }
}

struct BridgeFactoryClassInfo {
    cid: TUID,
    cardinality: int32,
    category: &'static str,
    name: &'static str,
    sub_categories: &'static str,
}

fn bridge_class_info(index: int32) -> Option<BridgeFactoryClassInfo> {
    match index {
        0 => Some(BridgeFactoryClassInfo {
            cid: COMPONENT_CLASS_ID,
            cardinality: PClassInfo_::ClassCardinality_::kManyInstances as int32,
            category: AUDIO_MODULE_CLASS,
            name: PLUGIN_NAME,
            sub_categories: PLUGIN_CATEGORY,
        }),
        1 => Some(BridgeFactoryClassInfo {
            cid: CONTROLLER_CLASS_ID,
            cardinality: PClassInfo_::ClassCardinality_::kManyInstances as int32,
            category: COMPONENT_CONTROLLER_CLASS,
            name: PLUGIN_NAME,
            sub_categories: "",
        }),
        _ => None,
    }
}

fn vst3_sdk_version() -> &'static str {
    "VST 3"
}

struct BridgeComponent {
    state: Mutex<BridgeProcessorState>,
}

impl BridgeComponent {
    fn new() -> Self {
        Self {
            state: Mutex::new(BridgeProcessorState::default()),
        }
    }
}

impl Class for BridgeComponent {
    type Interfaces = (IComponent, IAudioProcessor, IProcessContextRequirements);
}

impl IPluginBaseTrait for BridgeComponent {
    unsafe fn initialize(&self, context: *mut FUnknown) -> tresult {
        capture_host_application_name(context);
        kResultOk
    }

    unsafe fn terminate(&self) -> tresult {
        kResultOk
    }
}

impl IComponentTrait for BridgeComponent {
    unsafe fn getControllerClassId(&self, class_id: *mut TUID) -> tresult {
        if class_id.is_null() {
            return kInvalidArgument;
        }

        unsafe {
            *class_id = CONTROLLER_CLASS_ID;
        }
        kResultOk
    }

    unsafe fn setIoMode(&self, _mode: IoMode) -> tresult {
        kResultOk
    }

    unsafe fn getBusCount(&self, media_type: MediaType, dir: BusDirection) -> int32 {
        match (media_type, dir) {
            (MEDIA_TYPE_AUDIO, BUS_DIRECTION_OUTPUT) => 1,
            (MEDIA_TYPE_EVENT, BUS_DIRECTION_INPUT) => 1,
            _ => 0,
        }
    }

    unsafe fn getBusInfo(
        &self,
        media_type: MediaType,
        dir: BusDirection,
        index: int32,
        bus: *mut BusInfo,
    ) -> tresult {
        if bus.is_null() || index != 0 {
            return kInvalidArgument;
        }

        let bus = unsafe { &mut *bus };
        match (media_type, dir) {
            (MEDIA_TYPE_AUDIO, BUS_DIRECTION_OUTPUT) => {
                bus.mediaType = MEDIA_TYPE_AUDIO;
                bus.direction = BUS_DIRECTION_OUTPUT;
                bus.channelCount = 2;
                bus.busType = BusTypes_::kMain as BusType;
                bus.flags = BusInfo_::BusFlags_::kDefaultActive as uint32;
                copy_wstring(MAIN_OUTPUT_NAME, &mut bus.name);
                kResultOk
            }
            (MEDIA_TYPE_EVENT, BUS_DIRECTION_INPUT) => {
                bus.mediaType = MEDIA_TYPE_EVENT;
                bus.direction = BUS_DIRECTION_INPUT;
                bus.channelCount = 1;
                bus.busType = BusTypes_::kMain as BusType;
                bus.flags = 0;
                copy_wstring(EVENT_INPUT_NAME, &mut bus.name);
                kResultOk
            }
            _ => kInvalidArgument,
        }
    }

    unsafe fn getRoutingInfo(
        &self,
        _in_info: *mut vst3::Steinberg::Vst::RoutingInfo,
        _out_info: *mut vst3::Steinberg::Vst::RoutingInfo,
    ) -> tresult {
        kNotImplemented
    }

    unsafe fn activateBus(
        &self,
        _media_type: MediaType,
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

impl IAudioProcessorTrait for BridgeComponent {
    unsafe fn setBusArrangements(
        &self,
        _inputs: *mut SpeakerArrangement,
        num_ins: int32,
        outputs: *mut SpeakerArrangement,
        num_outs: int32,
    ) -> tresult {
        if num_ins != 0 || num_outs != 1 || outputs.is_null() {
            return kInvalidArgument;
        }

        let output = unsafe { *outputs };
        if output == SpeakerArr::kStereo {
            kResultOk
        } else {
            kInvalidArgument
        }
    }

    unsafe fn getBusArrangement(
        &self,
        dir: BusDirection,
        index: int32,
        arr: *mut SpeakerArrangement,
    ) -> tresult {
        if arr.is_null() || index != 0 {
            return kInvalidArgument;
        }

        match dir {
            BUS_DIRECTION_OUTPUT => {
                unsafe {
                    *arr = SpeakerArr::kStereo;
                }
                kResultOk
            }
            _ => kInvalidArgument,
        }
    }

    unsafe fn canProcessSampleSize(&self, symbolic_sample_size: int32) -> tresult {
        if symbolic_sample_size == SAMPLE_SIZE_32 {
            kResultOk
        } else {
            kInvalidArgument
        }
    }

    unsafe fn getLatencySamples(&self) -> uint32 {
        0
    }

    unsafe fn setupProcessing(&self, setup: *mut ProcessSetup) -> tresult {
        if setup.is_null() {
            return kInvalidArgument;
        }

        let setup = unsafe { &*setup };
        let mut state = lock_recover(&self.state);
        state.prepare(setup.sampleRate, setup.maxSamplesPerBlock.max(1) as usize);
        kResultOk
    }

    unsafe fn setProcessing(&self, _state: TBool) -> tresult {
        kResultOk
    }

    unsafe fn process(&self, data: *mut ProcessData) -> tresult {
        if data.is_null() {
            return kInvalidArgument;
        }

        // The bridge plug-in is a controller surface. The real-time callback
        // intentionally performs no dashboard, filesystem, or allocation work.
        let data = unsafe { &mut *data };
        if !data.processContext.is_null() {
            if let Some(mut state) = try_lock_recover(&self.state) {
                state.apply_process_context(unsafe { &*data.processContext });
                if let Some(host_context) = state.host_context() {
                    crate::host_context::publish_host_context(host_context);
                }
            }
        }
        clear_output_buses(data);
        kResultOk
    }

    unsafe fn getTailSamples(&self) -> uint32 {
        0
    }
}

impl IProcessContextRequirementsTrait for BridgeComponent {
    unsafe fn getProcessContextRequirements(&self) -> uint32 {
        (ProcessContext_::StatesAndFlags_::kTempoValid
            | ProcessContext_::StatesAndFlags_::kTimeSigValid
            | ProcessContext_::StatesAndFlags_::kProjectTimeMusicValid
            | ProcessContext_::StatesAndFlags_::kBarPositionValid
            | ProcessContext_::StatesAndFlags_::kCycleValid) as u32
    }
}

struct BridgeController;

impl BridgeController {
    fn new() -> Self {
        Self
    }
}

impl Class for BridgeController {
    type Interfaces = (IEditController,);
}

impl IPluginBaseTrait for BridgeController {
    unsafe fn initialize(&self, context: *mut FUnknown) -> tresult {
        capture_host_application_name(context);
        kResultOk
    }

    unsafe fn terminate(&self) -> tresult {
        kResultOk
    }
}

impl IEditControllerTrait for BridgeController {
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
        0
    }

    unsafe fn getParameterInfo(&self, _param_index: int32, _info: *mut ParameterInfo) -> tresult {
        kInvalidArgument
    }

    unsafe fn getParamStringByValue(
        &self,
        _id: ParamID,
        _value_normalized: ParamValue,
        _string: *mut String128,
    ) -> tresult {
        kInvalidArgument
    }

    unsafe fn getParamValueByString(
        &self,
        _id: ParamID,
        _string: *mut TChar,
        _value_normalized: *mut ParamValue,
    ) -> tresult {
        kInvalidArgument
    }

    unsafe fn normalizedParamToPlain(
        &self,
        _id: ParamID,
        value_normalized: ParamValue,
    ) -> ParamValue {
        value_normalized
    }

    unsafe fn plainParamToNormalized(&self, _id: ParamID, plain_value: ParamValue) -> ParamValue {
        plain_value
    }

    unsafe fn getParamNormalized(&self, _id: ParamID) -> ParamValue {
        0.0
    }

    unsafe fn setParamNormalized(&self, _id: ParamID, _value: ParamValue) -> tresult {
        kInvalidArgument
    }

    unsafe fn setComponentHandler(&self, _handler: *mut IComponentHandler) -> tresult {
        kResultOk
    }

    unsafe fn createView(&self, _name: FIDString) -> *mut IPlugView {
        ComWrapper::new(BridgePlugView::default())
            .to_com_ptr::<IPlugView>()
            .expect("BridgePlugView exposes IPlugView")
            .into_raw()
    }
}

struct BridgePlugView {
    rect: Mutex<ViewRect>,
    editor_state: Mutex<BridgeEditorState>,
    view_model: Mutex<BridgeEditorViewModel>,
    status_worker: DashboardStatusWorker,
    status_job: Mutex<Option<JoinHandle<Result<BridgeStatus, DashboardClientError>>>>,
    last_status_poll: Mutex<Option<Instant>>,
    export_worker: DashboardExportWorker,
    export_job: Mutex<Option<JoinHandle<Result<BridgeExportResponse, DashboardClientError>>>>,
    latest_export_worker: DashboardLatestExportWorker,
    latest_export_job:
        Mutex<Option<JoinHandle<Result<BridgeExportResponse, DashboardClientError>>>>,
    last_latest_export_poll: Mutex<Option<Instant>>,
    context_publish_worker: DashboardContextPublishWorker,
    context_publish_job:
        Mutex<Option<JoinHandle<Result<BridgeContextPublishResponse, DashboardClientError>>>>,
    last_context_publish: Mutex<Option<Instant>>,
    drag_service: Box<dyn BridgeDragService + Send>,
    surface_opener: Box<dyn DawAgentSurfaceOpener + Send>,
    instance_id: String,
    surface_parent: Mutex<Option<(usize, EditorPlatformType)>>,
    native_surface: Mutex<Option<NativeEditorSurface>>,
}

impl Default for BridgePlugView {
    fn default() -> Self {
        let rect = ViewRect {
            left: 0,
            top: 0,
            right: EDITOR_WIDTH,
            bottom: EDITOR_HEIGHT,
        };
        let editor_state = BridgeEditorState::default();
        let (width, height) = rect_size(rect);
        Self {
            rect: Mutex::new(rect),
            view_model: Mutex::new(BridgeEditorViewModel::from_state(
                &editor_state,
                width,
                height,
            )),
            editor_state: Mutex::new(editor_state),
            status_worker: default_status_worker(),
            status_job: Mutex::new(None),
            last_status_poll: Mutex::new(None),
            export_worker: default_export_worker(),
            export_job: Mutex::new(None),
            latest_export_worker: default_latest_export_worker(),
            latest_export_job: Mutex::new(None),
            last_latest_export_poll: Mutex::new(None),
            context_publish_worker: default_context_publish_worker(),
            context_publish_job: Mutex::new(None),
            last_context_publish: Mutex::new(None),
            drag_service: Box::new(NativeBridgeDragService),
            surface_opener: Box::new(NativeDawAgentSurfaceOpener),
            instance_id: next_daw_agent_instance_id(),
            surface_parent: Mutex::new(None),
            native_surface: Mutex::new(None),
        }
    }
}

impl BridgePlugView {
    #[cfg(test)]
    fn with_export_worker(export_worker: DashboardExportWorker) -> Self {
        Self {
            export_worker,
            ..Self::default()
        }
    }

    #[cfg(test)]
    fn with_latest_export_worker(latest_export_worker: DashboardLatestExportWorker) -> Self {
        Self {
            latest_export_worker,
            ..Self::default()
        }
    }

    #[cfg(test)]
    fn with_context_publish_worker(context_publish_worker: DashboardContextPublishWorker) -> Self {
        Self {
            context_publish_worker,
            ..Self::default()
        }
    }

    #[cfg(test)]
    fn with_drag_service(drag_service: impl BridgeDragService + Send + 'static) -> Self {
        Self {
            drag_service: Box::new(drag_service),
            ..Self::default()
        }
    }

    #[cfg(test)]
    fn with_surface_opener(opener: impl DawAgentSurfaceOpener + Send + 'static) -> Self {
        Self {
            surface_opener: Box::new(opener),
            ..Self::default()
        }
    }

    #[cfg(test)]
    fn with_status_worker_and_surface_opener(
        status_worker: DashboardStatusWorker,
        opener: impl DawAgentSurfaceOpener + Send + 'static,
    ) -> Self {
        Self {
            status_worker,
            surface_opener: Box::new(opener),
            ..Self::default()
        }
    }

    #[cfg(test)]
    fn instance_id(&self) -> String {
        self.instance_id.clone()
    }

    #[cfg(test)]
    fn render_lines(&self) -> Vec<String> {
        lock_recover(&self.view_model).render_lines()
    }

    #[cfg(test)]
    fn dispatch_action_at(&self, x: i32, y: i32) -> Option<BridgeEditorAction> {
        let action = {
            let view_model = lock_recover(&self.view_model);
            view_model.hit_test(x, y)
        }?;
        self.dispatch_action(action);
        Some(action)
    }

    fn dispatch_action(&self, action: BridgeEditorAction) {
        let _ = self.poll_status_job();
        let _ = self.poll_export_job();
        self.refresh_host_context();
        if action == BridgeEditorAction::OpenAtri {
            self.open_daw_agent_surface();
            self.refresh_view_model();
            self.sync_native_surface();
            return;
        }
        let request = {
            let mut state = lock_recover(&self.editor_state);
            state.handle_action(action)
        }
        .map(|request| request.with_instance_id(self.instance_id.clone()));
        if let Some(request) = request {
            self.start_export_job(request);
        }
        self.refresh_view_model();
        self.sync_native_surface();
    }

    fn open_daw_agent_surface(&self) {
        let url = {
            let state = lock_recover(&self.editor_state);
            state.daw_agent_surface_url(&self.instance_id)
        };

        if let Err(error) = self.surface_opener.open(&url) {
            self.mark_editor_error(format!("failed to open DAW agent surface: {error}"));
        }
    }

    fn start_export_job(&self, request: BridgeExportRequest) {
        let mut export_job = lock_recover(&self.export_job);
        if export_job
            .as_ref()
            .map(|job| !job.is_finished())
            .unwrap_or(false)
        {
            drop(export_job);
            self.mark_export_error("another ATRI bridge export is already running");
            return;
        }

        *export_job = Some(self.export_worker.export_once(request));
    }

    fn poll_export_job(&self) -> bool {
        let finished_job = {
            let mut export_job = lock_recover(&self.export_job);
            if export_job
                .as_ref()
                .map(JoinHandle::is_finished)
                .unwrap_or(false)
            {
                export_job.take()
            } else {
                None
            }
        };

        let Some(finished_job) = finished_job else {
            return false;
        };

        match finished_job.join() {
            Ok(Ok(response)) => self.apply_export_response(response),
            Ok(Err(error)) => self.apply_export_error(error),
            Err(_) => self.mark_export_error("ATRI bridge export worker panicked"),
        }
        self.refresh_view_model();
        self.sync_native_surface();
        true
    }

    fn handle_tick(&self) {
        let _ = self.poll_status_job();
        let _ = self.poll_export_job();
        let _ = self.poll_latest_export_job();
        let _ = self.poll_context_publish_job();
        self.refresh_host_context();
        self.start_status_job_if_due();
        self.start_latest_export_job_if_due();
        self.start_context_publish_job_if_due();
    }

    fn start_status_job_if_due(&self) {
        {
            let status_job = lock_recover(&self.status_job);
            if status_job
                .as_ref()
                .map(|job| !job.is_finished())
                .unwrap_or(false)
            {
                return;
            }
        }

        let now = Instant::now();
        {
            let mut last_poll = lock_recover(&self.last_status_poll);
            if last_poll
                .map(|last_poll| now.duration_since(last_poll) < BRIDGE_STATUS_POLL_INTERVAL)
                .unwrap_or(false)
            {
                return;
            }
            *last_poll = Some(now);
        }

        let changed = {
            let mut state = lock_recover(&self.editor_state);
            if state.connection() == BridgeConnectionState::Disconnected {
                state.mark_connecting();
                true
            } else {
                false
            }
        };
        let mut status_job = lock_recover(&self.status_job);
        if status_job
            .as_ref()
            .map(|job| !job.is_finished())
            .unwrap_or(false)
        {
            return;
        }
        *status_job = Some(self.status_worker.check_once());
        drop(status_job);

        if changed {
            self.refresh_view_model();
            self.sync_native_surface();
        }
    }

    fn poll_status_job(&self) -> bool {
        let finished_job = {
            let mut status_job = lock_recover(&self.status_job);
            if status_job
                .as_ref()
                .map(JoinHandle::is_finished)
                .unwrap_or(false)
            {
                status_job.take()
            } else {
                None
            }
        };

        let Some(finished_job) = finished_job else {
            return false;
        };

        match finished_job.join() {
            Ok(Ok(status)) => self.apply_status_response(status),
            Ok(Err(error)) => self.apply_status_error(error),
            Err(_) => self.mark_editor_error("ATRI bridge status worker panicked"),
        }
        self.refresh_view_model();
        self.sync_native_surface();
        true
    }

    fn start_latest_export_job_if_due(&self) {
        {
            let latest_export_job = lock_recover(&self.latest_export_job);
            if latest_export_job
                .as_ref()
                .map(|job| !job.is_finished())
                .unwrap_or(false)
            {
                return;
            }
        }

        let now = Instant::now();
        {
            let mut last_poll = lock_recover(&self.last_latest_export_poll);
            if last_poll
                .map(|last_poll| now.duration_since(last_poll) < LATEST_EXPORT_POLL_INTERVAL)
                .unwrap_or(false)
            {
                return;
            }
            *last_poll = Some(now);
        }

        let mut latest_export_job = lock_recover(&self.latest_export_job);
        if latest_export_job
            .as_ref()
            .map(|job| !job.is_finished())
            .unwrap_or(false)
        {
            return;
        }
        *latest_export_job = Some(
            self.latest_export_worker
                .fetch_once(self.instance_id.clone()),
        );
    }

    fn poll_latest_export_job(&self) -> bool {
        let finished_job = {
            let mut latest_export_job = lock_recover(&self.latest_export_job);
            if latest_export_job
                .as_ref()
                .map(JoinHandle::is_finished)
                .unwrap_or(false)
            {
                latest_export_job.take()
            } else {
                None
            }
        };

        let Some(finished_job) = finished_job else {
            return false;
        };

        let changed = match finished_job.join() {
            Ok(Ok(response)) => self.apply_latest_export_response(response),
            Ok(Err(_)) | Err(_) => false,
        };
        if changed {
            self.refresh_view_model();
            self.sync_native_surface();
        }
        changed
    }

    fn start_context_publish_job_if_due(&self) {
        {
            let context_publish_job = lock_recover(&self.context_publish_job);
            if context_publish_job
                .as_ref()
                .map(|job| !job.is_finished())
                .unwrap_or(false)
            {
                return;
            }
        }

        let now = Instant::now();
        {
            let last_publish = lock_recover(&self.last_context_publish);
            if last_publish
                .map(|last_publish| now.duration_since(last_publish) < CONTEXT_PUBLISH_INTERVAL)
                .unwrap_or(false)
            {
                return;
            }
        }

        let Some(request) = self.context_publish_request() else {
            return;
        };
        let mut context_publish_job = lock_recover(&self.context_publish_job);
        if context_publish_job
            .as_ref()
            .map(|job| !job.is_finished())
            .unwrap_or(false)
        {
            return;
        }
        *context_publish_job = Some(self.context_publish_worker.publish_once(request));
        drop(context_publish_job);
        *lock_recover(&self.last_context_publish) = Some(now);
    }

    fn context_publish_request(&self) -> Option<BridgeContextPublishRequest> {
        let host_context = lock_recover(&self.editor_state).host_context()?;
        let mut request = BridgeContextPublishRequest::new(self.instance_id.clone(), host_context);
        if let Some(host_name) = crate::host_context::latest_host_application_name() {
            request = request.with_host_name(host_name);
        }
        Some(request)
    }

    fn poll_context_publish_job(&self) -> bool {
        let finished_job = {
            let mut context_publish_job = lock_recover(&self.context_publish_job);
            if context_publish_job
                .as_ref()
                .map(JoinHandle::is_finished)
                .unwrap_or(false)
            {
                context_publish_job.take()
            } else {
                None
            }
        };

        let Some(finished_job) = finished_job else {
            return false;
        };

        let changed = match finished_job.join() {
            Ok(Ok(response)) => {
                if response.ok {
                    false
                } else {
                    self.mark_editor_error(response.error_message());
                    true
                }
            }
            Ok(Err(error)) => {
                self.mark_editor_error(error.user_message());
                true
            }
            Err(_) => {
                self.mark_editor_error("ATRI bridge context publish worker panicked");
                true
            }
        };
        if changed {
            self.refresh_view_model();
            self.sync_native_surface();
        }
        true
    }

    #[cfg(test)]
    fn pending_export_format(&self) -> Option<crate::bridge_contract::BridgeExportFormat> {
        lock_recover(&self.editor_state).pending_export_format()
    }

    #[cfg(test)]
    fn export_state(&self) -> crate::editor::BridgeExportState {
        lock_recover(&self.editor_state).export_state()
    }

    #[cfg(test)]
    fn last_export_path(&self) -> Option<String> {
        lock_recover(&self.editor_state)
            .last_export_path()
            .map(ToOwned::to_owned)
    }

    #[cfg(test)]
    fn last_export_error(&self) -> Option<String> {
        lock_recover(&self.editor_state)
            .last_export_error()
            .map(ToOwned::to_owned)
    }

    fn apply_export_response(&self, response: BridgeExportResponse) {
        lock_recover(&self.editor_state).apply_export_response(response);
    }

    fn apply_status_response(&self, status: BridgeStatus) {
        lock_recover(&self.editor_state).apply_status(status);
    }

    fn apply_status_error(&self, error: DashboardClientError) {
        lock_recover(&self.editor_state).mark_error(error.user_message());
    }

    fn apply_latest_export_response(&self, response: BridgeExportResponse) -> bool {
        if response
            .bridge_scope_instance_id()
            .is_some_and(|instance_id| instance_id != self.instance_id)
        {
            return false;
        }
        lock_recover(&self.editor_state).apply_external_export_response(response)
    }

    fn apply_export_error(&self, error: DashboardClientError) {
        lock_recover(&self.editor_state).apply_export_error(error);
    }

    fn mark_export_error(&self, message: impl Into<String>) {
        lock_recover(&self.editor_state).mark_export_error(message);
    }

    fn mark_editor_error(&self, message: impl Into<String>) {
        lock_recover(&self.editor_state).mark_error(message);
    }

    fn start_drag_from_last_export(&self) {
        let (export_path, export_payload) = {
            let state = lock_recover(&self.editor_state);
            (
                state.last_export_path().map(ToOwned::to_owned),
                state.last_export_payload().cloned(),
            )
        };
        let result = match export_payload.as_ref() {
            Some(export) => BridgeDragPayload::from_export(export),
            None => export_path
                .ok_or(BridgeDragError::MissingCompletedExport)
                .and_then(BridgeDragPayload::from_export_path),
        }
        .and_then(|payload| self.drag_service.start_drag(payload));

        if let Err(error) = result {
            self.mark_export_error(error.to_string());
            self.refresh_view_model();
            self.sync_native_surface();
        }
    }

    fn scroll_midi_preview(&self, rows: i32) {
        let changed = lock_recover(&self.editor_state).scroll_midi_preview(rows);
        if changed {
            self.refresh_view_model();
            self.sync_native_surface();
        }
    }

    fn update_rect(&self, rect: ViewRect) {
        *lock_recover(&self.rect) = rect;
        self.refresh_view_model();
        self.sync_native_surface();
    }

    fn view_size(&self) -> (i32, i32) {
        rect_size(*lock_recover(&self.rect))
    }

    fn refresh_view_model(&self) {
        self.refresh_host_context();
        let (width, height) = self.view_size();
        let state = lock_recover(&self.editor_state).clone();
        let mut view_model = lock_recover(&self.view_model);
        *view_model = BridgeEditorViewModel::from_state(&state, width, height);
    }

    fn refresh_host_context(&self) {
        let Some(host_context) = crate::host_context::latest_host_context() else {
            return;
        };
        lock_recover(&self.editor_state).apply_host_context(host_context);
    }

    fn attach_native_surface(
        &self,
        parent: *mut c_void,
        platform_type: FIDString,
    ) -> Result<(), crate::editor_surface::EditorSurfaceError> {
        let rect = *lock_recover(&self.rect);
        let view_model = lock_recover(&self.view_model).clone();
        let spec = EditorSurfaceSpec::from_vst3(parent, platform_type, rect, &view_model)?;
        let surface = unsafe {
            NativeEditorSurface::attach(
                &spec,
                self as *const BridgePlugView as *mut c_void,
                bridge_surface_event_callback,
            )
        }?;

        *lock_recover(&self.surface_parent) = Some((spec.parent_handle(), spec.platform()));
        *lock_recover(&self.native_surface) = Some(surface);
        Ok(())
    }

    fn detach_native_surface(&self) {
        *lock_recover(&self.surface_parent) = None;
        *lock_recover(&self.native_surface) = None;
    }

    fn sync_native_surface(&self) {
        let Some(spec) = self.current_surface_spec() else {
            return;
        };
        let mut native_surface = lock_recover(&self.native_surface);
        if let Some(surface) = native_surface.as_mut() {
            let _ = surface.update(&spec);
        }
    }

    fn current_surface_spec(&self) -> Option<EditorSurfaceSpec> {
        let (parent_handle, platform) = (*lock_recover(&self.surface_parent))?;
        let rect = *lock_recover(&self.rect);
        let rect = crate::editor_surface::SurfaceRect::from_view_rect(rect).ok()?;
        let view_model = lock_recover(&self.view_model).clone();
        EditorSurfaceSpec::from_view_model(parent_handle, platform, rect, &view_model).ok()
    }
}

impl Class for BridgePlugView {
    type Interfaces = (IPlugView,);
}

impl IPlugViewTrait for BridgePlugView {
    unsafe fn isPlatformTypeSupported(&self, r#type: FIDString) -> tresult {
        if r#type.is_null() {
            return kInvalidArgument;
        }

        if EditorPlatformType::from_vst3(r#type).is_some() {
            kResultOk
        } else {
            kResultFalse
        }
    }

    unsafe fn attached(&self, parent: *mut c_void, r#type: FIDString) -> tresult {
        if parent.is_null() || r#type.is_null() {
            return kInvalidArgument;
        }
        if EditorPlatformType::from_vst3(r#type).is_none() {
            return kResultFalse;
        }

        match self.attach_native_surface(parent, r#type) {
            Ok(()) => kResultOk,
            Err(_) => kInternalError,
        }
    }

    unsafe fn removed(&self) -> tresult {
        self.detach_native_surface();
        kResultOk
    }

    unsafe fn onWheel(&self, _distance: f32) -> tresult {
        kResultOk
    }

    unsafe fn onKeyDown(&self, key: char16, _key_code: int16, _modifiers: int16) -> tresult {
        if let Some(action) = editor_action_for_key(key) {
            self.dispatch_action(action);
        }
        kResultOk
    }

    unsafe fn onKeyUp(&self, _key: char16, _key_code: int16, _modifiers: int16) -> tresult {
        kResultOk
    }

    unsafe fn getSize(&self, size: *mut ViewRect) -> tresult {
        if size.is_null() {
            return kInvalidArgument;
        }

        let rect = self
            .rect
            .lock()
            .map(|rect| *rect)
            .unwrap_or_else(|_| default_editor_rect());
        unsafe {
            *size = rect;
        }
        kResultOk
    }

    unsafe fn onSize(&self, new_size: *mut ViewRect) -> tresult {
        if new_size.is_null() {
            return kInvalidArgument;
        }

        self.update_rect(unsafe { *new_size });
        kResultOk
    }

    unsafe fn onFocus(&self, _state: TBool) -> tresult {
        kResultOk
    }

    unsafe fn setFrame(&self, _frame: *mut IPlugFrame) -> tresult {
        kResultOk
    }

    unsafe fn canResize(&self) -> tresult {
        kResultTrue
    }

    unsafe fn checkSizeConstraint(&self, rect: *mut ViewRect) -> tresult {
        if rect.is_null() {
            return kInvalidArgument;
        }

        let rect = unsafe { &mut *rect };
        rect.right = rect.right.max(rect.left + EDITOR_WIDTH);
        rect.bottom = rect.bottom.max(rect.top + EDITOR_HEIGHT);
        kResultOk
    }
}

unsafe fn bridge_surface_event_callback(context: *mut c_void, event: NativeEditorSurfaceEvent) {
    if context.is_null() {
        return;
    }

    let view = unsafe { &*(context as *const BridgePlugView) };
    match event {
        NativeEditorSurfaceEvent::Action(action) => view.dispatch_action(action),
        NativeEditorSurfaceEvent::DragExport => view.start_drag_from_last_export(),
        NativeEditorSurfaceEvent::ScrollPreview(rows) => view.scroll_midi_preview(rows),
        NativeEditorSurfaceEvent::Tick => view.handle_tick(),
    }
}

fn default_editor_rect() -> ViewRect {
    ViewRect {
        left: 0,
        top: 0,
        right: EDITOR_WIDTH,
        bottom: EDITOR_HEIGHT,
    }
}

fn rect_size(rect: ViewRect) -> (i32, i32) {
    (
        rect.right.saturating_sub(rect.left).max(1),
        rect.bottom.saturating_sub(rect.top).max(1),
    )
}

fn next_daw_agent_instance_id() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    format!(
        "bridge-{}-{}-{}",
        std::process::id(),
        nanos,
        NEXT_DAW_AGENT_INSTANCE_ID.fetch_add(1, Ordering::Relaxed)
    )
}

fn editor_action_for_key(key: char16) -> Option<BridgeEditorAction> {
    let ch = char::from_u32(key as u32)?.to_ascii_lowercase();
    match ch {
        'o' => Some(BridgeEditorAction::OpenAtri),
        'm' => Some(BridgeEditorAction::ExportMidi),
        'd' => Some(BridgeEditorAction::ExportDawproject),
        'w' => Some(BridgeEditorAction::ExportMixdownWav),
        's' => Some(BridgeEditorAction::ExportStems),
        _ => None,
    }
}

fn default_export_worker() -> DashboardExportWorker {
    DashboardExportWorker::new(
        BridgeDashboardClient::new(DashboardEndpoint::default()),
        DASHBOARD_EXPORT_TIMEOUT,
    )
}

fn default_status_worker() -> DashboardStatusWorker {
    DashboardStatusWorker::new(
        BridgeDashboardClient::new(DashboardEndpoint::default()),
        DASHBOARD_STATUS_TIMEOUT,
    )
}

fn default_latest_export_worker() -> DashboardLatestExportWorker {
    DashboardLatestExportWorker::new(
        BridgeDashboardClient::new(DashboardEndpoint::default()),
        DASHBOARD_EXPORT_TIMEOUT,
    )
}

fn default_context_publish_worker() -> DashboardContextPublishWorker {
    DashboardContextPublishWorker::new(
        BridgeDashboardClient::new(DashboardEndpoint::default()),
        DASHBOARD_CONTEXT_TIMEOUT,
    )
}

fn clear_output_buses(data: &mut ProcessData) {
    if data.outputs.is_null() || data.numOutputs <= 0 || data.numSamples <= 0 {
        return;
    }

    let outputs = unsafe { slice::from_raw_parts_mut(data.outputs, data.numOutputs as usize) };
    for bus in outputs {
        if bus.numChannels <= 0 {
            continue;
        }

        if unsafe { bus.__field0.channelBuffers32 }.is_null() {
            continue;
        }

        let channels = unsafe {
            slice::from_raw_parts_mut(bus.__field0.channelBuffers32, bus.numChannels as usize)
        };
        for channel in channels {
            if channel.is_null() {
                continue;
            }
            let samples = unsafe { slice::from_raw_parts_mut(*channel, data.numSamples as usize) };
            samples.fill(0.0);
        }
    }
}

fn copy_cstring(src: &str, dst: &mut [c_char]) {
    dst.fill(0);
    let c_string = CString::new(src).unwrap_or_default();
    let bytes = c_string.as_bytes_with_nul();
    for (src, dst) in bytes.iter().zip(dst.iter_mut()) {
        *dst = *src as c_char;
    }
    if bytes.len() > dst.len() {
        if let Some(last) = dst.last_mut() {
            *last = 0;
        }
    }
}

fn copy_wstring(src: &str, dst: &mut [TChar]) {
    dst.fill(0);
    for (slot, unit) in dst.iter_mut().zip(src.encode_utf16()) {
        *slot = unit as TChar;
    }
}

fn capture_host_application_name(context: *mut FUnknown) {
    if context.is_null() {
        return;
    }
    let Some(host_context) = (unsafe { ComRef::from_raw(context) }) else {
        return;
    };
    let Some(host_app) = host_context.cast::<IHostApplication>() else {
        return;
    };
    let mut name = [0 as TChar; 128];
    if unsafe { host_app.getName(&mut name) } != kResultOk {
        return;
    }
    let host_name = read_wstring(&name);
    if !host_name.trim().is_empty() {
        crate::host_context::publish_host_application_name(host_name);
    }
}

fn read_wstring(units: &[TChar]) -> String {
    let len = units
        .iter()
        .position(|unit| *unit == 0)
        .unwrap_or(units.len());
    String::from_utf16_lossy(
        &units[..len]
            .iter()
            .map(|unit| *unit as u16)
            .collect::<Vec<_>>(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bridge_contract::{BridgeExportFormat, BridgeHostContext};
    use crate::dashboard_client::{
        BridgeDashboardClient, DashboardEndpoint, DashboardExportWorker,
        DashboardLatestExportWorker, DashboardStatusWorker,
    };
    use crate::daw_agent_surface::{DawAgentSurfaceOpenError, DawAgentSurfaceOpener};
    use crate::editor::{BridgeEditorAction, BridgeExportState};
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::ptr;
    use std::thread;
    use std::time::{Duration, Instant};

    #[test]
    fn bridge_plug_view_initializes_rendered_editor_model() {
        let view = BridgePlugView::default();

        let lines = view.render_lines();
        let action = view.dispatch_action_at(116, 138);

        assert!(lines.iter().any(|line| line == "ATRI Bridge"));
        assert_eq!(action, Some(BridgeEditorAction::ExportMidi));
        assert_eq!(view.pending_export_format(), Some(BridgeExportFormat::Midi));
        assert_eq!(view.export_state(), BridgeExportState::InProgress);
    }

    #[test]
    fn bridge_plug_view_updates_render_model_after_resize() {
        let view = BridgePlugView::default();
        view.update_rect(ViewRect {
            left: 0,
            top: 0,
            right: 640,
            bottom: 320,
        });

        assert_eq!(view.view_size(), (640, 320));
    }

    #[test]
    fn bridge_plug_view_recovers_poisoned_view_model_lock() {
        let view = BridgePlugView::default();
        let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let _guard = view.view_model.lock().unwrap();
            panic!("poison view model");
        }));

        let lines = view.render_lines();

        assert!(lines.iter().any(|line| line == "ATRI Bridge"));
    }

    #[test]
    fn bridge_plug_view_recovers_poisoned_editor_state_lock_for_export_updates() {
        let view = BridgePlugView::default();
        let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let _guard = view.editor_state.lock().unwrap();
            panic!("poison editor state");
        }));

        view.apply_export_response(BridgeExportResponse {
            ok: true,
            bridge: None,
            export: Some(serde_json::json!({
                "format": "midi",
                "path": "data/music_workstation/exports/session.mid"
            })),
        });

        assert_eq!(view.export_state(), BridgeExportState::Completed);
        assert_eq!(
            view.last_export_path(),
            Some("data/music_workstation/exports/session.mid".to_string())
        );
    }

    #[test]
    fn bridge_plug_view_refresh_uses_latest_shared_host_context() {
        let _guard = crate::host_context::test_host_context_guard();
        crate::host_context::clear_latest_host_context_for_test();
        crate::host_context::publish_host_context(BridgeHostContext {
            sample_rate: Some(48_000.0),
            block_size: Some(256),
            is_playing: Some(true),
            tempo_bpm: Some(128.0),
            time_signature: Some([4, 4]),
            ..BridgeHostContext::default()
        });
        let view = BridgePlugView::default();

        view.refresh_view_model();

        assert!(
            view.render_lines()
                .iter()
                .any(|line| line == "Host: 128.0 BPM 4/4 playing @ 48000 Hz")
        );
        crate::host_context::clear_latest_host_context_for_test();
    }

    #[test]
    fn bridge_plug_view_key_shortcuts_map_to_editor_actions() {
        assert_eq!(
            editor_action_for_key('m' as char16),
            Some(BridgeEditorAction::ExportMidi)
        );
        assert_eq!(
            editor_action_for_key('d' as char16),
            Some(BridgeEditorAction::ExportDawproject)
        );
        assert_eq!(
            editor_action_for_key('w' as char16),
            Some(BridgeEditorAction::ExportMixdownWav)
        );
        assert_eq!(
            editor_action_for_key('s' as char16),
            Some(BridgeEditorAction::ExportStems)
        );
        assert_eq!(
            editor_action_for_key('o' as char16),
            Some(BridgeEditorAction::OpenAtri)
        );
        assert_eq!(editor_action_for_key('x' as char16), None);
    }

    #[test]
    fn bridge_plug_view_open_atri_launches_daw_agent_surface_url() {
        let _guard = crate::host_context::test_host_context_guard();
        crate::host_context::clear_latest_host_application_name_for_test();
        let opener = RecordingDawAgentSurfaceOpener::default();
        let recordings = opener.recordings();
        let view = BridgePlugView::with_surface_opener(opener);
        view.editor_state.lock().unwrap().apply_status(
            crate::bridge_contract::BridgeStatus::connected_for_test("ATRI Session", 7),
        );
        let instance_id = view.instance_id();

        view.dispatch_action(BridgeEditorAction::OpenAtri);

        assert_eq!(
            recordings.lock().unwrap().clone(),
            vec![format!(
                "http://127.0.0.1:6185/?surface=daw-agent&project_session_id=atri-session&instance_id={instance_id}&workspace=atri_studio&host=Studio%20One"
            )]
        );
        assert_eq!(view.export_state(), BridgeExportState::Idle);
        assert_eq!(view.pending_export_format(), None);
        crate::host_context::clear_latest_host_application_name_for_test();
    }

    #[test]
    fn bridge_plug_view_surface_tick_applies_bridge_status_before_opening_daw_agent() {
        let _guard = crate::host_context::test_host_context_guard();
        crate::host_context::clear_latest_host_application_name_for_test();
        let endpoint = spawn_bridge_status_server(
            r#"{
                "ok": true,
                "bridge": {
                    "api_version": 1,
                    "manifest_schema_version": 1,
                    "local_only": true
                },
                "project": {
                    "title": "Bridge Status Session",
                    "revision": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
                },
                "exports": {
                    "formats": ["midi", "dawproject"],
                    "hostless_formats": ["midi", "dawproject"],
                    "host_required_formats": ["wav"]
                },
                "host": {"running": false}
            }"#,
        );
        let opener = RecordingDawAgentSurfaceOpener::default();
        let recordings = opener.recordings();
        let view = BridgePlugView::with_status_worker_and_surface_opener(
            DashboardStatusWorker::new(
                BridgeDashboardClient::new(endpoint),
                Duration::from_secs(1),
            ),
            opener,
        );
        let instance_id = view.instance_id();

        wait_for_status_tick(&view);
        view.dispatch_action(BridgeEditorAction::OpenAtri);

        assert_eq!(
            recordings.lock().unwrap().clone(),
            vec![format!(
                "http://127.0.0.1:6185/?surface=daw-agent&project_session_id=bridge-status-session&instance_id={instance_id}&workspace=atri_studio&host=Studio%20One"
            )]
        );
        crate::host_context::clear_latest_host_application_name_for_test();
    }

    #[test]
    fn bridge_plug_view_rejects_null_hwnd_parent() {
        let view = BridgePlugView::default();

        let result = unsafe { view.attached(ptr::null_mut(), kPlatformTypeHWND) };

        assert_eq!(result, kInvalidArgument);
    }

    #[test]
    fn bridge_plug_view_export_action_applies_background_success() {
        let endpoint = spawn_bridge_export_server(
            r#"{
                "ok": true,
                "bridge": {
                    "api_version": 1,
                    "manifest_schema_version": 1,
                    "local_only": true
                },
                "export": {
                    "format": "midi",
                    "path": "data/music_workstation/exports/session.mid"
                }
            }"#,
            200,
        );
        let view = BridgePlugView::with_export_worker(DashboardExportWorker::new(
            BridgeDashboardClient::new(endpoint),
            Duration::from_secs(1),
        ));

        view.dispatch_action(BridgeEditorAction::ExportMidi);
        wait_for_export_completion(&view);

        assert_eq!(view.export_state(), BridgeExportState::Completed);
        assert_eq!(
            view.last_export_path(),
            Some("data/music_workstation/exports/session.mid".to_string())
        );
        assert_eq!(view.pending_export_format(), None);
    }

    #[test]
    fn bridge_plug_view_export_action_applies_background_error() {
        let endpoint = spawn_bridge_export_server(
            r#"{
                "ok": false,
                "error": "host is required for wav export"
            }"#,
            400,
        );
        let view = BridgePlugView::with_export_worker(DashboardExportWorker::new(
            BridgeDashboardClient::new(endpoint),
            Duration::from_secs(1),
        ));

        view.dispatch_action(BridgeEditorAction::ExportMixdownWav);
        wait_for_export_completion(&view);

        assert_eq!(view.export_state(), BridgeExportState::Error);
        assert_eq!(
            view.last_export_error(),
            Some("host is required for wav export".to_string())
        );
        assert_eq!(view.pending_export_format(), None);
    }

    #[test]
    fn bridge_plug_view_surface_tick_applies_finished_background_export() {
        let endpoint = spawn_bridge_export_server(
            r#"{
                "ok": true,
                "bridge": {
                    "api_version": 1,
                    "manifest_schema_version": 1,
                    "local_only": true
                },
                "export": {
                    "format": "dawproject",
                    "path": "data/music_workstation/exports/session.dawproject"
                }
            }"#,
            200,
        );
        let view = BridgePlugView::with_export_worker(DashboardExportWorker::new(
            BridgeDashboardClient::new(endpoint),
            Duration::from_secs(1),
        ));

        view.dispatch_action(BridgeEditorAction::ExportDawproject);
        wait_for_export_tick(&view);

        assert_eq!(view.export_state(), BridgeExportState::Completed);
        assert_eq!(
            view.last_export_path(),
            Some("data/music_workstation/exports/session.dawproject".to_string())
        );
    }

    #[test]
    fn bridge_plug_view_surface_tick_applies_latest_daw_agent_export() {
        let endpoint = spawn_bridge_latest_export_server(
            r#"{
                "ok": true,
                "bridge": {
                    "api_version": 1,
                    "manifest_schema_version": 1,
                    "local_only": true
                },
                "export": {
                    "format": "midi",
                    "path": "data/music_workstation/exports/daw-agent-region.mid",
                    "bridge_preview": {
                        "kind": "midi_region",
                        "track_id": 3,
                        "track_name": "Edited Synth",
                        "beat_range": [4.0, 8.0],
                        "note_count": 12,
                        "pitch_range": [48, 72]
                    }
                }
            }"#,
            200,
        );
        let view = BridgePlugView::with_latest_export_worker(DashboardLatestExportWorker::new(
            BridgeDashboardClient::new(endpoint),
            Duration::from_secs(1),
        ));

        wait_for_latest_export_tick(&view);

        assert_eq!(view.export_state(), BridgeExportState::Completed);
        assert_eq!(
            view.last_export_path(),
            Some("data/music_workstation/exports/daw-agent-region.mid".to_string())
        );
        assert!(
            view.render_lines()
                .iter()
                .any(|line| line == "Drag MIDI preview into DAW")
        );
    }

    #[test]
    fn bridge_plug_view_ignores_latest_export_for_different_instance() {
        let view = BridgePlugView::default();

        let changed = view.apply_latest_export_response(BridgeExportResponse {
            ok: true,
            bridge: None,
            export: Some(serde_json::json!({
                "format": "midi",
                "path": "data/music_workstation/exports/other.mid",
                "bridge_scope": {"instance_id": "bridge-other"}
            })),
        });

        assert!(!changed);
        assert_eq!(view.last_export_path(), None);
    }

    #[test]
    fn bridge_plug_view_starts_latest_export_poll_while_button_export_in_progress() {
        let endpoint = DashboardEndpoint::new("http://127.0.0.1:0").unwrap();
        let view = BridgePlugView::with_latest_export_worker(DashboardLatestExportWorker::new(
            BridgeDashboardClient::new(endpoint),
            Duration::from_millis(1),
        ));
        view.editor_state
            .lock()
            .unwrap()
            .begin_export(BridgeExportFormat::Dawproject);

        view.start_latest_export_job_if_due();

        let job = view.latest_export_job.lock().unwrap().take();
        assert!(job.is_some());
        if let Some(job) = job {
            let _ = job.join();
        }
    }

    #[test]
    fn bridge_plug_view_surface_tick_publishes_host_context_to_dashboard() {
        let _guard = crate::host_context::test_host_context_guard();
        crate::host_context::clear_latest_host_context_for_test();
        crate::host_context::clear_latest_host_application_name_for_test();
        crate::host_context::publish_host_application_name("REAPER");
        crate::host_context::publish_host_context(crate::bridge_contract::BridgeHostContext {
            sample_rate: Some(48_000.0),
            block_size: Some(256),
            is_playing: Some(true),
            tempo_bpm: Some(128.0),
            time_signature: Some([3, 4]),
            ..crate::bridge_contract::BridgeHostContext::default()
        });
        let endpoint = spawn_bridge_context_server(
            r#"{
                "ok": true,
                "bridge": {
                    "api_version": 1,
                    "manifest_schema_version": 1,
                    "local_only": true
                },
                "context": {
                    "host": "REAPER",
                    "tempo_bpm": 128
                }
            }"#,
            200,
        );
        let view = BridgePlugView::with_context_publish_worker(DashboardContextPublishWorker::new(
            BridgeDashboardClient::new(endpoint),
            Duration::from_secs(1),
        ));

        unsafe {
            bridge_surface_event_callback(
                &view as *const BridgePlugView as *mut c_void,
                NativeEditorSurfaceEvent::Tick,
            );
        }
        assert!(view.context_publish_job.lock().unwrap().is_some());
        wait_for_context_publish_tick(&view);

        crate::host_context::clear_latest_host_context_for_test();
        crate::host_context::clear_latest_host_application_name_for_test();
    }

    #[test]
    fn bridge_plug_view_context_publish_error_is_visible_in_editor_state() {
        let _guard = crate::host_context::test_host_context_guard();
        crate::host_context::clear_latest_host_context_for_test();
        crate::host_context::clear_latest_host_application_name_for_test();
        crate::host_context::publish_host_context(crate::bridge_contract::BridgeHostContext {
            sample_rate: Some(48_000.0),
            block_size: Some(256),
            is_playing: Some(false),
            tempo_bpm: Some(96.0),
            time_signature: Some([4, 4]),
            ..crate::bridge_contract::BridgeHostContext::default()
        });
        let endpoint = spawn_bridge_context_server(
            r#"{"ok": false, "error": "context publish rejected"}"#,
            400,
        );
        let view = BridgePlugView::with_context_publish_worker(DashboardContextPublishWorker::new(
            BridgeDashboardClient::new(endpoint),
            Duration::from_secs(1),
        ));

        unsafe {
            bridge_surface_event_callback(
                &view as *const BridgePlugView as *mut c_void,
                NativeEditorSurfaceEvent::Tick,
            );
        }
        wait_for_context_publish_tick(&view);

        assert!(
            view.render_lines()
                .iter()
                .any(|line| line == "Dashboard error: context publish rejected")
        );
        crate::host_context::clear_latest_host_context_for_test();
        crate::host_context::clear_latest_host_application_name_for_test();
    }

    #[test]
    fn bridge_component_process_publishes_host_context_snapshot() {
        let _guard = crate::host_context::test_host_context_guard();
        crate::host_context::clear_latest_host_context_for_test();
        let component = BridgeComponent::new();
        let mut context = unsafe { std::mem::zeroed::<ProcessContext>() };
        context.state = (ProcessContext_::StatesAndFlags_::kPlaying
            | ProcessContext_::StatesAndFlags_::kTempoValid
            | ProcessContext_::StatesAndFlags_::kTimeSigValid) as u32;
        context.sampleRate = 48_000.0;
        context.tempo = 128.0;
        context.timeSigNumerator = 3;
        context.timeSigDenominator = 4;
        let mut data = unsafe { std::mem::zeroed::<ProcessData>() };
        data.processContext = &mut context;

        let result = unsafe { component.process(&mut data) };

        assert_eq!(result, kResultOk);
        assert_eq!(
            crate::host_context::latest_host_context(),
            Some(BridgeHostContext {
                sample_rate: Some(48_000.0),
                block_size: Some(256),
                is_playing: Some(true),
                tempo_bpm: Some(128.0),
                time_signature: Some([3, 4]),
                ..BridgeHostContext::default()
            })
        );
        crate::host_context::clear_latest_host_context_for_test();
    }

    #[test]
    fn bridge_plug_view_drag_event_uses_completed_export_path() {
        let drag_service = RecordingDragService::default();
        let recordings = drag_service.recordings();
        let view = BridgePlugView::with_drag_service(drag_service);
        view.apply_export_response(BridgeExportResponse {
            ok: true,
            bridge: None,
            export: Some(serde_json::json!({
                "format": "dawproject",
                "path": "data/music_workstation/exports/session.dawproject"
            })),
        });

        unsafe {
            bridge_surface_event_callback(
                &view as *const BridgePlugView as *mut c_void,
                NativeEditorSurfaceEvent::DragExport,
            );
        }

        assert_eq!(
            recordings.lock().unwrap().clone(),
            vec!["data/music_workstation/exports/session.dawproject".to_string()]
        );
    }

    #[test]
    fn bridge_surface_event_callback_scrolls_midi_preview_rows() {
        let view = BridgePlugView::default();
        view.apply_export_response(BridgeExportResponse {
            ok: true,
            bridge: None,
            export: Some(serde_json::json!({
                "format": "midi",
                "path": "data/music_workstation/exports/region.mid",
                "bridge_preview": {
                    "kind": "midi_region",
                    "track_id": 1,
                    "track_name": "Piano",
                    "beat_range": [4.0, 8.0],
                    "note_count": 20,
                    "pitch_range": [36, 84],
                    "tracks": [
                        {"track_id": 1, "track_name": "Piano", "note_count": 8, "pitch_range": [60, 84]},
                        {"track_id": 2, "track_name": "Bass", "note_count": 6, "pitch_range": [36, 48]},
                        {"track_id": 3, "track_name": "Pad", "note_count": 6, "pitch_range": [52, 72]}
                    ]
                }
            })),
        });
        view.refresh_view_model();

        unsafe {
            bridge_surface_event_callback(
                &view as *const BridgePlugView as *mut c_void,
                NativeEditorSurfaceEvent::ScrollPreview(1),
            );
        }

        let preview = lock_recover(&view.view_model).preview().cloned().unwrap();
        assert_eq!(preview.track_rows()[0].title, "Bass");
    }

    fn wait_for_export_completion(view: &BridgePlugView) {
        let started = Instant::now();
        loop {
            if view.poll_export_job() {
                return;
            }
            assert!(
                started.elapsed() < Duration::from_secs(2),
                "timed out waiting for export worker"
            );
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn wait_for_export_tick(view: &BridgePlugView) {
        let started = Instant::now();
        loop {
            unsafe {
                bridge_surface_event_callback(
                    view as *const BridgePlugView as *mut c_void,
                    crate::editor_surface::NativeEditorSurfaceEvent::Tick,
                );
            }
            if view.export_state() == BridgeExportState::Completed {
                return;
            }
            assert!(
                started.elapsed() < Duration::from_secs(2),
                "timed out waiting for surface tick"
            );
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn wait_for_latest_export_tick(view: &BridgePlugView) {
        let started = Instant::now();
        loop {
            unsafe {
                bridge_surface_event_callback(
                    view as *const BridgePlugView as *mut c_void,
                    crate::editor_surface::NativeEditorSurfaceEvent::Tick,
                );
            }
            if view.export_state() == BridgeExportState::Completed {
                return;
            }
            assert!(
                started.elapsed() < Duration::from_secs(2),
                "timed out waiting for latest export worker"
            );
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn wait_for_context_publish_tick(view: &BridgePlugView) {
        let started = Instant::now();
        loop {
            if view.poll_context_publish_job() {
                return;
            }
            assert!(
                started.elapsed() < Duration::from_secs(5),
                "timed out waiting for context publish worker"
            );
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn wait_for_status_tick(view: &BridgePlugView) {
        let started = Instant::now();
        loop {
            unsafe {
                bridge_surface_event_callback(
                    view as *const BridgePlugView as *mut c_void,
                    crate::editor_surface::NativeEditorSurfaceEvent::Tick,
                );
            }
            if view
                .render_lines()
                .iter()
                .any(|line| line == "Connected: Bridge Status Session rev 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
            {
                return;
            }
            assert!(
                started.elapsed() < Duration::from_secs(2),
                "timed out waiting for status worker"
            );
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn spawn_bridge_status_server(body: &'static str) -> DashboardEndpoint {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 4096];
            let bytes = stream.read(&mut request).unwrap();
            let request = String::from_utf8_lossy(&request[..bytes]);
            assert!(request.starts_with("GET /api/music/studio/bridge/status "));

            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            );
            stream.write_all(response.as_bytes()).unwrap();
        });
        DashboardEndpoint::new(format!("http://127.0.0.1:{port}")).unwrap()
    }

    fn spawn_bridge_export_server(body: &'static str, status: u16) -> DashboardEndpoint {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 4096];
            let bytes = stream.read(&mut request).unwrap();
            let request = String::from_utf8_lossy(&request[..bytes]);
            assert!(request.starts_with("POST /api/music/studio/bridge/export "));
            assert!(request.contains("\"consumer\":\"bridge\""));
            assert!(request.contains("\"instance_id\":\"bridge-"));

            let response = format!(
                "HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            );
            stream.write_all(response.as_bytes()).unwrap();
        });
        DashboardEndpoint::new(format!("http://127.0.0.1:{port}")).unwrap()
    }

    fn spawn_bridge_context_server(body: &'static str, status: u16) -> DashboardEndpoint {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 4096];
            let bytes = stream.read(&mut request).unwrap();
            let request = String::from_utf8_lossy(&request[..bytes]);
            assert!(request.starts_with("POST /api/music/studio/bridge/context "));
            assert!(request.contains("\"instance_id\":\"bridge-"));
            assert!(request.contains("\"tempo_bpm\":"));
            let response = format!(
                "HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            );
            stream.write_all(response.as_bytes()).unwrap();
        });
        DashboardEndpoint::new(format!("http://127.0.0.1:{port}")).unwrap()
    }

    fn spawn_bridge_latest_export_server(body: &'static str, status: u16) -> DashboardEndpoint {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 4096];
            let bytes = stream.read(&mut request).unwrap();
            let request = String::from_utf8_lossy(&request[..bytes]);
            assert!(
                request
                    .starts_with("GET /api/music/studio/bridge/export/latest?instance_id=bridge-")
            );

            let response = format!(
                "HTTP/1.1 {status} OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            );
            stream.write_all(response.as_bytes()).unwrap();
        });
        DashboardEndpoint::new(format!("http://127.0.0.1:{port}")).unwrap()
    }

    #[derive(Default)]
    struct RecordingDragService {
        recordings: std::sync::Arc<std::sync::Mutex<Vec<String>>>,
    }

    impl RecordingDragService {
        fn recordings(&self) -> std::sync::Arc<std::sync::Mutex<Vec<String>>> {
            std::sync::Arc::clone(&self.recordings)
        }
    }

    impl BridgeDragService for RecordingDragService {
        fn start_drag(&self, payload: BridgeDragPayload) -> Result<(), BridgeDragError> {
            let mut recordings = self.recordings.lock().unwrap();
            recordings.extend(
                payload
                    .files()
                    .iter()
                    .map(|path| path.to_string_lossy().to_string()),
            );
            Ok(())
        }
    }

    #[derive(Default)]
    struct RecordingDawAgentSurfaceOpener {
        recordings: std::sync::Arc<std::sync::Mutex<Vec<String>>>,
    }

    impl RecordingDawAgentSurfaceOpener {
        fn recordings(&self) -> std::sync::Arc<std::sync::Mutex<Vec<String>>> {
            std::sync::Arc::clone(&self.recordings)
        }
    }

    impl DawAgentSurfaceOpener for RecordingDawAgentSurfaceOpener {
        fn open(&self, url: &str) -> Result<(), DawAgentSurfaceOpenError> {
            self.recordings.lock().unwrap().push(url.to_string());
            Ok(())
        }
    }

    struct TestHostApplication {
        name: String,
    }

    impl TestHostApplication {
        fn new(name: impl Into<String>) -> Self {
            Self { name: name.into() }
        }
    }

    impl Class for TestHostApplication {
        type Interfaces = (IHostApplication,);
    }

    impl IHostApplicationTrait for TestHostApplication {
        unsafe fn getName(&self, name: *mut String128) -> tresult {
            if name.is_null() {
                return kInvalidArgument;
            }
            copy_wstring(&self.name, unsafe { &mut *name });
            kResultOk
        }

        unsafe fn createInstance(
            &self,
            _cid: *mut TUID,
            _iid: *mut TUID,
            obj: *mut *mut c_void,
        ) -> tresult {
            if !obj.is_null() {
                unsafe {
                    *obj = ptr::null_mut();
                }
            }
            kNoInterface
        }
    }

    #[test]
    fn capture_host_application_name_reads_vst3_host_application() {
        let _guard = crate::host_context::test_host_context_guard();
        crate::host_context::clear_latest_host_application_name_for_test();
        let host_app = ComWrapper::new(TestHostApplication::new("REAPER"));
        let host_context = host_app
            .to_com_ptr::<FUnknown>()
            .expect("TestHostApplication exposes FUnknown");

        capture_host_application_name(host_context.as_ptr());

        assert_eq!(
            crate::host_context::latest_host_application_name(),
            Some("REAPER".to_string())
        );
        crate::host_context::clear_latest_host_application_name_for_test();
    }
}
