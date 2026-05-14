use super::beats::Beats;

/// Which time domain a position or duration is measured in.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum TimeDomain {
    AudioTime,
    BeatTime,
}

/// A position on the timeline, either in audio samples or musical beats.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum TimePos {
    Audio(i64),   // sample position
    Beats(Beats), // musical beat position
}

impl TimePos {
    pub fn zero(domain: TimeDomain) -> Self {
        match domain {
            TimeDomain::AudioTime => TimePos::Audio(0),
            TimeDomain::BeatTime => TimePos::Beats(Beats::ZERO),
        }
    }

    pub fn domain(&self) -> TimeDomain {
        match self {
            TimePos::Audio(_) => TimeDomain::AudioTime,
            TimePos::Beats(_) => TimeDomain::BeatTime,
        }
    }

    pub fn is_audio(&self) -> bool {
        matches!(self, TimePos::Audio(_))
    }

    pub fn is_beats(&self) -> bool {
        matches!(self, TimePos::Beats(_))
    }

    pub fn as_samples(&self) -> Option<i64> {
        match self {
            TimePos::Audio(s) => Some(*s),
            _ => None,
        }
    }

    pub fn as_beats(&self) -> Option<Beats> {
        match self {
            TimePos::Beats(b) => Some(*b),
            _ => None,
        }
    }
}

impl Default for TimePos {
    fn default() -> Self {
        TimePos::zero(TimeDomain::AudioTime)
    }
}
