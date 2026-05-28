use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use crate::bridge_contract::{BridgeExportRequest, BridgeExportResponse, BridgeStatus};
use crate::daw_agent_surface::{
    DawAgentSurfaceParams, daw_agent_surface_url, percent_encode_query,
};
use serde::de::DeserializeOwned;
use thiserror::Error;

pub const DEFAULT_DASHBOARD_BASE_URL: &str = "http://127.0.0.1:6185";
const BRIDGE_STATUS_PATH: &str = "/api/music/studio/bridge/status";
const BRIDGE_EXPORT_PATH: &str = "/api/music/studio/bridge/export";
const BRIDGE_LATEST_EXPORT_PATH: &str = "/api/music/studio/bridge/export/latest";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DashboardEndpoint {
    base_url: String,
}

impl DashboardEndpoint {
    pub fn new(base_url: impl Into<String>) -> Result<Self, DashboardEndpointError> {
        let base_url = base_url.into().trim().trim_end_matches('/').to_string();
        if base_url.is_empty() {
            return Err(DashboardEndpointError::EmptyBaseUrl);
        }
        if !is_local_http_url(&base_url) {
            return Err(DashboardEndpointError::NonLocalBaseUrl(base_url));
        }
        Ok(Self { base_url })
    }

    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    pub fn bridge_status_url(&self) -> String {
        format!("{}{}", self.base_url, BRIDGE_STATUS_PATH)
    }

    pub fn bridge_export_url(&self) -> String {
        format!("{}{}", self.base_url, BRIDGE_EXPORT_PATH)
    }

    pub fn bridge_latest_export_url(&self, instance_id: Option<&str>) -> String {
        let base = format!("{}{}", self.base_url, BRIDGE_LATEST_EXPORT_PATH);
        let Some(instance_id) = instance_id.map(str::trim).filter(|value| !value.is_empty()) else {
            return base;
        };
        format!("{base}?instance_id={}", percent_encode_query(instance_id))
    }

    pub fn daw_agent_surface_url(&self, params: &DawAgentSurfaceParams) -> String {
        daw_agent_surface_url(&self.base_url, params)
    }
}

impl Default for DashboardEndpoint {
    fn default() -> Self {
        Self {
            base_url: DEFAULT_DASHBOARD_BASE_URL.to_string(),
        }
    }
}

#[derive(Debug, Error, PartialEq, Eq)]
pub enum DashboardEndpointError {
    #[error("dashboard base URL cannot be empty")]
    EmptyBaseUrl,
    #[error("dashboard bridge endpoint must stay local: {0}")]
    NonLocalBaseUrl(String),
}

#[derive(Debug, Error)]
pub enum DashboardClientError {
    #[error("invalid dashboard URL: {0}")]
    InvalidUrl(String),
    #[error("dashboard IO failed: {0}")]
    Io(String),
    #[error("dashboard returned HTTP status {status}")]
    HttpStatus {
        status: u16,
        message: Option<String>,
    },
    #[error("dashboard response did not contain an HTTP body")]
    MissingBody,
    #[error("dashboard response JSON was invalid: {0}")]
    Json(String),
}

