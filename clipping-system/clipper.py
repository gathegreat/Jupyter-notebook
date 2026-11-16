"""STEP 4 - Cut the clip and make it vertical (9:16) for TikTok / Reels / Shorts.

The clip starts `lead_seconds` BEFORE the moment (default 40s, the video says
35-45s) so viewers see the build-up, and ends `tail_seconds` after it.

Landscape streams are placed in the middle of a 1080x1920 frame on top of a
blurred, zoomed copy of themselves (the standard "clip channel" look).

Two files are made:
  clip.mp4          full quality, this is what gets posted
  clip_preview.mp4  small copy that fits Discord's 10 MB upload limit

Try it on its own:
    python clipper.py input.mp4 <moment_second> out.mp4
"""
import shutil
import subprocess
import sys
from pathlib import Path

from common import log, require_tools
from highlight_finder import probe_duration

L = log("clip")

VERTICAL = ("[0:v]split=2[a][b];"
            "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:2[bg];"
            "[b]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]")


def cut(src, start, duration, out, vertical=True):
    """src is a file path, or a list of chunk paths to join (concat demuxer fixes the
    timestamps that restart at 0 in every chunk)."""
    require_tools("ffmpeg")
    cmd = ["ffmpeg", "-y", "-v", "fatal"]
    if isinstance(src, list):
        lst = Path(out).with_suffix(".concat.txt")
        lst.write_text("".join(f"file '{Path(p).resolve()}'\n" for p in src))
        cmd += ["-f", "concat", "-safe", "0", "-ss", f"{start:.2f}", "-i", str(lst)]
    else:
        cmd += ["-ss", f"{start:.2f}", "-i", str(src)]
    cmd += ["-t", f"{duration:.2f}"]
    if vertical:
        cmd += ["-filter_complex", VERTICAL, "-map", "[v]", "-map", "0:a?"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)]
    try:
        subprocess.run(cmd, check=True)
    finally:
        Path(out).with_suffix(".concat.txt").unlink(missing_ok=True)


def make_preview(full, out, max_mb=8):
    if Path(full).stat().st_size < max_mb * 1024 * 1024:   # already small enough for Discord
        shutil.copyfile(full, out)
        return
    dur = max(probe_duration(full), 1)
    video_kbps = int(max_mb * 8192 / dur) - 96
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(full), "-vf", "scale=-2:960",
                    "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{video_kbps}k",
                    "-maxrate", f"{video_kbps}k", "-bufsize", f"{video_kbps * 2}k",
                    "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out)], check=True)


def make_clip(chunks, chunk_index, moment, out_path, opts):
    """chunks: ordered list of finished chunk paths for one recording.
    moment: second inside chunks[chunk_index] where the highlight peaks.
    If the clip crosses into the previous/next chunk, those chunks are joined."""
    lead, tail = opts.get("lead_seconds", 40), opts.get("tail_seconds", 15)
    cur = Path(chunks[chunk_index])
    files, start, duration = [cur], moment - lead, lead + tail
    if start < 0 and chunk_index > 0:          # build-up started in the previous chunk
        prev = Path(chunks[chunk_index - 1])
        files.insert(0, prev)
        start += probe_duration(prev)
    elif start < 0:                           # very start of the recording: no earlier footage
        start, duration = 0, moment + tail
    if moment + tail > probe_duration(cur) and chunk_index + 1 < len(chunks):
        files.append(Path(chunks[chunk_index + 1]))   # reaction runs into the next chunk
    src = files if len(files) > 1 else str(cur)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cut(src, start, duration, out_path, opts.get("vertical", True))
    preview = out_path.with_name(out_path.stem + "_preview.mp4")
    make_preview(out_path, preview)
    L.info("clip ready: %s (%.0fs)", out_path.name, probe_duration(out_path))
    return out_path, preview


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit("usage: python clipper.py <video> <moment_second> <out.mp4>")
    make_clip([sys.argv[1]], 0, float(sys.argv[2]), sys.argv[3], {})
