"use strict";
/* Ausgaben – Frontend (ohne Build-Schritt, laeuft im HA-Ingress und als iPhone-Web-App) */

const S = {
  meta: null,
  tab: "uebersicht",
  zeitraum: { einheit: "monat", anker: new Date() },
  bandAuswahl: null,
  suche: { q: "", kategorie: "", konto: "", pruefen: false, offen: false, von: "", bis: "", sortierung: "" },
  liste: { eintraege: [], offset: 0, fertig: false, summe: 0, anzahl: 0 },
  verlauf: { einheit: "monat", kategorie: "" },
  letzteWaehrung: localStorage.getItem("ausgaben_waehrung") || "EUR",
  letztesKonto: localStorage.getItem("ausgaben_konto") || "",
};
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const ansicht = $("#ansicht");

// ---------------------------------------------------------------- Hilfen
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const geldFmt = {};
function geld(n, w = "EUR", stellen = 2) {
  const key = w + stellen;
  geldFmt[key] ??= new Intl.NumberFormat("de-DE", { style: "currency", currency: w, minimumFractionDigits: stellen, maximumFractionDigits: stellen });
  return geldFmt[key].format(n ?? 0);
}
const kurzGeld = (n) => (Math.abs(n) >= 1000 ? geld(n, "EUR", 0) : geld(n));
const pad = (n) => String(n).padStart(2, "0");
const isoTag = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const ausIso = (s) => { const [y, m, d] = s.slice(0, 10).split("-").map(Number); return new Date(y, m - 1, d); };
const tagPlus = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];
const MON_KURZ = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"];
const WT = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];
const WT_LANG = ["Sonntag", "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag"];
function kw(d) {
  const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
  const tag = t.getUTCDay() || 7; t.setUTCDate(t.getUTCDate() + 4 - tag);
  const jahr = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
  return Math.ceil(((t - jahr) / 86400000 + 1) / 7);
}
function tagLabel(iso) {
  const d = ausIso(iso), heute = new Date(), gestern = tagPlus(heute, -1);
  if (isoTag(d) === isoTag(heute)) return "Heute";
  if (isoTag(d) === isoTag(gestern)) return "Gestern";
  const jahr = d.getFullYear() !== heute.getFullYear() ? ` ${d.getFullYear()}` : "";
  return `${WT_LANG[d.getDay()]}, ${d.getDate()}. ${MONATE[d.getMonth()]}${jahr}`;
}
const kat = (id) => S.meta?.kategorien.find((k) => k.id === id);
const icFarbe = (farbe) => `background:${farbe}22;`;
function zahlLesen(s) {
  if (typeof s === "number") return s;
  s = String(s || "").trim().replace(/\s/g, "");
  if (s.includes(",")) s = s.replace(/\./g, "").replace(",", ".");
  return parseFloat(s);
}

function toast(text, aktion) {
  const t = $("#toast");
  t.innerHTML = esc(text) + (aktion ? `<button>${esc(aktion.label)}</button>` : "");
  t.hidden = false;
  if (aktion) $("button", t).onclick = () => { t.hidden = true; aktion.fn(); };
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), aktion ? 5000 : 2600);
}

class OfflineFehler extends Error {
  constructor() { super("Offline – das geht erst wieder mit Verbindung"); this.offline = true; }
}
async function api(pfad, opt = {}) {
  const init = { method: opt.method || "GET", headers: {}, credentials: "same-origin" };
  const token = localStorage.getItem("ausgaben_token");
  if (token) init.headers["X-Ausgaben-Token"] = token;
  if (opt.body instanceof FormData) init.body = opt.body;
  else if (opt.body !== undefined) { init.body = JSON.stringify(opt.body); init.headers["Content-Type"] = "application/json"; }
  // Ohne Netz (z. B. Flugmodus mit VPN) haengen Anfragen sonst sehr lange: Zeitgrenze setzen.
  const abbruch = new AbortController();
  // Lange Vorgaenge (Import, Bankabruf, KI) ohne Grenze; Lesen 30 s; im Offline-Zustand kurz.
  const grenze = opt.zeitlimit ?? (S.offline ? 4000 : (init.method === "GET" ? 30000 : null));
  const timer = grenze ? setTimeout(() => abbruch.abort(), grenze) : null;
  init.signal = abbruch.signal;
  let r;
  try { r = await fetch("api/" + pfad, init); } catch (_) { offlineMelden(null); throw new OfflineFehler(); }
  finally { clearTimeout(timer); }
  const offlineStand = r.headers.get("X-Ausgaben-Offline");
  if (offlineStand !== null) { offlineMelden(+offlineStand || null); if (r.status === 503) throw new OfflineFehler(); }
  else if (r.status !== 503) onlineMelden();
  if (r.status === 401) { anmeldungZeigen(); throw new Error("Nicht angemeldet"); }
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* leer */ }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return opt.roh ? r : r.json();
}
const qs = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== "" && v != null && v !== false)).toString();

// ---------------------------------------------------------------- Offline-Betrieb
const WS_KEY = "ausgaben_warteschlange";
const wsLesen = () => { try { return JSON.parse(localStorage.getItem(WS_KEY) || "[]"); } catch (_) { return []; } };
const wsSchreiben = (l) => localStorage.setItem(WS_KEY, JSON.stringify(l));
const neueRef = () => (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2));

function offlineMelden(stand) {
  if (!S.offline) S.offline = { stand };
  else if (stand && (!S.offline.stand || stand < S.offline.stand)) S.offline.stand = stand;
  leisteZeigen();
}
function onlineMelden() {
  if (!S.offline) return;
  S.offline = null; leisteZeigen();
  warteschlangeSenden();
}
function leisteZeigen() {
  let el = $("#offline-leiste");
  if (!el) { el = document.createElement("button"); el.id = "offline-leiste"; document.body.appendChild(el); el.onclick = () => warteschlangeSenden(true); }
  const n = wsLesen().length;
  const teile = [];
  if (S.offline) {
    const d = S.offline.stand ? new Date(S.offline.stand) : null;
    const heute = d && d.toDateString() === new Date().toDateString();
    teile.push("Offline" + (d ? ` · Stand ${d.toLocaleString("de-DE", heute ? { hour: "2-digit", minute: "2-digit" } : { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" })}` : ""));
  }
  if (n) teile.push(`${n} ${n === 1 ? "Eintrag wartet" : "Einträge warten"}`);
  el.textContent = teile.join(" · ");
  el.hidden = !teile.length;
  document.documentElement.classList.toggle("mit-offline-leiste", !!teile.length);
}
function wartendeAktualisieren() {
  if (S.tab === "eintraege" && $("#liste")) {
    const s = S.suche;
    if (!s.q && !s.kategorie && !s.konto && !s.pruefen && !s.offen && !s.von && !s.bis) wartendeEinbauen($("#liste"));
  } else if (S.tab === "uebersicht" && $("#letzte")) wartendeEinbauen($("#letzte"));
}
function inWarteschlange(x) {
  const l = wsLesen(); l.push({ ...x, zeit: Date.now() });
  try { wsSchreiben(l); } catch (_) { throw new Error("Der Speicher auf dem Gerät ist voll, der Eintrag wurde nicht gespeichert"); }
  leisteZeigen();
}
async function warteschlangeSenden(vonHand = false) {
  if (S.sendetWs || !wsLesen().length) return;
  S.sendetWs = true;
  let gesendet = 0;
  try {
    for (;;) {
      const l = wsLesen();
      if (!l.length) break;
      const x = l[0];
      try {
        if (x.art === "eintrag") {
          await api("eintraege", { method: "POST", body: x.daten });
        } else if (x.art === "beleg") {
          const r = await api("beleg", { method: "POST", body: { bild: x.bild } });
          if (r.lesbar && r.betrag != null) {
            await api("eintraege", { method: "POST", body: {
              client_ref: x.ref, pruefen: true, datum: r.datum ? `${r.datum}T${r.uhrzeit || "12:00"}` : x.datum,
              betrag: r.betrag, waehrung: r.waehrung || "EUR", kategorie_id: r.kategorie_id, konto_id: r.konto_id || x.konto_id,
              bezeichnung: r.bezeichnung || r.haendler || "Kassenbon", notiz: "Offline fotografierter Kassenbon, bitte prüfen" } });
          } else {
            toast(`Ein offline fotografierter Kassenbon war nicht lesbar: ${r.hinweis || "bitte von Hand erfassen"}`);
          }
        }
      } catch (e) {
        if (e.offline || e.message === "Nicht angemeldet") throw e;
        toast(`Nicht übertragen: ${e.message}`); // fehlerhafter Eintrag blockiert nicht die übrigen
      }
      const rest = wsLesen(); rest.shift(); wsSchreiben(rest);
      gesendet++;
    }
  } catch (e) {
    if (vonHand) toast(e.message);
  } finally {
    S.sendetWs = false;
    leisteZeigen();
    if (gesendet) {
      toast(`${gesendet} offline ${gesendet === 1 ? "erfasster Eintrag" : "erfasste Einträge"} übertragen`);
      try { await metaLaden(); neuZeichnen(); } catch (_) { /* egal */ }
    }
  }
}
window.addEventListener("online", () => warteschlangeSenden());
window.addEventListener("unhandledrejection", (ev) => {
  if (ev.reason && ev.reason.offline) { ev.preventDefault(); toast(ev.reason.message); }
});
setInterval(() => { if (wsLesen().length && document.visibilityState === "visible") warteschlangeSenden(); }, 30000);

async function metaLaden() {
  S.meta = await api("meta");
  $(".tabs [data-tab=mehr]")?.classList.toggle("punkt", !!S.meta.update?.verfuegbar);
  const d = S.meta.darstellung;
  if (d && (d.design !== S.design.id || d.modus !== S.design.modus)) designAnwenden(d.design, d.modus);
  return S.meta;
}

// ---------------------------------------------------------------- Design
const DESIGNS = {"salbei":{"name":"Salbei","hell":{"grund":"#F3F5F4","flaeche":"#FFFFFF","flaeche-2":"#E9EEEC","tinte":"#16201C","leise":"#66736D","linie":"#DCE3E0","akzent":"#1E6B52","akzent-zart":"#DCEDE6","pruefen":"#A3461F","pruefen-zart":"#F6E4DA","gutschrift":"#2E7D5B","auf-akzent":"#FFFFFF"},"dunkel":{"grund":"#0F1513","flaeche":"#18201D","flaeche-2":"#212B27","tinte":"#E8EEEB","leise":"#93A39B","linie":"#2A3531","akzent":"#5CC29A","akzent-zart":"#1D3A2F","pruefen":"#F08A5D","pruefen-zart":"#3A2519","gutschrift":"#6FD0A4","auf-akzent":"#08130F"}},"ozean":{"name":"Ozean","hell":{"grund":"#F2F5F9","flaeche":"#FFFFFF","flaeche-2":"#E6ECF4","tinte":"#121C2B","leise":"#5F6B7C","linie":"#D9E1EC","akzent":"#1F5FAE","akzent-zart":"#DCE8F7","pruefen":"#A3461F","pruefen-zart":"#F7E3D9","gutschrift":"#2C7A57","auf-akzent":"#FFFFFF"},"dunkel":{"grund":"#0D131C","flaeche":"#161E2A","flaeche-2":"#1F2937","tinte":"#E6ECF4","leise":"#8E9BAE","linie":"#283343","akzent":"#6FA8F0","akzent-zart":"#1A2C46","pruefen":"#F08A5D","pruefen-zart":"#3A2519","gutschrift":"#6FD0A4","auf-akzent":"#0A1220"}},"nordlicht":{"name":"Nordlicht","hell":{"grund":"#F0F6F6","flaeche":"#FFFFFF","flaeche-2":"#E2EEEE","tinte":"#0F1F22","leise":"#56696C","linie":"#D3E3E4","akzent":"#0B6E77","akzent-zart":"#D3EEF0","pruefen":"#A3461F","pruefen-zart":"#F7E3D9","gutschrift":"#2E7D5B","auf-akzent":"#FFFFFF"},"dunkel":{"grund":"#0A1220","flaeche":"#111B2D","flaeche-2":"#18253A","tinte":"#E3EEF6","leise":"#8FA3B8","linie":"#22324A","akzent":"#3DD6C5","akzent-zart":"#123A44","pruefen":"#F08A5D","pruefen-zart":"#3A2519","gutschrift":"#7CE0A8","auf-akzent":"#04201F"}},"lavendel":{"name":"Lavendel","hell":{"grund":"#F5F3F9","flaeche":"#FFFFFF","flaeche-2":"#ECE8F4","tinte":"#1C1729","leise":"#6B6480","linie":"#E0DBEB","akzent":"#5B3FB0","akzent-zart":"#E6DFF7","pruefen":"#A3461F","pruefen-zart":"#F6E4DA","gutschrift":"#2E7D5B","auf-akzent":"#FFFFFF"},"dunkel":{"grund":"#120F1A","flaeche":"#1B1724","flaeche-2":"#25202F","tinte":"#ECE8F4","leise":"#9E97B2","linie":"#2F293B","akzent":"#A893F2","akzent-zart":"#2A2245","pruefen":"#F08A5D","pruefen-zart":"#3A2519","gutschrift":"#6FD0A4","auf-akzent":"#140C2A"}},"beere":{"name":"Beere","hell":{"grund":"#F7F2F4","flaeche":"#FFFFFF","flaeche-2":"#F0E6EA","tinte":"#241419","leise":"#7A626B","linie":"#E9DCE1","akzent":"#A3264F","akzent-zart":"#F6DDE6","pruefen":"#8C4C0D","pruefen-zart":"#F7E8D6","gutschrift":"#2E7D5B","auf-akzent":"#FFFFFF"},"dunkel":{"grund":"#170F12","flaeche":"#21161A","flaeche-2":"#2B1D23","tinte":"#F2E6EA","leise":"#A9939B","linie":"#362730","akzent":"#F07AA0","akzent-zart":"#3F1D2B","pruefen":"#F2A65A","pruefen-zart":"#3A2A17","gutschrift":"#6FD0A4","auf-akzent":"#1A0810"}},"koralle":{"name":"Koralle","hell":{"grund":"#FAF4F1","flaeche":"#FFFFFF","flaeche-2":"#F3E8E3","tinte":"#25150F","leise":"#7A6359","linie":"#EBDDD6","akzent":"#A83A22","akzent-zart":"#FBE0D8","pruefen":"#6E46B0","pruefen-zart":"#EDE3F7","gutschrift":"#2E7D5B","auf-akzent":"#FFFFFF"},"dunkel":{"grund":"#18110E","flaeche":"#221915","flaeche-2":"#2D211C","tinte":"#F5EAE5","leise":"#AE9990","linie":"#382A24","akzent":"#FF8A70","akzent-zart":"#46231A","pruefen":"#B69CF5","pruefen-zart":"#2C2341","gutschrift":"#6FD0A4","auf-akzent":"#2A0B04"}},"bernstein":{"name":"Bernstein","hell":{"grund":"#F8F5EF","flaeche":"#FFFFFF","flaeche-2":"#F0EADF","tinte":"#221B10","leise":"#75695A","linie":"#E6DECF","akzent":"#8F5400","akzent-zart":"#F6E7C8","pruefen":"#B23A3A","pruefen-zart":"#F6DCDC","gutschrift":"#2E7D5B","auf-akzent":"#FFFFFF"},"dunkel":{"grund":"#15120C","flaeche":"#1F1A12","flaeche-2":"#29231A","tinte":"#F3ECDF","leise":"#A89C88","linie":"#352D21","akzent":"#F2B544","akzent-zart":"#3C2E12","pruefen":"#F07A7A","pruefen-zart":"#3C1C1C","gutschrift":"#6FD0A4","auf-akzent":"#1E1404"}},"graphit":{"name":"Graphit","hell":{"grund":"#F4F4F5","flaeche":"#FFFFFF","flaeche-2":"#E9E9EB","tinte":"#18181B","leise":"#66666E","linie":"#DEDEE2","akzent":"#27272A","akzent-zart":"#E4E4E7","pruefen":"#AE3A09","pruefen-zart":"#FBE4D8","gutschrift":"#2E7D5B","auf-akzent":"#FFFFFF"},"dunkel":{"grund":"#0C0C0E","flaeche":"#17171A","flaeche-2":"#222226","tinte":"#ECECEE","leise":"#9A9AA3","linie":"#2C2C31","akzent":"#E8E8EB","akzent-zart":"#2A2A2F","pruefen":"#FB923C","pruefen-zart":"#3A2414","gutschrift":"#6FD0A4","auf-akzent":"#111113"}}};
const MODI = [["auto", "Automatisch"], ["hell", "Hell"], ["dunkel", "Dunkel"]];
const dunkelAbfrage = matchMedia("(prefers-color-scheme: dark)");
S.design = (() => { try { const d = JSON.parse(localStorage.getItem("ausgaben_design") || "null"); return { id: d?.id || "salbei", modus: d?.modus || "auto" }; } catch (_) { return { id: "salbei", modus: "auto" }; } })();
function designAnwenden(id = S.design.id, modus = S.design.modus) {
  if (!DESIGNS[id]) id = "salbei";
  if (!MODI.some(([m]) => m === modus)) modus = "auto";
  S.design = { id, modus };
  const dunkel = modus === "dunkel" || (modus === "auto" && dunkelAbfrage.matches);
  const v = DESIGNS[id][dunkel ? "dunkel" : "hell"];
  const st = document.documentElement.style;
  for (const [k, w] of Object.entries(v)) st.setProperty("--" + k, w);
  st.colorScheme = dunkel ? "dark" : "light";
  document.querySelectorAll('meta[name="theme-color"]').forEach((m) => { m.removeAttribute("media"); m.content = v.grund; });
  try { localStorage.setItem("ausgaben_design", JSON.stringify({ id, modus, hell: DESIGNS[id].hell, dunkel: DESIGNS[id].dunkel })); } catch (_) { /* egal */ }
}
dunkelAbfrage.addEventListener("change", () => { if (S.design.modus === "auto") designAnwenden(); });
designAnwenden();

function designVorschau(d) {
  const halb = (v) => `<div class="halb" style="background:${v.grund}">
      <div class="mini" style="background:${v.flaeche}"><div class="strich" style="background:${v.tinte};width:70%"></div><div class="strich" style="background:${v.leise};width:45%;opacity:.7"></div>
        <div class="balken"><i style="background:${v.akzent};height:55%"></i><i style="background:${v.akzent};height:100%"></i><i style="background:${v["flaeche-2"]};height:35%"></i><i style="background:${v.akzent};height:75%"></i></div></div>
      <span class="punkt" style="background:${v["pruefen-zart"]};box-shadow:inset 0 0 0 2px ${v.pruefen}"></span>
      <span class="knopf-mini" style="background:${v.akzent}"></span></div>`;
  return `<div class="vorschau">${halb(d.hell)}${halb(d.dunkel)}</div>`;
}
function farbpunkte(id) {
  const d = DESIGNS[id] || DESIGNS.salbei;
  return `<span class="farbpunkte"><i style="background:${d.hell.akzent}"></i><i style="background:${d.dunkel.akzent}"></i><i style="background:${d.hell.pruefen}"></i></span>`;
}
async function offlineAnsicht() {
  const h = await api("https");
  const ip = (h.namen.find((n) => /^\d+\.\d+\.\d+\.\d+$/.test(n)) || location.hostname);
  const httpsAdresse = `https://${ip}:${h.port}`;
  const httpAdresse = `${location.protocol}//${location.host}`;
  const istHttps = location.protocol === "https:";
  const sw = !!navigator.serviceWorker?.controller;
  const n = wsLesen().length;
  unterseite("Offline & HTTPS", `
    <div class="mehr-liste">
      <div class="zeile">Diese Verbindung<span class="rechts">${istHttps ? "HTTPS ✓" : "HTTP (ohne Offline)"}</span></div>
      <div class="zeile">Offline-Speicher<span class="rechts">${sw ? "aktiv ✓" : istHttps ? "startet beim nächsten Öffnen" : "nicht möglich"}</span></div>
      <div class="zeile">Warten auf Übertragung<span class="rechts">${n}</span></div>
    </div>
    ${n ? '<div class="knopfreihe"><button class="knopf zweit" id="o-senden">Jetzt übertragen</button></div>' : ""}
    ${istHttps && sw ? `<p class="leise" style="font-size:14px;margin:12px 4px">Alles eingerichtet. Ohne Netz startet die App mit dem letzten Stand; neue Ausgaben und Kassenbon-Fotos werden gespeichert und automatisch übertragen.</p>` : `
    <div class="abschnitt">Einrichtung auf dem iPhone (einmalig)</div>
    <ol class="anleitung">
      <li><b>Zertifikat laden:</b> In <b>Safari</b> (nicht in der Homescreen-App) <span class="zahl">${esc(httpAdresse)}/zertifikat.crt</span> öffnen und „Zulassen“ tippen.
        <div class="knopfreihe"><a class="knopf zweit" href="zertifikat.crt" target="_blank" rel="noopener">Zertifikat herunterladen</a><button class="knopf zweit" id="o-kopie1">Adresse kopieren</button></div></li>
      <li><b>Profil installieren:</b> Einstellungen → Allgemein → VPN &amp; Geräteverwaltung → „Ausgaben Add-on CA“ → Installieren.</li>
      <li><b>Vertrauen einschalten:</b> Einstellungen → Allgemein → Info → ganz unten Zertifikatsvertrauenseinstellungen → „Ausgaben Add-on CA“ einschalten.</li>
      <li><b>App neu ablegen:</b> In Safari <span class="zahl">${esc(httpsAdresse)}</span> öffnen, Token eingeben, Teilen → Zum Home-Bildschirm. Die alte Homescreen-App kannst du danach löschen.
        <div class="knopfreihe"><button class="knopf zweit" id="o-kopie2">Adresse kopieren</button></div></li>
      <li>Die neue App einmal mit Verbindung öffnen, damit sie den aktuellen Stand speichert.</li>
    </ol>`}
    <div class="abschnitt">Zertifikat</div>
    <div class="mehr-liste">
      <div class="zeile">Gilt für<span class="rechts">${esc(h.namen.join(", ") || "–")}</span></div>
      <div class="zeile">Erneuert sich automatisch, aktuell bis<span class="rechts">${new Date(h.gueltig_bis).toLocaleDateString("de-DE")}</span></div>
      <div class="zeile" style="display:block">Fingerabdruck<br><small class="leise zahl" style="word-break:break-all">${esc(h.fingerabdruck)}</small></div>
    </div>
    <label class="feld" style="margin-top:12px"><span>Weitere Adressen (Komma-getrennt, z. B. Tailscale-Namen)</span><input id="o-namen" value="${esc(h.eigene.join(", "))}" autocomplete="off" autocapitalize="off"></label>
    <div class="knopfreihe" style="margin-top:0"><button class="knopf zweit" id="o-sichern">Sichern</button></div>
    <p class="leise" style="font-size:13px;margin:8px 4px">Adressen, unter denen du die App aufrufst, lernt das Add-on automatisch. Ohne Netz gehen Bankabrufe, KI und Änderungen an bestehenden Einträgen nicht; neue Ausgaben und Kassenbon-Fotos schon.</p>`);
  const kopieren = async (t) => { try { await navigator.clipboard.writeText(t); toast("Adresse kopiert"); } catch (_) { toast(t); } };
  $("#o-kopie1")?.addEventListener("click", () => kopieren(`${httpAdresse}/zertifikat.crt`));
  $("#o-kopie2")?.addEventListener("click", () => kopieren(httpsAdresse));
  $("#o-senden")?.addEventListener("click", async () => { await warteschlangeSenden(true); offlineAnsicht(); });
  $("#o-sichern").onclick = async () => {
    await api("https", { method: "PUT", body: { eigene: $("#o-namen").value.split(",").map((x) => x.trim()).filter(Boolean) } });
    toast("Gesichert, Zertifikat aktualisiert"); offlineAnsicht();
  };
}

const STARTSEITEN = { uebersicht: "Übersicht", eintraege: "Einträge", verlauf: "Verlauf" };
function startseiteSheet() {
  const aktuell = S.meta.darstellung.startseite || "uebersicht";
  sheetOeffnen(`<div class="griff"></div><div class="sheet-kopf"><span style="width:60px"></span><h3>Startseite</h3><button data-zu>Fertig</button></div>
    <p class="leise" style="font-size:14px;margin:4px 4px 10px">Diese Seite erscheint beim Öffnen der App, auf allen Geräten.</p>
    <div class="mehr-liste">${Object.entries(STARTSEITEN).map(([id, name]) => `<button data-start="${id}">${esc(name)}<span class="rechts">${id === aktuell ? "✓" : ""}</span></button>`).join("")}</div>`);
  $$("[data-start]").forEach((b) => (b.onclick = async () => {
    try { S.meta = await api("einstellungen", { method: "PUT", body: { startseite: b.dataset.start } }); }
    catch (err) { toast(err.message); return; }
    sheetSchliessen(); toast(`Startseite: ${STARTSEITEN[b.dataset.start]}`); mehrAnsicht();
  }));
}

function designAnsicht() {
  const zeichnen = () => {
    unterseite("Design", `
      <div class="segment" role="tablist">${MODI.map(([m, t]) => `<button data-modus="${m}" class="${S.design.modus === m ? "aktiv" : ""}">${t}</button>`).join("")}</div>
      <p class="leise" style="font-size:13px;margin:8px 4px 0">${S.design.modus === "auto" ? "Hell oder dunkel folgt der Einstellung deines Geräts." : S.design.modus === "hell" ? "Immer hell, unabhängig vom Gerät." : "Immer dunkel, unabhängig vom Gerät."}</p>
      <div class="design-gitter">${Object.entries(DESIGNS).map(([id, d]) => `<button class="design-karte ${S.design.id === id ? "aktiv" : ""}" data-design="${id}" aria-pressed="${S.design.id === id}">
        ${designVorschau(d)}<span class="name">${esc(d.name)}${S.design.id === id ? '<span class="haken">✓</span>' : ""}</span></button>`).join("")}</div>
      <p class="leise" style="font-size:13px;margin:6px 4px">Jede Vorschau zeigt links die helle, rechts die dunkle Variante. Die Wahl gilt für alle Geräte, auf denen du die App nutzt.</p>`);
    $$("[data-modus]").forEach((b) => (b.onclick = () => waehlen(S.design.id, b.dataset.modus)));
    $$("[data-design]").forEach((b) => (b.onclick = () => waehlen(b.dataset.design, S.design.modus)));
  };
  const waehlen = async (id, modus) => {
    designAnwenden(id, modus);
    zeichnen();
    try { S.meta = await api("einstellungen", { method: "PUT", body: { design: id, design_modus: modus } }); }
    catch (err) { toast("Nur auf diesem Gerät gespeichert: " + err.message); }
  };
  zeichnen();
}


