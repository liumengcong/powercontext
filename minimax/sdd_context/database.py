from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import pymysql
from pymysql.cursors import DictCursor

DDL = """CREATE TABLE IF NOT EXISTS sdd_documents (
  source_id VARCHAR(128) PRIMARY KEY,
  kind VARCHAR(16) NOT NULL,
  path VARCHAR(512) NOT NULL,
  symbol VARCHAR(255) NOT NULL,
  start_line INT NOT NULL,
  end_line INT NOT NULL,
  revision VARCHAR(40) NOT NULL,
  file_sha256 VARCHAR(64) NOT NULL,
  content LONGTEXT NOT NULL,
  keywords TEXT NOT NULL
)"""


@contextmanager
def database(runtime: Path):
    """Use real seekdb locally, or an explicitly configured remote MySQL endpoint."""
    instance = None
    if os.getenv("SEEKDB_HOST"):
        options = {
            "host": os.environ["SEEKDB_HOST"],
            "port": int(os.getenv("SEEKDB_PORT", "2881")),
            "user": os.getenv("SEEKDB_USER", "root"),
            "password": os.getenv("SEEKDB_PASSWORD", ""),
        }
    else:
        import pylibseekdb

        runtime.mkdir(parents=True, exist_ok=True)
        instance = pylibseekdb.open(str((runtime / "seekdb").resolve()))
        options = instance.connection_options()
    conn = None
    try:
        conn = pymysql.connect(
            **options,
            database=os.getenv("SEEKDB_DATABASE", "test"),
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
        )
        with conn.cursor() as cursor:
            cursor.execute("SELECT VERSION() AS version")
            version = cursor.fetchone()["version"]
            if not any(name in version.lower() for name in ("oceanbase", "seekdb")):
                raise RuntimeError(f"Expected seekdb/OceanBase, got {version}")
            cursor.execute(DDL)
        yield conn, version
    finally:
        if conn is not None:
            conn.close()
        if instance is not None:
            instance.close()
