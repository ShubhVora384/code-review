"""Entry point for the dedicated worker container."""

from __future__ import annotations

import asyncio
import contextlib
import signal

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_db, init_db
from app.workers.queue import close, worker_loop


async def _main() -> None:
    configure_logging()
    log = get_logger("worker")
    await init_db()

    loop = asyncio.get_running_loop()
    task = asyncio.create_task(worker_loop(concurrency=1))

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, task.cancel)

    log.info("worker.ready", redis=settings.redis_url)
    with contextlib.suppress(asyncio.CancelledError):
        await task

    await close()
    await dispose_db()
    log.info("worker.stopped")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main())
