use std::f32::consts::TAU;

use atri_core::audio::buffer_set::BufferSet;
use atri_core::midi::event::ScheduledMidiEvent;
use atri_core::midi::message::MidiMessage;
use atri_core::plugin::Plugin;

#[derive(Debug, Clone)]
struct Voice {
    pitch: u8,
    phase: f32,
    phase_step: f32,
    gain: f32,
    releasing: bool,
}

/// A tiny built-in sine synth used for host tests and plugin-free playback.
pub struct BasicSynth {
    name: String,
    sample_rate: f32,
    active: bool,
    block_size: usize,
    voices: Vec<Voice>,
}

impl BasicSynth {
    pub fn new(sample_rate: u32) -> Self {
        Self {
            name: "ATRI Basic Synth".to_string(),
            sample_rate: sample_rate as f32,
            active: false,
            block_size: 256,
            voices: Vec::with_capacity(32),
        }
    }

    fn handle_event(&mut self, event: &ScheduledMidiEvent) {
        match &event.event.message {
            MidiMessage::NoteOn {
                pitch, velocity, ..
            } if *velocity > 0 => {
                self.note_on(*pitch, *velocity);
            }
            MidiMessage::NoteOn { pitch, .. } | MidiMessage::NoteOff { pitch, .. } => {
                self.note_off(*pitch);
            }
            MidiMessage::AllNotesOff { .. } => self.voices.clear(),
            _ => {}
        }
    }

    fn note_on(&mut self, pitch: u8, velocity: u8) {
        let freq = 440.0 * 2f32.powf((pitch as f32 - 69.0) / 12.0);
        let gain = velocity as f32 / 127.0 * 0.18;
        self.voices.push(Voice {
            pitch,
            phase: 0.0,
            phase_step: TAU * freq / self.sample_rate,
            gain,
            releasing: false,
        });
    }

    fn note_off(&mut self, pitch: u8) {
        for voice in &mut self.voices {
            if voice.pitch == pitch {
                voice.releasing = true;
            }
        }
    }

    fn render_sample(&mut self) -> f32 {
        let mut sample = 0.0;
        for voice in &mut self.voices {
            sample += voice.phase.sin() * voice.gain;
            voice.phase = (voice.phase + voice.phase_step) % TAU;
            if voice.releasing {
                voice.gain *= 0.985;
            }
        }
        self.voices.retain(|voice| voice.gain > 0.0005);
        sample.clamp(-1.0, 1.0)
    }
}

impl Plugin for BasicSynth {
    fn name(&self) -> &str {
        &self.name
    }

    fn activate(&mut self) {
        self.active = true;
    }

    fn deactivate(&mut self) {
        self.active = false;
        self.voices.clear();
    }

    fn set_block_size(&mut self, nframes: usize) {
        self.block_size = nframes;
    }

    fn connect_and_run(
        &mut self,
        bufs: &mut BufferSet,
        midi: &[ScheduledMidiEvent],
        _start_sample: i64,
        _end_sample: i64,
        _speed: f64,
        nframes: usize,
    ) {
        if !self.active {
            return;
        }

        let Some(buf) = bufs.get_mut(0) else {
            return;
        };

        let mut event_index = 0;
        for frame in 0..nframes {
            while event_index < midi.len() && midi[event_index].offset <= frame {
                self.handle_event(&midi[event_index]);
                event_index += 1;
            }

            let sample = self.render_sample();
            for channel in 0..buf.channels() {
                buf.channel_mut(channel)[frame] += sample;
            }
        }
    }

    fn get_parameter(&self, _index: u32) -> f32 {
        0.0
    }

    fn set_parameter(&mut self, _index: u32, _value: f32) {}

    fn parameter_count(&self) -> u32 {
        0
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use atri_core::midi::event::MidiEvent;

    #[test]
    fn note_event_produces_audio() {
        let mut synth = BasicSynth::new(48_000);
        let mut bufs = BufferSet::new(1, 2, 128);
        let midi = [ScheduledMidiEvent::new(
            MidiEvent::new(
                0,
                MidiMessage::NoteOn {
                    channel: 0,
                    pitch: 69,
                    velocity: 100,
                },
            ),
            0,
        )];

        synth.activate();
        synth.connect_and_run(&mut bufs, &midi, 0, 128, 1.0, 128);

        let energy: f32 = bufs
            .get(0)
            .unwrap()
            .channel(0)
            .iter()
            .map(|s| s.abs())
            .sum();
        assert!(energy > 0.0);
    }
}
