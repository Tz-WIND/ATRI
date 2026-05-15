use std::ffi::{c_char, c_void};
use std::fmt;
use std::mem;
use std::path::Path;
use std::ptr;

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
pub struct PluginFactory {
    factory: Option<ComPtr<IPluginFactory>>,
    library: libloading::Library,
    #[cfg(target_os = "windows")]
    exit_dll: Option<ExitDll>,
    pub path: String,
    pub plugin_name: String,
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

impl PluginFactory {
    /// Load a VST3 plugin from the given platform-specific shared library.
    pub fn load(path: &Path) -> Result<Self, String> {
        let path_str = path.to_string_lossy().to_string();
        if !path.exists() {
            return Err(format!("Plugin not found: {}", path_str));
        }

        let plugin_name = path
            .file_stem()
            .map(|s| s.to_string_lossy().to_string())
            .unwrap_or_else(|| "Unknown".to_string());

        let library = unsafe { libloading::Library::new(path) }
            .map_err(|err| format!("Failed to load plugin {}: {}", path_str, err))?;

        let get_factory: GetPluginFactory = unsafe {
            *library
                .get(b"GetPluginFactory\0")
                .map_err(|err| format!("GetPluginFactory not found: {err}"))?
        };

        #[cfg(target_os = "windows")]
        let exit_dll = initialize_windows_module(&library, &plugin_name)?;

        let factory = unsafe { get_factory() };
        let factory = match unsafe { ComPtr::from_raw(factory) } {
            Some(factory) => factory,
            None => {
                #[cfg(target_os = "windows")]
                if let Some(exit_dll) = exit_dll {
                    unsafe {
                        let _ = exit_dll();
                    }
                }
                return Err("GetPluginFactory returned null".to_string());
            }
        };

        Ok(Self {
            factory: Some(factory),
            library,
            #[cfg(target_os = "windows")]
            exit_dll,
            path: path_str,
            plugin_name,
        })
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
        self.factory.is_some()
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

#[cfg(target_os = "windows")]
fn initialize_windows_module(
    library: &libloading::Library,
    plugin_name: &str,
) -> Result<Option<ExitDll>, String> {
    if let Ok(init_dll) = unsafe { library.get::<InitDll>(b"InitDll\0") } {
        let ok = unsafe { init_dll() };
        if !ok {
            return Err(format!(
                "VST3 plugin '{plugin_name}' InitDll returned false"
            ));
        }
    }

    Ok(unsafe { library.get::<ExitDll>(b"ExitDll\0") }
        .ok()
        .map(|sym| *sym))
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

        let factory = PluginFactory::load(&dll_path).expect("Should load real VST3 DLL");

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
                Ok(factory) => {
                    eprintln!("Loaded: {} from {}", factory.plugin_name, factory.path);
                    assert!(factory.is_loaded());
                }
                Err(err) => {
                    eprintln!("Failed to load {}: {}", info.name, err);
                }
            }
        }
    }
}
