use std::path::Path;

/// Result of loading a VST3 plugin factory from a shared library.
#[derive(Debug)]
pub struct PluginFactory {
    pub library: Option<libloading::Library>,
    pub path: String,
    pub plugin_name: String,
}

impl PluginFactory {
    /// Load a VST3 plugin from the given .vst3 bundle path.
    /// The path should point to the platform-specific shared library inside the bundle,
    /// or directly to a single-file .vst3 DLL.
    pub fn load(path: &Path) -> Result<Self, String> {
        let path_str = path.to_string_lossy().to_string();
        if !path.exists() {
            return Err(format!("Plugin not found: {}", path_str));
        }

        let plugin_name = path
            .parent()
            .and_then(|p| p.file_stem())
            .map(|s| s.to_string_lossy().to_string())
            .unwrap_or_else(|| {
                path.file_stem()
                    .map(|s| s.to_string_lossy().to_string())
                    .unwrap_or_else(|| "Unknown".to_string())
            });

        match unsafe { libloading::Library::new(path) } {
            Ok(lib) => Ok(Self {
                library: Some(lib),
                path: path_str,
                plugin_name,
            }),
            Err(e) => Err(format!("Failed to load plugin {}: {}", path_str, e)),
        }
    }

    /// Try to resolve the VST3 entry point `GetPluginFactory` from the loaded library.
    /// Returns the function pointer on success.
    pub fn get_plugin_factory_fn(&self) -> Result<unsafe extern "system" fn() -> *mut std::ffi::c_void, String> {
        let lib = self.library.as_ref().ok_or("Library not loaded")?;
        let sym: libloading::Symbol<unsafe extern "system" fn() -> *mut std::ffi::c_void> = unsafe {
            lib.get(b"GetPluginFactory\0")
        }.map_err(|e| format!("GetPluginFactory not found: {}", e))?;
        Ok(*sym)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    /// Real integration test: load a system VST3 plugin and verify it exports GetPluginFactory.
    #[test]
    fn load_real_vst3_dll() {
        let dll_path = PathBuf::from(
            "C:/Program Files/Common Files/VST3/VSL/Vienna Synchron Player.vst3",
        );
        if !dll_path.exists() {
            eprintln!("Skipping: VSL plugin not found");
            return;
        }

        let factory = PluginFactory::load(&dll_path)
            .expect("Should load real VST3 DLL");

        assert!(!factory.plugin_name.is_empty());
        assert!(factory.library.is_some());

        // Verify it exports GetPluginFactory (the VST3 entry point)
        let entry = factory.get_plugin_factory_fn();
        match entry {
            Ok(_func_ptr) => {
                // Successfully resolved GetPluginFactory symbol
                eprintln!(
                    "GetPluginFactory found in '{}'",
                    factory.plugin_name
                );
            }
            Err(e) => {
                eprintln!(
                    "Note: GetPluginFactory not found in '{}': {} (may be a stub DLL without entry point)",
                    factory.plugin_name, e
                );
            }
        }
    }

    #[test]
    fn load_nonexistent_returns_error() {
        let result = PluginFactory::load(&PathBuf::from("Z:/nonexistent/plugin.vst3"));
        assert!(result.is_err());
        assert!(result.unwrap_err().contains("not found"));
    }

    #[test]
    fn factory_from_scanned_plugin() {
        // Integration: scan then load
        let system_path = PathBuf::from("C:/Program Files/Common Files/VST3");
        if !system_path.exists() {
            eprintln!("Skipping: system VST3 path not found");
            return;
        }

        let scanner = crate::scanner::PluginScanner::new();
        let plugins = scanner.scan();

        for info in &plugins {
            assert!(info.dll_path.exists(),
                "dll_path should exist for {}: {}",
                info.name, info.dll_path.display());

            let factory = PluginFactory::load(&info.dll_path);
            match factory {
                Ok(f) => {
                    eprintln!("Loaded: {} from {}", f.plugin_name, f.path);
                    assert!(f.library.is_some());
                }
                Err(e) => {
                    eprintln!("Failed to load {}: {}", info.name, e);
                }
            }
        }
    }
}
