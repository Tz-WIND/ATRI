use std::fmt;

/// Bar-Beats-Ticks position (1-indexed for bars and beats).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
#[allow(non_camel_case_types)]
pub struct BBT_Time {
    pub bars: i32,
    pub beats: i32,
    pub ticks: i32,
}

/// An offset in BBT space (can be negative).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
#[allow(non_camel_case_types)]
pub struct BBT_Offset {
    pub bars: i32,
    pub beats: i32,
    pub ticks: i32,
}

impl BBT_Time {
    pub fn new(bars: i32, beats: i32, ticks: i32) -> Self {
        Self { bars, beats, ticks }
    }
}

impl fmt::Display for BBT_Time {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}|{}|{}", self.bars, self.beats, self.ticks)
    }
}
