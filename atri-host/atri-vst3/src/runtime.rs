use std::cmp;
use std::collections::BTreeMap;
use std::ffi::c_void;
use std::ptr;
use std::slice;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use atri_core::midi::event::ScheduledMidiEvent;
use atri_core::midi::message::MidiMessage;
use atri_core::plugin::{
    EditorParentHandle, EditorParentKind, PluginEditorContext, PluginEditorHandle,
};
use vst3::{Class, ComPtr, ComWrapper, Steinberg::Vst::*, Steinberg::*};

#[derive(Debug)]
struct MemoryStreamState {
    bytes: Vec<u8>,
    position: usize,
}

pub(crate) struct MemoryStreamHandle {
    wrapper: ComWrapper<Vst3MemoryStream>,
    state: Arc<Mutex<MemoryStreamState>>,
}

impl MemoryStreamHandle {
    pub(crate) fn empty() -> Self {
        Self::with_bytes(Vec::new())
    }

    pub(crate) fn from_bytes(bytes: &[u8]) -> Self {
        Self::with_bytes(bytes.to_vec())
    }

    fn with_bytes(bytes: Vec<u8>) -> Self {
        let state = Arc::new(Mutex::new(MemoryStreamState { bytes, position: 0 }));
        let wrapper = ComWrapper::new(Vst3MemoryStream {
            state: Arc::clone(&state),
        });
        Self { wrapper, state }
    }

    pub(crate) fn with_stream<T>(&self, f: impl FnOnce(*mut IBStream) -> T) -> T {
        let stream = self
            .wrapper
            .to_com_ptr::<IBStream>()
            .expect("Vst3MemoryStream exposes IBStream");
        f(stream.as_ptr())
    }

    pub(crate) fn bytes(&self) -> Vec<u8> {
        self.state
            .lock()
            .map(|state| state.bytes.clone())
            .unwrap_or_default()
    }
}

struct Vst3MemoryStream {
    state: Arc<Mutex<MemoryStreamState>>,
}

impl Class for Vst3MemoryStream {
    type Interfaces = (IBStream,);
}

impl IBStreamTrait for Vst3MemoryStream {
    unsafe fn read(
        &self,
        buffer: *mut c_void,
        num_bytes: int32,
        num_bytes_read: *mut int32,
    ) -> tresult {
        if num_bytes < 0 || (num_bytes > 0 && buffer.is_null()) {
            return kInvalidArgument;
        }

        let requested = num_bytes as usize;
        let mut state = match self.state.lock() {
            Ok(state) => state,
            Err(_) => return kInternalError,
        };
        let available = state.bytes.len().saturating_sub(state.position);
        let copied = cmp::min(requested, available);

        if copied > 0 {
            // SAFETY: VST3 provided a non-null writable buffer for at least `numBytes`.
            let dst = unsafe { slice::from_raw_parts_mut(buffer.cast::<u8>(), copied) };
            dst.copy_from_slice(&state.bytes[state.position..state.position + copied]);
            state.position += copied;
        }

        if !num_bytes_read.is_null() {
            // SAFETY: The pointer is an optional VST3 out parameter checked for null above.
            unsafe {
                *num_bytes_read = copied as int32;
            }
        }
        kResultOk
    }

    unsafe fn write(
        &self,
        buffer: *mut c_void,
        num_bytes: int32,
        num_bytes_written: *mut int32,
    ) -> tresult {
        if num_bytes < 0 || (num_bytes > 0 && buffer.is_null()) {
            return kInvalidArgument;
        }

        let requested = num_bytes as usize;
        let mut state = match self.state.lock() {
            Ok(state) => state,
            Err(_) => return kInternalError,
        };
        let end = match state.position.checked_add(requested) {
            Some(end) => end,
            None => return kInvalidArgument,
        };
        if end > state.bytes.len() {
            state.bytes.resize(end, 0);
        }

        if requested > 0 {
            // SAFETY: VST3 provided a non-null readable buffer for at least `numBytes`.
            let src = unsafe { slice::from_raw_parts(buffer.cast::<u8>(), requested) };
            let start = state.position;
            state.bytes[start..end].copy_from_slice(src);
            state.position = end;
        }

        if !num_bytes_written.is_null() {
            // SAFETY: The pointer is an optional VST3 out parameter checked for null above.
            unsafe {
                *num_bytes_written = requested as int32;
            }
        }
        kResultOk
    }