impl DashboardClientError {
    pub fn user_message(&self) -> String {
        match self {
            Self::InvalidUrl(message) | Self::Io(message) | Self::Json(message) => message.clone(),
            Self::HttpStatus { status, message } => message
                .clone()
                .unwrap_or_else(|| format!("dashboard returned HTTP status {status}")),
            Self::MissingBody => "dashboard response did not contain an HTTP body".to_string(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DashboardRequest {
    pub method: &'static str,
    pub url: String,
    pub body: Option<String>,
}

#[derive(Debug, Clone)]
pub struct BridgeDashboardClient {
    endpoint: DashboardEndpoint,
}

impl BridgeDashboardClient {
    pub fn new(endpoint: DashboardEndpoint) -> Self {
        Self { endpoint }
    }

    pub fn endpoint(&self) -> &DashboardEndpoint {
        &self.endpoint
    }

    pub fn status_request(&self) -> DashboardRequest {
        DashboardRequest {
            method: "GET",
            url: self.endpoint.bridge_status_url(),
            body: None,
        }
    }

    pub fn export_request(
        &self,
        request: &BridgeExportRequest,
    ) -> Result<DashboardRequest, DashboardClientError> {
        let body = serde_json::to_string(request)
            .map_err(|err| DashboardClientError::Json(err.to_string()))?;
        Ok(DashboardRequest {
            method: "POST",
            url: self.endpoint.bridge_export_url(),
            body: Some(body),
        })
    }

    pub fn fetch_status(&self, timeout: Duration) -> Result<BridgeStatus, DashboardClientError> {
        let request = self.status_request();
        let response = send_http_request(&request, timeout)?;
        parse_json_response(&response)
    }

    pub fn request_export(
        &self,
        request: BridgeExportRequest,
        timeout: Duration,
    ) -> Result<BridgeExportResponse, DashboardClientError> {
        let request = self.export_request(&request)?;
        let response = send_http_request(&request, timeout)?;
        parse_json_response(&response)
    }

    pub fn fetch_latest_export(
        &self,
        instance_id: Option<&str>,
        timeout: Duration,
    ) -> Result<BridgeExportResponse, DashboardClientError> {
        let request = DashboardRequest {
            method: "GET",
            url: self.endpoint.bridge_latest_export_url(instance_id),
            body: None,
        };
        let response = send_http_request(&request, timeout)?;
        parse_json_response(&response)
    }
}

#[derive(Debug, Clone)]
pub struct DashboardStatusWorker {
    client: BridgeDashboardClient,
    timeout: Duration,
}

impl DashboardStatusWorker {
    pub fn new(client: BridgeDashboardClient, timeout: Duration) -> Self {
        Self { client, timeout }
    }

    pub fn check_once(&self) -> JoinHandle<Result<BridgeStatus, DashboardClientError>> {
        let client = self.client.clone();
        let timeout = self.timeout;
        thread::spawn(move || client.fetch_status(timeout))
    }
}

#[derive(Debug, Clone)]
pub struct DashboardExportWorker {
    client: BridgeDashboardClient,
    timeout: Duration,
}

impl DashboardExportWorker {
    pub fn new(client: BridgeDashboardClient, timeout: Duration) -> Self {
        Self { client, timeout }
    }

    pub fn export_once(
        &self,
        request: BridgeExportRequest,
    ) -> JoinHandle<Result<BridgeExportResponse, DashboardClientError>> {
        let client = self.client.clone();
        let timeout = self.timeout;
        thread::spawn(move || client.request_export(request, timeout))
    }
}

#[derive(Debug, Clone)]
pub struct DashboardLatestExportWorker {
    client: BridgeDashboardClient,
    timeout: Duration,
}

impl DashboardLatestExportWorker {
    pub fn new(client: BridgeDashboardClient, timeout: Duration) -> Self {
        Self { client, timeout }
    }

    pub fn fetch_once(
        &self,
        instance_id: impl Into<String>,
    ) -> JoinHandle<Result<BridgeExportResponse, DashboardClientError>> {
        let client = self.client.clone();
        let timeout = self.timeout;
        let instance_id = instance_id.into();
        thread::spawn(move || client.fetch_latest_export(Some(instance_id.as_str()), timeout))
    }
}

struct HttpTarget {
    authority: String,
    host: String,
    port: u16,
    path: String,
}

impl HttpTarget {
    fn parse(url: &str) -> Result<Self, DashboardClientError> {
        let rest = url
            .strip_prefix("http://")
            .ok_or_else(|| DashboardClientError::InvalidUrl(url.to_string()))?;
        let (authority, path) = rest
            .split_once('/')
            .map(|(authority, path)| (authority, format!("/{path}")))
            .unwrap_or((rest, "/".to_string()));
        let (host, port) = parse_host_port(authority)?;

        Ok(Self {
            authority: authority.to_string(),
            host,
            port,
            path,
        })
    }

    fn socket_addr(&self) -> Result<std::net::SocketAddr, DashboardClientError> {
        format!("{}:{}", self.host, self.port)
            .to_socket_addrs()
            .map_err(|err| DashboardClientError::Io(err.to_string()))?
            .next()
            .ok_or_else(|| DashboardClientError::InvalidUrl(self.authority.clone()))
    }
}

fn parse_host_port(authority: &str) -> Result<(String, u16), DashboardClientError> {
    let (host, port) = authority
        .rsplit_once(':')
        .map(|(host, port)| {
            let port = port
                .parse::<u16>()
                .map_err(|_| DashboardClientError::InvalidUrl(authority.to_string()))?;
            Ok((host.to_string(), port))
        })
        .unwrap_or_else(|| Ok((authority.to_string(), 80)))?;

    if host != "127.0.0.1" && host != "localhost" {
        return Err(DashboardClientError::InvalidUrl(authority.to_string()));
    }
    Ok((host, port))
}

fn send_http_request(
    request: &DashboardRequest,
    timeout: Duration,
) -> Result<String, DashboardClientError> {
    let target = HttpTarget::parse(&request.url)?;
    let mut stream = TcpStream::connect_timeout(&target.socket_addr()?, timeout)
        .map_err(|err| DashboardClientError::Io(err.to_string()))?;
    stream
        .set_read_timeout(Some(timeout))
        .map_err(|err| DashboardClientError::Io(err.to_string()))?;
    stream
        .set_write_timeout(Some(timeout))
        .map_err(|err| DashboardClientError::Io(err.to_string()))?;

    let request_text = match request.body.as_deref() {
        Some(body) => format!(
            "{} {} HTTP/1.1\r\nHost: {}\r\nAccept: application/json\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            request.method,
            target.path,
            target.authority,
            body.len(),
            body
        ),
        None => format!(
            "{} {} HTTP/1.1\r\nHost: {}\r\nAccept: application/json\r\nConnection: close\r\n\r\n",
            request.method, target.path, target.authority
        ),
    };

    stream
        .write_all(request_text.as_bytes())
        .map_err(|err| DashboardClientError::Io(err.to_string()))?;

    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|err| DashboardClientError::Io(err.to_string()))?;
    Ok(response)
}

fn parse_json_response<T>(response: &str) -> Result<T, DashboardClientError>
where
    T: DeserializeOwned,
{
    let (head, body) = response
        .split_once("\r\n\r\n")
        .or_else(|| response.split_once("\n\n"))
        .ok_or(DashboardClientError::MissingBody)?;
    let status = head
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|status| status.parse::<u16>().ok())
        .ok_or_else(|| DashboardClientError::InvalidUrl("missing HTTP status".to_string()))?;
    if status != 200 {
        return Err(DashboardClientError::HttpStatus {
            status,
            message: response_error_message(body),
        });
    }

    serde_json::from_str(body).map_err(|err| DashboardClientError::Json(err.to_string()))
}

fn response_error_message(body: &str) -> Option<String> {
    let value = serde_json::from_str::<serde_json::Value>(body).ok()?;
    value
        .get("error")
        .or_else(|| value.get("message"))
        .and_then(serde_json::Value::as_str)
        .map(ToOwned::to_owned)
}

fn is_local_http_url(url: &str) -> bool {
    url.starts_with("http://127.0.0.1:")
        || url == "http://127.0.0.1"
        || url.starts_with("http://localhost:")
        || url == "http://localhost"
}
