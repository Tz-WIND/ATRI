//! Embedded WebView2 surface for the ATRI Bridge editor (Windows only).
//!
//! The bridge editor hosts the existing `daw-agent` operation page inside the
//! plug-in window by parenting a WebView2 controller to the native editor
//! surface. Environment and controller creation are asynchronous; the WebView2
//! runtime invokes the completion handlers on the host UI thread message loop,
//! so we never spin our own message pump from inside `IPlugView::attached`.

#![cfg(target_os = "windows")]

use std::sync::{Arc, Mutex};

use thiserror::Error;
use webview2_com::{
    CreateCoreWebView2ControllerCompletedHandler, CreateCoreWebView2EnvironmentCompletedHandler,
    Microsoft::Web::WebView2::Win32::{
        CreateCoreWebView2EnvironmentWithOptions, ICoreWebView2Controller, ICoreWebView2Environment,
    },
};
use windows::Win32::Foundation::{HWND, RECT};
use windows::core::{HSTRING, PCWSTR};

const WEBVIEW_USER_DATA_SUBDIR: &str = "ATRI\\bridge-webview2";
type WebViewFailureCallback = Box<dyn Fn() + Send + Sync + 'static>;

#[derive(Debug, Error, Clone, PartialEq, Eq)]
pub enum WebViewError {
    #[error("failed to start WebView2 environment creation: {0}")]
    EnvironmentCreate(String),
}

/// Client-relative placement for the embedded WebView2 surface.
///
/// Expressed with plain integers so callers that work in `windows-sys` types do
/// not need to depend on the `windows` crate's `RECT`/`HWND`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WebViewBounds {
    pub left: i32,
    pub top: i32,
    pub width: i32,
    pub height: i32,
}

impl WebViewBounds {
    fn to_rect(self) -> RECT {
        RECT {
            left: self.left,
            top: self.top,
            right: self.left + self.width.max(0),
            bottom: self.top + self.height.max(0),
        }
    }
}

/// Owns the embedded WebView2 controller for one editor surface.
///
/// The handle can be created and dropped before the asynchronous controller
/// becomes available; pending state is reconciled when the completion handler
/// runs on the UI thread.
pub struct BridgeWebView {
    shared: Arc<WebViewShared>,
}

struct WebViewShared {
    inner: Mutex<WebViewInner>,
    on_failure: WebViewFailureCallback,
}

struct WebViewInner {
    controller: Option<ICoreWebView2Controller>,
    bounds: WebViewBounds,
    url: String,
    closed: bool,
    failed: bool,
}

// SAFETY: The WebView2 controller stored here is only ever created, resized,
// and closed on the host UI thread. `IPlugView::attached`, `onSize`, and
// `removed` are delivered on that thread, and the WebView2 completion handlers
// are dispatched by the host message loop on the same thread. The bridge never
// touches the controller from the audio thread or the dashboard worker threads.
// The `Arc<Mutex<..>>` exists solely to satisfy the `Send`/`Sync` bounds the
// surrounding plug-view requires, not to enable real cross-thread access to the
// COM interface.
unsafe impl Send for WebViewShared {}
unsafe impl Sync for WebViewShared {}

