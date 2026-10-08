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
| `db/schema.sql` | Tabelle: annunci, storico prezzi, veicoli, valutazioni, commercianti, aperture |
| `dealfinder/core/` | Modello annuncio, pulizia prezzi (+IVA, rate, civetta), dati mancanti, deduplica |
| `dealfinder/collectors/` | `subito.py` (collettore), `meta.py` (adattatore fornitore esterno) |
| `dealfinder/pricing/engine.py` | Confronti a 3 livelli, mercato privato vs commercianti, prezzo prudente, confidenza |
| `dealfinder/pricing/margin.py` | Margine per commerciante, IVA sul margine, soglie, classifica, regola 7 aperture |
| `dealfinder/pipeline/` | Lavori programmati (raccolta, filtro, approfondimento, verifica) |
| `tests/` | Test su dati di prova |

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
python -m dealfinder.pipeline.collect opportunita
python run_tests.py         # oppure: pytest
```

## Online (Render)

`render.yaml` descrive database e lavori programmati. Le chiavi (Anthropic,
fornitore Meta, proxy) si inseriscono nelle variabili d'ambiente di Render,
mai nel codice.

## Stato dei lavori

- [x] Fase 1 — database, pulizia, deduplica, motore prezzi, margine, collettore Subito, adattatore Meta
- [ ] Fase 2 — base di mercato reale, backtest settimanale
- [ ] Fase 3 — AI: estrazione testo, analisi foto, identificazione veicolo, agente ricambi
- [ ] Fase 4 — API e sito web commercianti, pannello amministratore
- [ ] Fase 5 — fornitore Meta collegato
- [ ] Fase 6 — messa online e prima raccolta reale

Da verificare al primo avvio reale: struttura JSON delle pagine Subito e nomi
delle province negli indirizzi di ricerca (`config.py`).
