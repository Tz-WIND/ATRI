use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use atri_core::audio::buffer::AudioBuffer;
use atri_core::audio::buffer_set::BufferSet;
use atri_core::midi::event::{MidiEvent, ScheduledMidiEvent};
use atri_core::midi::note::MidiNote;
use atri_core::time::tempo::{Meter, Tempo};
use atri_core::time::tempo_map::{SwapLock, TempoMap};

use super::mixer::Mixer;
use super::processor::Processor;
use super::route::Route;
use super::transport::Transport;

pub struct Session {
    pub routes: Vec<Arc<Mutex<Route>>>,
    pub tempo_map: SwapLock<TempoMap>,
    pub transport: Transport,
    pub sample_rate: u32,
    pub buffer_size: usize,
    pub mixer: Mixer,
    next_route_id: u32,
    route_indices: HashMap<u32, usize>,
    route_bufs: Vec<BufferSet>,
    midi_events: Vec<Vec<ScheduledMidiEvent>>,
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
            next_route_id: 0,
            route_indices: HashMap::new(),
            route_bufs: Vec::new(),
            midi_events: Vec::new(),
            master_buf: AudioBuffer::new(2, buffer_size),
        }
    }

    pub fn add_track(&mut self, name: String) -> u32 {
        let id = self.next_route_id;
        self.next_route_id = self.next_route_id.saturating_add(1);
        let index = self.routes.len();
        self.routes.push(Arc::new(Mutex::new(Route::new(id, name))));
        self.route_indices.insert(id, index);
        self.route_bufs.push(BufferSet::new(1, 2, self.buffer_size));
        self.midi_events.push(Vec::new());
        id
    }

    pub fn reconfigure(&mut self, sample_rate: u32, buffer_size: usize) {
        if self.sample_rate != sample_rate {
            let old_sample_rate = self.sample_rate;
            self.transport.position =
                rescale_sample_position(self.transport.position, old_sample_rate, sample_rate);
            self.transport.loop_start = self
                .transport
                .loop_start
                .map(|position| rescale_sample_position(position, old_sample_rate, sample_rate));
            self.transport.loop_end = self
                .transport
                .loop_end
                .map(|position| rescale_sample_position(position, old_sample_rate, sample_rate));
            self.sample_rate = sample_rate;
            self.tempo_map
                .update(|tempo_map| tempo_map.with_sample_rate(sample_rate));
        }

        if self.resize_buffers(buffer_size) {
            self.notify_processors_block_size(buffer_size);
        }
    }

    pub fn remove_track(&mut self, track_id: u32) -> bool {
        let Some(index) = self.route_index(track_id) else {
            return false;
        };
        self.routes.remove(index);
        self.route_bufs.remove(index);
        self.midi_events.remove(index);
        self.route_indices.remove(&track_id);
        for route_index in self.route_indices.values_mut() {
            if *route_index > index {
                *route_index -= 1;
            }
        }
        true
    }

    pub fn add_processor(&mut self, track_id: u32, processor: Arc<Mutex<dyn Processor>>) -> bool {
        let Some(route) = self.route(track_id) else {
            return false;
        };
        route
            .lock()
            .map(|mut route| route.add_processor(processor))
            .is_ok()
    }

    pub fn set_processor_slot(
        &mut self,
        track_id: u32,
        slot_index: usize,
        processor: Option<Arc<Mutex<dyn Processor>>>,
    ) -> bool {
        if let Some(processor) = &processor {
            if let Ok(mut processor) = processor.lock() {
                processor.set_block_size(self.buffer_size);
            }
        }
        self.with_route(track_id, |route| {
            route.set_processor_slot(slot_index, processor);
        })
    }

    pub fn clear_processor_slot(&mut self, track_id: u32, slot_index: usize) -> bool {
        self.with_route(track_id, |route| route.clear_processor_slot(slot_index))
    }

    pub fn processor_slot(
        &self,
        track_id: u32,
        slot_index: usize,
    ) -> Option<Arc<Mutex<dyn Processor>>> {
        let route = self.route(track_id)?;
        let route = route.lock().ok()?;
        route.processors.get(slot_index)?.as_ref().cloned()
    }

    pub fn set_track_notes(&mut self, track_id: u32, notes: Vec<MidiNote>) -> bool {
        self.set_track_midi(track_id, notes, Vec::new())
    }

    pub fn set_track_midi(
        &mut self,
        track_id: u32,
        notes: Vec<MidiNote>,
        events: Vec<MidiEvent>,
    ) -> bool {
        let capacity = notes.len() * 2 + events.len();
        let Some(index) = self.route_index(track_id) else {
            return false;
        };

        if let Ok(mut route) = self.routes[index].lock() {
            route.set_midi(notes, events);
            self.midi_events[index].reserve(capacity);
            return true;
        }

        false
    }

    pub fn set_track_volume(&mut self, track_id: u32, value: f32) -> bool {
        self.with_route(track_id, |route| route.gain.set_value(value))
    }

    pub fn set_track_pan(&mut self, track_id: u32, value: f32) -> bool {
        self.with_route(track_id, |route| route.pan.value = value.clamp(-1.0, 1.0))
    }

    pub fn set_track_mute(&mut self, track_id: u32, value: bool) -> bool {
        self.with_route(track_id, |route| route.mute = value)
    }

    pub fn set_track_solo(&mut self, track_id: u32, value: bool) -> bool {
        self.with_route(track_id, |route| route.solo = value)
    }

    /// Main processing callback. `output` must be interleaved stereo.
    pub fn process(&mut self, output: &mut [f32]) {
        let nframes = output.len() / 2;
        // The audio callback may see a different block size on some backends.
        // Keep buffer storage valid here, but leave processor block-size
        // notifications to the control path to avoid locking every processor
        // from the realtime render path.
        self.resize_buffers(nframes);

        let speed = self.transport.speed;
        let start_sample = self.transport.position;
        if self.transport.is_rolling() {
            self.transport.advance(nframes);
        }
        let end_sample = start_sample + nframes as i64;
        let tempo_map = self.tempo_map.read().clone();
        let any_solo = self
            .routes
            .iter()
            .any(|route| route.lock().map(|route| route.solo).unwrap_or(false));

        self.master_buf.silence(nframes);

        for (idx, route_arc) in self.routes.iter().enumerate() {
            let Ok(mut route) = route_arc.lock() else {
                continue;
            };

            if any_solo && !route.solo {
                continue;
            }

            self.route_bufs[idx].silence(nframes);
            if self.transport.is_rolling() {
                route.sequencer.collect_events_in_samples(
                    start_sample,
                    end_sample,
                    &tempo_map,
                    &mut self.midi_events[idx],
                );
            } else {
                self.midi_events[idx].clear();
            }

            route.process(
                &mut self.route_bufs[idx],
                &self.midi_events[idx],
                start_sample,
                end_sample,
                speed,
                nframes,
            );

            if let Some(buf) = self.route_bufs[idx].get(0) {
                self.mixer.add(buf, &mut self.master_buf, nframes);
            }
        }

        self.master_buf.to_interleaved(output, nframes);
    }

    fn route(&self, track_id: u32) -> Option<&Arc<Mutex<Route>>> {
        self.route_index(track_id)
            .and_then(|index| self.routes.get(index))
    }

    fn route_index(&self, track_id: u32) -> Option<usize> {
        self.route_indices.get(&track_id).copied()
    }

    fn with_route(&mut self, track_id: u32, f: impl FnOnce(&mut Route)) -> bool {
        let Some(index) = self.route_index(track_id) else {
            return false;
        };
        self.routes[index]
            .lock()
            .map(|mut route| f(&mut route))
            .is_ok()
    }

    fn resize_buffers(&mut self, nframes: usize) -> bool {
        if nframes == self.buffer_size {
            return false;
        }

        self.buffer_size = nframes;
        for bufs in &mut self.route_bufs {
            bufs.resize(nframes);
        }
        self.master_buf.resize(nframes);
        true
    }

    fn notify_processors_block_size(&mut self, nframes: usize) {
        for route in &self.routes {
            let Ok(route) = route.lock() else {
                continue;
            };
            for processor in route.processors.iter().flatten() {
                if let Ok(mut processor) = processor.lock() {
                    processor.set_block_size(nframes);
                }
            }
        }
    }
}

