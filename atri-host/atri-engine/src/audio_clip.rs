use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use atri_core::audio::buffer::AudioBuffer;
use atri_core::time::beats::Beats;
use atri_core::time::tempo_map::TempoMap;

#[derive(Debug, Clone)]
pub struct AudioClipSpec {
    pub path: PathBuf,
    pub start_beats: f64,
    pub duration_beats: f64,
    pub source_offset_seconds: f64,
    pub gain: f32,
    pub channel_mode: AudioChannelMode,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AudioChannelMode {
    Mono,
    Multichannel,
}

#[derive(Debug, Clone)]
pub struct AudioClip {
    start_beats: f64,
    duration_beats: f64,
    source_offset_seconds: f64,
    gain: f32,
    channel_mode: AudioChannelMode,
    source: Arc<AudioSource>,
}

#[derive(Debug)]
struct AudioSource {
    sample_rate: u32,
    channels: u16,
    frames: usize,
    samples: Vec<f32>,
}

#[derive(Debug)]
struct WavFormat {
    format: u16,
    channels: u16,
    sample_rate: u32,
    block_align: u16,
    bits_per_sample: u16,
}

impl AudioClip {
    pub fn load(spec: AudioClipSpec) -> Result<Self, String> {
        if !spec.start_beats.is_finite() || spec.start_beats < 0.0 {
            return Err("audio clip start must be a non-negative finite beat".to_string());
        }
        if !spec.duration_beats.is_finite() || spec.duration_beats <= 0.0 {
            return Err("audio clip duration must be a positive finite beat length".to_string());
        }
        if !spec.source_offset_seconds.is_finite() || spec.source_offset_seconds < 0.0 {
            return Err("audio clip source offset must be a non-negative finite value".to_string());
        }
        if !spec.gain.is_finite() || spec.gain < 0.0 {
            return Err("audio clip gain must be a non-negative finite value".to_string());
        }

        Ok(Self {
            start_beats: spec.start_beats,
            duration_beats: spec.duration_beats,
            source_offset_seconds: spec.source_offset_seconds,
            gain: spec.gain,
            channel_mode: spec.channel_mode,
            source: Arc::new(AudioSource::load(&spec.path)?),
        })
    }

    pub fn render_into(
        &self,
        buffer: &mut AudioBuffer,
        start_sample: i64,
        end_sample: i64,
        tempo_map: &TempoMap,
        nframes: usize,
    ) {
        if buffer.channels() == 0 || nframes == 0 {
            return;
        }

        let clip_start = tempo_map.sample_at_beats(Beats::from_beats(self.start_beats));
        let clip_end =
            tempo_map.sample_at_beats(Beats::from_beats(self.start_beats + self.duration_beats));
        let overlap_start = start_sample.max(clip_start);
        let overlap_end = end_sample.min(clip_end);
        if overlap_start >= overlap_end {
            return;
        }

        let host_sample_rate = f64::from(tempo_map.sample_rate().max(1));
        let source_sample_rate = f64::from(self.source.sample_rate.max(1));
        let source_offset = self.source_offset_seconds * source_sample_rate;
        let first_output = (overlap_start - start_sample).max(0) as usize;
        let first_source_frame = source_offset
            + (overlap_start - clip_start) as f64 * source_sample_rate / host_sample_rate;
        let frames = (overlap_end - overlap_start).max(0) as usize;
        let channels = buffer.channels();

        for frame in 0..frames {
            let output_frame = first_output + frame;
            if output_frame >= nframes || output_frame >= buffer.capacity() {
                break;
            }
            let source_frame =
                first_source_frame + frame as f64 * source_sample_rate / host_sample_rate;
            if source_frame >= self.source.frames as f64 {
                break;
            }
            for channel in 0..channels {
                let sample = match self.channel_mode {
                    AudioChannelMode::Mono => self.source.mono_sample(source_frame),
                    AudioChannelMode::Multichannel => self.source.sample(channel, source_frame),
                } * self.gain;
                buffer.channel_mut(channel)[output_frame] += sample;
            }
        }
    }
}

impl AudioSource {
    fn load(path: &Path) -> Result<Self, String> {
        match path
            .extension()
            .and_then(|extension| extension.to_str())
            .unwrap_or_default()
            .to_ascii_lowercase()
            .as_str()
        {
            "flac" => parse_flac(path).map_err(|err| format!("{}: {err}", path.display())),
            "aac" | "m4a" | "mp3" => {
                parse_compressed_audio(path).map_err(|err| format!("{}: {err}", path.display()))
            }
            _ => {
                let data = fs::read(path).map_err(|err| {
                    format!("failed to read audio clip '{}': {err}", path.display())
                })?;
                parse_wav(&data).map_err(|err| format!("{}: {err}", path.display()))
            }
        }
    }

