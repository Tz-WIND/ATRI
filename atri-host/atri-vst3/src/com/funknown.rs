/// Placeholder for VST3 FUnknown base interface.
/// In VST3, all interfaces inherit from FUnknown which provides
/// queryInterface, addRef, and release methods.
pub trait FUnknown {
    fn add_ref(&self);
    fn release(&self);
}