    unsafe fn seek(&self, pos: int64, mode: int32, result: *mut int64) -> tresult {
        let mut state = match self.state.lock() {
            Ok(state) => state,
            Err(_) => return kInternalError,
        };

        let base = match mode {
            IBStream_::IStreamSeekMode_::kIBSeekSet => 0_i64,
            IBStream_::IStreamSeekMode_::kIBSeekCur => state.position as int64,
            IBStream_::IStreamSeekMode_::kIBSeekEnd => state.bytes.len() as int64,
            _ => return kInvalidArgument,
        };
        let Some(new_position) = base.checked_add(pos) else {
            return kInvalidArgument;
        };
        if new_position < 0 {
            return kInvalidArgument;
        }
        let Ok(new_position) = usize::try_from(new_position) else {
            return kInvalidArgument;
        };

        state.position = new_position;
        if !result.is_null() {
            // SAFETY: The pointer is an optional VST3 out parameter checked for null above.
            unsafe {
                *result = state.position as int64;
            }
        }
        kResultOk
    }

    unsafe fn tell(&self, pos: *mut int64) -> tresult {
        if pos.is_null() {
            return kInvalidArgument;
        }
        let state = match self.state.lock() {
            Ok(state) => state,
            Err(_) => return kInternalError,
        };
        // SAFETY: The pointer is a required VST3 out parameter checked for null above.
        unsafe {
            *pos = state.position as int64;
        }
        kResultOk
    }
}

pub(crate) struct AtriHostApplication {
    name: String,
}

impl AtriHostApplication {
    pub(crate) fn new(name: impl Into<String>) -> Self {
        Self { name: name.into() }
    }
}

impl Class for AtriHostApplication {
    type Interfaces = (IHostApplication,);
}

impl IHostApplicationTrait for AtriHostApplication {
    unsafe fn getName(&self, name: *mut String128) -> tresult {
        if name.is_null() {
            return kInvalidArgument;
        }
        // SAFETY: The pointer is a required VST3 out parameter checked for null above.
        unsafe {
            copy_wstring(&self.name, &mut *name);
        }
        kResultOk
    }

    unsafe fn createInstance(
        &self,
        _cid: *mut TUID,
        _iid: *mut TUID,
        obj: *mut *mut c_void,
    ) -> tresult {
        if !obj.is_null() {
            // SAFETY: The pointer is an optional VST3 out parameter checked for null above.
            unsafe {
                *obj = ptr::null_mut();
            }
        }
        kNoInterface
    }
}

pub(crate) struct AtriComponentHandler;

impl Class for AtriComponentHandler {
    type Interfaces = (
        IComponentHandler,
        IComponentHandler2,
        IComponentHandler3,
        IComponentHandlerBusActivation,
        IComponentHandlerSystemTime,
    );
}

impl IComponentHandlerTrait for AtriComponentHandler {
    unsafe fn beginEdit(&self, _id: ParamID) -> tresult {
        kResultOk
    }

    unsafe fn performEdit(&self, _id: ParamID, _value_normalized: ParamValue) -> tresult {
        kResultOk
    }

    unsafe fn endEdit(&self, _id: ParamID) -> tresult {
        kResultOk
    }

    unsafe fn restartComponent(&self, flags: int32) -> tresult {
        log::debug!("VST3 component requested restart flags={flags}");
        kResultOk
    }
}

impl IComponentHandler2Trait for AtriComponentHandler {
    unsafe fn setDirty(&self, state: TBool) -> tresult {
        log::debug!("VST3 component dirty state changed: {state}");
        kResultOk
    }

