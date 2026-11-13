from backend.app.core.config import apply_cpu_limits, get_settings


apply_cpu_limits()

from celery import Celery


settings = get_settings()
celery_app = Celery(
    "drum_transcription", broker=str(settings.REDIS_URL), backend=str(settings.REDIS_URL),
    include=["backend.app.workers.tasks"],
)
celery_app.conf.update(
    worker_concurrency=1,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_publish_retry=False,
    broker_connection_timeout=2,
    redis_socket_connect_timeout=2,
    redis_socket_timeout=2,
    redis_retry_on_timeout=False,
    worker_max_tasks_per_child=5,
    task_soft_time_limit=1800,
    task_time_limit=2100,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    result_accept_content=["json"],
    result_expires=86400,
    broker_transport_options={"visibility_timeout": 3600, "socket_connect_timeout": 2,
                              "socket_timeout": 2, "max_retries": 0},
    result_backend_transport_options={"visibility_timeout": 3600,
                                      "retry_policy": {"max_retries": 0}},
    visibility_timeout=3600,
    broker_connection_retry_on_startup=True,
    timezone="UTC",
    enable_utc=True,
)