fn rescale_sample_position(position: i64, old_sample_rate: u32, new_sample_rate: u32) -> i64 {
    if old_sample_rate == 0 {
        return position;
    }

    let position = position as i128 * new_sample_rate as i128 / old_sample_rate as i128;
    position.clamp(i64::MIN as i128, i64::MAX as i128) as i64
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    #[test]
    fn track_ids_remain_stable_after_remove() {
        let mut session = Session::new(48_000, 128);
        let first = session.add_track("A".into());
        let second = session.add_track("B".into());

        assert!(session.remove_track(first));
        assert!(session.set_track_volume(second, 0.5));
        assert!(!session.set_track_volume(first, 0.5));
    }

    #[test]
    fn route_index_map_updates_after_middle_remove() {
        let mut session = Session::new(48_000, 128);
        let first = session.add_track("A".into());
        let second = session.add_track("B".into());
        let third = session.add_track("C".into());

        assert_eq!(session.route_index(first), Some(0));
        assert_eq!(session.route_index(second), Some(1));
        assert_eq!(session.route_index(third), Some(2));

        assert!(session.remove_track(second));

        assert_eq!(session.route_index(first), Some(0));
        assert_eq!(session.route_index(second), None);
        assert_eq!(session.route_index(third), Some(1));
        assert!(session.set_track_pan(third, 0.25));
        assert!(!session.set_track_mute(second, true));
    }

    #[test]
    fn process_accepts_variable_buffer_sizes() {
        let mut session = Session::new(48_000, 128);
        let mut output = vec![0.0; 512 * 2];
        session.process(&mut output);
        assert_eq!(session.buffer_size, 512);
    }

    #[test]
    fn process_resize_does_not_notify_all_processors() {
        let mut session = Session::new(48_000, 128);
        let track = session.add_track("Keys".into());
        let block_size_calls = Arc::new(AtomicUsize::new(0));

        assert!(session.set_processor_slot(
            track,
            0,
            Some(Arc::new(Mutex::new(CountingBlockSizeProcessor {
                calls: Arc::clone(&block_size_calls),
            }))),
        ));
        block_size_calls.store(0, Ordering::SeqCst);

        let mut output = vec![0.0; 512 * 2];
        session.process(&mut output);

        assert_eq!(session.buffer_size, 512);
        assert_eq!(block_size_calls.load(Ordering::SeqCst), 0);

        session.reconfigure(48_000, 256);

        assert_eq!(session.buffer_size, 256);
        assert_eq!(block_size_calls.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn reconfigure_preserves_session_state() {
        let mut session = Session::new(48_000, 128);
        let track = session.add_track("Keys".into());
        assert!(session.set_track_volume(track, 0.5));
        assert!(session.set_track_pan(track, 0.25));
        assert!(session.set_track_mute(track, true));
        assert!(session.set_track_solo(track, true));
        session.transport.seek(48_000);
        session.transport.loop_start = Some(24_000);
        session.transport.loop_end = Some(48_000);

        let beat_4 =
            atri_core::time::beats::Beats::from_ticks(4 * atri_core::time::beats::PPQN as i64);
        session
            .tempo_map
            .update(|tempo_map| tempo_map.with_tempo(Tempo::new(90.0, 4), beat_4));

        session.reconfigure(96_000, 256);

        assert_eq!(session.sample_rate, 96_000);
        assert_eq!(session.buffer_size, 256);
        assert_eq!(session.transport.position, 96_000);
        assert_eq!(session.transport.loop_start, Some(48_000));
        assert_eq!(session.transport.loop_end, Some(96_000));
        assert_eq!(session.tempo_map.read().sample_rate(), 96_000);
        assert_eq!(
            session.tempo_map.read().metric_at_beats(beat_4).tempo.bpm,
            90.0
        );
        assert_eq!(session.route_index(track), Some(0));
        assert_eq!(session.route_bufs[0].get(0).unwrap().capacity(), 256);
        assert_eq!(session.master_buf.capacity(), 256);

        let route = session.routes[0].lock().unwrap();
        assert_eq!(route.name, "Keys");
        assert_eq!(route.gain.target, 0.5);
        assert_eq!(route.pan.value, 0.25);
        assert!(route.mute);
        assert!(route.solo);
    }

    #[test]
    fn processor_slots_replace_and_clear_without_growing_chain() {
        let mut session = Session::new(48_000, 128);
        let track = session.add_track("Keys".into());

        assert!(session.set_processor_slot(
            track,
            0,
            Some(Arc::new(Mutex::new(TestProcessor::new("first")))),
        ));
        assert!(session.set_processor_slot(
            track,
            0,
            Some(Arc::new(Mutex::new(TestProcessor::new("second")))),
        ));
        assert!(session.set_processor_slot(
            track,
            2,
            Some(Arc::new(Mutex::new(TestProcessor::new("insert")))),
        ));
        assert!(session.clear_processor_slot(track, 2));

        let route = session.routes[0].lock().unwrap();
        assert_eq!(route.processors.len(), 3);
        assert!(route.processors[0].is_some());
        assert!(route.processors[1].is_none());
        assert!(route.processors[2].is_none());
    }

    struct TestProcessor {
        name: &'static str,
        active: bool,
    }

    impl TestProcessor {
        fn new(name: &'static str) -> Self {
            Self {
                name,
                active: false,
            }
        }
    }

    struct CountingBlockSizeProcessor {
        calls: Arc<AtomicUsize>,
    }

    impl Processor for CountingBlockSizeProcessor {
        fn name(&self) -> &str {
            "counting-block-size"
        }

        fn run(
            &mut self,
            _bufs: &mut BufferSet,
            _midi: &[ScheduledMidiEvent],
            _start_sample: i64,
            _end_sample: i64,
            _speed: f64,
            _nframes: usize,
            _result_required: bool,
        ) {
        }

        fn activate(&mut self) {}

        fn deactivate(&mut self) {}

        fn is_active(&self) -> bool {
            true
        }

        fn input_channels(&self) -> u16 {
            2
        }

        fn output_channels(&self) -> u16 {
            2
        }

        fn set_block_size(&mut self, _nframes: usize) {
            self.calls.fetch_add(1, Ordering::SeqCst);
        }
    }

    impl Processor for TestProcessor {
        fn name(&self) -> &str {
            self.name
        }

        fn run(
            &mut self,
            _bufs: &mut BufferSet,
            _midi: &[ScheduledMidiEvent],
            _start_sample: i64,
            _end_sample: i64,
            _speed: f64,
            _nframes: usize,
            _result_required: bool,
        ) {
        }

        fn activate(&mut self) {
            self.active = true;
        }

        fn deactivate(&mut self) {
            self.active = false;
        }

        fn is_active(&self) -> bool {
            self.active
        }

        fn input_channels(&self) -> u16 {
            2
        }

        fn output_channels(&self) -> u16 {
            2
        }
    }
}
