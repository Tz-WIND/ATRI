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

    /// Convert to a reference. Unsafe: caller must ensure ptr is valid and lifetime is correct.
    pub unsafe fn as_ref(&self) -> &T {
        unsafe { &*self.ptr }
    }

    /// Convert to a mutable reference. Unsafe: caller must ensure exclusive access.
    pub unsafe fn as_mut(&self) -> &mut T {
        unsafe { &mut *self.ptr }
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_comptr_is_valid() {
        let val = 42u32;
        let ptr = ComPtr::new(&val as *const u32 as *mut u32);
        assert!(!ptr.is_null());
        assert_eq!(ptr.as_ptr(), &val as *const u32 as *mut u32);
    }

    #[test]
    fn null_comptr() {
        let ptr: ComPtr<u32> = ComPtr::new(std::ptr::null_mut());
        assert!(ptr.is_null());
    }

    #[test]
    fn clone_copies_pointer() {
        let val = 7u32;
        let a = ComPtr::new(&val as *const u32 as *mut u32);
        let b = a.clone();
        assert_eq!(a.as_ptr(), b.as_ptr());
    }

    #[test]
    fn as_ref_works() {
        let val = 99u32;
        let ptr = ComPtr::new(&val as *const u32 as *mut u32);
        let r: &u32 = unsafe { ptr.as_ref() };
        assert_eq!(*r, 99);
    }

    #[test]
    fn as_mut_works() {
        let mut val = 10u32;
        let ptr = ComPtr::new(&mut val as *mut u32);
        unsafe { *ptr.as_mut() = 20; }
        assert_eq!(val, 20);
    }

    #[test]
    fn send_sync_trait_check() {
        fn assert_send_sync<T: Send + Sync>() {}
        assert_send_sync::<ComPtr<u32>>();
    }
}
