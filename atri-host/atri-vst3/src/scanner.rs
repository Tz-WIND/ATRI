use std::collections::HashSet;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use serde::Serialize;

const MAX_SCAN_DEPTH: usize = 16;
const MAX_SCAN_ENTRIES: usize = 20_000;
const MAX_SCAN_PLUGINS: usize = 4096;
const MAX_SCAN_DURATION: Duration = Duration::from_secs(15);
const MAX_PE_SECTIONS: usize = 96;
const MAX_PE_EXPORT_NAMES: u32 = 4096;
const MAX_PE_SYMBOL_LEN: usize = 256;

/// Information about a discovered VST3 plugin.
#[derive(Debug, Clone, PartialEq, Serialize)]
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

/// Information about a discovered legacy VST2 plugin.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Vst2PluginInfo {
    pub name: String,
    pub path: PathBuf,
    pub vendor: String,
    pub category: String,
    pub version: String,
}

/// Scans directories for VST3 plugins.
pub struct PluginScanner {
    vst3_paths: Vec<PathBuf>,
    vst2_paths: Vec<PathBuf>,
}

struct ScanState {
    visited_dirs: HashSet<PathBuf>,
    entries_seen: usize,
    started_at: Instant,
}

impl ScanState {
    fn new() -> Self {
        Self {
            visited_dirs: HashSet::new(),
            entries_seen: 0,
            started_at: Instant::now(),
        }
    }

    fn enter_dir(&mut self, dir: &Path, depth: usize) -> bool {
        if depth > MAX_SCAN_DEPTH || self.is_exhausted() {
            return false;
        }
        let Ok(canonical) = dir.canonicalize() else {
            return false;
        };
        self.visited_dirs.insert(canonical)
    }

    fn record_entry(&mut self) -> bool {
        if self.is_exhausted() {
            return false;
        }
        self.entries_seen += 1;
        self.entries_seen <= MAX_SCAN_ENTRIES
    }

    fn can_add_plugin(&self, plugin_count: usize) -> bool {
        plugin_count < MAX_SCAN_PLUGINS && !self.is_exhausted()
    }

    fn is_exhausted(&self) -> bool {
        self.entries_seen >= MAX_SCAN_ENTRIES || self.started_at.elapsed() > MAX_SCAN_DURATION
    }
}

impl PluginScanner {
    pub fn new() -> Self {
        let mut vst3_paths = Vec::new();
        let mut vst2_paths = Vec::new();
        if let Ok(common) = std::env::var("COMMONPROGRAMFILES") {
            let common = PathBuf::from(common);
            vst3_paths.push(common.join("VST3"));
            vst2_paths.push(common.join("VST2"));
        }
        if let Ok(program) = std::env::var("PROGRAMFILES") {
            let program = PathBuf::from(program);
            vst3_paths.push(program.join("Common Files").join("VST3"));
            vst2_paths.push(program.join("VSTPlugins"));
            vst2_paths.push(program.join("Steinberg").join("VSTPlugins"));
        }
        if let Ok(program_x86) = std::env::var("PROGRAMFILES(X86)") {
            let program_x86 = PathBuf::from(program_x86);
            vst2_paths.push(program_x86.join("VSTPlugins"));
            vst2_paths.push(program_x86.join("Steinberg").join("VSTPlugins"));
        }
        Self {
            vst3_paths,
            vst2_paths,
        }
    }

    pub fn with_path(mut self, path: impl Into<PathBuf>) -> Self {
        self.vst3_paths.push(path.into());
        self
    }

    pub fn with_paths<I, P>(mut self, paths: I) -> Self
    where
        I: IntoIterator<Item = P>,
        P: Into<PathBuf>,
    {
        self.vst3_paths.extend(paths.into_iter().map(Into::into));
        self
    }

    pub fn with_vst2_path(mut self, path: impl Into<PathBuf>) -> Self {
        self.vst2_paths.push(path.into());
        self
    }

