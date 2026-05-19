use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use atri_core::audio::buffer::AudioBuffer;
use atri_core::audio::buffer_set::BufferSet;
use atri_core::midi::event::{MidiEvent, ScheduledMidiEvent};
use atri_core::midi::message::MidiMessage;
use atri_core::midi::note::MidiNote;
use atri_core::time::beats::Beats;
use atri_core::time::tempo::{Meter, Tempo};
use atri_core::time::tempo_map::{SwapLock, TempoMap};

use super::audio_clip::AudioClip;
use super::mixer::Mixer;
use super::processor::Processor;
use super::route::Route;
use super::transport::Transport;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AutomationCurve {
    Linear,
    Hold,
}

#[derive(Debug, Clone, PartialEq)]
pub struct AutomationPoint {
    pub beat: f64,
    pub value: f32,
    pub curve: AutomationCurve,
}

#[derive(Debug, Clone, PartialEq)]
pub enum AutomationTarget {
    PluginParameter {
        track_id: u32,
        slot_index: usize,
        param_index: u32,
    },
    TrackVolume {
        track_id: u32,
    },
    TrackPan {
        track_id: u32,
    },
    TempoBpm,
    TimeSignatureNumerator,
}

#[derive(Debug, Clone, PartialEq)]
pub struct AutomationLane {
    pub target: AutomationTarget,
    pub points: Vec<AutomationPoint>,
    pub muted: bool,
}

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
    route_delay_lines: Vec<RouteDelayLine>,
    midi_events: Vec<Vec<ScheduledMidiEvent>>,
    automation_lanes: Vec<AutomationLane>,
    master_buf: AudioBuffer,
}

#[derive(Default)]
struct RouteDelayLine {
    delay_samples: usize,
    channels: u16,
    write_pos: usize,
    samples: Vec<Vec<f32>>,
}

impl RouteDelayLine {
    fn process(&mut self, buffer: &mut AudioBuffer, nframes: usize, delay_samples: usize) {
        let nframes = nframes.min(buffer.capacity());
        if nframes == 0 {
            return;
        }
        if delay_samples == 0 {
            self.clear();
            return;
        }

        self.configure(buffer.channels(), delay_samples);
        let start_pos = self.write_pos;
        for channel_index in 0..usize::from(self.channels) {
            let mut pos = start_pos;
            let channel = buffer.channel_mut(channel_index as u16);
            let delay_channel = &mut self.samples[channel_index];
            for sample in channel.iter_mut().take(nframes) {
                let delayed = delay_channel[pos];
                delay_channel[pos] = *sample;
                *sample = delayed;
                pos += 1;
                if pos == self.delay_samples {
                    pos = 0;
                }
            }
        }
        self.write_pos = (start_pos + nframes) % self.delay_samples;
    }

    fn clear(&mut self) {
        self.delay_samples = 0;
        self.channels = 0;
        self.write_pos = 0;
        self.samples.clear();
    }

    fn configure(&mut self, channels: u16, delay_samples: usize) {
        if self.delay_samples == delay_samples && self.channels == channels {
            return;
        }
        self.delay_samples = delay_samples;
        self.channels = channels;
        self.write_pos = 0;
        self.samples = (0..usize::from(channels))
            .map(|_| vec![0.0; delay_samples])
            .collect();
    }
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
            route_delay_lines: Vec::new(),
            midi_events: Vec::new(),
            automation_lanes: Vec::new(),
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
        self.route_delay_lines.push(RouteDelayLine::default());
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
            self.notify_processors_sample_rate(f64::from(sample_rate));
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
        self.route_delay_lines.remove(index);
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
                processor.set_sample_rate(f64::from(self.sample_rate));
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

