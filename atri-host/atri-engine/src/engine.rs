use std::sync::{Arc, Mutex};
use super::session::Session;

/// The audio engine manages the cpal audio stream and the session.
pub struct AudioEngine {
    pub session: Arc<Mutex<Session>>,
    sample_rate: u32,
    buffer_size: usize,
}

impl AudioEngine {
    pub fn new(sample_rate: u32, buffer_size: usize) -> Self {
        let session = Arc::new(Mutex::new(Session::new(sample_rate, buffer_size)));
        Self { session, sample_rate, buffer_size }
    }

    pub fn sample_rate(&self) -> u32 {
        self.sample_rate
    }

    pub fn buffer_size(&self) -> usize {
        self.buffer_size
    }
}