    pub fn with_vst2_paths<I, P>(mut self, paths: I) -> Self
    where
        I: IntoIterator<Item = P>,
        P: Into<PathBuf>,
    {
        self.vst2_paths.extend(paths.into_iter().map(Into::into));
        self
    }

    /// All known VST3 search paths (standard dirs + any user-added paths).
    pub fn search_paths(&self) -> &[PathBuf] {
        &self.vst3_paths
    }

    pub fn vst2_search_paths(&self) -> &[PathBuf] {
        &self.vst2_paths
    }

    pub fn scan(&self) -> Vec<PluginInfo> {
        let mut plugins = Vec::new();
        let mut state = ScanState::new();

        for base in &self.vst3_paths {
            Self::scan_vst3_dir(base, 0, &mut state, &mut plugins);
            if state.is_exhausted() || plugins.len() >= MAX_SCAN_PLUGINS {
                break;
            }
        }

        plugins.sort_by(|a, b| a.path.cmp(&b.path));
        plugins.dedup_by(|a, b| a.path == b.path);
        plugins
    }

    pub fn scan_vst2(&self) -> Vec<Vst2PluginInfo> {
        let mut plugins = Vec::new();
        let mut state = ScanState::new();
        for base in &self.vst2_paths {
            Self::scan_vst2_dir(base, 0, &mut state, &mut plugins);
            if state.is_exhausted() || plugins.len() >= MAX_SCAN_PLUGINS {
                break;
            }
        }
        plugins.sort_by(|a, b| a.path.cmp(&b.path));
        plugins.dedup_by(|a, b| a.path == b.path);
        plugins
    }

