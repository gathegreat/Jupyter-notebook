"""STEP 2 - Record a live stream in 10-minute chunks (plus the chat, on Twitch).

Streamlink grabs the stream and pipes it into ffmpeg, which cuts it into
chunk_0000.ts, chunk_0001.ts, ... and writes chunks.csv as each one finishes.
For Twitch, the chat is logged at the same time to chat.jsonl so the
highlight finder can see when chat explodes.

Try it on its own (Ctrl+C to stop):
    python recorder.py https://www.twitch.tv/somestreamer somestreamer
"""
import json
import random
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import websocket

from common import RECORDINGS, log, require_tools

L = log("record")


class TwitchChatLogger(threading.Thread):
    """Reads Twitch chat anonymously (no account needed) and logs every message with a timestamp."""

    def __init__(self, channel, out_path):
        super().__init__(daemon=True)
        self.channel = channel.lower()
        self.out_path = Path(out_path)
        self.stop_event = threading.Event()

    def run(self):
        backoff = 5
        while not self.stop_event.is_set():
            try:
                self._run_once()
                backoff = 5
            except Exception as e:
                L.warning("chat for %s disconnected (%s); retrying in %ds", self.channel, e, backoff)
                self.stop_event.wait(backoff)
                backoff = min(backoff * 2, 300)

    def _run_once(self):
        # Twitch chat over a secure WebSocket (port 443) works even behind strict firewalls
        ws = websocket.create_connection("wss://irc-ws.chat.twitch.tv:443", timeout=30)
        ws.send("PASS SCHMOOPIIE")
        ws.send(f"NICK justinfan{random.randint(10000, 99999)}")
        ws.send(f"JOIN #{self.channel}")
        try:
            with open(self.out_path, "a", encoding="utf-8") as out:
                while not self.stop_event.is_set():
                    try:
                        data = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    if not data:
                        raise ConnectionError("closed by Twitch")
                    for line in data.split("\r\n"):
                        if line.startswith("PING"):
                            ws.send(line.replace("PING", "PONG", 1))
                        elif " PRIVMSG " in line:
                            msg = line.split(" :", 1)[-1]
                            out.write(json.dumps({"t": time.time(), "msg": msg}, ensure_ascii=False) + "\n")
                    out.flush()
        finally:
            ws.close()

    def stop(self):
        self.stop_event.set()


class StreamRecorder:
    def __init__(self, streamer, chunk_seconds=600):
        self.streamer = streamer
        self.chunk_seconds = chunk_seconds
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.folder = RECORDINGS / f"{streamer['name']}_{stamp}"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.started_at = None
        self.procs = []
        self.chat = None

    @property
    def chunk_list(self):
        return self.folder / "chunks.csv"

    @property
    def chat_file(self):
        return self.folder / "chat.jsonl" if self.chat else None

    def start(self):
        require_tools("streamlink", "ffmpeg")
        sl = subprocess.Popen(
            ["streamlink", "--stdout", "--twitch-disable-ads", self.streamer["url"], "best"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        ff = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
             "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy",
             "-f", "segment", "-segment_time", str(self.chunk_seconds), "-reset_timestamps", "1",
             "-segment_list", str(self.chunk_list), "-segment_list_type", "csv",
             str(self.folder / "chunk_%04d.ts")],
            stdin=sl.stdout, stderr=subprocess.DEVNULL)
        sl.stdout.close()
        self.procs = [sl, ff]
        self.started_at = time.time()
        (self.folder / "meta.json").write_text(json.dumps(
            {"streamer": self.streamer, "started_at": self.started_at}, indent=2))
        if self.streamer["platform"] == "twitch":
            self.chat = TwitchChatLogger(self.streamer["name"], self.folder / "chat.jsonl")
            self.chat.start()
        L.info("recording %s -> %s", self.streamer["name"], self.folder)

    def is_running(self):
        return any(p.poll() is None for p in self.procs)

    def stop(self):
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
        for p in self.procs:
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
        if self.chat:
            self.chat.stop()
        L.info("stopped recording %s", self.streamer["name"])

    def finished_chunks(self):
        """Chunks ffmpeg has finished writing: [(path, start_sec, end_sec, wall_clock_start)]."""
        if not self.chunk_list.exists():
            return []
        rows = []
        for line in self.chunk_list.read_text().splitlines():
            parts = line.strip().split(",")
            if len(parts) == 3:
                rows.append((self.folder / parts[0], float(parts[1]), float(parts[2])))
        if not rows:
            return []
        first = rows[0][1]
        return [(p, s, e, self.started_at + (s - first)) for p, s, e in rows]


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("usage: python recorder.py <stream url> <name> [twitch|kick|youtube]")
    platform = sys.argv[3] if len(sys.argv) > 3 else ("twitch" if "twitch.tv" in sys.argv[1] else "other")
    rec = StreamRecorder({"name": sys.argv[2], "url": sys.argv[1], "platform": platform})
    rec.start()
    try:
        while rec.is_running():
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    rec.stop()
    print("chunks:", [str(c[0].name) for c in rec.finished_chunks()])
