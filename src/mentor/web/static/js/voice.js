// Voice interview client (D5, D25–D27, M6). Loaded as a module on the interview page.
//
// Mic → AudioWorklet (16 kHz PCM16, 100 ms frames) → WebSocket → server pipeline; the server
// sends back 24 kHz PCM16 audio, played through a gapless AudioContext queue, and JSON events
// for the live transcript, progress and barge-in (`stop_audio`). When voice ends for any reason
// the page reloads into the text interview (D26), with `?voz=<reason>` for a notice.
// No inline scripts or styles (CSP, D37); every user-facing string comes from the template (D3).

import { NoiseMeter } from "./noise.js";

const TTS_RATE = 24000;
const LEVEL_GAIN = 4; // speech RMS is small; scale it for the meter

const byId = (id) => document.getElementById(id);
const show = (el) => el && el.classList.remove("hidden");
const hide = (el) => el && el.classList.add("hidden");

const panel = byId("voice-panel");
const supported = Boolean(
  panel &&
    navigator.mediaDevices &&
    navigator.mediaDevices.getUserMedia &&
    window.AudioWorkletNode &&
    window.WebSocket,
);
if (supported && panel.dataset.exhausted !== "true") {
  document.documentElement.classList.add("voice-ok"); // reveals the mic button (CSS)
}

let active = null;

// Delegated: the composer (and its mic button) is re-rendered by htmx after text replies.
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-voice-start]");
  if (!button || button.disabled || active || !supported) return;
  active = new VoiceSession();
  active.start();
});

class VoiceSession {
  constructor() {
    this.ready = false;
    this.ended = false;
    this.muted = false;
    this.thinking = false;
    this.sources = new Set();
    this.playhead = 0;
    this.oddByte = null;
    this.userBubble = null;
    this.agentBubble = null;
    this.drained = null;
    this.noise = new NoiseMeter();
  }

  async start() {
    hide(byId("voice-notice-microphone"));
    // Created inside the click, so browsers allow it to play audio.
    this.context = new AudioContext();
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true, // keeps the agent's voice from triggering barge-in
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      await this.context.resume();
      await this.context.audioWorklet.addModule(new URL("./pcm-capture.js", import.meta.url));
      const source = this.context.createMediaStreamSource(this.stream);
      this.capture = new AudioWorkletNode(this.context, "pcm-capture");
      source.connect(this.capture);
      this.capture.connect(this.context.destination); // keeps it processing; outputs silence
    } catch {
      this.cleanup();
      active = null;
      show(byId("voice-notice-microphone"));
      return;
    }

    hide(byId("composer"));
    show(panel);
    this.refreshStatus();
    this.capture.port.onmessage = ({ data }) => this.onFrame(data);
    byId("voice-mute").onclick = () => this.toggleMute();
    byId("voice-end").onclick = () => this.requestEnd();
    window.addEventListener("pagehide", () => this.cleanup());

