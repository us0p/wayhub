// AudioWorklet processor for the voice interview (D25): converts the microphone (at the
// context's sample rate) to 16 kHz PCM16 mono and posts 100 ms frames with their RMS level.
// Downsampling averages the input samples covering each output sample (a box filter), which
// is enough anti-aliasing for speech recognition.

const TARGET_RATE = 16000;
const FRAME_SAMPLES = TARGET_RATE / 10;

class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET_RATE; // `sampleRate` is a global of the worklet scope
    this.carry = new Float32Array(0); // input not yet consumed
    this.position = 0; // fractional read position into `carry`
    this.frame = new Int16Array(FRAME_SAMPLES);
    this.filled = 0;
    this.sumSquares = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    const data = new Float32Array(this.carry.length + channel.length);
    data.set(this.carry);
    data.set(channel, this.carry.length);

    let t = this.position;
    while (t + this.ratio <= data.length) {
      const start = Math.floor(t);
      const end = Math.max(Math.floor(t + this.ratio), start + 1);
      let sum = 0;
      for (let i = start; i < end; i++) sum += data[i];
      this.push(sum / (end - start));
      t += this.ratio;
    }
    const kept = Math.floor(t);
    this.carry = data.slice(kept);
    this.position = t - kept;
    return true;
  }

  push(value) {
    const s = Math.max(-1, Math.min(1, value));
    this.sumSquares += s * s;
    this.frame[this.filled++] = s < 0 ? s * 0x8000 : s * 0x7fff;
    if (this.filled === FRAME_SAMPLES) {
      const level = Math.sqrt(this.sumSquares / FRAME_SAMPLES);
      this.port.postMessage({ pcm: this.frame.buffer, level }, [this.frame.buffer]);
      this.frame = new Int16Array(FRAME_SAMPLES);
      this.filled = 0;
      this.sumSquares = 0;
    }
  }
}

registerProcessor("pcm-capture", PcmCapture);