    /// Recurse into a vendor directory to find .vst3 bundles/files.
    fn scan_vst3_dir(
        dir: &Path,
        depth: usize,
        state: &mut ScanState,
        plugins: &mut Vec<PluginInfo>,
    ) {
        if !state.enter_dir(dir, depth) {
            return;
        }
        if let Ok(entries) = std::fs::read_dir(dir) {
            for entry in entries.flatten() {
                if !state.record_entry() || !state.can_add_plugin(plugins.len()) {
                    return;
                }
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
                    Self::scan_vst3_dir(&path, depth + 1, state, plugins);
                }
            }
        }
    }

    fn scan_vst2_dir(
        dir: &Path,
        depth: usize,
        state: &mut ScanState,
        plugins: &mut Vec<Vst2PluginInfo>,
    ) {
        if !state.enter_dir(dir, depth) {
            return;
        }

        let Ok(entries) = std::fs::read_dir(dir) else {
            return;
        };

        for entry in entries.flatten() {
            if !state.record_entry() || !state.can_add_plugin(plugins.len()) {
                return;
            }
            let path = entry.path();
            if path.is_dir() {
                Self::scan_vst2_dir(&path, depth + 1, state, plugins);
                continue;
            }

            if path.extension().map_or(false, |ext| {
                ext.to_string_lossy().eq_ignore_ascii_case("dll")
            }) {
                if !is_vst2_dll(&path) {
                    continue;
                }
                if let Some(name) = path
                    .file_stem()
                    .map(|name| name.to_string_lossy().to_string())
                {
                    plugins.push(Vst2PluginInfo {
                        name,
                        path,
                        vendor: String::new(),
                        category: "VST2".to_string(),
                        version: String::new(),
                    });
                }
            }
        }
    }

    /// Parse a VST3 bundle directory (e.g. Plugin.vst3/).
    fn parse_bundle_dir(bundle_path: &Path) -> Option<PluginInfo> {
        let stem = bundle_path.file_stem()?.to_string_lossy().to_string();
        let dll_path = vst3_bundle_library_path(bundle_path)?;

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
        let name = file_path.file_stem()?.to_string_lossy().to_string();

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
                let value = serde_json::from_str::<serde_json::Value>(&content).ok();
                let name = json_str(value.as_ref(), "name")
                    .unwrap_or_else(|| extract_json_str(&content, "name"));
                let vendor = json_str(value.as_ref(), "vendor")
                    .unwrap_or_else(|| extract_json_str(&content, "vendor"));
                let category = json_str(value.as_ref(), "category")
                    .unwrap_or_else(|| extract_json_str(&content, "category"));
                let version = json_str(value.as_ref(), "version")
                    .unwrap_or_else(|| extract_json_str(&content, "version"));
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

fn is_vst2_dll(path: &Path) -> bool {
    let Ok(mut file) = File::open(path) else {
        return false;
    };
    let Ok(metadata) = file.metadata() else {
        return false;
    };
    pe_exports_any_symbol_from_read_seek(&mut file, metadata.len(), &["VSTPluginMain", "main"])
}

#[cfg(test)]
fn pe_exports_any_symbol(bytes: &[u8], symbols: &[&str]) -> bool {
    let mut cursor = std::io::Cursor::new(bytes);
    pe_exports_any_symbol_from_read_seek(&mut cursor, bytes.len() as u64, symbols)
}

fn pe_exports_any_symbol_from_read_seek<R>(reader: &mut R, file_len: u64, symbols: &[&str]) -> bool
where
    R: Read + Seek,
{
    let Some(pe_offset) = read_u32_at(reader, file_len, 0x3c).map(u64::from) else {
        return false;
    };
    if read_exact_at::<2, _>(reader, file_len, 0) != Some(*b"MZ")
        || read_exact_at::<4, _>(reader, file_len, pe_offset) != Some(*b"PE\0\0")
    {
        return false;
    }

    let coff_offset = pe_offset + 4;
    let section_count = read_u16_at(reader, file_len, coff_offset + 2).unwrap_or(0) as usize;
    let optional_header_size =
        read_u16_at(reader, file_len, coff_offset + 16).unwrap_or(0) as usize;
    let optional_offset = coff_offset + 20;
    let optional_magic = read_u16_at(reader, file_len, optional_offset).unwrap_or(0);
    let data_directory_offset = match optional_magic {
        0x10b => optional_offset + 96,
        0x20b => optional_offset + 112,
        _ => return false,
    };

    let export_rva = read_u32_at(reader, file_len, data_directory_offset).unwrap_or(0);
    if export_rva == 0 {
        return false;
    }

    let section_offset = optional_offset + optional_header_size as u64;
    let sections = read_pe_sections(reader, file_len, section_offset, section_count);
    let export_offset = match rva_to_file_offset_from_sections(&sections, export_rva, file_len) {
        Some(offset) => offset,
        None => return false,
    };

    let name_count = read_u32_at(reader, file_len, export_offset + 24).unwrap_or(0);
    let names_rva = read_u32_at(reader, file_len, export_offset + 32).unwrap_or(0);
    let Some(names_offset) = rva_to_file_offset_from_sections(&sections, names_rva, file_len)
    else {
        return false;
    };

    for idx in 0..name_count.min(MAX_PE_EXPORT_NAMES) {
        let name_rva_offset = names_offset + u64::from(idx) * 4;
        let Some(name_rva) = read_u32_at(reader, file_len, name_rva_offset) else {
            break;
        };
        let Some(name_offset) = rva_to_file_offset_from_sections(&sections, name_rva, file_len)
        else {
            continue;
        };
        if let Some(name) = read_c_string_at(reader, file_len, name_offset) {
            if symbols.iter().any(|symbol| *symbol == name.as_str()) {
                return true;
            }
        }
    }

    false
}

#[derive(Debug, Clone, Copy)]
struct PeSection {
    virtual_size: u32,
    virtual_address: u32,
    raw_size: u32,
    raw_offset: u32,
}

fn read_pe_sections<R>(
    reader: &mut R,
    file_len: u64,
    section_offset: u64,
    section_count: usize,
) -> Vec<PeSection>
where
    R: Read + Seek,
{
    let mut sections = Vec::new();
    for section in 0..section_count.min(MAX_PE_SECTIONS) {
        let Some(offset) = section_offset.checked_add((section * 40) as u64) else {
            break;
        };
        let Some(virtual_size) = read_u32_at(reader, file_len, offset + 8) else {
            break;
        };
        let Some(virtual_address) = read_u32_at(reader, file_len, offset + 12) else {
            break;
        };
        let Some(raw_size) = read_u32_at(reader, file_len, offset + 16) else {
            break;
        };
        let Some(raw_offset) = read_u32_at(reader, file_len, offset + 20) else {
            break;
        };
        sections.push(PeSection {
            virtual_size,
            virtual_address,
            raw_size,
            raw_offset,
        });
    }
    sections
}

fn rva_to_file_offset_from_sections(
    sections: &[PeSection],
    rva: u32,
    file_len: u64,
) -> Option<u64> {
    let rva = u64::from(rva);
    for section in sections {
        let virtual_address = u64::from(section.virtual_address);
        let mapped_size = u64::from(section.virtual_size.max(section.raw_size));
        if rva >= virtual_address && rva < virtual_address.saturating_add(mapped_size) {
            return u64::from(section.raw_offset).checked_add(rva - virtual_address);
        }
    }
    (rva < file_len).then_some(rva)
}

fn read_c_string_at<R>(reader: &mut R, file_len: u64, offset: u64) -> Option<String>
where
    R: Read + Seek,
{
    let available = file_len.checked_sub(offset)?;
    let len = available.min(MAX_PE_SYMBOL_LEN as u64) as usize;
    let bytes = read_vec_at(reader, file_len, offset, len)?;
    let end = bytes.iter().position(|byte| *byte == 0)?;
    String::from_utf8(bytes[..end].to_vec()).ok()
}

fn read_u16_at<R>(reader: &mut R, file_len: u64, offset: u64) -> Option<u16>
where
    R: Read + Seek,
{
    read_exact_at::<2, _>(reader, file_len, offset).map(u16::from_le_bytes)
}

fn read_u32_at<R>(reader: &mut R, file_len: u64, offset: u64) -> Option<u32>
where
    R: Read + Seek,
{
    read_exact_at::<4, _>(reader, file_len, offset).map(u32::from_le_bytes)
}

fn read_exact_at<const N: usize, R>(reader: &mut R, file_len: u64, offset: u64) -> Option<[u8; N]>
where
    R: Read + Seek,
{
    if offset.checked_add(N as u64)? > file_len {
        return None;
    }
    let mut data = [0u8; N];
    reader.seek(SeekFrom::Start(offset)).ok()?;
    reader.read_exact(&mut data).ok()?;
    Some(data)
}

fn read_vec_at<R>(reader: &mut R, file_len: u64, offset: u64, len: usize) -> Option<Vec<u8>>
where
    R: Read + Seek,
{
    if offset.checked_add(len as u64)? > file_len {
        return None;
    }
    let mut data = vec![0u8; len];
    reader.seek(SeekFrom::Start(offset)).ok()?;
    reader.read_exact(&mut data).ok()?;
    Some(data)
}

pub fn vst3_bundle_library_path(bundle_path: &Path) -> Option<PathBuf> {
    let stem = bundle_path.file_stem()?.to_string_lossy();
    let contents = bundle_path.join("Contents");

    #[cfg(target_os = "macos")]
    {
        let macos_dir = contents.join("MacOS");
        let named_binary = macos_dir.join(stem.as_ref());
        if named_binary.exists() {
            return Some(named_binary);
        }
        if let Ok(entries) = std::fs::read_dir(&macos_dir) {
            if let Some(entry) = entries.flatten().find(|entry| entry.path().is_file()) {
                return Some(entry.path());
            }
        }
        Some(named_binary)
    }

    #[cfg(any(target_os = "windows", target_os = "linux"))]
    {
        let platform_dir = vst3_platform_dir()?;
        Some(
            contents
                .join(platform_dir)
                .join(vst3_binary_name(stem.as_ref())),
        )
    }

    #[cfg(not(any(target_os = "macos", target_os = "windows", target_os = "linux")))]
    {
        let _ = contents;
        let _ = stem;
        None
    }
}

#[cfg(target_os = "windows")]
fn vst3_binary_name(stem: &str) -> String {
    format!("{stem}.vst3")
}

#[cfg(target_os = "linux")]
fn vst3_binary_name(stem: &str) -> String {
    format!("{stem}.so")
}

#[cfg(target_os = "windows")]
fn vst3_platform_dir() -> Option<&'static str> {
    if cfg!(target_arch = "x86_64") {
        Some("x86_64-win")
    } else if cfg!(target_arch = "x86") {
        Some("x86-win")
    } else if cfg!(target_arch = "aarch64") {
        Some("arm64-win")
    } else if cfg!(target_arch = "arm") {
        Some("arm-win")
    } else {
        None
    }
}

