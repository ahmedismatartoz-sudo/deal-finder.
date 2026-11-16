# Fondamenta della velocità (ramo `fond-velocita`, 16/11/2026)

Obiettivo: un annuncio nuovo di Subito o AutoScout24 visibile sul sito entro 10 minuti (p95), giro veloce del worker
sotto i 120 s (p95), nessun annuncio perso ai riavvii. **Nessuna regola di business è cambiata**: soglie, margini,
policy_v2, tetto commercianti, zona, limite km, telefono obbligatorio e categorie sono quelli di prima. L'impronta
della configurazione (`CONFIG_IMPRONTA`) e delle regole è identica a `origin/main` 7422ccf: lo verifica
`tests/test_fond_velocita.py::test_impronta_delle_regole_invariata`. Le variabili nuove iniziano tutte con `TR_` e non
entrano nell'impronta.

## Cosa è cambiato

| # | Problema | Cosa fa ora | Dove |
|---|----------|-------------|------|
| 1 | Il giro veloce Subito rileggeva la finestra "dall'ultimo giro meno 10 minuti" (~8.000 invariati al giorno) | Legge dal più recente e si ferma alla **prima pagina tutta nota**. "Nota" = nel database con lo stesso prezzo (un ribasso torna da valutare) o già scartata in questo processo (fuori zona) | `tempo_reale._subito_nuovi_leggero`, `_subito_veloce` |
| 2a | `_AS_VISTI` segnava gli id AutoScout24 **prima** del salvataggio: se il giro cadeva, persi fino alla lettura completa | Un annuncio in zona è "visto" solo **dopo** `_salva` (`_segna_visti`); si ricordano subito solo quelli fuori zona o illeggibili (non vanno mai nel database) | `_autoscout_nuovi`, `_segna_visti` |
| 2b | Il filo della lettura completa non era sorvegliato | Ha un suo Guardiano (`TR_COMPLETO_GIRO_MAX_MIN`, 120): oltre il limite il processo esce e Render lo riavvia | `tempo_reale_worker.main`, `Guardiano` |
| 2c | La ripresa dopo un 429 (`_autoscout_tutti`) girava dentro il giro veloce | La fa il filo della lettura completa, controllando ogni `TR_RIPRESA_CONTROLLO_MIN` minuti | `giro_ripresa_as`, `_completo_a_parte` |
| 2d | Due fili leggevano AutoScout24 in parallelo, ognuno con la sua pausa | Un **limitatore unico** per processo: tra due richieste almeno `TR_AS_INTERVALLO_S` secondi (default = `TR_AS_PAUSA_S`, 1,5: il ritmo di prima) | `Limitatore`, `ClienteLimitato` |
| 3 | Il sito rifaceva il giro "nuovi" accanto al worker (stesso lucchetto); lucchetto occupato senza log | Il sito si fa da parte con battito fresco **e** ultimo giro "nuovi" da meno di `TR_WORKER_GIRO_OK_MIN` (10) minuti: ora è sicuro perché il completo scrive la sua riga e la ripresa 429 è fuori dal giro veloce. Il Guardiano del giro veloce scende a **12 minuti** (prima 45). Riga `TR LUCCHETTO <nome> occupato` (e `TR NUOVI (sito) saltato`) quando il lucchetto è preso | `worker_attivo`, `Guardiano`, `con_lucchetto`, `app._tempo_reale` |
| 4 | Gli id da valutare stavano solo in memoria | **Coda persistente** `tr_coda` (migrazione `058_coda_tempo_reale.sql`): l'annuncio entra in coda nella stessa transazione in cui si salva; il giro dopo (anche in un altro processo) riprende quelli non valutati. Primo giro dopo l'avvio = giro di recupero (più pagine) | `_coda_metti`, `_coda_prendi`, `_coda_fatti`, `_valuta` |
| 5 | Per ogni annuncio invariato 6 istruzioni e un commit | Annunci già salvati, attivi e con lo stesso prezzo: **una** `UPDATE ... last_seen_at, last_checked_at` a blocchi (più il "sì" del telefono di Subito, solo sì/no) | `db.segna_invariati`, `tempo_reale._salva` |
| 6 | Al SIGTERM il worker finiva il giro (anche minuti) | L'arresto accende `tempo_reale.FERMA`: i giri si fermano alla fase dopo, le pause e le riprove dopo un 429 si interrompono; dopo `TR_ARRESTO_MAX_S` (25) uscita forzata. Quello già salvato resta in coda | `_Fine`, `fermato()`, `_dormi` |
| 7 | Un'auto pubblicata dal worker durante un ricalcolo dell'elenco (36–177 s) aspettava il calcolo successivo; controllo del worker ogni 60 s | Una richiesta con motivo (worker, giro nuovi) arrivata **durante** un calcolo fa ripartire il calcolo appena finito (uno solo); il sito controlla il worker ogni `TR_SITO_CONTROLLO_S` (20) s. Il calcolo resta fuori dalle richieste, con la copia salvata all'avvio | `app._aggiorna_elenco`, `_nuove_dal_worker` |
| 8 | `_ritardo_subito` contava anche i già visti riletti; nessuna misura fino al sito | Ritardo solo sugli annunci **nuovi**; catena registrata in `tr_coda`: pubblicato sul portale → letto → valutato → pubblicato dal worker → comparso nell'elenco del sito | `_ritardo_subito`, `_coda_fatti`, `segna_sul_sito`, `app._ritardo_sul_sito` |

