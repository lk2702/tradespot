"""
Schritt 1: Signal-Journal (Paper-Trading) für den Ansatz
"Kurslücke nach oben mit hohem Volumen" bei Aktien.

Aufrufe:
    python journal.py          täglicher Lauf: Kurse laden, neue Signale suchen,
                               Ergebnisse offener Trades nachtragen, Meldung per ntfy
    python journal.py schnell  wie oben, aber ohne neue Kurse zu laden
    python journal.py bilanz   Zwischenbilanz des Journals anzeigen
    python journal.py test     Test-Benachrichtigung aufs iPhone

Regeln (nicht mitten im Test ändern, sonst ist die Auswertung wertlos):
- Signal: Eine liquide Aktie (S&P 500 / DAX) hat am letzten abgeschlossenen
  Handelstag eine Kurslücke von mindestens +3 % und mindestens das Doppelte
  des 20-Tage-Durchschnittsvolumens (Werte aus scan.py).
- Einstieg: Eröffnungskurs des nächsten Handelstags. Ausstieg: Schlusskurs
  nach 3 bzw. 5 Handelstagen ab dem Signaltag (wie im Backtest).
- Pro Aktie läuft höchstens ein offener Trade.
- Kosten: 0,1 % pro Trade (wie im Backtest) werden abgezogen.
- Lücken über 50 % gelten als verdächtig (Datenfehler/Split) und werden ignoriert.
- Es zählt nur der jeweils letzte abgeschlossene Handelstag. Läuft das Skript
  an einem Tag nicht, fehlen die Signale dieses Tages im Journal.

Das Journal ist Paper-Trading: Es wird nichts gekauft. Keine Anlageberatung.

ntfy: Der Topic-Name wird aus der Umgebungsvariable NTFY_TOPIC gelesen oder aus
der Datei ntfy_topic.txt (eine Zeile) im selben Ordner.
"""
import os
import ssl
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import certifi
import pandas as pd

from daten_basis import aktualisiere, berechne_liquiditaet, verbinde
from scan import GAP_SCHWELLE, GAP_VOLUMEN

KOSTEN = 0.0010            # je Trade, wie im Backtest für Aktien
MAX_PLAUSIBEL_GAP = 0.50   # größere Lücken gelten als verdächtig
MAX_ALTER_TAGE = 5         # Signal-Kerze darf höchstens so alt sein


# ---------- Datenbank ----------
def tabelle_anlegen(con) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS journal ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ticker TEXT, name TEXT, gruppe TEXT, "
        "signal_datum TEXT, signal_close REAL, gap REAL, vol_ratio REAL, "
        "einstieg_datum TEXT, einstieg_preis REAL, "
        "ausstieg3_datum TEXT, ausstieg3_preis REAL, ergebnis3 REAL, "
        "ausstieg5_datum TEXT, ausstieg5_preis REAL, ergebnis5 REAL, "
        "angelegt TEXT, UNIQUE (ticker, signal_datum))"
    )
    con.commit()


# ---------- Signale finden ----------
def finde_signale(con) -> list[dict]:
    heute = date.today()
    seit = (heute - timedelta(days=60)).isoformat()
    df = pd.read_sql_query(
        "SELECT k.ticker, k.datum, k.open, k.close, k.volume "
        "FROM kurse k JOIN universum u ON u.ticker = k.ticker "
        "WHERE u.liquide = 1 AND u.gruppe NOT IN ('KRYPTO', 'ROHSTOFF') "
        "AND k.datum >= ? AND k.datum < ? ORDER BY k.ticker, k.datum",
        con, params=(seit, heute.isoformat()), parse_dates=["datum"],
    )
    info = {t: (n, g) for t, n, g in con.execute("SELECT ticker, name, gruppe FROM universum")}

    signale = []
    for ticker, g in df.groupby("ticker"):
        g = g.dropna(subset=["close"]).reset_index(drop=True)
        if len(g) < 21:
            continue
        letzte, vortag = g.iloc[-1], g.iloc[-2]
        if (pd.Timestamp(heute) - letzte["datum"]).days > MAX_ALTER_TAGE:
            continue
        schnitt = g["volume"].iloc[-21:-1].mean()
        if pd.isna(schnitt) or schnitt <= 0 or pd.isna(letzte["volume"]):
            continue
        gap = letzte["open"] / vortag["close"] - 1
        vr = letzte["volume"] / schnitt
        if gap >= GAP_SCHWELLE and vr >= GAP_VOLUMEN:
            if gap > MAX_PLAUSIBEL_GAP:
                print(f"  Übersprungen (verdächtige Lücke {gap:+.0%}): {ticker}")
                continue
            signale.append({
                "ticker": ticker, "name": info[ticker][0], "gruppe": info[ticker][1],
                "datum": letzte["datum"].strftime("%Y-%m-%d"),
                "close": float(letzte["close"]), "gap": float(gap), "vr": float(vr),
            })
    signale.sort(key=lambda s: s["vr"], reverse=True)
    return signale


