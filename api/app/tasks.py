"""Celery app instance used by the API to dispatch tasks to the worker."""
from celery import Celery
from .config import settings

celery_app = Celery("kwhale", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
