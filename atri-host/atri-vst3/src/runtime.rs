use std::cmp;
use std::ffi::c_void;
use std::ptr;
use std::slice;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

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
}
