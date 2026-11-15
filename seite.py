"""
Baut die Webseite docs/index.html aus den Auswertungen (Journal, Scan, Backtest).

Wird von lauf_cloud.py aufgerufen. Die Seite ist eine einzelne HTML-Datei ohne
externe Abhängigkeiten und für das iPhone optimiert.

Keine Anlageberatung.
"""
import html
from datetime import datetime

import numpy as np
import pandas as pd

GAP_VARIANTE = "Kurslücke nach oben, hohes Volumen -> long"

CSS = """
:root{--bg:#f5f6f8;--card:#ffffff;--text:#1a1d21;--muted:#667085;--line:#e4e7ec;
--good:#067647;--goodbg:#e3f5ea;--bad:#b42318;--badbg:#fdeceb;--warn:#8a5a00;--warnbg:#fff4d6}
@media (prefers-color-scheme: dark){:root{--bg:#0e1116;--card:#161b22;--text:#e6e9ee;--muted:#9aa4b2;
--line:#2a313b;--good:#56d28a;--goodbg:#12301f;--bad:#ff9b92;--badbg:#3a1714;--warn:#f0c15a;--warnbg:#3a2d0c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:980px;margin:0 auto;padding:16px}
h1{font-size:1.5rem;margin:8px 0 2px}
h2{font-size:1.15rem;margin:28px 0 10px}
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
.kacheln{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
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


# ---------- Backtest-Belege ----------
def gap_belege(erg) -> dict:
    """Backtest-Zeilen der getesteten Regel (Aktien, Kurslücke nach oben), je Haltedauer."""
    if erg is None or erg.empty:
        return {}
    teil = erg[(erg["klasse"] == "Aktien") & (erg["variante"] == GAP_VARIANTE)]
    return {int(z["h"]): z for _, z in teil.iterrows()}


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


# ---------- Abschnitte ----------
def heute_abschnitt(signale: list[dict], neue_tickers: set, erg) -> str:
    belege = gap_belege(erg)
    bestaetigt = any(bool(belege[h].get("streng", False)) for h in (3, 5) if h in belege)
    teile = ["<h2>Heute: Kaufsignale</h2>"]
    if not signale:
        teile.append('<div class="karte"><p>Am letzten Handelstag gab es kein Kaufsignal nach der '
                     'getesteten Regel. Das ist an den meisten Tagen so.</p></div>')
        return "".join(teile)

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
            f'<p class="muted small">{e(evid_text)}</p>'
            '</div>'
        )
    teile.append('<p class="muted small">Daten stammen von Tageskerzen. Termine ohne Berücksichtigung '
                 'von Feiertagen. Bei Aktien in US-Dollar und Euro fallen Gebühren und Spreads an, '
                 'der Backtest rechnet mit 0,1 % je Trade.</p>')
    return "".join(teile)


def offene_abschnitt(journal_df: pd.DataFrame, letzte_kurse: dict) -> str:
    teile = ["<h2>Offene Positionen (Paper-Trading)</h2>"]
    if journal_df.empty:
        teile.append('<p class="muted">Noch keine Einträge.</p>')
        return "".join(teile)
    offen = journal_df[journal_df["ausstieg5_datum"].isna()]
    zeilen = []
    for _, z in offen.iterrows():
        sd = z["signal_datum"]
        if ist_zahl(z["einstieg_preis"]):
            einstieg = f"{datum(z['einstieg_datum'])} @ {z['einstieg_preis']:.2f}"
            kurs = letzte_kurse.get(z["ticker"])
            aktuell = pct_farbig(kurs / z["einstieg_preis"] - 1) if kurs else "–"
        else:
            einstieg = f"folgt (ca. {datum(tag_plus(sd, 1))})"
            aktuell = "–"
        if ist_zahl(z["ergebnis3"]):
            ex3 = f"{datum(z['ausstieg3_datum'])}: {pct_farbig(z['ergebnis3'])}"
        else:
            ex3 = f"ca. {datum(tag_plus(sd, 3))}"
        ex5 = f"ca. {datum(tag_plus(sd, 5))}"
        zeilen.append([e(z["ticker"]), datum(sd), einstieg, aktuell, ex3, ex5])
    teile.append(tabelle(["Aktie", "Signal", "Einstieg", "Stand (brutto)", "Ausstieg 3 Tage",
                          "Ausstieg 5 Tage"], zeilen, "Keine offenen Positionen."))
    return "".join(teile)


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
    fertig = journal_df[journal_df["ergebnis3"].notna()].sort_values("signal_datum", ascending=False).head(20)
    zeilen = [[datum(z["signal_datum"]), e(z["ticker"]), pct(z["gap"], 1),
               pct_farbig(z["ergebnis3"]), pct_farbig(z["ergebnis5"])] for _, z in fertig.iterrows()]
    teile.append("<h3>Letzte abgeschlossene Trades</h3>")
    teile.append(tabelle(["Signal", "Aktie", "Lücke", "Ergebnis 3 Tage", "Ergebnis 5 Tage"], zeilen,
                         "Noch keine abgeschlossenen Trades."))
    return "".join(teile)


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


def scan_abschnitt(scan_tab, erg) -> str:
    teile = ["<h2>Marktscan: alle Werte (Aktien, Krypto, Rohstoffe)</h2>",
             '<p class="muted small">Auffälligkeiten auf der letzten Tageskerze. Nur was im Backtest '
             'bestätigt ist, gilt als Signal. Alles andere ist reine Information.</p>']
    if scan_tab is None or scan_tab.empty:
        teile.append('<p class="muted">Scan heute nicht verfügbar.</p>')
        return "".join(teile)
    kand = scan_tab[scan_tab["setups"].map(len) > 0].sort_values("vol_ratio", ascending=False)
    teile.append(f'<p class="small">Geprüft: {len(scan_tab)} Werte · mit Auffälligkeit: {len(kand)}</p>')
    zeilen = []
    for _, z in kand.head(60).iterrows():
        klasse = klasse_von_gruppe(z["gruppe"])
        for s in z["setups"]:
            ok = bestaetigte_varianten(erg, klasse, s, z["gap"])
            status = badge("im Backtest bestätigt", "ok") if ok else badge("nur Information", "neutral")
            zeilen.append([e(z["ticker"]), e(str(z["name"])[:26]), e(klasse), e(s),
                           f"{z['close']:.2f}", pct_farbig(z["ret_1d"], 1), pct_farbig(z["ret_5d"], 1),
                           f"{z['rsi']:.0f}", f"×{z['vol_ratio']:.1f}", status])
    teile.append(tabelle(["Wert", "Name", "Gruppe", "Auffälligkeit", "Kurs", "1 Tag", "5 Tage", "RSI",
                          "Volumen", "Status"], zeilen, "Heute keine Auffälligkeiten."))
    return "".join(teile)


def status_abschnitt(status: dict) -> str:
    zeilen = [[e(k), e(v)] for k, v in status.items()]
    return "<h2>Systemstatus</h2>" + tabelle(["Punkt", "Wert"], zeilen)


# ---------- Gesamtseite ----------
def baue_seite(stand: datetime, signale: list[dict], neue_tickers: set, journal_df: pd.DataFrame,
               letzte_kurse: dict, scan_tab, erg, backtest_stand, status: dict) -> str:
    kopf = (
        '<!doctype html><html lang="de"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        '<meta http-equiv="refresh" content="1800">'
        '<meta name="apple-mobile-web-app-title" content="Journal">'
        f"<title>Trading-Journal</title><style>{CSS}</style></head><body><div class=\"wrap\">"
    )
    banner = (
        '<div class="banner"><b>Paper-Trading:</b> Hier wird nichts gekauft. Nur die Regel '
        '„Kurslücke nach oben mit hohem Volumen“ (Aktien, Halten 3 bis 5 Handelstage) hat den '
        'Backtest bestanden. Alles andere ist reine Information. Das ist keine Anlageberatung, '
        'und Verluste sind jederzeit möglich.</div>'
    )
    titel = (f'<h1>Trading-Journal</h1><div class="muted small">Stand: {stand.strftime("%d.%m.%Y %H:%M")} '
             '(Berlin) · aktualisiert an Börsentagen morgens</div>')
    ende = "</div></body></html>"
    return "".join([
        kopf, titel, banner,
        heute_abschnitt(signale, neue_tickers, erg),
        offene_abschnitt(journal_df, letzte_kurse),
        bilanz_abschnitt(journal_df),
        haltedauer_abschnitt(erg, backtest_stand),
        backtest_gesamt_abschnitt(erg),
        scan_abschnitt(scan_tab, erg),
        status_abschnitt(status),
        ende,
    ])
