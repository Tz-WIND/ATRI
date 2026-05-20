use std::sync::{Arc, Mutex, TryLockError};

use super::session::Session;

/// The audio engine manages the cpal audio stream and the session.
pub struct AudioEngine {
    session: Arc<Mutex<Session>>,
    sample_rate: u32,
    buffer_size: usize,
}

impl AudioEngine {
    pub fn new(sample_rate: u32, buffer_size: usize) -> Self {
        let session = Arc::new(Mutex::new(Session::new(sample_rate, buffer_size)));
        Self {
            session,
            sample_rate,
            buffer_size,
        }
    }

    pub fn sample_rate(&self) -> u32 {
        self.sample_rate
    }

    pub fn buffer_size(&self) -> usize {
        self.buffer_size
    }

    pub fn with_session<T>(&self, f: impl FnOnce(&mut Session) -> T) -> T {
        let mut session = self
            .session
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        f(&mut session)
    }

    pub fn try_with_session<T>(&self, f: impl FnOnce(&mut Session) -> T) -> Option<T> {
        match self.session.try_lock() {
            Ok(mut session) => Some(f(&mut session)),
            Err(TryLockError::Poisoned(poisoned)) => {
                let mut session = poisoned.into_inner();
                Some(f(&mut session))
            }
            Err(TryLockError::WouldBlock) => None,
        }
    }

    pub fn reconfigure(&mut self, sample_rate: u32, buffer_size: usize) {
        if self.sample_rate == sample_rate && self.buffer_size == buffer_size {
            return;
        }

        let mut session = self
            .session
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        session.reconfigure(sample_rate, buffer_size);

        self.sample_rate = sample_rate;
        self.buffer_size = buffer_size;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reconfigure_keeps_existing_session() {
        let mut engine = AudioEngine::new(48_000, 128);
        let original_session = Arc::clone(&engine.session);

        {
            let mut session = engine.session.lock().unwrap();
            let track = session.add_track("Keys".into());
            assert!(session.set_track_volume(track, 0.5));
            assert!(session.set_track_pan(track, -0.25));
            assert!(session.set_track_mute(track, true));
        }

        engine.reconfigure(44_100, 256);

        assert!(Arc::ptr_eq(&original_session, &engine.session));
        assert_eq!(engine.sample_rate(), 44_100);
        assert_eq!(engine.buffer_size(), 256);

        let session = engine.session.lock().unwrap();
        assert_eq!(session.sample_rate, 44_100);
        assert_eq!(session.buffer_size, 256);
        assert_eq!(session.tempo_map.read().sample_rate(), 44_100);
        assert_eq!(session.routes.len(), 1);

        let route = session.routes[0].lock().unwrap();
        assert_eq!(route.name, "Keys");
        assert_eq!(route.gain.target, 0.5);
        assert_eq!(route.pan.value, -0.25);
        assert!(route.mute);
    }

    #[test]
    fn try_with_session_returns_none_when_session_is_locked() {
        let engine = AudioEngine::new(48_000, 128);
        let _session_guard = engine.session.lock().unwrap();

        assert!(
            engine
                .try_with_session(|session| session.sample_rate)
                .is_none()
        );
    }
}
