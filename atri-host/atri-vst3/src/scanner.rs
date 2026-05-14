use std::path::{Path, PathBuf};

/// Information about a discovered VST3 plugin.
#[derive(Debug, Clone, PartialEq)]
pub struct PluginInfo {
    pub name: String,
    pub path: PathBuf,
    /// For bundle format: the platform-specific DLL inside Contents/.
    /// For single-file format: same as `path`.
    pub dll_path: PathBuf,
    pub vendor: String,
    pub category: String,
    pub version: String,
    /// Whether this is a single-file .vst3 (true) or a bundle directory (false).
    pub is_single_file: bool,
}

/// Scans directories for VST3 plugins.
pub struct PluginScanner {
    search_paths: Vec<PathBuf>,
}

impl PluginScanner {
    pub fn new() -> Self {
        let mut search_paths = Vec::new();
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

    /// All known search paths (standard dirs + any user-added paths).
    pub fn search_paths(&self) -> &[PathBuf] {
        &self.search_paths
    }

    pub fn scan(&self) -> Vec<PluginInfo> {
        let mut plugins = Vec::new();

        for base in &self.search_paths {
            if !base.exists() {
                continue;
            }
            if let Ok(entries) = std::fs::read_dir(base) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if path.extension().map_or(false, |ext| ext == "vst3") {
                        if path.is_dir() {
                            // VST3 bundle directory format
                            if let Some(info) = Self::parse_bundle_dir(&path) {
                                plugins.push(info);
                            }
                        } else if path.is_file() {
                            // VST3 single-file format (DLL renamed to .vst3)
                            if let Some(info) = Self::parse_single_file(&path) {
                                plugins.push(info);
                            }
                        }
                    } else if path.is_dir() {
                        // Recurse into vendor subdirectories (e.g. VST3/VSL/Plugin.vst3)
                        Self::scan_dir(&path, &mut plugins);
                    }
                }
            }
        }

