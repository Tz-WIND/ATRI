use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BridgeContract {
    pub api_version: u32,
    pub manifest_schema_version: u32,
    pub local_only: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BridgeProjectSummary {
    pub title: String,
    pub revision: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BridgeStatus {
    pub ok: bool,
    pub bridge: BridgeContract,
    pub project: BridgeProjectSummary,
    #[serde(default)]
    pub formats: Vec<String>,
}

impl BridgeStatus {
    #[cfg(test)]
    pub fn connected_for_test(title: &str, revision: u64) -> Self {
        Self {
            ok: true,
            bridge: BridgeContract {
                api_version: 1,
                manifest_schema_version: 1,
                local_only: true,
            },
            project: BridgeProjectSummary {
                title: title.to_string(),
                revision,
            },
            formats: vec!["midi".to_string(), "dawproject".to_string()],
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum BridgeExportFormat {
    Midi,
    Dawproject,
    Wav,
    Flac,
    Mp3,
    Stems,
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct BridgeHostContext {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub sample_rate: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub block_size: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub is_playing: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tempo_bpm: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub time_signature: Option<[i32; 2]>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BridgeExportRequest {
    pub format: BridgeExportFormat,
    pub consumer: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub host_context: Option<BridgeHostContext>,
}

impl BridgeExportRequest {
    pub fn new(format: BridgeExportFormat) -> Self {
        Self {
            format,
            consumer: "bridge".to_string(),
            host_context: None,
        }
    }

    pub fn with_host_context(mut self, host_context: BridgeHostContext) -> Self {
        self.host_context = Some(host_context);
        self
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BridgeExportResponse {
    pub ok: bool,
    #[serde(default)]
    pub bridge: Option<BridgeContract>,
    #[serde(default)]
    pub export: Option<serde_json::Value>,
}

impl BridgeExportResponse {
    pub fn export_path(&self) -> Option<&str> {
        self.export
            .as_ref()
            .and_then(|export| export.get("path"))
            .and_then(serde_json::Value::as_str)
    }
}
