/// VST3 host application context.
/// Implements the IHostApplication interface that plugins call back into.
#[derive(Debug, Clone)]
pub struct Vst3Host {
    pub name: String,
}

impl Vst3Host {
    pub fn new() -> Self {
        Self {
            name: "ATRI Host".to_string(),
        }
    }

    pub fn with_name(name: impl Into<String>) -> Self {
        Self { name: name.into() }
    }
}

impl Default for Vst3Host {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_host_name() {
        let host = Vst3Host::new();
        assert_eq!(host.name, "ATRI Host");
    }

    #[test]
    fn custom_host_name() {
        let host = Vst3Host::with_name("My DAW");
        assert_eq!(host.name, "My DAW");
    }

    #[test]
    fn default_trait() {
        let host = Vst3Host::default();
        assert_eq!(host.name, "ATRI Host");
    }

    #[test]
    fn clone_works() {
        let host = Vst3Host::with_name("Test");
        let host2 = host.clone();
        assert_eq!(host2.name, "Test");
    }
}
