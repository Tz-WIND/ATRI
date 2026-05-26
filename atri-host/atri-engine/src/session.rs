use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};

use atri_core::audio::buffer::AudioBuffer;
use atri_core::audio::buffer_set::BufferSet;
use atri_core::midi::event::{MidiEvent, ScheduledMidiEvent};
use atri_core::midi::message::MidiMessage;
use atri_core::midi::note::MidiNote;
use atri_core::plugin::{CapturedPluginParameterEdit, PluginParameterInfo};
use atri_core::time::beats::Beats;
use atri_core::time::tempo::{Meter, Tempo};
use atri_core::time::tempo_map::{SwapLock, TempoMap};

use super::audio_clip::AudioClip;
use super::mixer::Mixer;
use super::processor::{GainState, Processor};
use super::route::{Route, RouteKind, RouteSend};
use super::transport::Transport;

#[cfg(test)]
use std::alloc::{GlobalAlloc, Layout, System};
#[cfg(test)]
use std::cell::Cell;

#[cfg(test)]
struct CountingAllocator;

#[cfg(test)]
thread_local! {
    static TRACK_ALLOCATIONS: Cell<bool> = const { Cell::new(false) };
    static ALLOCATION_COUNT: Cell<usize> = const { Cell::new(0) };
}

#[cfg(test)]
unsafe impl GlobalAlloc for CountingAllocator {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let ptr = unsafe { System.alloc(layout) };
        if TRACK_ALLOCATIONS.with(Cell::get) {
            ALLOCATION_COUNT.with(|count| count.set(count.get().saturating_add(1)));
        }
        ptr
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        unsafe { System.dealloc(ptr, layout) };
    }
}

#[cfg(test)]
#[global_allocator]
static GLOBAL_ALLOCATOR: CountingAllocator = CountingAllocator;

#[cfg(test)]
fn reset_allocation_count() {
    TRACK_ALLOCATIONS.with(|track| track.set(false));
    ALLOCATION_COUNT.with(|count| count.set(0));
    TRACK_ALLOCATIONS.with(|track| track.set(true));
}

#[cfg(test)]
fn stop_allocation_count() -> usize {
    TRACK_ALLOCATIONS.with(|track| track.set(false));
    ALLOCATION_COUNT.with(Cell::get)
}

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

#[derive(Debug, Clone, Copy, PartialEq)]
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

