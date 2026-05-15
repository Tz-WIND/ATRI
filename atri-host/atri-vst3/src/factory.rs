use std::ffi::{c_char, c_void};
use std::fmt;
use std::mem;
use std::path::Path;
use std::ptr;

#[cfg(target_os = "windows")]
use std::os::windows::ffi::OsStrExt;

use vst3::{
    ComPtr, Interface,
    Steinberg::{
        FIDString, FUnknown, IPluginFactory, IPluginFactory3, IPluginFactory3Trait,
        IPluginFactoryTrait, PClassInfo, TUID, kResultOk,
    },
};

const AUDIO_MODULE_CLASS: &str = "Audio Module Class";
const COMPONENT_CONTROLLER_CLASS: &str = "Component Controller Class";

type GetPluginFactory = unsafe extern "system" fn() -> *mut IPluginFactory;

#[cfg(target_os = "windows")]
type InitDll = unsafe extern "system" fn() -> bool;

#[cfg(target_os = "windows")]
type ExitDll = unsafe extern "system" fn() -> bool;

#[derive(Debug, Clone)]
pub struct PluginClassInfo {
    pub cid: TUID,
    pub category: String,
    pub name: String,
}

/// Loaded VST3 module and its root plugin factory.
///
/// Loading is split into two phases so that `InitDll` and
/// `GetPluginFactory` can run on the same thread as `createView()`
/// (the main/editor thread), keeping QApplication and Qt widgets
/// on the same thread for plugins like Kontakt 8.
pub struct PluginFactory {
    factory: Option<ComPtr<IPluginFactory>>,
    library: libloading::Library,
    get_factory_fn: GetPluginFactory,
    #[cfg(target_os = "windows")]
    init_dll: Option<InitDll>,
    #[cfg(target_os = "windows")]
    exit_dll: Option<ExitDll>,
    pub path: String,
    pub plugin_name: String,
    initialized: bool,
}

impl fmt::Debug for PluginFactory {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("PluginFactory")
            .field("path", &self.path)
            .field("plugin_name", &self.plugin_name)
            .field("loaded", &self.is_loaded())
            .finish_non_exhaustive()
    }
}

/// Windows: add `plugin_dir` to the DLL search path while running `f`,
/// then restore the default search order. This ensures that the plugin's
/// own DLLs (e.g. platform interface modules loaded via boost::dll) can
/// be found by Windows.
#[cfg(target_os = "windows")]
fn with_plugin_dll_dir<T>(plugin_dir: &Path, f: impl FnOnce() -> T) -> T {
    unsafe extern "system" {
        fn SetDllDirectoryW(lpPathName: *const u16) -> i32;
    }

    let wide: Vec<u16> = plugin_dir
        .as_os_str()
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();

    unsafe {
        SetDllDirectoryW(wide.as_ptr());
    }
    let result = f();
    // Restore default DLL search order
    unsafe {
        SetDllDirectoryW(ptr::null());
    }
    result
}

#[cfg(not(target_os = "windows"))]
fn with_plugin_dll_dir<T>(_plugin_dir: &Path, f: impl FnOnce() -> T) -> T {
    f()
}

impl PluginFactory {
    /// Phase 1: load the DLL and resolve entry-point symbols.
    /// Call this from any thread. Does NOT invoke `InitDll` or
    /// `GetPluginFactory` yet — those are deferred to [`initialize`].
    pub fn load(path: &Path) -> Result<Self, String> {
        let path_str = path.to_string_lossy().to_string();
        if !path.exists() {
            return Err(format!("Plugin not found: {}", path_str));
        }

        let plugin_name = path
            .file_stem()
            .map(|s| s.to_string_lossy().to_string())
            .unwrap_or_else(|| "Unknown".to_string());

        let plugin_dir = path.parent().unwrap_or(Path::new("."));

        let library = with_plugin_dll_dir(plugin_dir, || unsafe {
            libloading::Library::new(path)
        })
        .map_err(|err| format!("Failed to load plugin {}: {}", path_str, err))?;

        let get_factory_fn: GetPluginFactory = unsafe {
            *library
                .get(b"GetPluginFactory\0")
                .map_err(|err| format!("GetPluginFactory not found: {err}"))?
        };

        #[cfg(target_os = "windows")]
        let (init_dll, exit_dll) = get_windows_entry_points(&library, &plugin_name)?;

        Ok(Self {
            factory: None,
            library,
            get_factory_fn,
            #[cfg(target_os = "windows")]
            init_dll,
            #[cfg(target_os = "windows")]
            exit_dll,
            path: path_str,
            plugin_name,
            initialized: false,
        })
    }