        plugins
    }

    /// Recurse into a vendor directory to find .vst3 bundles/files.
    fn scan_dir(dir: &Path, plugins: &mut Vec<PluginInfo>) {
        if let Ok(entries) = std::fs::read_dir(dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.extension().map_or(false, |ext| ext == "vst3") {
                    if path.is_dir() {
                        if let Some(info) = Self::parse_bundle_dir(&path) {
                            plugins.push(info);
                        }
                    } else if path.is_file() {
                        if let Some(info) = Self::parse_single_file(&path) {
                            plugins.push(info);
                        }
                    }
                } else if path.is_dir() {
                    Self::scan_dir(&path, plugins);
                }
            }
        }
    }

    /// Parse a VST3 bundle directory (e.g. Plugin.vst3/).
    fn parse_bundle_dir(bundle_path: &Path) -> Option<PluginInfo> {
        let arch_dir = if cfg!(target_arch = "x86_64") {
            "x86_64-win"
        } else {
            "x86-win"
        };
        let stem = bundle_path.file_stem()?.to_string_lossy().to_string();
        let dll_path = bundle_path
            .join("Contents")
            .join(arch_dir)
            .join(&format!("{}.vst3", stem));

        let module_info = bundle_path.join("moduleinfo.json");
        let (name, vendor, category, version) = if module_info.exists() {
            Self::parse_moduleinfo(&module_info)
        } else {
            (stem, String::new(), String::new(), String::new())
        };

        Some(PluginInfo {
            name,
            path: bundle_path.to_path_buf(),
            dll_path,
            vendor,
            category,
            version,
            is_single_file: false,
        })
    }

    /// Parse a single-file VST3 plugin (.vst3 DLL directly).
    fn parse_single_file(file_path: &Path) -> Option<PluginInfo> {
        let name = file_path
            .file_stem()?
            .to_string_lossy()
            .to_string();

        Some(PluginInfo {
            name,
            path: file_path.to_path_buf(),
            dll_path: file_path.to_path_buf(),
            vendor: String::new(),
            category: String::new(),
            version: String::new(),
            is_single_file: true,
        })
    }

    fn parse_moduleinfo(path: &Path) -> (String, String, String, String) {
        match std::fs::read_to_string(path) {
            Ok(content) => {
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

impl Default for PluginScanner {
    fn default() -> Self {
        Self::new()
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::io::Write;

    // ── integration: scan real system VST3 directory ────────────────

    #[test]
    fn scan_system_vst3_directory() {
        let system_path = PathBuf::from("C:/Program Files/Common Files/VST3");
        if !system_path.exists() {
            eprintln!("Skipping: system VST3 path not found");
            return;
        }

        let scanner = PluginScanner::new();
        let plugins = scanner.scan();

        eprintln!("Found {} plugins in system VST3 paths:", plugins.len());
        for p in &plugins {
            eprintln!(
                "  - {} (vendor: {}, single_file: {}, dll: {})",
                p.name, p.vendor, p.is_single_file, p.dll_path.display()
            );
        }

        // The system path should be in search_paths
        let paths: Vec<_> = scanner
            .search_paths()
            .iter()
            .map(|p| p.to_string_lossy().to_lowercase())
            .collect();
        let has_vst3_path = paths.iter().any(|p| p.contains("common files") && p.contains("vst3"));
        assert!(has_vst3_path, "Scanner should include Common Files VST3 path");

        // VSL Vienna Synchron Player is installed — verify it's found
        let vsl = plugins.iter().find(|p| p.name.contains("Vienna"));
        if let Some(vsl_plugin) = vsl {
            assert!(
                vsl_plugin.is_single_file,
                "VSL plugin should be single-file format"
            );
            assert!(
                vsl_plugin.dll_path.exists(),
                "VSL dll_path should exist: {}",
                vsl_plugin.dll_path.display()
            );
            assert_eq!(vsl_plugin.path, vsl_plugin.dll_path,
                "For single-file format, path should equal dll_path");
        } else {
            eprintln!("Note: Vienna Synchron Player not found (may be uninstalled)");
        }
    }

    // ── unit: single-file VST3 parsing ─────────────────────────────

    #[test]
    fn parse_single_file_vst3() {
        // We test parse_single_file directly with a known file
        let path = PathBuf::from(
            "C:/Program Files/Common Files/VST3/VSL/Vienna Synchron Player.vst3",
        );
        if !path.exists() {
            eprintln!("Skipping: VSL plugin not found at expected path");
            return;
        }

        let info = PluginScanner::parse_single_file(&path).expect("Should parse single-file VST3");
        assert_eq!(info.name, "Vienna Synchron Player");
        assert!(info.is_single_file);
        assert_eq!(info.path, info.dll_path);
        assert!(info.vendor.is_empty(), "Single-file VST3 has no vendor from moduleinfo");
    }

    // ── unit: bundle directory VST3 parsing (with temp dir) ────────

    #[test]
    fn parse_bundle_dir_vst3() {
        let tmp = std::env::temp_dir().join("atri-vst3-test-bundle");
        let _ = fs::remove_dir_all(&tmp);

        // Create a fake bundle: TestPlugin.vst3/Contents/x86_64-win/TestPlugin.vst3
        let bundle = tmp.join("TestPlugin.vst3");
        let contents = bundle.join("Contents").join("x86_64-win");
        fs::create_dir_all(&contents).unwrap();

        // Create the inner DLL file
        let dll = contents.join("TestPlugin.vst3");
        fs::write(&dll, b"fake dll").unwrap();

        // Create moduleinfo.json
        let meta = bundle.join("moduleinfo.json");
        let mut f = fs::File::create(&meta).unwrap();
        writeln!(f, r#"{{"name": "TestPlugin", "vendor": "ATRI Labs", "category": "Synth", "version": "1.0.0"}}"#).unwrap();

        let info = PluginScanner::parse_bundle_dir(&bundle).expect("Should parse bundle dir");
        assert_eq!(info.name, "TestPlugin");
        assert_eq!(info.vendor, "ATRI Labs");
        assert_eq!(info.category, "Synth");
        assert_eq!(info.version, "1.0.0");
        assert!(!info.is_single_file);
        assert_eq!(info.path, bundle);
        assert_eq!(info.dll_path, dll);
        assert!(info.dll_path.exists());

        let _ = fs::remove_dir_all(&tmp);
    }

    // ── unit: bundle without moduleinfo.json ───────────────────────

    #[test]
    fn parse_bundle_dir_no_moduleinfo() {
        let tmp = std::env::temp_dir().join("atri-vst3-test-no-meta");
        let _ = fs::remove_dir_all(&tmp);

        let bundle = tmp.join("NoMetaPlugin.vst3");
        let contents = bundle.join("Contents").join("x86_64-win");
        fs::create_dir_all(&contents).unwrap();
        fs::write(contents.join("NoMetaPlugin.vst3"), b"fake dll").unwrap();
        // No moduleinfo.json

        let info = PluginScanner::parse_bundle_dir(&bundle).expect("Should parse without moduleinfo");
        assert_eq!(info.name, "NoMetaPlugin");
        assert!(info.vendor.is_empty());
        assert!(!info.is_single_file);

        let _ = fs::remove_dir_all(&tmp);
    }

    // ── unit: scan temp directory with mixed formats ───────────────

    #[test]
    fn scan_mixed_formats() {
        let tmp = std::env::temp_dir().join("atri-vst3-scan-test");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(&tmp).unwrap();

        // Create a single-file .vst3 in the root
        let single = tmp.join("SinglePlugin.vst3");
        fs::write(&single, b"fake single-file dll").unwrap();

        // Create a vendor subdirectory with a bundle .vst3
        let vendor_dir = tmp.join("FakeVendor");
        fs::create_dir_all(&vendor_dir).unwrap();
        let bundle = vendor_dir.join("BundlePlugin.vst3");
        let contents = bundle.join("Contents").join("x86_64-win");
        fs::create_dir_all(&contents).unwrap();
        fs::write(contents.join("BundlePlugin.vst3"), b"fake bundle dll").unwrap();

        let scanner = PluginScanner::new().with_path(tmp.clone());
        let plugins = scanner.scan();

        eprintln!("Scanned {} plugins from temp dir:", plugins.len());
        for p in &plugins {
            eprintln!("  - {} (single_file: {})", p.name, p.is_single_file);
        }

        let single_info = plugins.iter().find(|p| p.name == "SinglePlugin");
        assert!(single_info.is_some(), "Should find SinglePlugin");
        assert!(single_info.unwrap().is_single_file);

        let bundle_info = plugins.iter().find(|p| p.name == "BundlePlugin");
        assert!(bundle_info.is_some(), "Should find BundlePlugin in vendor subdir");
        assert!(!bundle_info.unwrap().is_single_file);

        let _ = fs::remove_dir_all(&tmp);
    }

    // ── unit: scanner with non-existent path ───────────────────────

    #[test]
    fn scan_nonexistent_path_does_not_panic() {
        let scanner = PluginScanner::new()
            .with_path(PathBuf::from("Z:/nonexistent/vst3/path"));
        let plugins = scanner.scan();
        // Should not panic. May have plugins from standard paths, but
        // none from the nonexistent path.
        let from_nonexistent = plugins.iter().any(|p| {
            p.path.to_string_lossy().contains("nonexistent")
        });
        assert!(!from_nonexistent, "No plugins from nonexistent path");
    }

    // ── unit: extract_json_str ─────────────────────────────────────

    #[test]
    fn test_extract_json_str() {
        let json = r#"{"name": "MyPlugin", "vendor": "ACME", "version": "2.0"}"#;
        assert_eq!(extract_json_str(json, "name"), "MyPlugin");
        assert_eq!(extract_json_str(json, "vendor"), "ACME");
        assert_eq!(extract_json_str(json, "version"), "2.0");
        assert_eq!(extract_json_str(json, "category"), "");
        assert_eq!(extract_json_str("", "name"), "");
    }

    // ── unit: PluginScanner::new includes standard paths (Windows) ─

    #[test]
    #[cfg(target_os = "windows")]
    fn scanner_new_has_standard_windows_paths() {
        let scanner = PluginScanner::new();
        let paths: Vec<String> = scanner
            .search_paths()
            .iter()
            .map(|p| p.to_string_lossy().to_string())
            .collect();
        // At least one path should mention VST3
        let has_vst3 = paths.iter().any(|s| s.contains("VST3"));
        assert!(has_vst3, "Standard paths should include VST3: {:?}", paths);
    }
}
