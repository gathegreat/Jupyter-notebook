"""STEP 6 - Post an approved clip to all your socials through Post for Me
(https://www.postforme.dev, $10/month for up to 1,000 posts, unlimited accounts).

Flow (from their API reference):
  1. POST /v1/media/create-upload-url  -> {upload_url, media_url}
  2. PUT the video file to upload_url
  3. POST /v1/social-posts {caption, social_accounts: [ids], media: [{url: media_url}]}

Find your social account IDs (after connecting TikTok/Instagram/YouTube in their dashboard):
    python postforme.py accounts
"""
import sys

import requests

from common import load_config, log

L = log("postforme")
API = "https://api.postforme.dev/v1"


def _headers(api_key):
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def list_accounts(api_key):
    r = requests.get(f"{API}/social-accounts", headers=_headers(api_key), timeout=30)
    r.raise_for_status()
    return r.json()["data"]


def upload_video(api_key, path):
    r = requests.post(f"{API}/media/create-upload-url", headers=_headers(api_key), timeout=30)
    r.raise_for_status()
    j = r.json()
    with open(path, "rb") as f:
        up = requests.put(j["upload_url"], data=f, headers={"Content-Type": "video/mp4"}, timeout=600)
    up.raise_for_status()
    return j["media_url"]


def post_clip(pcfg, path, caption):
    if not pcfg.get("api_key") or not pcfg.get("social_account_ids"):
        raise RuntimeError("postforme.api_key and postforme.social_account_ids must be set in config.json")
    media_url = upload_video(pcfg["api_key"], path)
    r = requests.post(f"{API}/social-posts", headers=_headers(pcfg["api_key"]), json={
        "caption": caption,
        "social_accounts": pcfg["social_account_ids"],
        "media": [{"url": media_url}],
    }, timeout=60)
    r.raise_for_status()
    post = r.json()
    L.info("posted %s -> post id %s (status %s)", path, post.get("id"), post.get("status"))
    return post


if __name__ == "__main__":
    cfg = load_config()["postforme"]
    if sys.argv[1:] == ["accounts"]:
        for a in list_accounts(cfg["api_key"]):
            print(f"{a['id']}  {a['platform']:<12} {a.get('username') or ''}  ({a.get('status')})")
    elif len(sys.argv) == 3:
        print(post_clip(cfg, sys.argv[1], sys.argv[2]))
    else:
        sys.exit("usage: python postforme.py accounts | python postforme.py <clip.mp4> \"caption\"")
