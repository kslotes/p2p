"""Background order-history sync for the web app.

The sync itself is a blocking callable (it runs its own event loop for the
Binance client and writes to SQLite), so each run happens in a daemon thread:
the market monitor's event loop never waits on it, and Ctrl+C never waits for
a long backfill to finish. At most one sync runs at a time.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from p2p.domain.orders import OrderHistoryError

logger = logging.getLogger(__name__)

DEFAULT_SYNC_MINUTES = 5.0
MAX_ERROR_LENGTH = 200


def _utc_now() -> datetime:
    return datetime.now(UTC)


def safe_error(exc: BaseException) -> str:
    """A short message that is safe to show and log.

    ``OrderHistoryError`` messages are written for humans; only their first
    line is kept because payload validation details quote the offending input
    (e.g. a counterparty nickname). Any other exception may carry a signed URL
    or personal data, so only its class name is reported.
    """
    if isinstance(exc, OrderHistoryError):
        lines = str(exc).strip().splitlines()
        message = lines[0].strip() if lines else ""
        if message:
            return message[:MAX_ERROR_LENGTH]
    return type(exc).__name__


async def _in_daemon_thread(function: Callable[[], Any]) -> Any:
    """Run ``function`` in a daemon thread and await its result."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future[Any] = loop.create_future()

    def resolve(result: Any, error: BaseException | None) -> None:
        if future.done():  # cancelled while the thread was working
            return
        if error is not None:
            future.set_exception(error)
        else:
            future.set_result(result)

    def target() -> None:
        result: Any = None
        error: BaseException | None = None
        try:
            result = function()
        except BaseException as exc:  # noqa: BLE001 - handed to the awaiting task
            error = exc
        with contextlib.suppress(RuntimeError):  # the loop closed meanwhile
            loop.call_soon_threadsafe(resolve, result, error)

    threading.Thread(target=target, name="order-sync", daemon=True).start()
    return await future


class OrderSyncRunner:
    """Run ``sync`` at start and then every ``interval`` seconds.

    ``sync=None`` means not configured (no credentials): nothing ever runs.
    ``interval=0`` disables the automatic runs; ``trigger`` still works.
    Failures are recorded as a short safe message and never propagate.
    """

    def __init__(
        self,
        sync: Callable[[], Any] | None,
        *,
        interval: float = DEFAULT_SYNC_MINUTES * 60,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._sync = sync
        self.interval = interval
        self._now = now
        self.last_attempt: datetime | None = None
        self.last_success: datetime | None = None
        self.last_error: str | None = None
        self._current: asyncio.Task[None] | None = None
        self._loop_task: asyncio.Task[None] | None = None

    @property
    def configured(self) -> bool:
        return self._sync is not None

    @property
    def running(self) -> bool:
        return self._current is not None

    def trigger(self) -> bool:
        """Start a sync now unless unconfigured or one is already running."""
        if self._sync is None or self._current is not None:
            return False
        self._current = asyncio.create_task(self._run(self._sync))
        return True

    async def wait(self) -> None:
        """Wait for the running sync, if any, to finish."""
        if self._current is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(self._current)

    async def start(self) -> None:
        if self.configured and self.interval > 0:
            self._loop_task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        # A sync already inside its thread keeps going until the process exits
        # (daemon thread); only the waiting tasks are cancelled here.
        for task in (self._loop_task, self._current):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._loop_task = self._current = None

    async def _loop(self) -> None:
        while True:
            self.trigger()  # skipped while a manual sync is still running
            await asyncio.sleep(self.interval)

    async def _run(self, sync: Callable[[], Any]) -> None:
        self.last_attempt = self._now()
        try:
            await _in_daemon_thread(sync)
        except Exception as exc:  # noqa: BLE001 - a failed sync must never escape
            self.last_error = safe_error(exc)
            logger.warning("order history sync failed: %s", self.last_error)
        else:
            self.last_success = self._now()
            self.last_error = None
        finally:
            self._current = None
