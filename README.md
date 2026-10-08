# Deal Finder

SaaS per commercianti di auto usate: raccoglie ogni giorno annunci da Subito.it e
Facebook Marketplace, stima il prezzo di rivendita, il costo dei ricambi e il
margine, e mostra solo le opportunità che superano le soglie.

Deal Finder è un intermediario: mostra l'opportunità e il link all'annuncio
originale; il commerciante contatta il venditore sulla piattaforma.

## Come funziona

```
Subito (collettore interno) ─┐
Meta Marketplace (fornitore  ├─► pulizia + deduplica ─► base di mercato (storico prezzi)
  esterno, es. Apify)        ─┘                               │
                                                               ▼
            filtro veloce (regole + AI)  ─►  approfondimento candidati
            (foto, identificazione veicolo, agente ricambi, prezzi, margine)
                                                               ▼
            API  ─►  sito web commercianti  (poi app mobile, stessa API)
```

## Struttura

| Cartella | Contenuto |
|---|---|
| `db/migrations/` | Tabelle (applicate in ordine da `python -m dealfinder.init_db`) |
| `dealfinder/core/` | Modello annuncio, pulizia prezzi (+IVA, rate, civetta), dati mancanti, deduplica |
| `dealfinder/collectors/` | `subito.py` (collettore), `meta.py` (adattatore fornitore esterno) |
| `dealfinder/ai/` | Lettura testo, analisi foto, identificazione veicolo, agente ricambi con ricerca web, tassonomia danni |
| `dealfinder/pricing/` | Motore prezzi, margine, motivazioni e controlli, backtest settimanale |
| `dealfinder/pipeline/` | Raccolta, filtro + approfondimento, verifica disponibilità |
| `dealfinder/web/` | API (Starlette) e sito per commercianti in `static/` |
| `tests/` | Test su dati di prova (`python run_tests.py`) |

## Flusso di un annuncio

`nuovo` → regole sul prezzo → lettura testo (AI veloce) → stima rapida dal mercato
→ foto (AI veloce, 3 foto) → `candidato` → foto complete (AI approfondita)
→ identificazione veicolo (anche da targa, se configurato) → agente ricambi
→ valutazione e margine → `approfondito`. Ogni scarto ha un motivo (`stage_reason`).

Il margine mostrato al commerciante viene ricalcolato con **i suoi** costi, soglie
e tipo di ricambio preferito (originale, aftermarket, usato).

## API (usata dal sito e dalla futura app)

| Metodo | Percorso | Cosa fa |
|---|---|---|
| POST | `/api/auth/login` | Accesso, restituisce il token |
| GET/PUT | `/api/me` | Profilo, zona, budget, costi, soglie |
| GET | `/api/opportunities` | Lista (`status`, `sort`, `damaged`, `source`, `province`, `max_price`) |
| GET | `/api/opportunities/{id}` | Scheda completa |
| POST | `/api/opportunities/{id}/open` | Registra l'apertura e dà il link dell'annuncio |
| POST | `/api/opportunities/{id}/feedback` | Esito: contattato, trattativa, comprata, venduta, scartata |
| GET | `/api/activity` | Le auto del commerciante |
| GET/POST | `/api/notifications`, `/api/notifications/seen` | Nuove opportunità |
| GET/POST/PATCH | `/api/admin/...` | Pannello amministratore e account |

## Regole di prodotto

- **Soglie** (sul margine netto, configurabili per commerciante): 2.000 € se l'acquisto è sotto 5.000 €, 3.000 € sopra.
- **Margine** = rivendita prudente − acquisto − ricambi − trasporto − pratiche − preparazione − riserva garanzia − riserva imprevisti − IVA sul margine. **La manodopera è esclusa**: la valuta ogni commerciante.
- **Rivendita prudente** = 25° percentile del mercato commercianti, corretto per anno e km, meno lo sconto di trattativa (default 8%, da calibrare con le vendite reali).
- **Da verificare** invece di "opportunità" quando: meno di 5 confronti, confronti troppo dispersi, solo confronti larghi, stato dell'auto non verificato, ricambi non stimati, segnali di truffa.
- **Danni**: solo leggeri (carrozzeria, fari, vetri, specchietti, cerchi). Esclusi telaio, airbag, alluvione, motore/cambio.
- **Aperture**: un annuncio sparisce quando l'hanno aperto 7 commercianti diversi.
- **Dati personali**: niente nomi né telefoni dei venditori; la targa si salva solo come hash.

## Avvio in locale

```bash
pip install -r requirements.txt
cp .env.example .env        # e compila i valori
python -m dealfinder.init_db
python -m dealfinder.pipeline.collect mercato
python -m dealfinder.pipeline.collect opportunita   # raccolta + analisi
uvicorn dealfinder.web.app:app --reload            # sito su http://localhost:8000
python run_tests.py
```

## Online (Render)

`render.yaml` crea: database, sito/API, e quattro lavori programmati
(opportunità ogni 3 ore, mercato ogni notte, verifica disponibilità ogni giorno,
qualità delle stime ogni lunedì). Le chiavi si inseriscono nel pannello Render.

## Da verificare al primo avvio reale

- Struttura JSON delle pagine Subito e nomi delle province negli indirizzi (`config.py`).
- Nomi dei modelli AI e versione dello strumento di ricerca web (variabili `AI_*`).
- Formato dei dati dello scraper Meta scelto (`FIELD_MAP` in `collectors/meta.py`, `META_PROVIDER_INPUT`).
- Sconto di trattativa (8%) e soglie di confidenza: da tarare con il backtest e le vendite reali.

## Collegare lo scraper di Facebook (Apify)

1. Account su apify.com, scegliere lo scraper e provarlo dalla console su Milano.
2. Chiave API: Apify → Settings → API & Integrations.
3. Su Render: `META_PROVIDER_TOKEN` (chiave), `META_ACTOR_ID` (es. `crawloop/facebook-marketplace-scraper`),
   `META_PROVIDER_INPUT` (una ricerca o una lista di ricerche). Esempio per `crawloop`:

```json
[
  {"marketplaceLocation": "milan", "categoryId": "vehicles", "radiusKm": 60, "minPrice": 500, "maxPrice": 20000,
   "daysSinceListed": 1, "maxItems": 400, "includeSeller": true,
   "proxyConfiguration": {"useApifyProxy": true, "apifyProxyGroups": ["RESIDENTIAL"], "apifyProxyCountry": "IT"}},
  {"marketplaceLocation": "brescia", "categoryId": "vehicles", "radiusKm": 30, "minPrice": 500, "maxPrice": 20000,
   "daysSinceListed": 1, "maxItems": 200, "includeSeller": true,
   "proxyConfiguration": {"useApifyProxy": true, "apifyProxyGroups": ["RESIDENTIAL"], "apifyProxyCountry": "IT"}}
]
```

I nomi dei campi dipendono dallo scraper: verificarli su un esempio reale e, se serve, aggiornare `FIELD_MAP`.
