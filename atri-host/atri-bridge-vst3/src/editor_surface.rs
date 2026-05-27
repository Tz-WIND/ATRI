use std::ffi::{CStr, c_void};

use thiserror::Error;
use vst3::Steinberg::{FIDString, ViewRect};

use crate::editor::{BridgeEditorAction, BridgeEditorButton, BridgeEditorViewModel};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EditorPlatformType {
    WindowsHwnd,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SurfaceRect {
    pub left: i32,
    pub top: i32,
    pub width: i32,
    pub height: i32,
}

impl SurfaceRect {
    pub fn from_view_rect(rect: ViewRect) -> Result<Self, EditorSurfaceError> {
        let width = rect.right.saturating_sub(rect.left);
        let height = rect.bottom.saturating_sub(rect.top);
        if width <= 0 || height <= 0 {
            return Err(EditorSurfaceError::InvalidSize);
        }

        Ok(Self {
            left: rect.left,
            top: rect.top,
            width,
            height,
        })
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EditorSurfaceSpec {
    parent_handle: usize,
    platform: EditorPlatformType,
    rect: SurfaceRect,
    lines: Vec<String>,
    buttons: Vec<BridgeEditorButton>,
}

impl EditorSurfaceSpec {
    pub fn from_view_model(
        parent_handle: usize,
        platform: EditorPlatformType,
        rect: SurfaceRect,
        view_model: &BridgeEditorViewModel,
    ) -> Result<Self, EditorSurfaceError> {
        if parent_handle == 0 {
            return Err(EditorSurfaceError::NullParent);
        }
        if rect.width <= 0 || rect.height <= 0 {
            return Err(EditorSurfaceError::InvalidSize);
        }

        Ok(Self {
            parent_handle,
            platform,
            rect,
            lines: view_model.render_lines(),
            buttons: view_model.buttons().to_vec(),
        })
    }

    pub fn from_vst3(
        parent: *mut c_void,
        platform_type: FIDString,
        rect: ViewRect,
        view_model: &BridgeEditorViewModel,
    ) -> Result<Self, EditorSurfaceError> {
        let platform = EditorPlatformType::from_vst3(platform_type)
            .ok_or(EditorSurfaceError::UnsupportedPlatform)?;
        let rect = SurfaceRect::from_view_rect(rect)?;
        Self::from_view_model(parent as usize, platform, rect, view_model)
    }

    pub fn parent_handle(&self) -> usize {
        self.parent_handle
    }

    pub fn platform(&self) -> EditorPlatformType {
        self.platform
    }

    pub fn rect(&self) -> SurfaceRect {
        self.rect
    }

    pub fn lines(&self) -> &[String] {
        &self.lines
    }

    pub fn buttons(&self) -> &[BridgeEditorButton] {
        &self.buttons
    }

    pub fn hit_test(&self, x: i32, y: i32) -> Option<BridgeEditorAction> {
        self.buttons
            .iter()
            .find(|button| {
                x >= button.x
                    && x < button.x + button.width
                    && y >= button.y
                    && y < button.y + button.height
            })
            .map(|button| button.action)
    }

    pub fn drag_export_hit_test(&self, x: i32, y: i32) -> bool {
        let has_completed_export = self
            .lines
            .iter()
            .any(|line| line.starts_with("Last export:"));
        has_completed_export
            && self.hit_test(x, y).is_none()
            && x >= 24
            && x < self.rect.width.saturating_sub(24)
            && (68..100).contains(&y)
    }
}

impl EditorPlatformType {
    pub fn from_vst3(platform_type: FIDString) -> Option<Self> {
        if fid_string_matches(platform_type, b"HWND\0") {
            Some(Self::WindowsHwnd)
        } else {
            None
        }
    }
}

#[derive(Debug, Error, Clone, PartialEq, Eq)]
pub enum EditorSurfaceError {
    #[error("editor parent handle is null")]
    NullParent,
    #[error("editor platform type is not supported")]
    UnsupportedPlatform,
    #[error("editor surface size must be positive")]
    InvalidSize,
    #[error("{0} failed while creating the native editor surface")]
    NativeCallFailed(&'static str),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NativeEditorSurfaceEvent {
    Action(BridgeEditorAction),
    DragExport,
    Tick,
}

pub type SurfaceEventCallback = unsafe fn(*mut c_void, NativeEditorSurfaceEvent);

pub struct NativeEditorSurface {
    inner: NativeEditorSurfaceInner,
}

impl NativeEditorSurface {
    pub unsafe fn attach(
        spec: &EditorSurfaceSpec,
        callback_context: *mut c_void,
        callback: SurfaceEventCallback,
    ) -> Result<Self, EditorSurfaceError> {
        #[cfg(target_os = "windows")]
        {
            let inner = unsafe {
                windows_editor::WindowsEditorSurface::attach(spec, callback_context, callback)
            }?;
            return Ok(Self {
                inner: NativeEditorSurfaceInner::Windows(inner),
            });
        }

        #[cfg(not(target_os = "windows"))]
        {
            let _ = (spec, callback_context, callback);
            Err(EditorSurfaceError::UnsupportedPlatform)
        }
    }

    pub fn update(&mut self, spec: &EditorSurfaceSpec) -> Result<(), EditorSurfaceError> {
        match &mut self.inner {
            #[cfg(target_os = "windows")]
            NativeEditorSurfaceInner::Windows(surface) => surface.update(spec),
            #[cfg(not(target_os = "windows"))]
            NativeEditorSurfaceInner::Unsupported => {
                let _ = spec;
                Err(EditorSurfaceError::UnsupportedPlatform)
            }
        }
    }
}

enum NativeEditorSurfaceInner {
    #[cfg(target_os = "windows")]
    Windows(windows_editor::WindowsEditorSurface),
    #[cfg(not(target_os = "windows"))]
    Unsupported,
}

fn fid_string_matches(platform_type: FIDString, expected: &[u8]) -> bool {
    if platform_type.is_null() {
        return false;
    }

    // VST3 platform identifiers are null-terminated string constants supplied
    // by the host. We only read until the first terminator and compare to the
    // SDK token we support for this bridge editor.
    unsafe { CStr::from_ptr(platform_type).to_bytes_with_nul() == expected }
}

#[cfg(target_os = "windows")]
mod windows_editor {
    use std::ffi::c_void;
    use std::mem;
    use std::ptr;
    use std::sync::OnceLock;

    use windows_sys::Win32::Foundation::{
        COLORREF, HINSTANCE, HWND, LPARAM, LRESULT, RECT, WPARAM,
    };
    use windows_sys::Win32::Graphics::Gdi::{
        BeginPaint, CreateSolidBrush, DC_PEN, DeleteObject, EndPaint, FillRect, GetStockObject,
        HBRUSH, HGDIOBJ, InvalidateRect, NULL_BRUSH, PAINTSTRUCT, Rectangle, SelectObject,
        SetBkMode, SetDCPenColor, SetTextColor, TRANSPARENT, TextOutW,
    };
    use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        CREATESTRUCTW, CS_HREDRAW, CS_VREDRAW, CreateWindowExW, DefWindowProcW, DestroyWindow,
        GWLP_USERDATA, GetWindowLongPtrW, KillTimer, RegisterClassW, SW_SHOW, SWP_NOACTIVATE,
        SWP_NOZORDER, SetTimer, SetWindowLongPtrW, SetWindowPos, ShowWindow, WM_ERASEBKGND,
        WM_LBUTTONDOWN, WM_NCCREATE, WM_NCDESTROY, WM_PAINT, WM_TIMER, WNDCLASSW, WS_CHILD,
        WS_VISIBLE,
    };

    use super::{
        EditorSurfaceError, EditorSurfaceSpec, NativeEditorSurfaceEvent, SurfaceEventCallback,
    };

    const CLASS_NAME: &str = "ATRI Bridge Editor Surface";
    const WINDOW_TITLE: &str = "ATRI Bridge";
    const EXPORT_POLL_TIMER_ID: usize = 1;
    const EXPORT_POLL_INTERVAL_MS: u32 = 200;

    static WINDOW_CLASS_ATOM: OnceLock<u16> = OnceLock::new();

    pub struct WindowsEditorSurface {
        hwnd: HWND,
    }

    impl WindowsEditorSurface {
        pub unsafe fn attach(
            spec: &EditorSurfaceSpec,
            callback_context: *mut c_void,
            callback: SurfaceEventCallback,
        ) -> Result<Self, EditorSurfaceError> {
            register_window_class()?;

            let class_name = wide_null(CLASS_NAME);
            let title = wide_null(WINDOW_TITLE);
            let state = Box::new(WindowsEditorWindowState {
                spec: spec.clone(),
                callback_context,
                callback,
            });
            let state_ptr = Box::into_raw(state);
            let module = current_module_handle();
            let rect = spec.rect();
            let hwnd = unsafe {
                CreateWindowExW(
                    0,
                    class_name.as_ptr(),
                    title.as_ptr(),
                    WS_CHILD | WS_VISIBLE,
                    rect.left,
                    rect.top,
                    rect.width,
                    rect.height,
                    spec.parent_handle() as HWND,
                    0,
                    module,
                    state_ptr.cast(),
                )
            };
            if hwnd == 0 {
                return Err(EditorSurfaceError::NativeCallFailed("CreateWindowExW"));
            }

            unsafe {
                ShowWindow(hwnd, SW_SHOW);
                SetTimer(hwnd, EXPORT_POLL_TIMER_ID, EXPORT_POLL_INTERVAL_MS, None);
            }
            Ok(Self { hwnd })
        }

        pub fn update(&mut self, spec: &EditorSurfaceSpec) -> Result<(), EditorSurfaceError> {
            let state = window_state(self.hwnd)
                .ok_or(EditorSurfaceError::NativeCallFailed("GetWindowLongPtrW"))?;
            unsafe {
                (*state).spec = spec.clone();
                let rect = spec.rect();
                SetWindowPos(
                    self.hwnd,
                    0,
                    rect.left,
                    rect.top,
                    rect.width,
                    rect.height,
                    SWP_NOZORDER | SWP_NOACTIVATE,
                );
                InvalidateRect(self.hwnd, ptr::null(), 1);
            }
            Ok(())
        }
    }

    impl Drop for WindowsEditorSurface {
        fn drop(&mut self) {
            if self.hwnd != 0 {
                unsafe {
                    KillTimer(self.hwnd, EXPORT_POLL_TIMER_ID);
                    DestroyWindow(self.hwnd);
                }
                self.hwnd = 0;
            }
        }
    }

    struct WindowsEditorWindowState {
        spec: EditorSurfaceSpec,
        callback_context: *mut c_void,
        callback: SurfaceEventCallback,
    }

    unsafe extern "system" fn window_proc(
        hwnd: HWND,
        msg: u32,
        wparam: WPARAM,
        lparam: LPARAM,
    ) -> LRESULT {
        match msg {
            WM_NCCREATE => {
                let create = lparam as *const CREATESTRUCTW;
                if create.is_null() {
                    return 0;
                }
                let state = unsafe { (*create).lpCreateParams as *mut WindowsEditorWindowState };
                if state.is_null() {
                    return 0;
                }
                unsafe {
                    SetWindowLongPtrW(hwnd, GWLP_USERDATA, state as isize);
                }
                1
            }
            WM_NCDESTROY => {
                unsafe {
                    KillTimer(hwnd, EXPORT_POLL_TIMER_ID);
                }
                let state = window_state(hwnd);
                if let Some(state) = state {
                    unsafe {
                        SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
                        drop(Box::from_raw(state));
                    }
                }
                unsafe { DefWindowProcW(hwnd, msg, wparam, lparam) }
            }
            WM_ERASEBKGND => 1,
            WM_PAINT => {
                if let Some(state) = window_state(hwnd) {
                    unsafe {
                        paint(hwnd, &(*state).spec);
                    }
                    0
                } else {
                    unsafe { DefWindowProcW(hwnd, msg, wparam, lparam) }
                }
            }
            WM_LBUTTONDOWN => {
                if let Some(state) = window_state(hwnd) {
                    let x = signed_low_word(lparam);
                    let y = signed_high_word(lparam);
                    let dispatch = unsafe {
                        let state_ref = &*state;
                        let event = state_ref
                            .spec
                            .hit_test(x, y)
                            .map(NativeEditorSurfaceEvent::Action)
                            .or_else(|| {
                                state_ref
                                    .spec
                                    .drag_export_hit_test(x, y)
                                    .then_some(NativeEditorSurfaceEvent::DragExport)
                            });
                        event.map(|event| (event, state_ref.callback_context, state_ref.callback))
                    };
                    if let Some((event, context, callback)) = dispatch {
                        unsafe {
                            callback(context, event);
                        }
                    }
                    0
                } else {
                    unsafe { DefWindowProcW(hwnd, msg, wparam, lparam) }
                }
            }
            WM_TIMER => {
                if wparam == EXPORT_POLL_TIMER_ID {
                    if let Some(state) = window_state(hwnd) {
                        let (context, callback) =
                            unsafe { ((*state).callback_context, (*state).callback) };
                        unsafe {
                            callback(context, NativeEditorSurfaceEvent::Tick);
                        }
                        return 0;
                    }
                }
                unsafe { DefWindowProcW(hwnd, msg, wparam, lparam) }
            }
            _ => unsafe { DefWindowProcW(hwnd, msg, wparam, lparam) },
        }
    }

    fn register_window_class() -> Result<(), EditorSurfaceError> {
        let atom = WINDOW_CLASS_ATOM.get_or_init(|| unsafe {
            let class_name = wide_null(CLASS_NAME);
            let wnd_class = WNDCLASSW {
                style: CS_HREDRAW | CS_VREDRAW,
                lpfnWndProc: Some(window_proc),
                cbClsExtra: 0,
                cbWndExtra: 0,
                hInstance: current_module_handle(),
                hIcon: 0,
                hCursor: 0,
                hbrBackground: 0,
                lpszMenuName: ptr::null(),
                lpszClassName: class_name.as_ptr(),
            };
            RegisterClassW(&wnd_class)
        });
        if *atom == 0 {
            Err(EditorSurfaceError::NativeCallFailed("RegisterClassW"))
        } else {
            Ok(())
        }
    }

    fn current_module_handle() -> HINSTANCE {
        unsafe { GetModuleHandleW(ptr::null()) as HINSTANCE }
    }

    fn window_state(hwnd: HWND) -> Option<*mut WindowsEditorWindowState> {
        if hwnd == 0 {
            return None;
        }
        let ptr = unsafe { GetWindowLongPtrW(hwnd, GWLP_USERDATA) };
        (ptr != 0).then_some(ptr as *mut WindowsEditorWindowState)
    }

    unsafe fn paint(hwnd: HWND, spec: &EditorSurfaceSpec) {
        let mut paint = unsafe { mem::zeroed::<PAINTSTRUCT>() };
        let hdc = unsafe { BeginPaint(hwnd, &mut paint) };
        if hdc == 0 {
            return;
        }

        fill_rect(hdc, &paint.rcPaint, rgb(24, 28, 34));
        unsafe {
            SetBkMode(hdc, TRANSPARENT as i32);
            SetTextColor(hdc, rgb(234, 238, 243));
        }

        let mut y = 22;
        for line in spec.lines() {
            text_out(hdc, 24, y, line);
            y += 24;
        }

        for button in spec.buttons() {
            let rect = RECT {
                left: button.x,
                top: button.y,
                right: button.x + button.width,
                bottom: button.y + button.height,
            };
            fill_rect(hdc, &rect, rgb(43, 49, 58));
            stroke_rect(hdc, &rect, rgb(110, 126, 148));
            unsafe {
                SetTextColor(hdc, rgb(244, 247, 250));
            }
            text_out(hdc, button.x + 10, button.y + 9, button.label);
        }

        unsafe {
            EndPaint(hwnd, &paint);
        }
    }

    fn fill_rect(hdc: isize, rect: &RECT, color: COLORREF) {
        unsafe {
            let brush = CreateSolidBrush(color);
            if brush != 0 {
                FillRect(hdc, rect, brush as HBRUSH);
                DeleteObject(brush as HGDIOBJ);
            }
        }
    }

    fn stroke_rect(hdc: isize, rect: &RECT, color: COLORREF) {
        unsafe {
            let old_brush = SelectObject(hdc, GetStockObject(NULL_BRUSH));
            let old_pen = SelectObject(hdc, GetStockObject(DC_PEN));
            SetDCPenColor(hdc, color);
            Rectangle(hdc, rect.left, rect.top, rect.right, rect.bottom);
            if old_brush != 0 {
                SelectObject(hdc, old_brush);
            }
            if old_pen != 0 {
                SelectObject(hdc, old_pen);
            }
        }
    }

    fn text_out(hdc: isize, x: i32, y: i32, text: &str) {
        let text = wide_null(text);
        let len = text.len().saturating_sub(1).min(i32::MAX as usize) as i32;
        unsafe {
            TextOutW(hdc, x, y, text.as_ptr(), len);
        }
    }

    fn wide_null(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(std::iter::once(0)).collect()
    }

    fn rgb(red: u8, green: u8, blue: u8) -> COLORREF {
        red as COLORREF | ((green as COLORREF) << 8) | ((blue as COLORREF) << 16)
    }

    fn signed_low_word(value: LPARAM) -> i32 {
        (value as u32 & 0xffff) as i16 as i32
    }

    fn signed_high_word(value: LPARAM) -> i32 {
        ((value as u32 >> 16) & 0xffff) as i16 as i32
    }
}
