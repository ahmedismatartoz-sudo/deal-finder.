# Openapi (openapi.com / openapi.it): Visure Camerali + Company (ricerca dell'11/10/2026)

Legenda: **[CONFERMATO]** = scritto nella fonte citata. **[NON DOCUMENTATO]** = non trovato nelle pagine pubbliche lette.
Nota sul metodo: curl diretto verso *.openapi.com/*.openapi.it è bloccato dal proxy di questo ambiente. Le pagine sono state lette con WebFetch, che riassume e cita solo frammenti brevi, quindi alcuni JSON sono ricostruiti campo per campo e non copiati riga per riga (lo indico ogni volta). Non ho potuto aprire il file OAS di visurecamerali, la pagina docs.openapi.it degli esempi sandbox, developers.openapi.it e github.com/openapi.

Fonti principali:
- OAuth: https://console.openapi.com/apis/oauth/documentation
- Company: https://console.openapi.com/apis/company/documentation
- OAS Company: https://console.openapi.com/oas/en/company.openapi.json
- Visure Camerali: https://console.openapi.com/apis/visure-camerali/documentation
- FAQ Visure: https://console.openapi.com/apis/visure-camerali/faq
- Prodotti (prezzi): https://openapi.com/products/italian-capital-company-registration-report, https://openapi.com/products/start-data-italian-company, https://openapi.com/products/advanced-data-italian-company, https://openapi.com/products/italian-full-company
- SDK PHP (minimale): https://root.packagist.org/packages/openapi/openapi-sdk → https://github.com/openapi/openapi-php-sdk
- Blog sandbox: https://openapi.com/blog/how-test-openapi-api

---

## 0. Autenticazione (comune a entrambi i servizi) [CONFERMATO, fonte: OAuth documentation]

- Host OAuth: produzione `https://oauth.openapi.it`, sandbox `https://test.oauth.openapi.it`
- Il token si crea con `POST /token` e **Basic Auth** (`email:APIkey`). L'API key è nella console (https://console.openapi.com/oauth). Per la sandbox serve la **"APIKey Sandbox"**, distinta da quella di produzione (fonte: blog how-test-openapi-api).
- Body JSON:
  - `scopes` (array di stringhe, obbligatorio)
  - `ttl` (int, secondi, massimo 1 anno; se manca vale 1 anno)
  - `expire` (deprecato)
- Esempio di richiesta (dalla documentazione):
```json
{
  "scopes": ["GET:comparabili.openapi.it/tassonomie", "GET:imprese.openapi.it/*", "*:*.openapi.it/*"],
  "expire": 0,
  "ttl": 2592000
}
```
- Esempio di risposta 200:
```json
{
  "scopes": ["POST:valutometro.altravia.com/valutazione"],
  "expire": 1634223407,
  "token": "5f8711afe4754a532a7a8358",
  "success": true,
  "message": "",
  "error": null
}
```
- Formato degli scope: `METHOD:dominio/endpoint`. Si può usare `*` per metodo, dominio o endpoint (es. `*:*.openapi.it/*`).
- Altri endpoint: `GET /token`, `GET|PUT|PATCH|DELETE /token/{token}`, `GET /scopes`, `GET /credit`, `GET /counters/total`, `GET /counters/{day|month|year}/{value}`.
- La pagina avvisa che OAuth v1 è stato sostituito dagli endpoint di OAuth v2 ed è deprecato dal **31/12/2027** (la reference è indicata come "Oauth (1.0.0)", la "Web Service Revision" come 2.0.0). Il percorso esatto di OAuth v2 non l'ho trovato scritto: **[NON DOCUMENTATO nelle pagine lette]**.
- Le chiamate ai servizi usano l'header `Authorization: Bearer <token>`.
- Esempio dell'SDK PHP: `new OauthClient('username','apikey', true /*test*/)` e `createToken($scopes, 3600)`. Nel README c'è anche una chiamata a `https://test.company.openapi.com/IT-advanced` con i parametri query `denominazione` e `provincia`. Uso dubbio: la documentazione di Company indica solo il path param.

Scope che servono (per come li elenca la console):
- Visure: `POST:visurecamerali.openapi.it/ordinaria-societa-capitale`, `GET:visurecamerali.openapi.it/ordinaria-societa-capitale` (più gli equivalenti `-persone`, `-impresa-individuale` e `GET .../impresa`). In sandbox l'host è `test.visurecamerali.openapi.it`.
- Company: `GET:company.openapi.com/IT-start`, `GET:company.openapi.com/IT-advanced`, `GET|POST:company.openapi.com/IT-full`, `GET:company.openapi.com/IT-check_id`. In sandbox l'host è `test.company.openapi.com`.
- La console mostra gli scope come "METHOD host/path". Il formato con i due punti `METHOD:host/path` è quello della documentazione OAuth. Non è scritto se il wildcard sul path (`/*`) copra anche i sotto-path tipo `/{id}/allegati`: **[NON DOCUMENTATO]**.

