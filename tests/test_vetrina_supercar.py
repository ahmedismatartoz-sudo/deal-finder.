"""23/11: vetrina a parte "Supercar Lombardia" (web/vetrina_supercar.py, GET /api/vetrina/supercar).

Sportive e supercar in vendita in Lombardia al prezzo dell'annuncio: niente margine, niente stima, mai un numero di
telefono. Fuori da sito_regole: le regole degli affari non cambiano (impronta invariata).
"""
import json
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from dealfinder.web import sito_regole as SR
from dealfinder.web import vetrina_supercar as VS

SI = [
    # marche sempre dentro, a qualunque prezzo
    ("ferrari", "488", "GTB", 215000), ("lamborghini", "huracan", "", 190000), ("mclaren", "720s", "", 210000),
    ("porsche", "cayenne", "3.0 Diesel", 9500), ("aston-martin", "vantage", "", 80000), ("Aston Martin", "DB11", "", 120000),
    ("bentley", "continental", "GT", 60000), ("rolls-royce", "ghost", "", 150000), ("maserati", "ghibli", "Diesel", 19000),
    ("bugatti", "chiron", "", 2500000), ("lotus", "elise", "", 38000), ("pagani", "huayra", "", 3000000),
    ("koenigsegg", "agera", "", 2000000), ("maybach", "57", "", 90000), ("alpina", "b3", "", 45000),
    ("alpine", "a110", "", 55000), ("mercedes-maybach", "s-580", "", 180000),
    # modelli sportivi delle altre marche
    ("mercedes-benz", "a-45-amg", "S 4Matic+", 42000), ("mercedes", "classe-c", "C 63 AMG S", 60000),
    ("mercedes-benz", "amg-gt", "4.0 V8", 95000), ("mercedes-benz", "classe-a", "A 35 AMG Line Premium Plus AMG", 41000),
    ("mercedes-benz", "g-63", "AMG", 160000), ("mercedes-benz", "sls", "", 220000), ("mercedes-amg", "gt", "", 99000),
    ("mercedes-benz", "classe-s", "Maybach S 560", 110000),
    ("bmw", "m3", "Competition", 70000), ("bmw", "serie-4", "M4 Coupé Competition M xDrive", 85000),
    ("bmw", "m2", "", 55000), ("bmw", "m5", "", 90000), ("bmw", "m6", "", 50000), ("bmw", "m8", "", 110000),
    ("bmw", "x5-m", "Competition", 100000), ("bmw", "x5", "X5M", 95000), ("bmw", "x3-m", "", 70000),
    ("bmw", "x4", "X4 M Competition", 75000), ("bmw", "x6-m", "", 105000), ("bmw", "xm", "", 150000),
    ("bmw", "z4", "M40i", 52000), ("bmw", "serie-5", "Alpina B5", 65000),
    ("audi", "r8", "5.2 V10", 130000), ("audi", "rs3", "Sportback", 52000), ("audi", "a4", "RS4 Avant", 68000),
    ("audi", "rs-5", "", 60000), ("audi", "rs6", "Avant", 115000), ("audi", "rs-7", "", 120000),
    ("audi", "rs-q3", "", 55000), ("audi", "q8", "RS Q8", 135000), ("audi", "tt", "TT RS Coupé", 58000),
    ("audi", "tt-rs", "", 58000),
    ("nissan", "gt-r", "", 110000), ("nissan", "gtr", "Nismo", 180000), ("chevrolet", "corvette", "C8", 105000),
    ("corvette", "c7", "", 70000), ("ford", "mustang", "5.0 V8 GT", 45000), ("lexus", "lc", "500", 80000),
    ("lexus", "lc-500h", "", 78000), ("lexus", "lfa", "", 700000), ("jaguar", "f-type", "3.0 V6", 48000),
    ("alfa-romeo", "4c", "", 62000), ("alfa-romeo", "giulia", "2.9 V6 Quadrifoglio", 70000),
    ("alfa-romeo", "stelvio", "Quadrifoglio", 75000), ("abarth", "124", "Spider", 27000),
    ("abarth", "695", "Biposto", 42000), ("honda", "nsx", "", 140000), ("honda", "civic", "2.0 Type R", 39000),
    ("honda", "civic", "Type-R GT", 41000), ("toyota", "supra", "GR 3.0", 58000), ("toyota", "gr-supra", "", 55000),
    ("seat", "leon", "Cupra R 2.0 TSI", 33000), ("cupra", "leon", "Cupra R ST", 36000),
    ("volkswagen", "golf", "2.0 TSI R DSG 4Motion", 38000), ("volkswagen", "golf", "R32", 26000),
    ("vw", "golf-r", "", 40000),
]

# 23/11 pomeriggio (cliente: "TUTTE le sportive", nessun prezzo minimo): modelli e versioni in più
SI += [
    ("audi", "s3", "Sportback 2.0 TFSI quattro", 14000), ("audi", "a3", "S3 SPB", 21000), ("audi", "s1", "", 17000),
    ("audi", "s4", "Avant", 30000), ("audi", "s5", "", 32000), ("audi", "s6", "", 40000), ("audi", "s7", "", 45000),
    ("audi", "s8", "", 60000), ("audi", "sq5", "", 42000), ("audi", "sq7", "", 70000), ("audi", "sq8", "", 95000),
    ("audi", "tt", "1.8 TFSI S line", 9000), ("audi", "tts", "", 28000), ("audi", "tt", "TTS Roadster", 26000),
    ("audi", "rs3", "", 12000),
    ("bmw", "serie-1", "M135i xDrive", 30000), ("bmw", "m140i", "", 33000), ("bmw", "serie-2", "M235i", 28000),
    ("bmw", "serie-2", "M240i Coupé", 45000), ("bmw", "serie-3", "M340i xDrive", 55000), ("bmw", "serie-3", "M340d", 52000),
    ("bmw", "serie-4", "M440i", 60000), ("bmw", "serie-5", "M550i", 65000), ("bmw", "z4", "sDrive20i", 9000),
    ("bmw", "z4", "2.0i Roadster M Sport", 14000), ("bmw", "m3", "E46", 24999),
    ("mercedes-benz", "slk", "200 Kompressor", 8000), ("mercedes-benz", "slk-200", "", 9000),
    ("mercedes-benz", "slc", "180", 25000), ("mercedes-benz", "sl", "500", 30000), ("mercedes-benz", "sl-350", "", 28000),
    ("mercedes-benz", "classe-c", "C 63 AMG", 22000), ("mercedes-benz", "classe-e", "E 53 AMG 4Matic+", 70000),
    ("mercedes-benz", "classe-e", "E 63 AMG S", 85000), ("mercedes-benz", "classe-c", "C 43 AMG", 38000),
    ("volkswagen", "golf", "GTI Performance 2.0 TSI", 23000), ("volkswagen", "golf", "2.0 TDI GTD", 19000),
    ("volkswagen", "golf", "1.4 GTE Plug-In", 21000), ("volkswagen", "golf-gti", "", 18000),
    ("volkswagen", "polo", "2.0 TSI GTI DSG", 20000), ("volkswagen", "scirocco", "1.4 TSI", 9000),
    ("volkswagen", "up", "1.0 TSI GTI", 14000), ("volkswagen", "up!", "GTI", 14000),
    ("mini", "cooper-s", "", 12000), ("mini", "mini", "2.0 Cooper S Hype", 18000), ("mini", "countryman", "John Cooper Works ALL4", 30000),
    ("mini", "mini", "JCW", 27000),
    ("abarth", "595", "1.4 Turbo T-Jet 145 CV", 11000), ("abarth", "500", "", 9000), ("abarth", "124", "Spider", 21000),
    ("abarth", "punto", "Evo", 7000), ("fiat", "124-spider", "1.4 MultiAir Lusso", 19000), ("fiat", "124", "Spider", 18000),
    ("fiat", "500", "1.4 16V T-Jet Abarth", 8000),
    ("alfa-romeo", "8c", "Competizione", 300000), ("alfa-romeo", "brera", "2.2 JTS", 7000), ("alfa-romeo", "spider", "2.4 JTDm", 9000),
    ("alfa-romeo", "gtv", "2.0 TS", 8000), ("alfa-romeo", "giulia", "2.2 Turbodiesel 210 CV AT8 AWD Q4 Veloce", 32000),
    ("alfa-romeo", "stelvio", "2.0 Turbo 280 CV Veloce", 38000),
    ("hyundai", "i20", "1.6 T-GDI N", 24000), ("hyundai", "i20-n", "", 25000), ("hyundai", "i30", "2.0 T-GDI N Performance", 29000),
    ("hyundai", "kona", "N 2.0 T-GDI DCT", 33000), ("hyundai", "veloster", "N", 28000), ("hyundai", "ioniq-5", "N", 65000),
    ("hyundai", "ioniq-5-n", "", 66000),
    ("ford", "fiesta", "1.5 EcoBoost ST", 17000), ("ford", "focus", "2.3 EcoBoost ST", 26000), ("ford", "focus", "RS 2.3", 34000),
    ("ford", "puma", "1.5 EcoBoost ST", 27000), ("ford", "mustang", "GT", 18000), ("ford", "gt", "", 600000),
    ("ford", "fiesta-st", "", 15000),
    ("renault", "clio", "RS 1.6 TCe 200 CV EDC", 15000), ("renault", "clio", "R.S. Trophy", 19000),
    ("renault", "megane", "R.S. 280", 27000), ("renault", "Mégane", "RS Trophy 300", 33000), ("renault", "megane", "Trophy-R", 50000),
    ("toyota", "gr86", "", 38000), ("toyota", "gt86", "2.0", 22000), ("toyota", "gt-86", "", 21000), ("toyota", "gr-yaris", "", 38000),
    ("toyota", "yaris", "GR 1.6 Circuit", 40000), ("toyota", "celica", "1.8 VVT-i", 4000), ("toyota", "supra", "", 45000),
    ("mazda", "mx-5", "1.5 Skyactiv-G", 16000), ("mazda", "mx5", "", 9000), ("mazda", "rx-8", "", 7000),
    ("subaru", "brz", "", 27000), ("subaru", "impreza", "WRX STI", 30000), ("subaru", "wrx", "", 28000), ("subaru", "wrx-sti", "", 35000),
    ("peugeot", "208", "1.6 THP GTi", 12000), ("peugeot", "308", "GTi 270", 20000), ("peugeot", "rcz", "1.6 THP", 9000),
    ("cupra", "leon", "2.0 TSI VZ 300 CV", 34000), ("cupra", "formentor", "VZ5", 55000), ("cupra", "ateca", "VZ", 30000),
    ("cupra", "born", "VZ", 40000), ("seat", "leon", "2.0 TSI Cupra 290", 22000), ("seat", "leon-cupra", "", 20000),
    ("skoda", "octavia", "2.0 TSI RS", 23000), ("Škoda", "octavia", "RS 245", 26000), ("skoda", "octavia", "vRS", 21000),
    ("honda", "s2000", "", 35000), ("honda", "civic", "Type R", 24000),
    ("nissan", "350z", "", 14000), ("nissan", "370z", "Nismo", 30000), ("nissan", "350-z", "Roadster", 13000), ("nissan", "z", "", 60000),
    ("tesla", "model-s", "Plaid", 85000), ("lexus", "rc", "RC F", 55000), ("lexus", "rc-f", "", 52000), ("lexus", "is", "IS F", 30000),
    ("jaguar", "f-type", "2.0", 24000), ("dodge", "challenger", "SRT Hellcat", 80000), ("dodge", "charger", "SRT8", 35000),
    ("dodge", "viper", "", 90000), ("chevrolet", "camaro", "", 40000), ("chevrolet", "corvette", "C5", 24000),
    ("morgan", "plus-4", "", 60000), ("caterham", "seven", "", 40000), ("ktm", "x-bow", "", 80000), ("ariel", "atom", "", 70000),
    ("donkervoort", "d8", "", 150000), ("smart", "roadster", "", 6000), ("smart", "roadster-coupe", "Brabus", 9000),
    ("lotus", "elise", "", 30000), ("alpine", "a110", "", 55000),
]

