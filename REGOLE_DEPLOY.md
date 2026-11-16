# Regole per mettere online (Skovo)

Scritte il 16/11/2026 dopo due settimane con 234 commit, 55 correzioni di correzioni e circa 100 deploy del sito in
8 giorni. Lo scopo è uno solo: **ogni messa online deve essere decisa, controllata e reversibile**.

## 1. Quando

- **Due finestre al giorno: 13:00 e 20:00 (ora di Roma).** Tutto quello che è pronto si mette online insieme in una
  delle due finestre, non un commit alla volta.
- Fuori finestra solo per un guasto in corso (sito vuoto, sito giù, dati sbagliati davanti ai clienti). In quel caso
  il commit dice `URGENTE:` e il motivo.
- Mai durante un incontro con il cliente, mai tra le 7 e le 9 (riepilogo del mattino e giri dei cron).
- I commit che non devono partire subito portano `[skip render]` nel messaggio (come si fa già).

## 2. Prima di mettere online (obbligatorio)

```
./prima_del_deploy.sh
```

Fa, nell'ordine, e si ferma al primo errore:

0. **impronta delle regole di business** (`python run_tests.py --impronta`): soglie, margini, tetti, zona, km,
   telefono, trattativa calcolati con i soli default del codice devono essere uguali a `regole_impronta.json`;
1. compileall di `dealfinder`;
2. import dei moduli principali senza database;
3. tutti i test con i default del codice;
4. tutti i test con le regole di produzione (`RIVENDITA_VERA=1`, `BENZ_RICHIESTE=1`; rossi noti in
   `tests/rosso_noto.txt`).

Gli stessi passi girano su GitHub a ogni push e pull request (`.github/workflows/controllo.yml`, "controllo").
**Un ramo con il controllo rosso non va su main.**

### Cambiare una regola di business

Le regole del cliente non cambiano per sbaglio. Se il cliente decide un cambio:

1. si cambia il codice (`dealfinder/regole.py`, `web/scovo.py`, `web/sito_regole.py`, ...);
2. `python run_tests.py --impronta-aggiorna` riscrive `regole_impronta.json`;
3. se servono, si rigenerano gli attesi di `tests/test_non_regressione_dump.py` (casi veri dei log);
4. **commit a parte** con messaggio che comincia con `REGOLE:` e dice chi ha deciso cosa (es.
   `REGOLE: soglia 5-7.000 € da 1.500 a 1.400 (cliente, 18/11)`).

Il controllo su GitHub rifiuta ogni commit che cambia `regole_impronta.json` senza `REGOLE:` nel messaggio.

## 3. Niente lavori pesanti nel sito

Il servizio web (`scovo`, piano starter) serve le pagine e basta. Ogni lavoro pesante lì dentro ha già causato
riavvii per memoria e sito vuoto. **Non vanno nel web**:

- raccolta e rilettura degli annunci (stanno nel worker `tempo_reale_worker` e nei cron);
- addestramento di modelli (`ai_prezzi`, `valore_reale`, `calibra`), ricalcoli completi, backtest;
- analisi AI in blocco, controllo foto in blocco, esportazioni grandi;
- qualunque ciclo su tutti gli annunci del database.

Nel web restano solo: calcolo dell'elenco (già fatto), thread leggero della sentinella (una volta all'ora), canarino
(poche query e due richieste HTTP), coda delle email. Un lavoro nuovo va in un cron esistente o nel worker, mai in un
servizio nuovo a pagamento senza decisione esplicita.

## 4. Dopo la messa online (entro 15 minuti)

1. `python -m dealfinder.canarino` (o la riga `CANARINO` nei log del sito): deve dire `CANARINO ok`.
2. Nei log del sito: `CONFIG_IMPRONTA` con il commit nuovo, nessun `Traceback`, riga `SITO_FUNNEL`.
3. Numero di auto sul sito simile a prima (riga `SENTINELLA`, controllo `sito_conteggio`).

Se una di queste non va: **si torna indietro subito** (sezione 5), poi si capisce con calma.

## 5. Come si torna indietro

In ordine di velocità:

1. **Render, pannello del servizio → Events → deploy precedente → "Rollback"** (un minuto; non tocca il codice su
   GitHub). Attenzione: con l'auto-deploy acceso il prossimo push rimette online la versione nuova; quindi subito
   dopo il punto 2.
2. **`git revert <commit>`** sul ramo main e push: il deploy successivo è la versione di prima, con la storia
   intatta. Mai `git push --force` su main.
3. Interruttori già previsti nel codice (senza deploy di codice, cambiando una variabile su Render: va comunque
   deciso e scritto nel registro): `SENTINELLA=0`, `CANARINO=0`, `RIEPILOGO_GIORNO=0`, `TR_WORKER=0/1`, ...
   Le **regole di business non hanno interruttori** (sono in `regole.py`): si cambiano solo con un commit `REGOLE:`.

Le migrazioni del database sono solo aggiunte (`CREATE TABLE IF NOT EXISTS`, colonne nuove): tornare indietro col
codice non richiede di toccare il database.

## 6. Sorveglianza

- **Sentinella** (`dealfinder/sentinella.py`): regole del cliente e salute dei lavori, una volta all'ora, email solo
  per allarmi nuovi.
- **Canarino** (`dealfinder/canarino.py`): una volta all'ora risponde a "un'auto nuova arriva sul sito con prezzo
  giusto e foto?" (worker vivo, giro `nuovi` recente, almeno 100 auto, annuncio recente attivo con lo stesso prezzo,
  foto 200). Esito nei log (`CANARINO ok` / `CANARINO ko: motivi`) e nella tabella `canarino_esiti`.
- **Riepilogo giornaliero** (`dealfinder/riepilogo_giorno.py`): una email al giorno dopo le 8 con auto sul sito,
  entrate/uscite, imbuto, versioni del codice (deploy), esiti del canarino, giri dei cron, allarmi.
  `python -m dealfinder.riepilogo_giorno` stampa il testo senza mandarlo.

### Agganci da fare (file di altri gruppi, non toccati in questo ramo)

In `dealfinder/sentinella.py`, funzione `_ciclo_web`, dentro il `try` del ciclo:

```python
            if time.monotonic() - ultimo >= ogni_min * 60 or not ultimo:
                salva_impronta("web")
                esegui("web")
                from . import canarino            # una volta all'ora, con l'elenco già in memoria
                canarino.da_sito()
                ultimo = time.monotonic()
            else:
                solo_coda()
            from . import riepilogo_giorno        # ogni 10 minuti: parte al massimo una volta al giorno, dopo le 8
            riepilogo_giorno.esegui()
```

Entrambe le funzioni non sollevano mai eccezioni. Il riepilogo usa lo stesso canale delle email della sentinella
(`sentinella._invia_brevo`, Brevo transazionale verso info@skovo.app).

Facoltativo (impostazione di Render, non fatta): "Auto-Deploy" del servizio web su "After CI checks pass", così
Render non mette online un commit con il controllo di GitHub rosso.
