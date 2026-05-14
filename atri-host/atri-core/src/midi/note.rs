use serde::{Deserialize, Serialize};

/// A high-level musical note with pitch, timing, and velocity.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct MidiNote {
    /// MIDI pitch (0-127, middle C = 60).
    pub pitch: u8,
    /// Start time in beats (quarter notes).
    pub start_beats: f64,
    /// Duration in beats (quarter notes).
    pub duration_beats: f64,
    /// MIDI velocity (0-127).
    pub velocity: u8,
}

impl MidiNote {
    pub fn new(pitch: u8, start_beats: f64, duration_beats: f64, velocity: u8) -> Self {
        Self { pitch, start_beats, duration_beats, velocity }
    }

    pub fn end_beats(&self) -> f64 {
        self.start_beats + self.duration_beats
    }
}