// ---------------------------------------------------------------- Zeitraum
function zeitraum(einheit = S.zeitraum.einheit, anker = S.zeitraum.anker) {
  const a = new Date(anker.getFullYear(), anker.getMonth(), anker.getDate());
  let von, bis, label, sub, vor;
  if (einheit === "tag") {
    von = bis = a; label = tagLabel(isoTag(a)); sub = null; vor = [tagPlus(a, -1), tagPlus(a, -1)];
  } else if (einheit === "woche") {
    von = tagPlus(a, -((a.getDay() + 6) % 7)); bis = tagPlus(von, 6);
    label = `KW ${kw(von)}, ${von.getDate()}.${von.getMonth() !== bis.getMonth() ? " " + MON_KURZ[von.getMonth()] : ""}–${bis.getDate()}. ${MON_KURZ[bis.getMonth()]}`;
    sub = "tag"; vor = [tagPlus(von, -7), tagPlus(bis, -7)];
  } else if (einheit === "monat") {
    von = new Date(a.getFullYear(), a.getMonth(), 1); bis = new Date(a.getFullYear(), a.getMonth() + 1, 0);
    label = `${MONATE[a.getMonth()]} ${a.getFullYear()}`; sub = "tag";
    vor = [new Date(a.getFullYear(), a.getMonth() - 1, 1), new Date(a.getFullYear(), a.getMonth(), 0)];
  } else {
    von = new Date(a.getFullYear(), 0, 1); bis = new Date(a.getFullYear(), 11, 31);
    label = String(a.getFullYear()); sub = "monat"; vor = [new Date(a.getFullYear() - 1, 0, 1), new Date(a.getFullYear() - 1, 11, 31)];
  }
  const heute = new Date();
  const laufend = von <= heute && heute <= tagPlus(bis, 1);
  return { einheit, von: isoTag(von), bis: isoTag(bis), label, sub, vorVon: isoTag(vor[0]), vorBis: isoTag(vor[1]), laufend, vonD: von, bisD: bis };
}
function verschieben(richtung) {
  const a = S.zeitraum.anker, e = S.zeitraum.einheit;
  if (e === "tag") S.zeitraum.anker = tagPlus(a, richtung);
  else if (e === "woche") S.zeitraum.anker = tagPlus(a, 7 * richtung);
  else if (e === "monat") S.zeitraum.anker = new Date(a.getFullYear(), a.getMonth() + richtung, 1);
  else S.zeitraum.anker = new Date(a.getFullYear() + richtung, 0, 1);
}

// ---------------------------------------------------------------- Diagramm (SVG, ohne Bibliothek)
function balken(daten, opt = {}) {
  // daten: [{label, wert, schluessel}]
  const b = Math.round(Math.max(280, Math.min(728, ansicht.clientWidth - 64))), h = opt.hoehe || 150, unten = 18, oben = 8;
  const max = Math.max(opt.mindestMax ?? 1, ...daten.map((d) => d.wert));
  const n = daten.length, slot = b / Math.max(n, 1), breite = Math.max(2, Math.min(34, slot * 0.68));
  const werte = daten.filter((d) => d.wert > 0).map((d) => d.wert);
  const mittel = werte.length ? werte.reduce((x, y) => x + y, 0) / werte.length : 0;
  const y = (w) => oben + (h - unten - oben) * (1 - w / max);
  const jedes = Math.ceil(n / Math.min(opt.maxLabels || 12, Math.floor(b / 34)));
  let svg = `<svg class="diagramm" viewBox="0 0 ${b} ${h}" role="img" aria-label="${esc(opt.titel || "Diagramm")}">`;
  daten.forEach((d, i) => {
    const x = i * slot + (slot - breite) / 2, hoehe = Math.max(d.wert > 0 ? 2 : 1.5, h - unten - y(d.wert));
    svg += `<rect class="bar ${d.wert > 0 ? "" : "leer"} ${d.aktiv ? "aktiv" : ""}" data-i="${i}" x="${x.toFixed(1)}" y="${(h - unten - hoehe).toFixed(1)}" width="${breite.toFixed(1)}" height="${hoehe.toFixed(1)}" rx="${Math.min(4, breite / 3).toFixed(1)}"><title>${esc(d.label)}: ${(opt.format || geld)(d.wert)}</title></rect>`;
    if (i % jedes === 0) svg += `<text x="${(i * slot + slot / 2).toFixed(1)}" y="${h - 4}" text-anchor="middle">${esc(d.kurz ?? d.label)}</text>`;
  });
  if (mittel > 0 && n > 2) svg += `<line class="mittel" x1="0" x2="${b}" y1="${y(mittel).toFixed(1)}" y2="${y(mittel).toFixed(1)}"/>`;
  svg += "</svg>";
  return { svg, mittel };
}
function balkenKlicks(container, daten, info, oeffnen, format = geld) {
  $$("rect.bar", container).forEach((r) => {
    r.style.cursor = "pointer";
    r.addEventListener("click", () => {
      const d = daten[+r.dataset.i];
      $$("rect.bar", container).forEach((x) => x.classList.remove("aktiv"));
      r.classList.add("aktiv");
      info.innerHTML = `${esc(d.label)}: <b class="zahl">${format(d.wert)}</b>${oeffnen ? ' <button class="knopf zweit klein" style="margin-left:8px">Anzeigen</button>' : ""}`;
      if (oeffnen) $("button", info).onclick = () => oeffnen(d);
    });
  });
}

// ---------------------------------------------------------------- Uebersicht
async function uebersicht() {
  const z = zeitraum();
  const katFilter = S.bandAuswahl ? { kategorie: S.bandAuswahl } : {};
  ansicht.innerHTML = `
    <div class="kopf"><h1>Ausgaben</h1></div>
    <div class="segment" role="tablist">${["tag", "woche", "monat", "jahr"].map((e) =>
      `<button data-e="${e}" class="${S.zeitraum.einheit === e ? "aktiv" : ""}">${{ tag: "Tag", woche: "Woche", monat: "Monat", jahr: "Jahr" }[e]}</button>`).join("")}</div>
    <div class="zeitraum"><button class="rund" id="zurueck" aria-label="Vorheriger Zeitraum">‹</button>
      <div class="label">${esc(z.label)}</div>
      <button class="rund" id="vor" aria-label="Nächster Zeitraum" ${z.laufend ? "disabled" : ""}>›</button></div>
    <div id="inhalt"><div class="leer">Lade …</div></div>`;
  $$(".segment button").forEach((b) => (b.onclick = () => { S.zeitraum.einheit = b.dataset.e; S.bandAuswahl = null; uebersicht(); }));
  $("#zurueck").onclick = () => { verschieben(-1); uebersicht(); };
  $("#vor").onclick = () => { verschieben(1); uebersicht(); };

  const [kats, vor, reihe, liste] = await Promise.all([
    api("auswertung/kategorien?" + qs({ von: z.von, bis: z.bis })),
    api("eintraege?" + qs({ von: z.vorVon, bis: z.laufend ? isoTag(tagPlus(ausIso(z.vorVon), Math.floor((new Date() - z.vonD) / 86400000))) : z.vorBis, limit: 0, ...katFilter })),
    z.sub ? api("auswertung/zeitreihe?" + qs({ von: z.von, bis: z.bis, einheit: z.sub, ...katFilter })) : Promise.resolve([]),
    api("eintraege?" + qs({ von: z.von, bis: z.bis, limit: z.einheit === "tag" ? 200 : 30, ...katFilter })),
  ]);
  const gesamt = S.bandAuswahl ? (kats.kategorien.find((k) => String(k.id) === String(S.bandAuswahl))?.summe || 0) : kats.gesamt;
  const diff = gesamt - vor.summe;
  const vorText = { tag: "Vortag", woche: "Vorwoche", monat: "Vormonat", jahr: "Vorjahr" }[z.einheit];
  const vergleich = vor.summe > 0
    ? `<span class="${diff > 0 ? "hoch" : "runter"}">${diff > 0 ? "+" : "−"}${geld(Math.abs(diff))}</span> ggü. ${vorText}${z.laufend && z.einheit !== "tag" ? " bis heute" : ""}`
    : `${vorText}: keine Ausgaben`;
  const [ganz, cent] = geld(gesamt).split(",");
  const pruefen = S.meta.kennzahlen.pruefen;

  // Diagramm-Daten mit Luecken fuellen
  let diagramm = "";
  if (z.sub) {
    const map = Object.fromEntries(reihe.map((r) => [r.periode, r.summe]));
    const daten = [];
    if (z.sub === "tag") {
      for (let d = new Date(z.vonD); d <= z.bisD; d = tagPlus(d, 1)) {
        const k = isoTag(d);
        daten.push({ label: tagLabel(k), kurz: z.einheit === "woche" ? WT[d.getDay()] : String(d.getDate()), wert: map[k] || 0, schluessel: k });
      }
    } else {
      for (let m = 0; m < 12; m++) {
        const k = `${z.vonD.getFullYear()}-${pad(m + 1)}`;
        daten.push({ label: `${MONATE[m]} ${z.vonD.getFullYear()}`, kurz: MON_KURZ[m], wert: map[k] || 0, schluessel: k });
      }
    }
    const { svg, mittel } = balken(daten, { titel: "Ausgaben im Zeitraum", maxLabels: z.einheit === "monat" ? 8 : 12 });
    diagramm = `<div class="block"><h2>${z.sub === "tag" ? "Pro Tag" : "Pro Monat"}<span class="leise">Ø ${geld(mittel)}</span></h2>${svg}<div class="diagramm-info" id="dinfo">Tippe auf einen Balken für Details</div></div>`;
    S._diagrammDaten = daten;
  }

  const katListe = kats.kategorien.map((k) => `
    <button class="katzeile" data-k="${k.id ?? ""}">
      <span class="ic" style="${icFarbe(k.farbe)}">${esc(k.icon)}</span>
      <span><span class="name">${esc(k.name)}</span><span class="balken"><i style="width:${Math.max(1, k.anteil)}%;background:${k.farbe}"></i></span></span>
      <span class="wert zahl">${geld(k.summe)}<small>${k.anteil.toLocaleString("de-DE")} %, ${k.anzahl}×</small></span>
    </button>`).join("");

  $("#inhalt").innerHTML = `
    ${S.meta.kennzahlen.offen ? `<button class="hinweis offen" id="zum-offen"><b>${S.meta.kennzahlen.offen}</b><span>${S.meta.kennzahlen.offen === 1 ? "Eintrag ist" : "Einträge sind"} noch offen</span><span style="margin-left:auto">›</span></button>` : ""}
    ${pruefen ? `<button class="hinweis" id="zum-pruefen"><b>${pruefen}</b><span>${pruefen === 1 ? "Eintrag wartet" : "Einträge warten"} auf deine Bestätigung</span><span style="margin-left:auto">›</span></button>` : ""}
    <div class="gesamt">
      <div class="betrag zahl">${esc(ganz)}<small>,${esc(cent)}</small></div>
      <div class="vergleich">${S.bandAuswahl ? esc(kat(+S.bandAuswahl)?.name || "") + " – " : ""}${vergleich}</div>
    </div>
    <div class="band ${S.bandAuswahl ? "gewaehlt" : ""}" aria-label="Anteile nach Kategorie">${kats.kategorien.filter((k) => k.summe > 0).map((k) =>
      `<button style="flex-grow:${k.summe};background:${k.farbe}" class="${String(k.id) === String(S.bandAuswahl) ? "aktiv" : ""}" data-k="${k.id ?? ""}" title="${esc(k.name)}: ${geld(k.summe)}" aria-label="${esc(k.name)}"></button>`).join("")}</div>
    <div class="zweispaltig">
      ${diagramm || ""}
      <div class="block"><h2>Nach Kategorie<span class="leise">${kats.kategorien.length} Kategorien</span></h2>${katListe || '<div class="leer">Keine Ausgaben in diesem Zeitraum.</div>'}</div>
    </div>
    <div id="letzte"></div>`;

  $("#zum-pruefen")?.addEventListener("click", () => pruefenAnsicht());
  $("#zum-offen")?.addEventListener("click", offeneAnzeigen);
  $$(".band button").forEach((b) => (b.onclick = () => { S.bandAuswahl = S.bandAuswahl === b.dataset.k ? null : b.dataset.k; uebersicht(); }));
  $$(".katzeile").forEach((b) => (b.onclick = () => {
    Object.assign(S.suche, { q: "", kategorie: b.dataset.k, konto: "", pruefen: false, offen: false, von: z.von, bis: z.bis });
    tabWechseln("eintraege");
  }));
  if (z.sub) balkenKlicks($("#inhalt .block"), S._diagrammDaten, $("#dinfo"), (d) => {
    S.zeitraum = z.sub === "tag" ? { einheit: "tag", anker: ausIso(d.schluessel) } : { einheit: "monat", anker: ausIso(d.schluessel + "-01") };
    uebersicht();
  });
  const letzte = $("#letzte");
  letzte.innerHTML = `<div class="abschnitt" style="display:flex;justify-content:space-between"><span>${z.einheit === "tag" ? "Einträge" : "Letzte Einträge"}</span>${liste.anzahl > liste.eintraege.length ? `<button class="knopf zweit klein" id="alle">Alle ${liste.anzahl} zeigen</button>` : ""}</div>` + eintragsListe(liste.eintraege, z.einheit !== "tag");
  listeKlicks(letzte, liste.eintraege);
  wartendeEinbauen(letzte);
  $("#alle")?.addEventListener("click", () => {
    Object.assign(S.suche, { q: "", kategorie: S.bandAuswahl || "", konto: "", pruefen: false, offen: false, von: z.von, bis: z.bis });
    tabWechseln("eintraege");
  });
}

// ---------------------------------------------------------------- Listen
const eigen = (e) => (e.betrag_eur == null ? null : e.betrag_eur * (e.anteil ?? 1));
const geteilt = (e) => e.anteil != null && e.anteil < 0.9999;
function anteilText(e) {
  if (!geteilt(e)) return "";
  for (let n = 2; n <= 12; n++) for (let z = 1; z < n; z++) if (Math.abs(e.anteil - z / n) < 1e-6) return `${z}/${n}`;
  return Math.abs(e.betrag * e.anteil).toFixed(2).replace(".", ",");
}

function eintragZeile(e, mitDatum = false) {
  const k = kat(e.kategorie_id);
  const fremd = e.waehrung !== "EUR";
  const gut = e.betrag < 0;
  const datumText = mitDatum ? ausIso(e.datum).toLocaleDateString("de-DE", { day: "numeric", month: "short", year: "2-digit" }) : "";
  const unter = [datumText, k ? k.name : "Ohne Kategorie", e.konto && e.konto !== "Altdaten (Next)" ? e.konto : "", e.datum.slice(11, 16) !== "00:00" ? e.datum.slice(11, 16) : ""].filter(Boolean).join(", ");
  return `<div class="wisch" data-id="${e.id}"><button class="wisch-loeschen" tabindex="-1"><span>Löschen</span></button><button class="eintrag" data-id="${e.id}">
    <span class="ic" style="${icFarbe(k?.farbe || "#9AA3AE")}">${esc(k?.icon || "❔")}</span>
    <span style="min-width:0"><span class="titel">${esc(e.bezeichnung || e.haendler_roh || "Ohne Bezeichnung")}${e.pruefen ? '<span class="marke pruefen">prüfen</span>' : ""}${e.offen ? '<span class="marke offen">offen</span>' : ""}</span>
      <span class="unter">${esc(unter)}${e.notiz ? " – " + esc(e.notiz) : ""}</span></span>
    <span class="wert zahl ${gut ? "gut" : ""}">${gut ? "+" + geld(-(eigen(e) ?? 0)) : geld(eigen(e) ?? 0)}${geteilt(e) ? `<small>von ${geld(e.betrag_eur ?? e.betrag, e.betrag_eur != null ? "EUR" : e.waehrung)}</small>` : fremd ? `<small>${geld(e.betrag, e.waehrung)}</small>` : ""}${e.betrag_eur == null ? "<small>Kurs fehlt</small>" : ""}</span>
  </button></div>`;
}
function eintragsListe(eintraege, nachTag = true) {
  if (!eintraege.length) return '<div class="leer">Keine Einträge.</div>';
  if (!nachTag) return `<div class="liste">${eintraege.map((e) => eintragZeile(e, true)).join("")}</div>`;
  const gruppen = [];
  for (const e of eintraege) {
    const t = e.datum.slice(0, 10);
    if (!gruppen.length || gruppen.at(-1).tag !== t) gruppen.push({ tag: t, liste: [] });
    gruppen.at(-1).liste.push(e);
  }
  return gruppen.map((g) => `<div class="tag-kopf"><span>${tagLabel(g.tag)}</span><span class="zahl">${geld(g.liste.reduce((s, e) => s + (eigen(e) || 0), 0))}</span></div>
    <div class="liste">${g.liste.map(eintragZeile).join("")}</div>`).join("");
}
// Offline erfasste Einträge, die noch auf Übertragung warten (oben in den Listen)
function wartendeListe() {
  const l = wsLesen();
  if (!l.length) return "";
  const zeilen = l.map((x) => {
    const d = x.daten || {};
    const k = x.art === "eintrag" ? kat(d.kategorie_id) : null;
    const zeit = x.art === "eintrag" ? d.datum : x.datum;
    const wann = zeit ? ausIso(zeit).toLocaleDateString("de-DE", { day: "numeric", month: "short" }) + (zeit.slice(11, 16) && zeit.slice(11, 16) !== "00:00" ? ", " + zeit.slice(11, 16) : "") : "";
    const titel = x.art === "eintrag" ? (d.bezeichnung || k?.name || "Ohne Bezeichnung") : "Kassenbon-Foto";
    const wert = x.art === "eintrag" ? (d.betrag < 0 ? "+" + geld(-d.betrag, d.waehrung) : geld(d.betrag, d.waehrung)) : "–";
    return `<div class="wisch" data-ref="${esc(String(x.zeit))}"><button class="wisch-loeschen" tabindex="-1"><span>Löschen</span></button><button class="eintrag wartend" data-ref="${esc(String(x.zeit))}">
      <span class="ic" style="${icFarbe(k?.farbe || "#9AA3AE")}">${esc(x.art === "beleg" ? "🧾" : k?.icon || "❔")}</span>
      <span style="min-width:0"><span class="titel">${esc(titel)}<span class="marke wartet">wartet</span></span>
        <span class="unter">${esc([wann, k?.name, x.art === "beleg" ? "wird danach ausgelesen" : ""].filter(Boolean).join(", "))}</span></span>
      <span class="wert zahl">${esc(wert)}</span></button></div>`;
  }).join("");
  return `<div class="tag-kopf"><span>Noch nicht übertragen</span><span class="zahl">${l.length}</span></div><div class="liste">${zeilen}</div>`;
}
function wartendeEinbauen(container) {
  if (!container) return;
  container.querySelector(".wartend-block")?.remove();
  const html = wartendeListe();
  if (!html) return;
  const div = document.createElement("div");
  div.className = "wartend-block"; div.innerHTML = html;
  container.prepend(div);
  $$(".eintrag", div).forEach((b) => (b.onclick = () => {
    const w = b.closest(".wisch");
    if (w.dataset.gewischt || w.classList.contains("offen")) { wischSchliessen(w); return; }
    toast("Wird automatisch übertragen, sobald wieder Verbindung besteht. Zum Verwerfen nach links wischen.");
  }));
  wischenAktivieren(div);
}
function listeKlicks(container, eintraege) {
  $$(".eintrag", container).forEach((b) => (b.onclick = () => {
    const w = b.closest(".wisch");
    if (w && (w.dataset.gewischt || w.classList.contains("offen"))) { wischSchliessen(w); return; }
    erfassen(eintraege.find((e) => e.id === +b.dataset.id));
  }));
  wischenAktivieren(container);
}

// Wischen nach links zum Löschen (wie in iOS): kurz wischen zeigt „Löschen“, ganz durchwischen löscht sofort.
const WISCH_BREITE = 88;
let wischOffen = null;
function wischSetzen(w, x) {
  w.querySelector(".eintrag").style.transform = x ? `translateX(${x}px)` : "";
  w.querySelector(".wisch-loeschen").style.width = Math.max(0, -x) + "px";
}
function wischSchliessen(w) {
  if (!w) return;
  wischSetzen(w, 0);
  w.classList.remove("offen", "voll");
  if (wischOffen === w) wischOffen = null;
}
function wischenAktivieren(container) {
  $$(".wisch", container).forEach((w) => {
    const zeile = w.querySelector(".eintrag");
    let x0 = 0, y0 = 0, dx = 0, richtung = null, basis = 0;
    zeile.addEventListener("touchstart", (ev) => {
      const t = ev.touches[0];
      x0 = t.clientX; y0 = t.clientY; dx = 0; richtung = null;
      basis = w.classList.contains("offen") ? -WISCH_BREITE : 0;
      if (wischOffen && wischOffen !== w) wischSchliessen(wischOffen);
      zeile.style.transition = "none";
      w.querySelector(".wisch-loeschen").style.transition = "none";
    }, { passive: true });
    zeile.addEventListener("touchmove", (ev) => {
      const t = ev.touches[0], mx = t.clientX - x0, my = t.clientY - y0;
      if (!richtung) {
        if (Math.abs(mx) < 8 && Math.abs(my) < 8) return;
        richtung = Math.abs(mx) > Math.abs(my) ? "h" : "v";
      }
      if (richtung !== "h") return;
      ev.preventDefault();
      dx = Math.min(0, basis + mx);
      wischSetzen(w, dx);
      w.classList.toggle("voll", dx < -w.offsetWidth * 0.6);
    }, { passive: false });
    const ende = () => {
      zeile.style.transition = "";
      w.querySelector(".wisch-loeschen").style.transition = "";
      if (richtung !== "h") return;
      w.dataset.gewischt = "1";
      setTimeout(() => delete w.dataset.gewischt, 80);
      if (dx < -w.offsetWidth * 0.6) wischLoeschen(w);
      else if (dx < -WISCH_BREITE / 2) { wischSetzen(w, -WISCH_BREITE); w.classList.add("offen"); wischOffen = w; }
      else wischSchliessen(w);
    };
    zeile.addEventListener("touchend", ende);
    zeile.addEventListener("touchcancel", ende);
    w.querySelector(".wisch-loeschen").onclick = (ev) => { ev.stopPropagation(); wischLoeschen(w); };
  });
}
async function wischLoeschen(w) {
  wischSetzen(w, -w.offsetWidth);
  if (w.dataset.ref) { // noch nicht übertragen: nur aus der Warteschlange nehmen
    wsSchreiben(wsLesen().filter((x) => String(x.zeit) !== w.dataset.ref));
    w.style.height = w.offsetHeight + "px";
    requestAnimationFrame(() => w.classList.add("weg"));
    wischOffen = null; leisteZeigen();
    toast("Offline-Eintrag verworfen");
    setTimeout(() => { const b = w.closest(".wartend-block"); if (b && !wsLesen().length) b.remove(); }, 320);
    return;
  }
  try {
    await api(`eintraege/${w.dataset.id}`, { method: "DELETE" });
  } catch (err) {
    toast(err.message); wischSchliessen(w); return;
  }
  w.style.height = w.offsetHeight + "px";
  requestAnimationFrame(() => w.classList.add("weg"));
  wischOffen = null;
  toast("Eintrag gelöscht");
  setTimeout(async () => { try { await metaLaden(); neuZeichnen(); } catch (_) { /* egal */ } }, 320);
}

// ---------------------------------------------------------------- Eintraege + Suche
async function eintraegeAnsicht() {
  const s = S.suche;
  const kategorieChips = S.meta.kategorien.filter((k) => !k.archiviert && k.anzahl > 0);
  ansicht.innerHTML = `
    <div class="kopf"><h1>Einträge</h1><button class="knopf zweit klein" id="export">Exportieren</button></div>
    <div class="suche">
      <div class="suchfeld"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="color:var(--leise)"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
        <input id="q" type="search" placeholder="Suchen: Bezeichnung, Händler, Betrag" value="${esc(s.q)}" enterkeyhint="search"></div>
      <div class="chips">
        <button class="chip ${s.sortierung ? "aktiv" : ""}" id="f-sort">↕ ${esc(SORTIERUNGEN.find((x) => x[0] === s.sortierung)[1])}</button>
        ${filterAktiv() ? '<button class="chip" id="f-reset">✕ Filter zurücksetzen</button>' : ""}
        <button class="chip ${s.pruefen ? "aktiv" : ""}" id="f-pruefen">Zu prüfen</button>
        <button class="chip ${s.offen ? "aktiv" : ""}" id="f-offen">Noch offen</button>
        <button class="chip ${s.von || s.bis ? "aktiv" : ""}" id="f-zeit">${s.von || s.bis ? esc(zeitText(s.von, s.bis)) : "Zeitraum"}</button>
        <select class="chip ${s.konto ? "aktiv" : ""}" id="f-konto"><option value="">Alle Konten</option>${S.meta.konten.map((k) => `<option value="${k.id}" ${String(k.id) === String(s.konto) ? "selected" : ""}>${esc(k.name)}</option>`).join("")}</select>
        ${kategorieChips.map((k) => `<button class="chip ${String(k.id) === String(s.kategorie) ? "aktiv" : ""}" data-k="${k.id}">${esc(k.icon)} ${esc(k.name)}</button>`).join("")}
      </div>
      <div class="summenzeile"><span id="anzahl"></span><span id="summe" class="zahl"></span></div>
    </div>
    <div id="liste"></div>
    <div style="text-align:center;margin:16px 0"><button class="knopf zweit" id="mehr-laden" hidden>Weitere laden</button></div>`;
  let timer;
  $("#q").oninput = (ev) => { clearTimeout(timer); timer = setTimeout(() => { s.q = ev.target.value; listeNeu(); }, 250); };
  $("#f-pruefen").onclick = () => { s.pruefen = !s.pruefen; eintraegeAnsicht(); };
  $("#f-offen").onclick = () => { s.offen = !s.offen; eintraegeAnsicht(); };
  $("#f-konto").onchange = (ev) => { s.konto = ev.target.value; eintraegeAnsicht(); };
  $("#f-zeit").onclick = () => zeitraumWaehlen();
  $("#f-sort").onclick = () => sortierungWaehlen();
  $("#f-reset")?.addEventListener("click", () => { Object.assign(s, { q: "", kategorie: "", konto: "", pruefen: false, offen: false, von: "", bis: "" }); eintraegeAnsicht(); });
  $$(".chip[data-k]").forEach((b) => (b.onclick = () => { s.kategorie = String(s.kategorie) === b.dataset.k ? "" : b.dataset.k; eintraegeAnsicht(); }));
  $("#export").onclick = () => exportSheet({ ...s });
  $("#mehr-laden").onclick = () => listeLaden();
  ($(".chip.aktiv[data-k]") || (s.offen ? $("#f-offen") : null))?.scrollIntoView({ inline: "center", block: "nearest" });
  await listeNeu();
}
const SORTIERUNGEN = [["", "Neueste zuerst"], ["alt", "Älteste zuerst"], ["betrag", "Höchster Betrag zuerst"]];
function filterAktiv() {
  const s = S.suche;
  return !!(s.q || s.kategorie || s.konto || s.pruefen || s.offen || s.von || s.bis);
}
function sortierungWaehlen() {
  const s = S.suche;
  sheetOeffnen(`<div class="griff"></div><div class="sheet-kopf"><button data-zu>Abbrechen</button><h3>Sortieren</h3><span style="width:60px"></span></div>
    <div class="mehr-liste">${SORTIERUNGEN.map(([wert, text]) => `<button data-s="${wert}" class="${wert === s.sortierung ? "gewaehlt" : ""}">${text}<span class="rechts haken">${wert === s.sortierung ? "✓" : ""}</span></button>`).join("")}</div>`);
  $$("#sheet [data-s]").forEach((b) => (b.onclick = () => { s.sortierung = b.dataset.s; sheetSchliessen(); eintraegeAnsicht(); }));
}
function zeitText(von, bis) {
  const f = (x) => x ? ausIso(x).toLocaleDateString("de-DE", { day: "numeric", month: "short", year: "2-digit" }) : "…";
  return `${f(von)} – ${f(bis)}`;
}
async function listeNeu() { S.liste = { eintraege: [], offset: 0, fertig: false }; $("#liste").innerHTML = ""; await listeLaden(); }
async function listeLaden() {
  const s = S.suche;
  const r = await api("eintraege?" + qs({ q: s.q, kategorie: s.kategorie, konto: s.konto, pruefen: s.pruefen ? 1 : "", offen: s.offen ? 1 : "", von: s.von, bis: s.bis, sortierung: s.sortierung, limit: 150, offset: S.liste.offset }));
  S.liste.eintraege.push(...r.eintraege);
  S.liste.offset += r.eintraege.length;
  S.liste.fertig = S.liste.offset >= r.anzahl;
  $("#anzahl").textContent = `${r.anzahl.toLocaleString("de-DE")} ${r.anzahl === 1 ? "Eintrag" : "Einträge"}`;
  $("#summe").textContent = geld(r.summe);
  $("#liste").innerHTML = eintragsListe(S.liste.eintraege, s.sortierung !== "betrag");
  listeKlicks($("#liste"), S.liste.eintraege);
  if (!s.q && !s.kategorie && !s.konto && !s.pruefen && !s.offen && !s.von && !s.bis) wartendeEinbauen($("#liste"));
  $("#mehr-laden").hidden = S.liste.fertig;
}
function zeitraumWaehlen() {
  const s = S.suche, heute = new Date();
  const vorlagen = [
    ["Dieser Monat", isoTag(new Date(heute.getFullYear(), heute.getMonth(), 1)), isoTag(heute)],
    ["Letzter Monat", isoTag(new Date(heute.getFullYear(), heute.getMonth() - 1, 1)), isoTag(new Date(heute.getFullYear(), heute.getMonth(), 0))],
    ["Letzte 30 Tage", isoTag(tagPlus(heute, -29)), isoTag(heute)],
    ["Dieses Jahr", `${heute.getFullYear()}-01-01`, isoTag(heute)],
    ["Letztes Jahr", `${heute.getFullYear() - 1}-01-01`, `${heute.getFullYear() - 1}-12-31`],
  ];
  sheetOeffnen(`<div class="griff"></div><div class="sheet-kopf"><button data-zu>Abbrechen</button><h3>Zeitraum</h3><button class="fett" id="ok">Fertig</button></div>
    <div class="chips" style="flex-wrap:wrap">${vorlagen.map((v, i) => `<button class="chip" data-v="${i}">${v[0]}</button>`).join("")}<button class="chip" data-v="alle">Alles</button></div>
    <div class="zwei" style="margin-top:12px"><label class="feld"><span>Von</span><input type="date" id="von" value="${esc(s.von)}"></label>
    <label class="feld"><span>Bis</span><input type="date" id="bis" value="${esc(s.bis)}"></label></div>`);
  const markieren = () => {
    const von = $("#von").value, bis = $("#bis").value;
    $$("#sheet [data-v]").forEach((b) => {
      const v = b.dataset.v === "alle" ? ["", "", ""] : vorlagen[+b.dataset.v];
      b.classList.toggle("aktiv", v[1] === von && v[2] === bis);
    });
  };
  $$("#sheet [data-v]").forEach((b) => (b.onclick = () => {
    const v = b.dataset.v === "alle" ? ["", "", ""] : vorlagen[+b.dataset.v];
    $("#von").value = v[1]; $("#bis").value = v[2];
    markieren();
  }));
  ["input", "change"].forEach((ev) => { $("#von").addEventListener(ev, markieren); $("#bis").addEventListener(ev, markieren); });
  markieren();
  $("#ok").onclick = () => { s.von = $("#von").value; s.bis = $("#bis").value; sheetSchliessen(); eintraegeAnsicht(); };
}