# 23/11 (revisione): altre sportive chieste dal cliente ("tutte")
SI += [
    ("alfa-romeo", "giulia", "GTA", 180000), ("alfa-romeo", "giulia", "GTAm", 200000), ("alfa-romeo", "giulietta", "Quadrifoglio Verde", 16000),
    ("alfa-romeo", "mito", "1.4 T 170 CV Quadrifoglio Verde", 9000), ("alfa-romeo", "giulietta", "1750 TBi QV", 15000),
    ("bmw", "serie-8", "M850i xDrive", 80000), ("bmw", "serie-7", "M760Li", 90000), ("bmw", "i8", "", 75000), ("bmw", "z8", "", 200000),
    ("bmw", "x3", "M40i", 60000), ("bmw", "x3", "xDrive M40d", 55000), ("bmw", "x4", "M40i", 58000), ("bmw", "x2", "M35i", 40000),
    ("bmw", "i4", "M50", 60000), ("bmw", "ix", "M60", 110000), ("bmw", "z3", "M Roadster", 45000), ("bmw", "z3-m", "", 50000),
    ("bmw", "serie-1", "1M Coupé", 70000), ("bmw", "1m", "", 70000),
    ("audi", "e-tron-gt", "", 80000), ("audi", "e-tron", "GT quattro", 85000), ("audi", "sq2", "", 38000),
    ("mercedes-benz", "classe-g", "G 500", 110000), ("mercedes-benz", "g-500", "", 95000), ("mercedes-benz", "classe-g", "G 63 AMG", 180000),
    ("cupra", "ateca", "2.0 TSI 4Drive DSG", 33000), ("cupra", "born", "VZ", 42000), ("cupra", "formentor", "2.0 TSI 310 CV 4Drive DSG", 40000),
    ("cupra", "leon", "2.0 TSI 300 CV DSG", 35000), ("cupra", "formentor", "2.0 TSI 228 kW", 41000),
    ("volkswagen", "t-roc", "R 2.0 TSI 4Motion", 38000), ("volkswagen", "tiguan", "R", 50000), ("volkswagen", "arteon", "R Shooting Brake", 52000),
    ("seat", "ibiza", "1.8 TSI Cupra", 12000), ("seat", "ateca", "Cupra 2.0 TSI", 30000), ("skoda", "fabia", "1.4 TSI RS", 9000),
    ("skoda", "kodiaq", "RS 2.0 TDI", 38000), ("renault", "megane", "GT 1.6 TCe 205 CV", 17000), ("ford", "fiesta", "ST200", 16000),
    ("toyota", "gr-corolla", "", 55000), ("toyota", "corolla", "GR Circuit", 56000), ("toyota", "yaris", "GRMN", 30000),
    ("tesla", "model-x", "Plaid", 95000), ("jaguar", "xk", "4.2 V8", 25000), ("jaguar", "xkr", "", 35000), ("jaguar", "f-pace", "SVR 5.0 V8", 75000),
    ("land-rover", "range-rover-sport", "5.0 V8 SVR", 80000), ("jeep", "grand-cherokee", "6.4 V8 SRT8", 45000),
    ("jeep", "grand-cherokee", "Trackhawk", 110000), ("lancia", "delta", "HF Integrale Evoluzione", 90000), ("opel", "corsa", "OPC", 9000),
    ("opel", "astra", "2.0 Turbo OPC", 16000), ("kia", "stinger", "2.2 CRDi", 28000), ("kia", "ev6", "GT AWD", 55000),
    ("mitsubishi", "lancer", "Evolution X", 35000), ("mitsubishi", "lancer-evolution", "", 40000), ("polestar", "1", "", 120000),
    ("polestar", "2", "Long Range Dual Motor Performance", 45000),
]

