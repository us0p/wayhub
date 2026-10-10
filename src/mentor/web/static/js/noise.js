// Background-noise estimate for the voice interview (D68).
//
// The worklet reports the RMS level of each 100 ms mic frame (after the browser's own noise
// suppression). Speech has pauses, so a low percentile of the recent levels follows the
// background rather than the voice: that is the noise floor. The room counts as noisy when the
// floor stays above `enter` for `holdChecks` consecutive checks (one per second), and quiet
// again once it stays below `leave` (hysteresis, so the tip doesn't flicker).
//
// Window and percentile were chosen on simulated rooms (a fluent speaker with 4% pauses must not
// count as noise; a café at 0.03 RMS is flagged in ~10 s and cleared ~5 s after it gets quiet).
// The thresholds are a first guess (≈ -34 / -38 dBFS) to be tuned with real rooms; the floor
// is exposed on the voice panel as `data-noise-floor` for that. Frames while the agent's audio
// plays or the mic is muted should not be added (the caller skips them).

export class NoiseMeter {
  constructor({
    windowFrames = 150, // 15 s of 100 ms frames
    percentile = 0.03, // low enough to land on pauses even for a speaker who barely pauses
    enter = 0.02,
    leave = 0.012,
    holdChecks = 3,
    checkEvery = 10, // frames between checks: 1 s
  } = {}) {
    Object.assign(this, { windowFrames, percentile, enter, leave, holdChecks, checkEvery });
    this.levels = [];
    this.sinceCheck = 0;
    this.streak = 0;
    this.noisy = false;
    this.floor = null;
  }

  // Adds a frame's level; returns true when the noisy/quiet state changes.
  add(level) {
    this.levels.push(level);
    if (this.levels.length > this.windowFrames) this.levels.shift();
    if (++this.sinceCheck < this.checkEvery) return false;
    this.sinceCheck = 0;
    if (this.levels.length < this.windowFrames / 2) return false; // not enough to judge yet

    const sorted = [...this.levels].sort((a, b) => a - b);
    this.floor = sorted[Math.floor(sorted.length * this.percentile)];
    const flipping = this.noisy ? this.floor < this.leave : this.floor > this.enter;
    this.streak = flipping ? this.streak + 1 : 0;
    if (this.streak < this.holdChecks) return false;
    this.streak = 0;
    this.noisy = !this.noisy;
    return true;
  }
}
