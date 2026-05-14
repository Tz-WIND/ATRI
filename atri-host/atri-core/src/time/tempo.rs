use super::superclock::Superclock;

/// A tempo marking: speed in beats-per-minute.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Tempo {
    /// Quarter notes per minute.
    pub bpm: f64,
    /// The note value that equals one beat (4 = quarter, 8 = eighth, etc.).
    pub note_type: u8,
    /// Pre-computed: superclock ticks per quarter note at this tempo.
    pub superclocks_per_quarter: Superclock,
}

impl Tempo {
    pub fn new(bpm: f64, note_type: u8) -> Self {
        let superclocks_per_quarter = bpm_to_superclocks_per_quarter(bpm);
        Self {
            bpm,
            note_type,
            superclocks_per_quarter,
        }
    }
}

/// A time signature.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Meter {
    /// Beats per bar (numerator).
    pub num: u8,
    /// Note value per beat (denominator), e.g. 4 = quarter note.
    pub denom: u8,
}

impl Meter {
    pub fn new(num: u8, denom: u8) -> Self {
        Self { num, denom }
    }

    pub fn ticks_per_beat(&self) -> i32 {
        (4 * super::beats::PPQN) / self.denom as i32
    }

    pub fn ticks_per_bar(&self) -> i32 {
        self.ticks_per_beat() * self.num as i32
    }
}

/// A combined tempo + meter metric at a specific point on the timeline.
#[derive(Debug, Clone, Copy)]
pub struct TempoMetric {
    pub tempo: Tempo,
    pub meter: Meter,
}

impl TempoMetric {
    pub fn new(tempo: Tempo, meter: Meter) -> Self {
        Self { tempo, meter }
    }
}

use super::superclock::SUPERCLOCK_TICKS_PER_SECOND;

#[inline]
pub fn bpm_to_superclocks_per_quarter(bpm: f64) -> Superclock {
    (SUPERCLOCK_TICKS_PER_SECOND as f64 * 60.0 / bpm) as Superclock
}

#[inline]
pub fn superclocks_per_quarter_to_bpm(spq: Superclock) -> f64 {
    SUPERCLOCK_TICKS_PER_SECOND as f64 * 60.0 / spq as f64
}
