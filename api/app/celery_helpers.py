import asyncio
from .tasks import celery_app


async def poll_celery_task(name: str, args: list, timeout: float = 30.0) -> list[dict]:
    task = celery_app.send_task(name, args=args)
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        ready = await loop.run_in_executor(None, lambda: task.ready())
        if ready:
            return task.result or []
        await asyncio.sleep(0.2)
    return []

import uuid


async def fire_acquire_task(pool, user_id: str, provider: str | None,
                            provider_id: str | None, query: str | None) -> dict:
    task_id = str(uuid.uuid4())
    query_str = query or f'{provider}:{provider_id}'
    await pool.execute(
        'INSERT INTO download_queue (id, user_id, query, provider, provider_id) '
        'VALUES ($1,$2,$3,$4,$5)',
        task_id, user_id, query_str, provider, provider_id,
    )
    celery_app.send_task(
        'app.tasks.download_provider_track',
        args=[provider, provider_id, task_id],
        kwargs={'query': query},
        task_id=task_id,
    )
    return {'task_id': task_id, 'status': 'queued'}
