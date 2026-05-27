use std::sync::{Mutex, OnceLock};

use crate::bridge_contract::BridgeHostContext;

static LATEST_HOST_CONTEXT: OnceLock<Mutex<Option<BridgeHostContext>>> = OnceLock::new();

pub(crate) fn publish_host_context(host_context: BridgeHostContext) {
    if let Ok(mut latest) = latest_store().try_lock() {
        *latest = Some(host_context);
    }
}

pub(crate) fn latest_host_context() -> Option<BridgeHostContext> {
    latest_store().lock().ok().and_then(|latest| *latest)
}

fn latest_store() -> &'static Mutex<Option<BridgeHostContext>> {
    LATEST_HOST_CONTEXT.get_or_init(|| Mutex::new(None))
}

#[cfg(test)]
pub(crate) fn clear_latest_host_context_for_test() {
    if let Ok(mut latest) = latest_store().lock() {
        *latest = None;
    }
}

#[cfg(test)]
pub(crate) fn test_host_context_guard() -> std::sync::MutexGuard<'static, ()> {
    static TEST_HOST_CONTEXT_LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    TEST_HOST_CONTEXT_LOCK
        .get_or_init(|| Mutex::new(()))
        .lock()
        .expect("host context test lock should not be poisoned")
}
