"""Waking a worker for a chat that has something new (TV6).

The webhook stores the event first and wakes the worker after the commit. If the broker is down
the failure is logged and swallowed: the event is stored, and the beat sweep
(`worker/whatsapp_tasks.py`) hands any chat with unprocessed events to a worker a minute later.
"""

import logging
import uuid

logger = logging.getLogger(__name__)


def queue_thread(thread_id: uuid.UUID) -> None:
    """Queue `whatsapp.process_thread` for one chat."""
    from callobuzz_rag.worker.whatsapp_tasks import process_thread_task

    try:
        process_thread_task.apply_async(
            kwargs={"thread_id": str(thread_id)}, queue="periodic", retry=False
        )
    except Exception:
        logger.error("Could not queue WhatsApp chat %s; the sweep will", thread_id, exc_info=True)
