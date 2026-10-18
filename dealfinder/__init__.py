import os as _os

# Su alcuni servizi Render la variabile del database ha un altro nome (Render non accetta DATABASE_URL se è già
# collegata da un Blueprint): si accettano anche DATABASE_URLS e SCOVO_DB_URL.
if not _os.environ.get("DATABASE_URL"):
    for _k in ("DATABASE_URLS", "SCOVO_DB_URL", "DB_URL"):
        if _os.environ.get(_k):
            _os.environ["DATABASE_URL"] = _os.environ[_k]
            break
