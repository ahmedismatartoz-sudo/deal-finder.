"""Interfaccia comune delle fonti. Ogni fonte (Subito, AutoScout24,
fornitore Facebook, ...) è un adattatore sostituibile."""
from __future__ import annotations

import json
import random
import re
import time
from abc import ABC, abstractmethod
from typing import Any, Iterator

from ..core.models import Listing

RE_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


class Collector(ABC):
    source: str = "base"
    min_delay_s: float = 4.0     # ritmo basso e rispettoso
    max_delay_s: float = 9.0

    @abstractmethod
    def search(self, query: dict) -> Iterator[Listing]:
        """Annunci da una ricerca (zona, prezzo max, pagine)."""

    @abstractmethod
    def fetch(self, url: str) -> Listing | None:
        """Dettaglio di un annuncio; None se non più disponibile."""

    def is_available(self, url: str) -> bool:
        return self.fetch(url) is not None

    def pause(self) -> None:
        time.sleep(random.uniform(self.min_delay_s, self.max_delay_s))


def next_data(html: str) -> dict | None:
    """Molti siti di annunci espongono i dati in un JSON dentro la pagina."""
    m = RE_NEXT_DATA.search(html)
    return json.loads(m.group(1)) if m else None


def find_key(obj: Any, *names: str) -> Any:
    """Cerca ricorsivamente la prima chiave tra `names` (robusto ai cambi di struttura)."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for n in names:
                if n in cur and cur[n] not in (None, "", [], {}):
                    return cur[n]
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None
