"""Celery application for background threat intelligence tasks."""

from __future__ import annotations

import os

from celery import Celery
from celery.schedules import crontab

redis_url: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

app = Celery("biodefense", broker=redis_url, backend=redis_url)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)

# Periodic tasks
app.conf.beat_schedule = {
    # Scan for new threats every 6 hours
    "scan-threats-every-6h": {
        "task": "app.tasks.scan_threats",
        "schedule": crontab(minute=0, hour="*/6"),
    },
}

# Auto-discover tasks in app.tasks
app.autodiscover_tasks(["app"])
