pub type Superclock = i64;

/// Superclock ticks per second.
/// Chosen so that it has integer factors for all common sample rates
/// (44100, 48000, 88200, 96000, 176400, 192000) and note type divisors
/// (1, 2, 4, 8, 16, 32). 508_032_000 = 2^8 × 3^4 × 5^3 × 7^2.
pub const SUPERCLOCK_TICKS_PER_SECOND: Superclock = 508_032_000;

#[inline]
pub const fn superclock_to_samples(sc: Superclock, sample_rate: u32) -> i64 {
    (sc * sample_rate as i64) / SUPERCLOCK_TICKS_PER_SECOND
}

#[inline]
pub const fn samples_to_superclock(samples: i64, sample_rate: u32) -> Superclock {
    (samples * SUPERCLOCK_TICKS_PER_SECOND) / sample_rate as i64
}
