use std::path::Path;

/// Result of loading a VST3 plugin factory from a shared library.
pub struct PluginFactory {
    _library: Option<libloading::Library>,
    pub path: String,
    pub plugin_name: String,
}

impl PluginFactory {
    /// Load a VST3 plugin from the given .vst3 bundle path.
    /// The path should point to the platform-specific shared library inside the bundle.
    pub fn load(path: &Path) -> Result<Self, String> {
        let path_str = path.to_string_lossy().to_string();
        // On Windows, VST3 plugins are DLLs inside .vst3 directories
        if !path.exists() {
            return Err(format!("Plugin not found: {}", path_str));
        }

        let plugin_name = path
            .parent()
            .and_then(|p| p.file_stem())
            .map(|s| s.to_string_lossy().to_string())
            .unwrap_or_else(|| "Unknown".to_string());

        // Try to load the library
        match unsafe { libloading::Library::new(path) } {
            Ok(lib) => Ok(Self {
                _library: Some(lib),
                path: path_str,
                plugin_name,
            }),
            Err(e) => Err(format!("Failed to load plugin {}: {}", path_str, e)),
        }
    }
}
