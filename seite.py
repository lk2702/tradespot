"""
Baut die Webseite docs/index.html aus den Auswertungen (Journal, Scan, Backtest).

Wird von lauf_cloud.py aufgerufen. Die Seite ist eine einzelne HTML-Datei ohne
externe Abhängigkeiten, für das iPhone optimiert, mit Reitern, Mini-Charts
(inline SVG) und einem Risikorechner (JavaScript, rechnet nur im Browser).

Keine Anlageberatung.
"""
import html
from datetime import datetime

import numpy as np
import pandas as pd

GAP_VARIANTE = "Kurslücke nach oben, hohes Volumen -> long"

CSS = """
:root{--bg:#f5f6f8;--card:#ffffff;--text:#1a1d21;--muted:#667085;--line:#e4e7ec;--accent:#1d4ed8;--accenttext:#ffffff;
--good:#067647;--goodbg:#e3f5ea;--bad:#b42318;--badbg:#fdeceb;--warn:#8a5a00;--warnbg:#fff4d6}
@media (prefers-color-scheme: dark){:root{--bg:#0e1116;--card:#161b22;--text:#e6e9ee;--muted:#9aa4b2;--line:#2a313b;
--accent:#8ab4ff;--accenttext:#0e1116;--good:#56d28a;--goodbg:#12301f;--bad:#ff9b92;--badbg:#3a1714;
--warn:#f0c15a;--warnbg:#3a2d0c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:980px;margin:0 auto;padding:16px}
h1{font-size:1.5rem;margin:8px 0 2px}
h2{font-size:1.15rem;margin:22px 0 10px}
h3{font-size:1rem;margin:20px 0 8px}
.muted{color:var(--muted)}
.small{font-size:.875rem}
.banner{background:var(--warnbg);color:var(--warn);border-radius:10px;padding:12px 14px;margin:12px 0}
.karte{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;margin:10px 0}
.karte p{margin:6px 0}
.kopf{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.badge{display:inline-block;border-radius:999px;padding:2px 10px;font-size:.8rem;font-weight:600}
.badge.ok{background:var(--goodbg);color:var(--good)}
.badge.no{background:var(--badbg);color:var(--bad)}
.badge.neutral{background:var(--line);color:var(--muted)}
.kacheln{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:12px 0}
.kachel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}
.kachel .zahl{font-size:1.35rem;font-weight:700}
.scroll{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:12px}
table{border-collapse:collapse;width:100%;font-size:.9rem}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--muted);font-weight:600}
tr:last-child td{border-bottom:0}
details{margin:10px 0}
summary{cursor:pointer;font-weight:600}
.pos{color:var(--good)}
.neg{color:var(--bad)}
.tabs{display:none}
.js .tabs{display:flex;gap:8px;overflow-x:auto;position:sticky;top:0;z-index:5;background:var(--bg);
padding:10px 0;margin-top:6px;-webkit-overflow-scrolling:touch}
.tabs button{flex:0 0 auto;border:1px solid var(--line);background:var(--card);color:var(--text);
border-radius:999px;padding:8px 14px;font:inherit;font-size:.9rem;cursor:pointer}
.tabs button.aktiv{background:var(--accent);border-color:var(--accent);color:var(--accenttext);font-weight:600}
.js section.tab{display:none}
.js section.tab.aktiv{display:block}
.chart{margin-top:8px}
.spark,.balken{width:100%;height:auto;display:block}
.spark .linie{fill:none;stroke:var(--accent);stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.spark .flaeche{fill:var(--accent);opacity:.12}
.spark .marke{fill:var(--card);stroke:var(--good);stroke-width:2.5}
.spark .marke2{fill:var(--card);stroke:var(--warn);stroke-width:2.5}
.balken .bpos{fill:var(--good)}
.balken .bneg{fill:var(--bad)}
.balken .nulllinie{stroke:var(--line);stroke-width:1}
.rechner label{display:block;margin:12px 0 4px;font-size:.9rem;color:var(--muted)}
.rechner input,.rechner select{width:100%;padding:10px;font:inherit;border:1px solid var(--line);
border-radius:8px;background:var(--bg);color:var(--text)}
"""

