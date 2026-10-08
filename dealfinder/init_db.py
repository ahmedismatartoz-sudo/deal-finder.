"""Crea le tabelle: python -m dealfinder.init_db"""
from .db import connect, init_schema

if __name__ == "__main__":
    with connect() as conn:
        init_schema(conn)
        conn.commit()
    print("Database pronto.")
