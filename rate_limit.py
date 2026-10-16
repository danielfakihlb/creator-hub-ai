"""A tiny, dependency-free rate limiter -- brute-force protection for
login/signup.

Tracks how many requests each client IP has made to a given route within
a rolling time window, entirely in memory. No external package is used
for this: the limits needed here are simple, and this server currently
runs as a single process, so a plain in-process counter is enough and
is something we can actually verify works, rather than trusting an
unverified dependency.

Honest limitations, stated plainly (consistent with how the rest of this
project reports its own real limits):
  - This resets whenever the server restarts, same as the rest of this
    app's current state (see "no persistent storage" in project status).
  - If this server is ever run with more than one worker process or
    instance handling traffic at once, each process tracks its own
    separate counts instead of sharing one -- fine for the current
    single-worker setup, but would need a shared store (e.g. Redis)
    to stay accurate if that changes.
  - This limits by IP address. Someone behind a shared IP (an office,
    a VPN) shares that IP's attempt budget with everyone else on it --
    an accepted trade-off for a simple first layer of protection.
"""
import time
import threading
from functools import wraps

from flask import request

_lock = threading.Lock()
_attempts = {}  # (route_key, client_ip) -> [timestamp, timestamp, ...]


def _client_ip():
    # Behind Render's proxy, request.remote_addr alone would show the
    # proxy's own address -- the real client IP is in X-Forwarded-For.
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def rate_limit(max_attempts, window_seconds, message=None):
    """Decorator: after `max_attempts` requests from the same client IP
    within `window_seconds`, further requests to this route get a plain
    429 response instead of running the view -- real throttling, not a
    cosmetic warning."""
    def decorator(view):
        route_key = f"{view.__module__}.{view.__name__}"

        @wraps(view)
        def wrapped(*args, **kwargs):
            ip = _client_ip()
            key = (route_key, ip)
            now = time.time()
            with _lock:
                timestamps = _attempts.setdefault(key, [])
                cutoff = now - window_seconds
                timestamps[:] = [t for t in timestamps if t > cutoff]
                if len(timestamps) >= max_attempts:
                    wait = int(window_seconds - (now - timestamps[0])) + 1
                    body = message or "Too many attempts from this connection. Please wait and try again."
                    response = (f"429 -- {body}", 429)
                    return response
                timestamps.append(now)
            return view(*args, **kwargs)
        return wrapped
    return decorator
