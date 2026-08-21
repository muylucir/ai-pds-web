# backend/aipds/turn_handles.py -- handles that keep long turn input out of the URL.
#
# **Why it exists.** The chat text was going into the SSE URL's query string
# (`GET /events?text=...`), because EventSource supports only GET and cannot carry a
# request body. But one Hangul character becomes 9 bytes under encodeURIComponent
# (`%EC%97%AC` x 3). Measured: a 2,164-character input produced a 14,376-byte request
# line. Adding the auth cookies (3 Cognito JWTs, about 3.7KB) pushed it past Node.js's
# default maxHeaderSize of 16,384 bytes, and the Next.js proxy rejected it with an
# **HTTP 431**.
#
# Why that failure looked like "the connection was lost" on screen: EventSource does not
# expose the HTTP status code. A 431, a 500 and a dropped network all fire nothing but
# onerror. So the cause was completely hidden from the screen.
#
# **The shape of the fix.** Take the text in a POST body, exchange it for a short handle,
# and put only that handle in the SSE URL. This repo already has a precedent of the same
# shape -- a prototype's first turn sends only the `?text=__first__` sentinel and the
# server fills in the real prompt (_FIRST_TURN_SENTINEL in routes/prototypes.py).
#
# Why EventSource was not replaced with fetch + ReadableStream: this proxy layer has
# already been through, and solved, SSE breaking under HTTP/2 (the ERR_HTTP2_PROTOCOL_ERROR
# record in the app/api/[...path]/route.ts header). Keep the EventSource whose reconnection
# and cookie authentication are built into the browser, and remove only the cause of the
# problem, the URL length.
#
# **Why it is in-memory.** This value is consumed by a GET within seconds of the POST.
# Putting it in S3 would add a round trip to every turn start, delaying the first response,
# and would mean using permanent storage for a value that lives seconds. It is lost when
# the backend restarts, but then only that turn fails and the user can send again --
# app.proto_sessions is in-memory with the same character.
from __future__ import annotations

import logging
import secrets
import time
from typing import Callable

_log = logging.getLogger(__name__)

#: The handle's lifetime. Covering one browser round trip between the POST and the GET is
#: enough, and a longer one widens the window in which a handle left in a URL is still
#: alive. 60 seconds is generous even on a slow connection while being short enough that a
#: person cannot copy the URL and reuse it.
HANDLE_TTL_SECONDS = 60


class TurnHandleStore:
    """Turn input (payload) -> a short handle. Single-use and expiring.

    A handle is **not authentication.** The route verifies project access before creating
    one and checks the project again on consumption. The value is still unguessable
    because a URL is left in browser history, referrers and proxy logs -- a sequence
    number would be a route to reading someone else's turn text.
    """

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        # (project_id, payload, created_at) indexed by handle.
        self._items: dict[str, tuple[str, dict, float]] = {}
        # The clock is injected so tests can exercise expiry deterministically.
        self._clock = clock or time.monotonic

    def create(self, project_id: str, payload: dict) -> str:
        """Create a handle, sweeping expired ones along the way.

        Why there is no separate timer: this store lives only as long as the process, and
        this method is necessarily called at the start of every turn -- the moment to
        sweep already exists.
        """
        self._purge()
        handle = secrets.token_hex(16)
        self._items[handle] = (project_id, payload, self._clock())
        return handle

    def consume(self, project_id: str, handle: str) -> dict | None:
        """Consume a handle and return its payload. None when it is missing, expired, or from
        a different project.

        Why it is single-use is in the docstring above -- a handle left in a URL must not
        be able to run the same turn again. A mismatched project does not consume it: if a
        stray lookup burned the rightful owner's handle, tracing the cause would be hard
        in the case where it is a bug rather than an attack.
        """
        item = self._items.get(handle)
        if item is None:
            return None
        owner, payload, created = item
        if owner != project_id:
            _log.warning("turn handle used with the wrong project: %s", project_id)
            return None
        if self._clock() - created > HANDLE_TTL_SECONDS:
            self._items.pop(handle, None)
            return None
        self._items.pop(handle, None)
        return payload

    def size(self) -> int:
        """The number of live handles. Used by tests to confirm the sweep."""
        return len(self._items)

    def _purge(self) -> None:
        cutoff = self._clock() - HANDLE_TTL_SECONDS
        stale = [h for h, (_, _, created) in self._items.items() if created < cutoff]
        for h in stale:
            self._items.pop(h, None)
