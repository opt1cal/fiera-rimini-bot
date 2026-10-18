#!/usr/bin/env python3
"""
Avvisi Fiera di Rimini (IEG) via Telegram.

Ogni esecuzione:
  1. legge il calendario ufficiale IEG (scraping) e lo unisce a eventi_manuali.json
  2. trova gli eventi che iniziano entro DAYS_BEFORE giorni e non sono già stati notificati
  3. invia un messaggio Telegram (uno per ogni data di inizio) e salva lo stato
  4. pubblica calendario.json per i comandi del bot (vedi worker.js)
  5. segnala gli eventi NUOVI, quelli con date modificate e quelli ANNULLATI (spariti dal calendario)

Variabili d'ambiente:
  TELEGRAM_TOKEN     token del bot (da @BotFather)
  TELEGRAM_CHAT_IDS  uno o più chat id / @canale separati da virgola
  DAYS_BEFORE        giorni di anticipo (default 4)

Opzioni:
  --dry-run          stampa i messaggi senza inviarli e senza salvare lo stato
  --test             invia un messaggio di prova
  --list             mostra gli eventi trovati e termina
  --oggi YYYY-MM-DD  simula un'altra data odierna (utile per i test)
"""
import argparse
import html
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

BASE = Path(__file__).parent
MANUAL_FILE = BASE / "eventi_manuali.json"
STATE_FILE = BASE / "stato_inviati.json"
KNOWN_FILE = BASE / "eventi_noti.json"
CALENDAR_OUT = BASE / "calendario.json"   # letto dal Worker dei comandi (/prossimi ecc.)

IEG_URLS = [
    "https://www.iegexpo.it/en/venues/rimini/exhibitions",
    "https://www.iegexpo.it/it/venues/rimini/manifestazioni",
]
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; FieraRiminiBot/1.0; uso personale)"}

# Eventi che di norma portano molto traffico (modificabile a piacere).
RILEVANZA_ALTA = [
    "sigep", "ecomondo", "key", "rimini wellness", "meeting per",
    "ttg", "macfrut", "beer & food",
]

GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
        "agosto", "settembre", "ottobre", "novembre", "dicembre"]


# ----------------------------------------------------------------- calendario
def parse_ieg(page: str) -> list[dict]:
    """Estrae gli eventi dai link 'Google Calendar' presenti nella pagina IEG."""
    page = html.unescape(page)
    eventi = {}
    for m in re.finditer(r"https://calendar\.google\.com/calendar/render\?[^\s\"'<>)\]]+", page):
        qs = parse_qs(urlparse(m.group(0)).query)
        testo = (qs.get("text") or [""])[0]
        date_str = (qs.get("dates") or [""])[0]
        d = re.match(r"(\d{8})T?\d*/(\d{8})", date_str)
        if not (testo and d):
            continue
        nome = re.sub(r"<br\s*/?>", " ", testo)
        nome = re.sub(r"\s+", " ", nome).strip()
        inizio = datetime.strptime(d.group(1), "%Y%m%d").date()
        fine = datetime.strptime(d.group(2), "%Y%m%d").date()
        eventi[(nome, inizio)] = {"nome": nome, "inizio": inizio, "fine": fine}
    return list(eventi.values())


def scarica_calendario() -> list[dict]:
    for url in IEG_URLS:
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            eventi = parse_ieg(r.text)
            if eventi:
                print(f"Scraping OK: {len(eventi)} eventi da {url}")
                return eventi
            print(f"Nessun evento riconosciuto in {url}")
        except requests.RequestException as e:
            print(f"Errore scaricando {url}: {e}", file=sys.stderr)
    return []


def carica_manuali() -> tuple[list[dict], list[str]]:
    if not MANUAL_FILE.exists():
        return [], []
    dati = json.loads(MANUAL_FILE.read_text(encoding="utf-8"))
    eventi = [
        {
            "nome": e["nome"],
            "inizio": date.fromisoformat(e["inizio"]),
            "fine": date.fromisoformat(e["fine"]),
        }
        for e in dati.get("eventi", [])
    ]
    return eventi, [x.lower() for x in dati.get("escludi", [])]