JS = """
(function(){
  var tabs=document.querySelectorAll('.tabs button'), secs=document.querySelectorAll('section.tab');
  function show(id){
    for(var i=0;i<secs.length;i++){secs[i].classList.toggle('aktiv', secs[i].id===id);}
    for(var j=0;j<tabs.length;j++){tabs[j].classList.toggle('aktiv', tabs[j].getAttribute('data-t')===id);}
    try{history.replaceState(null,'','#'+id.replace('t-',''));}catch(e){}
    window.scrollTo(0,0);
    var nav=document.querySelector('.tabs'), aktiv=document.querySelector('.tabs button.aktiv');
    if(nav&&aktiv){nav.scrollLeft=Math.max(0,aktiv.offsetLeft-16);}
  }
  for(var k=0;k<tabs.length;k++){(function(b){b.addEventListener('click',function(){show(b.getAttribute('data-t'));});})(tabs[k]);}
  var h='t-'+location.hash.replace('#','');
  show(document.getElementById(h)?h:'t-heute');

  function zahl(id){return parseFloat(String(document.getElementById(id).value).replace(',','.'));}
  function fmt(x,d){return x.toLocaleString('de-DE',{maximumFractionDigits:d,minimumFractionDigits:d});}
  function rechne(){
    var depot=zahl('r-depot'), risiko=zahl('r-risiko'), kurs=zahl('r-kurs'), stop=zahl('r-stop');
    var out=document.getElementById('r-ergebnis'), warn=document.getElementById('r-warnung');
    if(!(depot>0&&risiko>0&&kurs>0&&stop>0)){out.innerHTML='<p class="muted">Bitte alle Felder mit Zahlen füllen.</p>';warn.innerHTML='';return;}
    var risikobetrag=depot*risiko/100, abstand=kurs*stop/100, stueck=Math.floor(risikobetrag/abstand);
    var wert=stueck*kurs, maxverl=stueck*abstand, anteil=wert/depot*100, stopkurs=kurs-abstand;
    function k(t,v){return '<div class="kachel"><div class="muted small">'+t+'</div><div class="zahl">'+v+'</div></div>';}
    out.innerHTML=k('Stückzahl',fmt(stueck,0))+k('Positionswert',fmt(wert,2))+k('Stop-Kurs',fmt(stopkurs,2))+
      k('Max. Verlust (bis Stop)',fmt(maxverl,2))+k('Anteil am Depot',fmt(anteil,1)+' %');
    var w=[];
    if(stueck<1){w.push('Mit diesen Angaben ergibt sich weniger als 1 Stück.');}
    if(anteil>25){w.push('Die Position wäre über 25 % des Depots groß (Klumpenrisiko).');}
    warn.innerHTML=w.length?'<div class="banner small">'+w.join(' ')+'</div>':'';
  }
  var ids=['r-depot','r-risiko','r-kurs','r-stop'];
  for(var m=0;m<ids.length;m++){var el=document.getElementById(ids[m]); if(el){el.addEventListener('input',rechne);}}
  var sel=document.getElementById('r-sig');
  if(sel){sel.addEventListener('change',function(){if(sel.value){document.getElementById('r-kurs').value=sel.value.replace('.',',');rechne();}});}
  rechne();
})();
"""


# ---------- kleine Helfer ----------
def e(x) -> str:
    return html.escape(str(x))


def ist_zahl(x) -> bool:
    return x is not None and not pd.isna(x)


def pct(x, d: int = 2) -> str:
    return f"{x:+.{d}%}" if ist_zahl(x) else "–"


def pct_farbig(x, d: int = 2) -> str:
    if not ist_zahl(x):
        return "–"
    klasse = "pos" if x > 0 else "neg" if x < 0 else ""
    return f'<span class="{klasse}">{x:+.{d}%}</span>'


def datum(iso) -> str:
    try:
        return pd.Timestamp(iso).strftime("%d.%m.%Y")
    except Exception:
        return "–"


def tag_plus(iso: str, n: int) -> str:
    """Datum n Handelstage nach iso (Wochenenden übersprungen, Feiertage nicht)."""
    return str(np.busday_offset(iso, n, roll="forward"))


def klasse_von_gruppe(gruppe: str) -> str:
    return {"KRYPTO": "Krypto", "ROHSTOFF": "Rohstoffe"}.get(gruppe, "Aktien")


def badge(text: str, art: str = "neutral") -> str:
    return f'<span class="badge {art}">{e(text)}</span>'


def kachel(titel: str, wert: str, sub: str = "") -> str:
    zusatz = f'<div class="muted small">{e(sub)}</div>' if sub else ""
    return (f'<div class="kachel"><div class="muted small">{e(titel)}</div>'
            f'<div class="zahl">{wert}</div>{zusatz}</div>')