Rate limit: 10.000 req/min per entrambi i servizi [CONFERMATO].

---

## A. Visure Camerali (PDF ufficiale)

### Host [CONFERMATO]
- Produzione: `https://visurecamerali.openapi.it`
- Sandbox: `https://test.visurecamerali.openapi.it`

### Endpoint [CONFERMATO, documentation]
Ricerca:
- `GET /impresa`: query `denominazione`, `provincia`, `codice_ateco`, `fatturato_min/max`, `dipendenti_min/max`, `skip`, `limit` (1–1000), `lat`, `lng`, `radius`.
- `GET /impresa/{cf_piva_id}`: compare nella pagina prodotto e nelle FAQ ("free of charge, with no limits") con l'esempio `https://visurecamerali.openapi.it/impresa/12485671007`. **Non compare** nella pagina documentation della console.

Tipi di visura (ognuno ha 4 operazioni):
| Tipo | Path |
|---|---|
| Ordinaria società di capitali | `/ordinaria-societa-capitale` |
| Storica società di capitali | `/storica-societa-capitale` |
| Ordinaria società di persone | `/ordinaria-societa-persone` |
| Storica società di persone | `/storica-societa-persone` |
| Ordinaria impresa individuale | `/ordinaria-impresa-individuale` |
| Storica impresa individuale | `/storica-impresa-individuale` |

Le 4 operazioni:
- `POST /{tipo}`: crea la richiesta
- `GET /{tipo}`: elenca le richieste
- `GET /{tipo}/{id}`: stato della richiesta (`id` = id della richiesta, non la P.IVA)
- `GET /{tipo}/{id}/allegati`: download (la pagina prodotto scrive `/attachments`; documentation e FAQ scrivono `/allegati`)

Altri servizi: `/bilancio-ottico`, `/certificato-iscrizione`, `/certificato-iscrizione-vigenza`, `/soci-attivi` (GET e POST).

**Come scegliere il tipo:** il risultato di `/impresa` contiene `codice_natura_giuridica` e `chiamate_disponibili`, cioè la lista degli endpoint di visura disponibili per quell'impresa. Quindi prima si cerca e poi si sceglie il tipo [CONFERMATO dall'esempio].

### Body del POST [CONFERMATO]
```json
{"cf_piva_id": "mssrrt77b18z112l"}
```
```json
{"cf_piva_id": "12485671007", "callback": {"url": "https://myserver.com", "method": "JSON", "data": {"myData": "myValue"}}}
```
`callback.data` viene restituito così com'è. Il formato del payload che arriva alla callback **[NON DOCUMENTATO]**.

### Flusso asincrono [CONFERMATO]
1. `POST /ordinaria-societa-capitale` con `cf_piva_id`. Risponde con `id` e `stato_richiesta` ("In erogazione" / "In elaborazione").
2. Attendere la callback (consigliata). Il polling di `GET /{tipo}/{id}` è possibile, ma la documentazione lo sconsiglia.
3. Quando lo stato è **"Visura evasa"**, si scarica con `GET /{tipo}/{id}/allegati`.
4. La risposta contiene `file` = **zip codificato in base64** che contiene il PDF.

Stati citati: "In erogazione", "In elaborazione", "Dati disponibili" (o "Dati Disponibili"), "Visura evasa". FAQ: "evasa=fulfilled, in erogazione=in delivery, dati disponibili=data available". Non è documentato quale stato renda disponibile il download, a parte "evasa". La pagina prodotto parla di "Completed", in contraddizione con gli esempi.
Tempi: "In a few seconds" / "within seconds to a few minutes" (pagina prodotto).

