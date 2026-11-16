# Ramo `fond-auto` (16/11/2026): più auto senza cambiare nessuna regola

Richiesta del cliente: *"non voglio cambiare nessuna regola, trova nuovi modi per far entrare più auto; se non ci sono
abbastanza confronti vai a vedere le auto vendute, se non ci sono neanche lì guarda le auto di altre città"*.

Nessuna regola toccata: `regole.py` invariato (VERSIONE `regole-2026-11-12a`, impronta default `f23abb1d281a`),
`POLICY_REV` invariato, stesse soglie `SOGLIE_PV`, stessi margini, stesso minimo di confronti (8 sotto 10.000 €, 5
sopra), tetto commercianti 60/40, zona, 150.000 km, telefono obbligatorio. Si lavora su dati, ricerca e bug.
Nessun deploy, nessuna variabile cambiata su Render.

## Cosa cambia

### 1. Cascata dei confronti (`pricing/ricalcolo.py`: `_cascata`, `serve_cascata`)
Dopo la ricerca di sempre (zona di casa → ripiego Nord sotto 5 confronti → tutta Italia per la fascia alta), **solo**
per le auto ancora `dati_insufficienti` con confronti sotto il minimo del sito:
1. `vendute`: stesse auto **sparite** dagli annunci negli ultimi 45 giorni (`CASCATA_VENDUTE_GIORNI`), zona di casa +
   Nord, ultimo prezzo chiesto; doppioni, truffe e prezzi finti esclusi; un'auto sparita con gli stessi dati di una
   già contata (anche ripubblicata sullo stesso sito) si conta una volta;
