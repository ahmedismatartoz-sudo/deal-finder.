# Fondamenta del server (16/11, ramo `fond-server`)

Nessuna regola di business cambia: soglie (SOGLIE_PV, margini, policy_v2), tetto commercianti, zona, km, telefono
obbligatorio, categorie e funzioni di valutazione sono le stesse (l'impronta della policy resta identica). Cambiano
**quando** e **dove** girano i lavori. Nessun servizio nuovo, nessuna variabile di Render da cambiare.

## 1. Cron `dealfinder-opportunita` (ogni 3 ore, "7 */3"): giro corto

Prima (diagnosi confermata sul codice): in fila lettura della zona (anche completa, 227–285 min), Torino completo
(50–72 min), supercar Europa (92 min + 60–71 min; la riga `supercar_eu` letta da `_due` non veniva **mai** scritta,
quindi girava a ogni giro), confronti extra (33–39 min), ricalcolo (42 min, lo fa anche il cron mercato), voci di
COLLECT_PRIMA. Totale 4–10 ore per 2–37 annunci nuovi: Render saltava i giri dopo.

Ora (`pipeline/turni.py`, `pipeline/collect.py`):

| Cosa | Quando | Dove |
|---|---|---|
| Annunci nuovi della zona (lettura **incrementale**) + valutazione e pubblicazione | ogni giro | cron opportunità |
| Torino incrementale | ogni TORINO_ORE (6) ore, come prima | cron opportunità |
| Confronti extra | 1 volta al giorno, a turno (subito se una città è in ritardo) | cron opportunità |
| Supercar: raccolta, poi **una** valutazione | 1 volta al giorno, a turno (due turni separati) | cron opportunità |
| Ricalcolo (voce `ricalcolo…` di COLLECT_PRIMA) | 1 volta al giorno (conta anche quello del cron mercato); subito dopo ≥ 1.000 confronti nuovi | cron opportunità |
| Altre voci di COLLECT_PRIMA | con la loro cadenza (`@ore`), a turno | cron opportunità |
| Torino completo | 1 volta al giorno, a turno (o nella notte se resta tempo) | opportunità / notte |
| **Zona completa** | 1 volta per notte (non nelle notti di mercato_nord, mai oltre 36 ore) | cron mercato, passo notturno |
| Lavori una tantum lunghi (raccolta60, perche_fuori, prova_cieca) | notte | cron mercato, passo notturno |

- **Tetto del giro**: `GIRO_ORE_MAX` = 2 h 45 min dall'avvio (riserva 15 min per la fine). Un lavoro a turno parte solo
  se la sua stima ci sta; stima = mediana delle durate degli ultimi giri riusciti (`turno:<nome>` in job_runs) + 15%,
  senza storia i valori in `turni.STIME_MIN`. Il più in ritardo per primo (il ricalcolo prima di tutti, come dal 6/11);
  un lavoro in ritardo di oltre un giorno parte anche se la stima supera di poco (85%) il tempo rimasto.
- **Lucchetti** (lucchetti di sessione Postgres, si liberano da soli se il processo muore): `turno:<nome>` per ogni
  lavoro a turno, `zona_cron:lettura` e `torino_cron:lettura` per le letture. Se la notte sta leggendo tutta la zona,
  il giro delle 03:07/06:07 salta la zona (i nuovi arrivano anche dal tempo reale); la notte aspetta fino a 45 minuti
  un giro ancora in lettura.
- **Avanzamento durante il giro**: riga `giro:opportunita` in job_runs aperta all'avvio, con il battito, la fase in
  corso, i secondi di ogni fase e i lavori rimandati; chiusa a fine giro (anche da `sys.exit`, prima di `ai_prezzi`).
- **Stessa configurazione nella notte**: a ogni giro il cron opportunità salva una fotografia delle sue variabili non
  segrete (`tempo_reale`, riga `config:opportunita`; mai chiavi, password, URL, proxy, variabili di processo). Il
  passo notturno del cron mercato la applica solo per sé (poi l'ambiente torna com'era): stesse funzioni, stesse
  variabili, stesso risultato per la stessa auto.
- **Ripiego**: se non c'è un giro completo della zona (o di Torino) da oltre 48 ore (`COMPLETO_MAX_ORE`), il giro ogni 3
  ore torna a decidere da sé come prima (completo ogni ZC_COMPLETO_ORE).
- **Interruttori** (default nel codice, nessuno da impostare): `GIRO_LEGGERO=0` torna al giro di prima (tutto in fila,
  solo i vecchi limiti di tempo); `GIRO_ORE_MAX`, `GIRO_RISERVA_MIN`, `CE_TURNO_ORE`, `RICALCOLO_ORE_MIN`,
  `TORINO_COMPLETO_TURNO_ORE`, `NOTTE=0` (niente passo notturno), `NOTTE_FINE_ORE` (10), `NOTTE_ZONA_ORE` (20).
- Rivalutazione solo delle righe cambiate: già nel codice dal 15/11 (`UTIL_INVARIATE_ORE`, `RIC_INVARIATE_ORE`, lettura
  incrementale con "già visto e invariato"); qui non è toccata (pricing/valutazione sono di un altro gruppo).

### Orari (UTC; Roma = UTC+1)

| Ora | Lavoro |
|---|---|
| 00:07, 03:07, …, 21:07 | giro opportunità (≤ 2 h 45 min) |
| 01:13 | cron mercato: raccolta mercato (~3–3,5 h), modello prezzi, poi **passo notturno** in un processo nuovo (zona completa ~4–5 h, Torino completo se resta tempo, lavori lunghi in coda), poi mercato_nord (se dovuto) o il modello AI dei prezzi. Fine entro ~10–10,5 h |
| 05:41 | verifica disponibilità (invariato) |

## 2. Righe "in corso" mai chiuse: battito

- `db.log_job` e `ciclo.inizia` danno alle loro righe un **battito** (`stats.battito`) ogni `BATTITO_S` (300 s) da un
  filo daemon con connessioni brevi; `finish`/`fatto`/`fallito` lo fermano.
- All'avvio di ogni cron `db.chiudi_interrotti` chiude come `ok=false, stats.interrotto=true, motivo='battito fermo'`
  le righe con il battito fermo da oltre 20 minuti, o senza battito (codice di prima) e iniziate da oltre 13 ore. Mai
  le righe battute dal processo stesso.
- `ciclo.inizia` chiude anche i **fratelli** con la data nel nome (`valore_reale_notte:20261115` quando parte
  `valore_reale_notte:20261116`), solo se fermi.
- Sentinella: allarme `cron_battito:<lavoro>` per il battito fermo da oltre 30 minuti; righe senza battito per 7 giorni
  (prima 48 ore).

## 3. Falsi allarmi della sentinella

- `_riga_istantanea` scrive la sezione `fascia` (`fascia:inc`, `fascia:trattare`) per i commercianti della fascia
  richiesta (stessa regola del sito, `sito_regole.fascia`, solo letta): il tetto dei 40 lontani non li conta più
  (prima 60 su 40, falso). `sezione_base()` ridà la sezione di prima agli altri controlli (sotto soglia li controlla
  ancora).
- `IN_CORSO_ORE` 12 (limite dei cron di Render), per lavoro: `giro:opportunita` 3,5, `zona_cron` 4.
- Allarmi ricordati nel database: un allarme continuo (visto nelle ultime 6 ore) resta "ripetuto" anche dopo riavvii;
  torna "nuovo" (email) se sparisce per oltre 6 ore, o come promemoria dopo 24 ore di fila. Prima tornava nuovo ogni 6 ore.
- Nuovi: `cron_giro_lungo` (giri opportunità oltre 180 min nelle 24 ore), attesa `giro:opportunita` entro 7 ore.

## 4. Lavori una tantum: dal sito al cron (`pipeline/coda_lavori.py`)

- Le voci **lunghe** di `BREVO_IMPORT_LISTE` (raccolta60, perche_fuori, prova_cieca) il sito non le esegue più: le
  scrive nella tabella `coda_lavori` (creata dal codice, una riga per voce: un riavvio non la duplica). Le fa il passo
  notturno del cron mercato. Le voci **brevi** (chiamate Brevo) restano nel sito per non ritardare gli invii; con
  `LAVORI_NEL_CRON=tutti` (sul sito) vanno anche loro al cron (giro opportunità, fino a 3 ore dopo). `LAVORI_NEL_CRON=0`
  torna a prima. Le voci che leggono l'elenco del sito in memoria (su_misura, nuovi_sm, rinvio_sm, su_misura2, zona,
  wa30, "1") restano nel sito.
