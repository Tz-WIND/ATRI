use super::timepos::{TimeDomain, TimePos};

/// A duration on the timeline, tied to a specific time domain.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct TimeCnt {
    pub distance: i64,
    pub domain: TimeDomain,
}

impl TimeCnt {
    pub fn zero(domain: TimeDomain) -> Self {
        Self { distance: 0, domain }
    }

    pub fn from_samples(samples: i64) -> Self {
        Self {
            distance: samples,
            domain: TimeDomain::AudioTime,
        }
    }

    pub fn from_beats(beats: f64) -> Self {
        use super::beats::PPQN;
        Self {
            distance: (beats * PPQN as f64) as i64,
            domain: TimeDomain::BeatTime,
        }
    }

    pub fn end_position(&self, start: TimePos) -> Option<TimePos> {
        match (start, self.domain) {
            (TimePos::Audio(s), TimeDomain::AudioTime) => {
                Some(TimePos::Audio(s + self.distance))
            }
            (TimePos::Beats(b), TimeDomain::BeatTime) => {
                use super::beats::Beats;
                Some(TimePos::Beats(Beats {
                    ticks: b.ticks + self.distance,
                }))
            }
            _ => None, // domain mismatch — needs TempoMap
        }
    }
}