### Esempi JSON (pagina prodotto, copiati riga per riga; email offuscata dal sito)
Ricerca `GET /impresa/12485671007`:
```json
{
  "data": [{
    "id": "61f2d9978e5bb376ab1c85d6",
    "denominazione": "OPENAPI SPA",
    "comune": "ROME",
    "codice_natura_giuridica": "SP",
    "chiamate_disponibili": [
      "visurecamerali.openapi.it/ordinaria-societa-capitale",
      "visurecamerali.openapi.it/storica-societa-capitale",
      "visurecamerali.openapi.it/bilancio-ottico",
      "visurecamerali.openapi.it/certificato-iscrizione",
      "visurecamerali.openapi.it/certificato-iscrizione-vigenza",
      "visurecamerali.openapi.it/soci-attivi"
    ]
  }],
  "success": true, "message": "", "error": null
}
```
POST (creazione):
```json
{
  "data": {
    "cf_piva_id": "12485671007",
    "tipo": "ordinaria-societa-capitale",
    "stato_richiesta": "In elaborazione",
    "timestamp_creation": 1649683350,
    "timestamp_last_update": 1649683350,
    "allegati": [],
    "callback": false,
    "owner": "<email>",
    "id": "625fec9313ddfc09f11c47e2"
  },
  "message": "", "success": true, "error": null
}
```
GET /{tipo}/{id}: stessa struttura, con `"stato_richiesta": "Dati disponibili"`. Nella documentation, con "Visura evasa", `allegati` contiene il nome di un file PDF.
Download:
```json
{
  "data": { "name": "625fec93fb8ca84347057795.zip", "size": 586660, "file": "<base64 zip>" },
  "success": true, "message": "", "error": null
}
```
**Attenzione:** la pagina prodotto usa `name`/`size`, la documentation della console usa `nome`/`dimensione`. Meglio gestire entrambe le coppie.
Lista `GET /{tipo}`: array di `{cf_piva_id, tipo, stato_richiesta, timestamp_last_update, owner, id}`.

