"""Impostazioni. Tutto ciò che è segreto arriva da variabili d'ambiente."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", "postgresql://localhost/dealfinder"))
    anthropic_api_key: str | None = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY"))
    scraper_proxy: str | None = field(default_factory=lambda: os.environ.get("SCRAPER_PROXY"))
    plate_salt: str = field(default_factory=lambda: os.environ.get("PLATE_SALT", "cambiami"))

    # Ricerca opportunità
    region: str = "lombardia"
    opportunity_provinces: tuple[str, ...] = field(default_factory=lambda: tuple(
        os.environ.get("OPPORTUNITY_PROVINCES", "milano,monza-e-della-brianza,bergamo,brescia").split(",")))
    max_purchase_eur: int = 20000
    max_pages_per_province: int = field(default_factory=lambda: int(os.environ.get("MAX_PAGES", "25")))

    # Base di mercato (più ampia della zona opportunità)
    market_provinces: tuple[str, ...] = ("milano", "monza-e-della-brianza", "bergamo", "brescia",
                                         "como", "varese", "lecco", "lodi", "pavia", "cremona",
                                         "mantova", "sondrio")
    market_max_price_eur: int = 40000

    # Regole prodotto
    max_dealer_opens: int = 7


settings = Settings()