    unsafe fn requestOpenEditor(&self, _name: FIDString) -> tresult {
        log::debug!("VST3 component requested editor open");
        kResultOk
    }

    unsafe fn startGroupEdit(&self) -> tresult {
        kResultOk
    }

    unsafe fn finishGroupEdit(&self) -> tresult {
        kResultOk
    }
}

impl IComponentHandler3Trait for AtriComponentHandler {
    unsafe fn createContextMenu(
        &self,
        _plug_view: *mut IPlugView,
        _param_id: *const ParamID,
    ) -> *mut IContextMenu {
        ptr::null_mut()
    }
}

impl IComponentHandlerBusActivationTrait for AtriComponentHandler {
    unsafe fn requestBusActivation(
        &self,
        _type: MediaType,
        _dir: BusDirection,
        _index: int32,
        _state: TBool,
    ) -> tresult {
        log::debug!("VST3 component requested bus activation change");
        kResultOk
    }
}

impl IComponentHandlerSystemTimeTrait for AtriComponentHandler {
    unsafe fn getSystemTime(&self, system_time: *mut int64) -> tresult {
        if system_time.is_null() {
            return kInvalidArgument;
        }
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_nanos().min(i64::MAX as u128) as i64)
            .unwrap_or(0);
        unsafe {
            *system_time = nanos;
        }
        kResultOk
    }
}

pub(crate) struct AtriPlugFrame {
    context: PluginEditorContext,
    state: Arc<Mutex<AtriPlugFrameState>>,
}

impl AtriPlugFrame {
    fn new(context: PluginEditorContext, state: Arc<Mutex<AtriPlugFrameState>>) -> Self {
        Self { context, state }
    }
}

#[derive(Default)]
struct AtriPlugFrameState {
    attached: bool,
    pending_size: Option<ViewRect>,
}

pub(crate) struct AtriPlugFrameHandle {
    wrapper: ComWrapper<AtriPlugFrame>,
    state: Arc<Mutex<AtriPlugFrameState>>,
}

impl AtriPlugFrameHandle {
    pub(crate) fn new(context: PluginEditorContext) -> Self {
        let state = Arc::new(Mutex::new(AtriPlugFrameState::default()));
        let wrapper = ComWrapper::new(AtriPlugFrame::new(context, Arc::clone(&state)));
        Self { wrapper, state }
    }

    pub(crate) fn as_com_ptr<I: vst3::Interface>(&self) -> Option<ComPtr<I>> {
        self.wrapper.to_com_ptr::<I>()
    }

    pub(crate) fn mark_attached(&self) -> Option<ViewRect> {
        let mut state = self.state.lock().ok()?;
        state.attached = true;
        state.pending_size.take()
    }
}

impl Class for AtriPlugFrame {
    type Interfaces = (IPlugFrame,);
}

impl IPlugFrameTrait for AtriPlugFrame {
    unsafe fn resizeView(&self, _view: *mut IPlugView, new_size: *mut ViewRect) -> tresult {
        if new_size.is_null() {
            return kInvalidArgument;
        }
        // SAFETY: The pointer is a required VST3 input parameter checked for null above.
        let rect = unsafe { *new_size };
        let width = rect.right.saturating_sub(rect.left);
        let height = rect.bottom.saturating_sub(rect.top);
        if width <= 0 || height <= 0 {
            return kInvalidArgument;
        }

        log::info!("VST3 plug frame resizeView requested: width={width}, height={height}");
        self.context.request_resize(width as u32, height as u32);
        if let Ok(mut state) = self.state.lock() {
            if !state.attached {
                state.pending_size = Some(rect);
                log::info!("VST3 plug frame resizeView deferred until editor attached");
            }
        }
        kResultOk
    }
}

pub(crate) struct Vst3EditorHandle {
    view: Option<ComPtr<IPlugView>>,
    _frame: AtriPlugFrameHandle,
}

impl Vst3EditorHandle {
    pub(crate) fn new(view: ComPtr<IPlugView>, frame: AtriPlugFrameHandle) -> Self {
        Self {
            view: Some(view),
            _frame: frame,
        }
    }
}

