"""STEP 3 - Find the moments worth clipping.

Every second of a chunk gets a score from three signals:
  * audio    - sudden loud reactions (laughing, screaming, hype) compared with
               how loud this streamer normally is
  * chat_rate - chat suddenly moving much faster than normal
  * emotes   - a burst of laugh/hype emotes and "clip it" messages
Each signal is turned into "how unusual is this second" (a robust z-score),
the signals are combined with the weights in config.json, and the highest-
scoring moments (spaced apart) become clip candidates.

Viewers react a few seconds AFTER something happens on stream, so chat
signals are shifted back by `chat_delay_seconds`.

Try it on its own:
    python highlight_finder.py some_video.mp4 [chat.jsonl] [wall_clock_start]
"""
import json
import subprocess
import sys

import numpy as np

from common import log

L = log("highlights")

HYPE_TOKENS = {
    # laughing
    "lul", "lol", "lmao", "lmfao", "kekw", "omegalul", "icant", "😂", "🤣", "💀", "xd", "haha",
    # hype / shock
    "pog", "pogchamp", "poggers", "pogu", "w", "ww", "www", "holy", "omg", "wtf", "🔥", "😱",
    "monkas", "pausechamp", "kappa",
    # people literally asking for a clip
    "clip", "clipit", "clipped",
}

DEFAULTS = {"weights": {"audio": 0.6, "chat_rate": 1.0, "emotes": 0.8},
            "min_score": 2.5, "min_gap_seconds": 90, "max_clips_per_chunk": 3,
            "chat_delay_seconds": 5}


def probe_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
    return float(out.strip() or 0)


def loudness_per_second(path, rate=8000):
    """Loudness (dB) of every second of audio."""
    raw = subprocess.run(["ffmpeg", "-v", "fatal", "-i", str(path), "-vn", "-ac", "1",
                          "-ar", str(rate), "-f", "s16le", "-"], capture_output=True).stdout
    a = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    n = len(a) // rate
    if n == 0:
        return np.zeros(0)
    rms = np.sqrt(np.mean(a[: n * rate].reshape(n, rate) ** 2, axis=1) + 1e-10)
    return 20 * np.log10(rms)


def robust_z(x, min_spread):
    """How unusual each value is compared with the typical value (ignores outliers).
    min_spread stops tiny wobbles in very steady signals from looking like spikes."""
    if len(x) == 0:
        return x
    med = np.median(x)
    spread = max(np.median(np.abs(x - med)) * 1.4826, min_spread)
    return (x - med) / spread


def smooth(x, win):
    if len(x) == 0 or win <= 1:
        return x
    # pad with the edge values (not zeros) so the first/last seconds don't look like spikes
    padded = np.pad(x, (win // 2, win - 1 - win // 2), mode="edge")
    return np.convolve(padded, np.ones(win) / win, mode="valid")


def chat_signals(chat_file, wall_start, n_seconds, delay):
    """Messages per second and hype-emotes per second, lined up with the video."""
    rate = np.zeros(n_seconds)
    hype = np.zeros(n_seconds)
    if not chat_file:
        return None, None
    try:
        lines = open(chat_file, encoding="utf-8").read().splitlines()
    except FileNotFoundError:
        return None, None
    for line in lines:
        try:
            m = json.loads(line)
        except ValueError:
            continue
        sec = int(m["t"] - wall_start - delay)
        if 0 <= sec < n_seconds:
            rate[sec] += 1
            words = m["msg"].lower().replace("!", " ").split()
            hype[sec] += sum(1 for w in words if w in HYPE_TOKENS or w.startswith("kek")
                             or w.startswith("lul") or w.startswith("pog"))
    if rate.sum() < 20:  # too little chat to be meaningful
        return None, None
    return rate, hype


def find_highlights(video, chat_file=None, wall_start=0.0, opts=None):
    """Returns a list of {"t": second in video, "score": float, "reasons": {...}} best first."""
    o = {**DEFAULTS, **(opts or {})}
    w = {**DEFAULTS["weights"], **o.get("weights", {})}

    loud = loudness_per_second(video)
    n = len(loud)
    if n == 0:
        return []
    signals = {"audio": robust_z(smooth(loud, 3), min_spread=2.0)}          # dB

    rate, hype = chat_signals(chat_file, wall_start, n, o["chat_delay_seconds"])
    if rate is not None:
        signals["chat_rate"] = robust_z(smooth(rate, 10), min_spread=0.5)  # msgs/sec
        signals["emotes"] = robust_z(smooth(hype, 10), min_spread=0.3)     # emotes/sec

    total_w = sum(w[k] for k in signals)
    score = sum(w[k] * signals[k] for k in signals) / total_w

    picked = []
    for t in np.argsort(score)[::-1]:
        if score[t] < o["min_score"] or len(picked) >= o["max_clips_per_chunk"]:
            break
        if all(abs(int(t) - p["t"]) >= o["min_gap_seconds"] for p in picked):
            picked.append({"t": int(t), "score": round(float(score[t]), 2),
                           "reasons": {k: round(float(v[t]), 2) for k, v in signals.items()}})
    return picked


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: python highlight_finder.py <video> [chat.jsonl] [wall_clock_start]")
    res = find_highlights(sys.argv[1],
                          sys.argv[2] if len(sys.argv) > 2 else None,
                          float(sys.argv[3]) if len(sys.argv) > 3 else 0.0,
                          {"min_gap_seconds": 20, "min_score": 1.5})
    print(json.dumps(res, indent=2) if res else "No moments stood out enough.")