    fn sample(&self, output_channel: u16, frame_position: f64) -> f32 {
        let channel = if self.channels == 1 {
            0
        } else {
            output_channel.min(self.channels - 1)
        };
        self.sample_channel(channel, frame_position)
    }

    fn mono_sample(&self, frame_position: f64) -> f32 {
        if self.channels == 0 {
            return 0.0;
        }
        let sum = (0..self.channels)
            .map(|channel| self.sample_channel(channel, frame_position))
            .sum::<f32>();
        sum / f32::from(self.channels)
    }

    fn sample_channel(&self, channel: u16, frame_position: f64) -> f32 {
        if frame_position < 0.0 || self.frames == 0 {
            return 0.0;
        }
        let frame = frame_position.floor() as usize;
        if frame >= self.frames {
            return 0.0;
        }
        let next_frame = (frame + 1).min(self.frames - 1);
        let frac = (frame_position - frame as f64) as f32;
        let channel = channel.min(self.channels - 1);
        let a = self.frame_sample(frame, channel);
        let b = self.frame_sample(next_frame, channel);
        a + (b - a) * frac
    }

    fn frame_sample(&self, frame: usize, channel: u16) -> f32 {
        self.samples
            .get(frame * usize::from(self.channels) + usize::from(channel))
            .copied()
            .unwrap_or(0.0)
    }
}

pub fn render_audio_clips(
    clips: &[AudioClip],
    buffer: &mut AudioBuffer,
    start_sample: i64,
    end_sample: i64,
    tempo_map: &TempoMap,
    nframes: usize,
) {
    for clip in clips {
        clip.render_into(buffer, start_sample, end_sample, tempo_map, nframes);
    }
}

fn parse_wav(data: &[u8]) -> Result<AudioSource, String> {
    if data.len() < 12 || &data[0..4] != b"RIFF" || &data[8..12] != b"WAVE" {
        return Err("unsupported audio file; expected RIFF/WAVE".to_string());
    }

    let mut offset = 12usize;
    let mut format = None;
    let mut audio_data = None;
    while offset + 8 <= data.len() {
        let id = &data[offset..offset + 4];
        let size = read_u32_le(data, offset + 4)? as usize;
        offset += 8;
        if offset + size > data.len() {
            return Err("WAV chunk extends past end of file".to_string());
        }
        let chunk = &data[offset..offset + size];
        match id {
            b"fmt " => format = Some(parse_wav_format(chunk)?),
            b"data" => audio_data = Some(chunk),
            _ => {}
        }
        offset += size + (size % 2);
    }

    let format = format.ok_or_else(|| "WAV fmt chunk missing".to_string())?;
    let audio_data = audio_data.ok_or_else(|| "WAV data chunk missing".to_string())?;
    decode_wav_samples(&format, audio_data)
}

fn parse_flac(path: &Path) -> Result<AudioSource, String> {
    let mut reader =
        claxon::FlacReader::open(path).map_err(|err| format!("failed to open FLAC: {err}"))?;
    let info = reader.streaminfo();
    let channels = u16::try_from(info.channels)
        .map_err(|_| format!("unsupported FLAC channel count: {}", info.channels))?;
    if channels == 0 {
        return Err("FLAC channel count must be positive".to_string());
    }
    let bits_per_sample = u32::from(info.bits_per_sample);
    if bits_per_sample == 0 || bits_per_sample > 32 {
        return Err(format!(
            "unsupported FLAC bit depth: {}",
            info.bits_per_sample
        ));
    }
    let scale = (1_u64 << bits_per_sample.saturating_sub(1)) as f32;
    let mut samples = Vec::new();
    for sample in reader.samples() {
        let sample = sample.map_err(|err| format!("failed to decode FLAC sample: {err}"))?;
        samples.push((sample as f32 / scale).clamp(-1.0, 1.0));
    }
    let frames = samples.len() / usize::from(channels);
    samples.truncate(frames * usize::from(channels));
    Ok(AudioSource {
        sample_rate: info.sample_rate,
        channels,
        frames,
        samples,
    })
}

fn parse_compressed_audio(path: &Path) -> Result<AudioSource, String> {
    use symphonia::core::codecs::audio::AudioDecoderOptions;
    use symphonia::core::errors::Error as SymphoniaError;
    use symphonia::core::formats::probe::Hint;
    use symphonia::core::formats::{FormatOptions, TrackType};
    use symphonia::core::io::MediaSourceStream;
    use symphonia::core::meta::MetadataOptions;

    let file = fs::File::open(path).map_err(|err| format!("failed to open audio: {err}"))?;
    let mss = MediaSourceStream::new(Box::new(file), Default::default());
    let mut hint = Hint::new();
    if let Some(extension) = path.extension().and_then(|extension| extension.to_str()) {
        hint.with_extension(extension);
    }

    let mut format = symphonia::default::get_probe()
        .probe(
            &hint,
            mss,
            FormatOptions::default(),
            MetadataOptions::default(),
        )
        .map_err(|err| format!("failed to probe compressed audio: {err}"))?;
    let track = format
        .default_track(TrackType::Audio)
        .ok_or_else(|| "compressed audio has no audio track".to_string())?;
    let track_id = track.id;
    let audio_params = track
        .codec_params
        .as_ref()
        .ok_or_else(|| "compressed audio codec parameters missing".to_string())?
        .audio()
        .ok_or_else(|| "compressed audio parameters are not audio".to_string())?;
    let mut decoder = symphonia::default::get_codecs()
        .make_audio_decoder(audio_params, &AudioDecoderOptions::default())
        .map_err(|err| format!("unsupported compressed audio codec: {err}"))?;

    let mut sample_rate = 0u32;
    let mut channels = 0u16;
    let mut samples = Vec::new();
    loop {
        let packet = match format.next_packet() {
            Ok(Some(packet)) => packet,
            Ok(None) => break,
            Err(SymphoniaError::ResetRequired) => break,
            Err(err) => return Err(format!("failed to read compressed audio packet: {err}")),
        };
        while !format.metadata().is_latest() {
            format.metadata().pop();
        }
        if packet.track_id != track_id {
            continue;
        }

        let decoded = match decoder.decode(&packet) {
            Ok(decoded) => decoded,
            Err(SymphoniaError::DecodeError(_)) | Err(SymphoniaError::IoError(_)) => continue,
            Err(err) => return Err(format!("failed to decode compressed audio: {err}")),
        };
        let spec = decoded.spec();
        let decoded_channels = u16::try_from(spec.channels().count()).map_err(|_| {
            format!(
                "unsupported compressed audio channel count: {}",
                spec.channels().count()
            )
        })?;
        if decoded_channels == 0 {
            return Err("compressed audio channel count must be positive".to_string());
        }
        if spec.rate() == 0 {
            return Err("compressed audio sample rate must be positive".to_string());
        }
        if sample_rate == 0 {
            sample_rate = spec.rate();
            channels = decoded_channels;
        } else if sample_rate != spec.rate() || channels != decoded_channels {
            return Err("compressed audio stream changed sample format mid-stream".to_string());
        }

        let mut packet_samples = vec![0.0f32; decoded.samples_interleaved()];
        decoded.copy_to_slice_interleaved(&mut packet_samples);
        samples.extend(packet_samples.into_iter().map(finite_sample));
    }

    if sample_rate == 0 || channels == 0 || samples.is_empty() {
        return Err("compressed audio decode produced no samples".to_string());
    }
    let frames = samples.len() / usize::from(channels);
    samples.truncate(frames * usize::from(channels));
    Ok(AudioSource {
        sample_rate,
        channels,
        frames,
        samples,
    })
}

fn parse_wav_format(chunk: &[u8]) -> Result<WavFormat, String> {
    if chunk.len() < 16 {
        return Err("WAV fmt chunk is too short".to_string());
    }
    let raw_format = read_u16_le(chunk, 0)?;
    let format = if raw_format == 0xfffe && chunk.len() >= 40 {
        read_u16_le(chunk, 24)?
    } else {
        raw_format
    };
    let channels = read_u16_le(chunk, 2)?;
    let sample_rate = read_u32_le(chunk, 4)?;
    let block_align = read_u16_le(chunk, 12)?;
    let bits_per_sample = read_u16_le(chunk, 14)?;

    if channels == 0 {
        return Err("WAV channel count must be positive".to_string());
    }
    if sample_rate == 0 {
        return Err("WAV sample rate must be positive".to_string());
    }
    if block_align == 0 {
        return Err("WAV block alignment must be positive".to_string());
    }

    Ok(WavFormat {
        format,
        channels,
        sample_rate,
        block_align,
        bits_per_sample,
    })
}

fn decode_wav_samples(format: &WavFormat, data: &[u8]) -> Result<AudioSource, String> {
    let bytes_per_sample = usize::from(format.bits_per_sample / 8);
    if bytes_per_sample == 0 || format.bits_per_sample % 8 != 0 {
        return Err("WAV bit depth must be byte-aligned".to_string());
    }
    if format.format != 1 && format.format != 3 {
        return Err("only PCM and IEEE float WAV clips are supported".to_string());
    }

    let channels = usize::from(format.channels);
    let block_align = usize::from(format.block_align);
    let minimum_frame_bytes = channels * bytes_per_sample;
    if block_align < minimum_frame_bytes {
        return Err("WAV block alignment is smaller than one sample frame".to_string());
    }

    let frames = data.len() / block_align;
    let mut samples = Vec::with_capacity(frames * channels);
    for frame in 0..frames {
        let frame_offset = frame * block_align;
        for channel in 0..channels {
            let sample_offset = frame_offset + channel * bytes_per_sample;
            let sample = decode_sample(
                format.format,
                format.bits_per_sample,
                &data[sample_offset..sample_offset + bytes_per_sample],
            )?;
            samples.push(sample);
        }
    }

    Ok(AudioSource {
        sample_rate: format.sample_rate,
        channels: format.channels,
        frames,
        samples,
    })
}

fn decode_sample(format: u16, bits_per_sample: u16, data: &[u8]) -> Result<f32, String> {
    match (format, bits_per_sample) {
        (1, 8) => Ok((f32::from(data[0]) - 128.0) / 128.0),
        (1, 16) => Ok(f32::from(i16::from_le_bytes([data[0], data[1]])) / 32768.0),
        (1, 24) => {
            let value = i32::from_le_bytes([
                data[0],
                data[1],
                data[2],
                if data[2] & 0x80 == 0 { 0x00 } else { 0xff },
            ]);
            Ok(value as f32 / 8_388_608.0)
        }
        (1, 32) => {
            let value = i32::from_le_bytes([data[0], data[1], data[2], data[3]]);
            Ok(value as f32 / 2_147_483_648.0)
        }
        (3, 32) => Ok(finite_sample(f32::from_le_bytes([
            data[0], data[1], data[2], data[3],
        ]))),
        (3, 64) => {
            let value = f64::from_le_bytes([
                data[0], data[1], data[2], data[3], data[4], data[5], data[6], data[7],
            ]);
            Ok(finite_sample(value as f32))
        }
        _ => Err(format!(
            "unsupported WAV sample format {format} with {bits_per_sample} bits"
        )),
    }
}

fn finite_sample(value: f32) -> f32 {
    if value.is_finite() {
        value.clamp(-1.0, 1.0)
    } else {
        0.0
    }
}

fn read_u16_le(data: &[u8], offset: usize) -> Result<u16, String> {
    let bytes = data
        .get(offset..offset + 2)
        .ok_or_else(|| "unexpected end of WAV data".to_string())?;
    Ok(u16::from_le_bytes([bytes[0], bytes[1]]))
}

fn read_u32_le(data: &[u8], offset: usize) -> Result<u32, String> {
    let bytes = data
        .get(offset..offset + 4)
        .ok_or_else(|| "unexpected end of WAV data".to_string())?;
    Ok(u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]))
}

