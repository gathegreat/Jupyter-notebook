"""STEP 1 - Who is live right now?

Asks each platform's API whether the streamers in config.json are live.
If you have not set up API keys for a platform, it falls back to asking
Streamlink (slower, but needs no keys).

Try it on its own:
    python live_checker.py
"""
import json
import subprocess
import time

import requests

from common import load_config, log

L = log("live")
_token_cache = {}  # platform -> (token, expires_at)


# ---------------------------------------------------------------- Twitch ---
def _twitch_token(cfg):
    tok, exp = _token_cache.get("twitch", (None, 0))
    if tok and time.time() < exp - 60:
        return tok
    r = requests.post("https://id.twitch.tv/oauth2/token", data={
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "grant_type": "client_credentials",
    }, timeout=15)
    r.raise_for_status()
    j = r.json()
    _token_cache["twitch"] = (j["access_token"], time.time() + j["expires_in"])
    return j["access_token"]


def check_twitch(streamers, cfg):
    """One request covers up to 100 streamers."""
    logins = [s["name"].lower() for s in streamers]
    headers = {"Client-Id": cfg["client_id"], "Authorization": f"Bearer {_twitch_token(cfg)}"}
    live = {}
    for i in range(0, len(logins), 100):
        r = requests.get("https://api.twitch.tv/helix/streams",
                         params=[("user_login", l) for l in logins[i:i + 100]],
                         headers=headers, timeout=15)
        r.raise_for_status()
        for st in r.json()["data"]:
            live[st["user_login"].lower()] = {"title": st["title"], "game": st["game_name"]}
    return {s["name"]: live.get(s["name"].lower()) for s in streamers}


# ------------------------------------------------------------------ Kick ---
def _kick_token(cfg):
    tok, exp = _token_cache.get("kick", (None, 0))
    if tok and time.time() < exp - 60:
        return tok
    r = requests.post("https://id.kick.com/oauth/token", data={
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "grant_type": "client_credentials",
    }, timeout=15)
    r.raise_for_status()
    j = r.json()
    _token_cache["kick"] = (j["access_token"], time.time() + j.get("expires_in", 3600))
    return j["access_token"]


def check_kick(streamers, cfg):
    headers = {"Authorization": f"Bearer {_kick_token(cfg)}"}
    r = requests.get("https://api.kick.com/public/v1/channels",
                     params=[("slug", s["name"].lower()) for s in streamers],
                     headers=headers, timeout=15)
    r.raise_for_status()
    live = {}
    for ch in r.json().get("data", []):
        stream = ch.get("stream") or {}
        if stream.get("is_live"):
            live[ch["slug"].lower()] = {"title": ch.get("stream_title", ""),
                                        "game": (ch.get("category") or {}).get("name", "")}
    return {s["name"]: live.get(s["name"].lower()) for s in streamers}


# --------------------------------------------------------------- YouTube ---
def check_youtube(streamers, cfg):
    """search.list costs 100 of your 10,000 free daily quota units per streamer per check.
    10 YouTube streamers checked every 2 minutes would need 72,000/day, so keep the list
    short, check YouTube less often, or leave api_key empty to use the Streamlink fallback."""
    out = {}
    for s in streamers:
        r = requests.get("https://www.googleapis.com/youtube/v3/search", params={
            "part": "snippet", "channelId": s["channel_id"], "eventType": "live",
            "type": "video", "key": cfg["api_key"],
        }, timeout=15)
        r.raise_for_status()
        items = r.json().get("items", [])
        out[s["name"]] = ({"title": items[0]["snippet"]["title"], "game": "",
                           "url": f"https://www.youtube.com/watch?v={items[0]['id']['videoId']}"}
                          if items else None)
    return out


# ------------------------------------------------- no-keys fallback ---
def check_with_streamlink(streamers):
    out = {}
    for s in streamers:
        try:
            p = subprocess.run(["streamlink", "--json", s["url"]],
                               capture_output=True, text=True, timeout=60)
            j = json.loads(p.stdout or "{}")
            out[s["name"]] = ({"title": (j.get("metadata") or {}).get("title") or "",
                               "game": (j.get("metadata") or {}).get("category") or ""}
                              if j.get("streams") else None)
        except Exception as e:  # network hiccup: treat as offline this round
            L.warning("streamlink check failed for %s: %s", s["name"], e)
            out[s["name"]] = None
    return out


CHECKERS = {"twitch": check_twitch, "kick": check_kick, "youtube": check_youtube}
KEYS_NEEDED = {"twitch": ("client_id", "client_secret"),
               "kick": ("client_id", "client_secret"),
               "youtube": ("api_key",)}


def check_all(cfg):
    """Returns {streamer_name: None | {"title":..., "game":...}}"""
    result = {}
    by_platform = {}
    for s in cfg["streamers"]:
        by_platform.setdefault(s["platform"], []).append(s)
    for platform, group in by_platform.items():
        pcfg = cfg.get(platform, {})
        has_keys = all(pcfg.get(k) for k in KEYS_NEEDED.get(platform, ("x",)))
        if platform == "youtube":
            has_keys = has_keys and all(s.get("channel_id") for s in group)
        try:
            if has_keys:
                result.update(CHECKERS[platform](group, pcfg))
            else:
                result.update(check_with_streamlink(group))
        except Exception as e:
            L.warning("%s API check failed (%s); falling back to streamlink", platform, e)
            result.update(check_with_streamlink(group))
    return result


if __name__ == "__main__":
    cfg = load_config()
    for name, info in check_all(cfg).items():
        print(f"{'LIVE   ' if info else 'offline'}  {name}  {info['title'] if info else ''}")
