# Modulo Incentivi — prototipo

Il commerciante inserisce **solo la partita IVA**. Il modulo:
1. recupera i dati ufficiali dell'impresa e la visura camerale originale;
2. fa al massimo 4 domande;
3. controlla i requisiti di ogni misura con regole esplicite;
4. mostra cosa conviene, cosa resta da valutare e cosa non fa per lui (con il motivo);
5. prepara un PDF e un link sicuro per il commercialista.

Indirizzo: `https://<sito>/incentivi/`. Il modulo è un'applicazione Starlette a sé (`incentivi/web.py`), montata dentro il sito Scovo.

## Come funziona

| Pezzo | File | Note |
|---|---|---|
| Catalogo misure | `data/misure.json` | **35 misure**. Generato da `data/ricerca/*.json` con `python -m incentivi.strumenti.costruisci_catalogo`. Ogni misura ha: fonte, date, stato, beneficio, documenti, dubbi, regole. |
| Schede di ricerca | `data/ricerca/*.json` | Ricerca web dell'11/10/2026. Ogni scheda ha il campo `dubbi` con quello che non è confermato. |
| Regole | `strumenti/costruisci_catalogo.py` → `regole` di ogni misura | Le regole sono dichiarative (tipo, parametri, testo, riferimento al bando). Le correzioni manuali sono in `CORREZIONI`. |
| Motore | `motore.py` | Deterministico, senza AI. Ogni regola dà ✅ / ❌ / ❓ e registra il dato usato e la sua fonte. |
| Visura | `visura.py` | Usa le API Openapi Company `IT-full` (dati strutturati) e Visure Camerali (PDF originale, flusso asincrono). La documentazione letta è in `data/ricerca/openapi.md`. |
| Archivio | `store.py`, `db/migrations/016_incentivi.sql` | Postgres. I documenti sono cifrati con Fernet; per ogni file si salva l'impronta SHA-256. |
| PDF commercialista | `pdf.py` | Generato con reportlab. |
| Aggiornamento e avvisi | `job.py` | Gira una volta al giorno: nel ciclo notturno e alla prima visita del giorno. |
| Interfaccia | `static/` | HTML, CSS e JS senza framework, pensata per il telefono. |
| Test | `tests/test_incentivi.py` | Coprono i test di accettazione del prompt. Si lanciano con `python3 run_tests.py`. |

Etichette delle misure:
- **🟢 Conviene**: tutte le regole sono ✅ e il bando è aperto.
- **🟡 Da valutare**: c'è almeno un ❓, oppure il bando apre a breve o è ricorrente, oppure è un prestito o una garanzia.
- **⚪ Non fa per te**: c'è almeno un ❌; il motivo mostrato è il primo ❌.

I bandi chiusi o esauriti non risultano mai disponibili.

Ogni valutazione salvata registra la **versione del catalogo** usata (`inc_valutazioni.versione_catalogo`).

## Variabili d'ambiente

Nessuna chiave sta nel codice.

| Variabile | Uso |
|---|---|
| `OPENAPI_TOKEN` | Token Bearer creato nella console Openapi con gli scope di `company.openapi.com` (IT-full) e di `visurecamerali.openapi.it` (impresa, ordinaria-*). Se manca, si usano i 3 profili di esempio. |
| `OPENAPI_AMBIENTE` | `sandbox` (predefinito) oppure `produzione`. |
| `INCENTIVI_CHIAVE` | Facoltativa: chiave da cui si deriva la cifratura dei documenti. Se manca si usa `SECRET_KEY`, da cui si deriva una chiave dedicata con HKDF. |
| `DATABASE_URL` | La stessa del sito. |

Per collegare Openapi:
1. Su console.openapi.com crea il token per la sandbox, con l'API key sandbox.
2. Su Render, nel servizio web, aggiungi `OPENAPI_TOKEN`.

In sandbox Company risponde solo per un elenco fisso di aziende di prova; non è documentato se le visure di prova restituiscano PDF reali.

## Limiti noti (da sistemare prima del lancio)

- **Dati del catalogo da ricontrollare.** Molte schede si basano anche su fonti secondarie: 9 misure su 35 hanno come fonte principale un sito non ufficiale, e sono segnate nell'app. La pagina "Aggiornamenti" elenca le misure aperte che hanno dubbi; ogni scheda li mostra in "Punti ancora da verificare".
- **Fonti non lette in automatico.** Il job ricalcola gli stati dalle date e prepara gli avvisi, ma il controllo di incentivi.gov.it, Bandi e Servizi, INPS, INAIL, GSE e Camere di Commercio è **simulato** e il log lo dice. Ogni nuova misura richiede la revisione umana.
- **Visura.** I dati vengono dal servizio strutturato Openapi Company. Il PDF della visura è conservato come originale ma non viene letto riga per riga. Da verificare con un account Openapi:
  - il formato esatto delle risposte;
  - il payload della callback;
  - i nomi dei campi `nome/dimensione` o `name/size`.
- **ATECO 2025.** La corrispondenza 45.11 → 47.81.10 nei profili di esempio è indicativa, da verificare sulle tabelle ISTAT. Le regole usano i codici ATECO 2007, come i bandi.
- **Dimensione d'impresa.** È presunta dagli addetti: il fatturato non viene letto.
- **Accesso.** Un'impresa è legata al browser tramite cookie: nel prototipo non c'è un account. Prima del lancio servono un login (quello di Scovo) e un'informativa privacy dedicata.
- **Email, push e WhatsApp sono simulati.** Gli avvisi si vedono in "Aggiornamenti". Il limite è di 2 avvisi a settimana, tranne le urgenze.
- **Nessun invio di domande.** Il software prepara, ricorda e controlla. Le domande le invia il legale rappresentante (SPID/CNS/firma digitale) o il professionista con delega.
