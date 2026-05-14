use super::buffer::AudioBuffer;

/// A collection of audio buffers, indexed by port number.
/// Used to pass audio data between processors in a chain.
#[derive(Debug, Clone)]
pub struct BufferSet {
    buffers: Vec<AudioBuffer>,
}

impl BufferSet {
    pub fn new(num_ports: usize, channels: u16, capacity: usize) -> Self {
        Self {
            buffers: (0..num_ports)
                .map(|_| AudioBuffer::new(channels, capacity))
                .collect(),
        }
    }

    pub fn len(&self) -> usize {
        self.buffers.len()
    }

    pub fn get(&self, index: usize) -> Option<&AudioBuffer> {
        self.buffers.get(index)
    }

    pub fn get_mut(&mut self, index: usize) -> Option<&mut AudioBuffer> {
        self.buffers.get_mut(index)
    }

    pub fn silence(&mut self, nframes: usize) {
        for buf in &mut self.buffers {
            buf.silence(nframes);
        }
    }

    pub fn resize(&mut self, capacity: usize) {
        for buf in &mut self.buffers {
            buf.resize(capacity);
        }
    }
}