### Prezzi [CONFERMATO, pagina prodotto Società di Capitali, IVA esclusa]
- `POST /ordinaria-societa-capitale`: **€4,90 + IVA** a chiamata (pay-per-use)
- `POST /storica-societa-capitale`: €5,90 + IVA
- Abbonamenti annuali: 100 chiamate/anno a €4,68 (e €5,85); 1000 chiamate/anno a €3,96 (e €4,95). Le coppie non hanno etichetta: probabilmente la prima è l'ordinaria e la seconda la storica.
- FAQ: "from €2.90" a richiesta (non è chiaro per quale tipo). Ricerca `/impresa` gratuita.
- Prezzi delle visure per società di persone e imprese individuali: ci sono le pagine prodotto (https://openapi.com/products/italian-partnership-registration-report, https://openapi.com/products/italian-sole-proprietorship-registration-report), ma **non le ho lette**.

### Sandbox
- [CONFERMATO] "experiment with all kinds of requests completely free of charge" su test.visurecamerali.openapi.it.
- Se la sandbox restituisce un PDF finto o fisso, o dati dummy: **[NON DOCUMENTATO]**.

---

## B. Company (dati strutturati)

### Host [CONFERMATO, OAS + documentation]
- Produzione: `https://company.openapi.com`
- Sandbox: `https://test.company.openapi.com`

### Endpoint [CONFERMATO]
- `GET /IT-start/{vatCode_or_taxCode}`: secondo la pagina prodotto accetta anche l'id interno
- `GET /IT-advanced/{vatCode_or_taxCode}`
- `GET /IT-full/{vatCode_or_taxCode}`: sincrono. Se la richiesta supera i 30 s di timeout, restituisce un id da recuperare con `GET /IT-check_id/{id}`.
- `POST /IT-full/{vatCode_or_taxCode}`: asincrono. Restituisce id e stato (es. "PENDING"). Nel payload si può mettere una callback, ma **il body esatto non è documentato** nelle pagine lette.
- Esempio: `curl -X GET "https://company.openapi.com/IT-aml/12485671007" -H "Authorization: Bearer REPLACE_BEARER_TOKEN"`
- Path param: `vatCode_or_taxCode` (es. `12485671007`). Per start/advanced/full non risultano altri parametri query documentati.
- Envelope: `{ "data": ..., "success": bool, "message": string, "error": int|null }`. Codici: 200, 204, 400, 402 (credito), 406, 503.
- Altri endpoint utili: `/IT-search` (filtri ATECO, provincia, dipendenti, stato, `dryRun` gratuito...), `/IT-closed`, `/IT-legalforms`, `/IT-pec`, `/IT-shareholders`, `/IT-marketing`.

### Campi (da OAS, schemi `Start` e `Advanced`) [CONFERMATO]
**IT-start** → `data`:
`taxCode`, `companyName`, `vatCode`, `address.registeredOffice{toponym, street, streetNumber, streetName, town, hamlet, province, zipCode, townCode, region{code,description}, gps{coordinates[2]}}`, `activityStatus` (enum: ATTIVA, REGISTRATA, INATTIVA, SOSPESA, IN_ISCRIZIONE, CESSATA), `creationTimestamp`, `lastUpdateTimestamp`, `sdiCode`, `sdiCodeTimestamp`, `id`, `registrationDate`.
→ Mancano ATECO, forma giuridica e dipendenti.

**IT-advanced** → `data` (secondo la pagina prodotto `data` è un **array** con un solo record; da verificare):
quanto c'è in start, più `reaCode`, `cciaa`, `atecoClassification{ateco{code,description} (ATECO 2025), ateco2022{...}, ateco2007{...}}`, `detailedLegalForm{code,description}`, `startDate`, `registrationDate`, `endDate`, `pec`, `taxCodeCeased`, `taxCodeCeasedTimestamp`, `vatGroup{vatGroupParticipation,isVatGroupLeader,registryOk}`, `balanceSheets{last{year,balanceSheetDate,turnover,netWorth,employees,shareCapital,totalStaffCost,totalAssets,avgGrossSalary}, all[...]}`, `shareHolders[{companyName,name,surname,taxCode,percentShare}]`.
→ I dipendenti sono solo in `balanceSheets.last.employees`. Non esiste `incorporationDate`: ci sono `startDate` e `registrationDate`.

**IT-full** → `data` con molte sezioni, tra cui `companyDetails`, `legalForm`, `companyStatus`, `companyDates`, `address`, `atecoClassification`, `employees`, `managers`, `shareholders`, `ecofin`, `pec`, `branches`... (elenco completo nella documentation). Estratto dall'esempio della pagina prodotto (P.IVA 12485671007):
```json
"companyDetails": { "vatCode": "12485671007", "taxCode": "12485671007", "lastUpdateDate": "2025-11-26T19:45:34.8168745Z",
  "cciaa": "RM", "reaCode": "1378273", "companyName": "OPENAPI SPA",
  "officeType": {"code": "SSL", "description": "Administrative headquarter and registered office"}, "openapiNumber": "IT93E20F0DS0001" },
"legalForm": { "legalForm": {"code": "SC", "description": "Joint stock business"},
               "detailedLegalForm": {"code": "SP", "description": "Limited company"} },
"companyStatus": { "activityStatus": {"code": "A", "description": "Enable"} },
"companyDates": { "registrationDate": "2013-07-19T00:00:00", "startDate": "2013-10-20T00:00:00", "incorporationDate": "2013-07-11T00:00:00" },
"address": { "streetName": "VIALE FILIPPO TOMMASO MARINETTI, 221", "zipCode": "00143", "town": "ROMA",
             "province": {"code": "RM", "description": "ROMA"}, "region": {"code": "12", "description": "LAZIO"},
             "country": {"code": "IT", "description": "Italia"} },
"atecoClassification": { "ateco": {"code": "621", "description": "Computer programming activities"}, "secondaryAteco": "62201",
                         "ateco2022": {"code": "6201", "description": "Production of software not related to editing"}, "secondaryAteco2022": "6202" },
"employees": { "employeeRange": {"code": "ER4", "description": "11 - 20"}, "employee": 19, "employeeTrend": 26.67 }
```
Nota: in IT-full lo stato è un oggetto `{code, description}`, mentre in start/advanced è una stringa enum. La struttura dell'indirizzo è diversa da quella di start/advanced. Secondo l'OAS, `companyDates` contiene anche `foundingDate` e `endDate`. Gli esempi pubblicati per start e advanced **non sono JSON valido** (virgole e due punti mancanti), quindi ci si deve basare sullo schema OAS.

### Prezzi [CONFERMATO, pagine prodotto, IVA esclusa]
- **IT-start**: €0,05 pay-per-use. Abbonamenti da €0,04 a €0,015. Richieste gratuite: 30/mese (altrove nella stessa pagina 10/mese).
- **IT-advanced**: €0,10 pay-per-use. Abbonamenti da €0,08 a €0,028. 30 richieste/mese gratuite.
- **IT-full**: €0,30 (GET o POST) pay-per-use. Abbonamenti annuali da €0,22 a €0,08 (due tabelle senza etichetta).

### Sandbox
- [CONFERMATO] "At this address you will find a list of companies to use in the sandbox and still receive a complete dataset" (link: docs.openapi.it/company-sandbox-examples.html). In pratica la sandbox funziona solo su un **elenco fisso di aziende di test** e per quelle restituisce un dataset completo. È gratuita.
- Quali P.IVA ci siano nell'elenco e se i dati siano reali o finti: **[NON VERIFICATO]**, non sono riuscito ad aprire la pagina. Negli esempi compare ovunque OPENAPI SPA, P.IVA 12485671007.
- Blog: per i dati reali bisogna usare Production e non Sandbox.

---

## Cose da verificare con un account (non documentate pubblicamente o in contraddizione)
1. Payload della callback (visure e IT-full POST) e body esatto di `POST /IT-full`.
2. Nomi dei campi del download visure: `nome/dimensione` oppure `name/size`. Path `/allegati` oppure `/attachments`.
3. Comportamento della sandbox visure: PDF di esempio fisso? Funziona con qualsiasi P.IVA?
4. Elenco delle P.IVA valide nella sandbox di Company.
5. Endpoint OAuth v2 (la v1 è deprecata dal 31/12/2027).
6. Se `IT-advanced` restituisce `data` come oggetto o come array.