    /// Phase 2: call `InitDll` (Windows) and `GetPluginFactory`.
    /// Must be called on the main/editor thread — the same thread
    /// that will later call `createView()` — so that Qt-based
    /// plugins (Kontakt 8, etc.) initialize QApplication on the
    /// correct thread.
    pub fn initialize(&mut self) -> Result<(), String> {
        if self.initialized {
            return Ok(());
        }

        #[cfg(target_os = "windows")]
        if let Some(init_dll) = self.init_dll {
            let ok = unsafe { init_dll() };
            if !ok {
                return Err(format!(
                    "VST3 plugin '{}' InitDll returned false",
                    self.plugin_name
                ));
            }
        }

        let factory = unsafe { (self.get_factory_fn)() };
        let factory = unsafe { ComPtr::from_raw(factory) }
            .ok_or_else(|| "GetPluginFactory returned null".to_string())?;

        self.factory = Some(factory);
        self.initialized = true;
        Ok(())
    }

    /// Resolve the VST3 entry point from the loaded module.
    pub fn get_plugin_factory_fn(&self) -> Result<GetPluginFactory, String> {
        let sym: libloading::Symbol<GetPluginFactory> = unsafe {
            self.library
                .get(b"GetPluginFactory\0")
                .map_err(|err| format!("GetPluginFactory not found: {err}"))?
        };
        Ok(*sym)
    }

    pub fn is_loaded(&self) -> bool {
        self.initialized && self.factory.is_some()
    }

    pub fn set_host_context(&self, host_context: *mut FUnknown) {
        let Some(factory) = self
            .factory
            .as_ref()
            .and_then(|factory| factory.cast::<IPluginFactory3>())
        else {
            return;
        };

        let result = unsafe { factory.setHostContext(host_context) };
        if result != kResultOk {
            log::debug!("VST3 factory setHostContext returned {result}");
        }
    }

    pub fn classes(&self) -> Result<Vec<PluginClassInfo>, String> {
        let factory = self.factory()?;
        let count = unsafe { factory.countClasses() };
        if count < 0 {
            return Err(format!(
                "VST3 factory returned invalid class count: {count}"
            ));
        }

        let mut classes = Vec::new();
        for index in 0..count {
            let mut info = unsafe { mem::zeroed::<PClassInfo>() };
            let result = unsafe { factory.getClassInfo(index, &mut info) };
            if result != kResultOk {
                return Err(format!(
                    "VST3 factory getClassInfo({index}) failed with tresult {result}"
                ));
            }
            classes.push(PluginClassInfo {
                cid: info.cid,
                category: fixed_cstr(&info.category),
                name: fixed_cstr(&info.name),
            });
        }
        Ok(classes)
    }

    pub fn first_component_class(&self) -> Result<PluginClassInfo, String> {
        let classes = self.classes()?;
        classes
            .iter()
            .find(|class| class.category == AUDIO_MODULE_CLASS)
            .or_else(|| {
                classes
                    .iter()
                    .find(|class| class.category.contains("Audio"))
            })
            .cloned()
            .ok_or_else(|| {
                format!(
                    "VST3 factory '{}' does not expose an audio component class",
                    self.plugin_name
                )
            })
    }

    pub fn controller_class(&self) -> Result<Option<PluginClassInfo>, String> {
        Ok(self
            .classes()?
            .into_iter()
            .find(|class| class.category == COMPONENT_CONTROLLER_CLASS))
    }