// ---------------------------------------------------------------- Pruefen
// Alle „Noch offen“-Einträge: Einträge-Liste mit diesem Filter
function offeneAnzeigen() {
  Object.assign(S.suche, { q: "", kategorie: "", konto: "", pruefen: false, offen: true, von: "", bis: "" });
  tabWechseln("eintraege");
}
async function pruefenAnsicht() {
  S.tab = "pruefen";
  $$(".tabs [data-tab]").forEach((b) => b.classList.toggle("aktiv", b.dataset.tab === "uebersicht"));
  const r = await api("eintraege?pruefen=1&limit=300");
  const kats = S.meta.kategorien.filter((k) => !k.archiviert);
  ansicht.innerHTML = `<div class="kopf"><button class="rund" id="zurueck" aria-label="Zurück">‹</button><h1>Prüfen</h1>
      ${r.anzahl ? '<button class="knopf zweit klein" id="alle-ok">Alle bestätigen</button>' : ""}</div>
    <p class="leise" style="margin:0 4px 8px;font-size:14px">Automatisch erkannte Ausgaben, bei denen Kategorie oder Bezeichnung unsicher ist. Tippe auf eine Kategorie zum Bestätigen oder auf den Eintrag für Details.</p>
    ${r.eintraege.map((e) => {
      const k = kat(e.kategorie_id);
      return `<div class="pruefkarte" data-id="${e.id}">
        <div class="oben"><div style="min-width:0"><div class="titel"><b>${esc(e.bezeichnung || e.haendler_roh)}</b></div>
          <div class="roh">${e.haendler_roh && e.haendler_roh !== e.bezeichnung ? esc(e.haendler_roh) + ", " : ""}${tagLabel(e.datum)} ${e.datum.slice(11, 16)}${e.ki_sicherheit != null ? `, ${Math.round(e.ki_sicherheit * 100)} % sicher` : ""}</div></div>
          <div class="wert zahl"><b>${geld(e.betrag, e.waehrung)}</b></div></div>
        <div class="chips">${k ? `<button class="chip vorschlag" data-k="${k.id}">✓ ${esc(k.icon)} ${esc(k.name)}</button>` : ""}
          ${kats.filter((x) => x.id !== k?.id).map((x) => `<button class="chip" data-k="${x.id}">${esc(x.icon)} ${esc(x.name)}</button>`).join("")}</div>
      </div>`;
    }).join("") || '<div class="leer">Alles erledigt. Neue unsichere Umsätze erscheinen hier automatisch.</div>'}`;
  $("#zurueck").onclick = () => tabWechseln("uebersicht");
  $("#alle-ok")?.addEventListener("click", async () => { await api("pruefen/alle_bestaetigen", { method: "POST", body: {} }); await metaLaden(); toast("Alle bestätigt"); pruefenAnsicht(); });
  $$(".pruefkarte").forEach((karte) => {
    const e = r.eintraege.find((x) => x.id === +karte.dataset.id);
    $(".oben", karte).onclick = () => erfassen(e);
    $$(".chip", karte).forEach((b) => (b.onclick = async () => {
      if (+b.dataset.k === e.kategorie_id) await api(`eintraege/${e.id}/bestaetigen`, { method: "POST" });
      else await api(`eintraege/${e.id}`, { method: "PUT", body: { kategorie_id: +b.dataset.k } });
      karte.remove();
      S.meta.kennzahlen.pruefen = Math.max(0, S.meta.kennzahlen.pruefen - 1);
      toast(`${e.bezeichnung || e.haendler_roh}: ${kat(+b.dataset.k)?.name}`);
    }));
  });
}

// ---------------------------------------------------------------- Erfassen / Bearbeiten
function erfassen(e = null) {
  const neu = !e;
  const jetzt = new Date();
  const d = e ? { ...e } : {
    datum: `${isoTag(jetzt)}T${pad(jetzt.getHours())}:${pad(jetzt.getMinutes())}`, betrag: "", waehrung: S.letzteWaehrung,
    kategorie_id: null, bezeichnung: "", notiz: "", konto_id: +S.letztesKonto || S.meta.konten.find((k) => k.name === "Bargeld")?.id,
  };
  const erstattung = d.betrag !== "" && d.betrag < 0;
  const kats = S.meta.kategorien.filter((k) => !k.archiviert || k.id === d.kategorie_id);
  const herkunft = e && e.quelle !== "manuell" ? `<div class="herkunft">
      ${e.quelle === "tr" ? "Automatisch von Trade Republic" : e.quelle === "next" ? "Aus Next übernommen" : esc(e.quelle)}
      ${e.haendler_roh ? `<br>Händler laut Bank: <b>${esc(e.haendler_roh)}</b>` : ""}
      ${e.kategorisiert_durch ? `<br>Kategorie per ${{ ki: "KI", regel: "gelernter Regel", verlauf: "Verlauf", nutzer: "Hand", next: "Next" }[e.kategorisiert_durch] || e.kategorisiert_durch}${e.ki_sicherheit != null && e.kategorisiert_durch !== "nutzer" ? ` (${Math.round(e.ki_sicherheit * 100)} % sicher)` : ""}` : ""}
    </div>` : "";
  sheetOeffnen(`<div class="griff"></div>
    <div class="sheet-kopf"><button data-zu>Abbrechen</button><h3>${neu ? "Neue Ausgabe" : "Eintrag"}</h3><button class="fett" id="speichern">Sichern</button></div>
    ${neu ? `<button class="beleg-knopf" id="beleg"><span aria-hidden="true">📷</span>Kassenbon scannen</button><input type="file" id="beleg-datei" accept="image/*" hidden><div id="beleg-info"></div>` : ""}
    <div class="betragseingabe"><input id="betrag" inputmode="decimal" placeholder="0,00" autocomplete="off" value="${d.betrag === "" ? "" : esc(Math.abs(d.betrag).toFixed(2).replace(".", ","))}" aria-label="Betrag">
      <select id="waehrung" aria-label="Währung">${S.meta.waehrungen.map((w) => `<option ${w === d.waehrung ? "selected" : ""}>${w}</option>`).join("")}</select></div>
    <div class="umrechnung" id="umrechnung"></div>
    <div class="vorzeichen"><div class="segment" style="width:240px"><button data-v="aus" class="${erstattung ? "" : "aktiv"}">Ausgabe</button><button data-v="ein" class="${erstattung ? "aktiv" : ""}">Erstattung</button></div></div>
    <label class="feld"><span>Bezeichnung</span><input id="bezeichnung" autocomplete="off" value="${esc(d.bezeichnung)}" placeholder="z. B. Bäcker, Tanken, Geschenk"></label>
    <div id="vorschlaege"></div>
    <div class="katgitter" id="katgitter">${kats.map((k) => `<button data-k="${k.id}" class="${k.id === d.kategorie_id ? "aktiv" : ""}" style="${k.id === d.kategorie_id ? "" : ""}"><span>${esc(k.icon)}</span>${esc(k.name)}</button>`).join("")}<button class="kat-neu" id="kat-neu" type="button"><span>＋</span>Neue Kategorie</button></div>
    <div class="kat-neu-form" id="kat-neu-form" hidden>
      <div class="zwei" style="grid-template-columns:64px 1fr"><label class="feld"><span>Symbol</span><input id="kn-icon" value="🏷️" style="text-align:center;font-size:21px" autocomplete="off"></label>
        <label class="feld"><span>Name</span><input id="kn-name" autocomplete="off" placeholder="z. B. Baumarkt"></label></div>
      <div class="knopfreihe"><button class="knopf zweit" id="kn-ab" type="button">Abbrechen</button><button class="knopf" id="kn-ok" type="button">Anlegen</button></div>
    </div>
    <div class="zwei"><label class="feld"><span>Datum und Uhrzeit</span><input id="datum" type="datetime-local" value="${esc(d.datum.slice(0, 16))}"></label>
      <label class="feld"><span>Konto</span><select id="konto">${S.meta.konten.filter((k) => !k.archiviert || k.id === d.konto_id).map((k) => `<option value="${k.id}" ${k.id === d.konto_id ? "selected" : ""}>${esc(k.icon)} ${esc(k.name)}</option>`).join("")}</select></label></div>
    <label class="feld"><span>Notiz</span><input id="notiz" value="${esc(d.notiz || "")}" placeholder="optional"></label>
    ${checkZeile("offen", !!(e && e.offen), "<b>Noch offen</b> – Betrag kann sich noch ändern oder der Eintrag entfällt evtl.")}
    <label class="feld"><span>Mein Anteil (leer = voller Betrag)</span><input id="anteil" autocomplete="off" value="${esc(e ? anteilText(e) : "")}" placeholder="z. B. 1/6, 50 % oder 2,67"></label>
    <div class="anteil-info" id="anteil-info"></div>
    ${(e && (e.haendler_roh || e.bezeichnung)) || neu ? `<div id="anteil-optionen">${checkZeile("anteil-regel", true, `<span id="anteil-regel-text"></span>`)}
      <div style="margin-left:28px">${checkZeile("anteil-alle", false, "Auch alle bisherigen Einträge umstellen")}</div></div>` : ""}
    <label class="feld" id="kurs-feld" hidden><span>Kurs (EUR je 1 Einheit, leer = automatisch)</span><input id="kurs" inputmode="decimal" value="${e?.kurs && e.waehrung !== "EUR" ? String(e.kurs).replace(".", ",") : ""}"></label>
    ${herkunft}
    ${!neu && e.haendler_roh ? `<label style="display:flex;gap:8px;align-items:center;font-size:14px;margin:8px 2px"><input type="checkbox" id="regel" checked> Für „${esc(e.haendler_roh)}“ künftig genauso einordnen</label>` : ""}
    ${!neu ? `<div class="knopfreihe"><button class="knopf zweit" id="aufteilen">Aufteilen</button><button class="knopf gefahr" id="loeschen">Löschen</button></div>` : ""}`);

  let katId = d.kategorie_id;
  let vorzeichen = erstattung ? -1 : 1;
  const betragFeld = $("#betrag");
  if (neu) setTimeout(() => betragFeld.focus(), 280);
  const katWaehlen = (b) => {
    katId = +b.dataset.k;
    $$("#katgitter button").forEach((x) => x.classList.toggle("aktiv", x === b));
  };
  $$("#katgitter button[data-k]").forEach((b) => (b.onclick = () => katWaehlen(b)));
  // Neue Kategorie direkt hier anlegen, ohne die Erfassung zu verlassen
  const knForm = $("#kat-neu-form");
  $("#kat-neu").onclick = () => {
    knForm.hidden = !knForm.hidden;
    if (!knForm.hidden) { setTimeout(() => $("#kn-name").focus(), 50); }
  };
  $("#kn-ab").onclick = () => (knForm.hidden = true);
  $("#kn-ok").onclick = async () => {
    const name = $("#kn-name").value.trim();
    if (!name) { toast("Bitte einen Namen eingeben"); return; }
    const belegt = new Set(S.meta.kategorien.map((k) => k.farbe));
    const farbe = FARBEN.find((f) => !belegt.has(f)) || FARBEN[S.meta.kategorien.length % FARBEN.length];
    $("#kn-ok").disabled = true;
    try {
      const k = await api("kategorien", { method: "POST", body: { name, icon: $("#kn-icon").value.trim() || "🏷️", farbe } });
      try { await metaLaden(); } catch (_) { S.meta.kategorien.push({ ...k, anzahl: 0 }); }
      const b = document.createElement("button");
      b.dataset.k = k.id; b.innerHTML = `<span>${esc(k.icon)}</span>${esc(k.name)}`;
      b.onclick = () => katWaehlen(b);
      $("#kat-neu").before(b); katWaehlen(b);
      knForm.hidden = true; $("#kn-icon").value = "🏷️";
      toast(`Kategorie „${k.name}“ angelegt`);
    } catch (err) {
      toast(err.offline ? "Neue Kategorien gehen nur mit Verbindung" : err.message);
    } finally { $("#kn-ok").disabled = false; }
  };
  $$(".vorzeichen button").forEach((b) => (b.onclick = () => {
    vorzeichen = b.dataset.v === "ein" ? -1 : 1;
    $$(".vorzeichen button").forEach((x) => x.classList.toggle("aktiv", x === b));
  }));
  const umrechnungZeigen = async () => {
    const w = $("#waehrung").value, b = zahlLesen(betragFeld.value);
    $("#kurs-feld").hidden = w === "EUR";
    if (w === "EUR" || !b) { $("#umrechnung").textContent = ""; return; }
    const manuell = zahlLesen($("#kurs").value);
    let k = manuell;
    if (!k) { try { k = (await api("kurs?" + qs({ waehrung_code: w, datum: $("#datum").value.slice(0, 10) }))).kurs; } catch (_) { k = null; } }
    $("#umrechnung").textContent = k ? `≈ ${geld(b * k)} (1 ${w} = ${k.toLocaleString("de-DE", { maximumFractionDigits: 4 })} €)` : "Kurs wird nachgeladen, sobald verfügbar";
  };
  ["input", "change"].forEach((ev) => { betragFeld.addEventListener(ev, umrechnungZeigen); $("#waehrung").addEventListener(ev, umrechnungZeigen); $("#kurs").addEventListener(ev, umrechnungZeigen); });
  umrechnungZeigen();

  const katSetzen = (id) => { katId = id; $$("#katgitter button").forEach((y) => y.classList.toggle("aktiv", +y.dataset.k === katId)); };
  const belegUebernehmen = (r, vorschau) => {
    const info = $("#beleg-info");
    if (!r.lesbar) { info.innerHTML = `<div class="hinweis"><span>${esc(r.hinweis)}</span></div>`; return; }
    if (r.betrag != null) {
      betragFeld.value = Math.abs(r.betrag).toFixed(2).replace(".", ",");
      vorzeichen = r.betrag < 0 ? -1 : 1;
      $$(".vorzeichen button").forEach((x) => x.classList.toggle("aktiv", (x.dataset.v === "ein") === (vorzeichen < 0)));
    }
    $("#waehrung").value = r.waehrung;
    if (r.datum) $("#datum").value = `${r.datum}T${r.uhrzeit || "12:00"}`;
    if (r.bezeichnung) $("#bezeichnung").value = r.bezeichnung;
    if (r.kategorie_id && kat(r.kategorie_id)) katSetzen(r.kategorie_id);
    if (r.konto_id && $(`#konto option[value="${r.konto_id}"]`)) $("#konto").value = r.konto_id;
    umrechnungZeigen();
    const teile = [
      r.haendler ? `<b>${esc(r.haendler)}</b>` : "",
      r.betrag != null ? geld(r.betrag, r.waehrung) : '<span style="color:var(--pruefen)">Summe nicht erkannt</span>',
      r.datum ? ausIso(r.datum).toLocaleDateString("de-DE") + (r.uhrzeit ? `, ${r.uhrzeit}` : "") : '<span style="color:var(--pruefen)">Datum nicht erkannt</span>',
      { bar: "bar", karte: "Karte" }[r.zahlungsart] || "",
    ].filter(Boolean);
    const herkunftKat = r.kategorisiert_durch
      ? `Kategorie ${{ regel: "aus gelernter Zuordnung", verlauf: "aus deinem Verlauf", ki: "von der KI" }[r.kategorisiert_durch]}${r.sicherheit != null && r.kategorisiert_durch !== "regel" ? ` (${Math.round(r.sicherheit * 100)} % sicher)` : ""}`
      : "Kategorie bitte selbst wählen";
    let html = `<div class="beleg-ergebnis"><img src="${vorschau}" alt="Kassenbon"><div>${teile.join(" · ")}<br><span class="leise">${esc(herkunftKat)}. Bitte kurz prüfen.</span>${r.hinweis ? `<br><span style="color:var(--pruefen)">${esc(r.hinweis)}</span>` : ""}</div></div>`;
    if (r.duplikate.length) {
      html += `<div class="hinweis beleg-dup"><div style="flex:1;min-width:0"><b>Schon erfasst?</b> Gleicher Betrag in ±2 Tagen:
        ${r.duplikate.map((x) => `<div class="beleg-dup-zeile"><span>${esc(x.bezeichnung || x.haendler_roh || "")} · ${ausIso(x.datum).toLocaleDateString("de-DE")} · ${esc(QUELLEN[x.quelle] || x.quelle)}</span><button class="knopf zweit klein" data-dup="${x.id}">Öffnen</button></div>`).join("")}</div></div>`;
    } else if (r.zahlungsart === "karte" && (S.meta.tr.eingerichtet || S.meta.banken.anzahl)) {
      html += `<p class="leise beleg-tipp">Mit Karte bezahlt: Zahlungen über Trade Republic oder eine verbundene Bank kommen ohnehin automatisch. Sicherst du den Bon trotzdem, legt das Add-on den automatischen Umsatz später zur Prüfung vor, statt doppelt zu zählen.</p>`;
    }
    if (r.positionen.length) {
      html += `<details class="erweitert"><summary>${r.positionen.length} ${r.positionen.length === 1 ? "Position" : "Positionen"} erkannt</summary>
        <ul class="beleg-pos">${r.positionen.map((x) => `<li><span>${esc(x.text)}</span><span>${x.betrag != null ? geld(x.betrag, r.waehrung) : ""}</span></li>`).join("")}</ul>
        <button class="knopf zweit klein" id="pos-notiz">In die Notiz übernehmen</button></details>`;
    }
    info.innerHTML = html;
    $$("[data-dup]", info).forEach((b) => (b.onclick = () => { const x = r.duplikate.find((y) => y.id === +b.dataset.dup); sheetSchliessen(); erfassen(x); }));
    $("#pos-notiz")?.addEventListener("click", () => {
      $("#notiz").value = r.positionen.map((x) => x.text + (x.betrag != null ? " " + x.betrag.toFixed(2).replace(".", ",") : "")).join(", ").slice(0, 500);
      toast("Positionen in die Notiz übernommen");
    });
  };
  if (neu) {
    $("#beleg").onclick = () => {
      if (!S.meta.ki.aktiv && !S.offline) { toast("Zum Einlesen von Kassenbons wird die KI gebraucht (API-Key in den Add-on-Optionen)"); return; }
      betragFeld.blur();
      $("#beleg-datei").click();
    };
    $("#beleg-datei").onchange = async (ev) => {
      const datei = ev.target.files?.[0];
      ev.target.value = "";
      if (!datei) return;
      const vorschau = URL.createObjectURL(datei);
      $("#beleg-info").innerHTML = `<div class="beleg-ergebnis"><img src="${vorschau}" alt=""><div><b>Lese Kassenbon …</b><br><span class="leise">Dauert ein paar Sekunden.</span></div><span class="kreisel" aria-hidden="true"></span></div>`;
      $("#beleg").disabled = true;
      let bild = null;
      try {
        bild = await bildVerkleinern(datei);
        const r = await api("beleg", { method: "POST", body: { bild }, zeitlimit: S.offline ? 4000 : 90000 });
        if ($("#beleg-info")) belegUebernehmen(r, vorschau);
      } catch (err) {
        if (err.offline && bild) {
          try {
            inWarteschlange({ art: "beleg", ref: neueRef(), bild, datum: $("#datum").value, konto_id: +$("#konto").value });
            sheetSchliessen();
            toast("Offline: Foto gespeichert. Es wird eingelesen und unter „Prüfen“ angelegt, sobald Verbindung besteht");
            wartendeAktualisieren();
            return;
          } catch (e2) { err = e2; }
        }
        if ($("#beleg-info")) $("#beleg-info").innerHTML = `<div class="hinweis"><span>${esc(err.message)}</span></div>`;
      } finally {
        if ($("#beleg")) $("#beleg").disabled = false;
      }
    };
  }

  let vTimer;
  $("#bezeichnung").oninput = (ev) => {
    clearTimeout(vTimer);
    const q = ev.target.value.trim();
    if (!q) { $("#vorschlaege").innerHTML = ""; return; }
    vTimer = setTimeout(async () => {
      let v;
      try { v = await api("vorschlaege?" + qs({ q })); } catch (_) { return; } // offline: keine Vorschläge
      $("#vorschlaege").innerHTML = v.length ? `<div class="vorschlagsliste">${v.slice(0, 5).map((x, i) => {
        const k = kat(x.kategorie_id);
        return `<button data-i="${i}"><span>${esc(k?.icon || "")}</span><span>${esc(x.bezeichnung)}</span><span class="rechts">${esc(k?.name || "")}, ${x.n}×</span></button>`;
      }).join("")}</div>` : "";
      $$("#vorschlaege button").forEach((b) => (b.onclick = () => {
        const x = v[+b.dataset.i];
        $("#bezeichnung").value = x.bezeichnung;
        katId = x.kategorie_id;
        $$("#katgitter button").forEach((y) => y.classList.toggle("aktiv", +y.dataset.k === katId));
        if (!betragFeld.value && x.letzter_betrag) betragFeld.value = Math.abs(x.letzter_betrag).toFixed(2).replace(".", ",");
        $("#vorschlaege").innerHTML = "";
        umrechnungZeigen();
      }));
    }, 180);
  };

  const anteilStart = $("#anteil").value;
  const anteilZeigen = () => {
    const t = $("#anteil").value.trim(), b = zahlLesen(betragFeld.value);
    const regel = $("#anteil-optionen");
    const name = (e && (e.bezeichnung || e.haendler_roh)) || $("#bezeichnung").value.trim();
    if (regel) {
      regel.style.display = !t && !anteilStart ? "none" : "";
      $("#anteil-regel-text").textContent = t
        ? `Ab jetzt für alle Einträge von „${name || "diesem Händler"}“ übernehmen`
        : `Regel für „${name || "diesem Händler"}“ entfernen`;
      $("#anteil-alle").closest(".check-zeile").style.display = $("#anteil-regel").checked ? "" : "none";
      $("#anteil-alle").nextElementSibling.textContent = t ? "Auch alle bisherigen Einträge umstellen" : "Auch bisherige Einträge wieder voll zählen";
    }
    let info = "";
    const m = t.replace(/\s|€/g, "").replace(",", ".");
    if (t && b) {
      let wert = null;
      const bruch = m.match(/^(\d+)\/(\d+)$/), proz = m.match(/^(\d+(?:\.\d+)?)%$/);
      if (bruch && +bruch[2] > 0) wert = Math.abs(b) * Math.min(1, bruch[1] / bruch[2]);
      else if (proz) wert = Math.abs(b) * Math.min(100, +proz[1]) / 100;
      else if (!isNaN(+m) && +m > 0) wert = Math.min(Math.abs(b), +m);
      info = wert == null ? "Als Bruch (1/6), Prozent (50 %) oder Betrag (2,67) angeben" : `Für dich zählen ${geld(wert, $("#waehrung").value)} von ${geld(Math.abs(b), $("#waehrung").value)}.`;
    }
    $("#anteil-info").textContent = info;
  };
  $("#anteil").addEventListener("input", anteilZeigen);
  $("#anteil-regel")?.addEventListener("change", anteilZeigen);
  betragFeld.addEventListener("input", anteilZeigen);
  $("#bezeichnung").addEventListener("input", anteilZeigen);
  anteilZeigen();

  $("#speichern").onclick = async () => {
    const knopf = $("#speichern");
    if (knopf.disabled) return;
    const betrag = zahlLesen(betragFeld.value);
    if (!betrag && betrag !== 0 || isNaN(betrag)) { toast("Bitte einen Betrag eingeben"); betragFeld.focus(); return; }
    if (!katId) { toast("Bitte eine Kategorie wählen"); return; }
    const kurs = zahlLesen($("#kurs").value);
    const daten = {
      betrag: Math.abs(betrag) * vorzeichen, waehrung: $("#waehrung").value, kategorie_id: katId,
      bezeichnung: $("#bezeichnung").value.trim(), notiz: $("#notiz").value.trim(), datum: $("#datum").value,
      konto_id: +$("#konto").value, regel_lernen: $("#regel") ? $("#regel").checked : false,
      offen: $("#offen").checked,
    };
    if (kurs && daten.waehrung !== "EUR") daten.kurs = kurs;
    if ($("#anteil").value.trim() !== anteilStart) {
      daten.anteil_text = $("#anteil").value.trim();
      daten.anteil_regel = !!$("#anteil-regel")?.checked;
      daten.anteil_rueckwirkend = daten.anteil_regel && !!$("#anteil-alle")?.checked;
    }
    knopf.disabled = true; knopf.textContent = "…";
    const freigeben = () => { if ($("#speichern")) { knopf.disabled = false; knopf.textContent = "Sichern"; } };
    try {
      if (neu) {
        daten.client_ref = neueRef();
        try {
          await api("eintraege", { method: "POST", body: daten, zeitlimit: 8000 });
        } catch (err) {
          if (!err.offline) throw err;
          inWarteschlange({ art: "eintrag", daten });
          sheetSchliessen();
          toast("Offline gespeichert – wird übertragen, sobald wieder Verbindung besteht");
          wartendeAktualisieren();
          return;
        }
        localStorage.setItem("ausgaben_waehrung", daten.waehrung);
        localStorage.setItem("ausgaben_konto", daten.konto_id);
        S.letzteWaehrung = daten.waehrung; S.letztesKonto = String(daten.konto_id);
      } else {
        await api(`eintraege/${e.id}`, { method: "PUT", body: daten });
      }
      sheetSchliessen();
      toast(neu ? `Gesichert: ${daten.bezeichnung || kat(katId).name}, ${geld(daten.betrag, daten.waehrung)}` : "Änderung gesichert");
      await metaLaden(); neuZeichnen();
    } catch (err) { toast(err.message); } finally { freigeben(); }
  };
  $("#loeschen")?.addEventListener("click", async () => {
    if (!confirm(`„${e.bezeichnung || e.haendler_roh}“ über ${geld(e.betrag, e.waehrung)} löschen?`)) return;
    await api(`eintraege/${e.id}`, { method: "DELETE" });
    sheetSchliessen(); toast("Eintrag gelöscht"); await metaLaden(); neuZeichnen();
  });
  $("#aufteilen")?.addEventListener("click", () => aufteilen(e));
}

