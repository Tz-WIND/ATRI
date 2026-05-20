use std::sync::{Arc, Mutex};

use atri_core::audio::buffer_set::BufferSet;
use atri_core::midi::event::{MidiEvent, ScheduledMidiEvent};
use atri_core::midi::note::MidiNote;
use atri_core::midi::sequencer::MidiSequencer;
use atri_core::time::tempo_map::TempoMap;

use super::audio_clip::{AudioClip, render_audio_clips};
use super::processor::{Gain, Pan, Processor};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RouteKind {
    Track,
    Bus,
}

#[derive(Debug, Clone, PartialEq)]
pub struct RouteSend {
    pub target_track_id: u32,
    pub level: f32,
    pub enabled: bool,
}

pub struct Route {
    pub id: u32,
    pub name: String,
    pub kind: RouteKind,
    pub output_track_id: Option<u32>,
    pub sends: Vec<RouteSend>,
    pub processors: Vec<Option<Arc<Mutex<dyn Processor>>>>,
    pub gain: Gain,
    pub pan: Pan,
    pub sequencer: MidiSequencer,
    pub audio_clips: Vec<AudioClip>,
    pub solo: bool,
    pub mute: bool,
}

impl Route {
    pub fn new(id: u32, name: String) -> Self {
        Self::new_with_kind(id, name, RouteKind::Track)
    }

    pub fn new_with_kind(id: u32, name: String, kind: RouteKind) -> Self {
        Self {
            id,
            name,
            kind,
            output_track_id: None,
            sends: Vec::new(),
            processors: Vec::new(),
            gain: Gain::new(1.0),
            pan: Pan::new(),
            sequencer: MidiSequencer::new(),
            audio_clips: Vec::new(),
            solo: false,
            mute: false,
        }
    }

    pub fn add_processor(&mut self, proc: Arc<Mutex<dyn Processor>>) {
        self.processors.push(Some(proc));
    }

    pub fn set_processor_slot(
        &mut self,
        slot_index: usize,
        proc: Option<Arc<Mutex<dyn Processor>>>,
    ) {
        if self.processors.len() <= slot_index {
            self.processors.resize_with(slot_index + 1, || None);
        }

        if let Some(old_proc) = self.processors[slot_index].take() {
            if let Ok(mut old_proc) = old_proc.lock() {
                old_proc.deactivate();
            }
        }

        self.processors[slot_index] = proc;
    }

    pub fn clear_processor_slot(&mut self, slot_index: usize) {
        self.set_processor_slot(slot_index, None);
    }

    pub fn set_notes(&mut self, notes: Vec<MidiNote>) {
        self.sequencer.set_notes(notes);
    }

    pub fn set_midi(&mut self, notes: Vec<MidiNote>, events: Vec<MidiEvent>) {
        self.sequencer.set_midi(notes, events);
    }

    pub fn set_audio_clips(&mut self, clips: Vec<AudioClip>) {
        self.audio_clips = clips;
    }

    pub fn audio_clip_count(&self) -> usize {
        self.audio_clips.len()
    }

    pub fn render_audio_clips(
        &self,
        bufs: &mut BufferSet,
        start_sample: i64,
        end_sample: i64,
        tempo_map: &TempoMap,
        nframes: usize,
    ) {
        let Some(buffer) = bufs.get_mut(0) else {
            return;
        };
        render_audio_clips(
            &self.audio_clips,
            buffer,
            start_sample,
            end_sample,
            tempo_map,
            nframes,
        );
    }

    pub fn signal_latency(&self) -> usize {
        self.processors
            .iter()
            .filter_map(|processor| processor.as_ref())
            .filter_map(|processor| processor.lock().ok())
            .filter(|processor| processor.is_active())
            .map(|processor| processor.signal_latency())
            .sum()
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
            let Some(proc) = proc else {
                continue;
            };
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
