use std::sync::{Arc, Mutex, RwLock};

use super::bbt::BBT_Time;
use super::beats::{Beats, PPQN};
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

    pub fn with_sample_rate(&self, sample_rate: u32) -> Self {
        let mut new = self.clone();
        new.sample_rate = sample_rate;
        new
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
        let sc_offset = (beats_f64 * tp.tempo.superclocks_per_quarter as f64) as Superclock;
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

        let total_ticks = bars_diff * tpb * mp.meter.num as i64 + beats_diff * tpb + ticks_diff;
        let beats = Beats {
            ticks: mp.beat_position.ticks + total_ticks,
        };
        self.superclock_at_beats(beats)
    }

    pub fn sample_at_bbt(&self, bbt: BBT_Time) -> i64 {
        super::superclock::superclock_to_samples(self.superclock_at_bbt(bbt), self.sample_rate)
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
        match new.tempo_points.binary_search_by(|p| p.position.cmp(&sc)) {
            Ok(idx) => new.tempo_points[idx] = point,
            Err(idx) => new.tempo_points.insert(idx, point),
        }
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
        match new.meter_points.binary_search_by(|p| p.position.cmp(&sc)) {
            Ok(idx) => new.meter_points[idx] = point,
            Err(idx) => new.meter_points.insert(idx, point),
        }
        new
    }

    pub fn current_tempo(&self) -> &Tempo {
        &self.tempo_points[0].tempo
    }

    pub fn current_meter(&self) -> &Meter {
        &self.meter_points[0].meter
    }
}

/// A read-optimized shared value with snapshot reads.
///
/// Writers clone the current value, modify it, then publish a new snapshot.
/// Readers get an `Arc` snapshot that remains valid across later updates.
pub struct SwapLock<T> {
    value: RwLock<Arc<T>>,
    // Serializes writers while allowing readers during snapshot construction.
    writer: Mutex<()>,
}

impl<T> SwapLock<T> {
    pub fn new(value: T) -> Self {
        Self {
            value: RwLock::new(Arc::new(value)),
            writer: Mutex::new(()),
        }
    }

    /// Acquire an immutable snapshot of the current value.
    /// The returned snapshot remains valid across later writes.
    pub fn read(&self) -> Arc<T> {
        let guard = self
            .value
            .read()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        Arc::clone(&guard)
    }

    /// Update the value by cloning, modifying, and publishing a new snapshot.
    pub fn update(&self, f: impl FnOnce(&T) -> T) {
        let _writer = self
            .writer
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let current = self.read();
        let next = Arc::new(f(current.as_ref()));
        let mut guard = self
            .value
            .write()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        *guard = next;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::time::tempo::{Meter, Tempo};
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::{Arc, mpsc};
    use std::thread;
    use std::time::Duration;

    #[derive(Clone)]
    struct DropTracked {
        id: u8,
        drops: Arc<AtomicUsize>,
    }

    impl Drop for DropTracked {
        fn drop(&mut self) {
            self.drops.fetch_add(1, Ordering::SeqCst);
        }
    }

    #[test]
    fn swap_lock_read_snapshot_survives_update_until_released() {
        let drops = Arc::new(AtomicUsize::new(0));
        let lock = SwapLock::new(DropTracked {
            id: 1,
            drops: Arc::clone(&drops),
        });

        let snapshot = lock.read();
        assert_eq!(snapshot.id, 1);

        lock.update(|current| {
            assert_eq!(current.id, 1);
            DropTracked {
                id: 2,
                drops: Arc::clone(&drops),
            }
        });

        assert_eq!(drops.load(Ordering::SeqCst), 0);
        assert_eq!(snapshot.id, 1);

        drop(snapshot);
        assert_eq!(drops.load(Ordering::SeqCst), 1);
        assert_eq!(lock.read().id, 2);
    }

    #[test]
    fn swap_lock_update_does_not_hold_write_lock_while_building_snapshot() {
        let lock = Arc::new(SwapLock::new(1usize));
        let update_lock = Arc::clone(&lock);
        let (done_tx, done_rx) = mpsc::channel();

        thread::spawn(move || {
            update_lock.update(|current| {
                assert_eq!(*current, 1);
                assert_eq!(*update_lock.read(), 1);
                2
            });
            done_tx.send(()).unwrap();
        });

        done_rx
            .recv_timeout(Duration::from_millis(200))
            .expect("SwapLock::update held the write lock while running the update closure");
        assert_eq!(*lock.read(), 2);
    }

    #[test]
    fn test_default_tempo_map() {
        let map = TempoMap::new(Tempo::new(120.0, 4), Meter::new(4, 4), 48000);

        // At zero, BBT should be 1|1|0
        let bbt = map.bbt_at_superclock(0);
        assert_eq!(bbt.bars, 1);
        assert_eq!(bbt.beats, 1);
        assert_eq!(bbt.ticks, 0);

        // One bar at 120 BPM 4/4 = 2 seconds = 96000 samples at 48kHz
        let bar_samples = map.sample_at_bbt(BBT_Time::new(2, 1, 0));
        assert_eq!(bar_samples, 96000);

        // Round-trip
        let bbt2 = map.bbt_at_superclock(crate::time::superclock::samples_to_superclock(
            bar_samples,
            48000,
        ));
        assert_eq!(bbt2.bars, 2);
        assert_eq!(bbt2.beats, 1);
        assert_eq!(bbt2.ticks, 0);
    }

    #[test]
    fn test_tempo_change() {
        let map = TempoMap::new(Tempo::new(120.0, 4), Meter::new(4, 4), 48000);

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

    #[test]
    fn with_sample_rate_preserves_tempo_and_meter_points() {
        let map = TempoMap::new(Tempo::new(120.0, 4), Meter::new(4, 4), 48000);
        let beat_4 = Beats::from_ticks(4 * PPQN as i64);
        let map = map
            .with_tempo(Tempo::new(90.0, 4), beat_4)
            .with_meter(Meter::new(3, 4), beat_4)
            .with_sample_rate(96000);

        assert_eq!(map.sample_rate(), 96000);
        assert_eq!(map.current_tempo().bpm, 120.0);
        assert_eq!(map.current_meter().num, 4);

        let metric = map.metric_at_beats(beat_4);
        assert_eq!(metric.tempo.bpm, 90.0);
        assert_eq!(metric.meter.num, 3);
    }
}