def trage_signale_ein(con, signale: list[dict]) -> list[dict]:
    neue = []
    heute = date.today().isoformat()
    for s in signale:
        offen = con.execute(
            "SELECT 1 FROM journal WHERE ticker = ? AND ausstieg5_datum IS NULL",
            (s["ticker"],),
        ).fetchone()
        if offen:
            continue
        cur = con.execute(
            "INSERT OR IGNORE INTO journal "
            "(ticker, name, gruppe, signal_datum, signal_close, gap, vol_ratio, angelegt) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (s["ticker"], s["name"], s["gruppe"], s["datum"], s["close"],
             s["gap"], s["vr"], heute),
        )
        if cur.rowcount == 1:
            neue.append(s)
    con.commit()
    return neue


# ---------- Ergebnisse nachtragen ----------
def aktualisiere_ergebnisse(con) -> list[dict]:
    heute = date.today().isoformat()
    offen = con.execute(
        "SELECT id, ticker, signal_datum, ergebnis3 FROM journal "
        "WHERE ausstieg5_datum IS NULL"
    ).fetchall()
    abgeschlossen = []
    for id_, ticker, sdat, ergebnis3_alt in offen:
        zeilen = con.execute(
            "SELECT datum, open, close FROM kurse WHERE ticker = ? AND datum > ? "
            "AND datum < ? ORDER BY datum LIMIT 5",
            (ticker, sdat, heute),
        ).fetchall()
        if not zeilen:
            continue
        einstieg_preis = zeilen[0][1]
        if not einstieg_preis or einstieg_preis <= 0:
            continue
        felder = {"einstieg_datum": zeilen[0][0], "einstieg_preis": einstieg_preis}
        if len(zeilen) >= 3:
            erg = zeilen[2][2] / einstieg_preis - 1 - KOSTEN
            felder.update(ausstieg3_datum=zeilen[2][0], ausstieg3_preis=zeilen[2][2],
                          ergebnis3=erg)
            if ergebnis3_alt is None:
                abgeschlossen.append({"ticker": ticker, "h": 3, "ergebnis": erg})
        if len(zeilen) >= 5:
            erg = zeilen[4][2] / einstieg_preis - 1 - KOSTEN
            felder.update(ausstieg5_datum=zeilen[4][0], ausstieg5_preis=zeilen[4][2],
                          ergebnis5=erg)
            abgeschlossen.append({"ticker": ticker, "h": 5, "ergebnis": erg})
        sets = ", ".join(f"{k} = ?" for k in felder)
        con.execute(f"UPDATE journal SET {sets} WHERE id = ?", (*felder.values(), id_))
    con.commit()
    return abgeschlossen


# ---------- Bilanz ----------
def bilanz_text(con) -> str:
    df = pd.read_sql_query("SELECT * FROM journal ORDER BY signal_datum, ticker", con)
    if df.empty:
        return "Das Journal ist noch leer."
    zeilen = [f"Trades gesamt: {len(df)} | noch offen: {int(df['ausstieg5_datum'].isna().sum())}"]
    for h, spalte in ((3, "ergebnis3"), (5, "ergebnis5")):
        r = df[spalte].dropna()
        if r.empty:
            zeilen.append(f"Haltedauer {h} Tage: noch keine abgeschlossenen Trades")
        else:
            zeilen.append(
                f"Haltedauer {h} Tage (nach Kosten): {len(r)} abgeschlossen | "
                f"Ø {r.mean():+.2%} | Median {r.median():+.2%} | "
                f"Treffer {(r > 0).mean():.0%} | schlechtester {r.min():+.2%} | "
                f"bester {r.max():+.2%}"
            )
    zeilen.append("Erst ab etwa 30 abgeschlossenen Trades ist das aussagekräftig.")
    return "\n".join(zeilen)


def zeige_bilanz(con) -> None:
    print(bilanz_text(con))
    df = pd.read_sql_query("SELECT * FROM journal ORDER BY signal_datum, ticker", con)
    if df.empty:
        return
    print("\nLetzte Einträge:")
    print("Signal      Aktie       Lücke  Volumen   3 Tage    5 Tage")
    for _, z in df.tail(15).iterrows():
        e3 = f"{z['ergebnis3']:+.2%}" if pd.notna(z["ergebnis3"]) else "offen"
        e5 = f"{z['ergebnis5']:+.2%}" if pd.notna(z["ergebnis5"]) else "offen"
        print(f"{z['signal_datum']}  {z['ticker']:<10} {z['gap']:>+6.1%}  x{z['vol_ratio']:<5.1f} "
              f"{e3:>8}  {e5:>8}")