impl PluginEditorHandle for Vst3EditorHandle {
    fn resize(&mut self, width: u32, height: u32) {
        let Some(view) = self.view.as_ref() else {
            return;
        };
        let width = width.max(1) as i32;
        let height = height.max(1) as i32;
        let mut rect = ViewRect {
            left: 0,
            top: 0,
            right: width,
            bottom: height,
        };
        log::info!("VST3 editor onSize(host-applied) start: width={width}, height={height}");
        let result = unsafe { view.onSize(&mut rect) };
        log::info!("VST3 editor onSize(host-applied) returned {result}");
    }

    fn close(&mut self) {
        let Some(view) = self.view.take() else {
            return;
        };

        // SAFETY: `view` is an owning COM pointer returned by `IEditController::createView`.
        unsafe {
            let _ = view.removed();
            let _ = view.setFrame(ptr::null_mut());
        }
    }
}

impl Drop for Vst3EditorHandle {
    fn drop(&mut self) {
        self.close();
    }
}

#[derive(Default)]
struct Vst3EventListState {
    events: Vec<Event>,
    sysex: Vec<Box<[u8]>>,
}

pub(crate) struct Vst3EventListHandle {
    _wrapper: ComWrapper<Vst3EventList>,
    ptr: ComPtr<IEventList>,
}

impl Vst3EventListHandle {
    pub(crate) fn empty() -> Self {
        Self::with_events(Vec::new())
    }

    pub(crate) fn from_midi(events: &[ScheduledMidiEvent], nframes: usize) -> Self {
        let mut vst_events = Vec::with_capacity(events.len());
        let mut sysex = Vec::new();
        for event in events {
            if let Some(event) = midi_event_to_vst3(event, nframes, &mut sysex) {
                vst_events.push(event);
            }
        }
        Self::with_state(Vst3EventListState {
            events: vst_events,
            sysex,
        })
    }

    fn with_events(events: Vec<Event>) -> Self {
        Self::with_state(Vst3EventListState {
            events,
            sysex: Vec::new(),
        })
    }

    fn with_state(state: Vst3EventListState) -> Self {
        let state = Arc::new(Mutex::new(state));
        let wrapper = ComWrapper::new(Vst3EventList { state });
        let ptr = wrapper
            .to_com_ptr::<IEventList>()
            .expect("Vst3EventList exposes IEventList");
        Self {
            _wrapper: wrapper,
            ptr,
        }
    }

    pub(crate) fn as_ptr(&self) -> *mut IEventList {
        self.ptr.as_ptr()
    }
}

struct Vst3EventList {
    state: Arc<Mutex<Vst3EventListState>>,
}

impl Class for Vst3EventList {
    type Interfaces = (IEventList,);
}

impl IEventListTrait for Vst3EventList {
    unsafe fn getEventCount(&self) -> int32 {
        self.state
            .lock()
            .map(|state| state.events.len().min(i32::MAX as usize) as int32)
            .unwrap_or(0)
    }

    unsafe fn getEvent(&self, index: int32, event: *mut Event) -> tresult {
        if event.is_null() || index < 0 {
            return kInvalidArgument;
        }
        let Ok(state) = self.state.lock() else {
            return kInternalError;
        };
        let Some(src) = state.events.get(index as usize) else {
            return kInvalidArgument;
        };
        unsafe {
            *event = *src;
        }
        kResultOk
    }

    unsafe fn addEvent(&self, event: *mut Event) -> tresult {
        if event.is_null() {
            return kInvalidArgument;
        }
        let Ok(mut state) = self.state.lock() else {
            return kInternalError;
        };
        let event = unsafe { retain_event_payload(*event, &mut state.sysex) };
        state.events.push(event);
        kResultOk
    }
}

#[derive(Debug, Clone, Copy)]
pub(crate) struct ParameterChangePoint {
    pub(crate) id: ParamID,
    pub(crate) sample_offset: usize,
    pub(crate) value: ParamValue,
}

