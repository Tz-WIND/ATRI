use std::collections::HashMap;
use std::sync::{Arc, Mutex, mpsc};
use std::time::Duration;

use atri_core::plugin::{
    EditorParentHandle, EditorParentKind, PluginEditorContext, PluginEditorHandle,
};
use atri_engine::processor::Processor;
use raw_window_handle::{HasWindowHandle, RawWindowHandle};
use winit::application::ApplicationHandler;
use winit::dpi::PhysicalSize;
use winit::event::WindowEvent;
use winit::event_loop::{ActiveEventLoop, EventLoop, EventLoopBuilder, EventLoopProxy};
use winit::window::{UserAttentionType, Window, WindowId};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct EditorKey {
    pub track_id: u32,
    pub slot_index: usize,
}

#[derive(Debug, Clone)]
pub struct OpenEditorWindow {
    pub parent: EditorParentHandle,
    pub title: String,
    pub already_open: bool,
}

type SharedEditors = Arc<Mutex<HashMap<EditorKey, Box<dyn PluginEditorHandle>>>>;
type SharedProcessor = Arc<Mutex<dyn Processor>>;
type OpenReply = mpsc::Sender<Result<OpenEditorWindow, String>>;

enum EditorWindowCommand {
    OpenAndAttach {
        key: EditorKey,
        title: String,
        processor: SharedProcessor,
        reply: OpenReply,
    },
    Resize {
        key: EditorKey,
        width: u32,
        height: u32,
    },
    Shutdown,
}

#[derive(Clone)]
pub struct EditorWindowManager {
    proxy: EventLoopProxy<EditorWindowCommand>,
}

impl EditorWindowManager {
    pub fn start_on_main_thread() -> Result<(Self, EditorWindowRuntime), String> {
        let thread_init = initialize_editor_thread()?;
        let editors: SharedEditors = Arc::new(Mutex::new(HashMap::new()));
        let event_loop = build_editor_event_loop()
            .map_err(|err| format!("failed to create editor event loop: {err}"))?;
        let proxy = event_loop.create_proxy();
        let app = EditorWindowApp::new(editors, proxy.clone());
        let runtime = EditorWindowRuntime {
            event_loop,
            app,
            _thread_init: thread_init,
        };

        Ok((Self { proxy }, runtime))
    }

    pub fn open_and_attach(
        &self,
        key: EditorKey,
        title: String,
        processor: SharedProcessor,
    ) -> Result<OpenEditorWindow, String> {
        let (reply_tx, reply_rx) = mpsc::channel();
        self.proxy
            .send_event(EditorWindowCommand::OpenAndAttach {
                key,
                title,
                processor,
                reply: reply_tx,
            })
            .map_err(|_| "editor window event loop is closed".to_string())?;

        reply_rx
            .recv_timeout(Duration::from_secs(60))
            .map_err(|err| format!("timed out opening plugin editor: {err}"))?
    }

    pub fn shutdown(&self) {
        let _ = self.proxy.send_event(EditorWindowCommand::Shutdown);
    }
}

pub struct EditorWindowRuntime {
    event_loop: EventLoop<EditorWindowCommand>,
    app: EditorWindowApp,
    _thread_init: EditorThreadInit,
}

impl EditorWindowRuntime {
    pub fn run(self) -> Result<(), String> {
        let Self {
            event_loop,
            mut app,
            _thread_init,
        } = self;
        event_loop
            .run_app(&mut app)
            .map_err(|err| format!("plugin editor event loop failed: {err}"))
    }
}

struct EditorWindowEntry {
    title: String,
    window: Window,
}

struct EditorWindowApp {
    editors: SharedEditors,
    proxy: EventLoopProxy<EditorWindowCommand>,
    windows: HashMap<EditorKey, EditorWindowEntry>,
    window_keys: HashMap<WindowId, EditorKey>,
}

impl EditorWindowApp {
    fn new(editors: SharedEditors, proxy: EventLoopProxy<EditorWindowCommand>) -> Self {
        Self {
            editors,
            proxy,
            windows: HashMap::new(),
            window_keys: HashMap::new(),
        }
    }

    fn editor_context(&self, key: EditorKey) -> PluginEditorContext {
        let proxy = self.proxy.clone();
        PluginEditorContext::new(move |width, height| {
            let _ = proxy.send_event(EditorWindowCommand::Resize { key, width, height });
        })
    }

