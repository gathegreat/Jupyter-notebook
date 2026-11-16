"""THE WHOLE SYSTEM - run this one file.

    python run.py              # full system with Discord approvals + Post for Me
    python run.py --no-discord # test mode: prints to the terminal, saves clips, never posts

Loop:
  every check_interval_seconds  -> who is live? start/stop recorders  (step 1 + 2)
  every 30 seconds              -> any new finished 10-min chunk? find highlights,
                                   cut vertical clips, send to Discord      (step 3 + 4 + 5)
  you react ✅ on a clip in Discord -> posted to all socials via Post for Me (step 6)
  you react ❌                       -> clip is discarded
"""
import asyncio
import sys
import threading
import time
import traceback

from clipper import make_clip
from common import CLIPS, load_config, load_state, log, require_tools, save_state
from highlight_finder import find_highlights
from live_checker import check_all
from postforme import post_clip
from recorder import StreamRecorder

L = log("main")
APPROVE, REJECT = "✅", "❌"


class Pipeline:
    def __init__(self, cfg, notify):
        self.cfg = cfg
        self.copts = cfg.get("clips", {})
        self.notify = notify          # notify(text, file=None) -> message id or None
        self.recorders = {}           # streamer name -> StreamRecorder
        self.done_chunks = {}         # streamer name -> set of chunk paths already analysed
        self.waiting = {}             # streamer name -> [(chunk index, moment)] needing the next chunk
        self.state = load_state()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()

    # ---------------------------------------------------- step 1 + 2 ---
    def check_live(self):
        status = check_all(self.cfg)
        by_name = {s["name"]: s for s in self.cfg["streamers"]}
        for name, info in status.items():
            rec = self.recorders.get(name)
            if info and (rec is None or not rec.is_running()):
                if rec:  # recorder died while still live: finish its chunks, start again
                    self.process_chunks(name, rec)
                rec = StreamRecorder(by_name[name], self.copts.get("chunk_seconds", 600))
                rec.start()
                self.recorders[name] = rec
                self.done_chunks.setdefault(name, set())
                self.notify(f"🔴 **{name}** just went live: *{info.get('title', '')}*\n"
                            f"Started recording in {self.copts.get('chunk_seconds', 600) // 60}-minute chunks.")
            elif not info and rec:
                rec.stop()
                self.process_chunks(name, rec)
                del self.recorders[name]
                self.notify(f"⚫ **{name}** went offline. Recording stopped.")

    # ------------------------------------------------ step 3 + 4 + 5 ---
    def process_chunks(self, name, rec):
        chunks = rec.finished_chunks()
        paths = [c[0] for c in chunks]
        recording = rec.is_running()
        tail = self.copts.get("tail_seconds", 15)

        # moments near the end of a chunk wait until the next chunk exists
        still_waiting = []
        for i, m in self.waiting.pop(name, []):
            if i + 1 < len(paths) or not recording:
                self.emit_clip(name, rec, paths, i, m)
            else:
                still_waiting.append((i, m))
        if still_waiting:
            self.waiting[name] = still_waiting

        done = self.done_chunks.setdefault(name, set())
        for i, (path, start, end, wall_start) in enumerate(chunks):
            if str(path) in done:
                continue
            done.add(str(path))
            try:
                moments = find_highlights(path, rec.chat_file, wall_start, self.copts)
            except Exception:
                L.error("analysis failed for %s\n%s", path, traceback.format_exc())
                continue
            L.info("%s: %d highlight(s) in %s", name, len(moments), path.name)
            for m in moments:
                if m["t"] + tail > end - start and i == len(paths) - 1 and recording:
                    self.waiting.setdefault(name, []).append((i, m))
                else:
                    self.emit_clip(name, rec, paths, i, m)

    def emit_clip(self, name, rec, paths, i, m):
        clip_id = f"{rec.folder.name}_c{i:04d}_{m['t']:04d}"
        try:
            full, preview = make_clip(paths, i, m["t"], CLIPS / f"{clip_id}.mp4", self.copts)
        except Exception:
            L.error("clipping failed for %s\n%s", clip_id, traceback.format_exc())
            return
        why = ", ".join(f"{k} {v:+.1f}" for k, v in m["reasons"].items())
        msg_id = self.notify(
            f"🎬 **New clip from {name}** (score {m['score']}; {why})\n"
            f"React {APPROVE} to post it everywhere or {REJECT} to bin it.\n`{clip_id}`",
            file=preview)
        with self.lock:
            self.state["clips"][clip_id] = {
                "streamer": name, "path": str(full), "preview": str(preview),
                "score": m["score"], "status": "pending", "message_id": msg_id}
            save_state(self.state)

    def loop(self):
        interval = self.cfg.get("check_interval_seconds", 120)
        last_check = 0
        while not self.stop_event.is_set():
            try:
                if time.time() - last_check >= interval:
                    last_check = time.time()
                    self.check_live()
                for name, rec in list(self.recorders.items()):
                    self.process_chunks(name, rec)
            except Exception:
                L.error("loop error\n%s", traceback.format_exc())
            self.stop_event.wait(30)

    def shutdown(self):
        self.stop_event.set()
        for rec in self.recorders.values():
            rec.stop()

    # ------------------------------------------------------------ step 6 ---
    def handle_reaction(self, message_id, emoji):
        with self.lock:
            hit = [(cid, c) for cid, c in self.state["clips"].items()
                   if c.get("message_id") == message_id and c["status"] == "pending"]
            if not hit:
                return None
            clip_id, clip = hit[0]
            clip["status"] = "posting" if emoji == APPROVE else "rejected"
            save_state(self.state)
        if emoji == REJECT:
            return f"🗑️ `{clip_id}` discarded."
        caption = self.cfg["postforme"].get("caption_template", "{streamer}").format(streamer=clip["streamer"])
        try:
            post = post_clip(self.cfg["postforme"], clip["path"], caption)
            new_status, reply = "posted", f"🚀 `{clip_id}` sent to Post for Me (post id `{post.get('id')}`)."
        except Exception as e:
            new_status, reply = "pending", f"⚠️ Posting `{clip_id}` failed: {e}. Remove and re-add ✅ to retry."
        with self.lock:
            clip["status"] = new_status
            save_state(self.state)
        return reply