async function bildVerkleinern(datei, maxKante = 1568, qualitaet = 0.85) {
  const url = URL.createObjectURL(datei);
  try {
    const img = await new Promise((ok, fehler) => {
      const i = new Image();
      i.onload = () => ok(i);
      i.onerror = () => fehler(new Error("Das Foto konnte nicht geöffnet werden"));
      i.src = url;
    });
    const f = Math.min(1, maxKante / Math.max(img.naturalWidth, img.naturalHeight));
    const c = document.createElement("canvas");
    c.width = Math.round(img.naturalWidth * f); c.height = Math.round(img.naturalHeight * f);
    const ctx = c.getContext("2d");
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, c.width, c.height);
    ctx.drawImage(img, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", qualitaet);
  } finally { URL.revokeObjectURL(url); }
}

function aufteilen(e) {
  const kats = S.meta.kategorien.filter((k) => !k.archiviert);
  const teile = [{ betrag: e.betrag, kategorie_id: e.kategorie_id, bezeichnung: e.bezeichnung }, { betrag: 0, kategorie_id: null, bezeichnung: "" }];
  const zeichnen = () => {
    const rest = Math.round((e.betrag - teile.reduce((s, t) => s + (zahlLesen(t.betrag) || 0), 0)) * 100) / 100;
    sheetOeffnen(`<div class="griff"></div><div class="sheet-kopf"><button data-zu>Abbrechen</button><h3>Aufteilen</h3><button class="fett" id="ok" ${Math.abs(rest) > 0.004 ? "disabled" : ""}>Sichern</button></div>
      <p class="leise" style="font-size:14px;margin:4px 2px">${esc(e.bezeichnung || e.haendler_roh)} über ${geld(e.betrag, e.waehrung)} auf mehrere Kategorien verteilen, z. B. PayPal-Zahlung für Essen und Geschenk.</p>
      ${teile.map((t, i) => `<div class="teil" data-i="${i}">
        <div><input placeholder="Bezeichnung" data-f="bezeichnung" value="${esc(t.bezeichnung || "")}" style="margin-bottom:6px">
          <select data-f="kategorie_id"><option value="">Kategorie …</option>${kats.map((k) => `<option value="${k.id}" ${k.id === t.kategorie_id ? "selected" : ""}>${esc(k.icon)} ${esc(k.name)}</option>`).join("")}</select></div>
        <input inputmode="decimal" data-f="betrag" value="${t.betrag ? String(t.betrag).replace(".", ",") : ""}" placeholder="0,00">
        <button data-weg="${i}" aria-label="Teil entfernen" ${teile.length <= 2 ? "disabled" : ""}>×</button></div>`).join("")}
      <div class="summenzeile"><span>Rest</span><span class="zahl" style="color:${Math.abs(rest) > 0.004 ? "var(--pruefen)" : "var(--gutschrift)"}">${geld(rest, e.waehrung)}</span></div>
      <div class="knopfreihe"><button class="knopf zweit" id="plusteil">Teil hinzufügen</button>${Math.abs(rest) > 0.004 ? '<button class="knopf zweit" id="rest">Rest in letzten Teil</button>' : ""}</div>`);
    $$("#sheet .teil").forEach((z) => $$("[data-f]", z).forEach((f) => (f.onchange = () => {
      const t = teile[+z.dataset.i];
      t[f.dataset.f] = f.dataset.f === "kategorie_id" ? (+f.value || null) : f.value;
      if (f.dataset.f === "betrag") zeichnen();
    })));
    $$("#sheet [data-weg]").forEach((b) => (b.onclick = () => { teile.splice(+b.dataset.weg, 1); zeichnen(); }));
    $("#plusteil").onclick = () => { teile.push({ betrag: 0, kategorie_id: null, bezeichnung: "" }); zeichnen(); };
    $("#rest")?.addEventListener("click", () => { const t = teile.at(-1); t.betrag = Math.round(((zahlLesen(t.betrag) || 0) + rest) * 100) / 100; zeichnen(); });
    $("#ok").onclick = async () => {
      if (teile.some((t) => !t.kategorie_id)) { toast("Jeder Teil braucht eine Kategorie"); return; }
      try {
        await api(`eintraege/${e.id}/aufteilen`, { method: "POST", body: { teile: teile.map((t) => ({ ...t, betrag: zahlLesen(t.betrag) })) } });
        sheetSchliessen(); toast(`In ${teile.length} Teile aufgeteilt`); await metaLaden(); neuZeichnen();
      } catch (err) { toast(err.message); }
    };
  };
  zeichnen();
}

// ---------------------------------------------------------------- Verlauf
async function verlaufAnsicht() {
  const v = S.verlauf, heute = new Date();
  const von = { tag: isoTag(tagPlus(heute, -29)), woche: isoTag(tagPlus(heute, -7 * 15 - ((heute.getDay() + 6) % 7))), monat: isoTag(new Date(heute.getFullYear() - 1, heute.getMonth() - 11, 1)), jahr: "2000-01-01" }[v.einheit];
  const kats = S.meta.kategorien.filter((k) => k.anzahl > 0);
  ansicht.innerHTML = `<div class="kopf"><h1>Verlauf</h1></div>
    <div class="segment">${["tag", "woche", "monat", "jahr"].map((e) => `<button data-e="${e}" class="${v.einheit === e ? "aktiv" : ""}">${{ tag: "Tage", woche: "Wochen", monat: "Monate", jahr: "Jahre" }[e]}</button>`).join("")}</div>
    <div class="chips"><button class="chip ${v.kategorie ? "" : "aktiv"}" data-k="">Alle Kategorien</button>${kats.map((k) => `<button class="chip ${String(k.id) === String(v.kategorie) ? "aktiv" : ""}" data-k="${k.id}">${esc(k.icon)} ${esc(k.name)}</button>`).join("")}</div>
    <div id="vinhalt"><div class="leer">Lade …</div></div>`;
  $$(".segment button").forEach((b) => (b.onclick = () => { v.einheit = b.dataset.e; verlaufAnsicht(); }));
  $$(".chips .chip").forEach((b) => (b.onclick = () => { v.kategorie = b.dataset.k; verlaufAnsicht(); }));
  $(".chips .chip.aktiv")?.scrollIntoView({ inline: "center", block: "nearest" });

  const reihe = await api("auswertung/zeitreihe?" + qs({ von, bis: isoTag(heute), einheit: v.einheit, kategorie: v.kategorie }));
  const map = Object.fromEntries(reihe.map((r) => [r.periode, r]));
  const daten = [];
  if (v.einheit === "tag") for (let d = ausIso(von); d <= heute; d = tagPlus(d, 1)) { const k = isoTag(d); daten.push({ schluessel: k, label: tagLabel(k), kurz: String(d.getDate()), wert: map[k]?.summe || 0, n: map[k]?.anzahl || 0 }); }
  else if (v.einheit === "woche") for (let d = ausIso(von); d <= heute; d = tagPlus(d, 7)) { const k = isoTag(d); daten.push({ schluessel: k, label: `KW ${kw(d)} (ab ${d.getDate()}.${d.getMonth() + 1}.)`, kurz: String(kw(d)), wert: map[k]?.summe || 0, n: map[k]?.anzahl || 0 }); }
  else if (v.einheit === "monat") for (let d = ausIso(von); d <= heute; d = new Date(d.getFullYear(), d.getMonth() + 1, 1)) { const k = `${d.getFullYear()}-${pad(d.getMonth() + 1)}`; daten.push({ schluessel: k, label: `${MONATE[d.getMonth()]} ${d.getFullYear()}`, kurz: d.getMonth() === 0 ? String(d.getFullYear()) : MON_KURZ[d.getMonth()], wert: map[k]?.summe || 0, n: map[k]?.anzahl || 0 }); }
  else { const erstes = reihe.length ? +reihe[0].periode : heute.getFullYear(); for (let j = erstes; j <= heute.getFullYear(); j++) daten.push({ schluessel: String(j), label: String(j), kurz: "'" + String(j).slice(2), wert: map[j]?.summe || 0, n: map[j]?.anzahl || 0 }); }
  const { svg, mittel } = balken(daten, { hoehe: 190, maxLabels: 14, titel: "Verlauf" });
  const summe = daten.reduce((s, d) => s + d.wert, 0);
  const max = daten.reduce((m, d) => (d.wert > m.wert ? d : m), { wert: 0 });
  $("#vinhalt").innerHTML = `<div class="block"><h2>${v.kategorie ? esc(kat(+v.kategorie)?.name) : "Alle Ausgaben"}<span class="leise">Ø ${geld(mittel)}</span></h2>${svg}
      <div class="diagramm-info" id="vinfo">Tippe auf einen Balken für Details</div></div>
    <div class="mehr-liste">
      <div class="zeile">Summe im Diagramm<span class="rechts zahl">${geld(summe)}</span></div>
      <div class="zeile">Höchster Wert<span class="rechts">${max.wert ? esc(max.label) + ", " + geld(max.wert) : "–"}</span></div>
    </div>
    <div class="mehr-liste">${[...daten].reverse().filter((d) => d.wert).slice(0, 24).map((d) => `<button data-s="${d.schluessel}">${esc(d.label)}<span class="rechts zahl">${geld(d.wert)}<br><small>${d.n} Einträge</small></span></button>`).join("")}</div>`;
  const oeffnen = (d) => {
    const ein = { tag: "tag", woche: "woche", monat: "monat", jahr: "jahr" }[v.einheit];
    const anker = v.einheit === "monat" ? ausIso(d.schluessel + "-01") : v.einheit === "jahr" ? new Date(+d.schluessel, 0, 1) : ausIso(d.schluessel);
    S.zeitraum = { einheit: ein, anker }; S.bandAuswahl = v.kategorie || null; tabWechseln("uebersicht");
  };
  balkenKlicks($("#vinhalt"), daten, $("#vinfo"), oeffnen);
  $$("#vinhalt [data-s]").forEach((b) => (b.onclick = () => oeffnen(daten.find((d) => d.schluessel === b.dataset.s))));
}

// ---------------------------------------------------------------- Mehr
async function mehrAnsicht() {
  S.unterseiteAktiv = null;
  await metaLaden();
  const m = S.meta, tr = m.tr;
  const trProblem = tr.eingerichtet && ["login_noetig", "fehler"].includes(tr.zustand);
  const bankAnzahl = (tr.eingerichtet ? 1 : 0) + m.banken.anzahl;
  const bankRechts = trProblem || m.banken.fehler.length
    ? `<span class="marke pruefen">${trProblem && tr.zustand === "login_noetig" && !m.banken.fehler.length ? "Anmeldung nötig" : "Fehler"}</span>`
    : bankAnzahl ? `${bankAnzahl} verbunden` : "einrichten";
  ansicht.innerHTML = `<div class="kopf"><h1>Mehr</h1></div>
    ${m.update?.verfuegbar ? `<button class="hinweis ok" id="m-update"><span style="font-size:20px">⬆️</span><span><b style="color:inherit">Update auf ${esc(m.update.neueste)} verfügbar</b><br><span class="leise" style="font-size:13px">Antippen zum Ansehen und Installieren</span></span><span style="margin-left:auto">›</span></button>` : ""}
    <div class="abschnitt">Daten</div>
    <div class="mehr-liste">
      <button id="m-kat"><span class="ic">🏷️</span>Kategorien<span class="rechts">${m.kategorien.filter((k) => !k.archiviert).length}</span></button>
      <button id="m-konten"><span class="ic">💳</span>Konten<span class="rechts">${m.konten.filter((k) => !k.archiviert).length}</span></button>
      ${m.banken.offen_nachholen ? `<button id="m-nach"><span class="ic">📋</span>Bankumsätze durchsehen<span class="rechts"><span class="marke pruefen">${m.banken.offen_nachholen}</span></span></button>` : ""}
      <button id="m-dup"><span class="ic">👯</span>Doppelte Einträge finden</button>
      <button id="m-fv"><span class="ic">⚖️</span>Fix &amp; variabel<span class="rechts">pro Monat</span></button>
      <button id="m-wk"><span class="ic">🔁</span>Wiederkehrende Ausgaben<span class="rechts">Abos, Lastschriften</span></button>
      <button id="m-regeln"><span class="ic">🧠</span>Gelernte Zuordnungen</button>
      <button id="m-anteile"><span class="ic">➗</span>Eigenanteile</button>
      <button id="m-pruefen"><span class="ic">🔎</span>Zu prüfen<span class="rechts">${m.kennzahlen.pruefen}</span></button>
      <button id="m-offen"><span class="ic">⏳</span>Noch offen<span class="rechts">${m.kennzahlen.offen || 0}</span></button>
    </div>
    <div class="abschnitt">Automatik</div>
    <div class="mehr-liste">
      <button id="m-banken"><span class="ic">🏦</span>Banken<span class="rechts">${bankRechts}</span></button>
      <button id="m-ki"><span class="ic">✨</span>KI-Kategorisierung<span class="rechts">${!m.ki.aktiv ? "aus" : m.ki.kosten_monat_eur ? `${kostenText(m.ki.kosten_monat_eur)} im ${MON_KURZ[new Date().getMonth()]}.` : esc(m.ki.modell)}</span></button>
    </div>
    <div class="abschnitt">Austausch</div>
    <div class="mehr-liste">
      <button id="m-export"><span class="ic">📤</span>Export nach Excel oder CSV</button>
      <button id="m-import"><span class="ic">📥</span>Next-Daten importieren</button>
    </div>
    <div class="abschnitt">Darstellung</div>
    <div class="mehr-liste">
      <button id="m-design"><span class="ic">🎨</span>Design<span class="rechts">${esc((DESIGNS[S.design.id] || DESIGNS.salbei).name)} · ${esc(MODI.find(([x]) => x === S.design.modus)[1])}${farbpunkte(S.design.id)}</span></button>
      <button id="m-offline"><span class="ic">📶</span>Offline &amp; HTTPS<span class="rechts">${location.protocol === "https:" && navigator.serviceWorker?.controller ? "aktiv" : "einrichten"}</span></button>
      <button id="m-start"><span class="ic">🏠</span>Startseite<span class="rechts">${esc(STARTSEITEN[S.meta.darstellung.startseite] || "Übersicht")}</span></button>
    </div>
    <div class="abschnitt">Info</div>
    <div class="mehr-liste">
      <button id="m-version"><span class="ic">ℹ️</span>Version &amp; Änderungen<span class="rechts">${m.update?.verfuegbar ? `<span class="marke pruefen">Update ${esc(m.update.neueste)}</span>` : esc(m.version || "")}</span></button>
    </div>
    <div class="abschnitt">Verwaltung</div>
    <div class="mehr-liste">
      <button id="m-reset" style="color:var(--pruefen)"><span class="ic">🗑️</span>Alle Einträge löschen</button>
    </div>
    <p class="leise" style="font-size:13px;margin:18px 4px">${m.anzahl_gesamt.toLocaleString("de-DE")} Einträge gespeichert. Die Daten liegen im Add-on und sind Teil deiner Home-Assistant-Backups.</p>`;
  $("#m-kat").onclick = kategorienVerwalten;
  $("#m-version").onclick = versionAnsicht;
  $("#m-update")?.addEventListener("click", versionAnsicht);
  $("#m-konten").onclick = kontenVerwalten;
  $("#m-regeln").onclick = regelnAnsicht;
  $("#m-anteile").onclick = anteileAnsicht;
  $("#m-wk").onclick = wiederkehrendAnsicht;
  $("#m-fv").onclick = fixVariabelAnsicht;
  $("#m-dup").onclick = doppelteAnsicht;
  $("#m-reset").onclick = zuruecksetzenSheet;
  $("#m-design").onclick = designAnsicht;
  $("#m-start").onclick = startseiteSheet;
  $("#m-offline").onclick = offlineAnsicht;
  $("#m-pruefen").onclick = pruefenAnsicht;
  $("#m-offen").onclick = offeneAnzeigen;
  $("#m-ki").onclick = kiAnsicht;
  $("#m-banken").onclick = bankenAnsicht;
  $("#m-nach")?.addEventListener("click", () => nachholenAnsicht());
  $("#m-export").onclick = () => exportSheet({});
  $("#m-import").onclick = importSheet;
}
// ---------------------------------------------------------------- Version und Änderungen
const mdKurz = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`(.+?)`/g, "<code>$1</code>");
async function versionAnsicht() {
  const v = await api("version");
  const u = v.update || {};
  const liste = v.aenderungen.map((a, i) => `
    <details class="version" ${i < 3 ? "open" : ""}>
      <summary><b>${esc(a.version)}</b>${a.version === v.version ? '<span class="marke auto">installiert</span>' : ""}<span class="leise">${a.punkte.length} ${a.punkte.length === 1 ? "Änderung" : "Änderungen"}</span></summary>
      <ul>${a.punkte.map((p) => `<li>${mdKurz(p)}</li>`).join("")}</ul>
    </details>`).join("");
  const geprueft = u.geprueft ? new Date(u.geprueft * 1000) : null;
  const geprueftText = geprueft ? `Zuletzt geprüft ${geprueft.toDateString() === new Date().toDateString() ? "heute" : geprueft.toLocaleDateString("de-DE")} um ${geprueft.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })}` : "Noch nicht geprüft";
  const updateBox = u.laeuft ? `<div class="update-box"><b>Update läuft …</b><p class="leise">Das Add-on wird gerade aktualisiert und startet danach neu.</p></div>`
    : u.verfuegbar ? `<div class="update-box neu">
        <div class="leise">Update verfügbar</div><div class="version-nr">${esc(u.neueste)}</div>
        ${(u.neu || []).map((a) => `${u.neu.length > 1 ? `<div class="leise" style="font-size:13px;margin-top:8px">Neu in ${esc(a.version)}</div>` : ""}<ul>${a.punkte.map((p) => `<li>${mdKurz(p)}</li>`).join("")}</ul>`).join("")}
        ${checkZeile("up-backup", false, "Vorher ein Backup des Add-ons anlegen (dauert länger)")}
        <div class="knopfreihe"><button class="knopf" id="up-los">Jetzt aktualisieren</button></div></div>`
    : `<div class="update-box"><div style="display:flex;align-items:center;gap:10px"><span style="font-size:22px">${u.meldung ? "⚠️" : geprueft ? "✅" : "🔎"}</span><div><b>${u.meldung ? "Prüfung nicht möglich" : geprueft ? "Du bist auf dem neuesten Stand" : "Noch nicht nach Updates gesucht"}</b><div class="leise" style="font-size:13px">${esc(u.meldung || geprueftText)}</div></div></div>
        <div class="knopfreihe"><button class="knopf zweit" id="up-pruefen">Nach Updates suchen</button></div></div>`;
  unterseite("Version & Änderungen", `
    <div class="version-kopf"><div class="leise">Installierte Version</div><div class="version-nr">${esc(v.version)}</div></div>
    ${updateBox}
    <div class="abschnitt">Änderungen</div>
    ${liste || '<div class="leer">Kein Änderungsprotokoll gefunden.</div>'}`);
  window.scrollTo(0, 0);
  $("#up-pruefen")?.addEventListener("click", async (ev) => {
    ev.target.disabled = true; ev.target.textContent = "Suche …";
    try {
      const r = await api("update/pruefen", { method: "POST", zeitlimit: 90000 });
      await metaLaden().catch(() => {});
      if (!r.verfuegbar && !r.meldung) toast("Kein Update vorhanden");
      versionAnsicht();
    } catch (err) { toast(err.message); ev.target.disabled = false; ev.target.textContent = "Nach Updates suchen"; }
  });
  $("#up-los")?.addEventListener("click", async () => {
    if (!confirm(`Auf Version ${u.neueste} aktualisieren? Die App ist dabei ein bis zwei Minuten nicht erreichbar.`)) return;
    try {
      await api("update/installieren", { method: "POST", body: { backup: $("#up-backup").checked } });
      updateWarten(v.version, u.neueste);
    } catch (err) { toast(err.message); }
  });
}
// Während des Updates: warten, bis das Add-on mit neuer Version antwortet, dann die App neu laden.
function updateWarten(alt, neu) {
  sheetOeffnen(`<div class="griff"></div><div style="text-align:center;padding:26px 8px 18px">
    <div class="kreisel" style="margin:0 auto 14px"></div>
    <p style="font-size:17px;font-weight:600;margin:0 0 6px">Aktualisiere auf ${esc(neu)} …</p>
    <p class="leise" id="up-text" style="margin:0">Home Assistant baut das Add-on neu. Die App lädt danach von selbst neu.</p></div>`);
  const start = Date.now();
  const text = (t) => { const el = $("#up-text"); if (el) el.textContent = t; else toast(t); };
  const runde = async () => {
    try {
      const r = await fetch("api/version", { cache: "no-store", headers: { "X-Ausgaben-Token": localStorage.getItem("ausgaben_token") || "" } });
      if (r.ok && !r.headers.get("X-Ausgaben-Offline")) {
        const d = await r.json();
        if (d.version && d.version !== alt) {
          text(`Version ${d.version} ist installiert. Lade neu …`);
          try { await (await navigator.serviceWorker?.getRegistration())?.update(); } catch (_) { /* egal */ }
          setTimeout(() => location.reload(), 800);
          return;
        }
        if (d.update && !d.update.laeuft && d.update.meldung && Date.now() - start > 15000) {
          text(d.update.meldung); return;
        }
      }
    } catch (_) { /* Add-on startet gerade neu */ }
    if (Date.now() - start > 15 * 60000) { text("Das dauert ungewöhnlich lange. Sieh bitte in Home Assistant unter Einstellungen → Add-ons nach."); return; }
    setTimeout(runde, 3000);
  };
  setTimeout(runde, 5000);
}

function unterseite(titel, html) {
  S.unterseiteAktiv = null;
  ansicht.innerHTML = `<div class="kopf"><button class="rund" id="zurueck" aria-label="Zurück">‹</button><h1>${esc(titel)}</h1></div>${html}`;
  $("#zurueck").onclick = mehrAnsicht;
}

