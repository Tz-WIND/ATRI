use super::event::{MidiEvent, ScheduledMidiEvent};
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

    pub fn event_capacity(&self) -> usize {
        self.notes.len() * 2
    }

    pub fn note_count(&self) -> usize {
        self.notes.len()
    }

    /// Collect NoteOn and NoteOff events in the sample range [start, end).
    pub fn collect_events_in_samples(
        &self,
        start_sample: i64,
        end_sample: i64,
        tempo_map: &TempoMap,
        out: &mut Vec<ScheduledMidiEvent>,
    ) {
        out.clear();
        for note in &self.notes {
            self.push_note_event(
                note.start_beats,
                MidiMessage::NoteOn {
                    channel: 0,
                    pitch: note.pitch,
                    velocity: note.velocity,
                },
                start_sample,
                end_sample,
                tempo_map,
                out,
            );

            self.push_note_event(
                note.end_beats(),
                MidiMessage::NoteOff {
                    channel: 0,
                    pitch: note.pitch,
                    velocity: 0,
                },
                start_sample,
                end_sample,
                tempo_map,
                out,
            );
        }

        out.sort_by_key(|ev| ev.offset);
    }

    /// Backwards-compatible helper for tests and non-realtime callers.
    pub fn get_events_in_range(
        &self,
        start_beats: f64,
        end_beats: f64,
        tempo_map: &TempoMap,
    ) -> Vec<ScheduledMidiEvent> {
        let start_sample = tempo_map.sample_at_beats(Beats::from_beats(start_beats));
        let end_sample = tempo_map.sample_at_beats(Beats::from_beats(end_beats));
        let mut events = Vec::with_capacity(self.event_capacity());
        self.collect_events_in_samples(start_sample, end_sample, tempo_map, &mut events);
        events
    }

    fn push_note_event(
        &self,
        beat: f64,
        message: MidiMessage,
        start_sample: i64,
        end_sample: i64,
        tempo_map: &TempoMap,
        out: &mut Vec<ScheduledMidiEvent>,
    ) {
        let sample = tempo_map.sample_at_beats(Beats::from_beats(beat));
        if sample < start_sample || sample >= end_sample {
            return;
        }

        let offset = (sample - start_sample) as usize;
        let tick = (beat * PPQN as f64) as i64;
        out.push(ScheduledMidiEvent::new(
            MidiEvent::new(tick, message),
            offset,
        ));
    }
}

impl Default for MidiSequencer {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::time::tempo::{Meter, Tempo};

    #[test]
    fn scheduled_events_keep_ppqn_tick_and_block_sample_offset() {
        let mut sequencer = MidiSequencer::new();
        sequencer.set_notes(vec![MidiNote::new(69, 1.0, 0.5, 100)]);
        let tempo_map = TempoMap::new(Tempo::new(120.0, 4), Meter::new(4, 4), 48_000);
        let event_sample = tempo_map.sample_at_beats(Beats::from_beats(1.0));
        let mut events = Vec::new();

        sequencer.collect_events_in_samples(
            event_sample - 5,
            event_sample + 16,
            &tempo_map,
            &mut events,
        );

        assert_eq!(events.len(), 1);
        assert_eq!(events[0].event.tick, PPQN as i64);
        assert_eq!(events[0].offset, 5);
        assert_eq!(
            events[0].event.message,
            MidiMessage::NoteOn {
                channel: 0,
                pitch: 69,
                velocity: 100
            }
        );
    }
}
