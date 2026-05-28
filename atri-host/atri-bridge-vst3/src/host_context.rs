use std::sync::{Mutex, MutexGuard, OnceLock, TryLockError};

use crate::bridge_contract::BridgeHostContext;

static LATEST_HOST_CONTEXT: OnceLock<Mutex<Option<BridgeHostContext>>> = OnceLock::new();

pub(crate) fn publish_host_context(host_context: BridgeHostContext) {
    if let Some(mut latest) = try_lock_recover(latest_store()) {
        *latest = Some(host_context);
    }
}

pub(crate) fn latest_host_context() -> Option<BridgeHostContext> {
    *lock_recover(latest_store())
}

fn latest_store() -> &'static Mutex<Option<BridgeHostContext>> {
    LATEST_HOST_CONTEXT.get_or_init(|| Mutex::new(None))
}

static LATEST_HOST_APPLICATION_NAME: OnceLock<Mutex<Option<String>>> = OnceLock::new();

pub(crate) fn publish_host_application_name(name: impl Into<String>) {
    let name = name.into().trim().to_string();
    if name.is_empty() {
        return;
    }
    if let Some(mut latest) = try_lock_recover(host_application_name_store()) {
        *latest = Some(name);
    }
}

pub(crate) fn latest_host_application_name() -> Option<String> {
    (*lock_recover(host_application_name_store())).clone()
}

fn host_application_name_store() -> &'static Mutex<Option<String>> {
    LATEST_HOST_APPLICATION_NAME.get_or_init(|| Mutex::new(None))
}

fn lock_recover<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

fn try_lock_recover<T>(mutex: &Mutex<T>) -> Option<MutexGuard<'_, T>> {
    match mutex.try_lock() {
        Ok(guard) => Some(guard),
        Err(TryLockError::Poisoned(poisoned)) => Some(poisoned.into_inner()),
        Err(TryLockError::WouldBlock) => None,
    }
}

#[cfg(test)]
pub(crate) fn clear_latest_host_context_for_test() {
    *lock_recover(latest_store()) = None;
}

#[cfg(test)]
pub(crate) fn clear_latest_host_application_name_for_test() {
    *lock_recover(host_application_name_store()) = None;
}

#[cfg(test)]
pub(crate) fn test_host_context_guard() -> std::sync::MutexGuard<'static, ()> {
    static TEST_HOST_CONTEXT_LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    TEST_HOST_CONTEXT_LOCK
        .get_or_init(|| Mutex::new(()))
        .lock()
        .expect("host context test lock should not be poisoned")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::panic::{AssertUnwindSafe, catch_unwind};

    #[test]
    fn latest_host_context_recovers_poisoned_store() {
        let _guard = test_host_context_guard();
        clear_latest_host_context_for_test();
        let host_context = BridgeHostContext {
            sample_rate: Some(48_000.0),
            block_size: Some(256),
            is_playing: Some(true),
            tempo_bpm: Some(120.0),
            time_signature: Some([4, 4]),
        };
        let _ = catch_unwind(AssertUnwindSafe(|| {
            let mut latest = latest_store().lock().unwrap();
            *latest = Some(host_context);
            panic!("poison host context");
        }));

        assert_eq!(latest_host_context(), Some(host_context));
        clear_latest_host_context_for_test();
    }
}