pub(crate) struct ParameterChangesHandle {
    _wrapper: ComWrapper<ParameterChanges>,
    ptr: ComPtr<IParameterChanges>,
}

impl ParameterChangesHandle {
    pub(crate) fn empty() -> Self {
        Self::from_changes(Vec::new())
    }

    pub(crate) fn from_changes(changes: Vec<ParameterChangePoint>) -> Self {
        let mut grouped: BTreeMap<ParamID, Vec<ParameterPoint>> = BTreeMap::new();
        for change in changes {
            grouped.entry(change.id).or_default().push(ParameterPoint {
                sample_offset: change.sample_offset.min(i32::MAX as usize) as int32,
                value: change.value.clamp(0.0, 1.0),
            });
        }

        let mut queues = Vec::with_capacity(grouped.len());
        for (id, mut points) in grouped {
            points.sort_by_key(|point| point.sample_offset);
            queues.push(ParamValueQueueHandle::new(id, points).ptr);
        }

        let state = Arc::new(Mutex::new(ParameterChangesState { queues }));
        let wrapper = ComWrapper::new(ParameterChanges { state });
        let ptr = wrapper
            .to_com_ptr::<IParameterChanges>()
            .expect("ParameterChanges exposes IParameterChanges");
        Self {
            _wrapper: wrapper,
            ptr,
        }
    }

    pub(crate) fn as_ptr(&self) -> *mut IParameterChanges {
        self.ptr.as_ptr()
    }
}

struct ParameterChangesState {
    queues: Vec<ComPtr<IParamValueQueue>>,
}

struct ParameterChanges {
    state: Arc<Mutex<ParameterChangesState>>,
}

impl Class for ParameterChanges {
    type Interfaces = (IParameterChanges,);
}

impl IParameterChangesTrait for ParameterChanges {
    unsafe fn getParameterCount(&self) -> int32 {
        self.state
            .lock()
            .map(|state| state.queues.len().min(i32::MAX as usize) as int32)
            .unwrap_or(0)
    }

    unsafe fn getParameterData(&self, index: int32) -> *mut IParamValueQueue {
        if index < 0 {
            return ptr::null_mut();
        }
        let Ok(state) = self.state.lock() else {
            return ptr::null_mut();
        };
        state
            .queues
            .get(index as usize)
            .map(ComPtr::as_ptr)
            .unwrap_or(ptr::null_mut())
    }

    unsafe fn addParameterData(
        &self,
        id: *const ParamID,
        index: *mut int32,
    ) -> *mut IParamValueQueue {
        if id.is_null() {
            if !index.is_null() {
                unsafe {
                    *index = -1;
                }
            }
            return ptr::null_mut();
        }

        let id = unsafe { *id };
        let Ok(mut state) = self.state.lock() else {
            if !index.is_null() {
                unsafe {
                    *index = -1;
                }
            }
            return ptr::null_mut();
        };

        for (queue_index, queue) in state.queues.iter().enumerate() {
            if unsafe { queue.getParameterId() } == id {
                if !index.is_null() {
                    unsafe {
                        *index = queue_index.min(i32::MAX as usize) as int32;
                    }
                }
                return queue.as_ptr();
            }
        }

        let queue = ParamValueQueueHandle::new(id, Vec::new()).ptr;
        let queue_ptr = queue.as_ptr();
        let queue_index = state.queues.len();
        state.queues.push(queue);
        if !index.is_null() {
            unsafe {
                *index = queue_index.min(i32::MAX as usize) as int32;
            }
        }
        queue_ptr
    }
}

#[derive(Debug, Clone, Copy)]
struct ParameterPoint {
    sample_offset: int32,
    value: ParamValue,
}

struct ParamValueQueueState {
    id: ParamID,
    points: Vec<ParameterPoint>,
}

struct ParamValueQueueHandle {
    _wrapper: ComWrapper<ParamValueQueue>,
    ptr: ComPtr<IParamValueQueue>,
}