def calendario_completo(oggi: date) -> tuple[list[dict], list[dict]]:
    """Restituisce (calendario unito, solo eventi scaricati dal sito)."""
    manuali, escludi = carica_manuali()
    scaricati = scarica_calendario()
    if not scaricati:
        print("ATTENZIONE: scraping fallito, uso solo eventi_manuali.json", file=sys.stderr)
    else:
        # Eventi manuali superati: stesso nome di un evento del sito ma date diverse,
        # oppure annullati/spostati già segnalati. Non devono generare avvisi.
        nomi_sito = {e["nome"].lower() for e in scaricati}
        chiavi_sito = {(e["nome"].lower(), e["inizio"]) for e in scaricati}
        annullati = carica_annullati()
        manuali = [
            m for m in manuali
            if chiave_ev(m) not in annullati
            and not ((m["nome"].lower() in nomi_sito
                      and (m["nome"].lower(), m["inizio"]) not in chiavi_sito
                      and m["inizio"] >= oggi))
        ]
    uniti = {}
    for e in manuali + scaricati:  # lo scraping sovrascrive i dati manuali identici
        uniti[(e["nome"].lower(), e["inizio"])] = e
    risultato = [e for e in uniti.values()
                 if not any(x in e["nome"].lower() for x in escludi)]
    return sorted(risultato, key=lambda e: (e["inizio"], e["nome"])), scaricati


# ------------------------------------------------------------------- messaggi
def fmt(d: date) -> str:
    return f"{GIORNI[d.weekday()]} {d.day} {MESI[d.month - 1]}"


def alta_rilevanza(nome: str) -> bool:
    n = nome.lower()
    return any(k in n for k in RILEVANZA_ALTA)


def quando(giorni: int) -> str:
    if giorni == 0:
        return "OGGI"
    if giorni == 1:
        return "DOMANI"
    return f"tra {giorni} giorni"


def costruisci_messaggio(gruppo: list[dict], oggi: date) -> str:
    inizio = gruppo[0]["inizio"]
    fine = max(e["fine"] for e in gruppo)
    giorni = (inizio - oggi).days
    alta = any(alta_rilevanza(e["nome"]) for e in gruppo)
    icona = "🔴" if alta else "🟠"
    livello = "ALTO" if alta else "MEDIO"

    righe = [f"🚗 <b>Fiera di Rimini: {quando(giorni)}</b>", ""]
    for e in gruppo:
        righe.append(f"• <b>{html.escape(e['nome'])}</b>")
    righe.append("")
    if inizio == fine:
        righe.append(f"📅 {fmt(inizio).capitalize()}")
    else:
        righe.append(f"📅 Da {fmt(inizio)} a {fmt(fine)}")
    righe.append(f"{icona} Impatto traffico stimato: {livello}")
    righe.append("")
    righe.append("Possibili rallentamenti nella zona della Fiera (Via Emilia, SS16, uscita A14 Rimini Nord). "
                 "Meglio mettere in conto più tempo.")
    return "\n".join(righe)


def invia_telegram(token: str, chat_ids: list[str], testo: str) -> None:
    for chat in chat_ids:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": testo, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=30,
        )
        if not r.ok:
            raise RuntimeError(f"Telegram ha risposto {r.status_code} per {chat}: {r.text}")


# --------------------------------------------------------- novità nel calendario
ASSENZE_PER_ANNULLO = 2   # controlli consecutivi in cui l'evento deve mancare prima di segnalarlo


def chiave_ev(e: dict) -> str:
    return f"{e['nome']}|{e['inizio'].isoformat()}"


def _leggi_noti_file():
    return json.loads(KNOWN_FILE.read_text(encoding="utf-8"))


def carica_noti():
    """Restituisce (eventi noti, chiavi annullate) oppure None al primo avvio."""
    if not KNOWN_FILE.exists():
        return None
    raw = _leggi_noti_file()
    if isinstance(raw, list):  # formato della versione precedente
        raw = {"eventi": raw, "annullati": []}
    noti = {}
    for e in raw.get("eventi", []):
        ev = {"nome": e["nome"], "inizio": date.fromisoformat(e["inizio"]),
              "fine": date.fromisoformat(e["fine"]), "assenze": int(e.get("assenze", 0))}
        noti[chiave_ev(ev)] = ev
    return noti, set(raw.get("annullati", []))


def carica_annullati() -> set[str]:
    if not KNOWN_FILE.exists():
        return set()
    raw = _leggi_noti_file()
    return set(raw.get("annullati", [])) if isinstance(raw, dict) else set()


