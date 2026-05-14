use super::message::MidiMessage;

/// A MIDI event at a musical timeline position.
///
/// `tick` is a PPQN tick on the song timeline. It is useful for sequencer
/// ordering, editing, and converting the event to sample time through a
/// `TempoMap`; it is not a sample offset inside an audio callback.
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

/// A MIDI event scheduled inside the current audio cycle.
///
/// `event.tick` preserves the original PPQN musical position. `offset` is the
/// sample offset from the start of the current audio block and is the value
/// processors should use for sample-accurate rendering within that block.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScheduledMidiEvent {
    pub event: MidiEvent,
    pub offset: usize,
}

impl ScheduledMidiEvent {
    pub fn new(event: MidiEvent, offset: usize) -> Self {
        Self { event, offset }
    }
}
