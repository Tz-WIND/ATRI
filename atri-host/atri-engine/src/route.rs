use std::sync::{Arc, Mutex};

use atri_core::audio::buffer_set::BufferSet;
use atri_core::midi::event::ScheduledMidiEvent;
use atri_core::midi::note::MidiNote;
use atri_core::midi::sequencer::MidiSequencer;

use super::processor::{Gain, Pan, Processor};

pub struct Route {
    pub id: u32,
    pub name: String,
    pub processors: Vec<Arc<Mutex<dyn Processor>>>,
    pub gain: Gain,
    pub pan: Pan,
    pub sequencer: MidiSequencer,
    pub solo: bool,
    pub mute: bool,
}

impl Route {
    pub fn new(id: u32, name: String) -> Self {
        Self {
            id,
            name,
            processors: Vec::new(),
            gain: Gain::new(1.0),
            pan: Pan::new(),
            sequencer: MidiSequencer::new(),
            solo: false,
            mute: false,
        }
    }

    pub fn add_processor(&mut self, proc: Arc<Mutex<dyn Processor>>) {
        self.processors.push(proc);
    }

    pub fn set_notes(&mut self, notes: Vec<MidiNote>) {
        self.sequencer.set_notes(notes);
    }

    /// Process this route's chain into the given BufferSet.
    pub fn process(
        &mut self,
        bufs: &mut BufferSet,
        midi: &[ScheduledMidiEvent],
        start_sample: i64,
        end_sample: i64,
        speed: f64,
        nframes: usize,
    ) {
        if self.mute {
            bufs.silence(nframes);
            return;
        }

        // Run all plugins/processors in chain
        for proc in &self.processors {
            if let Ok(mut p) = proc.lock() {
                p.run(bufs, midi, start_sample, end_sample, speed, nframes, true);
            }
        }

        // Apply gain then pan
        self.gain
            .run(bufs, &[], start_sample, end_sample, speed, nframes, true);
        self.pan
            .run(bufs, &[], start_sample, end_sample, speed, nframes, true);
    }
}
