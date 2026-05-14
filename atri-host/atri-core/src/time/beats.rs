use std::fmt;
use std::ops::{Add, AddAssign, Div, Mul, Neg, Sub, SubAssign};

/// Pulses Per Quarter Note — the smallest division of musical time.
pub const PPQN: i32 = 1920;

/// A duration or position in musical time, measured in ticks.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Default)]
pub struct Beats {
    pub ticks: i64,
}

impl Beats {
    pub const ZERO: Beats = Beats { ticks: 0 };

    pub fn from_ticks(ticks: i64) -> Self {
        Self { ticks }
    }

    pub fn from_beats(beats: f64) -> Self {
        Self {
            ticks: (beats * PPQN as f64) as i64,
        }
    }

    pub fn to_beats_f64(self) -> f64 {
        self.ticks as f64 / PPQN as f64
    }

    pub fn beats_part(self) -> i64 {
        self.ticks / PPQN as i64
    }

    pub fn ticks_part(self) -> i32 {
        (self.ticks % PPQN as i64) as i32
    }
}

impl fmt::Display for Beats {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}|{}", self.beats_part(), self.ticks_part())
    }
}

impl Add for Beats {
    type Output = Self;
    fn add(self, rhs: Self) -> Self {
        Self { ticks: self.ticks + rhs.ticks }
    }
}

impl AddAssign for Beats {
    fn add_assign(&mut self, rhs: Self) {
        self.ticks += rhs.ticks;
    }
}

impl Sub for Beats {
    type Output = Self;
    fn sub(self, rhs: Self) -> Self {
        Self { ticks: self.ticks - rhs.ticks }
    }
}

impl SubAssign for Beats {
    fn sub_assign(&mut self, rhs: Self) {
        self.ticks -= rhs.ticks;
    }
}

impl Mul<f64> for Beats {
    type Output = Self;
    fn mul(self, rhs: f64) -> Self {
        Self { ticks: (self.ticks as f64 * rhs) as i64 }
    }
}

impl Div<f64> for Beats {
    type Output = Self;
    fn div(self, rhs: f64) -> Self {
        Self { ticks: (self.ticks as f64 / rhs) as i64 }
    }
}

impl Neg for Beats {
    type Output = Self;
    fn neg(self) -> Self {
        Self { ticks: -self.ticks }
    }
}