impl ParamValueQueueHandle {
    fn new(id: ParamID, points: Vec<ParameterPoint>) -> Self {
        let state = Arc::new(Mutex::new(ParamValueQueueState { id, points }));
        let wrapper = ComWrapper::new(ParamValueQueue { state });
        let ptr = wrapper
            .to_com_ptr::<IParamValueQueue>()
            .expect("ParamValueQueue exposes IParamValueQueue");
        Self {
            _wrapper: wrapper,
            ptr,
        }
    }
}

struct ParamValueQueue {
    state: Arc<Mutex<ParamValueQueueState>>,
}

impl Class for ParamValueQueue {
    type Interfaces = (IParamValueQueue,);
}

impl IParamValueQueueTrait for ParamValueQueue {
    unsafe fn getParameterId(&self) -> ParamID {
        self.state.lock().map(|state| state.id).unwrap_or(0)
    }

    unsafe fn getPointCount(&self) -> int32 {
        self.state
            .lock()
            .map(|state| state.points.len().min(i32::MAX as usize) as int32)
            .unwrap_or(0)
    }

    unsafe fn getPoint(
        &self,
        index: int32,
        sample_offset: *mut int32,
        value: *mut ParamValue,
    ) -> tresult {
        if index < 0 || sample_offset.is_null() || value.is_null() {
            return kInvalidArgument;
        }
        let Ok(state) = self.state.lock() else {
            return kInternalError;
        };
        let Some(point) = state.points.get(index as usize) else {
            return kInvalidArgument;
        };
        unsafe {
            *sample_offset = point.sample_offset;
            *value = point.value;
        }
        kResultOk
    }

    unsafe fn addPoint(
        &self,
        sample_offset: int32,
        value: ParamValue,
        index: *mut int32,
    ) -> tresult {
        let Ok(mut state) = self.state.lock() else {
            return kInternalError;
        };
        let sample_offset = sample_offset.max(0);
        let value = value.clamp(0.0, 1.0);
        let insert_at = state
            .points
            .partition_point(|point| point.sample_offset <= sample_offset);
        state.points.insert(
            insert_at,
            ParameterPoint {
                sample_offset,
                value,
            },
        );
        if !index.is_null() {
            unsafe {
                *index = insert_at.min(i32::MAX as usize) as int32;
            }
        }
        kResultOk
    }
}

pub(crate) fn platform_type(parent: EditorParentHandle) -> FIDString {
    match parent.kind {
        EditorParentKind::WindowsHwnd => kPlatformTypeHWND,
        EditorParentKind::MacOsNsView => kPlatformTypeNSView,
        EditorParentKind::X11Window | EditorParentKind::XcbWindow => kPlatformTypeX11EmbedWindowID,
    }
}

pub(crate) fn parent_handle_as_ptr(parent: EditorParentHandle) -> *mut c_void {
    parent.raw as usize as *mut c_void
}

unsafe fn retain_event_payload(event: Event, sysex: &mut Vec<Box<[u8]>>) -> Event {
    if event.r#type != Event_::EventTypes_::kDataEvent as u16 {
        return event;
    }

    let data = unsafe { event.__field0.data };
    if data.bytes.is_null() || data.size == 0 {
        return event;
    }

    let bytes = unsafe { slice::from_raw_parts(data.bytes, data.size as usize) };
    sysex.push(bytes.to_vec().into_boxed_slice());
    let bytes = sysex
        .last()
        .map(|bytes| bytes.as_ptr())
        .unwrap_or(ptr::null());

    Event {
        __field0: Event__type0 {
            data: DataEvent { bytes, ..data },
        },
        ..event
    }
}