// ---------------------------------------------------------------- Wiederkehrende Ausgaben
const RHYTHMEN = [
  { name: "wöchentlich", tage: 7, min: 5, max: 9, mindest: 4 },
  { name: "alle 2 Wochen", tage: 14, min: 12, max: 16, mindest: 3 },
  { name: "monatlich", tage: 30.44, min: 26, max: 35, mindest: 3 },
  { name: "alle 2 Monate", tage: 60.9, min: 55, max: 67, mindest: 3 },
  { name: "vierteljährlich", tage: 91.3, min: 83, max: 100, mindest: 2 },
  { name: "halbjährlich", tage: 182.6, min: 170, max: 195, mindest: 2 },
  { name: "jährlich", tage: 365.25, min: 350, max: 380, mindest: 2 },
];
const median = (a) => { const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const tageZwischen = (a, b) => Math.round((ausIso(b) - ausIso(a)) / 86400000);
function wkIgnoriert() { try { return JSON.parse(localStorage.getItem("ausgaben_wk_ignoriert") || "[]"); } catch (_) { return []; } }
function wkIgnorieren(liste) { try { localStorage.setItem("ausgaben_wk_ignoriert", JSON.stringify(liste)); } catch (_) { /* egal */ } }

function wiederkehrendErkennen(eintraege) {
  const gruppen = new Map();
  for (const e of eintraege) {
    if (!(eigen(e) > 0)) continue;
    const name = (e.bezeichnung || e.haendler_roh || "").trim();
    if (!name) continue;
    const schluessel = name.toLowerCase();
    if (!gruppen.has(schluessel)) gruppen.set(schluessel, { name, liste: [] });
    gruppen.get(schluessel).liste.push(e);
  }
  const heute = isoTag(new Date());
  const ergebnis = [];
  for (const [schluessel, g] of gruppen) {
    // Mehrere Buchungen am selben Tag zusammenfassen
    const proTag = new Map();
    for (const e of g.liste) { const t = e.datum.slice(0, 10); proTag.set(t, (proTag.get(t) || 0) + eigen(e)); }
    const tage = [...proTag.keys()].sort();
    if (tage.length < 2) continue;
    const betraege = tage.map((t) => proTag.get(t));
    const abstaende = tage.slice(1).map((t, i) => tageZwischen(tage[i], t));
    const mAbstand = median(abstaende);
    const r = RHYTHMEN.find((x) => mAbstand >= x.min && mAbstand <= x.max);
    if (!r || tage.length < r.mindest) continue;
    const passend = abstaende.filter((a) => a >= r.min && a <= r.max).length / abstaende.length;
    if (passend < 0.7) continue;
    // Betrag muss weitgehend gleich bleiben (sonst ist es z. B. der Supermarkt)
    const mBetrag = median(betraege.slice(-6));
    const gleich = betraege.filter((b) => Math.abs(b - mBetrag) <= Math.max(1, mBetrag * 0.15)).length / betraege.length;
    if (gleich < 0.7) continue;
    // Nur zwei Buchungen: nur bei nahezu gleichem Betrag, und als "vermutet" markiert
    const sicher = tage.length >= 3;
    if (!sicher && Math.abs(betraege[0] - betraege[1]) > Math.max(0.05, mBetrag * 0.03)) continue;
    const aktuell = median(betraege.slice(-3)); // zuletzt gueltiger Betrag, z. B. nach Mieterhoehung
    const letzter = tage.at(-1);
    const seit = tageZwischen(letzter, heute);
    const aktiv = seit <= r.tage * 1.5 + 5;
    if (!sicher && !aktiv) continue;
    const naechster = isoTag(tagPlus(ausIso(letzter), Math.round(r.tage)));
    const k = kat(g.liste.at(-1).kategorie_id);
    ergebnis.push({
      schluessel, name: g.name, rhythmus: r.name, tage: r.tage, betrag: aktuell,
      schwankend: betraege.slice(-3).some((x) => Math.abs(x - aktuell) > Math.max(0.01, aktuell * 0.01)),
      proMonat: aktuell * 30.44 / r.tage, letzter, naechster, aktiv, anzahl: tage.length,
      erster: tage[0], tagImMonat: r.tage >= 28 ? Math.round(median(tage.map((t) => +t.slice(8, 10)))) : null,
      kategorie: k, sicher, ueberfaellig: aktiv && naechster < heute && seit > r.tage + 5,
    });
  }
  return ergebnis.sort((a, b) => b.proMonat - a.proMonat);
}

// Einträge der letzten zwei Jahre (für die Erkennung wiederkehrender Ausgaben), kurz zwischengespeichert
async function zweiJahreLaden(bisMonat = null) {
  const basis = bisMonat || new Date();
  const von = isoTag(new Date(basis.getFullYear() - 2, basis.getMonth(), 1));
  const c = S._zweiJahre;
  if (c && c.von <= von && Date.now() - c.zeit < 120000) return c.daten.filter((e) => e.datum >= von);
  const alle = [];
  for (let offset = 0; offset < 30000;) {
    const r = await api("eintraege?" + qs({ von, limit: 2000, offset, sortierung: "alt" }));
    alle.push(...r.eintraege);
    offset += r.eintraege.length;
    if (!r.eintraege.length || offset >= r.anzahl) break;
  }
  S._zweiJahre = { von, zeit: Date.now(), daten: alle };
  return alle;
}
function wkFix() { try { return JSON.parse(localStorage.getItem("ausgaben_wk_fix") || "[]"); } catch (_) { return []; } }
function wkFixSetzen(liste) { try { localStorage.setItem("ausgaben_wk_fix", JSON.stringify(liste)); } catch (_) { /* egal */ } }
const wkSchluessel = (e) => (e.bezeichnung || e.haendler_roh || "").trim().toLowerCase();

// ---------------------------------------------------------------- Fix & variabel
async function fixVariabelAnsicht() {
  S.fv = S.fv || { monat: new Date(new Date().getFullYear(), new Date().getMonth(), 1), umlegen: true, einheit: "monat" };
  const f = S.fv;
  const jahr = f.einheit === "jahr";
  const vonD = jahr ? new Date(f.monat.getFullYear(), 0, 1) : f.monat;
  const bisD = jahr ? new Date(f.monat.getFullYear(), 11, 31) : new Date(f.monat.getFullYear(), f.monat.getMonth() + 1, 0);
  const von = isoTag(vonD), bis = isoTag(bisD);
  const laufend = bis >= isoTag(new Date());
  const umlegen = !jahr && f.umlegen; // im Jahr zählen die echten Buchungen
  const monateImZeitraum = jahr ? (laufend ? new Date().getMonth() + 1 : 12) : 1;
  unterseite("Fix & variabel", `<div class="segment" role="tablist">${["monat", "jahr"].map((e) =>
      `<button data-fe="${e}" class="${f.einheit === e ? "aktiv" : ""}">${e === "monat" ? "Monat" : "Jahr"}</button>`).join("")}</div>
    <div class="zeitraum"><button class="rund" id="fv-zurueck" aria-label="Zurück">‹</button>
      <div class="label">${jahr ? f.monat.getFullYear() : `${MONATE[f.monat.getMonth()]} ${f.monat.getFullYear()}`}</div>
      <button class="rund" id="fv-vor" aria-label="Weiter" ${laufend ? "disabled" : ""}>›</button></div>
    <div id="fv-inhalt"><div class="leer">Analysiere die Buchungen …</div></div>`);
  S.unterseiteAktiv = fixVariabelAnsicht;
  const navi = (n) => {
    const jetzt = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
    const neu = jahr ? new Date(f.monat.getFullYear() + n, f.monat.getMonth(), 1) : new Date(f.monat.getFullYear(), f.monat.getMonth() + n, 1);
    f.monat = neu > jetzt ? jetzt : neu;
    fixVariabelAnsicht();
  };
  $("#fv-zurueck").onclick = () => navi(-1);
  $("#fv-vor").onclick = () => navi(1);
  $$("[data-fe]").forEach((b) => (b.onclick = () => { f.einheit = b.dataset.fe; fixVariabelAnsicht(); }));

  const alle = await zweiJahreLaden(new Date(Math.min(+bisD, Date.now())));
  const ignoriert = wkIgnoriert(), manuell = wkFix();
  // Jährliche/halbjährliche Posten kommen in zwei Jahren nur zwei- bis dreimal vor: dann reicht „vermutet“
  const posten = wiederkehrendErkennen(alle).filter((x) => (x.sicher || x.tage > 150) && !ignoriert.includes(x.schluessel));
  const MONATE_JE = { "alle 2 Monate": 2, vierteljährlich: 3, halbjährlich: 6, jährlich: 12 };
  const monatsanteil = (x) => (MONATE_JE[x.rhythmus] ? x.betrag / MONATE_JE[x.rhythmus] : x.proMonat);
  const postenNach = new Map(posten.map((x) => [x.schluessel, x]));
  const istFix = (e) => postenNach.has(wkSchluessel(e)) || manuell.includes(wkSchluessel(e));
  const imMonat = alle.filter((e) => e.datum.slice(0, 10) >= von && e.datum.slice(0, 10) <= bis && eigen(e) != null);
  const selten = (x) => x && x.tage > 40; // alle 2 Monate, vierteljährlich, halbjährlich, jährlich

  // Fixe Zeilen: echte Buchungen; seltene Posten wahlweise als Monatsanteil
  const fix = [], variabel = [];
  for (const e of imMonat) {
    if (!istFix(e)) { variabel.push(e); continue; }
    const x = postenNach.get(wkSchluessel(e));
    if (umlegen && selten(x)) continue; // wird unten als Monatsanteil gezählt
    fix.push({ art: "buchung", e, name: e.bezeichnung || e.haendler_roh || "Ohne Bezeichnung", betrag: eigen(e),
      kategorie_id: e.kategorie_id, rhythmus: x ? x.rhythmus : "als fix markiert", schluessel: wkSchluessel(e) });
  }
  if (umlegen) {
    for (const x of posten.filter(selten)) {
      const lief = x.erster <= bis && tageZwischen(x.letzter, von) <= x.tage + 10; // im Monat noch aktiv
      if (!lief) continue;
      const katId = alle.findLast((e) => wkSchluessel(e) === x.schluessel)?.kategorie_id ?? null;
      fix.push({ art: "anteil", name: x.name, betrag: monatsanteil(x), kategorie_id: katId, rhythmus: x.rhythmus, schluessel: x.schluessel, posten: x });
    }
  }
  const summe = (l, fn) => l.reduce((s, x) => s + fn(x), 0);
  const fixSumme = summe(fix, (x) => x.betrag), varSumme = summe(variabel, eigen);
  // Im Jahr: je Posten bzw. Bezeichnung eine Zeile statt jeder einzelnen Buchung
  const zusammenfassen = (liste, betragVon, katVon) => {
    const m = new Map();
    for (const x of liste) {
      const e = x.e || x, s = wkSchluessel(e) || `#${e.id}`, k = katVon(x);
      const key = `${k}|${s}`;
      if (!m.has(key)) m.set(key, { art: "summe", name: e.bezeichnung || e.haendler_roh || "Ohne Bezeichnung", schluessel: s, kategorie_id: k, betrag: 0, anzahl: 0, rhythmus: x.rhythmus });
      const z = m.get(key); z.betrag += betragVon(x); z.anzahl += 1;
    }
    return [...m.values()];
  };
  const fixZeilen = jahr ? zusammenfassen(fix, (x) => x.betrag, (x) => x.kategorie_id) : fix;
  const varSummen = jahr ? zusammenfassen(variabel, eigen, (e) => e.kategorie_id) : null;
  const gruppieren = (liste, betragVon) => {
    const m = new Map();
    for (const x of liste) {
      const id = x.kategorie_id ?? x.e?.kategorie_id ?? null;
      if (!m.has(id)) m.set(id, { id, k: kat(id), summe: 0, liste: [] });
      const g = m.get(id); g.summe += betragVon(x); g.liste.push(x);
    }
    return [...m.values()].sort((a, b) => b.summe - a.summe);
  };
  const datum = (t) => ausIso(t).toLocaleDateString("de-DE", { day: "numeric", month: "short" });
  const gruppeHtml = (g, gesamt, zeilen, typ) => `
    <details class="fv-gruppe">
      <summary class="katzeile">
        <span class="ic" style="${icFarbe(g.k?.farbe || "#9AA3AE")}">${esc(g.k?.icon || "❔")}</span>
        <span><span class="name">${esc(g.k?.name || "Ohne Kategorie")}</span><span class="balken"><i style="width:${gesamt > 0 ? Math.max(1, Math.round(g.summe / gesamt * 100)) : 0}%;background:${g.k?.farbe || "#9AA3AE"}"></i></span></span>
        <span class="wert zahl">${geld(g.summe)}<small>${gesamt > 0 ? Math.round(g.summe / gesamt * 100) : 0} %, ${g.liste.reduce((n, x) => n + (x.anzahl || 1), 0)}×</small></span>
      </summary>
      <div class="fv-zeilen">${zeilen}</div>
    </details>`;
  const fixGruppen = gruppieren(fixZeilen, (x) => x.betrag), varGruppen = jahr ? gruppieren(varSummen, (x) => x.betrag) : gruppieren(variabel, eigen);
  const fixHtml = fixGruppen.map((g) => gruppeHtml(g, fixSumme, g.liste.sort((a, b) => b.betrag - a.betrag).map((x, i) => `
      <button class="fv-zeile" data-fix="${esc(x.schluessel)}" ${x.e ? `data-id="${x.e.id}"` : ""}>
        <span style="min-width:0;flex:1"><span class="fv-name">${esc(x.name)}</span>
          <small class="leise">${esc(x.rhythmus)}${x.art === "summe" ? ` · ${x.anzahl}× gebucht` : x.art === "anteil" ? ` · ${geld(x.posten.betrag)} ${x.posten.tage > 300 ? "im Jahr" : "je Zahlung"}, Monatsanteil` : ` · ${datum(x.e.datum)}`}</small></span>
        <span class="zahl">${geld(x.betrag)}</span></button>`).join(""), "fix")).join("");
  const varHtml = jahr ? varGruppen.map((g) => gruppeHtml(g, varSumme, g.liste.sort((a, b) => b.betrag - a.betrag).map((x) => `
      <button class="fv-zeile" data-varsumme="${esc(x.schluessel)}" data-k="${x.kategorie_id ?? ""}">
        <span style="min-width:0;flex:1"><span class="fv-name">${esc(x.name)}</span>
          <small class="leise">${x.anzahl}× · Ø ${geld(x.betrag / x.anzahl)}</small></span>
        <span class="zahl">${geld(x.betrag)}</span></button>`).join(""), "var")).join("")
    : varGruppen.map((g) => gruppeHtml(g, varSumme, g.liste.sort((a, b) => b.datum.localeCompare(a.datum)).map((e) => `
      <button class="fv-zeile" data-var="${e.id}">
        <span style="min-width:0;flex:1"><span class="fv-name">${esc(e.bezeichnung || e.haendler_roh || "Ohne Bezeichnung")}</span>
          <small class="leise">${datum(e.datum)}${e.konto ? " · " + esc(e.konto) : ""}</small></span>
        <span class="zahl">${geld(eigen(e))}</span></button>`).join(""), "var")).join("");
  const gesamt = fixSumme + varSumme;
  const teil = (n) => (gesamt > 0 ? Math.round(n / gesamt * 100) : 0);
  $("#fv-inhalt").innerHTML = `
    <div class="fv-kacheln">
      <div class="fv-kachel"><div class="leise">Fix</div><div class="fv-wert zahl">${geld(fixSumme)}</div><div class="leise">${teil(fixSumme)} %${jahr ? ` · Ø ${geld(fixSumme / monateImZeitraum)}/Monat` : ""}</div></div>
      <div class="fv-kachel"><div class="leise">Variabel</div><div class="fv-wert zahl">${geld(varSumme)}</div><div class="leise">${teil(varSumme)} %${jahr ? ` · Ø ${geld(varSumme / monateImZeitraum)}/Monat` : ""}</div></div>
    </div>
    <div class="band" aria-hidden="true"><span style="flex-grow:${fixSumme || 0.0001};background:var(--akzent)"></span><span style="flex-grow:${varSumme || 0.0001};background:var(--leise)"></span></div>
    <p class="leise" style="font-size:13px;margin:6px 4px">Zusammen ${geld(gesamt)}${laufend ? " bis heute" : ""}${jahr ? `, Ø ${geld(gesamt / monateImZeitraum)} pro Monat${laufend ? ` (${monateImZeitraum} ${monateImZeitraum === 1 ? "Monat" : "Monate"})` : ""}` : umlegen ? ", seltene Fixkosten als Monatsanteil" : ""}.</p>
    ${jahr ? "" : `<div class="mehr-liste">${checkZeile("fv-umlegen", f.umlegen, "Vierteljährliche und jährliche Kosten auf die Monate verteilen")}</div>`}
    <div class="abschnitt">Fixkosten</div>
    <div class="block fv-block">${fixHtml || '<div class="leer">Keine Fixkosten in diesem ${jahr ? "Jahr" : "Monat"} erkannt.</div>'}</div>
    <div class="abschnitt">Variable Ausgaben</div>
    <div class="block fv-block">${varHtml || '<div class="leer">Keine variablen Ausgaben in diesem ${jahr ? "Jahr" : "Monat"}.</div>'}</div>
    <p class="leise" style="font-size:13px;margin:16px 4px">Fixkosten sind Ausgaben, die regelmäßig mit gleicher Bezeichnung und weitgehend gleichem Betrag kommen (wie unter „Wiederkehrende Ausgaben“). Tippe auf eine Ausgabe, um sie zu öffnen oder anders zuzuordnen.</p>`;
  $("#fv-umlegen")?.addEventListener("change", (ev) => { f.umlegen = ev.target.checked; fixVariabelAnsicht(); });
  const alleBuchungen = (name) => { sheetSchliessen(); Object.assign(S.suche, { q: name, kategorie: "", konto: "", pruefen: false, offen: false, von: jahr ? von : "", bis: jahr ? bis : "", sortierung: "" }); tabWechseln("eintraege"); };
  const eintragOeffnen = (id) => { const e = alle.find((x) => x.id === id); if (e) { sheetSchliessen(); erfassen(e); } };
  $$("[data-fix]").forEach((b) => (b.onclick = () => {
    const s = b.dataset.fix, x = fixZeilen.find((y) => y.schluessel === s && (!b.dataset.id || String(y.e?.id) === b.dataset.id));
    const manuellFix = manuell.includes(s) && !postenNach.has(s);
    sheetOeffnen(`<div class="griff"></div><div class="sheet-kopf"><button data-zu>Schließen</button><h3>${esc(x.name)}</h3><span style="width:60px"></span></div>
      <div class="mehr-liste">
        ${x.e ? `<button id="fv-oeffnen"><span class="ic">✏️</span>Buchung öffnen<span class="rechts zahl">${geld(x.betrag)}</span></button>` : ""}
        <button id="fv-alle"><span class="ic">🔎</span>Alle Buchungen von „${esc(x.name)}“${jahr ? ` in ${f.monat.getFullYear()}` : ""}</button>
        <button id="fv-variabel" style="color:var(--pruefen)"><span class="ic">↔️</span>Nicht als Fixkosten zählen</button>
      </div>
      ${x.posten || postenNach.get(s) ? `<p class="leise" style="font-size:13px">${esc((x.posten || postenNach.get(s)).rhythmus)}, zuletzt ${geld((x.posten || postenNach.get(s)).betrag)}.</p>` : ""}`);
    $("#fv-oeffnen")?.addEventListener("click", () => eintragOeffnen(x.e.id));
    $("#fv-alle").onclick = () => alleBuchungen(x.name);
    $("#fv-variabel").onclick = () => {
      if (manuellFix) wkFixSetzen(wkFix().filter((y) => y !== s));
      else wkIgnorieren([...wkIgnoriert(), s]);
      sheetSchliessen(); toast(`„${x.name}“ zählt jetzt als variabel`); fixVariabelAnsicht();
    };
  }));
  $$("[data-varsumme]").forEach((b) => (b.onclick = () => {
    const x = varSummen.find((y) => y.schluessel === b.dataset.varsumme && String(y.kategorie_id ?? "") === b.dataset.k);
    sheetOeffnen(`<div class="griff"></div><div class="sheet-kopf"><button data-zu>Schließen</button><h3>${esc(x.name)}</h3><span style="width:60px"></span></div>
      <div class="mehr-liste">
        <button id="fv-alle"><span class="ic">🔎</span>Alle ${x.anzahl} Buchungen in ${f.monat.getFullYear()}<span class="rechts zahl">${geld(x.betrag)}</span></button>
        ${x.schluessel.startsWith("#") ? "" : `<button id="fv-fix"><span class="ic">📌</span>„${esc(x.name)}“ immer als Fixkosten zählen</button>`}
      </div>`);
    $("#fv-alle").onclick = () => alleBuchungen(x.name);
    $("#fv-fix")?.addEventListener("click", () => {
      wkFixSetzen([...new Set([...wkFix(), x.schluessel])]);
      wkIgnorieren(wkIgnoriert().filter((y) => y !== x.schluessel));
      sheetSchliessen(); toast(`„${x.name}“ zählt jetzt als Fixkosten`); fixVariabelAnsicht();
    });
  }));
  $$("[data-var]").forEach((b) => (b.onclick = () => {
    const e = variabel.find((y) => String(y.id) === b.dataset.var);
    const name = e.bezeichnung || e.haendler_roh || "";
    sheetOeffnen(`<div class="griff"></div><div class="sheet-kopf"><button data-zu>Schließen</button><h3>${esc(name || "Ausgabe")}</h3><span style="width:60px"></span></div>
      <div class="mehr-liste">
        <button id="fv-oeffnen"><span class="ic">✏️</span>Buchung öffnen<span class="rechts zahl">${geld(eigen(e))}</span></button>
        ${name ? `<button id="fv-fix"><span class="ic">📌</span>„${esc(name)}“ immer als Fixkosten zählen</button>` : ""}
      </div>`);
    $("#fv-oeffnen").onclick = () => eintragOeffnen(e.id);
    $("#fv-fix")?.addEventListener("click", () => {
      const s = wkSchluessel(e);
      wkFixSetzen([...new Set([...wkFix(), s])]);
      wkIgnorieren(wkIgnoriert().filter((y) => y !== s));
      sheetSchliessen(); toast(`„${name}“ zählt jetzt als Fixkosten`); fixVariabelAnsicht();
    });
  }));
}

async function wiederkehrendAnsicht() {
  unterseite("Wiederkehrend", '<div class="leer">Analysiere die letzten zwei Jahre …</div>');
  const alle = await zweiJahreLaden();
  const ignoriert = wkIgnoriert();
  const gefunden = wiederkehrendErkennen(alle);
  const sichtbar = gefunden.filter((x) => !ignoriert.includes(x.schluessel));
  const aktiv = sichtbar.filter((x) => x.aktiv && x.sicher), beendet = sichtbar.filter((x) => !x.aktiv);
  const vermutet = sichtbar.filter((x) => x.aktiv && !x.sicher);
  const summeMonat = aktiv.reduce((s, x) => s + x.proMonat, 0);
  const datum = (t) => ausIso(t).toLocaleDateString("de-DE", { day: "numeric", month: "short", year: "2-digit" });
  const zeile = (x) => `<button data-s="${esc(x.schluessel)}">
      <span class="ic" style="${icFarbe(x.kategorie?.farbe || "#9AA3AE")}">${esc(x.kategorie?.icon || "🔁")}</span>
      <span style="min-width:0;flex:1"><span style="display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(x.name)}${x.ueberfaellig ? '<span class="marke pruefen">überfällig</span>' : ""}</span>
        <small class="leise">${esc(x.rhythmus)}${x.tagImMonat ? `, um den ${x.tagImMonat}.` : ""} · ${x.aktiv ? "nächste ca. " + datum(x.naechster) : "zuletzt " + datum(x.letzter)}</small></span>
      <span class="rechts zahl" style="color:var(--tinte)">${x.schwankend ? "ca. " : ""}${geld(x.betrag)}${x.tage !== 30.44 ? `<br><small class="leise">${geld(x.proMonat)}/Monat</small>` : ""}</span>
    </button>`;
  const rhythmusSumme = (name) => aktiv.filter((x) => x.rhythmus === name).reduce((s, x) => s + x.betrag, 0);
  ansicht.innerHTML = `<div class="kopf"><button class="rund" id="zurueck" aria-label="Zurück">‹</button><h1>Wiederkehrend</h1></div>
    <div class="gesamt"><div class="betrag zahl">${esc(geld(summeMonat).split(",")[0])}<small>,${esc(geld(summeMonat).split(",")[1])}</small></div>
      <div class="vergleich">pro Monat für ${aktiv.length} laufende ${aktiv.length === 1 ? "Posten" : "Posten"}, ${geld(summeMonat * 12)} im Jahr</div></div>
    ${rhythmusSumme("jährlich") ? `<p class="leise" style="font-size:13.5px;margin:6px 4px">Davon jährlich fällig: ${geld(rhythmusSumme("jährlich"))}, auf den Monat umgerechnet.</p>` : ""}
    <div class="abschnitt">Laufend</div>
    <div class="mehr-liste">${aktiv.map(zeile).join("") || '<div class="leer">Nichts gefunden.</div>'}</div>
    ${vermutet.length ? `<div class="abschnitt">Vermutlich wiederkehrend (erst zweimal gebucht)</div><div class="mehr-liste">${vermutet.map(zeile).join("")}</div>
      <p class="leise" style="font-size:13px;margin:6px 4px">Zusammen ${geld(vermutet.reduce((s, x) => s + x.proMonat, 0))} pro Monat, nicht in der Summe oben. Einmalige Zufälle wie Geschenke kannst du ausblenden.</p>` : ""}
    ${beendet.length ? `<div class="abschnitt">Nicht mehr gebucht (vermutlich beendet)</div><div class="mehr-liste" style="opacity:.75">${beendet.map(zeile).join("")}</div>` : ""}
    ${ignoriert.length ? `<div style="text-align:center;margin:14px 0"><button class="knopf zweit klein" id="wk-reset">${ignoriert.length} ausgeblendete wieder anzeigen</button></div>` : ""}
    <p class="leise" style="font-size:13px;margin:16px 4px">Erkannt werden Ausgaben mit gleicher Bezeichnung, regelmäßigem Abstand und weitgehend gleichem Betrag, aus den Einträgen der letzten zwei Jahre. Tippe auf einen Posten für alle Buchungen oder zum Ausblenden.</p>`;
  $("#zurueck").onclick = mehrAnsicht;
  $("#wk-reset")?.addEventListener("click", () => { wkIgnorieren([]); wiederkehrendAnsicht(); });
  $$("[data-s]").forEach((b) => (b.onclick = () => {
    const x = gefunden.find((y) => y.schluessel === b.dataset.s);
    sheetOeffnen(`<div class="griff"></div><div class="sheet-kopf"><button data-zu>Schließen</button><h3>${esc(x.name)}</h3><span style="width:60px"></span></div>
      <div class="mehr-liste">
        <div class="zeile">Rhythmus<span class="rechts">${esc(x.rhythmus)}</span></div>
        <div class="zeile">Betrag<span class="rechts zahl">${x.schwankend ? "ca. " : ""}${geld(x.betrag)}</span></div>
        <div class="zeile">Pro Jahr<span class="rechts zahl">${geld(x.proMonat * 12)}</span></div>
        <div class="zeile">Gebucht<span class="rechts">${x.anzahl}× seit ${datum(x.erster)}</span></div>
        <div class="zeile">${x.aktiv ? "Nächste Buchung ca." : "Zuletzt"}<span class="rechts">${datum(x.aktiv ? x.naechster : x.letzter)}</span></div>
      </div>
      <div class="knopfreihe"><button class="knopf" id="wk-alle">Alle Buchungen zeigen</button><button class="knopf zweit" id="wk-weg">Ausblenden</button></div>`);
    $("#wk-alle").onclick = () => {
      sheetSchliessen();
      Object.assign(S.suche, { q: x.name, kategorie: "", konto: "", pruefen: false, offen: false, von: "", bis: "", sortierung: "" });
      tabWechseln("eintraege");
    };
    $("#wk-weg").onclick = () => { wkIgnorieren([...wkIgnoriert(), x.schluessel]); sheetSchliessen(); toast(`${x.name} ausgeblendet`); wiederkehrendAnsicht(); };
  }));
}

const FARBEN = ["#3C8D5A", "#5E9E2F", "#2E9AA6", "#3E8FC2", "#4C6EAD", "#3B5998", "#7A5BC4", "#8E44AD", "#B5527D", "#E07A9B", "#C8463D", "#D9534F", "#D08A2E", "#E0A21B", "#9C6B3E", "#5B6770", "#8A8F98", "#2C7873"];
function kategorienVerwalten() {
  const m = S.meta;
  unterseite("Kategorien", `<div class="knopfreihe" style="margin:0 0 6px"><button class="knopf" id="neu">Neue Kategorie</button></div>
    <div class="mehr-liste">${m.kategorien.map((k) => `<button data-k="${k.id}" style="${k.archiviert ? "opacity:.5" : ""}"><span class="ic" style="${icFarbe(k.farbe)}">${esc(k.icon)}</span>${esc(k.name)}<span class="rechts">${k.anzahl.toLocaleString("de-DE")}${k.archiviert ? ", archiviert" : ""}</span></button>`).join("")}</div>
    <p class="leise" style="font-size:13px;margin:10px 4px">Die Beschreibung hilft der KI beim Einordnen. Archivierte Kategorien bleiben in alten Einträgen erhalten, tauchen aber nicht mehr bei der Erfassung auf.</p>`);
  $("#neu").onclick = () => kategorieBearbeiten(null);
  $$("[data-k]").forEach((b) => (b.onclick = () => kategorieBearbeiten(kat(+b.dataset.k))));
}
function kategorieBearbeiten(k) {
  const neu = !k;
  const d = k ? { ...k } : { name: "", icon: "🏷️", farbe: FARBEN[0], beschreibung: "", sortierung: 500, archiviert: 0 };
  sheetOeffnen(`<div class="griff"></div><div class="sheet-kopf"><button data-zu>Abbrechen</button><h3>${neu ? "Neue Kategorie" : "Kategorie"}</h3><button class="fett" id="ok">Sichern</button></div>
    <div class="zwei" style="grid-template-columns:80px 1fr"><label class="feld"><span>Symbol</span><input id="icon" value="${esc(d.icon)}" style="text-align:center;font-size:22px"></label>
      <label class="feld"><span>Name</span><input id="name" value="${esc(d.name)}"></label></div>
    <div class="feld"><span>Farbe</span><div class="farbwahl">${FARBEN.map((f) => `<button data-f="${f}" class="${f === d.farbe ? "aktiv" : ""}" style="background:${f}" aria-label="Farbe ${f}"></button>`).join("")}</div></div>
    <label class="feld"><span>Beschreibung für die KI</span><input id="beschreibung" value="${esc(d.beschreibung || "")}" placeholder="z. B. Baumarkt, Möbel, Deko"></label>
    <label class="feld"><span>Reihenfolge (kleiner = weiter vorne)</span><input id="sortierung" inputmode="numeric" value="${d.sortierung}"></label>
    ${!neu ? `<label style="display:flex;gap:8px;align-items:center;margin:10px 2px"><input type="checkbox" id="archiviert" ${d.archiviert ? "checked" : ""}> Archiviert</label>
    <label class="feld"><span>Zusammenführen: alle ${k.anzahl} Einträge verschieben nach …</span><select id="ziel"><option value="">–</option>${S.meta.kategorien.filter((x) => x.id !== k.id).map((x) => `<option value="${x.id}">${esc(x.icon)} ${esc(x.name)}</option>`).join("")}</select></label>` : ""}`);
  let farbe = d.farbe;
  $$(".farbwahl button").forEach((b) => (b.onclick = () => { farbe = b.dataset.f; $$(".farbwahl button").forEach((x) => x.classList.toggle("aktiv", x === b)); }));
  $("#ok").onclick = async () => {
    try {
      const ziel = $("#ziel")?.value;
      if (ziel) {
        if (!confirm(`„${k.name}“ in „${kat(+ziel).name}“ aufgehen lassen? Die Kategorie wird danach gelöscht.`)) return;
        await api(`kategorien/${k.id}/zusammenfuehren`, { method: "POST", body: { ziel: +ziel } });
      } else {
        const body = { name: $("#name").value, icon: $("#icon").value || "🏷️", farbe, beschreibung: $("#beschreibung").value, sortierung: +$("#sortierung").value || 500, archiviert: $("#archiviert")?.checked ? 1 : 0 };
        await api(neu ? "kategorien" : `kategorien/${k.id}`, { method: neu ? "POST" : "PUT", body });
      }
      sheetSchliessen(); await metaLaden(); kategorienVerwalten(); toast("Gesichert");
    } catch (err) { toast(err.message); }
  };
}
function kontenVerwalten() {
  unterseite("Konten", `<div class="mehr-liste">${S.meta.konten.map((k) => `<button data-k="${k.id}" style="${k.archiviert ? "opacity:.5" : ""}"><span class="ic">${esc(k.icon)}</span>${esc(k.name)}<span class="rechts">${(k.typ || "").startsWith("auto") ? "automatisch" : "manuell"}</span></button>`).join("")}</div>
    <div class="zwei" style="grid-template-columns:70px 1fr auto;align-items:end"><label class="feld"><span>Symbol</span><input id="kicon" value="💳" style="text-align:center"></label>
      <label class="feld"><span>Neues Konto</span><input id="kname" placeholder="z. B. Sparkasse, Amex"></label><button class="knopf" id="kneu" style="margin-bottom:12px">Anlegen</button></div>`);
  $("#kneu").onclick = async () => { try { await api("konten", { method: "POST", body: { name: $("#kname").value, icon: $("#kicon").value } }); await metaLaden(); kontenVerwalten(); } catch (e) { toast(e.message); } };
  $$("[data-k]").forEach((b) => (b.onclick = async () => {
    const k = S.meta.konten.find((x) => x.id === +b.dataset.k);
    const name = prompt("Name des Kontos", k.name);
    if (name === null) return;
    const archiv = name === "" ? !k.archiviert : k.archiviert;
    await api(`konten/${k.id}`, { method: "PUT", body: { name: name || k.name, archiviert: archiv } });
    await metaLaden(); kontenVerwalten();
  }));
}
async function anteileAnsicht() {
  const r = await api("anteile");
  unterseite("Eigenanteile", `<p class="leise" style="font-size:14px;margin:0 4px 8px">Für diese Händler zählt nur dein Anteil, etwa bei einem geteilten Abo. Festlegen kannst du das in einem Eintrag unter „Mein Anteil“.</p>
    <div class="mehr-liste">${r.map((x) => `<div class="zeile"><span style="min-width:0"><b>${esc(x.name || x.muster)}</b><br><small class="leise">${x.art === "bruch" ? `Anteil ${esc(x.text)}` : `fest ${esc(x.text)}`}</small></span><button class="knopf gefahr klein rechts" data-a="${x.id}" style="width:auto">Entfernen</button></div>`).join("") || '<div class="leer">Noch keine Eigenanteile festgelegt.</div>'}</div>`);
  $$("[data-a]").forEach((b) => (b.onclick = async () => {
    const zurueck = confirm("Sollen auch die bisherigen Einträge dieses Händlers wieder mit dem vollen Betrag zählen?\n\nOK = ja, Abbrechen = nur künftige Einträge");
    await api(`anteile/${b.dataset.a}?rueckwirkend=${zurueck}`, { method: "DELETE" }); toast("Eigenanteil entfernt"); anteileAnsicht();
  }));
}

async function regelnAnsicht() {
  const r = await api("regeln");
  unterseite("Gelernte Zuordnungen", `<p class="leise" style="font-size:14px;margin:0 4px 8px">Entstehen, wenn du die Kategorie eines automatisch erfassten Umsatzes änderst oder bestätigst. Sie haben Vorrang vor Verlauf und KI.</p>
    <div class="mehr-liste">${r.map((x) => `<div class="zeile"><span style="min-width:0"><b>${esc(x.muster)}</b><br><small class="leise">${esc(x.kategorie_icon || "")} ${esc(x.kategorie || "?")}, „${esc(x.bezeichnung || "")}“, ${x.treffer}× genutzt</small></span><button class="knopf gefahr klein rechts" data-r="${x.id}" style="width:auto">Entfernen</button></div>`).join("") || '<div class="leer">Noch keine Zuordnungen gelernt.</div>'}</div>`);
  $$("[data-r]").forEach((b) => (b.onclick = async () => { await api(`regeln/${b.dataset.r}`, { method: "DELETE" }); regelnAnsicht(); }));
}

const TR_TEXT = { ok: "verbunden", login_noetig: "Anmeldung nötig", fehler: "Fehler", unbekannt: "startet", nicht_eingerichtet: "nicht eingerichtet" };
const kurzZeit = (iso) => new Date(iso).toLocaleString("de-DE", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
function trStatusText(t) {
  if (["ok", "unbekannt"].includes(t.zustand)) {
    const z = t.letzter_abruf ? "zuletzt " + kurzZeit(t.letzter_abruf) : TR_TEXT[t.zustand];
    return t.aktiv ? z : z + " · automatisch aus";
  }
  return TR_TEXT[t.zustand] || t.zustand;
}
const checkZeile = (id, an, text) => `<label class="check-zeile"><input type="checkbox" id="${id}" ${an ? "checked" : ""}><span>${text}</span></label>`;

async function trAnsicht() {
  const t = (S.tr = await api("tr"));
  if (!t.eingerichtet) { await bankenAnsicht(); return bankHinzufuegen("tr"); }
  const problem = ["login_noetig", "fehler"].includes(t.zustand);
  const ab = (t.import_ab || "").slice(0, 10);
  const knopfAbruf = `<button class="knopf ${problem ? "zweit" : ""}" id="tr-abruf">Jetzt abrufen</button>`;
  const knopfLogin = `<button class="knopf ${problem ? "" : "zweit"}" id="tr-login">Anmelden</button>`;
  unterseite("Trade Republic", `
    ${problem ? `<div class="hinweis" style="display:block"><b>${t.zustand === "login_noetig" ? "Anmeldung nötig." : "Fehler."}</b> ${esc(t.letzte_meldung)}</div>` : ""}
    <div class="knopfreihe" style="margin-top:0">${problem ? knopfLogin + knopfAbruf : knopfAbruf + knopfLogin}</div>
    <div class="abschnitt">Zugang</div>
    <div class="mehr-liste">
      <div class="zeile">Status<span class="rechts">${esc(trStatusText(t))}</span></div>
      <div class="zeile">Telefonnummer<span class="rechts">${esc(t.telefon_anzeige)}</span></div>
      <div class="zeile">PIN<span class="rechts">${t.pin_gespeichert ? "gespeichert" : "wird bei der Anmeldung abgefragt"}</span></div>
      ${t.letzte_meldung && !problem ? `<div class="zeile" style="display:block"><small class="leise">${esc(t.letzte_meldung)}</small></div>` : ""}
    </div>
    <div class="abschnitt">Einstellungen</div>
    <div class="zwei"><label class="feld"><span>Laufend übernehmen ab</span><input type="date" id="tr-ab" value="${esc(ab)}"></label>
      <label class="feld"><span>Abrufen alle (Minuten)</span><input type="number" id="tr-int" min="5" max="1440" inputmode="numeric" value="${t.intervall}"></label></div>
    ${checkZeile("tr-aktiv", t.aktiv, "Automatisch abrufen")}
    <label class="feld"><span>Telefonnummer</span><input id="tr-tel" inputmode="tel" autocomplete="tel" value="${esc(t.telefon)}"></label>
    <label class="feld"><span>PIN ändern bzw. speichern (leer lassen = unverändert)</span><input type="password" id="tr-pin" inputmode="numeric" maxlength="4" autocomplete="new-password"></label>
    <details class="erweitert"><summary>Erweitert: Ereignistypen</summary>
      <label class="feld"><span>Übernommene Ereignistypen (Komma, * als Platzhalter)</span><input id="tr-typen" value="${esc(t.typen.join(", "))}"></label>
      <label class="feld"><span>Ignorieren</span><input id="tr-ign" value="${esc(t.ignorieren.join(", "))}"></label>
      ${Object.keys(t.typen_letzter_abruf || {}).length ? `<small class="leise">Beim letzten Abruf gesehen:</small><pre class="klein">${esc(Object.entries(t.typen_letzter_abruf).map(([k, v]) => `${k}: ${v}`).join("\n"))}</pre>` : ""}
    </details>
    <div class="knopfreihe"><button class="knopf zweit" id="tr-sichern">Einstellungen sichern</button></div>
    <div class="knopfreihe"><button class="knopf zweit" id="tr-voll">Ab Startdatum neu laden</button>${t.pin_gespeichert ? '<button class="knopf zweit" id="tr-pinweg">Gespeicherte PIN löschen</button>' : ""}</div>
    <div class="knopfreihe"><button class="knopf gefahr" id="tr-weg">Trade Republic entfernen</button></div>
    <p class="leise" style="font-size:13px;margin:12px 4px">Trade Republic verlangt bei jeder neuen Anmeldung eine Bestätigung in der TR-App. Danach läuft der Abruf ohne Rückfrage, bis Trade Republic die Sitzung beendet. Dann steht hier „Anmeldung nötig“.</p>`);
  $("#zurueck").onclick = bankenAnsicht;
  const abruf = async (voll) => {
    toast("Rufe Trade Republic ab …");
    try {
      const r = await api("tr/abrufen" + (voll ? "?voll=true" : ""), { method: "POST" });
      toast(["login_noetig", "fehler"].includes(r.status.zustand) ? r.status.letzte_meldung : `${r.neu} neue Umsätze`);
      await metaLaden(); trAnsicht();
    } catch (err) { toast(err.message); }
  };
  $("#tr-abruf").onclick = () => abruf(false);
  $("#tr-voll").onclick = () => abruf(true);
  $("#tr-login").onclick = () => trAnmelden("", () => trAnsicht());
  $("#tr-sichern").onclick = async () => {
    const liste = (id) => $(id).value.split(",").map((x) => x.trim()).filter(Boolean);
    const body = { intervall: +$("#tr-int").value || 15, aktiv: $("#tr-aktiv").checked, typen: liste("#tr-typen"), ignorieren: liste("#tr-ign") };
    if ($("#tr-ab").value !== ab) body.import_ab = $("#tr-ab").value ? $("#tr-ab").value + "T00:00:00" : "";
    if ($("#tr-tel").value.trim() && $("#tr-tel").value.trim() !== t.telefon) body.telefon = $("#tr-tel").value;
    if ($("#tr-pin").value) body.pin = $("#tr-pin").value;
    try {
      const n = await api("tr", { method: "PUT", body });
      toast("Gesichert");
      if (body.telefon && n.telefon !== t.telefon) trAnmelden(body.pin || "", () => trAnsicht()); else trAnsicht();
    } catch (err) { toast(err.message); }
  };
  $("#tr-pinweg")?.addEventListener("click", async () => { await api("tr", { method: "PUT", body: { pin: "" } }); toast("PIN gelöscht"); trAnsicht(); });
  $("#tr-weg").onclick = async () => {
    if (!confirm("Trade Republic entfernen? Telefonnummer, PIN und Sitzung werden gelöscht. Bereits übernommene Einträge bleiben erhalten.")) return;
    await api("tr", { method: "DELETE" }); S.tr = null; toast("Trade Republic entfernt"); bankenAnsicht();
  };
}

function trAnmelden(pin = "", danach = null, speichern = false) {
  sheetOeffnen(`<div class="griff"></div><div class="sheet-kopf"><span style="width:60px"></span><h3>Trade Republic</h3><span style="width:60px"></span></div><div id="trlog"><div class="leer">Verbinde mit Trade Republic …</div></div>`);
  let laeuft = true, angezeigt = "";
  const zeigen = (s) => {
    const box = $("#trlog");
    if (!box) { laeuft = false; return; }
    if (s.zustand === angezeigt && s.zustand === "warte") return;
    angezeigt = s.zustand;
    if (s.zustand === "pin_noetig") {
      laeuft = false;
      box.innerHTML = `<label class="feld"><span>PIN der Trade-Republic-App</span><input id="tl-pin" type="password" inputmode="numeric" maxlength="4" autocomplete="current-password"></label>
        ${checkZeile("tl-merken", false, "PIN im Add-on speichern")}
        <div class="knopfreihe"><button class="knopf" id="tl-ok">Weiter</button><button class="knopf zweit" id="tl-zu">Abbrechen</button></div>`;
      setTimeout(() => $("#tl-pin")?.focus(), 250);
      $("#tl-zu").onclick = sheetSchliessen;
      $("#tl-ok").onclick = () => {
        const p = $("#tl-pin").value.trim();
        if (!/^\d{4}$/.test(p)) { toast("Die PIN hat 4 Ziffern"); return; }
        trAnmelden(p, danach, $("#tl-merken").checked);
      };
    } else if (s.zustand === "code_noetig") {
      laeuft = false;
      box.innerHTML = `<label class="feld"><span>Code aus der Authenticator-App</span><input id="tl-code" inputmode="numeric" autocomplete="one-time-code"></label>
        <div class="knopfreihe"><button class="knopf" id="tl-ok">Bestätigen</button><button class="knopf zweit" id="tl-zu">Abbrechen</button></div>`;
      $("#tl-zu").onclick = sheetSchliessen;
      $("#tl-ok").onclick = async () => {
        box.innerHTML = '<div class="leer">Prüfe Code …</div>'; angezeigt = "";
        try { zeigen(await api("tr/login", { method: "POST", body: { code: $("#tl-code")?.value || "" } })); } catch (err) { zeigen({ zustand: "fehler", meldung: err.message }); }
      };
    } else if (s.zustand === "warte") {
      box.innerHTML = `<div style="text-align:center;padding:18px 0"><div style="font-size:44px">📲</div><p style="font-size:17px;font-weight:600;margin:8px 0">In der Trade-Republic-App bestätigen</p><p class="leise">Die Anfrage ist gerade auf deinem Handy angekommen.</p></div>
        <div class="knopfreihe"><button class="knopf zweit" id="tl-zu">Schließen</button></div>`;
      $("#tl-zu").onclick = sheetSchliessen;
    } else {
      laeuft = false;
      const ok = s.zustand === "ok";
      const text = s.meldung === "Angemeldet" ? "Angemeldet. Die Umsätze werden jetzt im Hintergrund abgerufen." : s.meldung;
      box.innerHTML = `<div class="hinweis ${ok ? "ok" : ""}"><span>${esc(text)}</span></div>
        <div class="knopfreihe"><button class="knopf" id="tl-fertig">${ok ? "Fertig" : "Schließen"}</button></div>`;
      $("#tl-fertig").onclick = async () => { sheetSchliessen(); await metaLaden(); danach?.(); };
    }
  };
  (async () => {
    try { zeigen(await api("tr/login", { method: "POST", body: pin ? { pin, pin_speichern: speichern } : {} })); }
    catch (err) { zeigen({ zustand: "fehler", meldung: err.message }); return; }
    while (laeuft) {
      await new Promise((r) => setTimeout(r, 2000));
      if (!laeuft || $("#sheet").hidden) break;
      try { zeigen(await api("tr/login")); } catch (_) { /* weiter versuchen */ }
    }
  })();
}
const kostenText = (eur, usd = null) => {
  const v = eur ?? usd;
  if (v == null) return "–";
  const zeichen = eur != null ? "€" : "$";
  if (v >= 1) return v.toLocaleString("de-DE", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " " + zeichen;
  const cent = v * 100;
  return cent.toLocaleString("de-DE", { maximumFractionDigits: cent && cent < 0.1 ? 3 : 2 }) + (eur != null ? " ct" : " ¢");
};
const tokenText = (n) => n >= 1e6 ? (n / 1e6).toLocaleString("de-DE", { maximumFractionDigits: 1 }) + " Mio." : n.toLocaleString("de-DE");

function kiVerbrauch(n) {
  const box = $("#ki-verbrauch");
  if (!box) return;
  const [heute, monat, gesamt] = n.zeitraeume;
  const kachel = (titel, z) => `<div class="ki-kachel"><small>${titel}</small><b>${kostenText(z.eur, z.usd)}</b><small>${z.anfragen.toLocaleString("de-DE")} ${z.anfragen === 1 ? "Anfrage" : "Anfragen"}</small></div>`;
  const tage = n.tage.map((t) => ({ label: ausIso(t.tag).toLocaleDateString("de-DE", { day: "numeric", month: "short" }) + ` · ${t.anfragen} Anfr.`, kurz: ausIso(t.tag).getDate() + ".", wert: t.eur ?? t.usd }));
  const fmt = (w) => kostenText(n.usd_eur ? w : null, n.usd_eur ? null : w);
  const diagramm = n.tage.some((t) => t.anfragen) ? balken(tage, { hoehe: 110, titel: "KI-Kosten je Tag", maxLabels: 6, format: fmt, mindestMax: 1e-9 }).svg : "";
  const zweckZeile = (z) => {
    const einheit = z.zweck === "beleg" ? (z.anzahl === 1 ? "Bon" : "Bons") : z.zweck === "kategorisierung" ? "Umsätze" : "Tests";
    const schnitt = z.anzahl ? ` · Ø ${kostenText(z.eur != null ? z.eur / z.anzahl : null, z.eur == null ? z.usd / z.anzahl : null)} je ${z.zweck === "beleg" ? "Bon" : "Umsatz"}` : "";
    return `<div class="zeile" style="display:block">${esc(z.name)}<span class="rechts" style="float:right">${kostenText(z.eur, z.usd)}</span><br><small class="leise">${z.anfragen} ${z.anfragen === 1 ? "Anfrage" : "Anfragen"}${z.zweck !== "test" ? ` für ${z.anzahl} ${einheit}` : ""}${z.zweck !== "test" ? schnitt : ""}${z.fehler ? ` · ${z.fehler} fehlgeschlagen` : ""}</small></div>`;
  };
  const p = n.preis;
  box.innerHTML = `
    <div class="ki-kacheln">${kachel("Heute", heute)}${kachel(MONATE[new Date().getMonth()], monat)}${kachel("Gesamt", gesamt)}</div>
    ${diagramm ? `<div class="block" style="padding:10px 12px 6px" id="ki-diagramm"><small class="leise">Letzte 30 Tage, Balken antippen für Details</small>${diagramm}<div class="diagramm-info" id="ki-diagramm-info"></div></div>` : ""}
    ${n.zwecke_monat.length ? `<div class="mehr-liste">${n.zwecke_monat.map(zweckZeile).join("")}
      <div class="zeile">Tokens im ${MONATE[new Date().getMonth()]}<span class="rechts">${tokenText(monat.eingabe)} ein · ${tokenText(monat.ausgabe)} aus</span></div></div>` : '<div class="leer" style="padding:14px">Noch keine KI-Anfragen gezählt.</div>'}
    ${monat.ohne_preis ? `<div class="hinweis" style="display:block">Für ${monat.ohne_preis} Anfragen ist kein Preis bekannt. Trag unten den Preis für das Modell ein.</div>` : ""}
    <div class="zwei" style="margin-top:12px"><label class="feld"><span>Eingabe, $ je 1 Mio. Tokens</span><input id="preis-ein" inputmode="decimal" value="${p ? String(p.eingabe).replace(".", ",") : ""}"></label>
      <label class="feld"><span>Ausgabe, $ je 1 Mio. Tokens</span><input id="preis-aus" inputmode="decimal" value="${p ? String(p.ausgabe).replace(".", ",") : ""}"></label></div>
    <div class="knopfreihe" style="margin-top:0"><button class="knopf zweit" id="preis-sichern">Preis sichern</button>${p && p.quelle === "eigen" && n.standard_preis ? '<button class="knopf zweit" id="preis-standard">Listenpreis verwenden</button>' : ""}</div>
    <p class="leise" style="font-size:13px;margin:8px 4px">Gezählt ${n.seit ? "seit " + new Date(n.seit).toLocaleDateString("de-DE") : "ab jetzt"}: nur Anfragen dieses Add-ons, mit den Tokenzahlen aus den Antworten der API. Die Kosten sind Tokens mal Preis für ${esc(n.modell)} (${p ? (p.quelle === "eigen" ? "eigener Preis" : "Listenpreis von Anthropic, Stand " + esc(n.stand_preise)) : "kein Preis bekannt"})${n.usd_eur ? `, umgerechnet mit dem EZB-Kurs (1 $ = ${n.usd_eur.toLocaleString("de-DE", { maximumFractionDigits: 4 })} €)` : ""}. Verbindlich ist die Abrechnung in der Anthropic Console unter „Usage“ und „Cost“.</p>`;
  if (diagramm) balkenKlicks($("#ki-diagramm"), tage, $("#ki-diagramm-info"), null, fmt);
  const preisSenden = async (body) => {
    try { kiVerbrauch(await api("ki/preise", { method: "PUT", body: { modell: n.modell, ...body } })); toast("Preis gesichert"); }
    catch (err) { toast(err.message); }
  };
  $("#preis-sichern").onclick = () => preisSenden({ eingabe: $("#preis-ein").value, ausgabe: $("#preis-aus").value });
  $("#preis-standard")?.addEventListener("click", () => preisSenden({ standard: true }));
}

async function kiAnsicht() {
  const [, nutzung] = await Promise.all([metaLaden(), api("ki/nutzung")]);
  const ki = S.meta.ki;
  unterseite("KI-Kategorisierung", `<div class="mehr-liste">
      <div class="zeile">Status<span class="rechts">${ki.aktiv ? "aktiv" : "aus (kein API-Key oder deaktiviert)"}</span></div>
      <div class="zeile">Modell<span class="rechts">${esc(ki.modell)}</span></div>
      ${ki.letzter_fehler ? `<div class="zeile" id="ki-fehler" style="display:block"><small style="color:var(--pruefen)">${esc(ki.letzter_fehler)}</small></div>` : ""}
    </div>
    <div class="abschnitt">Verbrauch</div>
    <div id="ki-verbrauch"></div>
    <div class="abschnitt">Einordnung</div>
    <p class="leise" style="font-size:14px;margin:0 4px 8px">Reihenfolge: gelernte Zuordnung, dann eigener Verlauf (seit 2013), erst danach Claude. An Claude gehen Händlername, Betrag und Datum der unbekannten Umsätze, deine Kategorien mit typischen Bezeichnungen als Beispiele und, falls eingetragen, dein Wohnort. Beim Kassenbon-Scan geht zusätzlich das Foto des Bons an Claude; gespeichert wird es nicht. API-Key und Modell stellst du in den Add-on-Optionen ein.</p>
    <label class="feld"><span>Wohnort (optional, hilft bei lokalen Händlern)</span><input id="ki-wohnort" placeholder="z. B. Musterstadt" value="${esc(S.meta.einstellungen.wohnort || "")}"></label>
    <label class="feld"><span>Mehrdeutige Händler (landen immer in „Prüfen“, Komma-getrennt)</span><input id="ki-mehrdeutig" value="${esc(S.meta.einstellungen.mehrdeutig.join(", "))}"></label>
    <div class="knopfreihe" style="margin-top:0"><button class="knopf zweit" id="ki-sichern">Sichern</button></div>
    <label class="feld"><span>Test mit Händlername</span><input id="testname" value="BACKSTUBE SIMON GMBH 0815"></label>
    <div class="knopfreihe"><button class="knopf" id="test" ${ki.aktiv ? "" : "disabled"}>Testen</button><button class="knopf zweit" id="neu">Offene Einträge neu einordnen</button></div>
    <div id="ergebnis"></div>`);
  kiVerbrauch(nutzung);
  const neuLaden = async () => { try { kiVerbrauch(await api("ki/nutzung")); } catch (_) { /* egal */ } };
  $("#test").onclick = async () => {
    try { const r = await api("ki/test", { method: "POST", body: { haendler: $("#testname").value } }); $("#ki-fehler")?.remove(); metaLaden(); $("#ergebnis").innerHTML = `<div class="hinweis ok">${esc(r.haendler)} → <b style="color:inherit">${esc(r.kategorie)}</b>, „${esc(r.bezeichnung)}“, ${Math.round(r.sicherheit * 100)} % sicher</div>`; }
    catch (err) { $("#ergebnis").innerHTML = `<div class="hinweis">${esc(err.message)}</div>`; }
    neuLaden();
  };
  $("#ki-sichern").onclick = async () => {
    await api("einstellungen", { method: "PUT", body: { wohnort: $("#ki-wohnort").value, mehrdeutig: $("#ki-mehrdeutig").value.split(",").map((x) => x.trim()).filter(Boolean) } });
    toast("Gesichert"); await metaLaden();
  };
  $("#neu").onclick = async () => { const r = await api("pruefen/neu_kategorisieren", { method: "POST" }); toast(`${r.bearbeitet} Einträge neu eingeordnet`); await metaLaden(); neuLaden(); };
}

function exportSheet(filter) {
  const heute = new Date();
  sheetOeffnen(`<div class="griff"></div><div class="sheet-kopf"><button data-zu>Schließen</button><h3>Export</h3><span style="width:60px"></span></div>
    <div class="zwei"><label class="feld"><span>Von</span><input type="date" id="evon" value="${esc(filter.von || `${heute.getFullYear()}-01-01`)}"></label>
    <label class="feld"><span>Bis</span><input type="date" id="ebis" value="${esc(filter.bis || isoTag(heute))}"></label></div>
    ${filter.q || filter.kategorie || filter.konto ? `<p class="leise" style="font-size:13px">Aktive Filter aus der Suche werden übernommen.</p>` : ""}
    <div class="knopfreihe"><button class="knopf" id="xlsx">Excel (.xlsx)</button><button class="knopf zweit" id="csv">CSV</button></div>
    <div class="knopfreihe"><button class="knopf zweit" id="ablage">Excel im Ordner /share/ausgaben ablegen</button></div>
    <p class="leise" style="font-size:13px">Falls der Download in der Home-Assistant-App nicht startet: Datei im Share-Ordner ablegen und per Samba oder Cyberduck abholen.</p>`);
  const laden = async (format) => {
    const p = qs({ ...filter, pruefen: filter.pruefen ? 1 : "", offen: filter.offen ? 1 : "", von: $("#evon").value, bis: $("#ebis").value, format });
    try {
      const r = await api("export?" + p, { roh: true });
      const blob = await r.blob();
      const name = (r.headers.get("Content-Disposition") || "").match(/filename="(.+)"/)?.[1] || `ausgaben.${format}`;
      const url = URL.createObjectURL(blob);
      const a = Object.assign(document.createElement("a"), { href: url, download: name });
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
    } catch (err) { toast(err.message); }
  };
  $("#xlsx").onclick = () => laden("xlsx");
  $("#ablage").onclick = async () => {
    try { const r = await api("export?" + qs({ ...filter, pruefen: filter.pruefen ? 1 : "", offen: filter.offen ? 1 : "", von: $("#evon").value, bis: $("#ebis").value, ablage: 1 })); toast("Abgelegt: " + r.pfad); }
    catch (err) { toast(err.message); }
  };
  $("#csv").onclick = () => laden("csv");
}
function importSheet() {
  sheetOeffnen(`<div class="griff"></div><div class="sheet-kopf"><button data-zu>Schließen</button><h3>Next importieren</h3><span style="width:60px"></span></div>
    <p style="font-size:14.5px">Wähle den Excel-Export aus Next („Alle Daten“). Bereits importierte Zeilen werden erkannt und übersprungen.</p>
    <label class="feld"><span>Datei (.xls)</span><input type="file" id="datei" accept=".xls,.xlsx,application/vnd.ms-excel"></label>
    <label class="feld"><span>Nur Einträge bis einschließlich (leer = alle)</span><input type="date" id="ibis"></label>
    <label style="display:flex;gap:8px;align-items:center;font-size:14.5px;margin:4px 2px"><input type="checkbox" id="itr" checked> Trade Republic ab dem Folgetag übernehmen</label>
    <div class="knopfreihe"><button class="knopf" id="los">Importieren</button></div><div id="iergebnis"></div>`);
  $("#los").onclick = async () => {
    const f = $("#datei").files[0];
    if (!f) { toast("Bitte eine Datei wählen"); return; }
    const fd = new FormData(); fd.append("datei", f);
    if ($("#ibis").value) { fd.append("bis", $("#ibis").value); if ($("#itr").checked) fd.append("tr_folgetag", "1"); }
    $("#los").disabled = true; $("#los").textContent = "Importiere …";
    try {
      const r = await api("import/next", { method: "POST", body: fd });
      const ab = r.tr_import_ab ? new Date(r.tr_import_ab).toLocaleDateString("de-DE") : "–";
      $("#iergebnis").innerHTML = `<div class="hinweis ok"><span>${r.neu.toLocaleString("de-DE")} neu, ${r.schon_vorhanden.toLocaleString("de-DE")} schon vorhanden${r.nach_stichtag ? `, ${r.nach_stichtag.toLocaleString("de-DE")} nach dem Stichtag ausgelassen` : ""}. Trade Republic wird ab ${esc(ab)} übernommen.</span></div>
        <div class="knopfreihe"><button class="knopf zweit" id="itr-laden">Trade Republic jetzt ab ${esc(ab)} laden</button></div>`;
      $("#itr-laden").onclick = async () => {
        $("#itr-laden").disabled = true; $("#itr-laden").textContent = "Lade …";
        try { const t = await api("tr/abrufen?voll=true", { method: "POST" }); toast(`${t.neu} Umsätze von Trade Republic übernommen`); }
        catch (err) { toast(err.message); }
        $("#itr-laden").disabled = false; $("#itr-laden").textContent = "Erneut laden";
      };
      await metaLaden();
    } catch (err) { $("#iergebnis").innerHTML = `<div class="hinweis">${esc(err.message)}</div>`; }
    $("#los").disabled = false; $("#los").textContent = "Importieren";
  };
}

function zuruecksetzenSheet() {
  const ab = (S.meta.tr.import_ab || "").slice(0, 10);
  sheetOeffnen(`<div class="griff"></div><div class="sheet-kopf"><button data-zu>Abbrechen</button><h3>Alle Einträge löschen</h3><span style="width:60px"></span></div>
    <p style="font-size:14.5px">Löscht alle ${S.meta.anzahl_gesamt.toLocaleString("de-DE")} Einträge. Kategorien, Konten, die Trade-Republic-Anmeldung und die Einstellungen bleiben erhalten. Danach kannst du Next und Trade Republic neu importieren.</p>
    <label class="feld"><span>Trade Republic übernehmen ab</span><input type="date" id="zab" value="${esc(ab)}"></label>
    <label style="display:flex;gap:8px;align-items:center;font-size:14.5px;margin:4px 2px"><input type="checkbox" id="zregeln"> Gelernte Zuordnungen auch löschen</label>
    <label class="feld"><span>Zur Bestätigung LÖSCHEN eintippen</span><input id="zok" autocomplete="off" autocapitalize="characters"></label>
    <div class="knopfreihe"><button class="knopf" id="zlos" style="background:var(--pruefen)" disabled>Endgültig löschen</button></div>
    <p class="leise" style="font-size:13px">Lässt sich nicht rückgängig machen, außer über ein Home-Assistant-Backup.</p>`);
  $("#zok").oninput = () => { $("#zlos").disabled = $("#zok").value.trim().toUpperCase() !== "LÖSCHEN"; };
  $("#zlos").onclick = async () => {
    try {
      const r = await api("admin/zuruecksetzen", { method: "POST", body: {
        bestaetigung: $("#zok").value, regeln_loeschen: $("#zregeln").checked,
        tr_import_ab: $("#zab").value ? $("#zab").value + "T00:00:00" : "" } });
      sheetSchliessen(); toast(`${r.geloescht.toLocaleString("de-DE")} Einträge gelöscht`);
      await metaLaden(); mehrAnsicht();
    } catch (err) { toast(err.message); }
  };
}

// ---------------------------------------------------------------- Doppelte Eintraege
const QUELLEN = { tr: "Trade Republic", next: "Next", manuell: "von Hand", bank: "Bank" };
function dupIgnoriert() { try { return JSON.parse(localStorage.getItem("ausgaben_dup_ignoriert") || "[]"); } catch (_) { return []; } }
function dupIgnorieren(liste) { try { localStorage.setItem("ausgaben_dup_ignoriert", JSON.stringify(liste.slice(-3000))); } catch (_) { /* egal */ } }

function doppelteFinden(eintraege, maxTage) {
  const toepfe = new Map();
  for (const e of eintraege) {
    if (!e.betrag) continue;
    const k = `${e.waehrung}|${Math.round(e.betrag * 100)}`;
    if (!toepfe.has(k)) toepfe.set(k, []);
    toepfe.get(k).push(e);
  }
  const paare = [];
  for (const liste of toepfe.values()) {
    if (liste.length < 2) continue;
    liste.sort((a, b) => a.datum.localeCompare(b.datum));
    for (let i = 0; i < liste.length; i++) {
      for (let j = i + 1; j < liste.length; j++) {
        const a = liste[i], b = liste[j];
        const tage = tageZwischen(a.datum, b.datum);
        if (tage > maxTage) break;
        // Zwei echte Bankumsaetze sind kein Duplikat, ebenso Teile einer Aufteilung
        if (a.quelle === "tr" && b.quelle === "tr") continue;
        if (a.quelle === "bank" && b.quelle === "bank" && a.konto_id === b.konto_id) continue;
        const ra = a.quelle_ref || "", rb = b.quelle_ref || "";
        if (ra && rb && (ra.split("#")[0] === rb.split("#")[0])) continue;
        const verschieden = a.quelle !== b.quelle;
        const gleicherName = (a.bezeichnung || "").trim().toLowerCase() === (b.bezeichnung || "").trim().toLowerCase();
        paare.push({ a, b, tage, verschieden, gleicherName, schluessel: [a.id, b.id].sort((x, y) => x - y).join("-"),
          rang: (verschieden ? 0 : 2) + (gleicherName || verschieden ? 0 : 1) + tage / 10 });
      }
    }
  }
  return paare.sort((x, y) => x.rang - y.rang || y.a.datum.localeCompare(x.a.datum));
}

async function doppelteAnsicht() {
  S.dup ??= { monate: 6, nurQuellen: false, tage: 2 };
  const o = S.dup;
  unterseite("Doppelte Einträge", '<div class="leer">Suche …</div>');
  const von = o.monate ? isoTag(new Date(new Date().getFullYear(), new Date().getMonth() - o.monate, 1)) : "";
  const alle = [];
  for (let offset = 0; offset < 50000;) {
    const r = await api("eintraege?" + qs({ von, limit: 2000, offset, sortierung: "alt" }));
    alle.push(...r.eintraege); offset += r.eintraege.length;
    if (!r.eintraege.length || offset >= r.anzahl) break;
  }
  const ignoriert = new Set(dupIgnoriert());
  let paare = doppelteFinden(alle, o.tage).filter((p) => !ignoriert.has(p.schluessel));
  if (o.nurQuellen) paare = paare.filter((p) => p.verschieden);
  const gesamt = paare.length;
  paare = paare.slice(0, 150);
  const datum = (t) => `${WT[ausIso(t).getDay()]}, ${ausIso(t).toLocaleDateString("de-DE", { day: "numeric", month: "short", year: "2-digit" })}${t.slice(11, 16) !== "00:00" ? " " + t.slice(11, 16) : ""}`;
  const zeile = (e) => {
    const k = kat(e.kategorie_id);
    return `<div class="dup-zeile" data-id="${e.id}">
      <span class="ic" style="${icFarbe(k?.farbe || "#9AA3AE")}">${esc(k?.icon || "❔")}</span>
      <span style="min-width:0"><span class="titel">${esc(e.bezeichnung || e.haendler_roh || "Ohne Bezeichnung")}</span>
        <small class="leise">${esc(datum(e.datum))} · ${esc(e.quelle === "bank" ? e.konto || "Bank" : QUELLEN[e.quelle] || e.quelle)}${k ? " · " + esc(k.name) : ""}</small></span>
      <button class="knopf gefahr klein" data-del="${e.id}">Löschen</button></div>`;
  };
  ansicht.innerHTML = `<div class="kopf"><button class="rund" id="zurueck" aria-label="Zurück">‹</button><h1>Doppelte Einträge</h1></div>
    <div class="chips">${[[3, "3 Monate"], [6, "6 Monate"], [12, "12 Monate"], [0, "Alles"]].map(([m, t]) => `<button class="chip ${o.monate === m ? "aktiv" : ""}" data-m="${m}">${t}</button>`).join("")}
      <button class="chip ${o.nurQuellen ? "aktiv" : ""}" id="d-quellen">Nur verschiedene Quellen</button>
      <button class="chip ${o.tage === 0 ? "aktiv" : ""}" id="d-tag">Nur gleicher Tag</button></div>
    <p class="leise" style="font-size:13.5px;margin:8px 4px">${gesamt ? `${gesamt} mögliche ${gesamt === 1 ? "Doppelung" : "Doppelungen"}: gleicher Betrag, höchstens ${o.tage === 0 ? "am selben Tag" : o.tage + " Tage Abstand"}. Wahrscheinlichste zuerst.` : "Keine möglichen Doppelungen gefunden."}${gesamt > 150 ? " Angezeigt werden die ersten 150." : ""}</p>
    ${paare.map((p) => `<div class="pruefkarte" data-p="${p.schluessel}">
      <div class="oben"><b class="zahl">${geld(p.a.betrag, p.a.waehrung)}</b>
        <span class="leise" style="font-size:13px">${p.tage === 0 ? "selber Tag" : p.tage === 1 ? "1 Tag Abstand" : p.tage + " Tage Abstand"}${p.verschieden ? ' · <span class="marke pruefen">verschiedene Quellen</span>' : ""}</span></div>
      ${zeile(p.a)}${zeile(p.b)}
      <div class="knopfreihe"><button class="knopf zweit klein" data-ok="${p.schluessel}">Kein Duplikat</button></div></div>`).join("")}
    ${ignoriert.size ? `<div style="text-align:center;margin:14px 0"><button class="knopf zweit klein" id="d-reset">${ignoriert.size} als „kein Duplikat“ markierte wieder anzeigen</button></div>` : ""}`;
  $("#zurueck").onclick = mehrAnsicht;
  $$("[data-m]").forEach((b) => (b.onclick = () => { o.monate = +b.dataset.m; doppelteAnsicht(); }));
  $("#d-quellen").onclick = () => { o.nurQuellen = !o.nurQuellen; doppelteAnsicht(); };
  $("#d-tag").onclick = () => { o.tage = o.tage === 0 ? 2 : 0; doppelteAnsicht(); };
  $("#d-reset")?.addEventListener("click", () => { dupIgnorieren([]); doppelteAnsicht(); });
  $$("[data-ok]").forEach((b) => (b.onclick = () => {
    dupIgnorieren([...dupIgnoriert(), b.dataset.ok]);
    b.closest(".pruefkarte").remove();
  }));
  $$(".dup-zeile").forEach((z) => {
    const e = alle.find((x) => x.id === +z.dataset.id);
    z.children[1].onclick = () => erfassen(e);
  });
  $$("[data-del]").forEach((b) => (b.onclick = async () => {
    const id = +b.dataset.del, e = alle.find((x) => x.id === id);
    if (!confirm(`„${e.bezeichnung || e.haendler_roh}“ vom ${ausIso(e.datum).toLocaleDateString("de-DE")} über ${geld(e.betrag, e.waehrung)} löschen?`)) return;
    try {
      await api(`eintraege/${id}`, { method: "DELETE" });
      // Alle Karten mit diesem Eintrag entfernen
      $$(`.dup-zeile[data-id="${id}"]`).forEach((x) => x.closest(".pruefkarte")?.remove());
      toast("Eintrag gelöscht");
    } catch (err) { toast(err.message); }
  }));
}

// ---------------------------------------------------------------- Banken (FinTS)
const BANK_LOGIN = { dkb: "Anmeldename", comdirect: "Zugangsnummer" };
async function bankenAnsicht() {
  const [d, t] = await Promise.all([api("banken"), api("tr")]);
  S.banken = d; S.tr = t;
  const zustand = (b) => b.job && ["laeuft", "tan_noetig"].includes(b.job.zustand) ? "läuft …"
    : b.meldung ? "Fehler" : b.letzter_abruf ? "zuletzt " + kurzZeit(b.letzter_abruf)
    : b.konten.length ? "verbunden" : "noch nicht verbunden";
  const trProblem = ["login_noetig", "fehler"].includes(t.zustand);
  const fints = d.banken;
  unterseite("Banken", `
    ${fints.length && !d.produkt_id ? `<div class="hinweis" style="display:block"><b>Produkt-ID fehlt.</b> DKB, comdirect und andere Banken lassen den Abruf nur mit einer registrierten FinTS-Produkt-ID zu. Beantragen kannst du sie kostenlos auf fints.org unter Hersteller → Produktregistrierung. Trag sie unten ein, sobald die E-Mail da ist.</div>` : ""}
    <div class="mehr-liste">
      ${t.eingerichtet ? `<button id="b-tr"><span class="ic">🟦</span><span style="flex:1;min-width:0">Trade Republic<br><small class="leise">${esc(t.telefon_anzeige)} · ${esc(trStatusText(t))}</small></span>${trProblem ? '<span class="marke pruefen">!</span>' : ""}</button>` : ""}
      ${fints.map((b) => `<button data-b="${b.id}"><span class="ic">🏦</span><span style="flex:1;min-width:0">${esc(b.name)}<br><small class="leise">${b.konten.filter((k) => k.aktiv).length} von ${b.konten.length} Konten · ${esc(zustand(b))}</small></span>
        ${b.offen_nachholen ? `<span class="marke pruefen">${b.offen_nachholen} offen</span>` : ""}${b.meldung ? '<span class="marke pruefen">!</span>' : ""}</button>`).join("")}
      ${!t.eingerichtet && !fints.length ? '<div class="leer">Noch keine Bank verbunden.</div>' : ""}
    </div>
    <div class="knopfreihe"><button class="knopf" id="b-neu">Bank hinzufügen</button></div>
    <p class="leise" style="font-size:13px;margin:12px 4px">Zugangsdaten, Startdatum und Abrufzeiten stellst du je Bank ein. Gespeicherte PINs liegen unverschlüsselt in der Add-on-Datenbank und damit in deinen Home-Assistant-Backups.</p>
    ${fints.length || d.produkt_id ? `<div class="abschnitt">DKB, comdirect und andere (FinTS)</div>
    <label class="feld"><span>FinTS-Produkt-ID (aus der Registrierungs-E-Mail)</span><input id="b-pid" value="${esc(d.produkt_id)}" autocomplete="off" autocapitalize="characters"></label>
    <label class="feld"><span>Umbuchungen erkennen an (Komma-getrennt, z. B. Empfänger eigener Konten)</span><textarea id="b-umb">${esc(d.umbuchung_begriffe.join(", "))}</textarea></label>
    ${checkZeile("b-protokoll", d.protokoll, "Fehlersuche: Bank-Kommunikation ins Add-on-Protokoll schreiben (PIN wird ausgeblendet)")}
    <div class="knopfreihe"><button class="knopf zweit" id="b-speichern">Einstellungen sichern</button></div>
    <p class="leise" style="font-size:13px;margin:12px 4px">Überweisungen an eigene Konten (gleiche IBAN wie ein verbundenes Konto) und Buchungen mit diesen Begriffen gelten als Umbuchung und zählen nicht als Ausgabe. Gutschriften wie Gehalt werden ebenfalls ausgelassen.</p>` : ""}`);
  $("#b-neu").onclick = () => bankHinzufuegen();
  $("#b-tr")?.addEventListener("click", trAnsicht);
  $("#b-speichern")?.addEventListener("click", async () => {
    await api("banken/einstellungen", { method: "PUT", body: { produkt_id: $("#b-pid").value, umbuchung_begriffe: $("#b-umb").value.split(","), protokoll: $("#b-protokoll").checked } });
    toast("Gesichert"); bankenAnsicht();
  });
  $$("[data-b]").forEach((x) => (x.onclick = () => bankDetail(+x.dataset.b)));
}

async function bankHinzufuegen(start) {
  if (!S.banken) S.banken = await api("banken");
  if (!S.tr) S.tr = await api("tr");
  const trFrei = !S.tr.eingerichtet;
  const auswahl = [...(trFrei ? [["tr", "Trade Republic"]] : []), ["dkb", "DKB"], ["comdirect", "comdirect"], ["andere", "Andere"]];
  let vorlage = start && auswahl.some(([v]) => v === start) ? start : auswahl[0][0];
  const trAb = (S.tr.import_ab || "").slice(0, 10);
  const standardAb = trAb || isoTag(new Date());
  const zeichnen = () => {
    const istTr = vorlage === "tr", pidFehlt = !istTr && !S.banken.produkt_id;
    sheetOeffnen(`<div class="griff"></div><div class="sheet-kopf"><button data-zu>Abbrechen</button><h3>Bank hinzufügen</h3><button class="fett" id="bn-ok">Verbinden</button></div>
      <div class="segment" style="margin:8px 0">${auswahl.map(([v, t]) => `<button data-v="${v}" class="${vorlage === v ? "aktiv" : ""}">${t}</button>`).join("")}</div>
      ${istTr ? `
      <label class="feld"><span>Telefonnummer mit Ländervorwahl</span><input id="bn-tel" inputmode="tel" autocomplete="tel" placeholder="+49 170 1234567"></label>
      <label class="feld"><span>PIN der Trade-Republic-App (4 Ziffern)</span><input id="bn-pin" type="password" inputmode="numeric" maxlength="4" autocomplete="current-password"></label>
      ${checkZeile("bn-merken", true, "PIN im Add-on speichern (dann reicht bei einer neuen Anmeldung die Bestätigung in der TR-App; liegt unverschlüsselt in der Add-on-Datenbank und damit in HA-Backups)")}
      <div class="zwei" style="margin-top:12px"><label class="feld"><span>Laufend übernehmen ab</span><input type="date" id="bn-ab" value="${esc(standardAb)}"></label>
        <label class="feld"><span>Abrufen alle (Minuten)</span><input type="number" id="bn-int" value="15" min="5" max="1440" inputmode="numeric"></label></div>
      <p class="leise" style="font-size:13px">Nach „Verbinden“ kommt eine Anfrage in die Trade-Republic-App, die du dort bestätigst.</p>` : `
      ${pidFehlt ? `<div class="hinweis" style="display:block;margin-bottom:10px"><b>FinTS-Produkt-ID nötig.</b> DKB, comdirect und andere Banken lassen den Abruf nur mit einer registrierten Produkt-ID zu. Du bekommst sie kostenlos auf fints.org unter Hersteller → Produktregistrierung.</div>
      <label class="feld"><span>FinTS-Produkt-ID</span><input id="bn-pid" autocomplete="off" autocapitalize="characters"></label>` : ""}
      ${vorlage === "andere" ? `<label class="feld"><span>Name</span><input id="bn-name"></label>
        <div class="zwei"><label class="feld"><span>BLZ</span><input id="bn-blz" inputmode="numeric"></label><label class="feld"><span>FinTS-Adresse</span><input id="bn-url" placeholder="https://…"></label></div>` : ""}
      <label class="feld"><span>${BANK_LOGIN[vorlage] || "Anmeldename"} fürs Online-Banking</span><input id="bn-login" autocomplete="username" autocapitalize="off"></label>
      <label class="feld"><span>PIN fürs Online-Banking</span><input id="bn-pin" type="password" autocomplete="current-password"></label>
      ${checkZeile("bn-merken", true, "PIN im Add-on speichern (nötig für den täglichen Abruf; liegt unverschlüsselt in der Add-on-Datenbank und damit in HA-Backups)")}
      <div class="zwei" style="margin-top:12px"><label class="feld"><span>Laufend übernehmen ab</span><input type="date" id="bn-ab" value="${esc(standardAb)}"></label>
        <label class="feld"><span>Täglich abrufen um (optional)</span><input type="time" id="bn-uhr"></label></div>
      <p class="leise" style="font-size:13px">Ältere Umsätze holst du danach gezielt über „Altbestand nachholen“ zur Durchsicht.</p>`}`);
    $$("#sheet [data-v]").forEach((b) => (b.onclick = () => { vorlage = b.dataset.v; zeichnen(); }));
    $("#bn-ok").onclick = async () => {
      const pin = $("#bn-pin").value.trim();
      if (istTr) {
        const tel = $("#bn-tel").value.trim();
        if (!tel || !pin) { toast("Telefonnummer und PIN eingeben"); return; }
        if (!/^\d{4}$/.test(pin)) { toast("Die PIN von Trade Republic hat 4 Ziffern"); return; }
        const body = { telefon: tel, pin: $("#bn-merken").checked ? pin : "", intervall: +$("#bn-int").value || 15, aktiv: true };
        if ($("#bn-ab").value !== trAb) body.import_ab = $("#bn-ab").value ? $("#bn-ab").value + "T00:00:00" : "";
        try { await api("tr", { method: "PUT", body }); S.tr = null; trAnmelden(pin, () => trAnsicht()); }
        catch (err) { toast(err.message); }
        return;
      }
      if (!$("#bn-login").value || !pin) { toast("Anmeldename und PIN eingeben"); return; }
      if (pidFehlt && !$("#bn-pid").value.trim()) { toast("Produkt-ID eintragen, ohne sie lehnt die Bank ab"); return; }
      try {
        if (pidFehlt) S.banken = await api("banken/einstellungen", { method: "PUT", body: { produkt_id: $("#bn-pid").value } });
        const b = await api("banken", { method: "POST", body: {
          vorlage: vorlage === "andere" ? "" : vorlage, name: $("#bn-name")?.value, blz: $("#bn-blz")?.value, url: $("#bn-url")?.value,
          login: $("#bn-login").value, pin, pin_speichern: $("#bn-merken").checked, import_ab: $("#bn-ab").value, abruf_uhrzeit: $("#bn-uhr").value } });
        auftragStarten(b.id, { art: "einrichten", pin }, () => bankDetail(b.id));
      } catch (err) { toast(err.message); }
    };
  };
  zeichnen();
}

async function bankDetail(id) {
  const d = await api("banken");
  S.banken = d;
  const b = d.banken.find((x) => x.id === id);
  if (!b) return bankenAnsicht();
  unterseite(b.name, `
    ${b.meldung ? `<div class="hinweis">${esc(b.meldung)}</div>` : ""}
    ${b.offen_nachholen ? `<button class="hinweis" id="bd-nach"><b>${b.offen_nachholen}</b><span>nachgeholte Umsätze warten auf Durchsicht</span><span style="margin-left:auto">›</span></button>` : ""}
    <div class="knopfreihe" style="margin-top:0"><button class="knopf" id="bd-abruf">Jetzt abrufen</button><button class="knopf zweit" id="bd-nachholen">Altbestand nachholen</button></div>
    <div class="abschnitt">Zugang</div>
    <div class="mehr-liste">
      <div class="zeile">Status<span class="rechts">${b.meldung ? "Fehler" : b.letzter_abruf ? "zuletzt " + esc(kurzZeit(b.letzter_abruf)) : b.konten.length ? "verbunden" : "noch nicht verbunden"}</span></div>
      <div class="zeile">${esc(BANK_LOGIN[b.vorlage] || "Anmeldename")}<span class="rechts">${esc(b.login)}</span></div>
      <div class="zeile">PIN<span class="rechts">${b.pin_gespeichert ? "gespeichert" : "wird bei jedem Abruf abgefragt"}</span></div>
    </div>
    <div class="abschnitt">Konten</div>
    <div class="mehr-liste">${b.konten.map((k) => `<label class="zeile"><input type="checkbox" data-k="${esc(k.schluessel)}" ${k.aktiv ? "checked" : ""}><span>${esc(k.name)}</span><span class="rechts">${esc(k.iban ? "Konto" : "Karte/Unterkonto")}</span></label>`).join("") || '<div class="leer">Noch keine Konten geladen.</div>'}</div>
    <div class="abschnitt">Einstellungen</div>
    <div class="zwei"><label class="feld"><span>Laufend übernehmen ab</span><input type="date" id="bd-ab" value="${esc((b.import_ab || "").slice(0, 10))}"></label>
      <label class="feld"><span>Täglich abrufen um</span><input type="time" id="bd-uhr" value="${esc(b.abruf_uhrzeit || "")}"></label></div>
    ${b.tan_verfahren_liste.length ? `<label class="feld"><span>TAN-Verfahren</span><select id="bd-tan">${b.tan_verfahren_liste.map((v) => `<option value="${esc(v.code)}" ${v.code === b.tan_verfahren ? "selected" : ""}>${esc(v.name)}</option>`).join("")}</select></label>` : ""}
    <label class="feld"><span>${esc(BANK_LOGIN[b.vorlage] || "Anmeldename")}</span><input id="bd-login" value="${esc(b.login)}" autocomplete="username" autocapitalize="off"></label>
    <label class="feld"><span>PIN ändern bzw. speichern (leer lassen = unverändert)</span><input type="password" id="bd-pin" autocomplete="new-password"></label>
    <div class="knopfreihe"><button class="knopf zweit" id="bd-sichern">Einstellungen sichern</button></div>
    <div class="knopfreihe"><button class="knopf zweit" id="bd-einrichten">Konten neu laden</button>
      ${b.pin_gespeichert ? '<button class="knopf zweit" id="bd-pinweg">Gespeicherte PIN löschen</button>' : ""}</div>
    <div class="knopfreihe"><button class="knopf gefahr" id="bd-weg">Bank entfernen</button></div>
    <p class="leise" style="font-size:13px;margin:12px 4px">Die DKB verlangt bei jedem Abruf eine Freigabe in der DKB-App. Beim täglichen Abruf kommt also zur eingestellten Uhrzeit eine Anfrage aufs Handy, die du innerhalb von 5 Minuten bestätigen musst.</p>`);
  $("#zurueck").onclick = bankenAnsicht;
  $("#bd-nach")?.addEventListener("click", () => nachholenAnsicht(id));
  $("#bd-abruf").onclick = () => auftragStarten(id, { art: "abruf" }, () => bankDetail(id), b.pin_gespeichert);
  $("#bd-einrichten").onclick = () => auftragStarten(id, { art: "einrichten" }, () => bankDetail(id), b.pin_gespeichert);
  $("#bd-nachholen").onclick = () => nachholenStarten(b);
  $$("[data-k]").forEach((c) => (c.onchange = async () => {
    await api(`banken/${id}`, { method: "PUT", body: { konten: [{ schluessel: c.dataset.k, aktiv: c.checked }] } });
    toast(c.checked ? "Konto wird abgerufen" : "Konto wird nicht mehr abgerufen");
  }));
  $("#bd-sichern").onclick = async () => {
    const body = { import_ab: $("#bd-ab").value, abruf_uhrzeit: $("#bd-uhr").value };
    if ($("#bd-tan")) body.tan_verfahren = $("#bd-tan").value;
    if ($("#bd-pin").value) body.pin = $("#bd-pin").value;
    if ($("#bd-login").value.trim() && $("#bd-login").value.trim() !== b.login) body.login = $("#bd-login").value.trim();
    await api(`banken/${id}`, { method: "PUT", body }); toast("Gesichert"); bankDetail(id);
  };
  $("#bd-pinweg")?.addEventListener("click", async () => { await api(`banken/${id}`, { method: "PUT", body: { pin: "" } }); toast("PIN gelöscht"); bankDetail(id); });
  $("#bd-weg").onclick = async () => {
    if (!confirm(`${b.name} entfernen? Bereits übernommene Einträge bleiben erhalten.`)) return;
    await api(`banken/${id}`, { method: "DELETE" }); toast("Bank entfernt"); bankenAnsicht();
  };
}

function nachholenStarten(b) {
  const bis = (b.import_ab || "").slice(0, 10) ? isoTag(tagPlus(ausIso(b.import_ab), -1)) : isoTag(new Date());
  const von = isoTag(new Date(new Date().getFullYear() - 1, 0, 1));
  sheetOeffnen(`<div class="griff"></div><div class="sheet-kopf"><button data-zu>Abbrechen</button><h3>Altbestand nachholen</h3><button class="fett" id="nh-ok">Abrufen</button></div>
    <p style="font-size:14.5px">Holt alle Umsätze im Zeitraum. Nichts wird direkt übernommen: Du siehst sie danach durch, schon erfasste Ausgaben (z. B. aus Next) sind vorsortiert.</p>
    <div class="zwei"><label class="feld"><span>Von</span><input type="date" id="nh-von" value="${esc(von)}"></label><label class="feld"><span>Bis</span><input type="date" id="nh-bis" value="${esc(bis)}"></label></div>
    <p class="leise" style="font-size:13px">Wie weit die Bank zurückliefert, entscheidet sie selbst. Für Umsätze älter als 90 Tage ist meist eine zusätzliche Freigabe nötig.</p>`);
  $("#nh-ok").onclick = () => {
    if (!$("#nh-von").value) { toast("Startdatum fehlt"); return; }
    auftragStarten(b.id, { art: "nachholen", von: $("#nh-von").value, bis: $("#nh-bis").value }, () => nachholenAnsicht(b.id), b.pin_gespeichert);
  };
}

function auftragStarten(id, body, danach, pinGespeichert = true) {
  if (!pinGespeichert && !body.pin) {
    sheetOeffnen(`<div class="griff"></div><div class="sheet-kopf"><button data-zu>Abbrechen</button><h3>PIN</h3><button class="fett" id="ap-ok">Weiter</button></div>
      <label class="feld"><span>PIN fürs Online-Banking (wird nicht gespeichert)</span><input type="password" id="ap-pin" autocomplete="current-password"></label>`);
    setTimeout(() => $("#ap-pin")?.focus(), 250);
    $("#ap-ok").onclick = () => { if ($("#ap-pin").value) auftragStarten(id, { ...body, pin: $("#ap-pin").value }, danach, true); };
    return;
  }
  sheetOeffnen(`<div class="griff"></div><div class="sheet-kopf"><span style="width:60px"></span><h3>Bank</h3><span style="width:60px"></span></div><div id="auftrag"><div class="leer">Starte …</div></div>`);
  let laeuft = true, tanAngezeigt = null;
  const zeigen = (j) => {
    const box = $("#auftrag");
    if (!box) { laeuft = false; return; }
    if (j.zustand === "tan_noetig" && j.tan_art === "code") {
      if (tanAngezeigt === j.tan_text + (j.tan_bild || "")) return;
      tanAngezeigt = j.tan_text + (j.tan_bild || "");
      box.innerHTML = `<p style="font-size:15px">${esc(j.tan_text)}</p>${j.tan_bild ? `<img src="${j.tan_bild}" alt="TAN-Grafik" style="max-width:240px;display:block;margin:10px auto;background:#fff;padding:8px;border-radius:10px">` : ""}
        <label class="feld"><span>TAN</span><input id="au-tan" inputmode="numeric" autocomplete="one-time-code"></label>
        <div class="knopfreihe"><button class="knopf" id="au-senden">Senden</button><button class="knopf zweit" id="au-stop">Abbrechen</button></div>`;
      $("#au-senden").onclick = async () => { const tan = $("#au-tan").value; tanAngezeigt = null; box.innerHTML = '<div class="leer">Sende TAN …</div>'; await api(`banken/${id}/tan`, { method: "POST", body: { tan } }); };
      $("#au-stop").onclick = () => api(`banken/${id}/tan`, { method: "POST", body: { abbrechen: true } });
      return;
    }
    tanAngezeigt = null;
    if (j.zustand === "tan_noetig") {
      box.innerHTML = `<div style="text-align:center;padding:18px 0"><div style="font-size:44px">📲</div><p style="font-size:17px;font-weight:600;margin:8px 0">${esc(j.tan_text)}</p><p class="leise">${esc(j.meldung)}</p></div>
        <div class="knopfreihe"><button class="knopf zweit" id="au-stop">Abbrechen</button></div>`;
      $("#au-stop").onclick = () => api(`banken/${id}/tan`, { method: "POST", body: { abbrechen: true } });
    } else if (j.zustand === "laeuft") {
      box.innerHTML = `<div class="leer">${esc(j.meldung)}</div>`;
    } else {
      laeuft = false;
      box.innerHTML = `<div class="hinweis ${j.zustand === "fertig" ? "ok" : ""}"><span>${esc(j.meldung)}</span></div>
        <div class="knopfreihe"><button class="knopf" id="au-weiter">${j.zustand === "fertig" && j.art === "nachholen" ? "Jetzt durchsehen" : "Fertig"}</button></div>`;
      $("#au-weiter").onclick = async () => { sheetSchliessen(); await metaLaden(); danach?.(); };
    }
  };
  (async () => {
    try { zeigen(await api(`banken/${id}/auftrag`, { method: "POST", body })); }
    catch (err) { zeigen({ zustand: "fehler", meldung: err.message }); return; }
    while (laeuft) {
      await new Promise((r) => setTimeout(r, 2000));
      if (!laeuft || $("#sheet").hidden) break;
      try { zeigen(await api(`banken/${id}/auftrag`)); } catch (_) { /* weiter versuchen */ }
    }
  })();
}

// ---------------------------------------------------------------- Nachholen: Durchsicht
async function nachholenAnsicht(bankId = 0) {
  const zeilen = await api("nachholen?" + qs({ bank_id: bankId || "" }));
  const N = (S.nach = { zeilen, auswahl: new Set(), aend: {}, bankId, offen: S.nach?.offen || {} });
  for (const z of zeilen) if (z.art === "ausgabe" && !z.duplikat_id) N.auswahl.add(z.id);
  const gruppen = [
    ["neu", "Neue Ausgaben", zeilen.filter((z) => z.art === "ausgabe" && !z.duplikat_id), "Ausgewählt: werden übernommen."],
    ["dup", "Vermutlich schon erfasst", zeilen.filter((z) => z.art === "ausgabe" && z.duplikat_id), "Gleicher Betrag zu einem vorhandenen Eintrag in ±2 Tagen. Nicht ausgewählt."],
    ["andere", "Umbuchungen und Gutschriften", zeilen.filter((z) => z.art !== "ausgabe"), "Zählen normalerweise nicht als Ausgabe. Gutschriften werden bei Auswahl als Erstattung übernommen."],
  ];
  const zeile = (z) => {
    const k = kat(N.aend[z.id]?.kategorie_id ?? z.kategorie_id);
    const bez = N.aend[z.id]?.bezeichnung ?? z.bezeichnung;
    return `<div class="nh-zeile" data-id="${z.id}">
      <input type="checkbox" data-cb="${z.id}" ${N.auswahl.has(z.id) ? "checked" : ""} aria-label="Auswählen">
      <button class="nh-text" data-ed="${z.id}"><span class="titel">${esc(bez || z.gegenpartei || "Bankumsatz")}</span>
        <small class="leise">${ausIso(z.datum).toLocaleDateString("de-DE", { day: "numeric", month: "short", year: "2-digit" })} · ${k ? esc(k.icon + " " + k.name) : '<span style="color:var(--pruefen)">Kategorie wählen</span>'}${z.duplikat_id ? ` · wie „${esc(z.dup_bezeichnung || "")}“ (${ausIso(z.dup_datum).toLocaleDateString("de-DE")})` : ""}</small></button>
      <span class="zahl nh-betrag ${z.betrag < 0 ? "gut" : ""}">${z.betrag < 0 ? "+" + geld(-z.betrag, z.waehrung) : geld(z.betrag, z.waehrung)}</span></div>`;
  };
  ansicht.innerHTML = `<div class="kopf"><button class="rund" id="zurueck" aria-label="Zurück">‹</button><h1>Durchsicht</h1></div>
    ${zeilen.length ? `<p class="leise" style="font-size:13.5px;margin:0 4px 6px">${zeilen.length} nachgeholte Bankumsätze. Tippe auf einen Umsatz, um Bezeichnung oder Kategorie zu ändern.</p>` : '<div class="leer">Nichts offen. Altbestand holst du unter Mehr → Banken → Bank → „Altbestand nachholen“.</div>'}
    ${gruppen.filter((g) => g[2].length).map(([key, titel, liste, info]) => `<div class="block" style="padding:12px 14px">
      <h2><span>${titel} <span class="leise">(${liste.length})</span></span><button class="knopf zweit klein" data-alle="${key}">Alle an/aus</button></h2>
      <p class="leise" style="font-size:12.5px;margin:-4px 0 6px">${info}</p>
      ${key === "andere" && !N.offen.andere ? `<button class="knopf zweit klein" data-aufklappen="andere">Anzeigen</button>` : `<div class="nh-liste">${liste.slice(0, N.offen[key] ? 5000 : 300).map(zeile).join("")}</div>
      ${liste.length > 300 && !N.offen[key] ? `<button class="knopf zweit klein" data-aufklappen="${key}">Alle ${liste.length} anzeigen</button>` : ""}`}</div>`).join("")}
    ${zeilen.length ? `<div class="nh-leiste"><button class="knopf" id="nh-ueb"></button></div>` : ""}`;
  $("#zurueck").onclick = () => (bankId ? bankDetail(bankId) : mehrAnsicht());
  const knopf = () => { const b = $("#nh-ueb"); if (b) b.textContent = `${N.auswahl.size} übernehmen, ${zeilen.length - N.auswahl.size} verwerfen`; };
  knopf();
  $$("[data-cb]").forEach((c) => (c.onchange = () => { c.checked ? N.auswahl.add(+c.dataset.cb) : N.auswahl.delete(+c.dataset.cb); knopf(); }));
  $$("[data-alle]").forEach((b) => (b.onclick = () => {
    const liste = gruppen.find((g) => g[0] === b.dataset.alle)[2];
    const alleAn = liste.every((z) => N.auswahl.has(z.id));
    liste.forEach((z) => (alleAn ? N.auswahl.delete(z.id) : N.auswahl.add(z.id)));
    $$("[data-cb]").forEach((c) => (c.checked = N.auswahl.has(+c.dataset.cb)));
    knopf();
  }));
  $$("[data-aufklappen]").forEach((b) => (b.onclick = () => { N.offen[b.dataset.aufklappen] = true; const ausw = new Set(N.auswahl), aend = N.aend; nachholenAnsicht(bankId).then(() => { S.nach.auswahl = ausw; S.nach.aend = aend; $$("[data-cb]").forEach((c) => (c.checked = ausw.has(+c.dataset.cb))); knopf(); }); }));
  $$("[data-ed]").forEach((b) => (b.onclick = () => nachholenBearbeiten(zeilen.find((z) => z.id === +b.dataset.ed), (neu) => {
    N.aend[neu.id] = neu; N.auswahl.add(neu.id);
    const alt = $(`.nh-zeile[data-id="${neu.id}"]`);
    alt.outerHTML = zeile(zeilen.find((z) => z.id === neu.id));
    const c = $(`[data-cb="${neu.id}"]`);
    c.onchange = () => { c.checked ? N.auswahl.add(neu.id) : N.auswahl.delete(neu.id); knopf(); };
    $(`[data-ed="${neu.id}"]`).onclick = b.onclick;
    knopf();
  })));
  $("#nh-ueb")?.addEventListener("click", async () => {
    const ohneKat = zeilen.filter((z) => N.auswahl.has(z.id) && !(N.aend[z.id]?.kategorie_id ?? z.kategorie_id)).length;
    if (ohneKat && !confirm(`${ohneKat} ausgewählte Umsätze haben noch keine Kategorie. Sie werden übernommen und landen in „Prüfen“. Fortfahren?`)) return;
    const eintraege = zeilen.filter((z) => N.auswahl.has(z.id)).map((z) => ({ id: z.id, ...(N.aend[z.id] || {}) }));
    const verwerfen = zeilen.filter((z) => !N.auswahl.has(z.id)).map((z) => z.id);
    try {
      const r = await api("nachholen/uebernehmen", { method: "POST", body: { eintraege, verwerfen } });
      toast(`${r.uebernommen} übernommen, ${r.verworfen} verworfen`);
      await metaLaden(); nachholenAnsicht(bankId);
    } catch (err) { toast(err.message); }
  });
}

function nachholenBearbeiten(z, fertig) {
  const N = S.nach;
  let katId = N.aend[z.id]?.kategorie_id ?? z.kategorie_id;
  sheetOeffnen(`<div class="griff"></div><div class="sheet-kopf"><button data-zu>Abbrechen</button><h3 class="zahl">${geld(Math.abs(z.betrag), z.waehrung)}</h3><button class="fett" id="ne-ok">Fertig</button></div>
    <div class="herkunft">${esc(z.gegenpartei || "–")}<br>${esc(z.zweck || "")}${z.buchungstext ? `<br>${esc(z.buchungstext)}` : ""}<br>${ausIso(z.datum).toLocaleDateString("de-DE")} · ${esc(z.bank || "")}</div>
    <label class="feld"><span>Bezeichnung</span><input id="ne-bez" value="${esc(N.aend[z.id]?.bezeichnung ?? z.bezeichnung ?? "")}"></label>
    <div class="katgitter">${S.meta.kategorien.filter((k) => !k.archiviert).map((k) => `<button data-k="${k.id}" class="${k.id === katId ? "aktiv" : ""}"><span>${esc(k.icon)}</span>${esc(k.name)}</button>`).join("")}</div>`);
  $$("#sheet [data-k]").forEach((b) => (b.onclick = () => { katId = +b.dataset.k; $$("#sheet [data-k]").forEach((x) => x.classList.toggle("aktiv", x === b)); }));
  $("#ne-ok").onclick = () => { const bezeichnung = $("#ne-bez").value.trim(); sheetSchliessen(); fertig({ id: z.id, kategorie_id: katId, bezeichnung }); };
}

// ---------------------------------------------------------------- Sheet, Tabs, Start
function sheetOeffnen(html) {
  const s = $("#sheet");
  s.style.transform = s.style.transition = ""; $("#sheet-hintergrund").style.opacity = "";
  s.innerHTML = html; s.hidden = false; $("#sheet-hintergrund").hidden = false;
  document.body.style.overflow = "hidden";
  $$("[data-zu]", s).forEach((b) => (b.onclick = sheetSchliessen));
}
function sheetSchliessen() {
  $("#sheet").hidden = true; $("#sheet-hintergrund").hidden = true; $("#sheet").innerHTML = "";
  document.body.style.overflow = "";
}
$("#sheet-hintergrund").onclick = sheetSchliessen;

// Wie in iOS: Sheet nach unten wegziehen, wenn es ganz oben steht.
(function sheetWegziehen() {
  const s = $("#sheet"), hg = $("#sheet-hintergrund");
  let y0 = 0, x0 = 0, t0 = 0, dy = 0, aktiv = false, richtung = null;
  s.addEventListener("touchstart", (ev) => {
    aktiv = s.scrollTop <= 0 && ev.touches.length === 1;
    y0 = ev.touches[0].clientY; x0 = ev.touches[0].clientX; t0 = Date.now(); dy = 0; richtung = null;
  }, { passive: true });
  s.addEventListener("touchmove", (ev) => {
    if (!aktiv) return;
    const my = ev.touches[0].clientY - y0, mx = ev.touches[0].clientX - x0;
    if (!richtung) {
      if (Math.abs(my) < 6 && Math.abs(mx) < 6) return;
      richtung = my > 0 && Math.abs(my) > Math.abs(mx) && s.scrollTop <= 0 ? "runter" : "anders";
      if (richtung !== "runter") { aktiv = false; return; }
      document.activeElement?.blur?.(); // Tastatur einklappen
    }
    ev.preventDefault();
    dy = Math.max(0, my);
    s.style.transition = hg.style.transition = "none";
    s.style.transform = `translateY(${dy}px)`;
    hg.style.opacity = String(Math.max(0.15, 1 - dy / (s.offsetHeight || 600)));
  }, { passive: false });
  const ende = () => {
    if (!aktiv || richtung !== "runter") { aktiv = false; return; }
    aktiv = false;
    const tempo = dy / Math.max(1, Date.now() - t0); // Punkte je ms
    s.style.transition = "transform .24s cubic-bezier(.2,.8,.2,1)"; hg.style.transition = "opacity .24s";
    if (dy > Math.min(140, s.offsetHeight * 0.3) || (tempo > 0.6 && dy > 40)) {
      s.style.transform = "translateY(100%)"; hg.style.opacity = "0";
      setTimeout(sheetSchliessen, 230);
    } else {
      s.style.transform = ""; hg.style.opacity = "";
    }
  };
  s.addEventListener("touchend", ende);
  s.addEventListener("touchcancel", ende);
})();
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#sheet").hidden) sheetSchliessen(); });

// ---------------------------------------------------------------- Ziehen zum Aktualisieren
// Wie in iOS-Apps: oben ganz nach unten ziehen, loslassen ruft Trade Republic und die Banken ab.
const ZIEH_SCHWELLE = 115;
// Fühlbares Feedback. iOS-Safari kennt navigator.vibrate nicht; ab iOS 18 löst das Umschalten
// eines Schalters (<input type=checkbox switch>) aber das System-Haptik-Feedback aus.
function ziehenAnzeige() {
  let el = $("#ziehen");
  if (!el) {
    el = document.createElement("div"); el.id = "ziehen";
    el.innerHTML = '<svg viewBox="0 0 32 32" width="30" height="30"><circle cx="16" cy="16" r="11" fill="none" stroke-width="3" stroke-linecap="round" pathLength="100"/></svg>';
    document.body.appendChild(el);
  }
  return el;
}
let ziehtGerade = false, aktualisiertGerade = false;
function ziehenAktivieren() {
  let y0 = 0, x0 = 0, zug = 0, richtung = null, aktiv = false, gefuehlt = false;
  const erlaubt = () => ["uebersicht", "eintraege", "verlauf", "pruefen"].includes(S.tab) && $("#sheet").hidden && !aktualisiertGerade;
  const setzen = (px, mitUebergang) => {
    const el = ziehenAnzeige();
    const anteil = Math.min(1, px / ZIEH_SCHWELLE);
    el.style.transition = ansicht.style.transition = mitUebergang ? "transform .25s ease, opacity .25s ease" : "none";
    ansicht.style.transform = px ? `translateY(${px}px)` : "";
    el.style.opacity = px ? Math.min(1, anteil * 1.4) : 0;
    el.style.transform = `translate(-50%, ${Math.min(px, ZIEH_SCHWELLE) * 0.55}px) rotate(${anteil * 270}deg)`;
    el.querySelector("circle").style.strokeDasharray = `${Math.max(4, anteil * 80)} 100`;
    el.classList.toggle("bereit", anteil >= 1);
  };
  document.addEventListener("touchstart", (ev) => {
    aktiv = erlaubt() && window.scrollY <= 0 && !ev.target.closest(".chips, .wisch.offen");
    if (!aktiv) return;
    y0 = ev.touches[0].clientY; x0 = ev.touches[0].clientX; zug = 0; richtung = null; gefuehlt = false;
  }, { passive: true });
  document.addEventListener("touchmove", (ev) => {
    if (!aktiv) return;
    const dy = ev.touches[0].clientY - y0, dx = ev.touches[0].clientX - x0;
    if (!richtung) {
      if (Math.abs(dy) < 6 && Math.abs(dx) < 6) return;
      richtung = dy > 0 && Math.abs(dy) > Math.abs(dx) && window.scrollY <= 0 ? "runter" : "anders";
      if (richtung !== "runter") { aktiv = false; return; }
    }
    ev.preventDefault();
    ziehtGerade = true;
    zug = dy < ZIEH_SCHWELLE ? dy : ZIEH_SCHWELLE + (dy - ZIEH_SCHWELLE) * 0.35; // weicher Widerstand
    zug = Math.max(0, zug * 0.75);
    setzen(zug, false);
    if (!gefuehlt && zug >= ZIEH_SCHWELLE) gefuehlt = true;
    if (gefuehlt && zug < ZIEH_SCHWELLE * 0.8) gefuehlt = false;
  }, { passive: false });
  const ende = () => {
    if (!aktiv || richtung !== "runter") { aktiv = false; return; }
    aktiv = false;
    setTimeout(() => (ziehtGerade = false), 50);
    if (zug >= ZIEH_SCHWELLE) {
      setzen(60, true);
      const el = ziehenAnzeige(); el.classList.add("laedt");
      allesAktualisieren().finally(() => { el.classList.remove("laedt"); setzen(0, true); });
    } else setzen(0, true);
  };
  document.addEventListener("touchend", ende);
  document.addEventListener("touchcancel", ende);
}
async function allesAktualisieren() {
  aktualisiertGerade = true;
  try {
    const r = await api("aktualisieren", { method: "POST", zeitlimit: 60000 });
    const teile = [];
    if (r.tr) teile.push(["login_noetig", "fehler"].includes(r.tr.zustand) ? `Trade Republic: ${r.tr.meldung || "Anmeldung nötig"}`
      : r.tr.zustand === "laeuft" ? r.tr.meldung : `Trade Republic: ${r.tr.neu ? r.tr.neu + " neu" : "nichts Neues"}`);
    if (r.banken.length) teile.push(`${r.banken.map((b) => b.name).join(", ")} ${r.banken.length === 1 ? "wird" : "werden"} abgerufen`);
    if (!r.tr && !r.banken.length) teile.push("Keine Bank mit gespeicherter PIN eingerichtet");
    const trLogin = r.tr?.zustand === "login_noetig";
    if (trLogin) teile[0] = "Trade Republic: Anmeldung abgelaufen, melde neu an";
    toast(teile.join(" · "));
    try { await metaLaden(); neuZeichnen(); } catch (_) { /* egal */ }
    if (r.banken.length) bankenBeobachten(r.banken.map((b) => b.id));
    // Sitzung abgelaufen: gleich neu anmelden (gespeicherte PIN, Bestätigung in der TR-App), danach Abruf verfolgen
    if (trLogin) {
      const vorher = (await api("tr").catch(() => ({}))).letzter_abruf;
      trAnmelden("", null);
      trBeobachten(vorher);
    }
  } catch (err) {
    toast(err.offline ? "Offline – Aktualisieren geht nur mit Verbindung" : err.message);
  } finally {
    aktualisiertGerade = false;
  }
}
// Nach einer Neuanmeldung ruft der Server Trade Republic von selbst ab; hier auf das Ergebnis warten.
async function trBeobachten(vorher, bis = Date.now() + 5 * 60000) {
  while (Date.now() < bis) {
    await new Promise((res) => setTimeout(res, 3000));
    let k;
    try { k = await api("tr"); } catch (_) { continue; }
    if (k.login?.zustand === "fehler" || k.login?.zustand === "pin_noetig" && $("#sheet").hidden) return;
    if (k.letzter_abruf && k.letzter_abruf !== vorher) {
      toast(`Trade Republic: ${k.neu_letzter_abruf ? k.neu_letzter_abruf + " neu" : "nichts Neues"}`);
      try { await metaLaden(); neuZeichnen(); } catch (_) { /* egal */ }
      return;
    }
  }
}
// Hintergrund-Abrufe der Banken verfolgen und melden, wenn fertig oder eine Freigabe nötig ist.
async function bankenBeobachten(ids, bis = Date.now() + 6 * 60000) {
  const gemeldet = new Set();
  while (ids.length && Date.now() < bis) {
    await new Promise((res) => setTimeout(res, 3000));
    let banken;
    try { banken = (await api("banken")).banken; } catch (_) { continue; }
    for (const b of banken.filter((x) => ids.includes(x.id))) {
      const j = b.job || {};
      if (j.zustand === "tan_noetig" && !gemeldet.has(b.id)) {
        gemeldet.add(b.id);
        toast(j.tan_art === "app" ? `${b.name}: bitte in der Banking-App freigeben` : `${b.name} braucht eine TAN`,
          j.tan_art === "app" ? null : { label: "Öffnen", fn: () => { S.tab = "mehr"; bankDetail(b.id); } });
      } else if (j.zustand === "fertig" || j.zustand === "fehler") {
        ids = ids.filter((i) => i !== b.id);
        if (j.zustand === "fehler") toast(`${b.name}: ${j.meldung}`);
        else toast(`${b.name}: ${j.ergebnis?.neu ? j.ergebnis.neu + " neue Umsätze" : "nichts Neues"}`);
        if (j.ergebnis?.neu) { try { await metaLaden(); neuZeichnen(); } catch (_) { /* egal */ } }
      }
    }
  }
}
ziehenAktivieren();

// Kein Zoomen wie in einer echten App: iOS-Safari ignoriert user-scalable=no, daher die Gesten selbst abfangen.
["gesturestart", "gesturechange", "gestureend"].forEach((t) => document.addEventListener(t, (ev) => ev.preventDefault(), { passive: false }));
document.addEventListener("touchmove", (ev) => { if (ev.touches.length > 1) ev.preventDefault(); }, { passive: false });

const ANSICHTEN = { uebersicht, eintraege: eintraegeAnsicht, verlauf: verlaufAnsicht, mehr: mehrAnsicht, pruefen: pruefenAnsicht };
function tabWechseln(tab) {
  S.tab = tab;
  $$(".tabs [data-tab]").forEach((b) => b.classList.toggle("aktiv", b.dataset.tab === tab));
  window.scrollTo(0, 0);
  ANSICHTEN[tab]().catch((e) => { ansicht.innerHTML = `<div class="leer">${esc(e.message)}</div>`; });
}
function neuZeichnen() {
  const y = window.scrollY;
  S._zweiJahre = null; // Daten haben sich geändert
  const p = (S.tab === "mehr" && S.unterseiteAktiv ? S.unterseiteAktiv : ANSICHTEN[S.tab])();
  p.then(() => window.scrollTo(0, y)).catch(() => {});
}
$$(".tabs [data-tab]").forEach((b) => (b.onclick = () => {
  if (b.dataset.tab === "eintraege" && S.tab !== "eintraege") Object.assign(S.suche, { q: "", kategorie: "", konto: "", pruefen: false, offen: false, von: "", bis: "" });
  if (b.dataset.tab === "uebersicht" && S.tab === "uebersicht") { S.zeitraum.anker = new Date(); S.bandAuswahl = null; }
  tabWechseln(b.dataset.tab);
}));
$("#plus").onclick = () => erfassen();

function anmeldungZeigen() {
  $(".tabs").hidden = true;
  ansicht.innerHTML = `<div class="anmeldung"><h1>Ausgaben</h1><p class="leise">Für den Zugriff außerhalb von Home Assistant brauchst du den Zugriffs-Token aus den Add-on-Optionen.</p>
    <label class="feld"><span>Zugriffs-Token</span><input id="token" type="password" autocomplete="current-password"></label>
    <button class="knopf" id="anmelden" style="width:100%">Anmelden</button></div>`;
  $("#anmelden").onclick = async () => {
    const token = $("#token").value.trim();
    const r = await fetch("api/auth", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }) });
    if (!r.ok) { toast("Token stimmt nicht"); return; }
    localStorage.setItem("ausgaben_token", token);
    $(".tabs").hidden = false; start();
  };
}

