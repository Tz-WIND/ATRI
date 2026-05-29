use serde::{Deserialize, Deserializer, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BridgeContract {
    pub api_version: u32,
    pub manifest_schema_version: u32,
    pub local_only: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BridgeProjectSummary {
    pub title: String,
    #[serde(deserialize_with = "deserialize_project_revision")]
    pub revision: String,
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
                revision: revision.to_string(),
            },
            formats: vec!["midi".to_string(), "dawproject".to_string()],
        }
    }
}

fn deserialize_project_revision<'de, D>(deserializer: D) -> Result<String, D::Error>
where
    D: Deserializer<'de>,
{
    let value = serde_json::Value::deserialize(deserializer)?;
    match value {
        serde_json::Value::String(revision) => Ok(revision),
        serde_json::Value::Number(revision) => Ok(revision.to_string()),
        _ => Err(serde::de::Error::custom(
            "project revision must be a string or number",
        )),
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
    pub instance_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub host_context: Option<BridgeHostContext>,
}

impl BridgeExportRequest {
    pub fn new(format: BridgeExportFormat) -> Self {
        Self {
            format,
            consumer: "bridge".to_string(),
            instance_id: None,
            host_context: None,
        }
    }

    pub fn with_instance_id(mut self, instance_id: impl Into<String>) -> Self {
        let instance_id = instance_id.into();
        let instance_id = instance_id.trim();
        if !instance_id.is_empty() {
            self.instance_id = Some(instance_id.to_string());
        }
        self
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

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BridgeMidiPreview {
    pub kind: String,
    pub track_id: i64,
    pub track_name: String,
    pub beat_range: [f64; 2],
    pub note_count: u64,
    pub pitch_range: [i32; 2],
}

impl BridgeExportResponse {
    pub fn export_path(&self) -> Option<&str> {
        self.export
            .as_ref()
            .and_then(|export| export.get("path"))
            .and_then(serde_json::Value::as_str)
    }

    pub fn midi_preview(&self) -> Option<BridgeMidiPreview> {
        let preview = self
            .export
            .as_ref()
            .and_then(|export| export.get("bridge_preview"))?;
        serde_json::from_value(preview.clone()).ok()
    }
}