fn midi_event_to_vst3(
    event: &ScheduledMidiEvent,
    nframes: usize,
    sysex: &mut Vec<Box<[u8]>>,
) -> Option<Event> {
    let offset = event.offset.min(nframes.saturating_sub(1)) as int32;
    let base = |event_type: u16, payload: Event__type0| Event {
        busIndex: 0,
        sampleOffset: offset,
        ppqPosition: 0.0,
        flags: 0,
        r#type: event_type,
        __field0: payload,
    };

    match &event.event.message {
        MidiMessage::NoteOn {
            channel,
            pitch,
            velocity,
        } if *velocity > 0 => Some(base(
            Event_::EventTypes_::kNoteOnEvent as u16,
            Event__type0 {
                noteOn: NoteOnEvent {
                    channel: i16::from(*channel),
                    pitch: i16::from(*pitch),
                    tuning: 0.0,
                    velocity: velocity_to_unit(*velocity),
                    length: 0,
                    noteId: -1,
                },
            },
        )),
        MidiMessage::NoteOn { channel, pitch, .. }
        | MidiMessage::NoteOff { channel, pitch, .. } => Some(base(
            Event_::EventTypes_::kNoteOffEvent as u16,
            Event__type0 {
                noteOff: NoteOffEvent {
                    channel: i16::from(*channel),
                    pitch: i16::from(*pitch),
                    velocity: 0.0,
                    noteId: -1,
                    tuning: 0.0,
                },
            },
        )),
        MidiMessage::ControlChange {
            channel,
            controller,
            value,
        } => Some(legacy_midi_cc(offset, *channel, *controller, *value, 0)),
        MidiMessage::AllNotesOff { channel } => Some(legacy_midi_cc(
            offset,
            *channel,
            ControllerNumbers_::kCtrlAllNotesOff as u8,
            0,
            0,
        )),
        MidiMessage::ProgramChange { channel, program } => Some(legacy_midi_cc(
            offset,
            *channel,
            ControllerNumbers_::kCtrlProgramChange as u8,
            *program,
            0,
        )),
        MidiMessage::ChannelPressure { channel, pressure } => Some(legacy_midi_cc(
            offset,
            *channel,
            ControllerNumbers_::kAfterTouch as u8,
            *pressure,
            0,
        )),
        MidiMessage::PitchBend { channel, value } => {
            let bend = (i32::from(*value) + 8192).clamp(0, 16_383) as u16;
            Some(legacy_midi_cc(
                offset,
                *channel,
                ControllerNumbers_::kPitchBend as u8,
                (bend & 0x7f) as u8,
                ((bend >> 7) & 0x7f) as u8,
            ))
        }
        MidiMessage::PolyphonicKeyPressure {
            channel,
            pitch,
            pressure,
        } => Some(base(
            Event_::EventTypes_::kPolyPressureEvent as u16,
            Event__type0 {
                polyPressure: PolyPressureEvent {
                    channel: i16::from(*channel),
                    pitch: i16::from(*pitch),
                    pressure: velocity_to_unit(*pressure),
                    noteId: -1,
                },
            },
        )),
        MidiMessage::SystemExclusive(bytes) => {
            if bytes.is_empty() || bytes.len() > u32::MAX as usize {
                return None;
            }
            sysex.push(bytes.clone().into_boxed_slice());
            let bytes = sysex
                .last()
                .map(|bytes| bytes.as_ptr())
                .unwrap_or(ptr::null());
            Some(base(
                Event_::EventTypes_::kDataEvent as u16,
                Event__type0 {
                    data: DataEvent {
                        size: sysex.last().map(|bytes| bytes.len()).unwrap_or(0) as uint32,
                        r#type: DataEvent_::DataTypes_::kMidiSysEx as uint32,
                        bytes,
                    },
                },
            ))
        }
    }
}

fn legacy_midi_cc(
    sample_offset: int32,
    channel: u8,
    controller: u8,
    value: u8,
    value2: u8,
) -> Event {
    Event {
        busIndex: 0,
        sampleOffset: sample_offset,
        ppqPosition: 0.0,
        flags: 0,
        r#type: Event_::EventTypes_::kLegacyMIDICCOutEvent as u16,
        __field0: Event__type0 {
            midiCCOut: LegacyMIDICCOutEvent {
                controlNumber: controller,
                channel: channel as i8,
                value: value as i8,
                value2: value2 as i8,
            },
        },
    }
}

fn velocity_to_unit(value: u8) -> f32 {
    f32::from(value.min(127)) / 127.0
}

