use std::ffi::{OsStr, OsString};
use std::path::PathBuf;
use std::process::ExitCode;

use atri_bridge_vst3::packaging::{BuildProfile, package_from_target_dir};

fn main() -> ExitCode {
    let args = match PackageArgs::parse_from(std::env::args_os()) {
        Ok(args) => args,
        Err(err) => {
            eprintln!("{err}");
            eprintln!("{}", usage());
            return ExitCode::from(2);
        }
    };

    match package_from_target_dir(&args.target_dir, args.profile, &args.output_dir) {
        Ok(bundle) => {
            println!("{}", bundle.display());
            ExitCode::SUCCESS
        }
        Err(err) => {
            eprintln!("{err}");
            ExitCode::from(1)
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct PackageArgs {
    profile: BuildProfile,
    target_dir: PathBuf,
    output_dir: PathBuf,
}

impl PackageArgs {
    fn parse_from<I, S>(args: I) -> Result<Self, String>
    where
        I: IntoIterator<Item = S>,
        S: Into<OsString>,
    {
        let mut profile = BuildProfile::Debug;
        let mut target_dir = default_target_dir();
        let mut output_dir: Option<PathBuf> = None;
        let mut args = args.into_iter().map(Into::into);
        let _program = args.next();

        while let Some(arg) = args.next() {
            if arg == OsStr::new("--release") {
                profile = BuildProfile::Release;
                continue;
            }

            if arg == OsStr::new("--target-dir") {
                target_dir = next_path_arg("--target-dir", args.next())?;
                continue;
            }

            if arg == OsStr::new("--output-dir") {
                output_dir = Some(next_path_arg("--output-dir", args.next())?);
                continue;
            }

            return Err(format!("unknown argument: {}", arg.to_string_lossy()));
        }

        let output_dir = output_dir.unwrap_or_else(|| target_dir.join(profile.dir_name()));
        Ok(Self {
            profile,
            target_dir,
            output_dir,
        })
    }
}

fn next_path_arg(flag: &str, value: Option<OsString>) -> Result<PathBuf, String> {
    let value = value.ok_or_else(|| format!("{flag} requires a path"))?;
    Ok(PathBuf::from(value))
}

fn default_target_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("atri-bridge-vst3 lives inside the atri-host workspace")
        .join("target")
}

fn usage() -> &'static str {
    "usage: package_bridge [--release] [--target-dir PATH] [--output-dir PATH]"
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn parse_args_defaults_to_debug_target_output() {
        let args = PackageArgs::parse_from(["package_bridge"]).unwrap();

        assert_eq!(args.profile, BuildProfile::Debug);
        assert_eq!(args.target_dir, default_target_dir());
        assert_eq!(args.output_dir, default_target_dir().join("debug"));
    }

    #[test]
    fn parse_args_accepts_release_target_and_output() {
        let args = PackageArgs::parse_from([
            "package_bridge",
            "--release",
            "--target-dir",
            "build-target",
            "--output-dir",
            "dist",
        ])
        .unwrap();

        assert_eq!(args.profile, BuildProfile::Release);
        assert_eq!(args.target_dir, PathBuf::from("build-target"));
        assert_eq!(args.output_dir, PathBuf::from("dist"));
    }
}