    fn handle_open_and_attach(
        &mut self,
        event_loop: &ActiveEventLoop,
        key: EditorKey,
        title: String,
        processor: SharedProcessor,
        reply: OpenReply,
    ) {
        let window = match self.create_or_focus_window(event_loop, key, title) {
            Ok(window) => window,
            Err(err) => {
                let _ = reply.send(Err(err));
                return;
            }
        };

        if window.already_open
            && self
                .editors
                .lock()
                .map(|editors| editors.contains_key(&key))
                .unwrap_or(false)
        {
            let _ = reply.send(Ok(window));
            return;
        }

        if let Some(entry) = self.windows.get(&key) {
            entry.window.set_visible(true);
            entry.window.focus_window();
            entry
                .window
                .request_user_attention(Some(UserAttentionType::Informational));
        }

        let context = self.editor_context(key);
        log::info!("opening native plugin editor for {key:?}");
        let editor = match processor.lock() {
            Ok(mut processor) => match processor.open_plugin_editor(window.parent, context) {
                Ok(editor) => editor,
                Err(err) => {
                    self.close_key(key);
                    let _ = reply.send(Err(err));
                    return;
                }
            },
            Err(_) => {
                self.close_key(key);
                let _ = reply.send(Err("plugin instance is unavailable".to_string()));
                return;
            }
        };
        log::info!("native plugin editor attached for {key:?}");

        let attach_result = {
            match self.editors.lock() {
                Ok(mut editors) => {
                    if let Some(mut old_editor) = editors.insert(key, editor) {
                        old_editor.close();
                    }
                    Ok(())
                }
                Err(_) => Err("editor registry is poisoned".to_string()),
            }
        };

        if let Err(err) = attach_result {
            self.close_key(key);
            let _ = reply.send(Err(err));
            return;
        }

        let _ = reply.send(Ok(window));
    }

    fn create_or_focus_window(
        &mut self,
        event_loop: &ActiveEventLoop,
        key: EditorKey,
        title: String,
    ) -> Result<OpenEditorWindow, String> {
        if let Some(entry) = self.windows.get(&key) {
            entry.window.set_visible(true);
            entry
                .window
                .request_user_attention(Some(UserAttentionType::Informational));
            return editor_parent_handle(&entry.window).map(|parent| OpenEditorWindow {
                parent,
                title: entry.title.clone(),
                already_open: true,
            });
        }

        let attributes = Window::default_attributes()
            .with_title(title.clone())
            .with_inner_size(PhysicalSize::new(900_u32, 620_u32))
            .with_resizable(false);
        let window = match event_loop.create_window(attributes) {
            Ok(window) => window,
            Err(err) => return Err(format!("failed to create editor window: {err}")),
        };
        window.set_resizable(false);

        let id = window.id();
        let parent = match editor_parent_handle(&window) {
            Ok(parent) => parent,
            Err(err) => {
                return Err(err);
            }
        };

        self.window_keys.insert(id, key);
        self.windows.insert(
            key,
            EditorWindowEntry {
                title: title.clone(),
                window,
            },
        );

        Ok(OpenEditorWindow {
            parent,
            title,
            already_open: false,
        })
    }

    fn handle_resize(&mut self, key: EditorKey, width: u32, height: u32) {
        let Some(entry) = self.windows.get(&key) else {
            return;
        };
        let size = PhysicalSize::new(width.max(120), height.max(80));
        log::info!(
            "setting native plugin editor physical size for {key:?}: {}x{}",
            size.width,
            size.height
        );
        entry.window.set_min_inner_size(None::<PhysicalSize<u32>>);
        entry.window.set_max_inner_size(None::<PhysicalSize<u32>>);
        let _ = entry.window.request_inner_size(size);
        entry.window.set_min_inner_size(Some(size));
        entry.window.set_max_inner_size(Some(size));
        self.resize_editor(key, size.width, size.height);
    }

    fn resize_editor(&self, key: EditorKey, width: u32, height: u32) {
        let Ok(mut editors) = self.editors.lock() else {
            return;
        };
        let Some(editor) = editors.get_mut(&key) else {
            return;
        };
        editor.resize(width, height);
    }

    fn close_key(&mut self, key: EditorKey) {
        if let Some(entry) = self.windows.remove(&key) {
            self.window_keys.remove(&entry.window.id());
        }
        self.close_editor(key);
    }

    fn close_window_id(&mut self, window_id: WindowId) {
        let Some(key) = self.window_keys.remove(&window_id) else {
            return;
        };
        self.windows.remove(&key);
        self.close_editor(key);
    }

    fn close_editor(&self, key: EditorKey) {
        if let Ok(mut editors) = self.editors.lock() {
            if let Some(mut editor) = editors.remove(&key) {
                editor.close();
            }
        }
    }

    fn close_all_editors(&self) {
        if let Ok(mut editors) = self.editors.lock() {
            for (_, mut editor) in editors.drain() {
                editor.close();
            }
        }
    }
}