NO = [
    # falsi positivi trovati in revisione
    ("audi", "a3", "SPB 1.6 TDI S&S 5 porte", 14000), ("audi", "a4", "2.0 TDI S 5 porte", 15000), ("audi", "a1", "1.0 TFSI S 3 porte", 12000),
    ("audi", "a3", "1.6 TDI Start&Stop S 3p", 11000), ("audi", "a4", "Avant 2.0 TDI S4 look", 18000), ("audi", "q2", "30 TDI", 25000),
    ("mercedes-benz", "classe-a", "A 180 AMG 35.000 km", 28000), ("mercedes-benz", "classe-c", "C 220 AMG 63.000km", 30000),
    ("mercedes-benz", "classe-g", "G 350 d", 90000), ("mercedes-benz", "glc", "300 de", 50000),
    ("cupra", "formentor", "1.5 TSI 150 CV", 30000), ("cupra", "leon", "1.4 e-Hybrid 204 CV", 30000), ("cupra", "born", "58 kWh", 33000),
    ("volkswagen", "tiguan", "2.0 TDI R-Line", 38000), ("volkswagen", "t-roc", "1.5 TSI R-Line", 30000), ("volkswagen", "arteon", "2.0 TDI Elegance", 35000),
    ("bmw", "x5", "xDrive40i", 70000), ("bmw", "x2", "sDrive18d Msport", 30000), ("bmw", "z3", "1.9 Roadster", 12000), ("bmw", "i4", "eDrive40", 50000),
    ("bmw", "serie-1", "116d 5p. Msport", 20000), ("alfa-romeo", "giulietta", "1.6 JTDm Sprint", 14000), ("alfa-romeo", "mito", "1.4 78 CV", 6000),
    ("seat", "ibiza", "1.0 TSI FR", 14000), ("skoda", "fabia", "1.0 TSI Monte Carlo", 15000), ("skoda", "kodiaq", "2.0 TDI Sportline", 35000),
    ("renault", "megane", "1.5 dCi GT Line", 14000), ("ford", "fiesta", "1.0 EcoBoost Titanium", 12000), ("toyota", "corolla", "1.8 Hybrid Style", 22000),
    ("tesla", "model-x", "Long Range", 70000), ("jaguar", "xe", "2.0 D", 22000), ("jaguar", "f-pace", "2.0 D R-Sport", 35000),
    ("land-rover", "range-rover-sport", "3.0 SDV6 HSE", 50000), ("jeep", "grand-cherokee", "3.0 V6 CRD Overland", 35000), ("jeep", "renegade", "", 18000),
    ("lancia", "delta", "1.6 MJT", 6000), ("lancia", "ypsilon", "", 9000), ("opel", "corsa", "1.2 OPC Line", 14000), ("opel", "astra", "1.6 CDTI", 10000),
    ("kia", "ev6", "GT Line", 45000), ("kia", "sportage", "GT Line", 30000), ("mitsubishi", "lancer", "1.5", 6000), ("polestar", "2", "Standard Range", 38000),
    # allestimenti solo estetici: non fanno una sportiva
    ("mercedes-benz", "classe-a", "A 180 d AMG Line Premium", 32000), ("mercedes-benz", "glc", "220 d 4Matic AMG Line", 55000),
    ("mercedes-benz", "classe-c", "C 220 d Pacchetto AMG", 40000), ("mercedes-benz", "classe-a", "A 250 AMG", 35000),
    ("mercedes-benz", "gle", "350 de 4Matic AMG Line Advanced Plus", 80000), ("mercedes-benz", "cls", "350 d", 30000),
    ("mercedes-benz", "gls", "400 d", 80000), ("mercedes-benz", "classe-a", "A 180 Sport", 20000),
    ("bmw", "serie-3", "320d M Sport", 38000), ("bmw", "serie-3", "320d Msport", 38000), ("bmw", "x5", "xDrive30d M Sport", 70000),
    ("bmw", "x5", "X5 M Sport", 70000), ("bmw", "x5", "M50d", 65000), 
    ("bmw", "serie-1", "118d M Sport", 30000), ("bmw", "x5", "X5 M 50d", 60000), ("bmw", "serie-1", "118i Sport Line", 22000),
    ("audi", "a3", "35 TDI S tronic S line edition", 33000), ("audi", "a6", "40 TDI S line", 45000),
    ("audi", "q3", "35 TFSI S line", 36000), ("audi", "a3", "Sportback 1.6 TDI Sport", 15000), ("audi", "a5", "Sportback 40 TDI", 35000),
    ("audi", "a4", "Avant 2.0 TDI S-Line", 20000), ("audi", "q5", "2.0 TDI quattro S tronic", 30000),
    ("volkswagen", "golf", "2.0 TDI R-Line", 30000), ("volkswagen", "golf", "1.5 TSI R Line", 29000),
    ("volkswagen", "polo", "1.0 TSI R-Line", 18000), ("volkswagen", "golf", "1.6 TDI Sport", 15000),
    ("ford", "mustang-mach-e", "", 45000), ("ford", "mustang", "Mach-E AWD", 50000), ("ford", "fiesta", "1.0 EcoBoost ST-Line", 15000),
    ("ford", "focus", "1.5 EcoBlue ST-Line X", 22000), ("ford", "puma", "1.0 EcoBoost Hybrid ST-Line", 23000), ("ford", "kuga", "ST-Line", 30000),
    ("alfa-romeo", "giulia", "2.2 Turbodiesel Super", 25000),
    ("alfa-romeo", "giulietta", "1.6 JTDm Veloce", 15000), ("alfa-romeo", "stelvio", "2.2 Sprint", 35000),
    ("honda", "civic", "1.5 Sport", 28000), ("toyota", "yaris", "1.5 Hybrid GR Sport", 22000), ("toyota", "c-hr", "GR Sport", 30000),
    ("toyota", "corolla", "GR Sport", 28000), ("seat", "ibiza", "FR", 15000), ("seat", "leon", "1.5 TSI FR", 20000),
    ("cupra", "formentor", "1.5 TSI", 30000), ("nissan", "qashqai", "", 30000), ("jaguar", "f-pace", "", 45000),
    ("lexus", "nx", "", 45000), ("lexus", "is", "300h F Sport", 30000), ("lexus", "rc", "300h F Sport", 35000),
    ("fiat", "500", "Sport", 12000), ("fiat", "500", "1.2 Lounge", 8000), ("fiat", "tipo", "Sport", 15000),
    ("hyundai", "i30", "1.0 T-GDI N Line", 20000), ("hyundai", "i20", "N-Line", 18000), ("hyundai", "kona", "N Line", 25000),
    ("hyundai", "tucson", "N Line", 35000), ("kia", "sportage", "GT Line", 30000), ("kia", "ceed", "GT Line", 22000),
    ("peugeot", "208", "GT Line", 16000), ("peugeot", "308", "GT Pack", 25000), ("peugeot", "3008", "GT Line", 28000),
    ("renault", "clio", "R.S. Line", 18000), ("renault", "megane", "RS Line", 22000), ("renault", "clio", "1.5 dCi Sport", 9000),
    ("mini", "cooper", "1.5", 15000), ("mini", "cooper-d", "", 14000), ("mini", "cooper-se", "", 25000), ("mini", "countryman", "Cooper D JCW Kit", 24000),
    ("skoda", "octavia", "2.0 TDI Style", 20000), ("skoda", "fabia", "Monte Carlo", 15000), ("mazda", "cx-5", "", 25000), ("mazda", "3", "Sport", 18000),
    ("subaru", "forester", "", 25000), ("tesla", "model-s", "Long Range", 60000), ("tesla", "model-3", "Performance", 45000),
    ("dodge", "ram", "", 60000), ("dodge", "challenger", "SXT", 35000), ("smart", "fortwo", "cabrio", 9000),
    ("chevrolet", "spark", "", 5000), 
    # senza prezzo
    ("ferrari", "458", "", 0), ("ferrari", "458", "", None), ("porsche", "911", "", "trattativa"), ("abarth", "595", "", 0),
]


def test_sportive_dentro():
    for make, model, version, price in SI:
        assert VS.e_sportiva(make, model, version, price), (make, model, version, price)


def test_non_sportive_fuori():
    for make, model, version, price in NO:
        assert not VS.e_sportiva(make, model, version, price), (make, model, version, price)


def test_allestimenti_esclusi():
    """I casi chiesti: "AMG Line", "M Sport", "S line", e poi "R-Line", "ST-Line", "GT Line", "N Line", "Sport"."""
    assert not VS.e_sportiva("mercedes-benz", "classe-a", "A 200 AMG Line", 90000)
    assert not VS.e_sportiva("bmw", "serie-5", "520d M Sport", 90000)
    assert not VS.e_sportiva("audi", "a5", "S line", 90000)
    assert not VS.e_sportiva("volkswagen", "golf", "R-Line", 30000)
    assert not VS.e_sportiva("ford", "fiesta", "ST-Line", 15000)
    assert not VS.e_sportiva("hyundai", "i30", "N Line", 20000)
    assert not VS.e_sportiva("kia", "sportage", "GT Line", 30000)
    assert not VS.e_sportiva("audi", "a3", "Sportback Sport", 20000)
    # ma la sportiva vera con l'allestimento scritto accanto resta
    assert VS.e_sportiva("bmw", "m4", "Competition M Sport seats", 90000)
    assert VS.e_sportiva("mercedes-benz", "classe-c", "C 43 AMG 4Matic Premium Plus", 90000)
    assert VS.e_sportiva("audi", "s3", "Sportback S line", 20000)