#[cfg(test)]
mod tests {
    use super::*;
    use atri_core::time::tempo::{Meter, Tempo};
    use atri_core::time::tempo_map::TempoMap;

    #[test]
    fn parse_pcm16_wav_reads_frames() {
        let source = parse_wav(&stereo_i16_wav(&[0, 16_384, -16_384, 32_767])).unwrap();

        assert_eq!(source.sample_rate, 48_000);
        assert_eq!(source.channels, 2);
        assert_eq!(source.frames, 2);
        assert!((source.samples[1] - 0.5).abs() < 0.001);
        assert!((source.samples[2] + 0.5).abs() < 0.001);
    }

    #[test]
    fn compressed_audio_extensions_use_compressed_decoder() {
        for extension in ["mp3", "aac", "m4a"] {
            let path = std::env::temp_dir().join(format!(
                "atri_audio_clip_test_{}_invalid.{extension}",
                std::process::id()
            ));
            fs::write(&path, b"not compressed audio").unwrap();

            let err = AudioSource::load(&path).unwrap_err();

            assert!(
                err.contains("failed to probe compressed audio"),
                "{extension} used wrong decoder: {err}"
            );
            let _ = fs::remove_file(path);
        }
    }

    #[test]
    fn audio_clip_renders_at_beat_position() {
        let path = std::env::temp_dir().join(format!(
            "atri_audio_clip_test_{}_{}.wav",
            std::process::id(),
            "render"
        ));
        fs::write(&path, stereo_i16_wav(&[16_384, 0, 16_384, 0])).unwrap();

        let clip = AudioClip::load(AudioClipSpec {
            path: path.clone(),
            start_beats: 1.0,
            duration_beats: 1.0,
            source_offset_seconds: 0.0,
            gain: 1.0,
            channel_mode: AudioChannelMode::Multichannel,
        })
        .unwrap();
        let tempo_map = TempoMap::new(Tempo::new(120.0, 4), Meter::new(4, 4), 48_000);
        let start_sample = tempo_map.sample_at_beats(Beats::from_beats(1.0));
        let mut buffer = AudioBuffer::new(2, 4);

        clip.render_into(&mut buffer, start_sample, start_sample + 4, &tempo_map, 4);

        assert!((buffer.channel(0)[0] - 0.5).abs() < 0.001);
        assert!(buffer.channel(1)[0].abs() < 0.001);
        let _ = fs::remove_file(path);
    }

