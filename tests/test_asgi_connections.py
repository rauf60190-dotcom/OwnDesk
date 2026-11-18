import asyncio

import httpx
import pytest
from django.db import connection


@pytest.mark.postgres
@pytest.mark.django_db(transaction=True)
def test_completed_asgi_requests_release_postgres_connections(settings):
    """Completed HTTP requests must not leave request-thread connections behind."""
    from config.asgi import application

    def count():
        with connection.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()')
            return cursor.fetchone()[0]

    baseline=count()

    async def requests():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),base_url='http://testserver') as client:
            for _ in range(25):
                responses=await asyncio.gather(*(client.get('/api/v1/health/ready') for _ in range(4)))
                assert all(response.status_code==200 for response in responses)

    asyncio.run(requests())
    assert count()<=baseline+1