#[cfg(target_os = "linux")]
fn vst3_platform_dir() -> Option<&'static str> {
    if cfg!(target_arch = "x86_64") {
        Some("x86_64-linux")
    } else if cfg!(target_arch = "x86") {
        Some("i386-linux")
    } else if cfg!(target_arch = "aarch64") {
        Some("aarch64-linux")
    } else {
        None
    }
}

fn json_str(value: Option<&serde_json::Value>, key: &str) -> Option<String> {
    value?.get(key)?.as_str().map(|value| value.to_string())
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

    fn real_system_vst_tests_enabled() -> bool {
        std::env::var("ATRI_RUN_SYSTEM_VST_TESTS").as_deref() == Ok("1")
    }

    // ── integration: scan real system VST3 directory ────────────────

    #[test]
    #[ignore = "requires ATRI_RUN_SYSTEM_VST_TESTS=1 and real system VST3 plugins"]
    fn scan_system_vst3_directory() {
        if !real_system_vst_tests_enabled() {
            eprintln!("Skipping: set ATRI_RUN_SYSTEM_VST_TESTS=1 to scan real system VST3 plugins");
            return;
        }

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
                p.name,
                p.vendor,
                p.is_single_file,
                p.dll_path.display()
            );
        }

        // The system path should be in search_paths
        let paths: Vec<_> = scanner
            .search_paths()
            .iter()
            .map(|p| p.to_string_lossy().to_lowercase())
            .collect();
        let has_vst3_path = paths
            .iter()
            .any(|p| p.contains("common files") && p.contains("vst3"));
        assert!(
            has_vst3_path,
            "Scanner should include Common Files VST3 path"
        );

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
            assert_eq!(
                vsl_plugin.path, vsl_plugin.dll_path,
                "For single-file format, path should equal dll_path"
            );
        } else {
            eprintln!("Note: Vienna Synchron Player not found (may be uninstalled)");
        }
    }

    // ── unit: single-file VST3 parsing ─────────────────────────────

    #[test]
    #[ignore = "requires ATRI_RUN_SYSTEM_VST_TESTS=1 and real system VST3 plugins"]
    fn parse_single_file_vst3() {
        if !real_system_vst_tests_enabled() {
            eprintln!(
                "Skipping: set ATRI_RUN_SYSTEM_VST_TESTS=1 to parse real system VST3 plugins"
            );
            return;
        }

        // We test parse_single_file directly with a known file
        let path =
            PathBuf::from("C:/Program Files/Common Files/VST3/VSL/Vienna Synchron Player.vst3");
        if !path.exists() {
            eprintln!("Skipping: VSL plugin not found at expected path");
            return;
        }

        let info = PluginScanner::parse_single_file(&path).expect("Should parse single-file VST3");
        assert_eq!(info.name, "Vienna Synchron Player");
        assert!(info.is_single_file);
        assert_eq!(info.path, info.dll_path);
        assert!(
            info.vendor.is_empty(),
            "Single-file VST3 has no vendor from moduleinfo"
        );
    }

    // ── unit: bundle directory VST3 parsing (with temp dir) ────────

    #[test]
    fn bundle_library_path_uses_target_platform_layout() {
        let path = vst3_bundle_library_path(&PathBuf::from("Example.vst3"))
            .expect("target should have a VST3 layout");
        let path = path.to_string_lossy();

        #[cfg(target_os = "windows")]
        assert!(path.ends_with(r"Example.vst3"));
        #[cfg(target_os = "windows")]
        assert!(path.contains("-win"));

        #[cfg(target_os = "linux")]
        assert!(path.ends_with("Example.so"));
        #[cfg(target_os = "linux")]
        assert!(path.contains("-linux"));

        #[cfg(target_os = "macos")]
        assert!(path.ends_with("MacOS/Example") || path.ends_with(r"MacOS\Example"));
    }

    #[test]
    fn parse_bundle_dir_vst3() {
        let tmp = std::env::temp_dir().join("atri-vst3-test-bundle");
        let _ = fs::remove_dir_all(&tmp);

        // Create a fake bundle using this target's VST3 bundle layout.
        let bundle = tmp.join("TestPlugin.vst3");
        let dll = vst3_bundle_library_path(&bundle).expect("target should have a VST3 layout");
        fs::create_dir_all(dll.parent().unwrap()).unwrap();
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
        let dll = vst3_bundle_library_path(&bundle).expect("target should have a VST3 layout");
        fs::create_dir_all(dll.parent().unwrap()).unwrap();
        fs::write(dll, b"fake dll").unwrap();
        // No moduleinfo.json

        let info =
            PluginScanner::parse_bundle_dir(&bundle).expect("Should parse without moduleinfo");
        assert_eq!(info.name, "NoMetaPlugin");
        assert!(info.vendor.is_empty());
        assert!(!info.is_single_file);

        let _ = fs::remove_dir_all(&tmp);
    }

    // ── unit: scan temp directory with mixed formats ───────────────

    #[test]
    fn builder_accepts_path_iterators_without_validation() {
        let vst3_a = PathBuf::from(r"Z:\missing-vst3-a");
        let vst3_b = PathBuf::from(r"Z:\missing-vst3-b");
        let vst2_a = PathBuf::from(r"Z:\missing-vst2-a");
        let vst2_b = PathBuf::from(r"Z:\missing-vst2-b");

        let scanner = PluginScanner::new()
            .with_paths([vst3_a.clone(), vst3_b.clone()])
            .with_vst2_paths([vst2_a.clone(), vst2_b.clone()]);

        assert!(scanner.search_paths().contains(&vst3_a));
        assert!(scanner.search_paths().contains(&vst3_b));
        assert!(scanner.vst2_search_paths().contains(&vst2_a));
        assert!(scanner.vst2_search_paths().contains(&vst2_b));
    }

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
        let dll = vst3_bundle_library_path(&bundle).expect("target should have a VST3 layout");
        fs::create_dir_all(dll.parent().unwrap()).unwrap();
        fs::write(dll, b"fake bundle dll").unwrap();

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
        assert!(
            bundle_info.is_some(),
            "Should find BundlePlugin in vendor subdir"
        );
        assert!(!bundle_info.unwrap().is_single_file);

        let _ = fs::remove_dir_all(&tmp);
    }

    // ── unit: scanner with non-existent path ───────────────────────

    #[test]
    fn scan_deduplicates_vst3_plugins_by_path() {
        let tmp = std::env::temp_dir().join("atri-vst3-scan-dedup-test");
        let _ = fs::remove_dir_all(&tmp);

        let vendor_dir = tmp.join("FakeVendor");
        fs::create_dir_all(&vendor_dir).unwrap();
        let bundle = vendor_dir.join("AtriDedupPlugin.vst3");
        let dll = vst3_bundle_library_path(&bundle).expect("target should have a VST3 layout");
        fs::create_dir_all(dll.parent().unwrap()).unwrap();
        fs::write(dll, b"fake bundle dll").unwrap();

        let scanner = PluginScanner::new()
            .with_path(tmp.clone())
            .with_path(tmp.clone())
            .with_path(vendor_dir);
        let plugins = scanner.scan();
        let matching_plugins: Vec<_> = plugins
            .iter()
            .filter(|plugin| plugin.path == bundle)
            .collect();

        assert_eq!(matching_plugins.len(), 1);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn scan_deduplicates_vst3_paths_by_canonical_directory() {
        let tmp = std::env::temp_dir().join("atri-vst3-scan-canonical-dedup-test");
        let _ = fs::remove_dir_all(&tmp);

        let vendor_dir = tmp.join("FakeVendor");
        fs::create_dir_all(&vendor_dir).unwrap();
        fs::write(
            vendor_dir.join("CanonicalDedup.vst3"),
            b"fake single-file dll",
        )
        .unwrap();

        let scanner = PluginScanner::new()
            .with_path(tmp.clone())
            .with_path(tmp.join("."));
        let plugins = scanner.scan();
        let matching_plugins: Vec<_> = plugins
            .iter()
            .filter(|plugin| plugin.name == "CanonicalDedup")
            .collect();

        assert_eq!(matching_plugins.len(), 1);

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn scan_respects_max_recursion_depth() {
        let tmp = std::env::temp_dir().join("atri-vst3-scan-depth-limit-test");
        let _ = fs::remove_dir_all(&tmp);

        let mut deep_dir = tmp.clone();
        for depth in 0..=MAX_SCAN_DEPTH {
            deep_dir = deep_dir.join(format!("d{depth}"));
        }
        fs::create_dir_all(&deep_dir).unwrap();
        fs::write(deep_dir.join("TooDeep.vst3"), b"fake single-file dll").unwrap();

        let scanner = PluginScanner::new().with_path(tmp.clone());
        let plugins = scanner.scan();

        assert!(!plugins.iter().any(|plugin| plugin.name == "TooDeep"));

        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn scan_nonexistent_path_does_not_panic() {
        let scanner = PluginScanner::new().with_path(PathBuf::from("Z:/nonexistent/vst3/path"));
        let plugins = scanner.scan();
        // Should not panic. May have plugins from standard paths, but
        // none from the nonexistent path.
        let from_nonexistent = plugins
            .iter()
            .any(|p| p.path.to_string_lossy().contains("nonexistent"));
        assert!(!from_nonexistent, "No plugins from nonexistent path");
    }

    #[test]
    fn scan_vst2_temp_directory() {
        let tmp = std::env::temp_dir().join("atri-vst2-scan-test");
        let _ = fs::remove_dir_all(&tmp);
        fs::create_dir_all(tmp.join("Vendor")).unwrap();
        fs::write(
            tmp.join("Vendor").join("Legacy.dll"),
            fake_pe_with_export("VSTPluginMain"),
        )
        .unwrap();
        fs::write(
            tmp.join("Vendor").join("Utility.dll"),
            fake_pe_with_export("NotAPlugin"),
        )
        .unwrap();
        fs::write(tmp.join("Vendor").join("Broken.dll"), b"not a dll").unwrap();

        let scanner = PluginScanner::new().with_vst2_path(tmp.clone());
        let plugins = scanner.scan_vst2();

        assert!(plugins.iter().any(|plugin| plugin.name == "Legacy"));
        assert!(!plugins.iter().any(|plugin| plugin.name == "Utility"));
        assert!(!plugins.iter().any(|plugin| plugin.name == "Broken"));
        let _ = fs::remove_dir_all(&tmp);
    }

    // ── unit: extract_json_str ─────────────────────────────────────

    #[test]
    fn vst2_export_detection_accepts_main_symbol() {
        assert!(pe_exports_any_symbol(
            &fake_pe_with_export("main"),
            &["VSTPluginMain", "main"]
        ));
    }

    #[test]
    fn vst2_export_detection_uses_bounded_reads() {
        let image = fake_pe_with_export_at("VSTPluginMain", 8 * 1024 * 1024);
        let image_len = image.len() as u64;
        let mut reader = CountingCursor::new(image);

        assert!(pe_exports_any_symbol_from_read_seek(
            &mut reader,
            image_len,
            &["VSTPluginMain", "main"]
        ));
        assert!(
            reader.bytes_read() < 4096,
            "PE export detection read {} bytes",
            reader.bytes_read()
        );
    }

    fn fake_pe_with_export(export_name: &str) -> Vec<u8> {
        fake_pe_with_export_at(export_name, 0x200)
    }

    fn fake_pe_with_export_at(export_name: &str, export_offset: usize) -> Vec<u8> {
        let mut bytes = vec![0u8; 0x400];
        if export_offset + 0x100 > bytes.len() {
            bytes.resize(export_offset + 0x100, 0);
        }
        bytes[0..2].copy_from_slice(b"MZ");
        write_u32_le(&mut bytes, 0x3c, 0x80);

        let pe_offset = 0x80;
        bytes[pe_offset..pe_offset + 4].copy_from_slice(b"PE\0\0");
        let coff_offset = pe_offset + 4;
        write_u16_le(&mut bytes, coff_offset, 0x8664);
        write_u16_le(&mut bytes, coff_offset + 2, 1);
        write_u16_le(&mut bytes, coff_offset + 16, 0xf0);

        let optional_offset = coff_offset + 20;
        write_u16_le(&mut bytes, optional_offset, 0x20b);
        write_u32_le(&mut bytes, optional_offset + 112, 0x1000);
        write_u32_le(&mut bytes, optional_offset + 116, 40);

        let section_offset = optional_offset + 0xf0;
        bytes[section_offset..section_offset + 6].copy_from_slice(b".edata");
        write_u32_le(&mut bytes, section_offset + 8, 0x200);
        write_u32_le(&mut bytes, section_offset + 12, 0x1000);
        write_u32_le(&mut bytes, section_offset + 16, 0x200);
        write_u32_le(&mut bytes, section_offset + 20, export_offset as u32);

        write_u32_le(&mut bytes, export_offset + 12, 0x1070);
        write_u32_le(&mut bytes, export_offset + 16, 1);
        write_u32_le(&mut bytes, export_offset + 20, 1);
        write_u32_le(&mut bytes, export_offset + 24, 1);
        write_u32_le(&mut bytes, export_offset + 28, 0x1040);
        write_u32_le(&mut bytes, export_offset + 32, 0x1050);
        write_u32_le(&mut bytes, export_offset + 36, 0x1060);

        write_u32_le(&mut bytes, export_offset + 0x40, 0x1080);
        write_u32_le(&mut bytes, export_offset + 0x50, 0x1090);
        write_u16_le(&mut bytes, export_offset + 0x60, 0);
        write_c_string(&mut bytes, export_offset + 0x70, "FakePlugin");
        write_c_string(&mut bytes, export_offset + 0x90, export_name);
        bytes
    }

    struct CountingCursor {
        inner: std::io::Cursor<Vec<u8>>,
        bytes_read: usize,
    }

    impl CountingCursor {
        fn new(bytes: Vec<u8>) -> Self {
            Self {
                inner: std::io::Cursor::new(bytes),
                bytes_read: 0,
            }
        }

        fn bytes_read(&self) -> usize {
            self.bytes_read
        }
    }

    impl std::io::Read for CountingCursor {
        fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
            let read = self.inner.read(buf)?;
            self.bytes_read += read;
            Ok(read)
        }
    }

    impl std::io::Seek for CountingCursor {
        fn seek(&mut self, pos: std::io::SeekFrom) -> std::io::Result<u64> {
            self.inner.seek(pos)
        }
    }

    fn write_c_string(bytes: &mut [u8], offset: usize, value: &str) {
        bytes[offset..offset + value.len()].copy_from_slice(value.as_bytes());
        bytes[offset + value.len()] = 0;
    }

    fn write_u16_le(bytes: &mut [u8], offset: usize, value: u16) {
        bytes[offset..offset + 2].copy_from_slice(&value.to_le_bytes());
    }

    fn write_u32_le(bytes: &mut [u8], offset: usize, value: u32) {
        bytes[offset..offset + 4].copy_from_slice(&value.to_le_bytes());
    }

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