def test_nessun_prezzo_minimo():
    assert VS.e_sportiva("porsche", "boxster", "", 8000) and VS.e_sportiva("maserati", "biturbo", "", 600)
    assert VS.e_sportiva("audi", "rs3", "", 9000) and VS.e_sportiva("abarth", "500", "", 3000)
    assert VS.e_sportiva("mazda", "mx-5", "", 1500) and not VS.e_sportiva("mazda", "mx-5", "", 0)
    assert not hasattr(VS, "PREZZO_MIN_ALTRE")


def test_supercar_o_sportiva():
    """Filtro "Supercar / Sportive": supercar = marca di lusso oppure prezzo da 70.000 €."""
    assert VS.SUPERCAR_DA == 70000
    assert VS.e_supercar("ferrari", 50000) and VS.e_supercar("porsche", 9000) and VS.e_supercar("Aston Martin", 40000)
    assert VS.e_supercar("bmw", 70000) and VS.e_supercar("audi", 130000)
    assert not VS.e_supercar("bmw", 69999) and not VS.e_supercar("abarth", 12000) and not VS.e_supercar("mini", None)


def test_controllo_veloce_sql_non_perde_sportive():
    """Il filtro largo fatto in SQL lascia passare tutto quello che e_sportiva accetta."""
    for make, model, version, price in SI:
        assert VS.passa_controllo_veloce(make, model, version), (make, model, version)
    sql, par = VS._sql_candidate()
    assert "l.status = 'attivo'" in sql and set(par["lomb"]) == set(VS.LOMBARDIA) and len(par["lomb"]) == 12
    for nome in re.findall(r"%\((\w+)\)s", sql):
        assert nome in par, nome
    assert "description" not in sql                   # la prima lettura è leggera
    assert "25000" not in sql and not any(v == 25000 for v in par.values())
    for chiave in ("ferrari", "aston-martin", "rolls-royce", "abarth", "morgan"):
        assert chiave in par["intere"]
    assert "bmw" in par["m_bmw"] and "mercedes-benz" in par["m_mercedes"] and "alfa-romeo" in par["m_alfaromeo"]
    for v in par.values():                            # parole semplici: valgono uguali in Postgres e in Python
        if isinstance(v, str):
            assert re.fullmatch(r"[a-z0-9|]+", v), v


# ------------------------------------------------------------------------------------------------ esclusioni e schede
ORA = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
TEL = "333 1234567"


def _riga(id, **k):
    r = {"id": id, "source": "autoscout24", "url": f"https://esempio.invalid/annuncio/{id}", "title": "Ferrari 488 GTB",
         "make": "ferrari", "model": "488", "version_raw": "GTB", "year": 2018, "mileage_km": 20000 + id,
         "fuel": "benzina", "gearbox": "automatico", "power_kw": 492, "city": "Milano", "province": "MI",
         "price_eur": 200000 + id, "price_flags": [], "seller_type": "privato", "stage": "nuovo", "stage_reason": None,
         "first_seen_at": ORA - timedelta(days=9), "telefono_ok": None, "description": "Auto perfetta, tagliandi ufficiali.",
         "n_photos": 12}
    r.update(k)
    return r


def _scegli(righe, **k):
    k.setdefault("nomi_tutti", SR.nomi_esclusi_re("", commercianti=True))
    k.setdefault("nomi_sempre", SR.nomi_esclusi_re("", commercianti=False))
    return VS.scegli(righe, ora=ORA.timestamp(), **k)


def test_dodici_province_lombarde():
    assert VS.LOMBARDIA == {"BG", "BS", "CO", "CR", "LC", "LO", "MN", "MI", "MB", "PV", "SO", "VA"}
    righe = [_riga(i + 1, province=p) for i, p in enumerate(sorted(VS.LOMBARDIA))]
    items, tolte = _scegli(righe)
    assert {it["provincia"] for it in items} == set(VS.LOMBARDIA) and len(items) == 12 and not tolte


def test_fuori_lombardia_e_roma():
    righe = [_riga(1), _riga(2, province="RM", city="Roma"), _riga(3, province="TO", city="Torino"),
             _riga(4, province="NO", city="Novara"), _riga(5, province="PC", city="Piacenza"),
             _riga(6, province=None, city="Roma"), _riga(7, province=None, city="Bergamo"),
             _riga(8, province=None, city="Paesino Sconosciuto"), _riga(9, province=None, city="Latina")]
    items, tolte = _scegli(righe)
    assert {it["id"] for it in items} == {1, 7}
    assert tolte["roma"] == 3 and tolte["fuori_lombardia"] == 4


def test_province_riconoscimento_largo_solo_in_vetrina():
    """Comuni lombardi che sito_regole.provincia_di non conosce, provincia scritta per esteso, regione."""
    casi = {"Desenzano del Garda": "BS", "Salò": "BS", "Lumezzane": "BS", "Darfo Boario Terme": "BS", "Sirmione": "BS",
            "Limone sul Garda": "BS", "Ponte di Legno": "BS", "Suzzara": "MN", "Castiglione delle Stiviere": "MN",
            "Morbegno": "SO", "Livigno": "SO", "Bormio": "SO", "Aprica": "SO", "Madesimo": "SO",
            "Chiesa in Valmalenco": "SO", "Casalmaggiore": "CR"}
    for citta, sigla in casi.items():
        assert VS.provincia_vetrina(None, citta) == (sigla, True), citta
        assert VS.provincia_vetrina("", citta.upper()) == (sigla, True), citta
    for scritta, sigla in (("Brescia", "BS"), ("Mantova", "MN"), ("SONDRIO", "SO"), ("Provincia di Cremona", "CR"),
                           ("Monza e Brianza", "MB"), ("Monza e della Brianza", "MB"), ("Milano", "MI"), ("Bergamo", "BG"),
                           ("Como", "CO"), ("Lecco", "LC"), ("Lodi", "LO"), ("Pavia", "PV"), ("Varese", "VA")):
        assert VS.provincia_vetrina(scritta, "Paesino Sconosciuto") == (sigla, True), scritta
    assert set(VS.PROVINCE_ESTESE.values()) == set(VS.LOMBARDIA)
    assert set(VS.comuni_in_piu().values()) <= set(VS.LOMBARDIA) and len(VS.comuni_in_piu()) > 350
    # la regione dell'annuncio basta, anche senza provincia
    assert VS.provincia_vetrina(None, "Paesino Sconosciuto", "Lombardia") == (None, True)
    assert VS.provincia_vetrina(None, "Paesino Sconosciuto", "Veneto") == (None, False)
    # fuori: sigla o provincia non lombarda vincono sul nome del comune e sulla regione sbagliata
    assert VS.provincia_vetrina("VR", "Sirmione") == ("VR", False)
    assert VS.provincia_vetrina("Verona", "Sirmione") == (None, False)
    assert VS.provincia_vetrina("Roma", "Roma") [1] is False and VS.provincia_vetrina("Torino", None) == (None, False)
    assert VS.provincia_vetrina("RM", "Roma", "Lombardia") == ("RM", False)
    # le regole del sito non cambiano: provincia_di continua a non conoscerli
    assert SR.provincia_di(None, "Livigno") is None and SR.provincia_di("Brescia", "Paesino") is None
    righe = [_riga(1, province=None, city="Desenzano del Garda"), _riga(2, province="Mantova", city="Suzzara"),
             _riga(3, province=None, city="Boh", region="Lombardia"), _riga(4, province="Verona", city="Peschiera"),
             _riga(5, province="Roma", city=None), _riga(6, province=None, city="Livigno")]
    items, tolte = _scegli(righe)
    assert {it["id"]: it["provincia"] for it in items} == {1: "BS", 2: "MN", 3: None, 6: "SO"}
    assert tolte == {"fuori_lombardia": 1, "roma": 1}


def test_segnaposto_del_telefono_mai_nella_scheda():
    righe = [_riga(1, version_raw=f"GTB chiamare {TEL}", title=f"Ferrari 488 {TEL}"),
             _riga(2, make=None, model=None, version_raw=None, title=f"Vendo Ferrari tel {TEL}", province="MI")]
    it = VS.scheda(righe[0])
    testo = json.dumps([it, VS.scheda(righe[1])])
    assert "numero col bottone" not in testo and TEL not in testo
    assert it["versione"] == "GTB chiamare"


