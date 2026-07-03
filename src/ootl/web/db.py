"""Postgres access for the serverless deployment.

One fresh connection per invocation, opened against Supabase's transaction-mode
pooler (Supavisor) -- the recommended pattern for serverless. Prepared
statements are disabled because transaction-mode pooling doesn't support them.
"""
from __future__ import annotations

import psycopg
from psycopg.rows import dict_row


async def connect(database_url: str) -> psycopg.AsyncConnection:
    """Open a connection suitable for Supabase's transaction pooler."""
    return await psycopg.AsyncConnection.connect(
        database_url,
        row_factory=dict_row,
        prepare_threshold=None,  # required with transaction-mode pooling
        autocommit=True,         # explicit transactions via conn.transaction()
        connect_timeout=10,
    )