impl ApplicationHandler<EditorWindowCommand> for EditorWindowApp {
    fn resumed(&mut self, _event_loop: &ActiveEventLoop) {}

    fn user_event(&mut self, event_loop: &ActiveEventLoop, event: EditorWindowCommand) {
        match event {
            EditorWindowCommand::OpenAndAttach {
                key,
                title,
                processor,
                reply,
            } => {
                self.handle_open_and_attach(event_loop, key, title, processor, reply);
            }
            EditorWindowCommand::Resize { key, width, height } => {
                self.handle_resize(key, width, height);
            }
            EditorWindowCommand::Shutdown => {
                self.close_all_editors();
                event_loop.exit();
            }
        }
    }

    fn window_event(
        &mut self,
        _event_loop: &ActiveEventLoop,
        window_id: WindowId,
        event: WindowEvent,
    ) {
        if matches!(event, WindowEvent::CloseRequested | WindowEvent::Destroyed) {
            self.close_window_id(window_id);
        }
    }

    fn exiting(&mut self, _event_loop: &ActiveEventLoop) {
        self.close_all_editors();
    }
}

#[cfg(target_os = "windows")]
struct EditorThreadInit {
    ole_initialized: bool,
}

#[cfg(target_os = "windows")]
impl Drop for EditorThreadInit {
    fn drop(&mut self) {
        if self.ole_initialized {
            unsafe {
                windows_sys::Win32::System::Ole::OleUninitialize();
            }
        }
    }
}

#[cfg(target_os = "windows")]
fn initialize_editor_thread() -> Result<EditorThreadInit, String> {
    use windows_sys::Win32::Foundation::RPC_E_CHANGED_MODE;
    use windows_sys::Win32::System::Ole::OleInitialize;

    let hr = unsafe { OleInitialize(std::ptr::null()) };
    if hr >= 0 {
        log::debug!("plugin editor thread initialized OLE/STA");
        return Ok(EditorThreadInit {
            ole_initialized: true,
        });
    }

    if hr == RPC_E_CHANGED_MODE {
        log::warn!(
            "plugin editor thread was already initialized with a different COM apartment; VST3 GUI may be unstable"
        );
        return Ok(EditorThreadInit {
            ole_initialized: false,
        });
    }

    Err(format!(
        "failed to initialize OLE on plugin editor thread: HRESULT 0x{:08X}",
        hr as u32
    ))
}

#[cfg(not(target_os = "windows"))]
struct EditorThreadInit;

#[cfg(not(target_os = "windows"))]
fn initialize_editor_thread() -> Result<EditorThreadInit, String> {
    Ok(EditorThreadInit)
}

fn build_editor_event_loop() -> Result<EventLoop<EditorWindowCommand>, winit::error::EventLoopError>
{
    let mut builder = EventLoop::<EditorWindowCommand>::with_user_event();
    configure_editor_event_loop(&mut builder);
    builder.build()
}

#[cfg(target_os = "windows")]
fn configure_editor_event_loop(builder: &mut EventLoopBuilder<EditorWindowCommand>) {
    let _ = builder;
}

#[cfg(target_os = "linux")]
fn configure_editor_event_loop(builder: &mut EventLoopBuilder<EditorWindowCommand>) {
    let _ = builder;
}

#[cfg(not(any(target_os = "windows", target_os = "linux")))]
fn configure_editor_event_loop(_builder: &mut EventLoopBuilder<EditorWindowCommand>) {}

fn editor_parent_handle(window: &Window) -> Result<EditorParentHandle, String> {
    let handle = window
        .window_handle()
        .map_err(|err| format!("failed to get native editor parent handle: {err}"))?
        .as_raw();

    match handle {
        RawWindowHandle::Win32(handle) => Ok(EditorParentHandle {
            kind: EditorParentKind::WindowsHwnd,
            raw: handle.hwnd.get() as usize as u64,
        }),
        RawWindowHandle::AppKit(handle) => Ok(EditorParentHandle {
            kind: EditorParentKind::MacOsNsView,
            raw: handle.ns_view.as_ptr() as usize as u64,
        }),
        RawWindowHandle::Xlib(handle) => Ok(EditorParentHandle {
            kind: EditorParentKind::X11Window,
            raw: u64::from(handle.window),
        }),
        RawWindowHandle::Xcb(handle) => Ok(EditorParentHandle {
            kind: EditorParentKind::XcbWindow,
            raw: handle.window.get() as u64,
        }),
        other => Err(format!(
            "unsupported editor parent window handle: {other:?}"
        )),
    }
}
