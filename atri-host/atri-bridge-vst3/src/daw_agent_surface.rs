use thiserror::Error;

pub const DEFAULT_DAW_AGENT_HOST_NAME: &str = "Studio One";
pub const DEFAULT_DAW_AGENT_PROJECT_SESSION_ID: &str = "default_project";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DawAgentWorkspace {
    AtriStudio,
    HostProject,
}

impl DawAgentWorkspace {
    pub fn as_query_value(self) -> &'static str {
        match self {
            Self::AtriStudio => "atri_studio",
            Self::HostProject => "host_project",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DawAgentSurfaceParams {
    project_session_id: String,
    instance_id: String,
    workspace: DawAgentWorkspace,
    host_name: String,
}

pub trait DawAgentSurfaceOpener {
    fn open(&self, url: &str) -> Result<(), DawAgentSurfaceOpenError>;
}

#[derive(Debug, Clone, Copy, Default)]
pub struct NativeDawAgentSurfaceOpener;

impl DawAgentSurfaceOpener for NativeDawAgentSurfaceOpener {
    fn open(&self, url: &str) -> Result<(), DawAgentSurfaceOpenError> {
        open_native_url(url)
    }
}

#[derive(Debug, Error, Clone, PartialEq, Eq)]
pub enum DawAgentSurfaceOpenError {
    #[error("opening the DAW agent surface is not supported on this platform")]
    UnsupportedPlatform,
    #[error("{call} failed while opening the DAW agent surface: {code}")]
    NativeCallFailed { call: &'static str, code: isize },
}

impl DawAgentSurfaceParams {
    pub fn from_project(
        project_title: Option<&str>,
        project_revision: Option<&str>,
        instance_id: impl Into<String>,
    ) -> Self {
        Self {
            project_session_id: project_session_id(project_title, project_revision),
            instance_id: instance_id.into(),
            workspace: DawAgentWorkspace::AtriStudio,
            host_name: DEFAULT_DAW_AGENT_HOST_NAME.to_string(),
        }
    }

    pub fn project_session_id(&self) -> &str {
        &self.project_session_id
    }

    pub fn instance_id(&self) -> &str {
        &self.instance_id
    }

    pub fn workspace(&self) -> DawAgentWorkspace {
        self.workspace
    }

    pub fn host_name(&self) -> &str {
        &self.host_name
    }

    pub fn with_workspace(mut self, workspace: DawAgentWorkspace) -> Self {
        self.workspace = workspace;
        self
    }

    pub fn with_host_name(mut self, host_name: impl Into<String>) -> Self {
        let host_name = host_name.into();
        self.host_name = if host_name.trim().is_empty() {
            DEFAULT_DAW_AGENT_HOST_NAME.to_string()
        } else {
            host_name
        };
        self
    }
}

pub fn daw_agent_surface_url(base_url: &str, params: &DawAgentSurfaceParams) -> String {
    format!(
        "{base_url}/?surface=daw-agent&project_session_id={}&instance_id={}&workspace={}&host={}",
        percent_encode_query(params.project_session_id()),
        percent_encode_query(params.instance_id()),
        percent_encode_query(params.workspace().as_query_value()),
        percent_encode_query(params.host_name()),
    )
}

fn project_session_id(project_title: Option<&str>, project_revision: Option<&str>) -> String {
    let title_id = project_title.and_then(slug_project_title);
    if let Some(title_id) = title_id {
        return title_id;
    }
    if let Some(revision) = project_revision
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        return format!("atri-project-r{revision}");
    }
    DEFAULT_DAW_AGENT_PROJECT_SESSION_ID.to_string()
}

fn slug_project_title(project_title: &str) -> Option<String> {
    let mut slug = String::new();
    let mut last_was_separator = false;

    for ch in project_title.trim().chars() {
        if ch.is_ascii_alphanumeric() {
            slug.push(ch.to_ascii_lowercase());
            last_was_separator = false;
        } else if !last_was_separator && !slug.is_empty() {
            slug.push('-');
            last_was_separator = true;
        }
    }

    while slug.ends_with('-') {
        slug.pop();
    }

    (!slug.is_empty()).then_some(slug)
}

pub(crate) fn percent_encode_query(value: &str) -> String {
    let mut encoded = String::new();
    for byte in value.as_bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' => {
                encoded.push(*byte as char);
            }
            _ => {
                encoded.push('%');
                encoded.push(hex_digit(byte >> 4));
                encoded.push(hex_digit(byte & 0x0f));
            }
        }
    }
    encoded
}

fn hex_digit(value: u8) -> char {
    match value {
        0..=9 => (b'0' + value) as char,
        10..=15 => (b'A' + value - 10) as char,
        _ => unreachable!("nibble is always in range"),
    }
}

#[cfg(target_os = "windows")]
fn open_native_url(url: &str) -> Result<(), DawAgentSurfaceOpenError> {
    use std::ptr;

    use windows_sys::Win32::UI::Shell::ShellExecuteW;
    use windows_sys::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;

    let operation = wide_null("open");
    let file = wide_null(url);
    let result = unsafe {
        ShellExecuteW(
            0,
            operation.as_ptr(),
            file.as_ptr(),
            ptr::null(),
            ptr::null(),
            SW_SHOWNORMAL,
        )
    };
    if (result as isize) <= 32 {
        Err(DawAgentSurfaceOpenError::NativeCallFailed {
            call: "ShellExecuteW",
            code: result as isize,
        })
    } else {
        Ok(())
    }
}

#[cfg(not(target_os = "windows"))]
fn open_native_url(_url: &str) -> Result<(), DawAgentSurfaceOpenError> {
    Err(DawAgentSurfaceOpenError::UnsupportedPlatform)
}

#[cfg(target_os = "windows")]
fn wide_null(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(std::iter::once(0)).collect()
}