    #[test]
    fn audio_clip_mono_channel_mode_folds_source_to_all_output_channels() {
        let path = std::env::temp_dir().join(format!(
            "atri_audio_clip_test_{}_{}.wav",
            std::process::id(),
            "mono"
        ));
        fs::write(&path, stereo_i16_wav(&[32_767, 0])).unwrap();

        let clip = AudioClip::load(AudioClipSpec {
            path: path.clone(),
            start_beats: 0.0,
            duration_beats: 1.0,
            source_offset_seconds: 0.0,
            gain: 1.0,
            channel_mode: AudioChannelMode::Mono,
        })
        .unwrap();
        let tempo_map = TempoMap::new(Tempo::new(120.0, 4), Meter::new(4, 4), 48_000);
        let mut buffer = AudioBuffer::new(2, 2);

        clip.render_into(&mut buffer, 0, 2, &tempo_map, 2);

        assert!((buffer.channel(0)[0] - 0.5).abs() < 0.001);
        assert!((buffer.channel(1)[0] - 0.5).abs() < 0.001);
        let _ = fs::remove_file(path);
    }

    fn stereo_i16_wav(samples: &[i16]) -> Vec<u8> {
        let data_bytes = samples.len() * 2;
        let mut wav = Vec::with_capacity(44 + data_bytes);
        wav.extend_from_slice(b"RIFF");
        wav.extend_from_slice(&(36 + data_bytes as u32).to_le_bytes());
        wav.extend_from_slice(b"WAVE");
        wav.extend_from_slice(b"fmt ");
        wav.extend_from_slice(&16u32.to_le_bytes());
        wav.extend_from_slice(&1u16.to_le_bytes());
        wav.extend_from_slice(&2u16.to_le_bytes());
        wav.extend_from_slice(&48_000u32.to_le_bytes());
        wav.extend_from_slice(&(48_000u32 * 2 * 2).to_le_bytes());
        wav.extend_from_slice(&(2u16 * 2).to_le_bytes());
        wav.extend_from_slice(&16u16.to_le_bytes());
        wav.extend_from_slice(b"data");
        wav.extend_from_slice(&(data_bytes as u32).to_le_bytes());
        for sample in samples {
            wav.extend_from_slice(&sample.to_le_bytes());
        }
        wav
    }
}
