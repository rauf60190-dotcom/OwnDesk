from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
import pytest

from django.conf import settings
from django.db.backends.sqlite3.base import DatabaseWrapper


@pytest.mark.django_db(transaction=True)
def test_local_atomic_writers_wait_before_reading(tmp_path):
    """Reproduce login's read/update racing the local worker on a real file."""
    from django.db import connections

    config = deepcopy(connections["default"].settings_dict)
    config.update(ENGINE="django.db.backends.sqlite3", NAME=str(tmp_path / "concurrent.sqlite3"))
    if settings.DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3":
        pytest.skip("Local SQLite configuration regression")
    config["OPTIONS"] = deepcopy(settings.DATABASES["default"]["OPTIONS"])
    assert config["OPTIONS"]["transaction_mode"] == "IMMEDIATE"
    ready, contender = Event(), Event()

    db = DatabaseWrapper(config, alias="local_setup")
    with db.cursor() as cursor:
        cursor.execute("CREATE TABLE counter (value INTEGER NOT NULL)")
        cursor.execute("INSERT INTO counter VALUES (0)")
    db.close()

    def write(first):
        db = DatabaseWrapper(deepcopy(config), alias="local_writer")
        try:
            db.ensure_connection()
            if not first:
                assert ready.wait(5)
                db.connection.set_trace_callback(
                    lambda sql: contender.set() if sql.startswith("BEGIN") else None
                )
            db.set_autocommit(False, force_begin_transaction_with_broken_autocommit=True)
            with db.cursor() as cursor:
                cursor.execute("SELECT value FROM counter")
                value = cursor.fetchone()[0]
                if first:
                    ready.set()
                    assert contender.wait(5)
                cursor.execute("UPDATE counter SET value = %s", [value + 1])
            db.commit()
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(write, True), pool.submit(write, False)
        first.result(timeout=10)
        second.result(timeout=10)
    db = DatabaseWrapper(config, alias="local_check")
    try:
        with db.cursor() as cursor:
            cursor.execute("SELECT value FROM counter")
            assert cursor.fetchone()[0] == 2
    finally:
        db.close()
