# Ramo `fond-auto` (16/11/2026): più auto senza cambiare nessuna regola

Richiesta del cliente: *"non voglio cambiare nessuna regola, trova nuovi modi per far entrare più auto; se non ci sono
abbastanza confronti vai a vedere le auto vendute, se non ci sono neanche lì guarda le auto di altre città"*.

Nessuna regola toccata: `regole.py` invariato (VERSIONE `regole-2026-11-12a`, impronta default `f23abb1d281a`),
`POLICY_REV` invariato, stesse soglie `SOGLIE_PV`, stessi margini, stesso minimo di confronti (8 sotto 10.000 €, 5
sopra), tetto commercianti 60/40, zona, 150.000 km, telefono obbligatorio. Si lavora su dati, ricerca e bug.
Nessun deploy, nessuna variabile cambiata su Render.

## Cosa cambia

### 1. Cascata dei confronti (`pricing/ricalcolo.py`: `_cascata`, `serve_cascata`, `mediana_attivi`)
Dopo la ricerca di sempre (zona di casa → ripiego Nord sotto 5 confronti → tutta Italia per la fascia alta), **solo**
per le auto ancora `dati_insufficienti` **perché il mercato non si è potuto stimare** (pochi confronti o confronti
troppo dispersi: valutazione senza M) e sotto il minimo del sito:
1. `vendute`: stesse auto **sparite** dagli annunci negli ultimi 45 giorni (`CASCATA_VENDUTE_GIORNI`), zona di casa +
   Nord, ultimo prezzo chiesto; doppioni, truffe e prezzi finti esclusi; un'auto sparita con gli stessi dati di una
   già contata (anche ripubblicata sullo stesso sito) si conta una volta. **Solo se prima c'era almeno un confronto
   attivo**: le sparite da sole non bastano;
