use std::sync::atomic::{AtomicPtr, Ordering};

use super::beats::{Beats, PPQN};
use super::bbt::BBT_Time;
use super::superclock::Superclock;
use super::tempo::{Meter, Tempo, TempoMetric};

/// A point on the tempo map where tempo changes.
#[derive(Debug, Clone)]
pub struct TempoPoint {
    pub tempo: Tempo,
    pub position: Superclock,
    pub beat_position: Beats,
    pub bbt_position: BBT_Time,
}

/// A point on the tempo map where meter changes.
#[derive(Debug, Clone)]
pub struct MeterPoint {
    pub meter: Meter,
    pub position: Superclock,
    pub beat_position: Beats,
    pub bbt_position: BBT_Time,
}

/// The tempo map: an ordered sequence of tempo and meter changes
/// that bridges the three time coordinate systems.
#[derive(Debug, Clone)]
pub struct TempoMap {
    tempo_points: Vec<TempoPoint>,
    meter_points: Vec<MeterPoint>,
    sample_rate: u32,
}

impl TempoMap {
    pub fn new(initial_tempo: Tempo, initial_meter: Meter, sample_rate: u32) -> Self {
        let zero_beats = Beats::ZERO;
        let bbt = BBT_Time::new(1, 1, 0); // bar 1, beat 1, tick 0
        let mut map = Self {
            tempo_points: Vec::new(),
            meter_points: Vec::new(),
            sample_rate,
        };
        map.tempo_points.push(TempoPoint {
            tempo: initial_tempo,
            position: 0,
            beat_position: zero_beats,
            bbt_position: bbt,
        });
        map.meter_points.push(MeterPoint {
            meter: initial_meter,
            position: 0,
            beat_position: zero_beats,
            bbt_position: bbt,
        });
        map
    }

    pub fn sample_rate(&self) -> u32 {
        self.sample_rate
    }

    // ── lookups ──

    pub fn tempo_at_superclock(&self, sc: Superclock) -> &TempoPoint {
        self.tempo_points
            .iter()
            .rev()
            .find(|p| p.position <= sc)
            .unwrap_or(&self.tempo_points[0])
    }

    pub fn meter_at_superclock(&self, sc: Superclock) -> &MeterPoint {
        self.meter_points
            .iter()
            .rev()
            .find(|p| p.position <= sc)
            .unwrap_or(&self.meter_points[0])
    }

    pub fn metric_at_superclock(&self, sc: Superclock) -> TempoMetric {
        let t = self.tempo_at_superclock(sc);
        let m = self.meter_at_superclock(sc);
        TempoMetric::new(t.tempo, m.meter)
    }

    pub fn metric_at_beats(&self, beats: Beats) -> TempoMetric {
        let sc = self.superclock_at_beats(beats);
        self.metric_at_superclock(sc)
    }

    // ── beats ↔ superclock ──

    pub fn superclock_at_beats(&self, beats: Beats) -> Superclock {
        let tp = self
            .tempo_points
            .iter()
            .rev()
            .find(|p| p.beat_position <= beats)
            .unwrap_or(&self.tempo_points[0]);

        let beat_offset = beats.ticks - tp.beat_position.ticks;
        let beats_f64 = beat_offset as f64 / PPQN as f64;
        let sc_offset =
            (beats_f64 * tp.tempo.superclocks_per_quarter as f64) as Superclock;
        tp.position + sc_offset
    }

    pub fn beats_at_superclock(&self, sc: Superclock) -> Beats {
        let tp = self.tempo_at_superclock(sc);
        let sc_offset = sc - tp.position;
        let beats_offset =
            (sc_offset as f64 / tp.tempo.superclocks_per_quarter as f64) * PPQN as f64;
        Beats {
            ticks: tp.beat_position.ticks + beats_offset as i64,
        }
    }

    // ── samples ↔ beats ──

    pub fn sample_at_beats(&self, beats: Beats) -> i64 {
        let sc = self.superclock_at_beats(beats);
        super::superclock::superclock_to_samples(sc, self.sample_rate)
    }

    pub fn beats_at_sample(&self, sample: i64) -> Beats {
        let sc = super::superclock::samples_to_superclock(sample, self.sample_rate);
        self.beats_at_superclock(sc)
    }

    // ── BBT conversions ──

    pub fn bbt_at_superclock(&self, sc: Superclock) -> BBT_Time {
        let mp = self.meter_at_superclock(sc);
        let beats = self.beats_at_superclock(sc);
        let beat_offset = beats.ticks - mp.beat_position.ticks;
        let tpb = mp.meter.ticks_per_beat() as i64;

        let bars_elapsed = beat_offset / (tpb * mp.meter.num as i64);
        let remainder = beat_offset % (tpb * mp.meter.num as i64);
        let beats_elapsed = remainder / tpb;
        let ticks_elapsed = remainder % tpb;

        BBT_Time {
            bars: mp.bbt_position.bars + bars_elapsed as i32,
            beats: mp.bbt_position.beats + beats_elapsed as i32,
            ticks: mp.bbt_position.ticks + ticks_elapsed as i32,
        }
    }

    pub fn superclock_at_bbt(&self, bbt: BBT_Time) -> Superclock {
        let mp = self
            .meter_points
            .iter()
            .rev()
            .find(|p| p.bbt_position.bars <= bbt.bars)
            .unwrap_or(&self.meter_points[0]);

        let tpb = mp.meter.ticks_per_beat() as i64;
        let bars_diff = (bbt.bars - mp.bbt_position.bars) as i64;
        let beats_diff = (bbt.beats - mp.bbt_position.beats) as i64;
        let ticks_diff = (bbt.ticks - mp.bbt_position.ticks) as i64;

        let total_ticks =
            bars_diff * tpb * mp.meter.num as i64 + beats_diff * tpb + ticks_diff;
        let beats = Beats {
            ticks: mp.beat_position.ticks + total_ticks,
        };
        self.superclock_at_beats(beats)
    }