def test_tempo_massimo_e_pausa_dopo_errore(monkeypatch):
    """Revisione: statement_timeout a ogni blocco; a memoria vuota e database fermo non si riprova a ogni richiesta."""
    class Conn:
        def __init__(self):
            self.sql = []

        def execute(self, sql, par=None):
            self.sql.append(sql)
            return _Ris([])

        def rollback(self):
            pass
    c = Conn()
    assert VS.carica(c)[0] == [] and c.sql[0].startswith("SET LOCAL statement_timeout") and "180s" in c.sql[0]
    c = Conn()
    VS._leggi_blocco(c, [1], frozenset())
    assert c.sql[0].startswith("SET LOCAL statement_timeout")
    chiamate = []

    def rotto(conn, scarti=frozenset()):
        chiamate.append(1)
        raise RuntimeError("database fermo")

    @contextmanager
    def connetti():
        yield object()
    monkeypatch.setattr(VS, "carica", rotto)
    monkeypatch.setattr(VS, "_cache", {"t": 0.0, "items": None, "totale": 0})
    monkeypatch.setattr(VS, "_errore", [0.0])
    import time as _t
    for _ in range(3):
        try:
            VS.elenco(connetti, None)
            raise AssertionError("doveva fallire")
        except VS.InCaricamento:
            pass
        for _i in range(200):                       # 23/11: la lettura gira in un thread a parte
            if not VS._cache_lock.locked():
                break
            _t.sleep(0.01)
    assert len(chiamate) == 1 and VS.PAUSA_DOPO_ERRORE == 60 and VS.ATTESA_LOCK == 25
    assert not VS._cache_lock.locked()


def test_loconte_e_commercianti_esclusi():
    righe = [
        _riga(1),
        _riga(2, description="Vendo per conto di Loconte Auto"),                               # nel testo: sempre
        _riga(3, seller_type="commerciante", title="Porsche 911 Officine Mulino", make="porsche", model="911"),
        _riga(4, seller_type="commerciante"),                                                  # nome del salone
        _riga(5, seller_type="commerciante", description="SR Auto vi aspetta in sede"),        # solo se commerciante
        _riga(6, seller_type="privato", description="Yaris 1.5 SR auto usata poco"),           # privato: resta
        _riga(7, seller_type="commerciante"),
    ]
    items, tolte = _scegli(righe, saloni={4: "Cosval Auto Srl", 7: "Autosalone Bianchi"})
    assert {it["id"] for it in items} == {1, 6, 7} and tolte["commerciante_escluso"] == 4
    assert next(it for it in items if it["id"] == 7)["salone"] == "Autosalone Bianchi"


def test_scarti_del_cliente():
    righe = [_riga(1), _riga(2), _riga(3, stage="scartato", stage_reason="analisi_manuale: tolta dal cliente: non la voglio"),
             _riga(4, stage="scartato", stage_reason="nessun_margine")]      # scartata dal giro degli affari: in vetrina resta
    items, tolte = _scegli(righe, scarti={2})
    assert {it["id"] for it in items} == {1, 4} and tolte["scarto_cliente"] == 2


def test_rata_e_leasing_fuori():
    righe = [
        _riga(1),
        _riga(2, description="Subentro leasing, rata mensile 1.900 €"),
        _riga(3, description="Possibile finanziamento"),                                     # privato: testo dei privati
        _riga(4, seller_type="commerciante", description="Possibilità di finanziamento e permute"),   # frase di rito: resta
        _riga(5, seller_type="commerciante", title="Ferrari 488 GTB leasing"),
        _riga(6, seller_type="commerciante", description="Prezzo valido solo con finanziamento"),
        _riga(7, price_flags=["leasing_o_rata"]),
        _riga(8),                                                                              # prezzo a rata per l'AI
        _riga(9),                                                                              # AI: rata poco probabile
        _riga(10, seller_type="commerciante", description="Canone mensile da 990 €"),
    ]
    items, tolte = _scegli(righe, rate={8: 0.9, 9: 0.2})
    assert {it["id"] for it in items} == {1, 4, 9}
    assert tolte["rata_leasing"] == 6 and tolte["prezzo_a_rata"] == 1


def test_senza_prezzo_e_doppioni():
    righe = [_riga(1), _riga(2, price_eur=None), _riga(3, price_eur=0), _riga(4, price_eur=1),
             _riga(5, stage_reason="doppione | visto altrove"), _riga(6, stage_reason="truffa"),
             # la stessa auto pubblicata due volte (anno, km, prezzo uguali): resta quella con più foto
             _riga(7, source="subito", mileage_km=5000, price_eur=150000, n_photos=3),
             _riga(8, source="autoscout24", mileage_km=5000, price_eur=150000, n_photos=9),
             _riga(9, source="autoscout24", mileage_km=5000, price_eur=150000, n_photos=4)]
    items, tolte = _scegli(righe)
    assert [it["id"] for it in items] == [1, 8]
    assert tolte["senza_prezzo"] == 3 and tolte["doppione"] == 3 and tolte["truffa"] == 1


def test_ordine_per_prezzo_e_tetto_per_gruppo():
    righe = [_riga(i, price_eur=p, mileage_km=1000 * i) for i, p in ((1, 90000), (2, 300000), (3, 150000), (4, 40000))]
    righe += [_riga(10 + i, make="abarth", model="595", version_raw="", title="Abarth 595", price_eur=9000 + i,
                    mileage_km=50000 + i) for i in range(5)]
    items, tolte = _scegli(righe)
    assert [it["prezzo"] for it in items] == [300000, 150000, 90000, 40000, 9004, 9003, 9002, 9001, 9000]
    assert [it["supercar"] for it in items] == [True] * 4 + [False] * 5
    # il tetto è per gruppo: le supercar (più care) non tolgono il posto alle sportive
    tagliate = VS.taglia(items, limite_supercar=2, limite_sportive=3)
    assert [it["prezzo"] for it in tagliate] == [300000, 150000, 9004, 9003, 9002]
    assert VS.LIMITE == VS.LIMITE_SUPERCAR + VS.LIMITE_SPORTIVE and VS.LIMITE_SPORTIVE >= VS.LIMITE_SUPERCAR >= 500


def test_riepilogo_per_il_log_senza_dati_personali():
    righe = [_riga(1), _riga(2, seller_type="commerciante", description=f"Chiamare {TEL}"),
             _riga(3, make="abarth", model="595", version_raw="", title="Abarth 595", price_eur=9000),
             _riga(4, make="bmw", model="m3", version_raw="Competition", title="BMW M3", price_eur=80000, seller_type="commerciante")]
    items, _ = _scegli(righe, saloni={2: "Autosalone Rossi", 4: "Autosalone Bianchi"})
    r = VS.riepilogo(items)
    assert r == {"totale": 4, "supercar": 3, "sportive": 1, "privati": 2, "commercianti": 2,
                 "con_mercato": 0, "con_guadagno": 0, "per_marca": {"Ferrari": 2, "Abarth": 1, "BMW": 1}}
    testo = json.dumps(r)
    assert "Autosalone" not in testo and TEL not in testo and "esempio.invalid" not in testo


# 23/11 sera: "mercato" e "confronti" ora ci sono (solo da valutazioni salvate); margine e guadagno restano vietati
# 23/11 sera (cliente): "guadagno" ora c'è, ma solo quello salvato dalla valutazione (mai calcolato qui)
VIETATE = ("margine", "pv", "gain", "affare", "stima", "netto", "sconto", "telefono", "tel",
           "phone", "description", "descrizione", "rip_lo", "rip_hi", "top", "trattare")


def test_scheda_senza_margine_e_senza_telefono():
    senza_nome = _riga(1, description=f"Chiamare il {TEL} ore pasti", title=f"Ferrari 488 tel {TEL}", version_raw=None,
                       model=None, make=None)
    righe = [_riga(2, seller_type="commerciante", telefono_ok=True), _riga(3),
             _riga(4, description=f"Chiamare il {TEL} ore pasti")]
    items, _ = _scegli(righe, saloni={2: f"Autosalone Rossi {TEL}"})
    assert len(items) == 3
    testo = json.dumps(items, default=str)
    assert TEL not in testo and TEL.replace(" ", "") not in testo and "3331234567" not in testo
    for it in items:
        assert not set(it) & set(VIETATE), set(it) & set(VIETATE)
        assert set(it) == {"id", "marca", "modello", "versione", "nome", "anno", "km", "carb", "cambio", "prezzo", "citta",
                           "provincia", "supercar", "venditore", "salone", "ha_telefono", "giorni", "fonte", "foto", "n_foto", "url",
                           "mercato", "confronti", "guadagno"}
        assert it["mercato"] is None and it["confronti"] is None        # nessuna valutazione: mai un mercato inventato
        assert isinstance(it["ha_telefono"], bool)
    per = {it["id"]: it for it in items}
    assert per[4]["ha_telefono"] is True and per[3]["ha_telefono"] is False and per[2]["ha_telefono"] is True
    assert per[2]["venditore"] == "commerciante" and per[3]["venditore"] == "privato" and per[3]["salone"] is None
    assert per[3]["giorni"] == 9 and per[3]["url"].endswith("/3") and per[3]["foto"] and per[3]["prezzo"] == 200003
    assert per[3]["marca"] == "Ferrari" and per[3]["carb"] == "benzina" and per[3]["cambio"] == "automatico"
    # numero scritto nel titolo di un annuncio senza marca/modello: il nome non lo mostra
    assert TEL not in VS.scheda(senza_nome)["nome"]