2. `nord+vendute`: annunci attivi del resto del Nord (anche quando in zona ce n'erano 5–7, prima nessun ripiego);
3. `italia+vendute`: annunci attivi di tutta Italia della base prezzi (tutti i prezzi, al massimo 4.000 righe).

**Non gonfiare i prezzi** (correzione dopo la revisione del giudice): il mercato M di un passo non può superare la
**mediana dei prezzi corretti dei confronti ATTIVI trovati prima della cascata** (se non ce n'erano: quella degli
attivi del passo; poi anche l'M di un passo già accettato): si tiene sempre il valore più basso, altrimenti il passo
non si usa. Prova del giudice (Jimny a 3.500 €, 1 attivo a 4.400 €, 8 sparite a 6.000 €): prima M 5.985 e
"opportunità", ora passo rifiutato e auto ancora "dati insufficienti" (test). Le sparite contano con l'ultimo prezzo
chiesto e passano dalla stessa prudenza dei prezzi chiesti: correzione anno/km solo verso il basso, nucleo dei
dispersi, mediana mai sopra quella di tutte le gemelle; la prudenza sui prezzi chiesti delle sparite è data dal tetto: M non supera mai la mediana dei confronti attivi trovati prima della cascata
(`M_fonte="annunci"`). Nessuna correzione per area (non ce n'è una misurata nel codice).

**Nessuna protezione aggirata**: la cascata non parte per le auto ferme con un M già stimato (prezzo sotto il 55% o
il 20% del mercato, costi o danni non stimati), per quelle con danni, prezzo dell'annuncio non affidabile o dati
mancanti, né per le auto già valutate (opportunità, candidato, non conveniente), che restano **identiche** (test con i
casi reali del DUMP_CANDIDATI del 16/11). Ogni valutazione lo dichiara: `fonte_confronti` (anche nel riassunto),
`cascata` (passi, tetto ed esito), `fuori_zona`, un'ipotesi in chiaro, i confronti venduti segnati `venduta`.
Interruttore `CASCATA_CONFRONTI=0`. In `policy_v2.pulisci` un'auto sparita entra solo se segnata dalla cascata.
Indice per le sparite nella migrazione 059.

### 2. Recupero (`pricing/recupero.py`, chiamato alla fine di `ricalcolo.esegui`)
Ogni ricalcolo del cron rivaluta al massimo `RECUPERO_PER_GIRO` (400) privati online la cui ultima valutazione (ultimi
10 giorni) è in attesa del giudice oppure `dati_insufficienti` senza mercato (pochi confronti / dispersi) e non ancora
passata dalla cascata. Ordine: valutazione **più vecchia** prima; ogni auto tentata si segna in `recupero_provato`
(migrazione **059**) e non si riprova prima di 24 ore (`RECUPERO_ORE`), così i giri successivi prendono le altre.
Budget AI: contano **tutte** le chiamate al giudice del giro (anche le auto ripescate che diventano candidato o
opportunità), tetto `RECUPERO_GIUDICE_MAX` (default `GIUDICE_ATTESA_PER_GIRO`, 150); il giro si ferma al tetto o a
budget comune finito. Un solo recupero alla volta: `pg_try_advisory_lock(734120)`.
**Aggancio per `riprova_giudice`** (app.py, non toccato): prendere lo stesso lucchetto prima del giro
(`pg_try_advisory_lock(734120)`, saltare se occupato, `pg_advisory_unlock` alla fine) per non chiamare il giudice due
volte sulla stessa auto. Spegnimento: `RECUPERO_CONFRONTI=0`. A mano: `python -m dealfinder.pricing.recupero`.

### 3. Worker con la configurazione del sito (`render-worker.yaml`) e AI mancante
- Segreti (chiave AI, sale delle targhe): `fromGroup: dealfinder-secrets` (un `fromService` verso una variabile di
  gruppo non si risolve, commit 1ce20d2). Il gruppo porta anche `SCRAPER_PROXY`, `BRIGHTDATA_*`, `PLATE_LOOKUP_*`:
  nel worker sono messe a valore **vuoto** a livello di servizio (vince sul gruppo), quindi nessuna spesa nuova.
- Regole e interruttori: `fromService` dal servizio web `scovo` per le 126 variabili impostate sul sito (riga
  `CONFIG_IMPRONTA` del 16/11: LETTORE, TR_SUBITO_SENZA_NUMERO=1, SOGLIE_PV, POLICY_*, ZONA_SITO, AI_BUDGET_GIORNO, ...).
  Nessuna è nel gruppo secondo `render.yaml`. Escluso **`TR_SUBITO`** (sul sito `0`; nel worker è la lettura completa
  di Subito, copiarla toglierebbe annunci). **Da decidere col cliente.**
- **Verifica col piano del Blueprint NON fatta**: il Blueprint del worker (servizio `scovo-tempo-reale-skdf`) non ha un
  id rintracciabile né dal repo né da Render MCP (`list_services`/`get_service` non lo riportano), e `plan_blueprint`
  pianifica il ramo configurato (main), non `fond-auto`. Anche l'elenco delle variabili di `scovo` non è leggibile da
  MCP: l'esistenza è dedotta dalla riga CONFIG_IMPRONTA del sito (valori impostati, non `<default>`). Prima del sync:
  aprire il piano nel pannello Render (solo piano) e controllare che non ci siano errori di riferimento.
- Nel codice (`ricalcolo._giudice_ai`): senza chiave AI vale un giudizio già archiviato; se non c'è, la valutazione
  **non si pubblica**: resta "in attesa" (`giudice_mancante`, `giudice_senza_ai`) e la riprendono `riprova_giudice` e
  il recupero. Acceso di default sui servizi Render (variabile `RENDER`), `GIUDICE_SENZA_AI_ATTESA=1|0` per forzarlo.

**Ordine di rilascio (obbligatorio)**
1. Merge di `render-worker.yaml` e sync del Blueprint del worker (piano senza errori), poi controllo nel log del
   worker: riga `CONFIG_IMPRONTA` con le variabili del sito e `GIUDICE_AI`/`VALUTAZIONE` con il giudice che lavora
   (la chiave AI c'è). In questa fase il codice è ancora quello di main.
2. Solo dopo, il codice di questo ramo (che con `RENDER` impostato e senza chiave AI mette in attesa). Se il codice
   arrivasse prima del sync, il worker metterebbe in attesa tutte le valutazioni da giudicare (nessuna auto non
   controllata sul sito, ma meno auto nuove finché `riprova_giudice` non le riprende). In alternativa, per rilasciare
   insieme: `GIUDICE_SENZA_AI_ATTESA=0` sul worker fino al sync.
- **Aggancio per `tempo_reale`** (non toccato): nessuno obbligatorio. Facoltativo: contare
  `es.valutazioni[lid].get("giudice_senza_ai")` in `stats["attesa_senza_ai"]` dopo `valuta_e_scrivi`.

### 4. Bug `_soglie_pv` (`web/scovo.py`)
Su Render `SOGLIE_PV` finisce con `1000000000:5000` e il codice aggiungeva di nuovo la fascia "oltre": tabella con
l'ultima fascia doppia → impronta regole del sito `c1380592c34a` contro `f23abb1d281a` del cron col default → la guardia
delle scritture vedeva `regole_cambiate`. Ora la fascia si aggiunge solo se manca; una virgola finale o un pezzo storto fanno tornare al default, come prima. Soglie effettive identiche (test su
tutti i prezzi 0–80.000 €), impronta del sito = impronta del cron = `f23abb1d281a`.

### 5. Doppioni Subito/AutoScout24 sul sito (`scovo.togli_doppioni`, passo `doppioni` in `sito_regole.elenco_sito`)
Dopo tutte le regole e prima dei tetti: siti diversi, stessa marca e modello, stesso anno, km ±3%, prezzo ±7%, stessa
**provincia**, stessa alimentazione e cambio (se noti da entrambe le parti), potenza ±5% (se nota) → una sola auto (prima quella col numero, poi più foto, poi margine, poi id più basso). `SITO_DOPPIONI=0` spegne.
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
- Ipotesi A: la cascata porta al minimo il 25–45% delle "solo N confronti" con almeno un confronto attivo (un
  annuncio resta online ~30 giorni: in 45 giorni le sparite sono circa quante le attive; il Nord per i 5–7 confronti
  sotto 10.000 €); il tetto sulla mediana degli attivi ne scarta una parte; i "dispersi" migliorano poco; le "45%
  sotto" non si toccano più (protezione anti-truffa).
- Ipotesi B: dopo la valutazione passa il 6–10% (oggi 406 su 6.760 = 6%); il tetto prudente abbassa il margine.
- Risultato: **+5/+15 auto** dalla cascata; le attese del giudice (fino a ~225 in più il 13/11) tornano valutate entro
  qualche giro; **−9** doppioni (erano la stessa auto due volte). Netto atteso: **da 0 a +10 auto** subito, più le
  attese recuperate, tutte controllate dal giudice (niente valutazioni non controllate dal worker).

## Verifiche nei log dopo il deploy (a cura di chi fa il merge)
- `CONFIG_IMPRONTA`: `policy.impronta` = `f23abb1d281a` su web, cron e worker; `POLICY_RIEPILOGO.impronta_regole` uguale.
- `DIAG_DATI_INSUFF` / funnel `valutata.dati_insufficienti`: verso ≤ ~175.
- `VALUTAZIONE ... scrittura:meno_confronti` in calo; nuovi contatori `cascata:vendute`, `cascata:nord+vendute`,
  `cascata:italia+vendute`, `cascata:nessun_aiuto`.
- `RECUPERO_CONFRONTI {...}` a fine ricalcolo: `cascata_usata`, `sul_sito_possibili`, `chiamate_giudice` ≤ tetto, auto diverse a ogni giro.
- `SITO_FUNNEL`: passo `doppioni` ~9 tolte al primo giro, poi 0 doppioni sul sito.
- Worker: righe `GIUDICE_AI`/`VALUTAZIONE` con il giudice attivo; nessuna `giudice_senza_ai` dopo il sync.

## Rischi
- Sparite = ultimo prezzo chiesto (non il prezzo di vendita): stesso tipo di dato delle attive, calibrato una volta.
- Più query per le sole auto in cascata (al massimo 3 per gruppo, in cache per gruppo).
- `fromService` richiede che le variabili esistano sul servizio `scovo`: se una viene tolta dal sito, il sync del
  Blueprint del worker va aggiornato. `ANTHROPIC_API_KEY` e `PLATE_SALT` sul sito arrivano anche dal gruppo
  `dealfinder-secrets`: verificare nel piano del Blueprint che il riferimento si risolva (in alternativa
  `fromGroup: dealfinder-secrets`, che però porterebbe anche le chiavi Bright Data al worker).
