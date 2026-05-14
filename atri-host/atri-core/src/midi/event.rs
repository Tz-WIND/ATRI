use super::message::MidiMessage;

/// A MIDI event with a precise tick position.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MidiEvent {
    pub tick: i64,
    pub message: MidiMessage,
}

impl MidiEvent {
    pub fn new(tick: i64, message: MidiMessage) -> Self {
        Self { tick, message }
    }
}
