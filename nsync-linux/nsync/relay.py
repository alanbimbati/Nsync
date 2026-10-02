"""Minimal multi-relay Nostr client: one reconnecting task per relay."""

import asyncio
import json
import logging
from collections.abc import Callable

import websockets

from .nostr import Event

log = logging.getLogger(__name__)

RECONNECT_DELAY = 5.0


class Relay:
    def __init__(self, url: str, filters: list[dict], on_event: Callable[[Event], None],
                 latest: dict[int, Event] | None = None):
        self.url = url
        self._filters = filters
        self._on_event = on_event
        self._ws = None
        # Replaceable events are only worth sending once, but a relay we were
        # offline to never saw them: keep the newest of each kind and resend on every connect.
        self._latest: dict[int, Event] = dict(latest or {})
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def set_filters(self, filters: list[dict]) -> None:
        self._filters = filters
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps(["REQ", "nsync", *filters]))  # same id: replaces the subscription
            except websockets.ConnectionClosed:
                pass

    async def publish(self, event: Event) -> None:
        self._latest[event.kind] = event
        if self._ws is not None:
            try:
                await self._ws.send(json.dumps(["EVENT", event.to_dict()]))
            except websockets.ConnectionClosed:
                pass  # _run reconnects and resends _latest

    async def _run(self) -> None:
        while True:
            try:
                async with websockets.connect(self.url, ping_interval=30, ping_timeout=15) as ws:
                    self._ws = ws
                    log.info("connected to %s", self.url)
                    await ws.send(json.dumps(["REQ", "nsync", *self._filters]))
                    for event in self._latest.values():
                        await ws.send(json.dumps(["EVENT", event.to_dict()]))
                    async for raw in ws:
                        self._handle(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("relay %s: %s, retrying in %ss", self.url, exc, RECONNECT_DELAY)
            finally:
                self._ws = None
            await asyncio.sleep(RECONNECT_DELAY)

    def _handle(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw)
            if msg[0] == "EVENT" and len(msg) >= 3:
                self._on_event(Event.from_dict(msg[2]))
            elif msg[0] == "OK" and len(msg) >= 4 and not msg[2]:
                log.warning("relay %s rejected event: %s", self.url, msg[3])
        except (ValueError, KeyError, IndexError, TypeError):
            log.debug("ignoring malformed message from %s", self.url)


class RelayPool:
    def __init__(self, urls: list[str], filters: list[dict], on_event: Callable[[Event], None]):
        self._filters = filters
        self._on_event = on_event
        self._latest: dict[int, Event] = {}
        self._relays: dict[str, Relay] = {}
        self._urls = list(urls)
        for u in urls:
            self._relays[u] = Relay(u, filters, on_event)

    @property
    def relays(self) -> list[Relay]:
        return list(self._relays.values())

    def start(self) -> None:
        for r in self._relays.values():
            r.start()

    async def stop(self) -> None:
        await asyncio.gather(*(r.stop() for r in self._relays.values()))

    async def publish(self, event: Event) -> None:
        self._latest[event.kind] = event  # relays that join later get it too
        await asyncio.gather(*(r.publish(event) for r in self._relays.values()))

    async def set_filters(self, filters: list[dict]) -> None:
        self._filters = filters
        await asyncio.gather(*(r.set_filters(filters) for r in self._relays.values()))

    async def set_urls(self, urls: list[str]) -> None:
        """Connect to the relays that are new and drop the ones that are gone."""
        for url in urls:
            if url not in self._relays:
                relay = Relay(url, self._filters, self._on_event, self._latest)
                self._relays[url] = relay
                relay.start()
        gone = [u for u in self._relays if u not in urls]
        for url in gone:
            await self._relays.pop(url).stop()