2. `nord+vendute`: annunci attivi del resto del Nord (anche quando in zona ce n'erano 5–7, prima nessun ripiego);
3. `italia+vendute`: annunci attivi di tutta Italia della base prezzi (tutti i prezzi, al massimo 4.000 righe).

Ci si ferma appena si arriva al minimo. Le fasce anno/km (livelli 1–3) restano quelle di `policy_v2.seleziona`.
**Non gonfiare i prezzi**: un passo che porterebbe il mercato M sopra la stima precedente non si usa. Nessuna
correzione per area (nel codice non ce n'è una misurata; il cliente ha osservato che a sud i prezzi calano, quindi i
confronti lontani tendono ad abbassare M). Ogni valutazione lo dichiara: `fonte_confronti` (anche nel riassunto),
`cascata` (passi provati ed esito), `fuori_zona`, un'ipotesi in chiaro, i confronti venduti segnati `venduta`.
Le auto già valutate (opportunità, candidato, non conveniente) e quelle con abbastanza confronti hanno la valutazione
**identica** (test con i casi reali del DUMP_CANDIDATI del 16/11). Interruttore `CASCATA_CONFRONTI=0`.
In `policy_v2.pulisci` un'auto sparita entra solo se segnata dalla cascata (`fonte_confronto="venduta"`).

### 2. Recupero (`pricing/recupero.py`, chiamato alla fine di `ricalcolo.esegui`)
Ogni ricalcolo del cron rivaluta al massimo `RECUPERO_PER_GIRO` (400) privati online la cui ultima valutazione (ultimi
10 giorni) è in attesa del giudice oppure `dati_insufficienti` per pochi confronti / dispersi / mercato non affidabile e
non ancora passata dalla cascata. Stessa strada (`valuta_e_scrivi`, fonte `cron-recupero`): vale il tetto del giudice
del cron (`GIUDICE_ATTESA_PER_GIRO`, 150 chiamate), così il budget AI comune non finisce come il 13/11.
Spegnimento: `RECUPERO_CONFRONTI=0`. A mano: `python -m dealfinder.pricing.recupero`.

### 3. Worker con la configurazione del sito (`render-worker.yaml`) e AI mancante
- Il worker riceve con `fromService` (riferimento al servizio web `scovo`, stessa chiave) la chiave AI, il sale delle
  targhe e le 126 variabili impostate sul sito nella riga `CONFIG_IMPRONTA` del 16/11 (LETTORE, TR_SUBITO_SENZA_NUMERO=1,
  SOGLIE_PV, POLICY_*, ZONA_SITO, AI_BUDGET_GIORNO, ...). Nessun valore e nessun segreto nel file.
  Esclusi: `BRIGHTDATA_*`, `SCRAPER_PROXY` (raccolta a pagamento) e **`TR_SUBITO`** (sul sito è `0`; nel worker è la
  lettura completa di Subito, copiarla toglierebbe annunci). **Da decidere col cliente.**
  Si applica con un sync del Blueprint, a mano, dopo la revisione.
- Nel codice (`ricalcolo._giudice_ai`): senza chiave AI vale un giudizio già archiviato; se non c'è, la valutazione
  **non si pubblica**: resta "in attesa" (`giudice_mancante`, `giudice_senza_ai`) e la riprendono `riprova_giudice` del
  sito e il recupero del cron. Acceso di default sui servizi Render (variabile `RENDER`), spento in locale/test;
  `GIUDICE_SENZA_AI_ATTESA=1|0` per forzarlo.
- **Aggancio per `tempo_reale`** (non toccato, altro agente): nessuno obbligatorio, la regola sta in
  `valuta_con_ripiego`. Facoltativo: in `tempo_reale.valuta_e_pubblica`, dopo `valuta_e_scrivi`, contare
  `es.valutazioni[lid].get("giudice_senza_ai")` in `stats["attesa_senza_ai"]` per vederlo nel log del worker.

### 4. Bug `_soglie_pv` (`web/scovo.py`)
Su Render `SOGLIE_PV` finisce con `1000000000:5000` e il codice aggiungeva di nuovo la fascia "oltre": tabella con
l'ultima fascia doppia → impronta regole del sito `c1380592c34a` contro `f23abb1d281a` del cron col default → la guardia
delle scritture vedeva `regole_cambiate`. Ora la fascia si aggiunge solo se manca. Soglie effettive identiche (test su
tutti i prezzi 0–80.000 €), impronta del sito = impronta del cron = `f23abb1d281a`.

### 5. Doppioni Subito/AutoScout24 sul sito (`scovo.togli_doppioni`, passo `doppioni` in `sito_regole.elenco_sito`)
Dopo tutte le regole e prima dei tetti: siti diversi, stessa marca e modello, stesso anno, km ±3%, prezzo ±7%, stessa
regione → una sola auto (prima quella col numero, poi più foto, poi margine, poi id più basso). `SITO_DOPPIONI=0` spegne.
Passo visibile in `SITO_FUNNEL` (`doppioni.stessa_auto_altro_sito`).

### 6. Numero nascosto di Subito (non riacceso)
Spento il 14/11 (c8b544c, `TEL_SUBITO_API` default 0). Motivo dai log del 13/11: 300 ricerche al giorno
(tetto `TEL_API_MAX_GIORNO`) con **0 "sì"**, circa 140 "no" e 65 "non trovato"; dopo le 12:30 ogni giro finiva
su `subito:tetto_giorno`. La pagina dell'annuncio dice sempre `showPhone=false` (prova del 17/10). Riaccenderlo non
porterebbe auto e consumerebbe richieste verso Subito (rischio di blocco), quindi resta spento. Il controllo
AutoScout24 (pagina del venditore) resta attivo. Se si vuole riprovare: `TEL_SUBITO_API=1` con `TEL_API_MAX_GIORNO=50`
per un giorno e si guarda `subito_api:si` in `TEL_NASCOSTO`.

## Stima delle auto in più (ipotesi dichiarate)
- Bacino: 329 `dati_insufficienti` tra gli 8.464 annunci valutati del funnel del 16/11 (DIAG_DATI_INSUFF: in 4 giorni
  ~2.100 righe "solo N confronti", ~1.400 "dispersi", ~100 "45% sotto la mediana", su tutte le fonti).
- Ipotesi A: la cascata porta al minimo il 35–55% di quelle con "solo N confronti" e "45% sotto" (un annuncio resta
  online ~30 giorni: in 45 giorni le auto sparite sono circa quante le attive; il Nord per i 5–7 confronti sotto
  10.000 €); i "dispersi" migliorano poco.
- Ipotesi B: dopo la valutazione passa il 6–12% (oggi 406 su 6.760 = 6%; queste auto hanno spesso prezzo molto sotto
  il mercato, quindi più margine).
- Risultato: **+10/+25 auto** dalla cascata; le attese del giudice (fino a ~225 in più il 13/11) tornano valutate entro
  qualche giro; **−9** doppioni (erano la stessa auto due volte). Netto atteso: **+5/+20 auto**, di qualità migliore
  (niente valutazioni non controllate dal worker).

## Verifiche nei log dopo il deploy (a cura di chi fa il merge)
- `CONFIG_IMPRONTA`: `policy.impronta` = `f23abb1d281a` su web, cron e worker; `POLICY_RIEPILOGO.impronta_regole` uguale.
- `DIAG_DATI_INSUFF` / funnel `valutata.dati_insufficienti`: verso ≤ ~175.
- `VALUTAZIONE ... scrittura:meno_confronti` in calo; nuovi contatori `cascata:vendute`, `cascata:nord+vendute`,
  `cascata:italia+vendute`, `cascata:nessun_aiuto`.
- `RECUPERO_CONFRONTI {...}` a fine ricalcolo: `cascata_usata`, `sul_sito_possibili`.
- `SITO_FUNNEL`: passo `doppioni` ~9 tolte al primo giro, poi 0 doppioni sul sito.
- Worker: righe `GIUDICE_AI`/`VALUTAZIONE` con il giudice attivo; nessuna `giudice_senza_ai` dopo il sync.

## Rischi
- Sparite = ultimo prezzo chiesto (non il prezzo di vendita): stesso tipo di dato delle attive, calibrato una volta.
- Più query per le sole auto in cascata (al massimo 3 per gruppo, in cache per gruppo).
- `fromService` richiede che le variabili esistano sul servizio `scovo`: se una viene tolta dal sito, il sync del
  Blueprint del worker va aggiornato. `ANTHROPIC_API_KEY` e `PLATE_SALT` sul sito arrivano anche dal gruppo
  `dealfinder-secrets`: verificare nel piano del Blueprint che il riferimento si risolva (in alternativa
  `fromGroup: dealfinder-secrets`, che però porterebbe anche le chiavi Bright Data al worker).
