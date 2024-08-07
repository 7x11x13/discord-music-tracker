import itertools
import json
import logging
import os
import time
from collections import defaultdict, deque
from typing import Optional

import dill  # type: ignore[import-untyped]
from discord_webhook import DiscordEmbed, DiscordWebhook
from soundcloud import SoundCloud, Track

from discord_tag_watcher.config import WatcherConfig

logging.basicConfig()
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

MAX_DISCORD_CONTENT_LENGTH = 2000
MAX_DISCORD_EMBED_TITLE_LENGTH = 256
MAX_DISCORD_EMBED_DESC_LENGTH = 4096


# last <cache_size> seen tracks for each webhook
seen_tracks: dict[str, deque[int]] = defaultdict(deque)
cache_size: Optional[int] = None


def _update_cache_size(new_size: Optional[int]):
    global seen_tracks
    global cache_size
    if new_size == cache_size:
        return
    new_seen_tracks: dict[str, deque[int]] = defaultdict(lambda: deque(maxlen=new_size))
    for k, v in seen_tracks.items():
        new_seen_tracks[k].extend(v)
    seen_tracks = new_seen_tracks
    cache_size = new_size


def _load_seen_tracks():
    global seen_tracks
    seen_file = os.environ.get("CACHE_PATH", "data/cache.pkl")
    try:
        with open(seen_file, "rb") as f:
            seen_tracks = dill.load(f)
        logger.info(f"Loaded tag cache for {len(seen_tracks)} tags")
    except OSError:
        pass


def _save_seen_tracks():
    seen_file = os.environ.get("CACHE_PATH", "data/cache.pkl")
    with open(seen_file, "wb") as f:
        dill.dump(seen_tracks, f)


client: Optional[SoundCloud] = None


def _get_client() -> SoundCloud:
    global client
    if client is None or not client.is_client_id_valid():
        client = SoundCloud()
    return client


config_last_modified: float = -1
config_cache: Optional[WatcherConfig] = None


def _load_config() -> WatcherConfig:
    global config_cache
    global config_last_modified
    config_file = os.environ.get("CONFIG_FILE_PATH", "data/config.json")
    last_modified = os.path.getmtime(config_file)
    if config_cache is None or last_modified != config_last_modified:
        with open(config_file) as f:
            config: WatcherConfig = json.load(f)
            config_last_modified = last_modified
            config_cache = config
            logger.info(
                f"Loaded config: {json.dumps(config, indent=4, sort_keys=True)}"
            )
    return config_cache


def _send_track(track: Track, webhook_url: str):
    hook = DiscordWebhook(webhook_url, rate_limit_retry=True)
    hook.username = "SoundCloud"
    hook.avatar_url = "https://a-v2.sndcdn.com/assets/images/brand-1b72dd82.svg"
    hook.content = track.permalink_url.replace("https://soundcloud.com", "https://fxsoundcloud.pages.dev", 1)
    r = hook.execute()
    r.raise_for_status()


def _watch_tags(tags: list[str], webhook_url: str, max_tracks: int):
    client = _get_client()
    seen_ids = set()
    tracks: list[Track] = []
    for tag in tags:
        for track in itertools.islice(client.get_tag_tracks_recent(tag), max_tracks):
            if track.id in seen_tracks[webhook_url]:
                break
            # not seen before track, send to webhook
            if track.id not in seen_ids:
                tracks.append(track)
                seen_ids.add(track.id)

    # send oldest first
    tracks.sort(key=lambda track: track.last_modified.timestamp())

    for track in tracks:
        _send_track(track, webhook_url)
        seen_tracks[webhook_url].append(track.id)
    _save_seen_tracks()


def main():
    _load_seen_tracks()
    while True:
        try:
            config = _load_config()
            _update_cache_size(config["cache_size"])
            logger.debug(f"Config: {config}")
            for link in config["links"]:
                _watch_tags(link["tags"], link["webhook_url"], config["max_tag_tracks"])
        except Exception:
            logger.exception("Error while watching")
        time.sleep(config["watch_interval_s"])


if __name__ == "__main__":
    main()
