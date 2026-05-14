use std::path::PathBuf;

use serde::Deserialize;

#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub struct HostConfig {
    pub vst3_plugin_paths: Vec<PathBuf>,
    pub vst2_plugin_paths: Vec<PathBuf>,
}

impl HostConfig {
    pub fn load() -> Self {
        let Some(path) = find_config_path() else {
            eprintln!("[atri-host] config.yaml not found; using standard VST scan paths");
            return Self::default();
        };

        match std::fs::read_to_string(&path) {
            Ok(content) => match parse_host_config(&content) {
                Ok(config) => {
                    eprintln!(
                        "[atri-host] loaded VST scan config from {} (vst3_paths={}, vst2_paths={})",
                        path.display(),
                        config.vst3_plugin_paths.len(),
                        config.vst2_plugin_paths.len()
                    );
                    config
                }
                Err(err) => {
                    eprintln!(
                        "[atri-host] failed to parse config.yaml at {}: {}; using standard VST scan paths",
                        path.display(),
                        err
                    );
                    Self::default()
                }
            },
            Err(err) => {
                eprintln!(
                    "[atri-host] failed to read config.yaml at {}: {}; using standard VST scan paths",
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
    serde_yaml::from_str(content)
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
    fn parse_host_config_rejects_invalid_yaml() {
        assert!(parse_host_config("vst3_plugin_paths: [unterminated").is_err());
    }
}