def tabelle(kopf: list[str], zeilen: list[list[str]], leer: str = "Keine Einträge.") -> str:
    if not zeilen:
        return f'<p class="muted">{e(leer)}</p>'
    th = "".join(f"<th>{e(k)}</th>" for k in kopf)
    tr = "".join("<tr>" + "".join(f"<td>{z}</td>" for z in zeile) + "</tr>" for zeile in zeilen)
    return f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table></div>'


# ---------- Charts (inline SVG) ----------
def sparkline(punkte: list, marken: list | None = None, breite: int = 320, hoehe: int = 90) -> str:
    """punkte: [(datum_iso, schlusskurs), ...]; marken: [(datum_iso, 'a' oder 'b'), ...]"""
    punkte = [(d, v) for d, v in punkte if ist_zahl(v)]
    if len(punkte) < 3:
        return ""
    werte = [v for _, v in punkte]
    lo, hi = min(werte), max(werte)
    spanne = (hi - lo) or 1.0
    n, pad = len(punkte), 7

    def xy(i: int, v: float) -> tuple[float, float]:
        return (pad + (breite - 2 * pad) * i / (n - 1),
                pad + (hoehe - 2 * pad) * (1 - (v - lo) / spanne))

    koord = [xy(i, v) for i, (_, v) in enumerate(punkte)]
    linie = " ".join(f"{x:.1f},{y:.1f}" for x, y in koord)
    flaeche = f"{pad},{hoehe - pad} {linie} {breite - pad},{hoehe - pad}"
    kreise = []
    index = {d: i for i, (d, _) in enumerate(punkte)}
    for d, art in (marken or []):
        d = str(d)[:10]
        if d in index:
            x, y = koord[index[d]]
            kreise.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" class="{"marke" if art == "a" else "marke2"}"/>')
    return (f'<svg class="spark" viewBox="0 0 {breite} {hoehe}" role="img" '
            f'aria-label="Kursverlauf der letzten {n} Handelstage">'
            f'<polygon class="flaeche" points="{flaeche}"/><polyline class="linie" points="{linie}"/>'
            f'{"".join(kreise)}</svg>')


def balken(werte: list[float], breite: int = 320, hoehe: int = 100) -> str:
    werte = [w for w in werte if ist_zahl(w)][-40:]
    if not werte:
        return ""
    maxabs = max(abs(w) for w in werte) or 0.01
    n, pad = len(werte), 6
    mitte = hoehe / 2
    bw = (breite - 2 * pad) / n
    teile = []
    for i, w in enumerate(werte):
        h = max(abs(w) / maxabs * (mitte - pad), 1.0)
        x = pad + i * bw + bw * 0.12
        y = mitte - h if w >= 0 else mitte
        teile.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw * 0.76:.1f}" height="{h:.1f}" '
                     f'class="{"bpos" if w >= 0 else "bneg"}"/>')
    return (f'<svg class="balken" viewBox="0 0 {breite} {hoehe}" role="img" aria-label="Ergebnis je Trade">'
            f'<line class="nulllinie" x1="{pad}" x2="{breite - pad}" y1="{mitte}" y2="{mitte}"/>'
            f'{"".join(teile)}</svg>')


def verlauf_block(verlaeufe: dict | None, ticker: str, marken: list | None = None) -> str:
    punkte = (verlaeufe or {}).get(ticker)
    svg = sparkline(punkte, marken) if punkte else ""
    if not svg:
        return ""
    legende = "grüner Punkt = Signaltag" + (", gelber Punkt = Einstieg" if any(a == "b" for _, a in (marken or [])) else "")
    return (f'<div class="chart">{svg}<div class="muted small">Schlusskurse der letzten {len(punkte)} '
            f'Handelstage ({legende})</div></div>')


# ---------- Backtest-Belege ----------
def gap_belege(erg) -> dict:
    """Backtest-Zeilen der getesteten Regel (Aktien, Kurslücke nach oben), je Haltedauer."""
    if erg is None or erg.empty:
        return {}
    teil = erg[(erg["klasse"] == "Aktien") & (erg["variante"] == GAP_VARIANTE)]
    return {int(z["h"]): z for _, z in teil.iterrows()}


def regel_bestaetigt(erg) -> bool:
    belege = gap_belege(erg)
    return any(bool(belege[h].get("streng", False)) for h in (3, 5) if h in belege)


