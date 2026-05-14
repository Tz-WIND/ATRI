use std::sync::{Arc, Mutex};
use atri_core::audio::buffer_set::BufferSet;
use atri_core::audio::buffer::AudioBuffer;
use atri_core::midi::note::MidiNote;
use atri_core::time::tempo::{Meter, Tempo};
use atri_core::time::tempo_map::{SwapLock, TempoMap};
use super::mixer::Mixer;
use super::route::Route;
use super::transport::Transport;

pub struct Session {
    pub routes: Vec<Arc<Mutex<Route>>>,
    pub tempo_map: SwapLock<TempoMap>,
    pub transport: Transport,
    pub sample_rate: u32,
    pub buffer_size: usize,
    pub mixer: Mixer,
    // Pre-allocated per-route buffers, reused each cycle.
    route_bufs: Vec<BufferSet>,
    master_buf: AudioBuffer,
}

impl Session {
    pub fn new(sample_rate: u32, buffer_size: usize) -> Self {
        let tempo_map = SwapLock::new(TempoMap::new(
            Tempo::new(120.0, 4),
            Meter::new(4, 4),
            sample_rate,
        ));
        Self {
            routes: Vec::new(),
            tempo_map,
            transport: Transport::new(),
            sample_rate,
            buffer_size,
            mixer: Mixer::new(),
            route_bufs: Vec::new(),
            master_buf: AudioBuffer::new(2, buffer_size),
        }
    }

    pub fn add_track(&mut self, name: String) -> u32 {
        let id = self.routes.len() as u32;
        self.routes.push(Arc::new(Mutex::new(Route::new(id, name))));
        self.route_bufs.push(BufferSet::new(1, 2, self.buffer_size));
        id
    }

    pub fn set_track_notes(&mut self, track_id: u32, notes: Vec<MidiNote>) {
        if let Some(route) = self.routes.get(track_id as usize) {
            if let Ok(mut r) = route.lock() {
                r.set_notes(notes);
            }
        }
    }

    pub fn set_track_volume(&mut self, track_id: u32, value: f32) {
        if let Some(route) = self.routes.get(track_id as usize) {
            if let Ok(mut r) = route.lock() {
                r.gain.set_value(value);
            }
        }
    }

    pub fn set_track_pan(&mut self, track_id: u32, value: f32) {
        if let Some(route) = self.routes.get(track_id as usize) {
            if let Ok(mut r) = route.lock() {
                r.pan.value = value.clamp(-1.0, 1.0);
            }
        }
    }

    /// Main processing callback — called each audio cycle.
    /// Returns the mixed stereo output as interleaved f32.
    pub fn process(&mut self, output: &mut [f32]) {
        let nframes = self.buffer_size;
        let speed = self.transport.speed;

        // Update transport
        let start_sample = self.transport.position;
        if self.transport.is_rolling() {
            self.transport.advance(nframes);
        }
        let end_sample = start_sample + nframes as i64;

        // Read tempo map snapshot (atomic load, no lock)
        let tempo_map = self.tempo_map.read();

        // Process each route
        let mut route_outputs: Vec<AudioBuffer> = Vec::with_capacity(self.routes.len());

        for (idx, route_arc) in self.routes.iter().enumerate() {
            if let Ok(mut route) = route_arc.lock() {
                // Clear route buffer
                self.route_bufs[idx].silence(nframes);

                // Get MIDI events for this cycle's beat range
                if self.transport.is_rolling() {
                    let start_beats = tempo_map.beats_at_sample(start_sample).to_beats_f64();
                    let end_beats = tempo_map.beats_at_sample(end_sample).to_beats_f64();
                    let _events = route.sequencer.get_events_in_range(
                        start_beats,
                        end_beats,
                        tempo_map,
                    );
                    // TODO: inject MIDI events into plugin at their sample offsets
                }

                // Run the route's processor chain
                route.process(
                    &mut self.route_bufs[idx],
                    start_sample,
                    end_sample,
                    speed,
                    nframes,
                );

                // Extract processed audio
                let buf = self.route_bufs[idx].get(0).cloned().unwrap_or_else(|| AudioBuffer::new(2, nframes));
                route_outputs.push(buf);
            }
        }

        // Mix to master
        self.master_buf.silence(nframes);
        self.mixer.sum(&route_outputs, &mut self.master_buf);

        // Output to interleaved stereo
        self.master_buf.to_interleaved(output, nframes);
    }
}
