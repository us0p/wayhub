from mentor import worker


async def test_opened_works_with_the_production_worker_pool_size() -> None:
    """Prod runs one task at a time (D88), so the worker's pool is `concurrency + 2` = 3:
    smaller than psycopg_pool's default `min_size` of 4, which used to crash it at startup."""
    async with worker.opened(max_size=1 + 2) as app:
        assert await app.connector.execute_query_one_async("SELECT 1 AS one") == {"one": 1}
