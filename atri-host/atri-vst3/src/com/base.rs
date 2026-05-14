/// A COM-style interface pointer with manual reference counting.
/// Wraps a raw pointer to a VST3 interface vtable.
pub struct ComPtr<T> {
    ptr: *mut T,
}

impl<T> ComPtr<T> {
    pub fn new(ptr: *mut T) -> Self {
        Self { ptr }
    }

    pub fn as_ptr(&self) -> *mut T {
        self.ptr
    }

    pub fn is_null(&self) -> bool {
        self.ptr.is_null()
    }
}

impl<T> Clone for ComPtr<T> {
    fn clone(&self) -> Self {
        // In a real COM implementation, we'd call AddRef here.
        // For Phase 1, we just copy the raw pointer and manage lifetime manually.
        Self { ptr: self.ptr }
    }
}

impl<T> Drop for ComPtr<T> {
    fn drop(&mut self) {
        // In a real COM implementation, we'd call Release here.
        // For Phase 1, we avoid double-free by having the factory own the plugin.
    }
}

unsafe impl<T> Send for ComPtr<T> {}
unsafe impl<T> Sync for ComPtr<T> {}