fn copy_wstring(src: &str, dst: &mut [TChar]) {
    let mut len = 0;
    let max_chars = dst.len().saturating_sub(1);
    for (unit, slot) in src.encode_utf16().take(max_chars).zip(dst.iter_mut()) {
        *slot = unit as TChar;
        len += 1;
    }
    if len < dst.len() {
        dst[len] = 0;
    } else if let Some(last) = dst.last_mut() {
        *last = 0;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use atri_core::midi::event::MidiEvent;

    #[test]
    fn memory_stream_round_trip() {
        let stream = MemoryStreamHandle::empty();
        let bytes = [1_u8, 2, 3, 4];
        stream.with_stream(|ptr| unsafe {
            let mut written = 0;
            assert_eq!(
                ((*(*ptr).vtbl).write)(
                    ptr,
                    bytes.as_ptr() as *mut c_void,
                    bytes.len() as int32,
                    &mut written,
                ),
                kResultOk
            );
            assert_eq!(written, 4);

            let mut seek_pos = 0;
            assert_eq!(
                ((*(*ptr).vtbl).seek)(
                    ptr,
                    0,
                    IBStream_::IStreamSeekMode_::kIBSeekSet,
                    &mut seek_pos,
                ),
                kResultOk
            );
            let mut out = [0_u8; 4];
            let mut read = 0;
            assert_eq!(
                ((*(*ptr).vtbl).read)(
                    ptr,
                    out.as_mut_ptr() as *mut c_void,
                    out.len() as int32,
                    &mut read,
                ),
                kResultOk
            );
            assert_eq!(read, 4);
            assert_eq!(out, bytes);
        });
        assert_eq!(stream.bytes(), bytes);
    }

    #[test]
    fn event_list_converts_sysex_and_keeps_payload_alive() {
        let midi = [ScheduledMidiEvent::new(
            MidiEvent::new(0, MidiMessage::SystemExclusive(vec![0xf0, 0x7e, 0xf7])),
            4,
        )];
        let events = Vst3EventListHandle::from_midi(&midi, 128);
        let ptr = events.as_ptr();

        unsafe {
            assert_eq!(((*(*ptr).vtbl).getEventCount)(ptr), 1);
            let mut event = std::mem::zeroed::<Event>();
            assert_eq!(((*(*ptr).vtbl).getEvent)(ptr, 0, &mut event), kResultOk);
            assert_eq!(event.r#type, Event_::EventTypes_::kDataEvent as u16);
            assert_eq!(event.sampleOffset, 4);
            let data = event.__field0.data;
            assert_eq!(data.r#type, DataEvent_::DataTypes_::kMidiSysEx as uint32);
            assert_eq!(data.size, 3);
            assert_eq!(
                slice::from_raw_parts(data.bytes, data.size as usize),
                [0xf0, 0x7e, 0xf7]
            );
        }
    }

    #[test]
    fn parameter_changes_group_points_by_parameter_id() {
        let changes = ParameterChangesHandle::from_changes(vec![
            ParameterChangePoint {
                id: 42,
                sample_offset: 12,
                value: 0.75,
            },
            ParameterChangePoint {
                id: 42,
                sample_offset: 4,
                value: 0.25,
            },
        ]);
        let ptr = changes.as_ptr();

        unsafe {
            assert_eq!(((*(*ptr).vtbl).getParameterCount)(ptr), 1);
            let queue = ((*(*ptr).vtbl).getParameterData)(ptr, 0);
            assert!(!queue.is_null());
            assert_eq!(((*(*queue).vtbl).getParameterId)(queue), 42);
            assert_eq!(((*(*queue).vtbl).getPointCount)(queue), 2);

            let mut offset = 0;
            let mut value = 0.0;
            assert_eq!(
                ((*(*queue).vtbl).getPoint)(queue, 0, &mut offset, &mut value),
                kResultOk
            );
            assert_eq!(offset, 4);
            assert_eq!(value, 0.25);
        }
    }
}
