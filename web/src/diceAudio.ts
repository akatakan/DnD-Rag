let audioContext: AudioContext | null = null;
let lastImpactAt = 0;

export function primeDiceAudio() {
  if (typeof AudioContext === "undefined") return;
  try {
    audioContext ??= new AudioContext();
  } catch {
    audioContext = null;
    return;
  }
  if (audioContext.state === "suspended") {
    audioContext.resume().catch(() => undefined);
  }
}

export function playDiceImpact(volume: number) {
  if (!audioContext || audioContext.state !== "running") return;
  const now = audioContext.currentTime;
  if (now - lastImpactAt < 0.08) return;
  lastImpactAt = now;
  const oscillator = audioContext.createOscillator();
  const gain = audioContext.createGain();
  oscillator.type = "triangle";
  oscillator.frequency.setValueAtTime(150 + Math.random() * 90, now);
  oscillator.frequency.exponentialRampToValueAtTime(55, now + 0.055);
  gain.gain.setValueAtTime(Math.min(0.075, volume * 0.009), now);
  gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.065);
  oscillator.connect(gain).connect(audioContext.destination);
  oscillator.addEventListener("ended", () => {
    oscillator.disconnect();
    gain.disconnect();
  }, { once: true });
  oscillator.start(now);
  oscillator.stop(now + 0.07);
}

export function releaseDiceAudio() {
  const current = audioContext;
  audioContext = null;
  lastImpactAt = 0;
  if (current && current.state !== "closed") {
    current.close().catch(() => undefined);
  }
}
