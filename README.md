# Bot Telegram – avvisi Fiera di Rimini (IEG)

Ti avvisa su Telegram quando un evento alla Fiera di Rimini sta per iniziare (default: 4 giorni prima),
così sai in anticipo dove ci può essere traffico. Gira gratis su GitHub Actions, senza server.

## Come funziona
- Ogni 6 ore legge il calendario ufficiale (https://www.iegexpo.it/en/venues/rimini/exhibitions).
- Lo unisce a `eventi_manuali.json` (dati di riserva se il sito cambia o è offline).
- Se un evento inizia entro 4 giorni e non è già stato notificato, invia un messaggio.
  Gli eventi con la stessa data (es. Key + HYPE) arrivano in un solo messaggio.
- **Nuovi eventi**: se nel calendario ufficiale compare un evento che non c'era, ti scrive subito
  ("🆕 Nuovi eventi in calendario"). Se un evento già noto cambia date, ti scrive "📝 Date modificate"
  con le date prima e dopo. Gli eventi già presenti al primo avvio vengono solo registrati, senza avvisi.
  Gli eventi visti sono salvati in `eventi_noti.json`.
- **Eventi annullati**: se un evento futuro sparisce dal calendario ufficiale, il bot ti scrive
  "❌ Eventi rimossi dal calendario (annullati o rinviati)" e smette di mandare l'avviso di inizio.
  Per evitare falsi allarmi l'evento deve mancare per **2 controlli consecutivi** (circa 12 ore) e, se
  sparisce in blocco oltre metà del calendario, il bot lo considera un problema del sito e non segnala nulla.
  Il bot non può sapere se è un annullamento o un rinvio: verifica sul sito dell'evento.
  Se l'evento torna in calendario (anche con nuove date) lo ricevi come "nuovo evento".
- Ricorda cosa ha già inviato in `stato_inviati.json`, quindi niente doppioni
  e, se un giorno GitHub salta l'esecuzione, l'avviso parte il giorno dopo.

## Setup (10 minuti)
1. **Crea il bot**: su Telegram scrivi a @BotFather → `/newbot` → copia il token.
2. **Scegli dove ricevere gli avvisi** (la via più comoda per più persone è un canale):
   - *Canale*: crea un canale, aggiungi il bot come **amministratore**. Chat id = `@nomecanale` (se pubblico).
     Chi vuole gli avvisi si iscrive al canale.
   - *Solo tu*: scrivi `/start` al bot, poi apri
     `https://api.telegram.org/bot<TOKEN>/getUpdates` e copia `message.chat.id`.
   - *Gruppo*: aggiungi il bot al gruppo, scrivi un messaggio, leggi l'id (negativo) da `getUpdates`.
   - Più destinatari: separali con la virgola (`@canale,123456789`).
3. **Crea un repository GitHub** (anche privato) con questi file.
4. In *Settings → Secrets and variables → Actions* aggiungi:
   - `TELEGRAM_TOKEN`
   - `TELEGRAM_CHAT_IDS`
5. Tab *Actions* → "Avviso Fiera Rimini" → *Run workflow* per provare.

## Test in locale
```bash
pip install -r requirements.txt
python bot.py --list                       # mostra gli eventi trovati
python bot.py --dry-run --oggi 2026-10-30  # simula una data, senza inviare
export TELEGRAM_TOKEN=... TELEGRAM_CHAT_IDS=...
python bot.py --test                       # messaggio di prova
```

## Personalizzazione
- **Anticipo**: variabile `DAYS_BEFORE` nel workflow.
- **Impatto traffico**: la lista `RILEVANZA_ALTA` in `bot.py` è una mia stima (eventi grandi/aperti al pubblico),
  non un dato ufficiale: modificala in base alla tua esperienza.
- **Escludere eventi**: aggiungi parole chiave in `"escludi"` in `eventi_manuali.json`.
- **Aggiungere eventi a mano**: nuova riga in `"eventi"`.
- **Frequenza controlli**: modifica `cron` in `.github/workflows/avviso.yml` (è in UTC). Gli avvisi non si duplicano anche se lo esegui più volte al giorno.

## Note
- Il calendario ufficiale elenca solo gli eventi già annunciati (al 16/10/2026 arriva fino ad agosto 2027).
  Le date possono cambiare: lo scraping giornaliero le tiene allineate.
- Lo scraping legge i link "aggiungi a Google Calendar" della pagina IEG. Se IEG ridisegna il sito
  e non trova nulla, nei log compare "ATTENZIONE: scraping fallito" e il bot usa solo il file manuale.
- Il bot vede solo gli eventi che IEG pubblica sulla pagina Rimini: eventi aggiunti solo a mano in
  `eventi_manuali.json` non generano l'avviso "nuovo evento", ma partono regolarmente gli avvisi pre-inizio.
- Gli eventi presenti in `eventi_manuali.json` ma spostati o annullati sul sito vengono ignorati
  automaticamente: non ricevi avvisi con le date vecchie.
- Evento già in corso al momento dell'avvio del bot: non viene segnalato (si avvisa solo agli inizi).
- GitHub sospende i workflow schedulati dopo 60 giorni di inattività del repository; i commit automatici
  di `stato_inviati.json` aiutano, ma se ricevi una mail di sospensione basta riattivarlo da *Actions*.

## Comandi del bot (/prossimi, /incorso, /help)

I comandi funzionano nella **chat privata con il bot** (o in un gruppo dove lo aggiungi), non nel canale:
nei canali gli iscritti non possono scrivere. Chi vuole interrogare il bot gli scrive direttamente.

| Comando | Cosa fa |
|---|---|
| `/prossimi [n]` | prossimi eventi con date, impatto e giorni mancanti (predefinito 5, massimo 15) |
| `/incorso` | eventi in corso oggi (se non ce ne sono, indica il prossimo) |
| `/help` | elenco dei comandi (anche con `/start`) |

I comandi compaiono anche nel menu "/" di Telegram. Per rispondere subito serve un piccolo
**Cloudflare Worker** (gratis, `worker.js`), perché GitHub Actions non può restare in ascolto.
Il Worker legge `calendario.json`, che il workflow aggiorna in automatico nel repository.

### Installazione del Worker
1. Il repository deve essere **pubblico** (Settings → General → Danger Zone → Change visibility):
   contiene solo il codice e le date degli eventi; token e chat id restano nei Secrets.
2. Crea un account gratuito su cloudflare.com → *Workers & Pages* → *Create* → crea un Worker
   (anche con il modello "Hello World") → *Deploy*.
3. Apri *Edit code*, cancella tutto, incolla il contenuto di `worker.js` e fai *Deploy*.
4. Nelle impostazioni del Worker (*Settings → Variables and Secrets*) aggiungi:
   - `TELEGRAM_TOKEN` (secret): il token di BotFather
   - `WEBHOOK_SECRET` (secret): una parola segreta a tua scelta, solo lettere e numeri (es. `Xk29fPq81zLm`)
   - `CALENDAR_URL` (testo): `https://raw.githubusercontent.com/<tuo-utente>/<repo>/main/calendario.json`
5. Apri nel browser, una sola volta, `https://<nome-worker>.<tuo-account>.workers.dev/setup?key=<WEBHOOK_SECRET>`.
   Se vedi `"ok": true` due volte, il bot è collegato e il menu dei comandi è registrato.
6. Scrivi `/help` al bot in chat privata.

`calendario.json` esiste solo dopo il primo giro del workflow: avvialo prima da *Actions → Run workflow*.
I nomi dei menu di Cloudflare possono cambiare leggermente nel tempo.
