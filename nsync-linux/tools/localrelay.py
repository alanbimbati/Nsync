"""A tiny in-memory Nostr relay, to try Nsync on one machine: nothing leaves it.

    python tools/localrelay.py 7777      # then use "ws://127.0.0.1:7777" as a relay
It keeps the newest event per (author, kind, d) and does not check signatures.
"""
import asyncio
import json
import sys

import websockets

events: dict[tuple, dict] = {}
subs: dict = {}


def matches(f: dict, e: dict) -> bool:
    d = next((t[1] for t in e["tags"] if t[0] == "d"), None)
    return ((not f.get("kinds") or e["kind"] in f["kinds"]) and (not f.get("authors") or e["pubkey"] in f["authors"])
            and (not f.get("#d") or d in f["#d"]))


async def handler(ws):
    subs[ws] = {}
    try:
        async for raw in ws:
            m = json.loads(raw)
            if m[0] == "EVENT":
                e = m[1]
                events[(e["pubkey"], e["kind"], next((t[1] for t in e["tags"] if t[0] == "d"), ""))] = e
                await ws.send(json.dumps(["OK", e["id"], True, ""]))
                for w, s in list(subs.items()):
                    for sid, fs in s.items():
                        if any(matches(f, e) for f in fs):
                            await w.send(json.dumps(["EVENT", sid, e]))
            elif m[0] == "REQ":
                subs[ws][m[1]] = m[2:]
                for e in list(events.values()):
                    if any(matches(f, e) for f in m[2:]):
                        await ws.send(json.dumps(["EVENT", m[1], e]))
                await ws.send(json.dumps(["EOSE", m[1]]))
            elif m[0] == "CLOSE":
                subs[ws].pop(m[1], None)
    finally:
        subs.pop(ws, None)


async def main(port: int):
    async with websockets.serve(handler, "127.0.0.1", port):
        await asyncio.Future()

asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 7777))