# ------------------------------------------------------------------------------------------------ mercato
def test_mercato_solo_da_valutazioni_vere():
    """23/11 sera: il mercato si mostra solo con almeno 5 confronti, valutazione recente e non di ripiego."""
    from datetime import datetime, timedelta, timezone
    ora = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    t = ora.timestamp()
    ieri, vecchia = ora - timedelta(days=1), ora - timedelta(days=8)
    assert VS.MERCATO_N_MIN == 5 and VS.MERCATO_GIORNI == 7 and VS.MERCATO_GIORNI_EU == 2
    assert VS.mercato_valido("45000", "5", ieri, ora=t) == (45000, 5)
    assert VS.mercato_valido(45000.4, 12, ieri, ora=t) == (45000, 12)
    assert VS.mercato_valido(45000, 4, ieri, ora=t) is None                      # pochi confronti
    assert VS.mercato_valido(45000, None, ieri, ora=t) is None                   # confronti ignoti
    assert VS.mercato_valido(None, 9, ieri, ora=t) is None and VS.mercato_valido(0, 9, ieri, ora=t) is None
    assert VS.mercato_valido(45000, 9, vecchia, ora=t) is None                   # più di 7 giorni
    assert VS.mercato_valido(45000, 9, ora - timedelta(days=6, hours=23), ora=t) == (45000, 9)
    assert VS.mercato_valido(45000, 9, None, ora=t) is None                      # senza data
    assert VS.mercato_valido(45000, 9, ieri.isoformat(), ora=t) == (45000, 9)    # data scritta come testo
    assert VS.mercato_valido(45000, 9, ieri, ripiego="true", ora=t) is None      # stima di ripiego: non è un mercato
    assert VS.mercato_valido(45000, 9, ieri, ripiego="false", ora=t) == (45000, 9)
    assert VS.mercato_valido(45000, 9, ora - timedelta(days=3), giorni=VS.MERCATO_GIORNI_EU, ora=t) is None
    # solo letture: nessun calcolo, nessuna AI
    for q, _g in VS.MERCATO_SQL:
        assert q.lstrip().upper().startswith("SELECT") and "ANY(%s)" in q
    assert "valutazioni_policy" in VS.MERCATO_SQL[0][0] and "supercar_eu" in VS.MERCATO_SQL[1][0]
    import inspect
    sorgente = inspect.getsource(VS)
    assert "anthropic" not in sorgente.lower() and "import policy_v2" not in sorgente and "from ..pricing" not in sorgente


def test_mercato_nella_scheda_senza_margine():
    items, _ = _scegli([_riga(1), _riga(2), _riga(3)], mercati={1: (230000, 8), 3: (190000, 5)})
    per = {it["id"]: it for it in items}
    assert per[1]["mercato"] == 230000 and per[1]["confronti"] == 8
    assert per[2]["mercato"] is None and per[2]["confronti"] is None
    assert per[3]["mercato"] == 190000 and per[3]["prezzo"] == 200003           # sopra il mercato: si mostra lo stesso
    for it in items:
        assert not set(it) & set(VIETATE)
    testo = json.dumps(items, default=str).lower()
    for parola in ("margine", "netto", "gain", "sconto"):
        assert parola not in testo
    r = VS.riepilogo(items)
    assert r["con_mercato"] == 2 and r["totale"] == 3


def test_mercato_letto_a_blocchi_con_tempo_massimo():
    from datetime import datetime, timedelta, timezone
    ora = datetime.now(timezone.utc)

    class Conn:
        def __init__(self, rotta=False):
            self.sql, self.rotta, self.rollback_n = [], rotta, 0

        def execute(self, sql, par=None):
            self.sql.append(sql)
            if "valutazioni_policy" in sql:
                if self.rotta:
                    raise RuntimeError("relation does not exist")
                return _Ris([{"listing_id": 1, "m": "50000", "n": "9", "ripiego": None, "quando": ora},
                             {"listing_id": 2, "m": "50000", "n": "3", "ripiego": None, "quando": ora},
                             {"listing_id": 4, "m": "50000", "n": "9", "ripiego": None, "quando": ora - timedelta(days=30)}])
            if "supercar_eu" in sql:
                return _Ris([{"listing_id": 1, "m": 99999, "n": 20, "ripiego": None, "quando": ora},
                             {"listing_id": 3, "m": 300000, "n": 6, "ripiego": None, "quando": ora},
                             {"listing_id": 5, "m": 300000, "n": 6, "ripiego": None, "quando": ora - timedelta(days=3)}])
            return _Ris([])

        def rollback(self):
            self.rollback_n += 1
    c = Conn()
    assert VS._leggi_mercati(c, [1, 2, 3, 4, 5]) == {1: (50000, 9, None), 3: (300000, 6, None)}
    assert c.sql[0].startswith("SET LOCAL statement_timeout") and c.sql[2].startswith("SET LOCAL statement_timeout")
    c = Conn(rotta=True)                                # tabella mancante: resta supercar_eu, la vetrina non si ferma
    assert VS._leggi_mercati(c, [1, 3]) == {1: (99999, 20, None), 3: (300000, 6, None)} and c.rollback_n == 1
    c = Conn()
    VS._leggi_blocco(c, [1], frozenset())
    assert any("valutazioni_policy" in q for q in c.sql) and any("supercar_eu" in q for q in c.sql)


# ------------------------------------------------------------------------------------------------ endpoint
class _Richiesta:
    def __init__(self):
        self.headers, self.path_params, self.query_params, self.cookies = {"authorization": "Bearer x"}, {}, {}, {}


class _Ris:
    def __init__(self, righe):
        self.righe = righe

    def fetchone(self):
        return self.righe[0] if self.righe else None

    def fetchall(self):
        return list(self.righe)


class _Conn:
    """Solo le letture di richiede(): permessi e riga dell'abbonamento."""
    def __init__(self, dealer):
        self.dealer = dealer

    def execute(self, sql, par=None):
        if "FROM dealers" in sql:
            return _Ris([self.dealer])
        return _Ris([])

    def commit(self):
        pass

    def rollback(self):
        pass


def _prepara(monkeypatch, utente, dealer, items):
    from dealfinder.web import app as A

    @contextmanager
    def connetti():
        yield _Conn(dealer)
    monkeypatch.setenv("ABBONAMENTO_OBBLIGATORIO", "1")
    monkeypatch.setattr(A, "current_user", lambda request, admin=False: dict(utente))
    monkeypatch.setattr(A, "connect", connetti)
    monkeypatch.setattr(A.abbonamento, "max_valido", lambda conn, did: False)
    chiamate = []

    def elenco(connetti_, scarti=None):
        chiamate.append(1)
        return list(items), len(items)
    monkeypatch.setattr(VS, "elenco", elenco)
    return A, chiamate


def _schede(ids):
    return VS.scegli([_riga(i) for i in ids], ora=ORA.timestamp())[0]


def test_accesso_negato_senza_abbonamento(monkeypatch):
    dealer = {"permessi": ["auto"], "richiede_abbonamento": True, "abbonamento_stato": None, "abbonamento_tolleranza": None,
              "con_password": False, "role": "commerciante", "paga_carta": False}
    A, chiamate = _prepara(monkeypatch, {"id": 7, "role": "commerciante"}, dealer, _schede([1, 2]))
    try:
        A.api_vetrina_supercar(_Richiesta())
        raise AssertionError("senza abbonamento la vetrina non si vede")
    except A.HTTPError as e:
        assert e.status == 402
    assert not chiamate                                   # l'elenco non viene nemmeno letto
    # senza il permesso "auto": 403
    A, chiamate = _prepara(monkeypatch, {"id": 7, "role": "commerciante"}, dict(dealer, permessi=[]), _schede([1]))
    try:
        A.api_vetrina_supercar(_Richiesta())
        raise AssertionError("senza permesso la vetrina non si vede")
    except A.HTTPError as e:
        assert e.status == 403
    assert not chiamate


