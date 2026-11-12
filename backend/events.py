"""Tiny pub/sub used for live dashboard notifications (Server-Sent Events)."""
import asyncio
import threading


class EventBus:
    def __init__(self):
        self.subs = set()
        self.lock = threading.Lock()
        self.loop = None

    def subscribe(self):
        q = asyncio.Queue(maxsize=100)
        with self.lock:
            self.subs.add(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            self.subs.discard(q)

    @staticmethod
    def _put(q, ev):
        try:
            q.put_nowait(ev)
        except asyncio.QueueFull:
            pass

    def publish(self, ev):
        """Safe to call from any thread."""
        if self.loop is None:
            return
        with self.lock:
            targets = list(self.subs)
        for q in targets:
            self.loop.call_soon_threadsafe(self._put, q, ev)


bus = EventBus()
