use super::superclock::Superclock;

pub const MIN_BPM: f64 = 1.0;
pub const MAX_BPM: f64 = 999.0;
pub const VALID_METER_DENOMINATORS: &[u8] = &[1, 2, 4, 8, 16, 32, 64];

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
        assert!(
            Self::is_valid_bpm(bpm),
            "tempo bpm must be finite and between {MIN_BPM} and {MAX_BPM}"
        );
        assert!(
            Meter::is_valid_denominator(note_type),
            "tempo note_type must be one of {VALID_METER_DENOMINATORS:?}"
        );
        let superclocks_per_quarter = bpm_to_superclocks_per_quarter(bpm);
        Self {
            bpm,
            note_type,
            superclocks_per_quarter,
        }
    }

    pub fn try_new(bpm: f64, note_type: u8) -> Option<Self> {
        if Self::is_valid_bpm(bpm) && Meter::is_valid_denominator(note_type) {
            Some(Self::new(bpm, note_type))
        } else {
            None
        }
    }

    pub fn is_valid_bpm(bpm: f64) -> bool {
        bpm.is_finite() && (MIN_BPM..=MAX_BPM).contains(&bpm)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tempo_try_new_rejects_non_finite_and_out_of_range_bpm() {
        assert!(Tempo::try_new(1.0, 4).is_some());
        assert!(Tempo::try_new(999.0, 4).is_some());

        for bpm in [
            -1.0,
            0.0,
            0.999,
            1000.0,
            f64::NAN,
            f64::INFINITY,
            f64::NEG_INFINITY,
        ] {
            assert!(Tempo::try_new(bpm, 4).is_none());
        }
    }

    #[test]
    fn meter_try_new_rejects_zero_and_uncommon_denominator() {
        assert!(Meter::try_new(4, 4).is_some());
        assert!(Meter::try_new(7, 8).is_some());

        for (num, denom) in [(0, 4), (4, 0), (4, 3), (4, 128)] {
            assert!(Meter::try_new(num, denom).is_none());
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
        assert!(
            Self::is_valid(num, denom),
            "meter must have a positive numerator and denominator in {VALID_METER_DENOMINATORS:?}"
        );
        Self { num, denom }
    }

    pub fn try_new(num: u8, denom: u8) -> Option<Self> {
        Self::is_valid(num, denom).then_some(Self { num, denom })
    }

    pub fn is_valid(num: u8, denom: u8) -> bool {
        num > 0 && Self::is_valid_denominator(denom)
    }

    pub fn is_valid_denominator(denom: u8) -> bool {
        VALID_METER_DENOMINATORS.contains(&denom)
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
