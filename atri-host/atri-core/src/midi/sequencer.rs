use super::event::MidiEvent;
use super::message::MidiMessage;
use super::note::MidiNote;
use crate::time::beats::{Beats, PPQN};
use crate::time::tempo_map::TempoMap;

/// A simple MIDI sequencer storing notes per track.
#[derive(Debug, Clone)]
pub struct MidiSequencer {
    notes: Vec<MidiNote>,
}

impl MidiSequencer {
    pub fn new() -> Self {
        Self { notes: Vec::new() }
    }

    pub fn set_notes(&mut self, notes: Vec<MidiNote>) {
        self.notes = notes;
        // Sort by start time for efficient range queries
        self.notes.sort_by(|a, b| {
            a.start_beats
                .partial_cmp(&b.start_beats)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
    }

    pub fn clear(&mut self) {
        self.notes.clear();
    }

    /// Get all MIDI events (NoteOn + NoteOff) that fall within the given
    /// beat range [start, end). Returns each event with its sample offset
    /// from the start of the buffer.
    pub fn get_events_in_range(
        &self,
        start_beats: f64,
        end_beats: f64,
        tempo_map: &TempoMap,
    ) -> Vec<(MidiEvent, usize)> {
        let mut events = Vec::new();

        for note in &self.notes {
            // NoteOn within range
            if note.start_beats >= start_beats && note.start_beats < end_beats {
                let beat_offset = note.start_beats - start_beats;
                let sample = tempo_map.sample_at_beats(Beats::from_beats(beat_offset));
                let offset = sample.max(0) as usize;

                events.push((
                    MidiEvent::new(
                        (note.start_beats * PPQN as f64) as i64,
                        MidiMessage::NoteOn {
                            channel: 0,
                            pitch: note.pitch,
                            velocity: note.velocity,
                        },
                    ),
                    offset,
                ));
            }

            // NoteOff within range
            let end = note.end_beats();
            if end >= start_beats && end < end_beats {
                let beat_offset = end - start_beats;
                let sample = tempo_map.sample_at_beats(Beats::from_beats(beat_offset));
                let offset = sample.max(0) as usize;

                events.push((
                    MidiEvent::new(
                        (end * PPQN as f64) as i64,
                        MidiMessage::NoteOff {
                            channel: 0,
                            pitch: note.pitch,
                            velocity: 0,
                        },
                    ),
                    offset,
                ));
            }
        }

        events.sort_by_key(|(_, offset)| *offset);
        events
    }
}

impl Default for MidiSequencer {
    fn default() -> Self {
        Self::new()
    }
}
