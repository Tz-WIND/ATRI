use vst3::{Steinberg::TUID, uid};

pub const PLUGIN_NAME: &str = "ATRI Bridge";
pub const VENDOR: &str = "ATRI";
pub const VENDOR_URL: &str = "https://atri.local";
pub const VENDOR_EMAIL: &str = "support@atri.local";
pub const PLUGIN_CATEGORY: &str = "Instrument";
pub const PLUGIN_VERSION: &str = env!("CARGO_PKG_VERSION");

pub const COMPONENT_CLASS_ID: TUID = uid(0x41545249, 0xB2104001, 0x9A010527, 0x00000004);
pub const CONTROLLER_CLASS_ID: TUID = uid(0x41545249, 0xB2104002, 0x9A010527, 0x00000004);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BridgePluginIdentity {
    pub name: &'static str,
    pub vendor: &'static str,
    pub category: &'static str,
    pub version: &'static str,
    pub component_class_id: TUID,
    pub controller_class_id: TUID,
}

impl Default for BridgePluginIdentity {
    fn default() -> Self {
        Self {
            name: PLUGIN_NAME,
            vendor: VENDOR,
            category: PLUGIN_CATEGORY,
            version: PLUGIN_VERSION,
            component_class_id: COMPONENT_CLASS_ID,
            controller_class_id: CONTROLLER_CLASS_ID,
        }
    }
}