- Stesso codice (spostato senza cambiarlo, tolte solo le attese d'avvio) e **stesse chiavi `_una_volta`**: una voce già
  fatta non si rifà. Stasera `raccolta60:tel1611` è già segnata dal sito (`raccolta60:raccolta60:tel1611`): il cron la
  trova fatta e non la ripete (test `test_raccolta60_gia_fatta_non_si_rifa`).
- Se al cron manca una variabile che serve (es. `BREVO_API_KEY`) la voce passa a `web` e la fa il sito; se nessun cron
  la prende entro 6 ore (brevi) o 30 ore (lunghe) la fa il sito (filo `lavori-ripiego`, ogni 30 minuti).
- Gli altri ~90 thread del sito non sono toccati (fuori da questo ramo; la parte giro "nuovi"/ELENCO è di un altro gruppo).

## 5. Deploy a cascata: `render.yaml` / `render-worker.yaml`

- `buildFilter.ignoredPaths` per ogni servizio: test, `*.md`, `scripts/` per tutti; per cron e tempo reale anche
  grafica e pagine statiche (`dealfinder/web/static/**`, `landing/**`, `presentazione/**`, `demo-*/**`).
  `dealfinder/web/app.py` **non** si ignora per i cron: `pipeline/esporta.py` e `bande.py` ne importano `LIST_SQL`.
- `autoDeploy: false` per `dealfinder-facebook` e `dealfinder-facebook-2` (Facebook spento per regola del cliente).
- Vale solo se i servizi sono collegati al Blueprint (sync del Blueprint su Render). Se sono stati creati a mano, gli
  stessi filtri vanno messi nel pannello di ogni servizio (Settings → Build Filters, Auto-Deploy). Nessuna variabile cambia.

## Verifiche nei log e nel database dopo il deploy

```sql
-- durata dei giri opportunità (< 180 min) e quanti nelle ultime 24 ore (8)
SELECT started_at, round(extract(epoch FROM finished_at - started_at)/60) AS minuti, ok, stats->'rimandati'
FROM job_runs WHERE job = 'giro:opportunita' AND started_at > now() - interval '24 hours' ORDER BY started_at;
-- 0 righe in corso con il battito fermo
SELECT job, started_at, stats->>'battito' FROM job_runs WHERE finished_at IS NULL
  AND ((stats ? 'battito' AND (stats->>'battito')::timestamptz < now() - interval '30 minutes')
       OR (NOT COALESCE(stats ? 'battito', false) AND started_at < now() - interval '13 hours'));
-- lavori a turno e zona completa notturna
SELECT job, max(finished_at) FROM job_runs WHERE ok AND (job LIKE 'turno:%' OR job LIKE '%:completo') GROUP BY job;
-- coda dei lavori una tantum
SELECT valore, stato, chi, nota FROM coda_lavori ORDER BY creato_at DESC LIMIT 20;
```

Nei log: `GIRO opportunita fase …`, `GIRO opportunita finito in N min`, `GIRO lavori rimandati …`, `TURNO <nome> fatto
in N min`, `NOTTE fatta in N min`, `INTERROTTI chiusi N lavori`, `LAVORI (cron, lunghi) …`, `SENTINELLA` con
`cron.giri_24h` = 8 e `cron.giri_lunghi_24h` = 0.

## Rischi noti

- Il primo giorno le stime sono quelle nel codice: un lavoro può essere rimandato finché non ha una durata registrata
  (si sblocca con la regola del "ritardo oltre un giorno").
- La zona completa ora è nel cron mercato: se la fotografia della configurazione manca (cron opportunità mai partito
  col codice nuovo) la notte la salta; dopo 48 ore senza completo il giro ogni 3 ore torna a farla da sé.
- Nelle notti di mercato_nord la zona completa si salta (al massimo 36 ore senza).
- Il deploy del sito interrompe un lavoro in corso nel sito (come sempre): aspettare la fine di `raccolta60:tel1611`.