def salva_noti(noti: dict, annullati: set, oggi: date) -> None:
    limite = oggi.toordinal() - 365
    righe = [
        {"nome": e["nome"], "inizio": e["inizio"].isoformat(), "fine": e["fine"].isoformat(),
         "assenze": e.get("assenze", 0)}
        for e in sorted(noti.values(), key=lambda e: (e["inizio"], e["nome"]))
        if e["fine"].toordinal() >= limite
    ]
    ann = sorted(k for k in annullati if k.rsplit("|", 1)[-1] >= str(date.fromordinal(limite)))
    KNOWN_FILE.write_text(
        json.dumps({"eventi": righe, "annullati": ann}, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8")


def rileva_novita(scaricati: list[dict], noti: dict, annullati: set, oggi: date):
    """Confronta il calendario scaricato con quello già noto.

    Aggiorna `noti` e `annullati` e restituisce (nuovi, modificati, annullati_ora).
    - modificato: stesso nome di un evento noto e ancora futuro che è sparito dal calendario
      (ha cambiato data);
    - annullato: evento futuro, non ancora iniziato, assente per ASSENZE_PER_ANNULLO controlli
      consecutivi (così un caricamento parziale del sito non genera falsi allarmi);
    - nuovo: tutto il resto, comprese le nuove edizioni di fiere annuali e gli eventi tornati
      in calendario dopo un annullamento.
    """
    correnti = {chiave_ev(e) for e in scaricati}
    nuovi, modificati, annullati_ora = [], [], []

    for e in scaricati:
        k = chiave_ev(e)
        if k in annullati:                     # era annullato ed è ricomparso
            annullati.discard(k)
            noti[k] = {**e, "assenze": 0}
            if e["fine"] >= oggi:
                nuovi.append(e)
            continue
        if k in noti:
            noti[k] = {**e, "assenze": 0}      # visto: azzera il contatore assenze
            continue
        noti[k] = {**e, "assenze": 0}
        if e["fine"] < oggi:
            continue
        spariti = [v for kk, v in noti.items()
                   if v["nome"] == e["nome"] and kk not in correnti
                   and kk not in annullati and v["inizio"] >= oggi]
        if spariti:
            modificati.append((spariti[0], e))
            annullati.add(chiave_ev(spariti[0]))   # data vecchia superata
        else:
            nuovi.append(e)

    # eventi futuri sparito dal calendario
    candidati = [(k, v) for k, v in noti.items()
                 if k not in correnti and k not in annullati and v["inizio"] > oggi]
    futuri_noti = [k for k, v in noti.items() if k not in annullati and v["inizio"] > oggi]
    if len(futuri_noti) >= 4 and len(candidati) > len(futuri_noti) / 2:
        print("ATTENZIONE: sparita oltre metà del calendario, probabile anomalia del sito: "
              "salto il controllo annullamenti.", file=sys.stderr)
        return nuovi, modificati, annullati_ora
    for k, v in candidati:
        v["assenze"] = v.get("assenze", 0) + 1
        if v["assenze"] >= ASSENZE_PER_ANNULLO:
            annullati.add(k)
            annullati_ora.append(v)
    return nuovi, modificati, annullati_ora


def salva_calendario(eventi: list[dict], oggi: date) -> None:
    """Scrive calendario.json (eventi in corso e futuri) per il Worker dei comandi."""
    righe = [
        {"nome": e["nome"], "inizio": e["inizio"].isoformat(), "fine": e["fine"].isoformat(),
         "impatto": "alto" if alta_rilevanza(e["nome"]) else "medio"}
        for e in eventi if e["fine"] >= oggi
    ]
    CALENDAR_OUT.write_text(
        json.dumps({"aggiornato": oggi.isoformat(), "eventi": righe}, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8")


def intervallo(e: dict) -> str:
    if e["inizio"] == e["fine"]:
        return fmt(e["inizio"])
    return f"{fmt(e['inizio'])} → {fmt(e['fine'])} {e['fine'].year}"


def messaggio_novita(nuovi: list[dict], modificati: list, annullati_ora: list) -> str:
    righe = []
    if nuovi:
        righe.append("🆕 <b>Nuovi eventi in calendario alla Fiera di Rimini</b>")
        righe.append("")
        for e in nuovi:
            icona = "🔴" if alta_rilevanza(e["nome"]) else "🟠"
            righe.append(f"• <b>{html.escape(e['nome'])}</b>")
            righe.append(f"   📅 {intervallo(e)} {icona}")
        righe.append("")
    if modificati:
        righe.append("📝 <b>Date modificate</b>")
        righe.append("")
        for vecchio, nuovo in modificati:
            righe.append(f"• <b>{html.escape(nuovo['nome'])}</b>")
            righe.append(f"   prima: {intervallo(vecchio)}")
            righe.append(f"   ora: {intervallo(nuovo)}")
        righe.append("")
    if annullati_ora:
        righe.append("❌ <b>Eventi rimossi dal calendario (annullati o rinviati)</b>")
        righe.append("")
        for e in annullati_ora:
            righe.append(f"• <b>{html.escape(e['nome'])}</b>")
            righe.append(f"   era previsto: {intervallo(e)}")
        righe.append("")
        righe.append("Non riceverai più l'avviso di inizio per questi eventi. "
                     "Se vengono reinseriti con nuove date te lo segnalo.")
        righe.append("")
    if nuovi or modificati:
        righe.append("Ti avviserò di nuovo qualche giorno prima dell'inizio.")
    return "\n".join(righe).strip()


def controlla_novita(scaricati, oggi, token, chat_ids, dry_run) -> None:
    if not scaricati:  # scraping fallito: meglio non confrontare nulla
        return
    caricato = carica_noti()
    if caricato is None:
        print(f"Primo avvio: registro {len(scaricati)} eventi già in calendario (nessun avviso).")
        if not dry_run:
            salva_noti({chiave_ev(e): {**e, "assenze": 0} for e in scaricati}, set(), oggi)
        return
    noti, annullati = caricato
    nuovi, modificati, annullati_ora = rileva_novita(scaricati, noti, annullati, oggi)
    if nuovi or modificati or annullati_ora:
        testo = messaggio_novita(nuovi, modificati, annullati_ora)
        if dry_run:
            print("-" * 40)
            print(html.unescape(re.sub(r"</?b>", "", testo)))
            return
        invia_telegram(token, chat_ids, testo)
        print(f"Novità segnalate: {len(nuovi)} nuovi, {len(modificati)} modificati, "
              f"{len(annullati_ora)} annullati.")
    else:
        print("Nessuna novità nel calendario.")
    if not dry_run:
        salva_noti(noti, annullati, oggi)


# ---------------------------------------------------------------------- stato
def carica_stato() -> set[str]:
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    return set()


def salva_stato(stato: set[str], oggi: date) -> None:
    # tiene solo le chiavi recenti per non far crescere il file
    recenti = sorted(k for k in stato if k.rsplit("|", 1)[-1] >= str(oggi.replace(year=oggi.year - 1)))
    STATE_FILE.write_text(json.dumps(recenti, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def chiave(e: dict) -> str:
    return f"{e['nome']}|{e['inizio'].isoformat()}"


# ----------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--oggi", type=date.fromisoformat, default=date.today())
    args = ap.parse_args()

    token = os.environ.get("TELEGRAM_TOKEN", "")
    chat_ids = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_IDS", "").split(",") if c.strip()]
    anticipo = int(os.environ.get("DAYS_BEFORE", "4"))

    if args.test:
        if not (token and chat_ids):
            sys.exit("Imposta TELEGRAM_TOKEN e TELEGRAM_CHAT_IDS")
        invia_telegram(token, chat_ids, "✅ Bot Fiera di Rimini collegato correttamente.")
        print("Messaggio di prova inviato.")
        return 0

    eventi, scaricati = calendario_completo(args.oggi)

    if args.list:
        for e in eventi:
            print(f"{e['inizio']} → {e['fine']}  {e['nome']}")
        return 0

    if scaricati and not args.dry_run:  # se lo scraping è fallito non sovrascrive dati buoni
        salva_calendario(eventi, args.oggi)

    stato = carica_stato()
    oggi = args.oggi

    if not args.dry_run and not (token and chat_ids):
        sys.exit("Imposta TELEGRAM_TOKEN e TELEGRAM_CHAT_IDS")
    controlla_novita(scaricati, oggi, token, chat_ids, args.dry_run)

    da_avvisare = [e for e in eventi
                   if 0 <= (e["inizio"] - oggi).days <= anticipo and chiave(e) not in stato]

    if not da_avvisare:
        print(f"Nessun nuovo evento da segnalare (oggi {oggi}, anticipo {anticipo} giorni).")
        return 0

    gruppi: dict[date, list[dict]] = {}
    for e in da_avvisare:
        gruppi.setdefault(e["inizio"], []).append(e)

    if not args.dry_run and not (token and chat_ids):
        sys.exit("Imposta TELEGRAM_TOKEN e TELEGRAM_CHAT_IDS")

    for inizio, gruppo in sorted(gruppi.items()):
        testo = costruisci_messaggio(gruppo, oggi)
        if args.dry_run:
            print("-" * 40)
            print(html.unescape(re.sub(r"</?b>", "", testo)))
            continue
        invia_telegram(token, chat_ids, testo)
        stato.update(chiave(e) for e in gruppo)
        salva_stato(stato, oggi)  # salva dopo ogni invio riuscito
        print(f"Inviato avviso per {inizio}: {[e['nome'] for e in gruppo]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