    pub fn create_instance<I: Interface>(
        &self,
        cid: &TUID,
        iid: &TUID,
        label: &str,
    ) -> Result<ComPtr<I>, String> {
        let factory = self.factory()?;
        let mut obj: *mut c_void = ptr::null_mut();
        let result = unsafe {
            factory.createInstance(
                cid.as_ptr() as FIDString,
                iid.as_ptr() as FIDString,
                &mut obj,
            )
        };

        if result != kResultOk {
            return Err(format!(
                "VST3 factory failed to create {label}: tresult {result}"
            ));
        }
        unsafe { ComPtr::from_raw(obj.cast::<I>()) }
            .ok_or_else(|| format!("VST3 factory returned null for {label}"))
    }

    fn factory(&self) -> Result<&ComPtr<IPluginFactory>, String> {
        self.factory
            .as_ref()
            .ok_or_else(|| "VST3 factory is not available".to_string())
    }
}

impl Drop for PluginFactory {
    fn drop(&mut self) {
        self.factory.take();

        #[cfg(target_os = "windows")]
        if let Some(exit_dll) = self.exit_dll {
            unsafe {
                let _ = exit_dll();
            }
        }
    }
}

/// Resolve the optional Windows `InitDll` / `ExitDll` entry points but do
/// NOT call `InitDll` yet — that is deferred to [`PluginFactory::initialize`]
/// so it runs on the correct thread.
#[cfg(target_os = "windows")]
fn get_windows_entry_points(
    library: &libloading::Library,
    _plugin_name: &str,
) -> Result<(Option<InitDll>, Option<ExitDll>), String> {
    let init_dll = unsafe { library.get::<InitDll>(b"InitDll\0") }
        .ok()
        .map(|sym| *sym);
    let exit_dll = unsafe { library.get::<ExitDll>(b"ExitDll\0") }
        .ok()
        .map(|sym| *sym);
    Ok((init_dll, exit_dll))
}

fn fixed_cstr(field: &[c_char]) -> String {
    let len = field.iter().position(|ch| *ch == 0).unwrap_or(field.len());
    let bytes = field[..len].iter().map(|ch| *ch as u8).collect::<Vec<_>>();
    String::from_utf8_lossy(&bytes).trim().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    /// Real integration test: load a system VST3 plugin and verify it exports GetPluginFactory.
    #[test]
    fn load_real_vst3_dll() {
        let dll_path =
            PathBuf::from("C:/Program Files/Common Files/VST3/VSL/Vienna Synchron Player.vst3");
        if !dll_path.exists() {
            eprintln!("Skipping: VSL plugin not found");
            return;
        }

        let mut factory = PluginFactory::load(&dll_path).expect("Should load real VST3 DLL");
        factory.initialize().expect("Should initialize factory");

        assert!(!factory.plugin_name.is_empty());
        assert!(factory.is_loaded());

        match factory.get_plugin_factory_fn() {
            Ok(_func_ptr) => {
                eprintln!("GetPluginFactory found in '{}'", factory.plugin_name);
            }
            Err(err) => {
                eprintln!(
                    "Note: GetPluginFactory not found in '{}': {}",
                    factory.plugin_name, err
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
    fn parses_fixed_c_string_fields() {
        let mut field = [0 as c_char; 16];
        for (dst, src) in field.iter_mut().zip(b"Audio\0") {
            *dst = *src as c_char;
        }
        assert_eq!(fixed_cstr(&field), "Audio");
    }

    #[test]
    fn factory_from_scanned_plugin() {
        let system_path = PathBuf::from("C:/Program Files/Common Files/VST3");
        if !system_path.exists() {
            eprintln!("Skipping: system VST3 path not found");
            return;
        }

        let scanner = crate::scanner::PluginScanner::new();
        let plugins = scanner.scan();

        for info in &plugins {
            assert!(
                info.dll_path.exists(),
                "dll_path should exist for {}: {}",
                info.name,
                info.dll_path.display()
            );

            let factory = PluginFactory::load(&info.dll_path);
            match factory {
                Ok(mut factory) => {
                    eprintln!("Loaded: {} from {}", factory.plugin_name, factory.path);
                    let _ = factory.initialize();
                    assert!(factory.is_loaded());
                }
                Err(err) => {
                    eprintln!("Failed to load {}: {}", info.name, err);
                }
            }
        }
    }
}