#[derive(Debug, Clone, PartialEq)]
pub struct RouteSnapshot {
    pub id: u32,
    pub name: String,
    pub kind: RouteKind,
    pub output_track_id: Option<u32>,
    pub sends: Vec<RouteSend>,
    pub volume: f32,
    pub volume_target: f32,
    pub pan: f32,
    pub mute: bool,
    pub solo: bool,
    pub note_count: usize,
    pub midi_event_count: usize,
    pub audio_clip_count: usize,
    pub processors: Vec<String>,
    pub processor_slots: Vec<Option<String>>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct CapturedRouteParameterEdit {
    pub track_id: u32,
    pub slot_index: usize,
    pub plugin_name: String,
    pub parameter: Option<PluginParameterInfo>,
    pub edit: CapturedPluginParameterEdit,
}

#[derive(Clone)]
pub struct SessionRenderState {
    transport_state: super::transport::TransportState,
    transport_position: i64,
    transport_speed: f64,
    loop_start: Option<i64>,
    loop_end: Option<i64>,
    tempo_map: TempoMap,
    routes: Vec<RouteRenderState>,
    processors: Vec<ProcessorRenderState>,
    route_delay_lines: Vec<RouteDelayLine>,
}

#[derive(Clone)]
struct RouteRenderState {
    id: u32,
    gain: GainState,
    pan: f32,
    solo: bool,
    mute: bool,
}

#[derive(Clone)]
struct ProcessorRenderState {
    track_id: u32,
    slot_index: usize,
    state_chunk: Option<Vec<u8>>,
    parameters: Vec<(u32, f32)>,
}

pub struct Session {
    routes: Vec<Arc<Mutex<Route>>>,
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
    automation_event_scratch: Vec<AutomationEvent>,
    route_topology: RouteTopology,
    route_latencies_scratch: Vec<usize>,
    solo_indices_scratch: Vec<usize>,
    master_buf: AudioBuffer,
}

#[derive(Default)]
struct RouteTopology {
    render_order: Vec<usize>,
    output_indices: Vec<Option<usize>>,
    sends: Vec<Vec<CachedRouteSend>>,
    reaches: Vec<bool>,
    route_count: usize,
}

impl RouteTopology {
    fn route_reaches(&self, from: usize, to: usize) -> bool {
        from < self.route_count
            && to < self.route_count
            && self.reaches[from * self.route_count + to]
    }
}

#[derive(Clone, Copy)]
struct CachedRouteSend {
    target_index: usize,
    level: f32,
}

#[derive(Clone, Copy)]
struct AutomationEvent {
    sample_offset: usize,
    value: f32,
    beat: f64,
}

#[derive(Default, Clone)]
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
            automation_event_scratch: Vec::new(),
            route_topology: RouteTopology::default(),
            route_latencies_scratch: Vec::new(),
            solo_indices_scratch: Vec::new(),
            master_buf: AudioBuffer::new(2, buffer_size),
        }
    }

    pub fn add_track(&mut self, name: String) -> u32 {
        self.add_route(name, RouteKind::Track)
    }

    pub fn add_bus(&mut self, name: String) -> u32 {
        self.add_route(name, RouteKind::Bus)
    }

    fn add_route(&mut self, name: String, kind: RouteKind) -> u32 {
        let id = self.next_route_id;
        self.next_route_id = self.next_route_id.saturating_add(1);
        let index = self.routes.len();
        self.routes
            .push(Arc::new(Mutex::new(Route::new_with_kind(id, name, kind))));
        self.route_indices.insert(id, index);
        self.route_bufs.push(BufferSet::new(1, 2, self.buffer_size));
        self.route_delay_lines.push(RouteDelayLine::default());
        self.midi_events.push(Vec::new());
        self.route_latencies_scratch.push(0);
        ensure_vec_capacity(&mut self.solo_indices_scratch, self.routes.len());
        self.rebuild_route_topology();
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
        for route in &self.routes {
            if let Ok(mut route) = route.lock() {
                route.clear_output_if_target(track_id);
                route.retain_sends_not_targeting(track_id);
            }
        }
        self.route_latencies_scratch.truncate(self.routes.len());
        self.rebuild_route_topology();
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

    pub fn set_route_kind(&mut self, track_id: u32, kind: RouteKind) -> bool {
        self.with_route(track_id, |route| route.kind = kind)
    }

    pub fn set_route_output(&mut self, track_id: u32, output_track_id: Option<u32>) -> bool {
        if !self.route_output_is_valid(track_id, output_track_id) {
            return false;
        }
        let updated = self.with_route(track_id, |route| route.set_output_track_id(output_track_id));
        if updated {
            self.rebuild_route_topology();
        }
        updated
    }

    pub fn set_route_config(
        &mut self,
        track_id: u32,
        kind: Option<RouteKind>,
        output_track_id: Option<u32>,
    ) -> bool {
        if !self.route_output_is_valid(track_id, output_track_id) {
            return false;
        }
        let Some(index) = self.route_index(track_id) else {
            return false;
        };
        let updated = self.routes[index]
            .lock()
            .map(|mut route| {
                if let Some(kind) = kind {
                    route.kind = kind;
                }
                route.set_output_track_id(output_track_id);
            })
            .is_ok();
        if updated {
            self.rebuild_route_topology();
        }
        updated
    }

    fn route_output_is_valid(&self, track_id: u32, output_track_id: Option<u32>) -> bool {
        if self.route_index(track_id).is_none() {
            return false;
        }
        if output_track_id == Some(track_id) {
            return false;
        }
        if let Some(output_id) = output_track_id {
            let Some(output_index) = self.route_index(output_id) else {
                return false;
            };
            let Ok(output_route) = self.routes[output_index].lock() else {
                return false;
            };
            if output_route.kind != RouteKind::Bus {
                return false;
            }
        }
        if output_track_id.is_some()
            && self.route_graph_has_cycle(Some((track_id, output_track_id)), None)
        {
            return false;
        }
        true
    }

    pub fn route_output(&self, track_id: u32) -> Option<Option<u32>> {
        let route = self.route(track_id)?;
        route.lock().ok().map(|route| route.output_track_id())
    }

    pub fn route_kind(&self, track_id: u32) -> Option<RouteKind> {
        let route = self.route(track_id)?;
        route.lock().ok().map(|route| route.kind)
    }

    pub fn set_route_sends(&mut self, track_id: u32, sends: Vec<RouteSend>) -> bool {
        if self.route_index(track_id).is_none() {
            return false;
        }

        let mut normalized = Vec::new();
        let mut seen_targets = HashSet::new();
        for send in sends {
            if send.target_track_id == track_id || !seen_targets.insert(send.target_track_id) {
                return false;
            }
            let Some(target_index) = self.route_index(send.target_track_id) else {
                return false;
            };
            let Ok(target_route) = self.routes[target_index].lock() else {
                return false;
            };
            if target_route.kind != RouteKind::Bus {
                return false;
            }
            normalized.push(RouteSend {
                target_track_id: send.target_track_id,
                level: send.level.clamp(0.0, 2.0),
                enabled: send.enabled,
            });
        }

        if self.route_graph_has_cycle(None, Some((track_id, normalized.as_slice()))) {
            return false;
        }

        let updated = self.with_route(track_id, |route| route.set_sends(normalized));
        if updated {
            self.rebuild_route_topology();
        }
        updated
    }

    pub fn route_sends(&self, track_id: u32) -> Option<Vec<RouteSend>> {
        let route = self.route(track_id)?;
        route.lock().ok().map(|route| route.sends().to_vec())
    }

    pub fn set_automation_lanes(&mut self, mut lanes: Vec<AutomationLane>) {
        for lane in &mut lanes {
            lane.points.sort_by(|a, b| {
                a.beat
                    .partial_cmp(&b.beat)
                    .unwrap_or(std::cmp::Ordering::Equal)
            });
        }
        let max_events = lanes
            .iter()
            .map(|lane| lane.points.len())
            .max()
            .unwrap_or(0);
        ensure_vec_capacity(&mut self.automation_event_scratch, max_events);
        self.automation_lanes = lanes;
    }

    pub fn automation_lane_count(&self) -> usize {
        self.automation_lanes.len()
    }

    pub fn route_snapshots(&self) -> Vec<RouteSnapshot> {
        self.routes
            .iter()
            .filter_map(|route| {
                let route = route.lock().ok()?;
                let mut processors = Vec::new();
                let mut processor_slots = Vec::with_capacity(route.processors.len());
                for processor in &route.processors {
                    let processor_name = processor.as_ref().and_then(|processor| {
                        processor
                            .lock()
                            .ok()
                            .map(|processor| processor.name().to_string())
                    });
                    if let Some(processor_name) = &processor_name {
                        processors.push(processor_name.clone());
                    }
                    processor_slots.push(processor_name);
                }

                Some(RouteSnapshot {
                    id: route.id,
                    name: route.name.clone(),
                    kind: route.kind,
                    output_track_id: route.output_track_id(),
                    sends: route.sends().to_vec(),
                    volume: route.gain.value,
                    volume_target: route.gain.target,
                    pan: route.pan.value,
                    mute: route.mute,
                    solo: route.solo,
                    note_count: route.sequencer.note_count(),
                    midi_event_count: route.sequencer.midi_event_count(),
                    audio_clip_count: route.audio_clip_count(),
                    processors,
                    processor_slots,
                })
            })
            .collect()
    }

    pub fn capture_render_state(&mut self) -> Result<SessionRenderState, String> {
        let mut routes = Vec::with_capacity(self.routes.len());
        let mut processors = Vec::new();

        for route_arc in &self.routes {
            let route = route_arc.lock().map_err(|_| {
                "failed to lock route for render-state capture (mutex poisoned)".to_string()
            })?;
            routes.push(RouteRenderState {
                id: route.id,
                gain: route.gain.capture_state(),
                pan: route.pan.value,
                solo: route.solo,
                mute: route.mute,
            });

            for (slot_index, processor) in route.processors.iter().enumerate() {
                let Some(processor) = processor else {
                    continue;
                };
                let mut processor = processor.lock().map_err(|_| {
                    format!(
                        "failed to lock processor for render-state capture (track {} slot {}, mutex poisoned)",
                        route.id, slot_index
                    )
                })?;
                let state_chunk = processor.get_state_chunk().ok();
                let parameter_count = processor.parameter_count();
                let mut parameters = Vec::with_capacity(parameter_count as usize);
                for index in 0..parameter_count {
                    if let Some(value) = processor.get_parameter(index) {
                        parameters.push((index, value));
                    }
                }
                processors.push(ProcessorRenderState {
                    track_id: route.id,
                    slot_index,
                    state_chunk,
                    parameters,
                });
            }
        }

        Ok(SessionRenderState {
            transport_state: self.transport.state,
            transport_position: self.transport.position,
            transport_speed: self.transport.speed,
            loop_start: self.transport.loop_start,
            loop_end: self.transport.loop_end,
            tempo_map: self.tempo_map.read().as_ref().clone(),
            routes,
            processors,
            route_delay_lines: self.route_delay_lines.clone(),
        })
    }

    pub fn restore_render_state(&mut self, snapshot: &SessionRenderState) -> Result<(), String> {
        self.transport.state = snapshot.transport_state;
        self.transport.position = snapshot.transport_position;
        self.transport.speed = snapshot.transport_speed;
        self.transport.loop_start = snapshot.loop_start;
        self.transport.loop_end = snapshot.loop_end;
        self.tempo_map.update(|_| snapshot.tempo_map.clone());
        self.route_delay_lines
            .clone_from(&snapshot.route_delay_lines);

        for state in &snapshot.routes {
            let Some(route) = self.route(state.id) else {
                continue;
            };
            let mut route = route.lock().map_err(|_| {
                format!(
                    "failed to lock route {} for render-state restore (mutex poisoned)",
                    state.id
                )
            })?;
            route.gain.restore_state(state.gain);
            route.pan.value = state.pan;
            route.solo = state.solo;
            route.mute = state.mute;
        }

        for state in &snapshot.processors {
            let Some(processor) = self.processor_slot(state.track_id, state.slot_index) else {
                continue;
            };
            let mut processor = processor.lock().map_err(|_| {
                format!(
                    "failed to lock processor for render-state restore (track {} slot {}, mutex poisoned)",
                    state.track_id, state.slot_index
                )
            })?;
            let restore_parameters = match &state.state_chunk {
                Some(chunk) => match processor.set_state_chunk(chunk) {
                    Ok(()) => true,
                    Err(err) => {
                        log::warn!(
                            "[session] failed to restore processor state for track {} slot {}: {err}",
                            state.track_id,
                            state.slot_index
                        );
                        false
                    }
                },
                None => true,
            };
            if restore_parameters {
                for (index, value) in &state.parameters {
                    if let Err(err) = processor.set_parameter(*index, *value) {
                        log::warn!(
                            "[session] failed to restore processor parameter {index} for track {} slot {}: {err}",
                            state.track_id,
                            state.slot_index
                        );
                    }
                }
            }
        }
        Ok(())
    }

    pub fn drain_captured_plugin_parameter_edits(&mut self) -> Vec<CapturedRouteParameterEdit> {
        let mut captured = Vec::new();
        for route_arc in &self.routes {
            let Ok(route) = route_arc.lock() else {
                continue;
            };
            let track_id = route.id;
            for (slot_index, processor) in route.processors.iter().enumerate() {
                let Some(processor) = processor else {
                    continue;
                };
                let Ok(mut processor) = processor.lock() else {
                    continue;
                };
                let plugin_name = processor.name().to_string();
                let parameter_info = processor.parameter_info();
                for edit in processor.drain_captured_parameter_edits() {
                    let parameter = parameter_info
                        .iter()
                        .find(|info| info.param_id == Some(edit.param_id))
                        .cloned();
                    captured.push(CapturedRouteParameterEdit {
                        track_id,
                        slot_index,
                        plugin_name: plugin_name.clone(),
                        parameter,
                        edit,
                    });
                }
            }
        }
        captured
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
        self.collect_solo_route_indices();
        let any_solo = !self.solo_indices_scratch.is_empty();
        self.update_route_mix_latencies();
        let max_route_latency = self
            .route_latencies_scratch
            .iter()
            .copied()
            .max()
            .unwrap_or(0);

        self.master_buf.silence(nframes);
        if self.transport.is_rolling() {
            self.apply_automation_lanes(start_sample, end_sample, &tempo_map, nframes);
        }
        let render_tempo_map = self.tempo_map.read().clone();
        let tempo_metric =
            render_tempo_map.metric_at_beats(render_tempo_map.beats_at_sample(start_sample));

        for idx in 0..self.route_bufs.len() {
            self.route_bufs[idx].silence(nframes);
            self.midi_events[idx].clear();
        }

        for order_idx in 0..self.route_topology.render_order.len() {
            let idx = self.route_topology.render_order[order_idx];
            let muted = self.routes[idx]
                .lock()
                .map(|route| route.mute)
                .unwrap_or(true);
            let muted_or_unsoloed = muted || (any_solo && !self.route_feeds_solo_path(idx));
            if muted_or_unsoloed {
                if let Some(delay_line) = self.route_delay_lines.get_mut(idx) {
                    delay_line.clear();
                }
                continue;
            }

            self.prepare_route_source(idx, start_sample, end_sample, &render_tempo_map, nframes);
            let compensation = max_route_latency
                .saturating_sub(self.route_latencies_scratch.get(idx).copied().unwrap_or(0));
            self.process_route_buffer(
                idx,
                start_sample,
                end_sample,
                speed,
                nframes,
                compensation,
                tempo_metric,
            );
            self.accumulate_route_sends(idx, nframes);
            self.accumulate_route_output(idx, nframes);
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

    /// Returns whether a route exists for the given track id.
    pub fn has_route(&self, track_id: u32) -> bool {
        self.route_index(track_id).is_some()
    }

    pub fn route_count(&self) -> usize {
        self.routes.len()
    }

    fn route_index(&self, track_id: u32) -> Option<usize> {
        self.route_indices.get(&track_id).copied()
    }

    fn route_output_index(&self, route_index: usize) -> Option<usize> {
        self.route_topology
            .output_indices
            .get(route_index)
            .copied()
            .flatten()
    }

    #[cfg(test)]
    fn route_render_order(&self) -> &[usize] {
        &self.route_topology.render_order
    }

    fn rebuild_route_topology(&mut self) {
        let route_count = self.routes.len();
        let mut targets = Vec::with_capacity(route_count);
        targets.resize_with(route_count, Vec::new);

        self.route_topology.route_count = route_count;
        self.route_topology.render_order.clear();
        self.route_topology.render_order.extend(0..route_count);
        self.route_topology.output_indices.clear();
        self.route_topology.output_indices.resize(route_count, None);
        self.route_topology.sends.resize_with(route_count, Vec::new);
        self.route_topology.sends.truncate(route_count);

        for sends in &mut self.route_topology.sends {
            sends.clear();
        }

        for (idx, route_arc) in self.routes.iter().enumerate() {
            let Ok(route) = route_arc.lock() else {
                continue;
            };

            if let Some(output_idx) = route
                .output_track_id()
                .and_then(|track_id| self.route_index(track_id))
            {
                self.route_topology.output_indices[idx] = Some(output_idx);
                targets[idx].push(output_idx);
            }

            for send in route
                .sends()
                .iter()
                .filter(|send| send.enabled && send.level > 0.0)
            {
                let Some(target_index) = self.route_index(send.target_track_id) else {
                    continue;
                };
                self.route_topology.sends[idx].push(CachedRouteSend {
                    target_index,
                    level: send.level,
                });
                targets[idx].push(target_index);
            }
        }

        let mut depths = vec![0; route_count];
        let mut visiting = vec![false; route_count];
        let mut computed = vec![false; route_count];
        for idx in 0..route_count {
            route_depth_from_targets(&targets, idx, &mut visiting, &mut computed, &mut depths);
        }
        self.route_topology.render_order.sort_by(|left, right| {
            depths[*right]
                .cmp(&depths[*left])
                .then_with(|| left.cmp(right))
        });

        self.route_topology
            .reaches
            .resize(route_count * route_count, false);
        self.route_topology.reaches.fill(false);
        for idx in 0..route_count {
            mark_reachable_routes(
                idx,
                idx,
                &targets,
                &mut self.route_topology.reaches,
                route_count,
            );
        }

        if self.route_latencies_scratch.len() != route_count {
            self.route_latencies_scratch.resize(route_count, 0);
        }
        ensure_vec_capacity(&mut self.solo_indices_scratch, route_count);
    }

    fn route_target_indices_with_override(
        &self,
        route_index: usize,
        override_output: Option<(u32, Option<u32>)>,
        override_sends: Option<(u32, &[RouteSend])>,
    ) -> Vec<usize> {
        let Some(route_arc) = self.routes.get(route_index) else {
            return Vec::new();
        };
        let Ok(route) = route_arc.lock() else {
            return Vec::new();
        };
        let route_id = route.id;
        let output_track_id = if let Some((override_track_id, output_track_id)) = override_output {
            if override_track_id == route_id {
                output_track_id
            } else {
                route.output_track_id()
            }
        } else {
            route.output_track_id()
        };
        let send_target_ids = if let Some((override_track_id, sends)) = override_sends {
            if override_track_id == route_id {
                sends
                    .iter()
                    .filter(|send| send.enabled && send.level > 0.0)
                    .map(|send| send.target_track_id)
                    .collect::<Vec<_>>()
            } else {
                route
                    .sends()
                    .iter()
                    .filter(|send| send.enabled && send.level > 0.0)
                    .map(|send| send.target_track_id)
                    .collect::<Vec<_>>()
            }
        } else {
            route
                .sends()
                .iter()
                .filter(|send| send.enabled && send.level > 0.0)
                .map(|send| send.target_track_id)
                .collect::<Vec<_>>()
        };
        drop(route);

        let mut target_indices = Vec::new();
        if let Some(output_index) = output_track_id.and_then(|track_id| self.route_index(track_id))
        {
            target_indices.push(output_index);
        }
        target_indices.extend(
            send_target_ids
                .into_iter()
                .filter_map(|track_id| self.route_index(track_id)),
        );
        target_indices
    }

    fn route_feeds_solo_path(&self, route_index: usize) -> bool {
        self.solo_indices_scratch.iter().copied().any(|solo_index| {
            solo_index == route_index
                || self.route_topology.route_reaches(route_index, solo_index)
                || self.route_topology.route_reaches(solo_index, route_index)
        })
    }

    fn route_graph_has_cycle(
        &self,
        override_output: Option<(u32, Option<u32>)>,
        override_sends: Option<(u32, &[RouteSend])>,
    ) -> bool {
        let mut visited = HashSet::new();
        for route_index in 0..self.routes.len() {
            let mut visiting = HashSet::new();
            if self.route_graph_has_cycle_from(
                route_index,
                override_output,
                override_sends,
                &mut visiting,
                &mut visited,
            ) {
                return true;
            }
        }
        false
    }

    fn route_graph_has_cycle_from(
        &self,
        route_index: usize,
        override_output: Option<(u32, Option<u32>)>,
        override_sends: Option<(u32, &[RouteSend])>,
        visiting: &mut HashSet<usize>,
        visited: &mut HashSet<usize>,
    ) -> bool {
        if visited.contains(&route_index) {
            return false;
        }
        if !visiting.insert(route_index) {
            return true;
        }
        for target_index in
            self.route_target_indices_with_override(route_index, override_output, override_sends)
        {
            if self.route_graph_has_cycle_from(
                target_index,
                override_output,
                override_sends,
                visiting,
                visited,
            ) {
                return true;
            }
        }
        visiting.remove(&route_index);
        visited.insert(route_index);
        false
    }

    fn collect_solo_route_indices(&mut self) {
        self.solo_indices_scratch.clear();
        for (idx, route) in self.routes.iter().enumerate() {
            let is_solo = route.lock().map(|route| route.solo).unwrap_or(false);
            if is_solo {
                self.solo_indices_scratch.push(idx);
            }
        }
    }

    fn prepare_route_source(
        &mut self,
        idx: usize,
        start_sample: i64,
        end_sample: i64,
        tempo_map: &TempoMap,
        nframes: usize,
    ) {
        let Ok(route) = self.routes[idx].lock() else {
            return;
        };
        if route.kind == RouteKind::Bus {
            return;
        }
        if self.transport.is_rolling() {
            route.render_audio_clips(
                &mut self.route_bufs[idx],
                start_sample,
                end_sample,
                tempo_map,
                nframes,
            );
            route.sequencer.collect_events_in_samples(
                start_sample,
                end_sample,
                tempo_map,
                &mut self.midi_events[idx],
            );
        } else {
            // On pause/stop, inject AllNotesOff so synth voices release
            // instead of sustaining forever mid-note.
            self.midi_events[idx].push(ScheduledMidiEvent::new(
                MidiEvent::new(0, MidiMessage::AllNotesOff { channel: 0 }),
                0,
            ));
        }
    }

    fn process_route_buffer(
        &mut self,
        idx: usize,
        start_sample: i64,
        end_sample: i64,
        speed: f64,
        nframes: usize,
        compensation: usize,
        tempo_metric: atri_core::time::tempo::TempoMetric,
    ) {
        let Ok(mut route) = self.routes[idx].lock() else {
            return;
        };
        route.process(
            &mut self.route_bufs[idx],
            &self.midi_events[idx],
            start_sample,
            end_sample,
            speed,
            nframes,
            tempo_metric,
        );
        if let Some(buf) = self.route_bufs[idx].get_mut(0) {
            if let Some(delay_line) = self.route_delay_lines.get_mut(idx) {
                delay_line.process(buf, nframes, compensation);
            }
        }
    }

    fn accumulate_route_output(&mut self, idx: usize, nframes: usize) {
        if let Some(output_idx) = self.route_output_index(idx) {
            self.add_route_buffer_to_route(idx, output_idx, nframes, 1.0);
        } else {
            let Some(source) = self.route_bufs[idx].get(0) else {
                return;
            };
            self.mixer.add(source, &mut self.master_buf, nframes);
        }
    }

    fn accumulate_route_sends(&mut self, idx: usize, nframes: usize) {
        let send_count = self
            .route_topology
            .sends
            .get(idx)
            .map(|sends| sends.len())
            .unwrap_or(0);
        for send_idx in 0..send_count {
            let Some(send) = self
                .route_topology
                .sends
                .get(idx)
                .and_then(|sends| sends.get(send_idx))
                .copied()
            else {
                break;
            };
            self.add_route_buffer_to_route(idx, send.target_index, nframes, send.level);
        }
    }

    fn add_route_buffer_to_route(
        &mut self,
        source_idx: usize,
        dest_idx: usize,
        nframes: usize,
        level: f32,
    ) {
        if source_idx == dest_idx {
            return;
        }

        if source_idx < dest_idx {
            let (left, right) = self.route_bufs.split_at_mut(dest_idx);
            let Some(source) = left.get(source_idx).and_then(|bufs| bufs.get(0)) else {
                return;
            };
            let Some(dest) = right.get_mut(0).and_then(|bufs| bufs.get_mut(0)) else {
                return;
            };
            add_scaled_buffer(source, dest, nframes, level);
        } else {
            let (left, right) = self.route_bufs.split_at_mut(source_idx);
            let Some(dest) = left.get_mut(dest_idx).and_then(|bufs| bufs.get_mut(0)) else {
                return;
            };
            let Some(source) = right.first().and_then(|bufs| bufs.get(0)) else {
                return;
            };
            add_scaled_buffer(source, dest, nframes, level);
        }
    }

    fn update_route_mix_latencies(&mut self) {
        let route_count = self.routes.len();
        if self.route_latencies_scratch.len() != route_count {
            self.route_latencies_scratch.resize(route_count, 0);
        }
        self.route_latencies_scratch.fill(0);

        let any_solo = !self.solo_indices_scratch.is_empty();
        for idx in 0..route_count {
            let muted = self.routes[idx]
                .lock()
                .map(|route| route.mute)
                .unwrap_or(true);
            if muted || (any_solo && !self.route_feeds_solo_path(idx)) {
                continue;
            }
            self.route_latencies_scratch[idx] = self.routes[idx]
                .lock()
                .map(|route| route.signal_latency())
                .unwrap_or(0);
        }
    }

    fn apply_automation_lanes(
        &mut self,
        start_sample: i64,
        end_sample: i64,
        tempo_map: &TempoMap,
        nframes: usize,
    ) {
        for lane_idx in 0..self.automation_lanes.len() {
            let lane = &self.automation_lanes[lane_idx];
            if lane.muted {
                continue;
            }
            let target = lane.target;
            let emit_segment_value = matches!(
                target,
                AutomationTarget::PluginParameter { .. }
                    | AutomationTarget::TrackVolume { .. }
                    | AutomationTarget::TrackPan { .. }
            );
            automation_events_in_block(
                lane,
                start_sample,
                end_sample,
                tempo_map,
                emit_segment_value,
                &mut self.automation_event_scratch,
            );
            if self.automation_event_scratch.is_empty() {
                continue;
            }
            match target {
                AutomationTarget::PluginParameter {
                    track_id,
                    slot_index,
                    param_index,
                } => {
                    let Some(processor) = self.processor_slot(track_id, slot_index) else {
                        continue;
                    };
                    let Ok(mut processor) = processor.try_lock() else {
                        continue;
                    };
                    for event_idx in 0..self.automation_event_scratch.len() {
                        let event = self.automation_event_scratch[event_idx];
                        let offset = event.sample_offset.min(nframes.saturating_sub(1));
                        let _ = processor.set_parameter_at_sample(param_index, offset, event.value);
                    }
                }
                AutomationTarget::TrackVolume { track_id } => {
                    for event_idx in 0..self.automation_event_scratch.len() {
                        let value = self.automation_event_scratch[event_idx].value;
                        let _ = self.set_track_volume(track_id, value);
                    }
                }
                AutomationTarget::TrackPan { track_id } => {
                    for event_idx in 0..self.automation_event_scratch.len() {
                        let value = self.automation_event_scratch[event_idx].value;
                        let _ = self.set_track_pan(track_id, value);
                    }
                }
                AutomationTarget::TempoBpm => {
                    for event_idx in 0..self.automation_event_scratch.len() {
                        let event = self.automation_event_scratch[event_idx];
                        let bpm = f64::from(event.value).clamp(1.0, 999.0);
                        let at = Beats::from_beats(event.beat.max(0.0));
                        self.tempo_map.update(|tempo_map| {
                            let metric = tempo_map.metric_at_beats(at);
                            tempo_map.with_tempo(Tempo::new(bpm, metric.tempo.note_type), at)
                        });
                    }
                }
                AutomationTarget::TimeSignatureNumerator => {
                    for event_idx in 0..self.automation_event_scratch.len() {
                        let event = self.automation_event_scratch[event_idx];
                        let numerator = event.value.round().clamp(1.0, 255.0) as u8;
                        let at = Beats::from_beats(event.beat.max(0.0));
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

fn ensure_vec_capacity<T>(vec: &mut Vec<T>, needed: usize) {
    if vec.capacity() < needed {
        vec.reserve(needed.saturating_sub(vec.len()));
    }
}

fn route_depth_from_targets(
    targets: &[Vec<usize>],
    route_index: usize,
    visiting: &mut [bool],
    computed: &mut [bool],
    depths: &mut [usize],
) -> usize {
    if computed[route_index] {
        return depths[route_index];
    }
    if visiting[route_index] {
        return 0;
    }

    visiting[route_index] = true;
    let depth = targets[route_index]
        .iter()
        .copied()
        .map(|target_index| {
            1usize.saturating_add(route_depth_from_targets(
                targets,
                target_index,
                visiting,
                computed,
                depths,
            ))
        })
        .max()
        .unwrap_or(0);
    visiting[route_index] = false;
    computed[route_index] = true;
    depths[route_index] = depth;
    depth
}

fn mark_reachable_routes(
    source_index: usize,
    route_index: usize,
    targets: &[Vec<usize>],
    reaches: &mut [bool],
    route_count: usize,
) {
    for target_index in targets[route_index].iter().copied() {
        let reach_index = source_index * route_count + target_index;
        if reaches[reach_index] {
            continue;
        }
        reaches[reach_index] = true;
        mark_reachable_routes(source_index, target_index, targets, reaches, route_count);
    }
}

fn automation_events_in_block(
    lane: &AutomationLane,
    start_sample: i64,
    end_sample: i64,
    tempo_map: &TempoMap,
    emit_segment_value: bool,
    events: &mut Vec<AutomationEvent>,
) {
    events.clear();
    if lane.points.is_empty() || end_sample <= start_sample {
        return;
    }

    let mut previous_point = None;
    let mut next_point = None;
    let mut has_point_at_start = false;
    for point in &lane.points {
        let point_sample = automation_point_sample(point, tempo_map);
        if point_sample < start_sample {
            previous_point = Some((point, point_sample));
        } else {
            if point_sample == start_sample {
                has_point_at_start = true;
            } else if next_point.is_none() {
                next_point = Some((point, point_sample));
            }
            break;
        }
    }

    if emit_segment_value && !has_point_at_start {
        if let Some((point, _point_sample)) = previous_point {
            events.push(AutomationEvent {
                sample_offset: 0,
                value: automation_value_at_sample(point, next_point, start_sample, tempo_map),
                beat: tempo_map
                    .beats_at_sample(start_sample.max(0))
                    .to_beats_f64()
                    .max(0.0),
            });
        }
    }

    for point in &lane.points {
        let point_sample = automation_point_sample(point, tempo_map);
        if point_sample < start_sample {
            continue;
        }
        if point_sample >= end_sample {
            break;
        }
        events.push(AutomationEvent {
            sample_offset: (point_sample - start_sample) as usize,
            value: point.value,
            beat: point.beat,
        });
    }
}

fn automation_point_sample(point: &AutomationPoint, tempo_map: &TempoMap) -> i64 {
    tempo_map.sample_at_beats(atri_core::time::beats::Beats::from_beats(
        point.beat.max(0.0),
    ))
}

fn automation_value_at_sample(
    point: &AutomationPoint,
    next_point: Option<(&AutomationPoint, i64)>,
    sample: i64,
    tempo_map: &TempoMap,
) -> f32 {
    let Some((next, _next_sample)) = next_point else {
        return point.value;
    };
    let point_beat = point.beat.max(0.0);
    let next_beat = next.beat.max(0.0);
    if point.curve == AutomationCurve::Hold || next_beat <= point_beat {
        return point.value;
    }

    let beat = tempo_map
        .beats_at_sample(sample.max(0))
        .to_beats_f64()
        .max(0.0);
    let progress = ((beat - point_beat) / (next_beat - point_beat)) as f32;
    point.value + (next.value - point.value) * progress.clamp(0.0, 1.0)
}

fn add_scaled_buffer(source: &AudioBuffer, dest: &mut AudioBuffer, nframes: usize, level: f32) {
    if source.channels() < 2 || dest.channels() < 2 {
        return;
    }
    let n = nframes.min(source.capacity()).min(dest.capacity());
    for i in 0..n {
        let left = dest.channel(0)[i] + source.channel(0)[i] * level;
        let right = dest.channel(1)[i] + source.channel(1)[i] * level;
        dest.channel_mut(0)[i] = left;
        dest.channel_mut(1)[i] = right;
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
    fn route_config_sets_kind_and_output_target() {
        let mut session = Session::new(48_000, 64);
        let track = session.add_track("Lead".to_string());
        let bus = session.add_bus("Lead Bus".to_string());

        assert!(session.set_route_output(track, Some(bus)));
        assert_eq!(session.route_output(track), Some(Some(bus)));
        assert_eq!(session.route_kind(track), Some(RouteKind::Track));
        assert_eq!(session.route_kind(bus), Some(RouteKind::Bus));
    }

    #[test]
    fn route_output_rejects_bus_output_cycle() {
        let mut session = Session::new(48_000, 64);
        let bus_a = session.add_bus("Bus A".to_string());
        let bus_b = session.add_bus("Bus B".to_string());

        assert!(session.set_route_output(bus_a, Some(bus_b)));
        assert!(!session.set_route_output(bus_b, Some(bus_a)));

        assert_eq!(session.route_output(bus_a), Some(Some(bus_b)));
        assert_eq!(session.route_output(bus_b), Some(None));
    }

    #[test]
    fn route_output_rejects_cycle_through_existing_send() {
        let mut session = Session::new(48_000, 64);
        let bus_a = session.add_bus("Bus A".to_string());
        let bus_b = session.add_bus("Bus B".to_string());

        assert!(session.set_route_sends(
            bus_a,
            vec![RouteSend {
                target_track_id: bus_b,
                level: 0.5,
                enabled: true,
            }],
        ));
        assert!(!session.set_route_output(bus_b, Some(bus_a)));

        assert_eq!(session.route_output(bus_b), Some(None));
    }

    #[test]
    fn route_output_bus_sums_to_master() {
        let mut session = Session::new(48_000, 16);
        let track = session.add_track("Tone".to_string());
        let bus = session.add_bus("Bus".to_string());
        assert!(session.set_route_output(track, Some(bus)));
        assert!(session.set_processor_slot(
            track,
            0,
            Some(Arc::new(Mutex::new(PdcImpulseProcessor::new(0, 1.0)))),
        ));

        let mut output = vec![0.0; 16 * 2];
        session.process(&mut output);

        assert!((output[0] - 0.5).abs() < 0.0001);
        assert!((output[1] - 0.5).abs() < 0.0001);
    }

    #[test]
    fn route_render_order_places_nested_buses_after_sources() {
        let mut session = Session::new(48_000, 4);
        let track = session.add_track("Track".to_string());
        let child_bus = session.add_bus("Child".to_string());
        let parent_bus = session.add_bus("Parent".to_string());
        assert!(session.set_route_output(track, Some(child_bus)));
        assert!(session.set_route_output(child_bus, Some(parent_bus)));

        let names: Vec<String> = session
            .route_render_order()
            .iter()
            .copied()
            .map(|idx| session.routes[idx].lock().unwrap().name.clone())
            .collect();

        assert_eq!(names, vec!["Track", "Child", "Parent"]);
    }

    #[test]
    fn route_render_order_places_send_sources_before_targets() {
        let mut session = Session::new(48_000, 4);
        let bus = session.add_bus("FX".to_string());
        let track = session.add_track("Track".to_string());
        assert!(session.set_route_sends(
            track,
            vec![RouteSend {
                target_track_id: bus,
                level: 0.5,
                enabled: true,
            }],
        ));

        let names: Vec<String> = session
            .route_render_order()
            .iter()
            .copied()
            .map(|idx| session.routes[idx].lock().unwrap().name.clone())
            .collect();

        assert_eq!(names, vec!["Track", "FX"]);
    }

    #[test]
    fn route_send_copies_post_fader_signal_to_target_bus() {
        let mut session = Session::new(48_000, 16);
        let track = session.add_track("Tone".to_string());
        let bus = session.add_bus("FX".to_string());
        assert!(session.set_route_sends(
            track,
            vec![RouteSend {
                target_track_id: bus,
                level: 0.5,
                enabled: true,
            }],
        ));
        assert!(session.set_processor_slot(
            track,
            0,
            Some(Arc::new(Mutex::new(PdcImpulseProcessor::new(0, 1.0)))),
        ));

        let mut output = vec![0.0; 16 * 2];
        session.process(&mut output);

        let direct = std::f32::consts::FRAC_PI_4.cos();
        let sent = direct * 0.5 * std::f32::consts::FRAC_PI_4.cos();
        assert!((output[0] - (direct + sent)).abs() < 0.0001);
        assert!((output[1] - (direct + sent)).abs() < 0.0001);
    }

    #[test]
    fn route_snapshots_expose_read_only_route_state() {
        let mut session = Session::new(48_000, 16);
        let track = session.add_track("Tone".to_string());
        let bus = session.add_bus("Bus".to_string());
        assert!(session.set_route_output(track, Some(bus)));
        assert!(session.set_route_sends(
            track,
            vec![RouteSend {
                target_track_id: bus,
                level: 0.5,
                enabled: true,
            }],
        ));

        let snapshots = session.route_snapshots();

        assert_eq!(snapshots.len(), 2);
        let track_snapshot = snapshots
            .iter()
            .find(|snapshot| snapshot.id == track)
            .expect("track snapshot");
        assert_eq!(track_snapshot.name, "Tone");
        assert_eq!(track_snapshot.kind, RouteKind::Track);
        assert_eq!(track_snapshot.output_track_id, Some(bus));
        assert_eq!(
            track_snapshot.sends,
            vec![RouteSend {
                target_track_id: bus,
                level: 0.5,
                enabled: true,
            }]
        );
    }

    #[test]
    fn soloed_track_feeds_through_output_bus() {
        let mut session = Session::new(48_000, 16);
        let track = session.add_track("Tone".to_string());
        let bus = session.add_bus("Bus".to_string());
        assert!(session.set_route_output(track, Some(bus)));
        assert!(session.set_track_solo(track, true));
        assert!(session.set_processor_slot(
            track,
            0,
            Some(Arc::new(Mutex::new(PdcImpulseProcessor::new(0, 1.0)))),
        ));

        let mut output = vec![0.0; 16 * 2];
        session.process(&mut output);

        assert!((output[0] - 0.5).abs() < 0.0001);
        assert!((output[1] - 0.5).abs() < 0.0001);
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
    fn automation_lanes_emit_linear_plugin_parameter_values_between_points() {
        let mut session = Session::new(48_000, 100);
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
                    value: 0.0,
                    curve: AutomationCurve::Linear,
                },
                AutomationPoint {
                    beat: 16.0 / atri_core::time::beats::PPQN as f64,
                    value: 1.0,
                    curve: AutomationCurve::Linear,
                },
            ],
            muted: false,
        }]);

        session.transport.play();
        let mut output = vec![0.0; 100 * 2];
        session.process(&mut output);
        changes.lock().unwrap().clear();

        session.process(&mut output);

        assert_eq!(*changes.lock().unwrap(), vec![(3, 0, 0.5)]);
    }

    #[test]
    fn automation_events_in_block_emit_curve_values_between_points() {
        let tempo_map = TempoMap::new(Tempo::new(120.0, 4), Meter::new(4, 4), 48_000);
        let mut events = Vec::new();
        let mut lane = AutomationLane {
            target: AutomationTarget::TrackVolume { track_id: 0 },
            points: vec![
                AutomationPoint {
                    beat: 0.0,
                    value: 0.25,
                    curve: AutomationCurve::Linear,
                },
                AutomationPoint {
                    beat: 16.0 / atri_core::time::beats::PPQN as f64,
                    value: 0.75,
                    curve: AutomationCurve::Linear,
                },
            ],
            muted: false,
        };

        automation_events_in_block(&lane, 100, 200, &tempo_map, true, &mut events);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].sample_offset, 0);
        assert!((events[0].value - 0.5).abs() < 0.0001);

        lane.points[0].curve = AutomationCurve::Hold;
        automation_events_in_block(&lane, 100, 200, &tempo_map, true, &mut events);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].sample_offset, 0);
        assert!((events[0].value - 0.25).abs() < 0.0001);
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
    fn process_forwards_current_tempo_metric_to_processors() {
        let mut session = Session::new(48_000, 128);
        let track_id = session.add_track("Tempo Sync".to_string());
        let captured_metric = Arc::new(Mutex::new(None));
        assert!(session.set_processor_slot(
            track_id,
            0,
            Some(Arc::new(Mutex::new(TempoCaptureProcessor {
                captured_metric: Arc::clone(&captured_metric),
            }))),
        ));
        session.tempo_map.update(|tempo_map| {
            tempo_map
                .with_tempo(Tempo::new(93.5, 4), Beats::from_beats(0.0))
                .with_meter(Meter::new(7, 8), Beats::from_beats(0.0))
        });

        let mut output = vec![0.0; 256];
        session.process(&mut output);

        let metric = captured_metric.lock().unwrap().unwrap();
        assert_eq!(metric.tempo.bpm, 93.5);
        assert_eq!(metric.meter.num, 7);
        assert_eq!(metric.meter.denom, 8);
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

    #[test]
    fn process_reuses_realtime_scratch_after_warmup() {
        let mut session = Session::new(48_000, 16);
        let dry_track = session.add_track("Dry".into());
        let latent_track = session.add_track("Latent".into());
        let bus = session.add_bus("Bus".into());
        assert!(session.set_route_output(dry_track, Some(bus)));
        assert!(session.set_route_output(latent_track, Some(bus)));
        assert!(session.set_track_solo(bus, true));
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
        session.set_automation_lanes(vec![AutomationLane {
            target: AutomationTarget::TrackVolume {
                track_id: dry_track,
            },
            points: vec![AutomationPoint {
                beat: Beats::from_ticks(2).to_beats_f64(),
                value: 0.75,
                curve: AutomationCurve::Linear,
            }],
            muted: false,
        }]);

        session.transport.play();
        let mut output = vec![0.0; 16 * 2];
        session.process(&mut output);

        reset_allocation_count();
        session.process(&mut output);
        let allocations = stop_allocation_count();

        assert_eq!(
            allocations, 0,
            "Session::process allocated {allocations} times after warmup"
        );
        let dry_index = session.route_index(dry_track).unwrap();
        assert_eq!(session.route_delay_lines[dry_index].delay_samples, 2);
        assert_eq!(session.route_snapshots()[dry_index].volume_target, 0.75);
    }

    #[test]
    fn adding_routes_reserves_solo_scratch_for_full_route_count() {
        let mut session = Session::new(48_000, 16);
        let mut route_ids = Vec::new();
        for index in 0..8 {
            route_ids.push(session.add_track(format!("Track {index}")));
        }
        for track_id in route_ids.iter().copied() {
            assert!(session.set_track_solo(track_id, true));
        }

        session.solo_indices_scratch = Vec::with_capacity(8);
        session.solo_indices_scratch.push(0);
        route_ids.push(session.add_track("Ninth".into()));
        for track_id in route_ids.iter().copied() {
            assert!(session.set_track_solo(track_id, true));
        }

        assert!(
            session.solo_indices_scratch.capacity() >= session.routes.len(),
            "solo scratch capacity {} did not cover {} routes",
            session.solo_indices_scratch.capacity(),
            session.routes.len()
        );
    }

    #[test]
    fn setting_automation_lanes_reserves_event_scratch_for_largest_lane() {
        let mut session = Session::new(48_000, 1024);
        let track_id = session.add_track("Automated".into());
        session.automation_event_scratch = Vec::with_capacity(8);
        session.automation_event_scratch.push(AutomationEvent {
            sample_offset: 0,
            value: 0.0,
            beat: 0.0,
        });
        session.set_automation_lanes(vec![AutomationLane {
            target: AutomationTarget::TrackVolume { track_id },
            points: (0..9)
                .map(|index| AutomationPoint {
                    beat: (index * 8) as f64 / atri_core::time::beats::PPQN as f64,
                    value: index as f32 / 10.0,
                    curve: AutomationCurve::Linear,
                })
                .collect(),
            muted: false,
        }]);

        session.transport.play();
        let mut output = vec![0.0; 1024 * 2];
        reset_allocation_count();
        session.process(&mut output);
        let allocations = stop_allocation_count();

        assert_eq!(
            allocations, 0,
            "automation event scratch allocated {allocations} times"
        );
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

    struct TempoCaptureProcessor {
        captured_metric: Arc<Mutex<Option<atri_core::time::tempo::TempoMetric>>>,
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

    impl Processor for TempoCaptureProcessor {
        fn name(&self) -> &str {
            "tempo-capture"
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

        fn set_tempo_context(&mut self, metric: atri_core::time::tempo::TempoMetric) {
            *self.captured_metric.lock().unwrap() = Some(metric);
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
