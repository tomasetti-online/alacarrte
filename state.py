"""ALACarrte mutable runtime state.

Single owner of every piece of process-wide mutable state: the in-memory task
registry, the lock guarding it, the concurrency semaphore, the per-IP rate map,
and the VPN/MusicBrainz flags. Modules mutate state through these names rather
than keeping their own copies, so a change to state ownership lands in one place.
"""
import threading

import config

tasks = {}
LOCK = threading.Lock()
GLOBAL_SLOTS = threading.Semaphore(config.CONCURRENCY)
rate_map = {}

# VPN reconnection flag: while True, run_ytdl routes through the proxy.
_RATE_LIMITED = False

# Last MusicBrainz query timestamp (throttle to ~1 query/sec).
_MB_LAST = 0