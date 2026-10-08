"""Strutture dati comuni a tutte le fonti."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DamageItem:
    part: str                 # es. "paraurti_anteriore"
    action: str = "sostituire"  # sostituire | riparare
    severity: str = "leggero"   # leggero | medio | grave
    side: str | None = None
    source: str = "testo"     # testo | foto
    confidence: float = 0.5


@dataclass
class Listing:
    source: str
    source_id: str
    url: str
    title: str | None = None
    description: str | None = None
    make: str | None = None
    model: str | None = None
    version_raw: str | None = None
    year: int | None = None
    mileage_km: int | None = None
    fuel: str | None = None
    gearbox: str | None = None
    power_kw: int | None = None
    price_raw: str | None = None
    price_eur: int | None = None
    price_flags: list[str] = field(default_factory=list)
    seller_type: str = "sconosciuto"
    city: str | None = None
    province: str | None = None
    region: str | None = None
    photos: list[str] = field(default_factory=list)
    damage_declared: bool | None = None
    damage_class: str = "sconosciuto"
    damage_items: list[DamageItem] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    disappeared_at: datetime | None = None
    raw: dict = field(default_factory=dict)

    REQUIRED = ("make", "model", "year", "mileage_km", "fuel", "price_eur")

    def compute_missing(self) -> list[str]:
        """Segnala i campi mancanti senza inventarli."""
        self.missing_fields = [f for f in self.REQUIRED if getattr(self, f) in (None, "")]
        for f in ("gearbox", "power_kw", "version_raw"):
            if getattr(self, f) in (None, ""):
                self.missing_fields.append(f)
        if not self.photos:
            self.missing_fields.append("photos")
        return self.missing_fields
