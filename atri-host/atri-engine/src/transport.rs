#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TransportState {
    Stopped,
    Playing,
    Paused,
}

pub struct Transport {
    pub state: TransportState,
    pub position: i64, // current sample position
    pub speed: f64,    // 0.0, 1.0, -1.0
    pub loop_start: Option<i64>,
    pub loop_end: Option<i64>,
    default_speed: f64,
}

impl Transport {
    pub fn new() -> Self {
        Self {
            state: TransportState::Stopped,
            position: 0,
            speed: 0.0,
            loop_start: None,
            loop_end: None,
            default_speed: 1.0,
        }
    }

    pub fn play(&mut self) {
        self.state = TransportState::Playing;
        self.speed = self.default_speed;
    }

    pub fn stop(&mut self) {
        self.state = TransportState::Stopped;
        self.speed = 0.0;
        self.position = 0;
    }

    pub fn pause(&mut self) {
        self.state = TransportState::Paused;
        self.speed = 0.0;
    }

    pub fn seek(&mut self, position: i64) {
        self.position = position;
    }

    pub fn is_rolling(&self) -> bool {
        self.state == TransportState::Playing && self.speed != 0.0
    }

    pub fn advance(&mut self, nframes: usize) -> i64 {
        if self.is_rolling() {
            self.position += (nframes as f64 * self.speed) as i64;
            // Loop handling
            if let (Some(start), Some(end)) = (self.loop_start, self.loop_end) {
                if self.position >= end {
                    self.position = start + (self.position - end);
                }
            }
        }
        self.position
    }
}

impl Default for Transport {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_initial_state() {
        let t = Transport::new();
        assert_eq!(t.state, TransportState::Stopped);
        assert_eq!(t.position, 0);
        assert_eq!(t.speed, 0.0);
        assert!(!t.is_rolling());
    }

    #[test]
    fn test_play_stop_transitions() {
        let mut t = Transport::new();
        t.play();
        assert!(t.is_rolling());
        assert_eq!(t.speed, 1.0);
        t.pause();
        assert!(!t.is_rolling());
        assert_eq!(t.state, TransportState::Paused);
        t.stop();
        assert_eq!(t.position, 0);
        assert_eq!(t.state, TransportState::Stopped);
    }

    #[test]
    fn test_advance() {
        let mut t = Transport::new();
        t.play();
        let pos = t.advance(256);
        assert_eq!(pos, 256);
        assert_eq!(t.position, 256);
        t.advance(256);
        assert_eq!(t.position, 512);
    }

    #[test]
    fn test_no_advance_when_stopped() {
        let mut t = Transport::new();
        t.advance(256);
        assert_eq!(t.position, 0);
    }

    #[test]
    fn test_seek() {
        let mut t = Transport::new();
        t.play();
        t.seek(48000);
        assert_eq!(t.position, 48000);
    }

    #[test]
    fn test_loop() {
        let mut t = Transport::new();
        t.loop_start = Some(0);
        t.loop_end = Some(48000);
        t.play();
        t.advance(48000);
        assert_eq!(t.position, 0);
    }
}
