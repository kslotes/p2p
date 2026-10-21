"""One shared background poller: every browser reads its cache, never Binance."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from p2p.application import GetMarketSnapshot, SpotPricePort
from p2p.application.monitor import Config, MonitorState, refresh

logger = logging.getLogger(__name__)


class SnapshotPoller:
    """Refresh every asset each ``config.interval`` seconds and cache the result.

    Failure semantics match the terminal monitor: a failed asset keeps its last
    good snapshot and records the error. ``changed`` is a fresh event per
    refresh; grab it *before* reading ``state`` and await it to be woken by the
    next refresh (or by ``stop``). With ``spot`` each refresh also fetches the
    USDT spot prices of the non-stablecoin assets.
    """

    def __init__(
        self,
        use_case: GetMarketSnapshot,
        config: Config,
        spot: SpotPricePort | None = None,
    ) -> None:
        self._use_case = use_case
        self._spot = spot
        self.config = config
        self.state = MonitorState()
        self.version = 0
        self.changed = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None

    async def refresh(self) -> None:
        await refresh(self._use_case, self.config, self.state, spot=self._spot)
        self.version += 1
        self._notify()

    async def start(self) -> None:
        """Fetch once (so the first page load has data), then keep polling."""
        await self.refresh()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._notify()  # wake streams so they notice the poller stopped

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.interval)
            try:
                await self.refresh()
            except Exception:
                # refresh() already records per-asset errors; anything reaching
                # here is a bug, so log it and keep the loop (and `running`) alive.
                logger.exception("snapshot refresh failed; retrying next interval")

    def _notify(self) -> None:
        changed, self.changed = self.changed, asyncio.Event()
        changed.set()