def bestaetigte_varianten(erg, klasse: str, setup: str, gap) -> list[str]:
    """Welche Varianten dieser Auffälligkeit haben den strengen Backtest bestanden?"""
    if erg is None or erg.empty or "streng" not in erg.columns:
        return []
    if setup == "Kurslücke mit hohem Volumen":
        prefix = "Kurslücke nach oben" if (ist_zahl(gap) and gap > 0) else "Kurslücke nach unten"
    else:
        prefix = setup
    teil = erg[(erg["klasse"] == klasse) & erg["variante"].str.startswith(prefix)
               & erg["streng"].astype(bool)]
    return sorted(set(teil["variante"]))


# ---------- Reiter: Heute ----------
def heute_abschnitt(signale: list[dict], neue_tickers: set, erg, verlaeufe: dict | None,
                    journal_df: pd.DataFrame) -> str:
    belege = gap_belege(erg)
    bestaetigt = regel_bestaetigt(erg)
    teile = ["<h2>Kaufsignale vom letzten Handelstag</h2>"]
    if not signale:
        teile.append('<div class="karte"><p><b>Heute kein Kaufsignal</b> nach der getesteten Regel. '
                     'Das ist an den meisten Tagen so.</p></div>')
    evid = []
    for h in (3, 5):
        if h in belege:
            b = belege[h]
            evid.append(f"{h} Tage: Vorsprung {pct(b['vorsprung'])}, Treffer {b['treffer']:.0%}")
    evid_text = "Backtest (5 Jahre, nach Kosten): " + " · ".join(evid) if evid else ""

    for s in signale:
        d = s["datum"]
        einstieg, ex3, ex5 = tag_plus(d, 1), tag_plus(d, 3), tag_plus(d, 5)
        marken = [badge("Kaufsignal (Paper)", "ok")]
        if s["ticker"] not in neue_tickers:
            marken.append(badge("Position schon offen, nicht neu gezählt", "neutral"))
        if not bestaetigt:
            marken.append(badge("im aktuellen Backtest nicht mehr bestätigt", "no"))
        teile.append(
            '<div class="karte">'
            f'<div class="kopf"><b>{e(s["ticker"])}</b> <span>{e(s["name"])}</span>{"".join(marken)}</div>'
            f'<p>Kurslücke {pct(s["gap"], 1)}, Volumen ×{s["vr"]:.1f} des Durchschnitts, '
            f'Schlusskurs {s["close"]:.2f} (Handelstag {datum(d)}).</p>'
            f'<p><b>Einstieg:</b> zur Eröffnung am {datum(einstieg)}<br>'
            f'<b>Halten:</b> 3 bis 5 Handelstage<br>'
            f'<b>Verkaufen:</b> ca. {datum(ex3)} (nach 3 Tagen) oder ca. {datum(ex5)} (nach 5 Tagen)</p>'
            f'{verlauf_block(verlaeufe, s["ticker"], [(d, "a")])}'
            f'<p class="muted small">{e(evid_text)}</p>'
            '</div>'
        )
    if signale:
        teile.append('<p class="muted small">Daten stammen von Tageskerzen. Termine ohne Berücksichtigung '
                     'von Feiertagen. Der Backtest rechnet mit 0,1 % Kosten je Trade.</p>')

    teile.append("<h3>Zuletzt im Journal</h3>")
    if journal_df is None or journal_df.empty:
        teile.append('<p class="muted">Noch keine Einträge.</p>')
    else:
        zeilen = []
        for _, z in journal_df.sort_values("signal_datum", ascending=False).head(8).iterrows():
            e3 = pct_farbig(z["ergebnis3"]) if ist_zahl(z["ergebnis3"]) else "offen"
            e5 = pct_farbig(z["ergebnis5"]) if ist_zahl(z["ergebnis5"]) else "offen"
            zeilen.append([datum(z["signal_datum"]), e(z["ticker"]), pct(z["gap"], 1), e3, e5])
        teile.append(tabelle(["Signal", "Aktie", "Lücke", "3 Tage", "5 Tage"], zeilen))
    return "".join(teile)