    const scheme = location.protocol === "https:" ? "wss" : "ws";
    this.socket = new WebSocket(`${scheme}://${location.host}/entrevista/voz`);
    this.socket.binaryType = "arraybuffer";
    this.socket.onmessage = (message) => {
      if (typeof message.data === "string") this.onEvent(JSON.parse(message.data));
      else this.play(message.data);
    };
    this.socket.onclose = () => {
      if (!this.ended) this.finish("failure"); // dropped connection: fall back to text
    };
  }

  // -- microphone ---------------------------------------------------------------------

  onFrame({ pcm, level }) {
    byId("voice-level").value = this.muted ? 0 : Math.min(1, level * LEVEL_GAIN);
    // Background noise (D68): skip frames where the agent's voice or a muted mic would skew it.
    if (!this.muted && !this.sources.size && this.noise.add(level)) {
      byId("voice-noise-tip").classList.toggle("hidden", !this.noise.noisy);
    }
    if (this.noise.floor !== null) panel.dataset.noiseFloor = this.noise.floor.toFixed(4);
    if (this.ready && !this.muted && this.socket.readyState === WebSocket.OPEN) {
      this.socket.send(pcm);
    }
  }

  toggleMute() {
    this.muted = !this.muted;
    this.send({ type: this.muted ? "mute" : "unmute" });
    const button = byId("voice-mute");
    button.setAttribute("aria-pressed", String(this.muted));
    for (const label of button.querySelectorAll("[data-when]")) {
      label.classList.toggle("hidden", (label.dataset.when === "muted") !== this.muted);
    }
    this.refreshStatus();
  }

  // -- server events ------------------------------------------------------------------

  onEvent(event) {
    switch (event.type) {
      case "ready":
        this.ready = true;
        this.showRemaining(event.remaining_seconds);
        break;
      case "remaining":
        this.showRemaining(event.seconds);
        break;
      case "transcript":
        if (!event.text) {
          // Only noise or a filler was heard ("hm"): drop the live bubble.
          if (this.userBubble) this.userBubble.remove();
          this.userBubble = null;
          break;
        }
        this.userBubble ||= this.bubble("user");
        this.setText(this.userBubble, event.text);
        break;
      case "user_turn":
        this.userBubble ||= this.bubble("user");
        this.setText(this.userBubble, event.text);
        this.userBubble.removeAttribute("aria-busy");
        this.userBubble = null;
        this.thinking = true;
        break;
      case "agent_text":
        this.agentBubble ||= this.bubble("assistant", this.userBubble);
        this.setText(this.agentBubble, this.text(this.agentBubble) + event.text);
        this.thinking = false;
        break;
      case "agent_done":
        if (this.agentBubble) this.agentBubble.removeAttribute("aria-busy");
        this.agentBubble = null;
        this.thinking = false;
        this.swapProgress(event.progress_html);
        break;
      case "stop_audio":
        this.stopAudio();
        break;
      case "end":
        this.finish(event.reason);
        return;
    }
    this.refreshStatus();
  }

  // -- playback -----------------------------------------------------------------------

  play(buffer) {
    let bytes = new Uint8Array(buffer);
    if (this.oddByte !== null) {
      const merged = new Uint8Array(bytes.length + 1);
      merged[0] = this.oddByte;
      merged.set(bytes, 1);
      bytes = merged;
      this.oddByte = null;
    }
    if (bytes.length % 2) {
      this.oddByte = bytes[bytes.length - 1];
      bytes = bytes.subarray(0, bytes.length - 1);
    }
    if (!bytes.length) return;
    const samples = new Int16Array(bytes.slice().buffer); // PCM16 little-endian
    const audio = this.context.createBuffer(1, samples.length, TTS_RATE);
    const channel = audio.getChannelData(0);
    for (let i = 0; i < samples.length; i++) channel[i] = samples[i] / 32768;

    const node = this.context.createBufferSource();
    node.buffer = audio;
    node.connect(this.context.destination);
    const at = Math.max(this.context.currentTime + 0.05, this.playhead);
    node.start(at);
    this.playhead = at + audio.duration;
    this.sources.add(node);
    node.onended = () => {
      this.sources.delete(node);
      if (!this.sources.size && this.drained) this.drained();
      this.refreshStatus();
    };
    this.refreshStatus();
  }

  stopAudio() {
    for (const node of this.sources) {
      try {
        node.stop();
      } catch {
        // already stopped
      }
    }
    this.sources.clear();
    this.playhead = 0;
    this.oddByte = null;
  }

  playbackDone() {
    if (!this.sources.size) return Promise.resolve();
    const done = new Promise((resolve) => (this.drained = resolve));
    const timeout = new Promise((resolve) => setTimeout(resolve, 30000));
    return Promise.race([done, timeout]);
  }

  // -- ending -------------------------------------------------------------------------

  requestEnd() {
    this.stopAudio();
    if (!this.send({ type: "end" })) this.finish("ended");
    setTimeout(() => this.finish("ended"), 1500); // the server didn't answer
  }

  async finish(reason) {
    if (this.ended) return;
    this.ended = true;
    if (this.capture) this.capture.port.onmessage = null;
    if (reason === "completed") await this.playbackDone(); // let the goodbye be heard
    this.cleanup();
    // `completed` makes the page open the closing modal, like the text interview (D61).
    location.assign(reason === "ended" ? "/entrevista" : `/entrevista?voz=${encodeURIComponent(reason)}`);
  }

  cleanup() {
    if (this.stream) for (const track of this.stream.getTracks()) track.stop();
    if (this.socket && this.socket.readyState <= WebSocket.OPEN) this.socket.close();
    if (this.context && this.context.state !== "closed") this.context.close();
  }

  send(message) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return false;
    this.socket.send(JSON.stringify(message));
    return true;
  }

  // -- UI -----------------------------------------------------------------------------

  refreshStatus() {
    let state = "listening";
    if (!this.ready) state = "connecting";
    else if (this.sources.size) state = "speaking";
    else if (this.thinking) state = "thinking";
    else if (this.muted) state = "muted";
    byId("voice-status").textContent = panel.dataset[`label${state[0].toUpperCase()}${state.slice(1)}`];
  }

  showRemaining(seconds) {
    byId("voice-remaining").textContent =
      seconds == null ? "" : panel.dataset.remaining.replace("{n}", Math.ceil(seconds / 60));
  }

  // A transcript bubble cloned from the page's <template> (same markup as server bubbles).
  bubble(role, before = null) {
    const template = byId(`voice-bubble-${role}`);
    const node = template.content.firstElementChild.cloneNode(true);
    node.setAttribute("aria-busy", "true");
    const transcript = byId("transcript");
    if (before && before.parentNode === transcript) transcript.insertBefore(node, before);
    else transcript.append(node);
    return node;
  }

  text(bubble) {
    return bubble.querySelector("p").textContent;
  }

  setText(bubble, text) {
    bubble.querySelector("p").textContent = text; // textContent: never parsed as HTML
  }

  swapProgress(html) {
    const template = document.createElement("template");
    template.innerHTML = html; // rendered and escaped by the server's templates
    for (const element of [...template.content.children]) {
      const current = element.id && byId(element.id);
      if (!current) continue;
      if (current.open) element.open = true; // keep the mobile checklist expanded
      current.replaceWith(element);
    }
  }
}
