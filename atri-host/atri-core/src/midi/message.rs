use serde::{Deserialize, Serialize};

/// Standard MIDI message types.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum MidiMessage {
    NoteOn { channel: u8, pitch: u8, velocity: u8 },
    NoteOff { channel: u8, pitch: u8, velocity: u8 },
    ControlChange { channel: u8, controller: u8, value: u8 },
    PitchBend { channel: u8, value: i16 },
    ProgramChange { channel: u8, program: u8 },
    ChannelPressure { channel: u8, pressure: u8 },
    PolyphonicKeyPressure { channel: u8, pitch: u8, pressure: u8 },
    SystemExclusive(Vec<u8>),
    AllNotesOff { channel: u8 },
}

impl MidiMessage {
    pub fn channel(&self) -> Option<u8> {
        match self {
            MidiMessage::NoteOn { channel, .. }
            | MidiMessage::NoteOff { channel, .. }
            | MidiMessage::ControlChange { channel, .. }
            | MidiMessage::PitchBend { channel, .. }
            | MidiMessage::ProgramChange { channel, .. }
            | MidiMessage::ChannelPressure { channel, .. }
            | MidiMessage::PolyphonicKeyPressure { channel, .. }
            | MidiMessage::AllNotesOff { channel } => Some(*channel),
            MidiMessage::SystemExclusive(_) => None,
        }
    }
}
