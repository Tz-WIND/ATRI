use std::path::PathBuf;

use serde::Deserialize;

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(default)]
pub struct HostConfig {
    pub vst3_plugin_paths: Vec<PathBuf>,
    pub vst2_plugin_paths: Vec<PathBuf>,
    pub audio_host: AudioHostConfig,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(default)]
pub struct AudioHostConfig {
    pub binary_path: String,
    pub sample_rate: u32,
    pub buffer_size: usize,
    pub auto_start: bool,
    #[serde(default = "default_audio_engine")]
    pub audio_engine: String,
    #[serde(default = "default_bit_depth")]
    pub bit_depth: String,
}

fn default_audio_engine() -> String {
    "default".to_string()
}

fn default_bit_depth() -> String {
    "f32".to_string()
}

impl Default for AudioHostConfig {
    fn default() -> Self {
        Self {
            binary_path: String::new(),
            sample_rate: 48_000,
            buffer_size: 256,
            auto_start: true,
            audio_engine: default_audio_engine(),
            bit_depth: default_bit_depth(),
        }
    }
}

impl Default for HostConfig {
    fn default() -> Self {
        Self {
            vst3_plugin_paths: Vec::new(),
            vst2_plugin_paths: Vec::new(),
            audio_host: AudioHostConfig::default(),
        }
    }
}

impl AudioHostConfig {
    fn normalize(&mut self) {
        if self.sample_rate == 0 {
            self.sample_rate = AudioHostConfig::default().sample_rate;
        }
        if self.buffer_size == 0 {
            self.buffer_size = AudioHostConfig::default().buffer_size;
        }
    }
}

impl HostConfig {
    pub fn load() -> Self {
        let Some(path) = find_config_path() else {
            eprintln!("[atri-host] config.yaml not found; using defaults");
            return Self::default();
        };

        match std::fs::read_to_string(&path) {
            Ok(content) => match parse_host_config(&content) {
                Ok(config) => {
                    eprintln!(
                        "[atri-host] loaded config from {} (vst3_paths={}, vst2_paths={}, sample_rate={}, buffer_size={}, engine={}, bit_depth={})",
                        path.display(),
                        config.vst3_plugin_paths.len(),
                        config.vst2_plugin_paths.len(),
                        config.audio_host.sample_rate,
                        config.audio_host.buffer_size,
                        config.audio_host.audio_engine,
                        config.audio_host.bit_depth,
                    );
                    config
                }
                Err(err) => {
                    eprintln!(
                        "[atri-host] failed to parse config.yaml at {}: {}; using defaults",
                        path.display(),
                        err
                    );
                    Self::default()
                }
            },
            Err(err) => {
                eprintln!(
                    "[atri-host] failed to read config.yaml at {}: {}; using defaults",
                    path.display(),
                    err
                );
                Self::default()
            }
        }
    }
}

fn find_config_path() -> Option<PathBuf> {
    if let Ok(path) = std::env::var("ATRI_CONFIG") {
        let path = PathBuf::from(path);
        if path.exists() {
            return Some(path);
        }
    }

    let mut roots = Vec::new();
    if let Ok(cwd) = std::env::current_dir() {
        roots.push(cwd);
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            roots.push(parent.to_path_buf());
        }
    }

    for root in roots {
        for ancestor in root.ancestors() {
            let candidate = ancestor.join("config.yaml");
            if candidate.exists() {
                return Some(candidate);
            }
        }
    }

    None
}

fn parse_host_config(content: &str) -> Result<HostConfig, serde_yaml::Error> {
    let mut config: HostConfig = serde_yaml::from_str(content)?;
    config.audio_host.normalize();
    Ok(config)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_host_config_reads_vst_scan_paths() {
        let config = parse_host_config(
            r#"
model: test
vst3_plugin_paths:
  - C:\IndentedVST3
  - D:\VST3
  - 'E:\Plugins\VST3'
vst2_plugin_paths:
  - D:\VST2
providers:
  ignored:
    vst3_plugin_paths:
      - Z:\nested
"#,
        )
        .unwrap();

        assert_eq!(
            config.vst3_plugin_paths,
            vec![
                PathBuf::from(r"C:\IndentedVST3"),
                PathBuf::from(r"D:\VST3"),
                PathBuf::from(r"E:\Plugins\VST3")
            ]
        );
        assert_eq!(config.vst2_plugin_paths, vec![PathBuf::from(r"D:\VST2")]);
        // defaults
        assert_eq!(config.audio_host.sample_rate, 48_000);
        assert_eq!(config.audio_host.buffer_size, 256);
        assert_eq!(config.audio_host.audio_engine, "default");
        assert_eq!(config.audio_host.bit_depth, "f32");
    }

    #[test]
    fn parse_host_config_reads_inline_and_anchored_vst_scan_paths() {
        let config = parse_host_config(
            r#"
model: test
common_vst3: &common_vst3 D:\VST3
vst3_plugin_paths:
  - *common_vst3
vst2_plugin_paths: ['D:\VST2', 'E:\Plugins\VST2']
"#,
        )
        .unwrap();

        assert_eq!(config.vst3_plugin_paths, vec![PathBuf::from(r"D:\VST3")]);
        assert_eq!(
            config.vst2_plugin_paths,
            vec![PathBuf::from(r"D:\VST2"), PathBuf::from(r"E:\Plugins\VST2")]
        );
    }

    #[test]
    fn parse_host_config_ignores_missing_keys() {
        assert_eq!(
            parse_host_config("model: test").unwrap(),
            HostConfig::default()
        );
    }

    #[test]
    fn parse_host_config_reads_audio_host_section() {
        let config = parse_host_config(
            r#"
model: test
audio_host:
  sample_rate: 96000
  buffer_size: 512
  audio_engine: asio
  bit_depth: i24
"#,
        )
        .unwrap();

        assert_eq!(config.audio_host.sample_rate, 96_000);
        assert_eq!(config.audio_host.buffer_size, 512);
        assert_eq!(config.audio_host.audio_engine, "asio");
        assert_eq!(config.audio_host.bit_depth, "i24");
    }

    #[test]
    fn parse_host_config_normalizes_zero_audio_values() {
        let config = parse_host_config(
            r#"
audio_host:
  sample_rate: 0
  buffer_size: 0
"#,
        )
        .unwrap();

        assert_eq!(config.audio_host.sample_rate, 48_000);
        assert_eq!(config.audio_host.buffer_size, 256);
    }

    #[test]
    fn parse_host_config_rejects_invalid_yaml() {
        assert!(parse_host_config("vst3_plugin_paths: [unterminated").is_err());
    }
}