    pub fn set_track_audio_clips(&mut self, track_id: u32, clips: Vec<AudioClip>) -> bool {
        self.with_route(track_id, |route| route.set_audio_clips(clips))
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

    pub fn set_automation_lanes(&mut self, mut lanes: Vec<AutomationLane>) {
        for lane in &mut lanes {
            lane.points.sort_by(|a, b| {
                a.beat
                    .partial_cmp(&b.beat)
                    .unwrap_or(std::cmp::Ordering::Equal)
            });
        }
        self.automation_lanes = lanes;
    }

    pub fn automation_lane_count(&self) -> usize {
        self.automation_lanes.len()
    }

    /// Main processing callback. `output` must be interleaved stereo.
    pub fn process(&mut self, output: &mut [f32]) {
        let nframes = output.len() / 2;
        // The audio callback may see a different block size on some backends.
        // Keep buffer storage valid here, but leave processor block-size
        // notifications to the control path to avoid locking every processor
        // from the realtime render path.
        let resized = self.resize_buffers(nframes);
        if resized {
            log::debug!(
                "[session] buffer resized: nframes={}, new_buffer_size={}",
                nframes,
                self.buffer_size
            );
        }

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
        let route_latencies = self.route_mix_latencies(any_solo);
        let max_route_latency = route_latencies.iter().copied().max().unwrap_or(0);

        self.master_buf.silence(nframes);
        if self.transport.is_rolling() {
            self.apply_automation_lanes(start_sample, end_sample, &tempo_map, nframes);
        }

        for (idx, route_arc) in self.routes.iter().enumerate() {
            let Ok(mut route) = route_arc.lock() else {
                continue;
            };

            if route.mute || (any_solo && !route.solo) {
                self.route_bufs[idx].silence(nframes);
                if let Some(delay_line) = self.route_delay_lines.get_mut(idx) {
                    delay_line.clear();
                }
                continue;
            }

            self.route_bufs[idx].silence(nframes);
            if self.transport.is_rolling() {
                route.render_audio_clips(
                    &mut self.route_bufs[idx],
                    start_sample,
                    end_sample,
                    &tempo_map,
                    nframes,
                );
            }
            if self.transport.is_rolling() {
                route.sequencer.collect_events_in_samples(
                    start_sample,
                    end_sample,
                    &tempo_map,
                    &mut self.midi_events[idx],
                );
            } else {
                // On pause/stop, inject AllNotesOff so synth voices release
                // instead of sustaining forever mid-note.
                self.midi_events[idx].clear();
                self.midi_events[idx].push(ScheduledMidiEvent::new(
                    MidiEvent::new(0, MidiMessage::AllNotesOff { channel: 0 }),
                    0,
                ));
            }

            route.process(
                &mut self.route_bufs[idx],
                &self.midi_events[idx],
                start_sample,
                end_sample,
                speed,
                nframes,
            );

            let compensation =
                max_route_latency.saturating_sub(route_latencies.get(idx).copied().unwrap_or(0));
            if let Some(buf) = self.route_bufs[idx].get_mut(0) {
                if let Some(delay_line) = self.route_delay_lines.get_mut(idx) {
                    delay_line.process(buf, nframes, compensation);
                }
            }

            if let Some(buf) = self.route_bufs[idx].get(0) {
                self.mixer.add(buf, &mut self.master_buf, nframes);
            }
        }

        // Detailed MIDI log: print every block that has events.
        {
            let total_midi: usize = self.midi_events.iter().map(|v| v.len()).sum();
            if total_midi > 0 {
                let pos_secs = self.transport.position as f64 / self.sample_rate as f64;
                let mut details = String::new();
                for (idx, events) in self.midi_events.iter().enumerate() {
                    if events.is_empty() {
                        continue;
                    }
                    use std::fmt::Write;
                    let _ = write!(&mut details, " t{idx}=[");
                    for (ei, ev) in events.iter().enumerate() {
                        let _ = write!(
                            &mut details,
                            "{}{:?}@{}",
                            if ei > 0 { ", " } else { "" },
                            ev.event.message,
                            ev.offset
                        );
                    }
                    let _ = write!(&mut details, "]");
                }
                log::debug!(
                    "[session] t={pos_secs:.2}s pos={} ev={}{details}",
                    self.transport.position,
                    total_midi,
                );
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

    fn route_mix_latencies(&self, any_solo: bool) -> Vec<usize> {
        self.routes
            .iter()
            .map(|route| {
                let Ok(route) = route.lock() else {
                    return 0;
                };
                if route.mute || (any_solo && !route.solo) {
                    return 0;
                }
                route.signal_latency()
            })
            .collect()
    }

    fn apply_automation_lanes(
        &mut self,
        start_sample: i64,
        end_sample: i64,
        tempo_map: &TempoMap,
        nframes: usize,
    ) {
        let lanes = self.automation_lanes.clone();
        for lane in lanes {
            if lane.muted {
                continue;
            }
            let events = automation_events_in_block(&lane, start_sample, end_sample, tempo_map);
            if events.is_empty() {
                continue;
            }
            match lane.target {
                AutomationTarget::PluginParameter {
                    track_id,
                    slot_index,
                    param_index,
                } => {
                    let Some(processor) = self.processor_slot(track_id, slot_index) else {
                        continue;
                    };
                    let Ok(mut processor) = processor.lock() else {
                        continue;
                    };
                    for (sample_offset, value, _beat) in events {
                        let offset = sample_offset.min(nframes.saturating_sub(1));
                        let _ = processor.set_parameter_at_sample(param_index, offset, value);
                    }
                }
                AutomationTarget::TrackVolume { track_id } => {
                    for (_sample_offset, value, _beat) in events {
                        let _ = self.set_track_volume(track_id, value);
                    }
                }
                AutomationTarget::TrackPan { track_id } => {
                    for (_sample_offset, value, _beat) in events {
                        let _ = self.set_track_pan(track_id, value);
                    }
                }
                AutomationTarget::TempoBpm => {
                    for (_sample_offset, value, beat) in events {
                        let bpm = f64::from(value).clamp(1.0, 999.0);
                        let at = Beats::from_beats(beat.max(0.0));
                        self.tempo_map.update(|tempo_map| {
                            let metric = tempo_map.metric_at_beats(at);
                            tempo_map.with_tempo(Tempo::new(bpm, metric.tempo.note_type), at)
                        });
                    }
                }
                AutomationTarget::TimeSignatureNumerator => {
                    for (_sample_offset, value, beat) in events {
                        let numerator = value.round().clamp(1.0, 255.0) as u8;
                        let at = Beats::from_beats(beat.max(0.0));
                        self.tempo_map.update(|tempo_map| {
                            let metric = tempo_map.metric_at_beats(at);
                            tempo_map.with_meter(Meter::new(numerator, metric.meter.denom), at)
                        });
                    }
                }
            }
        }
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

    fn notify_processors_sample_rate(&mut self, sample_rate: f64) {
        for route in &self.routes {
            let Ok(route) = route.lock() else {
                continue;
            };
            for processor in route.processors.iter().flatten() {
                if let Ok(mut processor) = processor.lock() {
                    processor.set_sample_rate(sample_rate);
                }
            }
        }
    }
}

fn automation_events_in_block(
    lane: &AutomationLane,
    start_sample: i64,
    end_sample: i64,
    tempo_map: &TempoMap,
) -> Vec<(usize, f32, f64)> {
    let mut events = Vec::new();
    for point in &lane.points {
        let point_sample = tempo_map.sample_at_beats(atri_core::time::beats::Beats::from_beats(
            point.beat.max(0.0),
        ));
        if point_sample < start_sample || point_sample >= end_sample {
            continue;
        }
        events.push((
            (point_sample - start_sample) as usize,
            point.value,
            point.beat,
        ));
    }
    events
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

    #[derive(Default)]
    struct RecordingParamProcessor {
        changes: Arc<Mutex<Vec<(u32, usize, f32)>>>,
    }

    impl Processor for RecordingParamProcessor {
        fn name(&self) -> &str {
            "recording-param"
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

        fn set_parameter_at_sample(
            &mut self,
            index: u32,
            sample_offset: usize,
            value: f32,
        ) -> Result<(), String> {
            self.changes
                .lock()
                .unwrap()
                .push((index, sample_offset, value));
            Ok(())
        }
    }

    #[test]
    fn automation_lanes_emit_plugin_parameter_changes_at_sample_offsets() {
        let mut session = Session::new(48_000, 128);
        let track_id = session.add_track("Automated".to_string());
        let changes = Arc::new(Mutex::new(Vec::new()));
        let processor = RecordingParamProcessor {
            changes: Arc::clone(&changes),
        };
        assert!(session.set_processor_slot(track_id, 0, Some(Arc::new(Mutex::new(processor)))));

        session.set_automation_lanes(vec![AutomationLane {
            target: AutomationTarget::PluginParameter {
                track_id,
                slot_index: 0,
                param_index: 3,
            },
            points: vec![
                AutomationPoint {
                    beat: 0.0,
                    value: 0.2,
                    curve: AutomationCurve::Linear,
                },
                AutomationPoint {
                    beat: 8.0 / atri_core::time::beats::PPQN as f64,
                    value: 0.8,
                    curve: AutomationCurve::Linear,
                },
            ],
            muted: false,
        }]);

        session.transport.play();
        let mut output = vec![0.0; 256];
        session.process(&mut output);

        assert_eq!(*changes.lock().unwrap(), vec![(3, 0, 0.2), (3, 100, 0.8)]);
    }

    #[test]
    fn automation_lanes_update_tempo_and_meter_targets() {
        let mut session = Session::new(48_000, 128);
        session.set_automation_lanes(vec![
            AutomationLane {
                target: AutomationTarget::TempoBpm,
                points: vec![AutomationPoint {
                    beat: 0.0,
                    value: 132.0,
                    curve: AutomationCurve::Linear,
                }],
                muted: false,
            },
            AutomationLane {
                target: AutomationTarget::TimeSignatureNumerator,
                points: vec![AutomationPoint {
                    beat: 0.0,
                    value: 7.6,
                    curve: AutomationCurve::Linear,
                }],
                muted: false,
            },
        ]);

        session.transport.play();
        let mut output = vec![0.0; 256];
        session.process(&mut output);

        let tempo_map = session.tempo_map.read();
        assert_eq!(tempo_map.current_tempo().bpm, 132.0);
        assert_eq!(tempo_map.current_meter().num, 8);
        assert_eq!(tempo_map.current_meter().denom, 4);
    }

    #[test]
    fn muted_automation_lanes_do_not_emit_changes() {
        let mut session = Session::new(48_000, 128);
        let track_id = session.add_track("Muted Automation".to_string());
        session.set_automation_lanes(vec![AutomationLane {
            target: AutomationTarget::TrackVolume { track_id },
            points: vec![AutomationPoint {
                beat: 0.0,
                value: 0.25,
                curve: AutomationCurve::Linear,
            }],
            muted: true,
        }]);

        session.transport.play();
        let mut output = vec![0.0; 256];
        session.process(&mut output);

        let route = session.routes[0].lock().unwrap();
        assert_eq!(route.gain.target, 1.0);
    }

    #[test]
    fn pdc_delays_lower_latency_routes_to_match_slowest_route() {
        let mut session = Session::new(48_000, 16);
        let dry_track = session.add_track("Dry".into());
        let latent_track = session.add_track("Latent".into());

        assert!(session.set_processor_slot(
            dry_track,
            0,
            Some(Arc::new(Mutex::new(PdcImpulseProcessor::new(0, 0.4)))),
        ));
        assert!(session.set_processor_slot(
            latent_track,
            0,
            Some(Arc::new(Mutex::new(PdcImpulseProcessor::new(2, 0.4)))),
        ));

        let mut output = vec![0.0; 16 * 2];
        session.process(&mut output);

        let expected = 2.0 * 0.4 * std::f32::consts::FRAC_PI_4.cos();
        assert!(output[0].abs() < 0.0001);
        assert!((output[4] - expected).abs() < 0.0001);
        assert!((output[5] - expected).abs() < 0.0001);
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

    struct PdcImpulseProcessor {
        latency: usize,
        amplitude: f32,
        emitted: bool,
    }

    impl PdcImpulseProcessor {
        fn new(latency: usize, amplitude: f32) -> Self {
            Self {
                latency,
                amplitude,
                emitted: false,
            }
        }
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

    impl Processor for PdcImpulseProcessor {
        fn name(&self) -> &str {
            "pdc-impulse"
        }

        fn run(
            &mut self,
            bufs: &mut BufferSet,
            _midi: &[ScheduledMidiEvent],
            _start_sample: i64,
            _end_sample: i64,
            _speed: f64,
            nframes: usize,
            _result_required: bool,
        ) {
            if self.emitted || self.latency >= nframes {
                return;
            }

            let Some(buffer) = bufs.get_mut(0) else {
                return;
            };
            for channel in 0..buffer.channels() {
                buffer.channel_mut(channel)[self.latency] += self.amplitude;
            }
            self.emitted = true;
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

        fn signal_latency(&self) -> usize {
            self.latency
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

    // ── Full pipeline frequency tests ──

    use crate::plugin_proc::PluginInsert;
    use crate::synth::BasicSynth;

    /// Count zero-crossings in a slice — each crossing = ½ cycle.
    fn count_zero_crossings(samples: &[f32]) -> usize {
        samples
            .windows(2)
            .filter(|w| w[0].signum() != w[1].signum() && w[0] != 0.0)
            .count()
    }

    /// Run the session for `total_samples` frames in blocks of `block_size`,
    /// return (channel 0 samples, channel 1 samples).
    fn render_session(
        session: &mut Session,
        total_samples: usize,
        block_size: usize,
    ) -> (Vec<f32>, Vec<f32>) {
        let mut left = Vec::with_capacity(total_samples);
        let mut right = Vec::with_capacity(total_samples);
        let mut remaining = total_samples;
        while remaining > 0 {
            let nframes = block_size.min(remaining);
            let mut output = vec![0.0f32; nframes * 2];
            session.process(&mut output);
            for i in 0..nframes {
                left.push(output[i * 2]);
                right.push(output[i * 2 + 1]);
            }
            remaining -= nframes;
        }
        (left, right)
    }

    #[test]
    fn session_basic_synth_a4_440hz_across_full_note() {
        let sr = 48_000;
        let mut session = Session::new(sr, 256);

        let track_id = session.add_track("Synth".into());
        let mut plugin = PluginInsert::new(Box::new(BasicSynth::new(sr)));
        plugin.activate();
        assert!(session.set_processor_slot(track_id, 0, Some(Arc::new(Mutex::new(plugin))),));

        // A4 = 440 Hz, note starting at beat 0 with duration 4 beats (2 seconds at 120bpm).
        // Process 1 second (48000 samples) so the note plays throughout the entire render.
        session.set_track_notes(track_id, vec![MidiNote::new(69, 0.0, 4.0, 100)]);

        session.transport.play();

        // Process 1 second of audio (48k samples) in blocks of 256
        let total_samples = 48_000;
        let (left, _right) = render_session(&mut session, total_samples, 256);

        // Verify audio is present (non-silence)
        let energy: f32 = left.iter().map(|s| s.abs()).sum();
        assert!(energy > 0.0, "no audio output from session pipeline");

        // Split into 4 quarters and check frequency consistency.
        // A4 = 440 Hz at 48 kHz → 440/48000 ≈ 0.00917 cycles/sample
        // In 12000 samples (0.25s): ~110 cycles → ~220 zero crossings.
        let quarter = total_samples / 4;
        let zc_q1 = count_zero_crossings(&left[0..quarter]);
        let zc_q2 = count_zero_crossings(&left[quarter..quarter * 2]);
        let zc_q3 = count_zero_crossings(&left[quarter * 2..quarter * 3]);
        let zc_q4 = count_zero_crossings(&left[quarter * 3..]);

        // Expected: ~220 crossings per quarter (440 Hz * 0.25s * 2 crossings/cycle)
        // Octave down (220 Hz) would give ~110 crossings.
        // The bug reportedly manifests at 50% note duration (midpoint).
        let min_expected = 160;
        let max_expected = 280;
        for (label, zc) in [("Q1", zc_q1), ("Q2", zc_q2), ("Q3", zc_q3), ("Q4", zc_q4)] {
            assert!(
                zc >= min_expected && zc <= max_expected,
                "{label} zero-crossings {zc} outside expected [{min_expected}, {max_expected}] — \
                 possible octave-down or frequency artifact"
            );
        }

        // Stricter: ratio between adjacent quarters should not halve (octave down).
        let ratios = [
            ("Q2/Q1", zc_q2 as f64 / zc_q1.max(1) as f64),
            ("Q3/Q2", zc_q3 as f64 / zc_q2.max(1) as f64),
            ("Q4/Q3", zc_q4 as f64 / zc_q3.max(1) as f64),
        ];
        for (label, ratio) in ratios {
            assert!(
                ratio > 0.5 && ratio < 2.0,
                "{label} frequency ratio {ratio:.2} out of range — \
                 possible octave jump between quarters"
            );
        }
    }

    #[test]
    fn session_note_on_triggers_exactly_once_per_note() {
        // Verify the sequencer doesn't generate duplicate NoteOn events
        // which could cause phasing/beating artifacts.
        let sr = 48_000;
        let mut session = Session::new(sr, 256);

        let track_id = session.add_track("Synth".into());
        let mut plugin = PluginInsert::new(Box::new(BasicSynth::new(sr)));
        plugin.activate();
        assert!(session.set_processor_slot(track_id, 0, Some(Arc::new(Mutex::new(plugin))),));

        // Single note
        session.set_track_notes(track_id, vec![MidiNote::new(69, 1.0, 2.0, 100)]);

        session.transport.play();

        // Process the full note + some extra
        let total_samples = 60_000; // slightly more than 1 second
        let (left, _right) = render_session(&mut session, total_samples, 256);

        // Find where audio starts and stops by threshold
        let threshold = 0.001;
        let first_nonzero = left.iter().position(|s| s.abs() > threshold);
        let last_nonzero = left.iter().rposition(|s| s.abs() > threshold);

        assert!(
            first_nonzero.is_some(),
            "audio should start when note triggers"
        );

        let start = first_nonzero.unwrap();
        let end = last_nonzero.unwrap();

        // Note at beat 1.0 with 120bpm 4/4 = 2 seconds per bar → beat 1.0 = 0.5 bar
        // Actually, beat 1.0 = the start of the timeline. At 120bpm:
        // 1 beat = 0.5 seconds = 24000 samples at 48kHz.
        // So the note starts at sample 24000 and ends at sample 72000.
        // We're only processing 60000 samples, so the note should still be playing.
        // The first non-zero should be around sample 24000.
        let expected_start = 24_000;
        let start_tolerance = 512; // within ~1 buffer
        assert!(
            (start as i64 - expected_start as i64).abs() < start_tolerance,
            "note started at sample {start}, expected ~{expected_start}"
        );

        // The last sample should be near the end of our render (note still playing)
        assert!(
            end > total_samples - 1000,
            "note should still be playing at the end of render, \
             last audio at sample {end} of {total_samples}"
        );
    }

    #[test]
    fn session_handles_variable_block_sizes() {
        // Simulate CPAL/WASAPI varying buffer sizes between callbacks.
        // This could trigger resize and expose buffer corruption or stale data.
        let sr = 48_000;
        let mut session = Session::new(sr, 256);

        let track_id = session.add_track("Synth".into());
        let mut plugin = PluginInsert::new(Box::new(BasicSynth::new(sr)));
        plugin.activate();
        assert!(session.set_processor_slot(track_id, 0, Some(Arc::new(Mutex::new(plugin))),));

        session.set_track_notes(track_id, vec![MidiNote::new(69, 0.0, 4.0, 100)]);
        session.transport.play();

        // Varying block sizes: 128, 256, 512, 192, 320, 64, 448
        let block_sizes = [128, 256, 512, 192, 320, 64, 448];
        let total_samples = 48_000;
        let mut left = Vec::with_capacity(total_samples);
        let mut remaining = total_samples;
        let mut size_idx = 0;

        while remaining > 0 {
            let nframes = block_sizes[size_idx % block_sizes.len()].min(remaining);
            let mut output = vec![0.0f32; nframes * 2];
            session.process(&mut output);
            for i in 0..nframes {
                left.push(output[i * 2]);
            }
            remaining -= nframes;
            size_idx += 1;
        }

        // Verify audio output
        let energy: f32 = left.iter().map(|s| s.abs()).sum();
        assert!(energy > 0.0, "no audio after variable block sizes");

        // Check frequency consistency across quarters
        let quarter = total_samples / 4;
        for (label, segment) in [
            ("Q1", &left[0..quarter]),
            ("Q2", &left[quarter..quarter * 2]),
            ("Q3", &left[quarter * 2..quarter * 3]),
            ("Q4", &left[quarter * 3..]),
        ] {
            let zc = count_zero_crossings(segment);
            assert!(
                zc >= 160 && zc <= 280,
                "{label} zero-crossings {zc} out of range with variable block sizes"
            );
        }

        // Verify no duplicate adjacent samples (sign of buffer corruption)
        let duplicates = left
            .windows(2)
            .filter(|w| (w[0] - w[1]).abs() < f32::EPSILON)
            .count();
        assert!(
            duplicates < total_samples / 10,
            "found {duplicates} duplicate adjacent samples — possible buffer corruption"
        );
    }

    #[test]
    fn session_96000hz_with_cpal_like_672_buffer() {
        // Reproduce the exact conditions of the user's CPAL/WASAPI setup:
        // 96000 Hz sample rate, 672-sample buffer, A4=440Hz note.
        let sr = 96_000;
        let block_size = 672;
        let mut session = Session::new(sr, block_size);

        let track_id = session.add_track("Synth".into());
        let mut plugin = PluginInsert::new(Box::new(BasicSynth::new(sr)));
        plugin.activate();
        assert!(session.set_processor_slot(track_id, 0, Some(Arc::new(Mutex::new(plugin))),));

        // A4=440Hz from beat 0, duration 2 beats (1 second at 120bpm = 96000 samples)
        session.set_track_notes(track_id, vec![MidiNote::new(69, 0.0, 2.0, 100)]);
        session.transport.play();

        // Process 1 second (96000 samples) in 672-sample blocks.
        // 96000 / 672 = 142.86 blocks → 143 blocks, 96096 samples total.
        let total_samples = sr as usize;
        let (left, _right) = render_session(&mut session, total_samples, block_size);

        let energy: f32 = left.iter().map(|s| s.abs()).sum();
        assert!(
            energy > 0.0,
            "no audio output at 96kHz with 672-sample blocks"
        );

        // Split into 4 quarters, check frequency.
        // A4=440Hz at 96kHz: 24000 samples per quarter, ~110 cycles → ~220 zero-crossings.
        let quarter = total_samples / 4;
        let min_expected = 160;
        let max_expected = 280;
        for (label, segment) in [
            ("Q1", &left[0..quarter]),
            ("Q2", &left[quarter..quarter * 2]),
            ("Q3", &left[quarter * 2..quarter * 3]),
            ("Q4", &left[quarter * 3..]),
        ] {
            let zc = count_zero_crossings(segment);
            assert!(
                zc >= min_expected && zc <= max_expected,
                "[96kHz] {label} zero-crossings {zc} outside [{min_expected}, {max_expected}] — \
                 possible octave-down or frequency artifact at 96kHz"
            );
        }

        // Stricter: ratio between adjacent quarters.
        let zc_q1 = count_zero_crossings(&left[0..quarter]);
        let zc_q2 = count_zero_crossings(&left[quarter..quarter * 2]);
        let zc_q3 = count_zero_crossings(&left[quarter * 2..quarter * 3]);
        let zc_q4 = count_zero_crossings(&left[quarter * 3..]);
        let ratios = [
            ("Q2/Q1", zc_q2 as f64 / zc_q1.max(1) as f64),
            ("Q3/Q2", zc_q3 as f64 / zc_q2.max(1) as f64),
            ("Q4/Q3", zc_q4 as f64 / zc_q3.max(1) as f64),
        ];
        for (label, ratio) in ratios {
            assert!(
                ratio > 0.5 && ratio < 2.0,
                "[96kHz] {label} ratio {ratio:.2} — possible octave jump"
            );
        }

        // Check for buffer corruption (duplicate adjacent samples).
        let duplicates = left
            .windows(2)
            .filter(|w| (w[0] - w[1]).abs() < f32::EPSILON)
            .count();
        assert!(
            duplicates < total_samples / 10,
            "[96kHz] found {duplicates} duplicate adjacent samples — buffer corruption?"
        );
    }
}