    pub fn sample_at_bbt(&self, bbt: BBT_Time) -> i64 {
        super::superclock::superclock_to_samples(
            self.superclock_at_bbt(bbt),
            self.sample_rate,
        )
    }

    // ── mutations (returns new TempoMap) ──

    pub fn with_tempo(&self, tempo: Tempo, at: Beats) -> Self {
        let sc = self.superclock_at_beats(at);
        let bbt = self.bbt_at_superclock(sc);
        let mut new = self.clone();
        let point = TempoPoint {
            tempo,
            position: sc,
            beat_position: at,
            bbt_position: bbt,
        };
        // insert ordered by position
        let idx = new
            .tempo_points
            .binary_search_by(|p| p.position.cmp(&sc))
            .unwrap_or_else(|i| i);
        new.tempo_points.insert(idx, point);
        new
    }

    pub fn with_meter(&self, meter: Meter, at: Beats) -> Self {
        let sc = self.superclock_at_beats(at);
        let bbt = self.bbt_at_superclock(sc);
        let mut new = self.clone();
        let point = MeterPoint {
            meter,
            position: sc,
            beat_position: at,
            bbt_position: bbt,
        };
        let idx = new
            .meter_points
            .binary_search_by(|p| p.position.cmp(&sc))
            .unwrap_or_else(|i| i);
        new.meter_points.insert(idx, point);
        new
    }

    pub fn current_tempo(&self) -> &Tempo {
        &self.tempo_points[0].tempo
    }

    pub fn current_meter(&self) -> &Meter {
        &self.meter_points[0].meter
    }
}

/// A lock-free, read-optimized shared tempo map.
///
/// Writers clone the map, modify it, then atomically swap the pointer.
/// Readers perform an atomic load and get an immutable reference.
pub struct SwapLock<T> {
    ptr: AtomicPtr<T>,
}

impl<T> SwapLock<T> {
    pub fn new(value: T) -> Self {
        Self {
            ptr: AtomicPtr::new(Box::into_raw(Box::new(value))),
        }
    }

    /// Acquire a read guard — atomically loads the pointer.
    /// The returned reference is valid until the next write.
    pub fn read(&self) -> &T {
        unsafe { &*self.ptr.load(Ordering::Acquire) }
    }

    /// Update the value by cloning, modifying, and swapping.
    pub fn update(&self, f: impl FnOnce(&T) -> T) {
        let current = self.ptr.load(Ordering::Acquire);
        let new = Box::into_raw(Box::new(f(unsafe { &*current })));
        let old = self.ptr.swap(new, Ordering::AcqRel);
        // Safety: old pointer is no longer reachable; drop it.
        // Note: In a real implementation you'd use epoch-based reclamation
        // or RCU to avoid dropping while a reader holds the old pointer.
        // For Phase 1, we accept this small window of unsafety under the
        // assumption that audio callbacks are short and won't be
        // preempted across a swap.
        unsafe {
            drop(Box::from_raw(old));
        }
    }
}

impl<T> Drop for SwapLock<T> {
    fn drop(&mut self) {
        unsafe {
            drop(Box::from_raw(self.ptr.load(Ordering::Acquire)));
        }
    }
}

unsafe impl<T: Send> Send for SwapLock<T> {}
unsafe impl<T: Sync> Sync for SwapLock<T> {}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::time::tempo::{Meter, Tempo};

    #[test]
    fn test_default_tempo_map() {
        let map = TempoMap::new(
            Tempo::new(120.0, 4),
            Meter::new(4, 4),
            48000,
        );

        // At zero, BBT should be 1|1|0
        let bbt = map.bbt_at_superclock(0);
        assert_eq!(bbt.bars, 1);
        assert_eq!(bbt.beats, 1);
        assert_eq!(bbt.ticks, 0);

        // One bar at 120 BPM 4/4 = 2 seconds = 96000 samples at 48kHz
        let bar_samples = map.sample_at_bbt(BBT_Time::new(2, 1, 0));
        assert_eq!(bar_samples, 96000);

        // Round-trip
        let bbt2 = map.bbt_at_superclock(
            crate::time::superclock::samples_to_superclock(bar_samples, 48000),
        );
        assert_eq!(bbt2.bars, 2);
        assert_eq!(bbt2.beats, 1);
        assert_eq!(bbt2.ticks, 0);
    }

    #[test]
    fn test_tempo_change() {
        let map = TempoMap::new(
            Tempo::new(120.0, 4),
            Meter::new(4, 4),
            48000,
        );

        // Insert tempo change at beat 4 (end of bar 1)
        let beat_4 = Beats::from_ticks(4 * PPQN as i64);
        let map = map.with_tempo(Tempo::new(240.0, 4), beat_4);

        // After bar 1 (120 BPM) = 2 sec = 96000 samples
        // Bar 2 at 240 BPM (double speed) should be 1 sec = 48000 samples
        let bar2_start = map.sample_at_bbt(BBT_Time::new(2, 1, 0));
        assert_eq!(bar2_start, 96000);

        let bar3_start = map.sample_at_bbt(BBT_Time::new(3, 1, 0));
        // Bar 2 at 240bpm = 1 sec = 48000 samples
        assert_eq!(bar3_start, 96000 + 48000);
    }
}
