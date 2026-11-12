"""Stage 8: Asynchronous background worker for demo delivery order progression."""
from __future__ import annotations

import asyncio
import logging
from typing import NoReturn

from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.models.demo_delivery import DemoDeliveryEvent
from app.services.demo_delivery_service import DemoDeliveryService

logger = logging.getLogger(__name__)


class DemoDeliveryWorker:
    """Background polling worker that advances demo delivery orders."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._running = False
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start the background worker task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="demo-delivery-worker")
        logger.info(
            "Demo delivery background worker started (poll_interval=1.0s, delay=%ss).",
            self.settings.demo_delivery_delay_seconds,
        )

    async def stop(self) -> None:
        """Stop the background worker gracefully."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Demo delivery background worker stopped.")

    async def _run_loop(self) -> None:
        """Main periodic polling loop."""
        while self._running:
            try:
                self.tick_once()
            except Exception as exc:
                logger.error("Error in demo delivery worker tick: %s", exc, exc_info=True)
            await asyncio.sleep(1.0)

    def tick_once(self) -> int:
        """Single synchronous tick: query active scheduled demo events and advance due ones."""
        db = SessionLocal()
        advanced_count = 0
        try:
            active_events = (
                db.query(DemoDeliveryEvent)
                .filter(DemoDeliveryEvent.status.in_(["SCHEDULED", "PROCESSING"]))
                .all()
            )
            if active_events:
                svc = DemoDeliveryService(db, settings=self.settings)
                for event in active_events:
                    did_advance = svc.advance_demo_delivery_if_due(event.order_id)
                    if did_advance:
                        advanced_count += 1
        finally:
            db.close()
        return advanced_count
