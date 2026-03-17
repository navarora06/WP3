import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery("knowledgeops", broker=REDIS_URL, backend=REDIS_URL)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Europe/Helsinki",
    enable_utc=True,
)

# IMPORTANT: import task modules AFTER celery_app exists
import tasks.ingest  # noqa: E402,F401
import tasks.gap  # noqa: E402,F401
import tasks.knowledge  # noqa: E402,F401
