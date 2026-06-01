use crate::bridge_contract::{
    BridgeExportFormat, BridgeExportRequest, BridgeExportResponse, BridgeHostContext,
    BridgeMidiPreview, BridgeStatus,
};
use crate::dashboard_client::{
    DEFAULT_DASHBOARD_BASE_URL, DashboardClientError, DashboardEndpoint,
};
use crate::daw_agent_surface::DawAgentSurfaceParams;

const MIDI_PREVIEW_VISIBLE_ROWS: usize = 2;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BridgeConnectionState {
    Disconnected,
    Connecting,
    Connected,
    Error,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BridgeExportState {
    Idle,
    InProgress,
    Completed,
    Error,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BridgeEditorAction {
    OpenAtri,
    ExportMidi,
    ExportDawproject,
    ExportMixdownWav,
    ExportStems,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BridgeEditorButton {
    pub label: &'static str,
    pub action: BridgeEditorAction,
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
}

impl BridgeEditorButton {
    fn contains(&self, x: i32, y: i32) -> bool {
        x >= self.x && x < self.x + self.width && y >= self.y && y < self.y + self.height
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BridgeEditorPreviewTrackRow {
    pub title: String,
    pub detail: String,
    pub pitch_low: i32,
    pub pitch_high: i32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BridgeEditorPreview {
    pub title: String,
    pub detail: String,
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
    pub pitch_low: i32,
    pub pitch_high: i32,
    pub can_scroll_up: bool,
    pub can_scroll_down: bool,
    track_rows: Vec<BridgeEditorPreviewTrackRow>,
}

impl BridgeEditorPreview {
    pub fn contains(&self, x: i32, y: i32) -> bool {
        x >= self.x && x < self.x + self.width && y >= self.y && y < self.y + self.height
    }

    pub fn track_rows(&self) -> &[BridgeEditorPreviewTrackRow] {
        &self.track_rows
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BridgeEditorViewModel {
    width: i32,
    height: i32,
    lines: Vec<String>,
    buttons: Vec<BridgeEditorButton>,
    preview: Option<BridgeEditorPreview>,
}

impl BridgeEditorViewModel {
    pub fn from_state(state: &BridgeEditorState, width: i32, height: i32) -> Self {
        Self {
            width,
            height,
            lines: render_state_lines(state),
            buttons: layout_buttons(width),
            preview: preview_from_state(state, width),
        }
    }

    pub fn render_lines(&self) -> Vec<String> {
        self.lines.clone()
    }

    pub fn buttons(&self) -> &[BridgeEditorButton] {
        &self.buttons
    }

    pub fn preview(&self) -> Option<&BridgeEditorPreview> {
        self.preview.as_ref()
    }

    pub fn button_labels(&self) -> Vec<&'static str> {
        self.buttons.iter().map(|button| button.label).collect()
    }

    pub fn hit_test(&self, x: i32, y: i32) -> Option<BridgeEditorAction> {
        self.buttons
            .iter()
            .find(|button| button.contains(x, y))
            .map(|button| button.action)
    }

    pub fn width(&self) -> i32 {
        self.width
    }

    pub fn height(&self) -> i32 {
        self.height
    }
}

impl BridgeEditorAction {
    pub fn export_format(self) -> Option<BridgeExportFormat> {
        match self {
            Self::OpenAtri => None,
            Self::ExportMidi => Some(BridgeExportFormat::Midi),
            Self::ExportDawproject => Some(BridgeExportFormat::Dawproject),
            Self::ExportMixdownWav => Some(BridgeExportFormat::Wav),
            Self::ExportStems => Some(BridgeExportFormat::Stems),
        }
    }
}

#[derive(Debug, Clone)]
pub struct BridgeEditorState {
    connection: BridgeConnectionState,
    project_title: Option<String>,
    project_revision: Option<String>,
    last_error: Option<String>,
    dashboard_endpoint: DashboardEndpoint,
    /// Legacy dashboard base URL retained for backward compatibility.
    ///
    /// The Open ATRI action now launches [`Self::daw_agent_surface_url`] instead.
    open_atri_url: String,
    export_state: BridgeExportState,
    pending_export_format: Option<BridgeExportFormat>,
    last_export_path: Option<String>,
    last_export_payload: Option<serde_json::Value>,
    last_export_error: Option<String>,
    last_midi_preview: Option<BridgeMidiPreview>,
    midi_preview_scroll_offset: usize,
    host_context: Option<BridgeHostContext>,
}

impl BridgeEditorState {
    pub fn connection(&self) -> BridgeConnectionState {
        self.connection
    }

    pub fn project_title(&self) -> Option<&str> {
        self.project_title.as_deref()
    }

    pub fn project_revision(&self) -> Option<&str> {
        self.project_revision.as_deref()
    }

    pub fn last_error(&self) -> Option<&str> {
        self.last_error.as_deref()
    }

    /// Legacy dashboard base URL retained for backward compatibility.
    ///
    /// Prefer [`Self::daw_agent_surface_url`] for the Open ATRI action.
    #[deprecated(
        note = "Open ATRI now uses daw_agent_surface_url(); this returns the legacy dashboard base URL"
    )]
    pub fn open_atri_url(&self) -> &str {
        &self.open_atri_url
    }

    /// URL opened by the Open ATRI editor action.
    pub fn daw_agent_surface_url(&self, instance_id: &str) -> String {
        let mut params = DawAgentSurfaceParams::from_project(
            self.project_title(),
            self.project_revision(),
            instance_id,
        );
        if let Some(host_name) = crate::host_context::latest_host_application_name() {
            params = params.with_host_name(host_name);
        }
        self.dashboard_endpoint.daw_agent_surface_url(&params)
    }

    pub fn export_state(&self) -> BridgeExportState {
        self.export_state
    }

    pub fn pending_export_format(&self) -> Option<BridgeExportFormat> {
        self.pending_export_format
    }

    pub fn last_export_path(&self) -> Option<&str> {
        self.last_export_path.as_deref()
    }

    pub fn last_export_payload(&self) -> Option<&serde_json::Value> {
        self.last_export_payload.as_ref()
    }

    pub fn last_export_error(&self) -> Option<&str> {
        self.last_export_error.as_deref()
    }

    pub fn last_midi_preview(&self) -> Option<&BridgeMidiPreview> {
        self.last_midi_preview.as_ref()
    }

    pub fn scroll_midi_preview(&mut self, rows: i32) -> bool {
        let Some(preview) = self.last_midi_preview.as_ref() else {
            return false;
        };
        let track_count = preview.display_tracks().len();
        let max_offset = track_count.saturating_sub(MIDI_PREVIEW_VISIBLE_ROWS);
        let next = if rows < 0 {
            self.midi_preview_scroll_offset
                .saturating_sub(rows.unsigned_abs() as usize)
        } else {
            self.midi_preview_scroll_offset
                .saturating_add(rows as usize)
        }
        .min(max_offset);
        if next == self.midi_preview_scroll_offset {
            return false;
        }
        self.midi_preview_scroll_offset = next;
        true
    }

    pub fn host_context(&self) -> Option<BridgeHostContext> {
        self.host_context.clone()
    }

    pub fn apply_host_context(&mut self, host_context: BridgeHostContext) {
        self.host_context = Some(host_context);
    }

    pub fn mark_connecting(&mut self) {
        self.connection = BridgeConnectionState::Connecting;
        self.last_error = None;
    }

    pub fn apply_status(&mut self, status: BridgeStatus) {
        if status.ok {
            self.connection = BridgeConnectionState::Connected;
            self.project_title = Some(status.project.title);
            self.project_revision = Some(status.project.revision);
            self.last_error = None;
        } else {
            self.mark_error("ATRI dashboard returned an unavailable bridge status");
        }
    }

    pub fn mark_disconnected(&mut self) {
        self.connection = BridgeConnectionState::Disconnected;
        self.project_title = None;
        self.project_revision = None;
    }

    pub fn mark_error(&mut self, message: impl Into<String>) {
        self.connection = BridgeConnectionState::Error;
        self.last_error = Some(message.into());
    }

    pub fn handle_action(&mut self, action: BridgeEditorAction) -> Option<BridgeExportRequest> {
        let format = action.export_format()?;
        self.begin_export(format);
        let request = match self.host_context.as_ref() {
            Some(host_context) => {
                BridgeExportRequest::new(format).with_host_context(host_context.clone())
            }
            None => BridgeExportRequest::new(format),
        };
        Some(request)
    }

    pub fn begin_export(&mut self, format: BridgeExportFormat) {
        self.export_state = BridgeExportState::InProgress;
        self.pending_export_format = Some(format);
        self.last_export_error = None;
    }

    pub fn apply_export_response(&mut self, response: BridgeExportResponse) {
        if response.ok {
            let export_payload = response.export.clone();
            self.export_state = BridgeExportState::Completed;
            self.last_export_path = response.export_path().map(ToOwned::to_owned);
            self.last_export_payload = export_payload;
            self.last_midi_preview = response.midi_preview();
            self.midi_preview_scroll_offset = 0;
            self.last_export_error = None;
            self.pending_export_format = None;
            return;
        }

        self.mark_export_error("ATRI dashboard returned a failed export response");
    }

    pub fn apply_external_export_response(&mut self, response: BridgeExportResponse) -> bool {
        if !response.ok {
            return false;
        }
        let Some(path) = response.export_path().map(ToOwned::to_owned) else {
            return false;
        };
        if self.last_export_path.as_deref() == Some(path.as_str()) {
            return false;
        }

        self.export_state = BridgeExportState::Completed;
        self.last_export_path = Some(path);
        self.last_export_payload = response.export.clone();
        self.last_midi_preview = response.midi_preview();
        self.midi_preview_scroll_offset = 0;
        self.last_export_error = None;
        self.pending_export_format = None;
        true
    }

    pub fn mark_export_error(&mut self, message: impl Into<String>) {
        self.export_state = BridgeExportState::Error;
        self.last_export_error = Some(message.into());
        self.last_export_payload = None;
        self.last_midi_preview = None;
        self.midi_preview_scroll_offset = 0;
        self.pending_export_format = None;
    }

    pub fn apply_export_error(&mut self, error: DashboardClientError) {
        self.mark_export_error(error.user_message());
    }
}

impl Default for BridgeEditorState {
    fn default() -> Self {
        let dashboard_endpoint = DashboardEndpoint::default();
        let open_atri_url = dashboard_endpoint.base_url().to_string();
        Self {
            connection: BridgeConnectionState::Disconnected,
            project_title: None,
            project_revision: None,
            last_error: None,
            dashboard_endpoint,
            open_atri_url,
            export_state: BridgeExportState::Idle,
            pending_export_format: None,
            last_export_path: None,
            last_export_payload: None,
            last_export_error: None,
            last_midi_preview: None,
            midi_preview_scroll_offset: 0,
            host_context: None,
        }
    }
}

impl From<DashboardEndpoint> for BridgeEditorState {
    fn from(endpoint: DashboardEndpoint) -> Self {
        Self {
            open_atri_url: endpoint.base_url().to_string(),
            dashboard_endpoint: endpoint,
            ..Self::default()
        }
    }
}

/// Legacy default dashboard base URL retained for backward compatibility.
#[deprecated(
    note = "Open ATRI now uses daw_agent_surface_url(); this returns the legacy dashboard base URL"
)]
pub fn default_open_atri_url() -> &'static str {
    DEFAULT_DASHBOARD_BASE_URL
}

fn render_state_lines(state: &BridgeEditorState) -> Vec<String> {
    let mut lines = vec!["ATRI Bridge".to_string()];
    lines.push(match state.connection() {
        BridgeConnectionState::Connected => format!(
            "Connected: {} rev {}",
            state.project_title().unwrap_or("Untitled"),
            state.project_revision().unwrap_or("0")
        ),
        BridgeConnectionState::Connecting => "Connecting to ATRI dashboard".to_string(),
        BridgeConnectionState::Disconnected => "Disconnected".to_string(),
        BridgeConnectionState::Error => {
            format!(
                "Dashboard error: {}",
                state.last_error().unwrap_or("unknown")
            )
        }
    });

    lines.push(match state.export_state() {
        BridgeExportState::Idle => "Export: idle".to_string(),
        BridgeExportState::InProgress => format!(
            "Exporting {}",
            state
                .pending_export_format()
                .map(format_name)
                .unwrap_or("project")
        ),
        BridgeExportState::Completed => format!(
            "Last export: {}",
            state.last_export_path().unwrap_or("path unavailable")
        ),
        BridgeExportState::Error => format!(
            "Export error: {}",
            state.last_export_error().unwrap_or("unknown")
        ),
    });

    if state.last_midi_preview().is_some() {
        lines.push("Drag MIDI preview into DAW".to_string());
    }

    if let Some(host_context) = state.host_context() {
        lines.push(render_host_context_line(&host_context));
    }
    lines
}

fn render_host_context_line(host_context: &BridgeHostContext) -> String {
    let tempo = host_context
        .tempo_bpm
        .map(|tempo| format!("{tempo:.1} BPM"))
        .unwrap_or_else(|| "tempo unknown".to_string());
    let meter = host_context
        .time_signature
        .map(|[numerator, denominator]| format!("{numerator}/{denominator}"))
        .unwrap_or_else(|| "meter unknown".to_string());
    let transport = match host_context.is_playing {
        Some(true) => "playing",
        Some(false) => "stopped",
        None => "transport unknown",
    };
    let sample_rate = host_context
        .sample_rate
        .map(|sample_rate| format!(" @ {sample_rate:.0} Hz"))
        .unwrap_or_default();

    format!("Host: {tempo} {meter} {transport}{sample_rate}")
}

fn preview_from_state(state: &BridgeEditorState, width: i32) -> Option<BridgeEditorPreview> {
    let preview = state.last_midi_preview()?;
    let [start, end] = preview.beat_range;
    let [pitch_low, pitch_high] = preview.pitch_range;
    let tracks = preview.display_tracks();
    if tracks.is_empty() {
        return None;
    }
    let max_offset = tracks.len().saturating_sub(MIDI_PREVIEW_VISIBLE_ROWS);
    let offset = state.midi_preview_scroll_offset.min(max_offset);
    let track_rows = tracks
        .iter()
        .skip(offset)
        .take(MIDI_PREVIEW_VISIBLE_ROWS)
        .map(|track| {
            let [track_low, track_high] = track.pitch_range;
            BridgeEditorPreviewTrackRow {
                title: track.track_name.clone(),
                detail: format!(
                    "{} notes | {}-{}",
                    track.note_count,
                    midi_note_name(track_low),
                    midi_note_name(track_high)
                ),
                pitch_low: track_low,
                pitch_high: track_high,
            }
        })
        .collect();

    Some(BridgeEditorPreview {
        title: preview.track_name.clone(),
        detail: format!(
            "{start:.2}-{end:.2} beat | {} | {} notes | {}-{}",
            track_count_label(tracks.len()),
            preview.note_count,
            midi_note_name(pitch_low),
            midi_note_name(pitch_high)
        ),
        x: 24,
        y: 68,
        width: width.saturating_sub(48).max(120),
        height: 44,
        pitch_low,
        pitch_high,
        can_scroll_up: offset > 0,
        can_scroll_down: offset < max_offset,
        track_rows,
    })
}

fn track_count_label(count: usize) -> String {
    if count == 1 {
        "1 track".to_string()
    } else {
        format!("{count} tracks")
    }
}

fn midi_note_name(pitch: i32) -> String {
    const NAMES: [&str; 12] = [
        "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B",
    ];
    let clamped = pitch.clamp(0, 127);
    let octave = clamped / 12 - 1;
    format!("{}{}", NAMES[(clamped % 12) as usize], octave)
}

fn layout_buttons(_width: i32) -> Vec<BridgeEditorButton> {
    vec![
        BridgeEditorButton {
            label: "Open ATRI",
            action: BridgeEditorAction::OpenAtri,
            x: 24,
            y: 124,
            width: 72,
            height: 32,
        },
        BridgeEditorButton {
            label: "MIDI",
            action: BridgeEditorAction::ExportMidi,
            x: 108,
            y: 124,
            width: 56,
            height: 32,
        },
        BridgeEditorButton {
            label: "DAWproject",
            action: BridgeEditorAction::ExportDawproject,
            x: 176,
            y: 124,
            width: 104,
            height: 32,
        },
        BridgeEditorButton {
            label: "WAV",
            action: BridgeEditorAction::ExportMixdownWav,
            x: 292,
            y: 124,
            width: 52,
            height: 32,
        },
        BridgeEditorButton {
            label: "Stems",
            action: BridgeEditorAction::ExportStems,
            x: 356,
            y: 124,
            width: 52,
            height: 32,
        },
    ]
}

fn format_name(format: BridgeExportFormat) -> &'static str {
    match format {
        BridgeExportFormat::Midi => "midi",
        BridgeExportFormat::Dawproject => "dawproject",
        BridgeExportFormat::Wav => "wav",
        BridgeExportFormat::Flac => "flac",
        BridgeExportFormat::Mp3 => "mp3",
        BridgeExportFormat::Stems => "stems",
    }
}
