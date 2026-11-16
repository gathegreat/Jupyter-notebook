# Auto-Clipping System (Twitch / Kick / YouTube → Discord → all socials)

This builds the system from the two videos:

```
 every 2 min                when someone goes live          every finished 10-min chunk
┌──────────────┐  live?   ┌────────────────────────┐      ┌─────────────────────────────┐
│ live_checker │ ───────▶ │ recorder (Streamlink)  │ ───▶ │ highlight_finder            │
│ Twitch/Kick/ │          │ 10-min chunks + chat   │      │ loud reactions, chat spikes,│
│ YouTube APIs │          └────────────────────────┘      │ laugh/hype emotes           │
└──────────────┘                                          └──────────────┬──────────────┘
        │ "🔴 X is live"                                                 │ best moments
        ▼                                                                 ▼
┌──────────────────────────────────────────┐  ✅ approve   ┌──────────────────────────────┐
│ Discord: live alerts + clip previews     │ ◀──────────── │ clipper: 40s build-up + 15s  │
│ you react ✅ or ❌                        │               │ after, vertical 9:16 1080p   │
└──────────────────┬───────────────────────┘               └──────────────────────────────┘
                   │ ✅
                   ▼
        ┌──────────────────────────┐
        │ Post for Me API ($10/mo) │ ──▶ TikTok, Instagram, YouTube Shorts, ...
        └──────────────────────────┘
```

| File | Step from the video |
|---|---|
| `live_checker.py` | 1. Check the platform APIs every couple of minutes to see who's live |
| `recorder.py` | 2. Streamlink records the stream in 10-minute chunks (and logs Twitch chat) |
| `highlight_finder.py` | 3. "Sentiment analysis": more laughter/noise, chat speeding up, emotes |
| `clipper.py` | 4. Cut the clip starting 35–45s before the moment, make it vertical |
| `run.py` | 5. Send it to Discord, wait for your approval |
| `postforme.py` | 6. On approval, post to every connected social account |

---

## 1. Install (one time)

You need **Python 3.10+** and **ffmpeg**.

- **Windows:** install Python from python.org (tick "Add to PATH"), then in PowerShell: `winget install Gyan.FFmpeg`
- **Mac:** `brew install python ffmpeg`
- **Linux / cloud server:** `sudo apt install python3-pip ffmpeg`

Then, in this folder:

```bash
pip install -r requirements.txt
```

This also installs Streamlink. Check it works: `streamlink --version`, `ffmpeg -version`.

## 2. Make your config

```bash
cp config.example.json config.json      # Windows: copy config.example.json config.json
```

Open `config.json` and put the streamers you want in `streamers` (`name` = their username on that
platform). `config.json` is git-ignored because it will hold your keys, so never share it.

## 3. Platform API keys (optional but recommended)

Without keys the system still works. It asks Streamlink instead, which is slower with many streamers.

- **Twitch:** go to <https://dev.twitch.tv/console>, then *Register Your Application*. Use any name, set the
  OAuth redirect to `http://localhost`, and pick category *Other*. Copy the **Client ID**, generate a
  **Client Secret**, and put both under `"twitch"`.
- **Kick:** on kick.com go to *Settings → Developer → Create App* and copy the client ID and secret into `"kick"`.
- **YouTube:** in <https://console.cloud.google.com> create a project, enable *YouTube Data API v3*,
  create an **API key** and put it in `"youtube"`. Each streamer also needs `channel_id` (the `UC...` id).
  ⚠️ The free quota allows about 100 live checks a day, so with keys on, keep YouTube streamers few.

## 4. Discord bot (where you approve clips)

1. Go to <https://discord.com/developers/applications>, click **New Application**, then **Bot**, then **Reset Token**, and copy
   the token into `discord.bot_token`.
2. Under **OAuth2 → URL Generator**, tick scope `bot` and the permissions *View Channels, Send Messages,
   Attach Files, Add Reactions, Read Message History*. Open the URL it gives you and add the bot to your server.
3. In Discord, turn on *Settings → Advanced → Developer Mode*. Right-click your clips channel, choose
   **Copy Channel ID**, and put it in `discord.channel_id`. Right-click yourself, choose **Copy User ID**, and put it in
   `approver_user_ids` so only you can approve.

## 5. Post for Me (auto-posting)

1. Sign up at <https://www.postforme.dev> ($10/month, up to 1,000 posts, unlimited accounts).
2. Connect your TikTok / Instagram / YouTube accounts in their dashboard and create an **API key**.
3. Put the key in `postforme.api_key`, then list your account IDs:
   ```bash
   python postforme.py accounts
   ```
   Copy the IDs you want to post to into `postforme.social_account_ids`.
4. Edit `caption_template` (`{streamer}` is replaced with the streamer's name).

## 6. Test each piece, then run it

```bash
python live_checker.py                            # who's live right now?
python recorder.py https://www.twitch.tv/NAME NAME  # record a bit, Ctrl+C to stop
python highlight_finder.py some_video.mp4         # which seconds would it clip?
python clipper.py some_video.mp4 60 test.mp4      # make a vertical clip around 1:00
python run.py --no-discord                        # whole system, prints to the terminal, never posts
python run.py                                     # the real thing
```

Leave `python run.py` running. The computer must stay on and must not go to sleep. A cheap cloud server
(about $5–10/month) is the easiest way to run it around the clock.

---

## Tuning (`clips` in config.json)

| Setting | Default | What it does |
|---|---|---|
| `chunk_seconds` | 600 | Length of each recording chunk (10 min, like the video) |
| `lead_seconds` | 40 | How much build-up before the moment (video says 35–45s) |
| `tail_seconds` | 15 | How much after the moment |
| `min_score` | 2.5 | Higher means fewer, only bigger moments. Lower means more clips |
| `max_clips_per_chunk` | 3 | Maximum clips from each 10-minute chunk |
| `min_gap_seconds` | 90 | Two clips must be at least this far apart |
| `chat_delay_seconds` | 5 | Chat reacts a few seconds late, so its signals are shifted back |
| `weights` | audio 0.6, chat 1.0, emotes 0.8 | How much each signal counts |
| `vertical` | true | 9:16 with blurred background, or false to keep the original shape |

Each Discord message shows *why* a clip was picked (for example `audio +3.1, chat_rate +5.2, emotes +4.0`). If
you're getting junk, raise `min_score`. If you're missing moments, lower it.

## Good to know

- **Chat signals are Twitch-only for now.** Twitch chat is read anonymously. Kick and YouTube streams
  are clipped from audio alone, so expect noisier picks there.
- **"Laughter" is detected as sudden loud reactions** (laughing, screaming, hype) compared with that
  streamer's normal volume. It's fast and needs no GPU, but it can't tell a laugh from a shout.
- **Disk space:** a 1080p60 stream is about 3 GB per hour. Recordings go to `data/recordings/`, so delete
  old folders regularly.
- **Discord upload limit:** previews are shrunk to under 8 MB. The full-quality file is what gets posted.
- **Permission:** only clip streamers who allow it (many run official clipping programs and pay
  clippers). Posting other people's content without permission can get your accounts struck.

## Files the system creates

```
data/recordings/<streamer>_<date>/chunk_0000.ts ...   10-min recordings
data/recordings/<streamer>_<date>/chat.jsonl          chat log (Twitch)
data/clips/<id>.mp4, <id>_preview.mp4                 finished clips
data/state.json                                       pending / posted / rejected clips
```