# ---------- Reiter: Positionen ----------
def positionen_abschnitt(journal_df: pd.DataFrame, letzte_kurse: dict, verlaeufe: dict | None) -> str:
    teile = ["<h2>Offene Positionen (Paper-Trading)</h2>"]
    if journal_df.empty or int(journal_df["ausstieg5_datum"].isna().sum()) == 0:
        teile.append('<div class="karte"><p>Keine offenen Positionen.</p></div>')
        return "".join(teile)
    for _, z in journal_df[journal_df["ausstieg5_datum"].isna()].iterrows():
        sd = z["signal_datum"]
        marken = [(sd, "a")]
        if ist_zahl(z["einstieg_preis"]):
            einstieg = f"{datum(z['einstieg_datum'])} zu {z['einstieg_preis']:.2f}"
            kurs = letzte_kurse.get(z["ticker"])
            stand_txt = (pct_farbig(kurs / z["einstieg_preis"] - 1) + " (ohne Kosten)") if kurs else "–"
            marken.append((z["einstieg_datum"], "b"))
            status = badge("läuft", "ok")
        else:
            einstieg = f"folgt, ca. {datum(tag_plus(sd, 1))}"
            stand_txt = "–"
            status = badge("Einstieg folgt", "neutral")
        if ist_zahl(z["ergebnis3"]):
            ex3 = f"{datum(z['ausstieg3_datum'])}: {pct_farbig(z['ergebnis3'])} (nach Kosten)"
        else:
            ex3 = f"ca. {datum(tag_plus(sd, 3))}"
        ex5 = f"ca. {datum(tag_plus(sd, 5))}"
        teile.append(
            '<div class="karte">'
            f'<div class="kopf"><b>{e(z["ticker"])}</b> <span>{e(z["name"])}</span>{status}</div>'
            f'<p><b>Signal:</b> {datum(sd)} · <b>Einstieg:</b> {einstieg}<br>'
            f'<b>Stand:</b> {stand_txt}<br>'
            f'<b>Ausstieg nach 3 Tagen:</b> {ex3}<br><b>Ausstieg nach 5 Tagen:</b> {ex5}</p>'
            f'{verlauf_block(verlaeufe, z["ticker"], marken)}'
            '</div>'
        )
    return "".join(teile)


# ---------- Reiter: Bilanz ----------
def _stat(df: pd.DataFrame, spalte: str):
    r = df[spalte].dropna()
    if r.empty:
        return 0, np.nan, np.nan, np.nan
    return len(r), r.mean(), r.median(), (r > 0).mean()


def bilanz_abschnitt(journal_df: pd.DataFrame) -> str:
    teile = ["<h2>Bilanz des Journals (nach Kosten)</h2>"]
    if journal_df.empty:
        teile.append('<p class="muted">Das Journal ist noch leer. Die Bilanz erscheint, sobald die ersten '
                     'Trades abgeschlossen sind.</p>')
        return "".join(teile)
    offen = int(journal_df["ausstieg5_datum"].isna().sum())
    kacheln = [kachel("Trades gesamt", str(len(journal_df))), kachel("Noch offen", str(offen))]
    for h, spalte in ((3, "ergebnis3"), (5, "ergebnis5")):
        n, mean, median, treffer = _stat(journal_df, spalte)
        kacheln.append(kachel(f"{h} Tage: abgeschlossen", str(n),
                              f"Ø {pct(mean)} · Treffer {treffer:.0%}" if n else "noch keine"))
    teile.append(f'<div class="kacheln">{"".join(kacheln)}</div>')
    n3 = _stat(journal_df, "ergebnis3")[0]
    if n3 < 30:
        teile.append(f'<p class="banner small">Erst {n3} abgeschlossene Trades. Ab etwa 30 wird die '
                     'Bilanz aussagekräftig, vorher sagt sie wenig.</p>')
    fertig = journal_df[journal_df["ergebnis3"].notna()].sort_values("signal_datum")
    if not fertig.empty:
        teile.append('<h3>Ergebnis je Trade (3 Tage, nach Kosten)</h3><div class="karte">'
                     f'{balken(list(fertig["ergebnis3"]))}'
                     '<div class="muted small">Jeder Balken ist ein abgeschlossener Trade, '
                     'links der älteste, rechts der neueste.</div></div>')
    letzte = fertig.sort_values("signal_datum", ascending=False).head(20)
    zeilen = [[datum(z["signal_datum"]), e(z["ticker"]), pct(z["gap"], 1),
               pct_farbig(z["ergebnis3"]), pct_farbig(z["ergebnis5"])] for _, z in letzte.iterrows()]
    teile.append("<h3>Letzte abgeschlossene Trades</h3>")
    teile.append(tabelle(["Signal", "Aktie", "Lücke", "Ergebnis 3 Tage", "Ergebnis 5 Tage"], zeilen,
                         "Noch keine abgeschlossenen Trades."))
    return "".join(teile)


