/// VST3 host application context.
/// Implements the IHostApplication interface that plugins call back into.
pub struct Vst3Host {
    pub name: String,
}

impl Vst3Host {
    pub fn new() -> Self {
        Self { name: "ATRI Host".to_string() }
    }
}

impl Default for Vst3Host {
    fn default() -> Self {
        Self::new()
    }
}