Perché il punto 7 non è un aggiornamento "solo delle auto nuove": l'elenco applica regole sull'insieme (tetto dei
commercianti, quote, sezioni). Aggiungere un'auto senza ricalcolare potrebbe sforare il tetto, cioè cambiare una
regola di business. Si è scelto di non perdere mai un ricalcolo e di accorciare l'attesa prima di partire.

## Variabili nuove (tutte facoltative, default sicuri)

| Variabile | Default | Effetto |
|-----------|---------|---------|
| `TR_SUBITO_VELOCE` | `1` | `0` = giro veloce Subito come prima (rilettura della finestra) |
| `TR_SUBITO_PAGINE_VELOCE` / `TR_SUBITO_SECONDI_VELOCE` | `5` / `60` | pagine per regione e tempo massimo del giro veloce Subito |
| `TR_RECUPERO_PAGINE` / `TR_RECUPERO_SECONDI` | `15` / `240` | primo giro dopo l'avvio (recupero del tempo da fermi) |
| `TR_AS_INTERVALLO_S` | `TR_AS_PAUSA_S` (1,5) | secondi minimi tra due richieste AutoScout24 nel processo |
| `TR_COMPLETO_GIRO_MAX_MIN` | `120` | lettura completa bloccata oltre questo: il worker esce |
| `TR_RIPRESA_CONTROLLO_MIN` | `5` | ogni quanto il filo completo controlla la ripresa dopo un 429 |
| `TR_CODA` | `1` | `0` = niente coda persistente (id in memoria come prima). Senza la migrazione 058 la coda è spenta da sola |
| `TR_CODA_TENTATIVI` / `TR_CODA_RIPRESA_MIN` / `TR_CODA_MAX` | `5` / `5` / `300` | tentativi per annuncio, minuti prima di riprendere un annuncio "preso" da un giro caduto, annunci ripresi per giro |
| `TR_INVARIATI_VELOCE` | `1` | `0` = invariati con l'upsert completo come prima |
| `TR_ARRESTO_MAX_S` | `25` | secondi dopo il SIGTERM prima dell'uscita forzata |
| `TR_SITO_CONTROLLO_S` | `20` | ogni quanto il sito guarda se il worker ha pubblicato |

| `TR_WORKER_GIRO_OK_MIN` | `10` | il sito si fa da parte solo se l'ultimo giro "nuovi" del worker è partito da meno di così |