# ---------- Reiter: Markt ----------
def markt_abschnitt(scan_tab, erg) -> str:
    teile = ["<h2>Markt: Gewinner, Verlierer und Auffälligkeiten</h2>"]
    if scan_tab is None or scan_tab.empty:
        teile.append('<p class="muted">Scan heute nicht verfügbar.</p>')
        return "".join(teile)
    tab = scan_tab.copy()
    tab["klasse"] = tab["gruppe"].map(klasse_von_gruppe)
    teile.append(f'<p class="muted small">Geprüft: {len(tab)} Werte, Veränderung zum Vortag auf Basis '
                 'der letzten abgeschlossenen Tageskerze.</p>')
    for klasse in ("Aktien", "Krypto", "Rohstoffe"):
        teil = tab[tab["klasse"] == klasse].dropna(subset=["ret_1d"])
        if teil.empty:
            continue
        zeilen = []
        for _, z in teil.nlargest(5, "ret_1d").iterrows():
            zeilen.append([badge("Gewinner", "ok"), e(z["ticker"]), e(str(z["name"])[:24]),
                           pct_farbig(z["ret_1d"], 1), pct_farbig(z["ret_5d"], 1), f"{z['close']:.2f}"])
        for _, z in teil.nsmallest(5, "ret_1d").iterrows():
            zeilen.append([badge("Verlierer", "no"), e(z["ticker"]), e(str(z["name"])[:24]),
                           pct_farbig(z["ret_1d"], 1), pct_farbig(z["ret_5d"], 1), f"{z['close']:.2f}"])
        teile.append(f"<h3>{e(klasse)}</h3>")
        teile.append(tabelle(["", "Wert", "Name", "1 Tag", "5 Tage", "Kurs"], zeilen))

    teile.append("<h3>Auffälligkeiten (Scan)</h3>")
    teile.append('<p class="muted small">Nur was im Backtest bestätigt ist, gilt als Signal. '
                 'Alles andere ist reine Information.</p>')
    kand = tab[tab["setups"].map(len) > 0].sort_values("vol_ratio", ascending=False)
    zeilen = []
    for _, z in kand.head(60).iterrows():
        for s in z["setups"]:
            ok = bestaetigte_varianten(erg, z["klasse"], s, z["gap"])
            status = badge("im Backtest bestätigt", "ok") if ok else badge("nur Information", "neutral")
            zeilen.append([e(z["ticker"]), e(str(z["name"])[:26]), e(z["klasse"]), e(s),
                           f"{z['close']:.2f}", pct_farbig(z["ret_1d"], 1), pct_farbig(z["ret_5d"], 1),
                           f"{z['rsi']:.0f}", f"×{z['vol_ratio']:.1f}", status])
    teile.append(f'<p class="small">Mit Auffälligkeit: {len(kand)} von {len(tab)} Werten</p>')
    teile.append(tabelle(["Wert", "Name", "Gruppe", "Auffälligkeit", "Kurs", "1 Tag", "5 Tage", "RSI",
                          "Volumen", "Status"], zeilen, "Heute keine Auffälligkeiten."))
    return "".join(teile)


# ---------- Reiter: Backtest ----------
def haltedauer_abschnitt(erg, stand) -> str:
    teile = ["<h2>Wie lange halten? (Backtest der Regel)</h2>"]
    belege = gap_belege(erg)
    if not belege:
        teile.append('<p class="muted">Backtest-Ergebnisse nicht verfügbar.</p>')
        return "".join(teile)
    zeilen = []
    for h in sorted(belege):
        b = belege[h]
        ok = bool(b.get("streng", False))
        zeilen.append([f"{h} Handelstag(e)", f"{int(b['n'])}", pct(b["mean_netto"]), f"{b['treffer']:.0%}",
                       pct(b["fall5"]), pct(b["vorsprung"]), f"{b['t']:.1f}",
                       badge("bestanden" if ok else "nicht bestanden", "ok" if ok else "no")])
    teile.append(tabelle(["Haltedauer", "Fälle", "Ø nach Kosten", "Treffer", "Schlechte 5 %",
                          "Vorsprung zur Basis", "t-Wert", "Strenger Test"], zeilen))
    teile.append('<p class="muted small">Regel: Aktie mit Kurslücke ab +3 % und mindestens doppeltem '
                 'Volumen, Kauf zur nächsten Eröffnung. „Vorsprung“ ist der Unterschied zu einem '
                 'beliebigen Tag. Stunden-Handel (z. B. 2 Stunden) lässt sich mit kostenlosen '
                 'Tagesdaten nicht seriös testen und ist hier nicht abgedeckt. '
                 f'Backtest zuletzt berechnet am {datum(stand)}.</p>')
    return "".join(teile)