# ---------- Benachrichtigung ----------
def lese_topic() -> str | None:
    topic = os.environ.get("NTFY_TOPIC")
    if topic and topic.strip():
        return topic.strip()
    datei = Path(__file__).with_name("ntfy_topic.txt")
    if datei.exists():
        inhalt = datei.read_text().strip()
        return inhalt or None
    return None


def sende_push(titel: str, text: str) -> None:
    topic = lese_topic()
    if not topic:
        print("Hinweis: Kein ntfy-Topic gefunden, es wird keine Meldung gesendet.")
        return
    anfrage = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=text.encode("utf-8"),
        headers={
            "Title": titel.encode("latin-1", "replace").decode("latin-1"),
            "Tags": "chart_with_upwards_trend",
            "User-Agent": "trading-journal/1.0",
        },
        method="POST",
    )
    try:
        kontext = ssl.create_default_context(cafile=certifi.where())
        urllib.request.urlopen(anfrage, timeout=20, context=kontext).read()
        print("Meldung aufs iPhone gesendet.")
    except Exception as fehler:
        print(f"Versand fehlgeschlagen [{type(fehler).__name__}: {fehler}]")


def baue_nachricht(con, neue: list[dict], abgeschlossen: list[dict]) -> tuple[str, str] | None:
    teile = []
    if neue:
        teile.append("Neue Signale (Kurslücke nach oben, hohes Volumen, Aktien):")
        for s in neue[:15]:
            teile.append(f"- {s['ticker']} ({s['name'][:24]}): Lücke {s['gap']:+.1%}, "
                         f"Volumen x{s['vr']:.1f}, Schluss {s['close']:.2f}")
        if len(neue) > 15:
            teile.append(f"... und {len(neue) - 15} weitere")
        teile.append("Regel: Einstieg zur nächsten Eröffnung, Ausstieg nach 3 bzw. 5 Handelstagen.")
    if abgeschlossen:
        teile.append("")
        teile.append("Abgeschlossen (nach Kosten):")
        for a in abgeschlossen[:15]:
            teile.append(f"- {a['ticker']}: {a['h']} Tage {a['ergebnis']:+.2%}")
    montag = date.today().weekday() == 0
    hat_ergebnisse = con.execute(
        "SELECT COUNT(*) FROM journal WHERE ergebnis3 IS NOT NULL").fetchone()[0] > 0
    if montag and hat_ergebnisse:
        teile += ["", "Wochenbilanz:", bilanz_text(con)]
    if not teile:
        return None
    teile += ["", "Paper-Trading, keine Anlageberatung."]
    titel = f"Journal: {len(neue)} neu, {len(abgeschlossen)} abgeschlossen"
    return titel, "\n".join(teile)


# ---------- Hauptprogramm ----------
def main() -> None:
    befehl = sys.argv[1] if len(sys.argv) > 1 else "lauf"
    con = verbinde()
    tabelle_anlegen(con)

    if befehl == "test":
        sende_push("Test", "Wenn du das liest, funktionieren die Benachrichtigungen.")
    elif befehl == "bilanz":
        zeige_bilanz(con)
    elif befehl in ("lauf", "schnell"):
        if befehl == "lauf":
            print("Lade neue Kurse ...")
            aktualisiere(con)
            berechne_liquiditaet(con)
        signale = finde_signale(con)
        neue = trage_signale_ein(con, signale)
        abgeschlossen = aktualisiere_ergebnisse(con)

        print(f"\nSignale auf der letzten Handelskerze: {len(signale)} | "
              f"neu ins Journal: {len(neue)} | neu abgeschlossen: {len(abgeschlossen)}")
        for s in neue:
            print(f"  NEU {s['ticker']:<10} {s['name'][:26]:<26} Lücke {s['gap']:+.1%} "
                  f"| Volumen x{s['vr']:.1f} | Schluss {s['close']:.2f} (Kerze {s['datum']})")
        for a in abgeschlossen:
            print(f"  FERTIG {a['ticker']:<10} {a['h']} Tage: {a['ergebnis']:+.2%}")
        print()
        print(bilanz_text(con))

        nachricht = baue_nachricht(con, neue, abgeschlossen)
        if nachricht:
            sende_push(*nachricht)
        else:
            print("\nNichts Neues, keine Meldung gesendet.")
    else:
        print("Befehle: (ohne) | schnell | bilanz | test")
    con.close()


if __name__ == "__main__":
    main()
