/**
 * Cloudflare Worker: risponde ai comandi Telegram del bot Fiera di Rimini.
 *
 *   /prossimi [n]  prossimi eventi (default 5, max 15)
 *   /incorso       eventi in corso oggi
 *   /help          elenco comandi
 *
 * Variabili da impostare su Cloudflare (Settings → Variables and Secrets):
 *   TELEGRAM_TOKEN  (secret)  token del bot
 *   WEBHOOK_SECRET  (secret)  parola segreta a tua scelta: solo lettere e numeri
 *   CALENDAR_URL    (testo)   https://raw.githubusercontent.com/<utente>/<repo>/main/calendario.json
 *
 * Dopo il deploy apri UNA volta: https://<tuo-worker>.workers.dev/setup?key=<WEBHOOK_SECRET>
 * (collega il bot al Worker e registra il menu dei comandi).
 */

const GIORNI = ["domenica", "lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato"];
const MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
  "agosto", "settembre", "ottobre", "novembre", "dicembre"];

const COMANDI = [
  { command: "prossimi", description: "Prossimi eventi in Fiera (es. /prossimi 10)" },
  { command: "incorso", description: "Eventi in corso oggi" },
  { command: "help", description: "Elenco dei comandi" },
];

const HELP = [
  "🚗 <b>Bot Fiera di Rimini</b>",
  "Ti aiuto a prevedere i giorni di traffico intenso legati agli eventi in Fiera.",
  "",
  "<b>Comandi</b>",
  "/prossimi [n] – prossimi eventi (predefinito 5, massimo 15)",
  "/incorso – eventi in corso oggi",
  "/help – questo elenco",
  "",
  "🔴 impatto traffico alto · 🟠 medio",
  "Gli avvisi automatici arrivano 4 giorni prima dell'inizio di ogni evento.",
].join("\n");

// ------------------------------------------------------------------ utilità
const esc = (t) => String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const parse = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d)); };
const fmt = (s) => { const d = parse(s); return `${GIORNI[d.getUTCDay()]} ${d.getUTCDate()} ${MESI[d.getUTCMonth()]}`; };
const intervallo = (e) => e.inizio === e.fine
  ? fmt(e.inizio)
  : `${fmt(e.inizio)} → ${fmt(e.fine)} ${e.fine.slice(0, 4)}`;
const giorniA = (s, oggi) => Math.round((parse(s) - parse(oggi)) / 86400000);
const quando = (g) => (g === 0 ? "oggi" : g === 1 ? "domani" : `tra ${g} giorni`);
const icona = (e) => (e.impatto === "alto" ? "🔴" : "🟠");
const oggiRoma = () => new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Rome" }).format(new Date());

async function tg(env, metodo, corpo) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_TOKEN}/${metodo}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(corpo),
  });
  return r.json();
}

const rispondi = (env, chatId, testo) =>
  tg(env, "sendMessage", { chat_id: chatId, text: testo, parse_mode: "HTML", disable_web_page_preview: true });

async function caricaCalendario(env) {
  const r = await fetch(env.CALENDAR_URL, { cf: { cacheTtl: 600, cacheEverything: true } });
  if (!r.ok) throw new Error(`Calendario non leggibile: HTTP ${r.status}`);
  const dati = await r.json();
  const eventi = [...dati.eventi].sort((a, b) => (a.inizio + a.nome).localeCompare(b.inizio + b.nome));
  return { eventi, aggiornato: dati.aggiornato };
}

const giornoMese = (s) => { const d = parse(s); return `${d.getUTCDate()} ${MESI[d.getUTCMonth()]}`; };
const piede = (agg) => (agg ? `\n🔄 Dati aggiornati al ${giornoMese(agg)}` : "");

// ------------------------------------------------------------------ comandi
function cmdProssimi(cal, oggi, arg) {
  let n = parseInt(arg, 10);
  if (!Number.isFinite(n) || n < 1) n = 5;
  n = Math.min(n, 15);
  const futuri = cal.eventi.filter((e) => e.inizio >= oggi).slice(0, n);
  if (!futuri.length) return "Al momento non risultano eventi in programma nel calendario." + piede(cal.aggiornato);
  const righe = ["📅 <b>Prossimi eventi alla Fiera di Rimini</b>", ""];
  for (const e of futuri) {
    righe.push(`• <b>${esc(e.nome)}</b> ${icona(e)}`);
    righe.push(`   ${intervallo(e)}`);
    righe.push(`   ⏳ ${quando(giorniA(e.inizio, oggi))}`);
  }
  return righe.join("\n") + "\n" + piede(cal.aggiornato);
}

function cmdInCorso(cal, oggi) {
  const inCorso = cal.eventi.filter((e) => e.inizio <= oggi && oggi <= e.fine);
  if (!inCorso.length) {
    const prossimo = cal.eventi.find((e) => e.inizio > oggi);
    const extra = prossimo
      ? `\nProssimo: <b>${esc(prossimo.nome)}</b>, ${quando(giorniA(prossimo.inizio, oggi))} (${fmt(prossimo.inizio)}).`
      : "";
    return "Oggi non ci sono eventi in corso alla Fiera di Rimini." + extra + "\n" + piede(cal.aggiornato);
  }
  const righe = ["🏟 <b>Eventi in corso oggi</b>", ""];
  for (const e of inCorso) {
    righe.push(`• <b>${esc(e.nome)}</b> ${icona(e)}`);
    righe.push(`   ${intervallo(e)}`);
  }
  return righe.join("\n") + "\n" + piede(cal.aggiornato);
}

async function gestisci(env, msg) {
  const chatId = msg.chat.id;
  const testo = (msg.text || "").trim();
  const [primo, ...resto] = testo.split(/\s+/);
  const cmd = primo.startsWith("/") ? primo.slice(1).split("@")[0].toLowerCase() : "";

  if (cmd === "start" || cmd === "help" || cmd === "") return rispondi(env, chatId, HELP);
  if (cmd !== "prossimi" && cmd !== "incorso") {
    return rispondi(env, chatId, "Comando non riconosciuto. Scrivi /help per l'elenco dei comandi.");
  }
  let cal;
  try {
    cal = await caricaCalendario(env);
  } catch (err) {
    console.error(err);
    return rispondi(env, chatId, "Non riesco a leggere il calendario in questo momento, riprova tra qualche minuto.");
  }
  const oggi = oggiRoma();
  return rispondi(env, chatId, cmd === "prossimi" ? cmdProssimi(cal, oggi, resto[0]) : cmdInCorso(cal, oggi));
}

// ------------------------------------------------------------------- routing
async function setup(url, env) {
  if (!env.WEBHOOK_SECRET || url.searchParams.get("key") !== env.WEBHOOK_SECRET) {
    return new Response("Non autorizzato", { status: 401 });
  }
  const webhook = await tg(env, "setWebhook", {
    url: `${url.origin}/webhook`,
    secret_token: env.WEBHOOK_SECRET,
    allowed_updates: ["message"],
  });
  const comandi = await tg(env, "setMyCommands", { commands: COMANDI });
  return new Response(JSON.stringify({ webhook, comandi }, null, 2), {
    headers: { "content-type": "application/json" },
  });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (url.pathname === "/setup") return setup(url, env);

    if (url.pathname === "/webhook" && request.method === "POST") {
      if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
        return new Response("Non autorizzato", { status: 401 });
      }
      const update = await request.json().catch(() => null);
      if (update && update.message && update.message.text) {
        ctx.waitUntil(gestisci(env, update.message).catch((e) => console.error(e)));
      }
      return new Response("ok");
    }
    return new Response("Bot Fiera di Rimini attivo");
  },
};