# --------------------------------------------------------------- modes ---
def run_console(cfg):
    def notify(text, file=None):
        print("\n" + text + (f"\n   preview: {file}" if file else ""), flush=True)
    p = Pipeline(cfg, notify)
    try:
        p.loop()
    except KeyboardInterrupt:
        p.shutdown()


def run_discord(cfg):
    import discord

    dcfg = cfg["discord"]
    if not dcfg.get("bot_token") or not dcfg.get("channel_id"):
        sys.exit("discord.bot_token and discord.channel_id must be set (or use --no-discord).")
    approvers = {int(u) for u in dcfg.get("approver_user_ids", [])}
    client = discord.Client(intents=discord.Intents.default())
    holder = {}

    def notify(text, file=None):
        async def send():
            ch = client.get_channel(int(dcfg["channel_id"])) or await client.fetch_channel(int(dcfg["channel_id"]))
            msg = await ch.send(text, file=discord.File(str(file)) if file else None)
            if file:
                await msg.add_reaction(APPROVE)
                await msg.add_reaction(REJECT)
            return msg.id
        return asyncio.run_coroutine_threadsafe(send(), client.loop).result(timeout=300)

    @client.event
    async def on_ready():
        L.info("Discord bot logged in as %s", client.user)
        if "pipeline" not in holder:
            holder["pipeline"] = Pipeline(cfg, notify)
            threading.Thread(target=holder["pipeline"].loop, daemon=True).start()

    @client.event
    async def on_raw_reaction_add(ev):
        if ev.user_id == client.user.id or ev.channel_id != int(dcfg["channel_id"]):
            return
        if approvers and ev.user_id not in approvers:
            return
        emoji = str(ev.emoji)
        if emoji not in (APPROVE, REJECT) or "pipeline" not in holder:
            return
        reply = await asyncio.to_thread(holder["pipeline"].handle_reaction, ev.message_id, emoji)
        if reply:
            await client.get_channel(ev.channel_id).send(reply)

    try:
        client.run(dcfg["bot_token"], log_handler=None)
    finally:
        if "pipeline" in holder:
            holder["pipeline"].shutdown()


if __name__ == "__main__":
    require_tools("ffmpeg", "ffprobe", "streamlink")
    config = load_config()
    run_console(config) if "--no-discord" in sys.argv else run_discord(config)