impl BridgeWebView {
    /// Begin hosting the `daw-agent` page inside `parent`.
    ///
    /// # Safety
    ///
    /// `parent` must be a valid HWND owned by the calling UI thread for the
    /// lifetime of the returned [`BridgeWebView`].
    pub unsafe fn attach(
        parent: isize,
        bounds: WebViewBounds,
        url: &str,
        on_failure: impl Fn() + Send + Sync + 'static,
    ) -> Result<Self, WebViewError> {
        let parent = HWND(parent);
        let shared = Arc::new(WebViewShared::new(bounds, url, Box::new(on_failure)));

        let user_data_folder = webview_user_data_folder();
        let shared_for_env = Arc::clone(&shared);

        let env_handler = CreateCoreWebView2EnvironmentCompletedHandler::create(Box::new(
            move |result: windows::core::Result<()>,
                  environment: Option<ICoreWebView2Environment>|
                  -> windows::core::Result<()> {
                if result.is_err() {
                    shared_for_env.mark_failed();
                    return Ok(());
                }
                let Some(environment) = environment else {
                    shared_for_env.mark_failed();
                    return Ok(());
                };
                if !shared_for_env.should_create_controller() {
                    return Ok(());
                }

                let shared_for_controller = Arc::clone(&shared_for_env);
                let controller_handler =
                    CreateCoreWebView2ControllerCompletedHandler::create(Box::new(
                        move |result: windows::core::Result<()>,
                              controller: Option<ICoreWebView2Controller>|
                              -> windows::core::Result<()> {
                            if result.is_err() {
                                shared_for_controller.mark_failed();
                                return Ok(());
                            }
                            let Some(controller) = controller else {
                                shared_for_controller.mark_failed();
                                return Ok(());
                            };
                            shared_for_controller.on_controller_ready(controller);
                            Ok(())
                        },
                    ));

                if unsafe { environment.CreateCoreWebView2Controller(parent, &controller_handler) }
                    .is_err()
                {
                    shared_for_env.mark_failed();
                }
                Ok(())
            },
        ));

        let user_data = user_data_folder
            .as_ref()
            .map(|folder| PCWSTR(folder.as_ptr()))
            .unwrap_or_else(PCWSTR::null);

        unsafe {
            CreateCoreWebView2EnvironmentWithOptions(PCWSTR::null(), user_data, None, &env_handler)
        }
        .map_err(|err| WebViewError::EnvironmentCreate(err.message()))?;

        Ok(Self { shared })
    }

    /// Resize the embedded WebView2 to the new client bounds.
    pub fn set_bounds(&self, bounds: WebViewBounds) {
        self.shared.set_bounds(bounds);
    }

    /// Reconcile the desired placement and navigation target with the live
    /// controller, if it has already been created.
    pub fn update(&self, bounds: WebViewBounds, url: &str) {
        self.shared.update(bounds, url);
    }
}

impl Drop for BridgeWebView {
    fn drop(&mut self) {
        self.shared.close();
    }
}

impl WebViewShared {
    fn new(bounds: WebViewBounds, url: &str, on_failure: WebViewFailureCallback) -> Self {
        Self {
            inner: Mutex::new(WebViewInner {
                controller: None,
                bounds,
                url: url.to_string(),
                closed: false,
                failed: false,
            }),
            on_failure,
        }
    }

    fn on_controller_ready(&self, controller: ICoreWebView2Controller) {
        let (bounds, url, should_close) = {
            let inner = lock(&self.inner);
            (
                inner.bounds,
                inner.url.clone(),
                inner.closed || inner.failed,
            )
        };
        if should_close {
            unsafe {
                let _ = controller.Close();
            }
            return;
        }

        let navigate_url = HSTRING::from(url.as_str());
        let result = unsafe {
            controller
                .SetBounds(bounds.to_rect())
                .and_then(|()| controller.SetIsVisible(true))
                .and_then(|()| controller.CoreWebView2())
                .and_then(|webview| webview.Navigate(&navigate_url))
        };
        if result.is_err() {
            unsafe {
                let _ = controller.Close();
            }
            self.mark_failed();
            return;
        }

        let mut inner = lock(&self.inner);
        if inner.closed || inner.failed {
            unsafe {
                let _ = controller.Close();
            }
            return;
        }
        inner.controller = Some(controller);
    }

    fn update(&self, bounds: WebViewBounds, url: &str) {
        self.set_bounds(bounds);
        self.set_url(url);
    }

    fn set_url(&self, url: &str) {
        let controller = {
            let mut inner = lock(&self.inner);
            if inner.url == url {
                return;
            }
            inner.url = url.to_string();
            if inner.failed {
                return;
            }
            inner.controller.clone()
        };

        if let Some(controller) = controller
            && self.navigate_controller(&controller, url).is_err()
        {
            self.mark_failed();
        }
    }

    fn navigate_controller(
        &self,
        controller: &ICoreWebView2Controller,
        url: &str,
    ) -> windows::core::Result<()> {
        let navigate_url = HSTRING::from(url);
        unsafe {
            controller
                .CoreWebView2()
                .and_then(|webview| webview.Navigate(&navigate_url))
        }
    }