def test_senza_accesso_401(monkeypatch):
    from dealfinder.web import app as A
    monkeypatch.setenv("SITO_APERTO", "1")                # anche a sito aperto: la vetrina è solo per chi ha l'accesso
    r = _Richiesta()
    r.headers = {}
    try:
        A.api_vetrina_supercar(r)
        raise AssertionError("senza accesso la vetrina non si vede")
    except A.HTTPError as e:
        assert e.status == 401


def test_abbonato_vede_e_solo_admin_ids(monkeypatch):
    from dealfinder.web import app as A
    solo = sorted(A.SOLO_ADMIN_IDS)[0]
    assert solo == 21604
    dealer = {"permessi": ["auto"], "richiede_abbonamento": True, "abbonamento_stato": "active", "abbonamento_tolleranza": None,
              "con_password": False, "role": "commerciante", "paga_carta": False}
    schede = _schede([1, solo, 3])
    A, _ = _prepara(monkeypatch, {"id": 7, "role": "commerciante"}, dealer, schede)
    d = json.loads(A.api_vetrina_supercar(_Richiesta()).body)
    assert [it["id"] for it in d["items"]] == [3, 1] and d["totale"] == 2
    testo = json.dumps(d)
    for parola in ("margine", "\"telefono\"", "\"tel\""):
        assert parola not in testo, parola
    # l'amministratore la vede (anche senza abbonamento)
    A, _ = _prepara(monkeypatch, {"id": 1, "role": "admin"}, {}, schede)
    d = json.loads(A.api_vetrina_supercar(_Richiesta()).body)
    assert solo in [it["id"] for it in d["items"]] and d["totale"] == 3 and d["limite"] == VS.LIMITE and d["supercar_da"] == 70000


def test_database_fermo_risposta_vuota(monkeypatch):
    A, _ = _prepara(monkeypatch, {"id": 1, "role": "admin"}, {}, [])

    def rotto(*a, **k):
        raise RuntimeError("database fermo")
    monkeypatch.setattr(VS, "elenco", rotto)
    d = json.loads(A.api_vetrina_supercar(_Richiesta()).body)
    assert d["items"] == [] and d["errore"] == "non disponibile"


def test_rotta_prima_di_quella_con_slug():
    from dealfinder.web import app as A
    percorsi = [getattr(r, "path", None) for r in A.routes]
    assert percorsi.index("/api/vetrina/supercar") < percorsi.index("/api/vetrina/{slug}")


def test_cache_di_qualche_minuto(monkeypatch):
    import time as _t
    letture = []

    def carica(conn, scarti=frozenset()):
        letture.append(set(scarti))
        return _schede([1, 2]), {"oltre_il_tetto": 3, "non_lette": 4, "tolte": {}}

    @contextmanager
    def connetti():
        yield object()

    def aspetta():
        for _i in range(300):
            if not VS._cache_lock.locked():
                return
            _t.sleep(0.01)
    monkeypatch.setattr(VS, "carica", carica)
    monkeypatch.setattr(VS, "_cache", {"t": 0.0, "items": None, "totale": 0})
    monkeypatch.setattr(VS, "_errore", [0.0])
    try:
        VS.elenco(connetti, lambda: {5})
    except VS.InCaricamento:
        pass
    aspetta()
    a = VS.elenco(connetti, lambda: {5})
    b = VS.elenco(connetti, lambda: {5})
    assert a == b and len(a[0]) == 2 and a[1] == 9 and letture == [{5}]          # la seconda dalla memoria
    VS._cache["t"] -= VS.CACHE_SECONDI + 1
    VS.elenco(connetti, lambda: {5})
    aspetta()
    assert len(letture) == 2 and 600 <= VS.CACHE_SECONDI <= 3600

    def rotto(conn, scarti=frozenset()):
        raise RuntimeError("database fermo")
    monkeypatch.setattr(VS, "carica", rotto)
    VS._cache["t"] -= VS.CACHE_SECONDI + 1
    assert VS.elenco(connetti, None) == a                           # database fermo: resta l'elenco di prima
    aspetta()
    assert VS.elenco(connetti, None) == a and not VS.da_ricaricare()   # e non si riprova subito


def test_vetrina_fuori_dalle_regole_e_dall_elenco():
    """La vetrina non tocca sito_regole né l'elenco del sito (conteggio delle auto, email ai commercianti)."""
    import inspect
    assert "vetrina_supercar" not in inspect.getsource(SR)
    sorgente = inspect.getsource(VS)
    assert "_elenco[" not in sorgente and "_elenco_per" not in sorgente and "SOGLIE" not in sorgente