async function start() {
  const imRahmen = document.documentElement.classList.contains("eingebettet") || location.pathname.includes("hassio_ingress");
  if ("serviceWorker" in navigator && window.isSecureContext && !imRahmen) {
    navigator.serviceWorker.register("sw.js").catch((e) => console.warn("Service Worker", e));
  }
  leisteZeigen();
  try { await metaLaden(); } catch (e) {
    if (e.message !== "Nicht angemeldet") ansicht.innerHTML = `<div class="leer">${esc(e.offline ? "Offline und noch keine gespeicherten Daten auf diesem Gerät. Öffne die App einmal mit Verbindung." : e.message)}</div>`;
    return;
  }
  warteschlangeSenden();
  const hash = location.hash.replace("#", "");
  if (hash === "neu") { tabWechseln("uebersicht"); erfassen(); }
  else if (hash === "pruefen") pruefenAnsicht();
  else if (hash === "design") { S.tab = "mehr"; $$(".tabs [data-tab]").forEach((x) => x.classList.toggle("aktiv", x.dataset.tab === "mehr")); designAnsicht(); }
  else if (hash === "banken" || hash === "tr") { S.tab = "mehr"; $$(".tabs [data-tab]").forEach((x) => x.classList.toggle("aktiv", x.dataset.tab === "mehr")); (hash === "tr" ? trAnsicht : bankenAnsicht)(); }
  else if (hash === "wiederkehrend") {
    S.tab = "mehr";
    $$(".tabs [data-tab]").forEach((x) => x.classList.toggle("aktiv", x.dataset.tab === "mehr"));
    wiederkehrendAnsicht();
  }
  else tabWechseln(ANSICHTEN[hash] ? hash : (S.meta.darstellung.startseite || "uebersicht"));
}
document.addEventListener("visibilitychange", async () => {
  if (document.visibilityState === "visible" && $("#sheet").hidden && S.meta) { await metaLaden(); neuZeichnen(); }
});
start();
