use std::path::PathBuf;

/// Information about a discovered VST3 plugin.
#[derive(Debug, Clone)]
pub struct PluginInfo {
    pub name: String,
    pub path: PathBuf,
    pub vendor: String,
    pub category: String,
    pub version: String,
}

/// Scans directories for VST3 plugins.
pub struct PluginScanner {
    search_paths: Vec<PathBuf>,
}

impl PluginScanner {
    pub fn new() -> Self {
        let mut search_paths = Vec::new();
        // Standard VST3 paths
        if let Ok(common) = std::env::var("COMMONPROGRAMFILES") {
            search_paths.push(PathBuf::from(common).join("VST3"));
        }
        if let Ok(program) = std::env::var("PROGRAMFILES") {
            search_paths.push(PathBuf::from(program).join("Common Files").join("VST3"));
        }
        Self { search_paths }
    }

    pub fn with_path(mut self, path: PathBuf) -> Self {
        self.search_paths.push(path);
        self
    }

    pub fn scan(&self) -> Vec<PluginInfo> {
        let mut plugins = Vec::new();

        for base in &self.search_paths {
            if !base.exists() {
                continue;
            }
            // Walk .vst3 directories
            if let Ok(entries) = std::fs::read_dir(base) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if path.extension().map_or(false, |ext| ext == "vst3") {
                        if let Some(info) = Self::parse_bundle(&path) {
                            plugins.push(info);
                        }
                    }
                }
            }
        }

        plugins
    }

    fn parse_bundle(bundle_path: &PathBuf) -> Option<PluginInfo> {
        // VST3 bundles are directories ending in .vst3
        // containing Contents/x86_64-win/plugin.vst3 on Windows
        let contents = bundle_path.join("Contents");
        let arch_dir = if cfg!(target_arch = "x86_64") {
            "x86_64-win"
        } else {
            "x86-win"
        };
        let _dll_path = contents.join(arch_dir).join(
            bundle_path.file_stem()?.to_string_lossy().to_string() + ".vst3",
        );

        // Parse moduleinfo.json for metadata
        let module_info = bundle_path.join("moduleinfo.json");
        let (name, vendor, category, version) = if module_info.exists() {
            Self::parse_moduleinfo(&module_info)
        } else {
            let name = bundle_path
                .file_stem()
                .map(|s| s.to_string_lossy().to_string())
                .unwrap_or_else(|| "Unknown".to_string());
            (name, String::new(), String::new(), String::new())
        };

        Some(PluginInfo {
            name,
            path: bundle_path.clone(),
            vendor,
            category,
            version,
        })
    }

    fn parse_moduleinfo(path: &std::path::Path) -> (String, String, String, String) {
        match std::fs::read_to_string(path) {
            Ok(content) => {
                // Simple JSON parsing without serde for scanner
                let name = extract_json_str(&content, "name");
                let vendor = extract_json_str(&content, "vendor");
                let category = extract_json_str(&content, "category");
                let version = extract_json_str(&content, "version");
                (name, vendor, category, version)
            }
            Err(_) => (String::new(), String::new(), String::new(), String::new()),
        }
    }
}

fn extract_json_str(json: &str, key: &str) -> String {
    let pattern = format!("\"{}\"", key);
    if let Some(start) = json.find(&pattern) {
        let after_key = &json[start + pattern.len()..];
        if let Some(val_start) = after_key.find('"') {
            let val_part = &after_key[val_start + 1..];
            if let Some(val_end) = val_part.find('"') {
                return val_part[..val_end].to_string();
            }
        }
    }
    String::new()
}

impl Default for PluginScanner {
    fn default() -> Self {
        Self::new()
    }
}