# ------------------------------------------------------------------------------------------------ Postgres vero
def test_query_su_postgres_vero():
    from tests import pg_vero
    conn = pg_vero.nuovo_db("vetrina_supercar")
    if conn is None:
        return
    conn.execute("""CREATE TABLE listings (id BIGINT PRIMARY KEY, source TEXT, source_id TEXT, url TEXT, title TEXT,
        description TEXT, make TEXT, model TEXT, version_raw TEXT, year INT, mileage_km INT, fuel TEXT, gearbox TEXT,
        power_kw INT, price_eur INT, price_flags TEXT[] NOT NULL DEFAULT '{}', seller_type TEXT, city TEXT, province TEXT,
        region TEXT,
        status TEXT, stage TEXT, stage_reason TEXT, first_seen_at TIMESTAMPTZ DEFAULT now(), telefono_ok BOOLEAN)""")
    conn.execute("CREATE TABLE listing_photos (id BIGSERIAL PRIMARY KEY, listing_id BIGINT, position INT, source_url TEXT)")
    conn.execute("CREATE TABLE commercianti_as24 (seller_id TEXT PRIMARY KEY, nome TEXT)")
    conn.execute("CREATE TABLE commercianti_as24_annunci (seller_id TEXT, listing_id TEXT)")
    conn.execute("CREATE TABLE decisione_ai (listing_id BIGINT, risposta JSONB)")
    conn.execute("CREATE TABLE valutazioni_policy (listing_id BIGINT, computed_at TIMESTAMPTZ DEFAULT now(), riassunto JSONB, categoria TEXT, margine INTEGER)")
    conn.execute("CREATE TABLE supercar_eu (listing_id BIGINT PRIMARY KEY, m INTEGER, n INTEGER, computed_at TIMESTAMPTZ DEFAULT now(), categoria TEXT DEFAULT 'opportunita', margine INTEGER, r INTEGER, costi INTEGER)")
    conn.execute("""INSERT INTO valutazioni_policy (listing_id, computed_at, riassunto) VALUES
        (4, now() - interval '3 days', '{"M": 70000, "n": 9}'), (4, now() - interval '1 day', '{"M": 84500.4, "n": 7}'),
        (7, now(), '{"M": 26000, "n": 4}'), (16, now() - interval '9 days', '{"M": 25000, "n": 30}'),
        (14, now(), '{"M": 13000, "n": 11, "stima_ripiego": true}'), (10, now() - interval '1 day', '{"M": 90000, "n": 12}'),
        (10, now(), '{"M": 150000, "n": 9}')""")
    # revisione: l'ultima valutazione di 10 è inaffidabile (dati_insufficienti) -> niente mercato, nemmeno quella prima
    conn.execute("UPDATE valutazioni_policy SET categoria = 'dati_insufficienti' WHERE listing_id = 10 AND riassunto->>'M' = '150000'")
    conn.execute("""UPDATE valutazioni_policy SET riassunto = riassunto || '{"margine": 6100, "acquisto": 80000}'::jsonb
                    WHERE listing_id = 4 AND riassunto->>'M' = '84500.4'""")
    conn.execute("UPDATE valutazioni_policy SET margine = 99999 WHERE listing_id = 4 AND riassunto->>'M' = '70000'")
    conn.execute("""INSERT INTO supercar_eu (listing_id, m, n, computed_at) VALUES (1, 225000, 6, now()),
        (4, 1, 99, now()), (6, 80000, 12, now() - interval '3 days')""")
    # come Supercar Europa: R = min(r, M × 0,92 × 0,95) = 196.650, guadagno = 196.650 − 170.000 − 200 = 26.450
    conn.execute("UPDATE supercar_eu SET margine = 99999, r = 200000, costi = 200, n = 9 WHERE listing_id = 1")
    conn.execute("INSERT INTO supercar_eu (listing_id, m, n, computed_at, categoria) VALUES (19, 60000, 8, now(), 'leasing')")
    # lead_tutti_dealer / lead_tutti_ann non ci sono: la lettura dei nomi non deve fermare la vetrina
    auto = [
        (1, "ferrari", "488", "GTB", 170000, "MI", "Milano", "attivo", "privato"),
        (21, "ferrari", "f8", "Tributo", 200000, "MI", "Milano", "attivo", "privato"),         # oltre 180.000 €
        (2, "ferrari", "488", "GTB", 220000, "RM", "Roma", "attivo", "privato"),                # Roma
        (3, "bmw", "serie-3", "320d M Sport", 40000, "MI", "Milano", "attivo", "commerciante"),  # non sportiva
        (4, "bmw", "m3", "Competition", 80000, "BG", "Bergamo", "attivo", "privato"),
        (5, "porsche", "911", "Carrera", 95000, "MI", "Milano", "venduto", "privato"),          # non attiva
        (6, "Aston-Martin", "vantage", "V8", 70000, None, "Como", "attivo", "privato"),         # provincia dalla città
        (7, "audi", "rs3", "Sportback", 24000, "MI", "Milano", "attivo", "privato"),            # nessun prezzo minimo
        (14, "abarth", "595", "Turismo", 12000, "LO", "Lodi", "attivo", "privato"),
        (15, "volkswagen", "golf", "2.0 TDI R-Line", 26000, "MI", "Milano", "attivo", "privato"),  # allestimento
        (16, "volkswagen", "golf", "2.0 TSI GTI", 23000, "PV", "Pavia", "attivo", "commerciante"),
        (17, "fiat", "panda", "1.2", 6000, "MI", "Milano", "attivo", "privato"),
        (18, "porsche", "boxster", "2.7", 20500, None, "Desenzano del Garda", "attivo", "privato"),   # comune in più
        (19, "porsche", "cayman", "2.7", 30500, "Mantova", "Suzzara", "attivo", "privato"),            # provincia per esteso
        (20, "porsche", "macan", "2.0", 41000, "Verona", "Peschiera del Garda", "attivo", "privato"), # fuori
        (8, "maserati", "ghibli", "Diesel", 18000, "BS", "Brescia", "attivo", "commerciante"),  # Loconte (nome)
        (9, "lamborghini", "urus", "", 250000, "MI", "Milano", "attivo", "privato"),            # prezzo a rata (AI)
        (10, "mercedes-benz", "classe-a", "A 45 S AMG 4Matic+", 52000, "VA", "Varese", "attivo", "commerciante"),
        (11, "mercedes-benz", "classe-a", "A 180 AMG Line", 33000, "VA", "Varese", "attivo", "commerciante"),
        (12, "ferrari", "f8", "Tributo", 0, "MI", "Milano", "attivo", "privato"),               # senza prezzo
        (13, "porsche", "macan", "", 45000, "TO", "Torino", "attivo", "privato"),               # fuori Lombardia
    ]
    for i, mk, md, ve, pr, pv, ci, st, se in auto:
        conn.execute("INSERT INTO listings (id, source, source_id, url, title, description, make, model, version_raw, year, "
                     "mileage_km, fuel, gearbox, price_eur, seller_type, city, province, status, stage) VALUES "
                     "(%s,'autoscout24',%s,%s,%s,%s,%s,%s,%s,2020,%s,'benzina','automatico',%s,%s,%s,%s,%s,'nuovo')",
                     (i, f"as{i}", f"https://esempio.invalid/{i}", f"{mk} {md}", "Bella auto.", mk, md, ve, 10000 + i, pr,
                      se, ci, pv, st))
    conn.execute("INSERT INTO listing_photos (listing_id, position, source_url) VALUES (1, 0, 'x'), (1, 1, 'y')")
    conn.execute("INSERT INTO commercianti_as24 VALUES ('s8', 'Loconte Automobili'), ('s4', 'Autosalone Orobico')")
    conn.execute("INSERT INTO commercianti_as24_annunci VALUES ('s8', 'as8'), ('s4', 'as4')")
    conn.execute("INSERT INTO decisione_ai VALUES (9, '{\"prezzo_rata\": {\"noul\": 0.95}}')")
    items, numeri = VS.carica(conn)
    tolte = numeri["tolte"]
    # 23/11 sera (cliente): solo privati e solo sotto i 180.000 € (fuori 21, i commercianti 8, 10, 16 e la 9 da 250.000)
    # 23/11 sera (cliente): solo opportunità = mercato e guadagno salvati, guadagno > 0, prezzo sotto il mercato
    assert [it["id"] for it in items] == [1, 4], ([it["id"] for it in items], numeri)
    assert all(it["venditore"] == "privato" and it["prezzo"] < 180000 for it in items)
    assert {it["id"]: (it["mercato"], it["confronti"], it["guadagno"]) for it in items} == {
        4: (84500, 7, 6100), 1: (225000, 9, 26450)}
    assert tolte["non_opportunita"] == 5 and numeri["riepilogo"]["con_guadagno"] == 2
    per = {it["id"]: it for it in items}
    assert per[1]["n_foto"] == 2 and per[1]["foto"] and per[4]["foto"] is None
    assert VS.e_opportunita({"mercato": 100, "guadagno": 5, "prezzo": 90})
    assert not VS.e_opportunita({"mercato": 100, "guadagno": 5, "prezzo": 120})      # sopra il mercato
    assert not VS.e_opportunita({"mercato": 100, "guadagno": -5, "prezzo": 90})
    assert not VS.e_opportunita({"mercato": None, "guadagno": None, "prezzo": 90})


def test_caricamento_in_un_thread_a_parte(monkeypatch):
    """23/11 pom.: in produzione la lettura supera i 20 s di una richiesta. La pagina non aspetta: prima risposta
    'in caricamento', poi l'elenco; a memoria vecchia si risponde con l'elenco di prima e intanto si ricarica."""
    import threading as _th
    import time as _t
    via = _th.Event()

    def lenta(conn, scarti=frozenset()):
        via.wait(5)
        return [{"id": 1, "marca": "Porsche", "supercar": True, "venditore": "privato"}], {}

    @contextmanager
    def connetti():
        yield object()
    monkeypatch.setattr(VS, "carica", lenta)
    monkeypatch.setattr(VS, "_cache", {"t": 0.0, "items": None, "totale": 0})
    monkeypatch.setattr(VS, "_errore", [0.0])
    try:
        VS.elenco(connetti, None)
        raise AssertionError("doveva dire in caricamento")
    except VS.InCaricamento:
        pass
    assert VS._cache_lock.locked() and not VS.avvia(connetti, None)      # un solo caricamento alla volta
    via.set()
    for _i in range(300):
        if not VS._cache_lock.locked():
            break
        _t.sleep(0.01)
    items, totale = VS.elenco(connetti, None)
    assert [it["id"] for it in items] == [1] and totale == 1
    VS._cache["t"] = 0.0                                  # memoria vecchia: elenco di prima, ricarica dietro
    items, _ = VS.elenco(connetti, None)
    assert [it["id"] for it in items] == [1]
    for _i in range(300):
        if not VS._cache_lock.locked():
            break
        _t.sleep(0.01)
    assert not VS._cache_lock.locked() and not VS.da_ricaricare()


def test_guadagno_come_gli_affari():
    """23/11 sera: il guadagno della vetrina è quello degli affari, mai il margine grezzo."""
    assert VS.guadagno_sito({"margine": 5000, "acquisto": 20000}, 20000, None) == 5000
    assert VS.guadagno_sito({"margine": 5000, "acquisto": 20000}, 21000, None) is None          # prezzo cambiato
    assert VS.guadagno_sito({"margine": 5000, "sospetto_truffa": True}, 20000, None) is None
    assert VS.guadagno_sito('{"margine": 4000}', 20000, None) == 4000
    assert VS.guadagno_sito({}, 20000, None) is None
    r = {"m": 100000, "r": 99000, "n": 8, "costi": 0}
    assert VS.guadagno_supercar(r, 80000) == int(100000 * 0.92 * 0.95) - 80000
    assert VS.guadagno_supercar(dict(r, n=7), 80000) is None                                  # pochi confronti
    assert VS.guadagno_supercar(r, 60000) is None                                             # troppo bella