def backtest_gesamt_abschnitt(erg) -> str:
    if erg is None or erg.empty:
        return ""
    zeilen = []
    for _, z in erg.iterrows():
        ok = bool(z.get("streng", False))
        zeilen.append([e(z["klasse"]), e(z["variante"]), f"{int(z['h'])} T", f"{int(z['n'])}",
                       pct(z["mean_netto"]), f"{z['treffer']:.0%}", pct(z["vorsprung"]),
                       f"{z['t']:.1f}" if ist_zahl(z["t"]) else "–",
                       badge("bestanden" if ok else "nein", "ok" if ok else "neutral")])
    return ("<details><summary>Alle Backtest-Ergebnisse (Aktien, Krypto, Rohstoffe)</summary>"
            + tabelle(["Gruppe", "Variante", "Halten", "Fälle", "Ø netto", "Treffer", "Vorsprung",
                       "t", "Test"], zeilen) + "</details>")


# ---------- Reiter: Rechner ----------
def rechner_abschnitt(signale: list[dict]) -> str:
    optionen = "".join(f'<option value="{s["close"]:.2f}">{e(s["ticker"])} (Schlusskurs {s["close"]:.2f})</option>'
                       for s in signale)
    return (
        "<h2>Risikorechner</h2>"
        '<p class="muted small">Zeigt, wie viele Stück zu deinem Risiko passen. Die Eingaben bleiben auf '
        'deinem Gerät und werden nirgends gespeichert. Das ist eine reine Rechnung und keine Empfehlung. '
        'Ein Stop-Loss ist im Backtest nicht geprüft, und über Nacht kann ein Kurs über den Stop hinaus '
        'springen. Gebühren sind nicht enthalten.</p>'
        '<div class="karte rechner">'
        '<label for="r-depot">Depotgröße (€, ohne Tausenderpunkt, z. B. 10000)</label>'
        '<input id="r-depot" type="text" inputmode="decimal" value="10000">'
        '<label for="r-risiko">Risiko pro Trade (% vom Depot)</label>'
        '<input id="r-risiko" type="text" inputmode="decimal" value="1">'
        '<label for="r-sig">Kaufkurs von einem heutigen Signal übernehmen (optional)</label>'
        f'<select id="r-sig"><option value="">–</option>{optionen}</select>'
        '<label for="r-kurs">Kaufkurs</label>'
        '<input id="r-kurs" type="text" inputmode="decimal" value="100">'
        '<label for="r-stop">Stop-Loss (% unter dem Kaufkurs)</label>'
        '<input id="r-stop" type="text" inputmode="decimal" value="5">'
        '</div><div id="r-warnung"></div><div id="r-ergebnis" class="kacheln"></div>'
    )


# ---------- Reiter: Info ----------
def erklaerung_abschnitt() -> str:
    return (
        "<h2>So liest du die Seite</h2>"
        '<div class="karte small">'
        "<p><b>Kaufsignal (Paper):</b> Eine Aktie hatte am letzten Handelstag eine Kurslücke von mindestens "
        "+3 % bei mindestens doppeltem Volumen. Das ist die einzige Regel, die den Backtest bestanden hat.</p>"
        "<p><b>Halten:</b> 3 bis 5 Handelstage. Danach wird verkauft, ein eigenes Verkaufssignal gibt es nicht.</p>"
        "<p><b>Vorsprung:</b> Unterschied des Ergebnisses zu einem beliebigen Tag. Nur wenn er klein ist, "
        "bleibt nach Kosten wenig übrig.</p>"
        "<p><b>t-Wert:</b> Maß, wie unwahrscheinlich ein Zufall ist. Ab etwa 2,5 gilt es hier als auffällig.</p>"
        "<p><b>Treffer:</b> Anteil der Trades, die nach Kosten im Plus endeten.</p>"
        "<p><b>Paper-Trading:</b> Es wird nichts gekauft. Das Journal protokolliert nur, was die Regel "
        "getan hätte, damit du ehrlich siehst, ob sie in neuen Daten hält.</p>"
        "<p><b>Grenzen:</b> Nur heutige Index-Mitglieder im Test (Überlebensverzerrung), kostenlose "
        "Datenquelle, nur Tagesdaten, nur ein Marktumfeld. Vergangenheit garantiert nichts.</p>"
        "</div>"
    )


