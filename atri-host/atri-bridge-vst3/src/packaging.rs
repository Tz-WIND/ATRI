use std::fs;
use std::path::{Path, PathBuf};

use thiserror::Error;

use crate::identity::{
    COMPONENT_CLASS_ID, CONTROLLER_CLASS_ID, PLUGIN_CATEGORY, PLUGIN_NAME, PLUGIN_VERSION, VENDOR,
    VENDOR_EMAIL, VENDOR_URL,
};

pub const VST3_BUNDLE_NAME: &str = "ATRI Bridge.vst3";
const AUDIO_MODULE_CLASS: &str = "Audio Module Class";
const COMPONENT_CONTROLLER_CLASS: &str = "Component Controller Class";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BuildProfile {
    Debug,
    Release,
}

impl BuildProfile {
    pub fn dir_name(self) -> &'static str {
        match self {
            Self::Debug => "debug",
            Self::Release => "release",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BridgePackageLayout {
    pub bundle_root: PathBuf,
    pub binary_path: PathBuf,
    pub moduleinfo_path: PathBuf,
}

impl BridgePackageLayout {
    pub fn for_current_target() -> Self {
        Self::for_root(PathBuf::new())
    }

    pub fn for_root(root: impl Into<PathBuf>) -> Self {
        let bundle_root = root.into().join(VST3_BUNDLE_NAME);
        let binary_path = bundle_root
            .join("Contents")
            .join(vst3_platform_dir())
            .join(vst3_binary_name());
        let moduleinfo_path = bundle_root
            .join("Contents")
            .join("Resources")
            .join("moduleinfo.json");

        Self {
            bundle_root,
            binary_path,
            moduleinfo_path,
        }
    }

    pub fn materialize_from_binary(
        &self,
        source: impl AsRef<Path>,
    ) -> Result<PathBuf, BridgePackageError> {
        let source = source.as_ref();
        let parent = self
            .binary_path
            .parent()
            .ok_or_else(|| BridgePackageError::InvalidLayout(self.binary_path.clone()))?;
        fs::create_dir_all(parent).map_err(|err| BridgePackageError::Io {
            path: parent.to_path_buf(),
            source: err,
        })?;
        fs::copy(source, &self.binary_path).map_err(|err| BridgePackageError::Io {
            path: self.binary_path.clone(),
            source: err,
        })?;
        self.write_moduleinfo()?;
        Ok(self.binary_path.clone())
    }

    fn write_moduleinfo(&self) -> Result<(), BridgePackageError> {
        let parent = self
            .moduleinfo_path
            .parent()
            .ok_or_else(|| BridgePackageError::InvalidLayout(self.moduleinfo_path.clone()))?;
        fs::create_dir_all(parent).map_err(|err| BridgePackageError::Io {
            path: parent.to_path_buf(),
            source: err,
        })?;
        let body = bridge_moduleinfo_json();
        fs::write(&self.moduleinfo_path, body).map_err(|err| BridgePackageError::Io {
            path: self.moduleinfo_path.clone(),
            source: err,
        })?;
        Ok(())
    }
}

fn bridge_moduleinfo_json() -> String {
    serde_json::json!({
        "Name": PLUGIN_NAME,
        "Version": PLUGIN_VERSION,
        "Vendor": VENDOR,
        "URL": VENDOR_URL,
        "Email": VENDOR_EMAIL,
        "Factory Info": {
            "Unicode": true,
        },
        "Classes": [
            {
                "CID": tuid_hex(&COMPONENT_CLASS_ID),
                "Category": AUDIO_MODULE_CLASS,
                "Name": PLUGIN_NAME,
                "Vendor": VENDOR,
                "Version": PLUGIN_VERSION,
                "SDKVersion": "VST 3",
                "Sub Categories": PLUGIN_CATEGORY,
                "Class Flags": 0,
            },
            {
                "CID": tuid_hex(&CONTROLLER_CLASS_ID),
                "Category": COMPONENT_CONTROLLER_CLASS,
                "Name": PLUGIN_NAME,
                "Vendor": VENDOR,
                "Version": PLUGIN_VERSION,
                "SDKVersion": "VST 3",
                "Sub Categories": "",
                "Class Flags": 0,
            },
        ],
    })
    .to_string()
}

fn tuid_hex(tuid: &vst3::Steinberg::TUID) -> String {
    tuid.iter()
        .map(|unit| format!("{:02X}", *unit as u8))
        .collect()
}

pub fn compiled_cdylib_path(target_dir: impl AsRef<Path>, profile: BuildProfile) -> PathBuf {
    target_dir
        .as_ref()
        .join(profile.dir_name())
        .join(compiled_cdylib_name())
}

pub fn package_from_target_dir(
    target_dir: impl AsRef<Path>,
    profile: BuildProfile,
    output_root: impl Into<PathBuf>,
) -> Result<PathBuf, BridgePackageError> {
    let source = compiled_cdylib_path(target_dir, profile);
    let layout = BridgePackageLayout::for_root(output_root);
    layout.materialize_from_binary(source)?;
    Ok(layout.bundle_root)
}

#[derive(Debug, Error)]
pub enum BridgePackageError {
    #[error("invalid VST3 package layout: {0}")]
    InvalidLayout(PathBuf),
    #[error("failed to write VST3 package path {path}: {source}")]
    Io {
        path: PathBuf,
        source: std::io::Error,
    },
}

#[cfg(target_os = "windows")]
pub fn compiled_cdylib_name() -> &'static str {
    "atri_bridge_vst3.dll"
}

#[cfg(target_os = "linux")]
pub fn compiled_cdylib_name() -> &'static str {
    "libatri_bridge_vst3.so"
}

#[cfg(target_os = "macos")]
pub fn compiled_cdylib_name() -> &'static str {
    "libatri_bridge_vst3.dylib"
}

#[cfg(not(any(target_os = "windows", target_os = "linux", target_os = "macos")))]
pub fn compiled_cdylib_name() -> &'static str {
    "atri_bridge_vst3"
}

#[cfg(target_os = "windows")]
pub fn vst3_binary_name() -> &'static str {
    "ATRI Bridge.vst3"
}

#[cfg(target_os = "linux")]
pub fn vst3_binary_name() -> &'static str {
    "ATRI Bridge.so"
}

#[cfg(target_os = "macos")]
pub fn vst3_binary_name() -> &'static str {
    "ATRI Bridge"
}

#[cfg(not(any(target_os = "windows", target_os = "linux", target_os = "macos")))]
pub fn vst3_binary_name() -> &'static str {
    "ATRI Bridge"
}

#[cfg(target_os = "windows")]
pub fn vst3_platform_dir() -> &'static str {
    if cfg!(target_arch = "x86_64") {
        "x86_64-win"
    } else if cfg!(target_arch = "x86") {
        "x86-win"
    } else if cfg!(target_arch = "aarch64") {
        "arm64-win"
    } else {
        "unknown-win"
    }
}

#[cfg(target_os = "linux")]
pub fn vst3_platform_dir() -> &'static str {
    if cfg!(target_arch = "x86_64") {
        "x86_64-linux"
    } else if cfg!(target_arch = "x86") {
        "i386-linux"
    } else if cfg!(target_arch = "aarch64") {
        "aarch64-linux"
    } else {
        "unknown-linux"
    }
}

#[cfg(target_os = "macos")]
pub fn vst3_platform_dir() -> &'static str {
    "MacOS"
}

#[cfg(not(any(target_os = "windows", target_os = "linux", target_os = "macos")))]
pub fn vst3_platform_dir() -> &'static str {
    "unknown"
}