Cambiamenti senza variabile nuova: il Guardiano del giro veloce ora scatta dopo 12 minuti (`TR_WORKER_GIRO_MAX_MIN`
esistente, prima 45); all'arresto gli annunci presi e non valutati tornano liberi in coda (`preso_at` NULL); nel giro
veloce Subito un annuncio è "noto" solo se è ATTIVO nel database (un riapparso torna entro un giro).
Per tornare al sito che gira sempre resta `TR_WORKER=0`.

## Migrazione

`db/migrations/058_coda_tempo_reale.sql`: crea `tr_coda` (`CREATE TABLE/INDEX IF NOT EXISTS`, si può rieseguire).
La applica il sito all'avvio come le altre. Finché manca, worker e sito lavorano come prima (controllo con
`to_regclass`). Le righe chiuse da più di 7 giorni si cancellano da sole (una volta all'ora).

## Come verificarlo nei log

- `TR NUOVI (worker) in Ns: {...}`: `tempo_giro_s` (obiettivo p95 < 120), `tempo_subito_s`, `tempo_autoscout_s`,
  `tempo_salva_s`, `tempo_valuta_s`, `subito_pagine` (di solito 1 per regione), `subito_noti`,
  `invariati_a_blocchi`, `coda:messi`, `coda:ripresi`, `coda:valutati`, `recupero_avvio` (primo giro),
  `ritardo_letto_valutato_p95_s`, `ritardo_annuncio_pubblicata_p95_min`, `ritardo_subito_*` (ora solo i nuovi).
- `TR_RITARDO {"id", "fonte", "annuncio_letto_min", "letto_valutato_s", "annuncio_pubblicata_min"}`: una riga per
  ogni auto messa sul sito dal tempo reale.
- `ELENCO_AGGIORNATO in Ns motivo=worker nuove=K` e `SITO_RITARDO {"annuncio_sito_p50_min", "annuncio_sito_p95_min",
  "letto_sito_*", "pubblicata_sito_*", "calcolo_s"}`: ritardo vero fino all'elenco del sito (obiettivo
  `annuncio_sito_p95_min` ≤ 10). `ELENCO_DI_NUOVO`: ricalcolo ripartito perché il worker ha pubblicato durante il calcolo.
- `TR CODA: ripresi N annunci rimasti da valutare`: dopo un riavvio o un giro caduto (deve comparire dopo un deploy
  fatto a metà giro); `TR CODA: N annunci abbandonati`: troppi tentativi (li valuta il cron).
- `TR LUCCHETTO nuovi occupato ...` / `TR NUOVI (sito) saltato`: due copie in concorrenza (normale solo per pochi
  minuti durante un deploy).
- `TR WORKER: richiesta di arresto, chiudo entro 25 secondi` seguita da `TR WORKER: arrestato in N s`: N < 30.
  `arresto non finito ... esco subito` = uscita forzata (la coda resta).
- `TR RIPRESA AUTOSCOUT (a parte)`: ripresa dopo un 429 fatta dal filo completo (non più nel giro veloce).
- In SQL: `SELECT esito, count(*), percentile_cont(.95) WITHIN GROUP (ORDER BY extract(epoch FROM sito_at - annuncio_at)/60)
  FROM tr_coda WHERE letto_at > now() - interval '1 day' GROUP BY 1;`

## Test

`tests/test_fond_velocita.py` (un test o più per ogni punto, connessioni e HTTP finti),
`tests/test_fond_velocita_pg.py` (Postgres vero con tutte le migrazioni: ciclo della coda `_coda_metti` →
giro caduto → `_coda_prendi` con `SKIP LOCKED` davanti a un blocco di un'altra sessione → `_coda_fatti`, più
`segna_sul_sito`, rilascio, pulizia e `segna_invariati`) e i test esistenti aggiornati
dove il comportamento è cambiato di proposito (`test_tr_veloce.py`: visto dopo il salvataggio;
`test_buchi_worker.py`: Guardiano a 12 minuti).
