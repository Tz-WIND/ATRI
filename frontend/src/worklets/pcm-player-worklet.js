class AtriPcmPlayer extends AudioWorkletProcessor {
  constructor() {
    super()
    this.queue = []
    this.current = null
    this.offset = 0
    this.port.onmessage = (event) => {
      if (event.data?.type !== 'samples' || !event.data.buffer) return
      this.queue.push({
        samples: new Float32Array(event.data.buffer),
        channels: event.data.channels || 2,
      })
      if (this.queue.length > 48) {
        this.queue.splice(0, this.queue.length - 48)
      }
    }
  }

  process(_inputs, outputs) {
    const output = outputs[0]
    const left = output[0]
    const right = output[1] || output[0]

    for (let i = 0; i < left.length; i += 1) {
      const frame = this.readFrame()
      left[i] = frame[0]
      right[i] = frame[1]
    }
    return true
  }

  readFrame() {
    while (!this.current || this.offset >= this.current.samples.length) {
      this.current = this.queue.shift()
      this.offset = 0
      if (!this.current) return [0, 0]
    }

    const samples = this.current.samples
    const channels = this.current.channels
    if (channels === 1) {
      const value = samples[this.offset] || 0
      this.offset += 1
      return [value, value]
    }

    const left = samples[this.offset] || 0
    const right = samples[this.offset + 1] || left
    this.offset += channels
    return [left, right]
  }
}

registerProcessor('atri-pcm-player', AtriPcmPlayer)