    fn set_bounds(&self, bounds: WebViewBounds) {
        let failed = {
            let mut inner = lock(&self.inner);
            inner.bounds = bounds;
            if inner.failed {
                return;
            }
            inner
                .controller
                .as_ref()
                .map(|controller| unsafe { controller.SetBounds(bounds.to_rect()).is_err() })
                .unwrap_or(false)
        };
        if failed {
            self.mark_failed();
        }
    }

    fn close(&self) {
        let mut inner = lock(&self.inner);
        inner.closed = true;
        if let Some(controller) = inner.controller.take() {
            unsafe {
                let _ = controller.Close();
            }
        }
    }

    fn should_create_controller(&self) -> bool {
        let inner = lock(&self.inner);
        !inner.closed && !inner.failed
    }

    fn mark_failed(&self) {
        let should_notify = {
            let mut inner = lock(&self.inner);
            if inner.failed || inner.closed {
                false
            } else {
                inner.failed = true;
                true
            }
        };
        if should_notify {
            (self.on_failure)();
        }
    }
}

fn lock(inner: &Mutex<WebViewInner>) -> std::sync::MutexGuard<'_, WebViewInner> {
    inner.lock().unwrap_or_else(|err| err.into_inner())
}

/// Resolve a writable user-data folder for the WebView2 runtime.
///
/// The plug-in is hosted inside the DAW process, which usually cannot write to
/// the bundle directory, so the runtime is pointed at `%LOCALAPPDATA%\ATRI\
/// bridge-webview2`. Returning `None` lets WebView2 fall back to its default
/// location.
fn webview_user_data_folder() -> Option<HSTRING> {
    let local_app_data = std::env::var_os("LOCALAPPDATA")?;
    let folder = std::path::Path::new(&local_app_data).join(WEBVIEW_USER_DATA_SUBDIR);
    let _ = std::fs::create_dir_all(&folder);
    Some(HSTRING::from(folder.as_os_str()))
}

#[cfg(test)]
mod tests {
    use std::sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    };

    use super::*;

    fn shared_for_test(
        bounds: WebViewBounds,
        url: &str,
        failure_count: Arc<AtomicUsize>,
    ) -> WebViewShared {
        WebViewShared::new(
            bounds,
            url,
            Box::new(move || {
                failure_count.fetch_add(1, Ordering::SeqCst);
            }),
        )
    }

    #[test]
    fn webview_shared_update_changes_pending_url_before_controller_exists() {
        let bounds = WebViewBounds {
            left: 0,
            top: 170,
            width: 900,
            height: 550,
        };
        let updated_bounds = WebViewBounds {
            left: 0,
            top: 170,
            width: 960,
            height: 600,
        };
        let shared = shared_for_test(
            bounds,
            "http://127.0.0.1:6185/?surface=daw-agent&project_session_id=default_project",
            Arc::new(AtomicUsize::new(0)),
        );

        shared.update(
            updated_bounds,
            "http://127.0.0.1:6185/?surface=daw-agent&project_session_id=atri-session",
        );

        let inner = lock(&shared.inner);
        assert_eq!(inner.bounds, updated_bounds);
        assert_eq!(
            inner.url,
            "http://127.0.0.1:6185/?surface=daw-agent&project_session_id=atri-session"
        );
    }

    #[test]
    fn webview_shared_closed_surface_skips_async_controller_creation() {
        let shared = shared_for_test(
            WebViewBounds {
                left: 0,
                top: 170,
                width: 900,
                height: 550,
            },
            "http://127.0.0.1:6185/?surface=daw-agent",
            Arc::new(AtomicUsize::new(0)),
        );

        assert!(shared.should_create_controller());

        shared.close();

        assert!(!shared.should_create_controller());
    }

    #[test]
    fn webview_shared_async_failure_marks_failed_once_and_notifies() {
        let failure_count = Arc::new(AtomicUsize::new(0));
        let shared = shared_for_test(
            WebViewBounds {
                left: 0,
                top: 170,
                width: 900,
                height: 550,
            },
            "http://127.0.0.1:6185/?surface=daw-agent",
            Arc::clone(&failure_count),
        );

        shared.mark_failed();
        shared.mark_failed();

        let inner = lock(&shared.inner);
        assert!(inner.failed);
        assert_eq!(failure_count.load(Ordering::SeqCst), 1);
    }
}