def status_abschnitt(status: dict) -> str:
    zeilen = [[e(k), e(v)] for k, v in status.items()]
    return "<h2>Systemstatus</h2>" + tabelle(["Punkt", "Wert"], zeilen)


# ---------- Gesamtseite ----------
def naechster_verkauf(journal_df: pd.DataFrame, heute_iso: str):
    if journal_df is None or journal_df.empty:
        return None
    termine = []
    for _, z in journal_df[journal_df["ausstieg5_datum"].isna()].iterrows():
        for n in ((5,) if ist_zahl(z["ergebnis3"]) else (3, 5)):
            termine.append(tag_plus(z["signal_datum"], n))
    kuenftig = sorted(t for t in termine if t >= heute_iso)
    return kuenftig[0] if kuenftig else None


def baue_seite(stand: datetime, signale: list[dict], neue_tickers: set, journal_df: pd.DataFrame,
               letzte_kurse: dict, scan_tab, erg, backtest_stand, status: dict,
               verlaeufe: dict | None = None) -> str:
    heute_iso = stand.strftime("%Y-%m-%d")
    offen = 0 if journal_df.empty else int(journal_df["ausstieg5_datum"].isna().sum())
    verkauf = naechster_verkauf(journal_df, heute_iso)
    bestaetigt = regel_bestaetigt(erg)

    kacheln = "".join([
        kachel("Kaufsignale vom letzten Handelstag", str(len(signale)),
               "Regel bestätigt" if bestaetigt else "Regel aktuell nicht bestätigt"),
        kachel("Offene Positionen", str(offen), "Paper-Trading"),
        kachel("Nächster Verkauf", datum(verkauf) if verkauf else "–"),
        kachel("Datenstand", status.get("Letzte Aktien-Tageskerze", "–"), "letzte Tageskerze"),
    ])

    reiter = [("t-heute", "Heute"), ("t-positionen", "Positionen"), ("t-bilanz", "Bilanz"),
              ("t-markt", "Markt"), ("t-backtest", "Backtest"), ("t-rechner", "Rechner"), ("t-info", "Info")]
    tabs = "".join(f'<button type="button" data-t="{i}">{e(n)}</button>' for i, n in reiter)

    inhalte = {
        "t-heute": heute_abschnitt(signale, neue_tickers, erg, verlaeufe, journal_df),
        "t-positionen": positionen_abschnitt(journal_df, letzte_kurse, verlaeufe),
        "t-bilanz": bilanz_abschnitt(journal_df),
        "t-markt": markt_abschnitt(scan_tab, erg),
        "t-backtest": haltedauer_abschnitt(erg, backtest_stand) + backtest_gesamt_abschnitt(erg),
        "t-rechner": rechner_abschnitt(signale),
        "t-info": erklaerung_abschnitt() + status_abschnitt(status),
    }
    sektionen = "".join(f'<section class="tab" id="{i}">{inhalte[i]}</section>' for i, _ in reiter)

    kopf = (
        '<!doctype html><html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        '<meta http-equiv="refresh" content="1800">'
        '<meta name="apple-mobile-web-app-title" content="Journal">'
        '<meta name="color-scheme" content="light dark">'
        "<title>Trading-Journal</title>"
        "<script>document.documentElement.className='js';</script>"
        f"<style>{CSS}</style></head><body><div class=\"wrap\">"
    )
    banner = (
        '<div class="banner small"><b>Paper-Trading:</b> Hier wird nichts gekauft. Nur die Regel '
        '„Kurslücke nach oben mit hohem Volumen“ (Aktien, Halten 3 bis 5 Handelstage) hat den '
        'Backtest bestanden, alles andere ist Information. Keine Anlageberatung, Verluste sind jederzeit möglich.</div>'
    )
    titel = (f'<h1>Trading-Journal</h1><div class="muted small">Stand: {stand.strftime("%d.%m.%Y %H:%M")} '
             '(Berlin) · aktualisiert an Börsentagen morgens</div>')
    return "".join([
        kopf, titel, f'<div class="kacheln">{kacheln}</div>', banner,
        f'<nav class="tabs">{tabs}</nav>', sektionen,
        f"</div><script>{JS}</script></body></html>",
    ])
