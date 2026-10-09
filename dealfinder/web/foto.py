"""Foto delle auto proposte, servite dal nostro sito.

I link alle foto di Facebook scadono dopo pochi giorni e quelli di Subito spariscono
con l'annuncio: per le auto proposte ai commercianti salviamo una copia ridotta
(lato lungo 960 px, JPEG) nella tabella photo_cache. Il sito chiede sempre
/api/foto/<annuncio>/<posizione>?s=<firma>: la firma evita che qualcuno scarichi
tutte le foto provando i numeri uno dopo l'altro.
"""
from __future__ import annotations

import hashlib
import hmac
import io
import logging

from .auth import _secret

log = logging.getLogger("foto")
MAX_SIDE = 960
MAX_PER_AUTO = 6
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")


def sign(listing_id: int, pos: int) -> str:
    return hmac.new(_secret(), f"foto:{listing_id}:{pos}".encode(), hashlib.sha256).hexdigest()[:16]


def url_for(listing_id: int, pos: int) -> str:
    return f"/api/foto/{listing_id}/{pos}?s={sign(listing_id, pos)}"


def check(listing_id: int, pos: int, s: str | None) -> bool:
    return bool(s) and hmac.compare_digest(s, sign(listing_id, pos))


def shrink(raw: bytes) -> bytes | None:
    """Riduce e ricomprime la foto. None se non è un'immagine valida."""
    try:
        from PIL import Image, ImageOps
        im = Image.open(io.BytesIO(raw))
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.thumbnail((MAX_SIDE, MAX_SIDE))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=78, optimize=True, progressive=True)
        return out.getvalue()
    except Exception:
        return None


def download(url: str, http=None) -> bytes | None:
    if not url or not url.startswith("http"):
        return None
    try:
        import httpx
        own = http is None
        http = http or httpx.Client(timeout=12, follow_redirects=True, headers={"User-Agent": UA})
        try:
            r = http.get(url)
        finally:
            if own:
                http.close()
        if r.status_code != 200 or len(r.content) > 15_000_000:
            return None
        return shrink(r.content)
    except Exception as e:
        log.info("foto non scaricata: %s", str(e)[:120])
        return None


def cached(conn, listing_id: int, pos: int):
    """(trovata, contenuto): trovata=False se non abbiamo mai provato a salvarla."""
    row = conn.execute("SELECT content FROM photo_cache WHERE listing_id=%s AND position=%s",
                       (listing_id, pos)).fetchone()
    if not row:
        return False, None
    c = row["content"]
    if isinstance(c, str) and c.startswith("\\x"):       # bytea letto come testo esadecimale
        c = bytes.fromhex(c[2:])
    return True, (bytes(c) if c is not None else None)


def save(conn, listing_id: int, pos: int, content: bytes | None) -> None:
    conn.execute("INSERT INTO photo_cache (listing_id, position, content) VALUES (%s,%s,%s) "
                 "ON CONFLICT (listing_id, position) DO UPDATE SET content=EXCLUDED.content, saved_at=now()",
                 (listing_id, pos, content))
    conn.commit()


def fetch_one(conn, listing_id: int, pos: int) -> bytes | None:
    found, content = cached(conn, listing_id, pos)
    if found:
        return content
    row = conn.execute("SELECT source_url FROM listing_photos WHERE listing_id=%s AND position=%s",
                       (listing_id, pos)).fetchone()
    content = download(row["source_url"]) if row else None
    if row:
        save(conn, listing_id, pos, content)
    return content


def warm(conn, limit_listings: int = 300, http=None) -> dict:
    """Salva le foto delle auto proposte che non abbiamo ancora (chiamato dal ciclo)."""
    rows = conn.execute(
        """SELECT p.listing_id, p.position, p.source_url FROM listing_photos p
           JOIN listings l ON l.id = p.listing_id
           WHERE l.stage='approfondito' AND l.status='attivo' AND p.position < %s
             AND NOT EXISTS (SELECT 1 FROM photo_cache c WHERE c.listing_id=p.listing_id AND c.position=p.position)
             AND p.listing_id IN (SELECT id FROM listings WHERE stage='approfondito' AND status='attivo'
                                  ORDER BY (source='facebook') DESC, deep_at DESC NULLS LAST LIMIT %s)
           ORDER BY p.listing_id, p.position""", (MAX_PER_AUTO, limit_listings)).fetchall()
    ok = fail = 0
    own = http is None
    if own:
        try:
            import httpx
            http = httpx.Client(timeout=12, follow_redirects=True, headers={"User-Agent": UA})
        except Exception:
            http = None
    try:
        for r in rows:
            content = download(r["source_url"], http) if http is not None else None
            save(conn, r["listing_id"], r["position"], content)
            ok += content is not None
            fail += content is None
    finally:
        if own and http is not None:
            http.close()
    # le foto delle auto non più proposte da oltre 30 giorni non servono: spazio sul disco
    conn.execute("DELETE FROM photo_cache c USING listings l WHERE l.id=c.listing_id "
                 "AND (l.status <> 'attivo' OR l.stage <> 'approfondito') AND c.saved_at < now() - interval '30 days'")
    conn.commit()
    return {"salvate": ok, "non_scaricabili": fail}
