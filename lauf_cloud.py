"""
Täglicher Lauf auf GitHub (siehe .github/workflows/update.yml).

Ablauf:
1. Universum und 5 Jahre Kurse frisch laden (kostenlos über yfinance).
2. Journal aus data/journal.csv wiederherstellen, neue Signale eintragen,
   Ergebnisse abgelaufener Trades nachtragen, Journal wieder speichern.
3. Marktscan über alle Werte (Aktien, Krypto, Rohstoffe).
4. Backtest (einmal pro Woche neu, sonst aus data/backtest.csv).
5. Webseite docs/index.html bauen.
6. Meldung aufs iPhone per ntfy (Topic aus dem GitHub-Secret NTFY_TOPIC).

Bricht ab (und veröffentlicht NICHTS), wenn die Daten unvollständig sind. Dann
bleibt die letzte funktionierende Seite stehen und GitHub meldet den Fehler.

Keine Anlageberatung.
"""
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import backtest as bt
import journal
import scan as scanmodul
import seite
from daten_basis import (DB_DATEI, HISTORIE_JAHRE, _lade_batches, aktualisiere, baue_universum,
                         berechne_liquiditaet, verbinde)

BASIS = Path(__file__).resolve().parent
DATEN = BASIS / "data"
DOCS = BASIS / "docs"
JOURNAL_CSV = DATEN / "journal.csv"
BACKTEST_CSV = DATEN / "backtest.csv"
BACKTEST_STAND = DATEN / "backtest_stand.txt"

BACKTEST_ALTER_TAGE = 7
MIN_UNIVERSUM = 300       # darunter wurden die Aktienlisten nicht geladen
MIN_ABDECKUNG = 0.80      # Anteil der Werte, die Kursdaten haben müssen
MAX_KERZE_ALTER = 6       # letzte Aktien-Kerze darf höchstens so alt sein (Tage)


def fehler_abbruch(text: str) -> None:
    print(f"\nABBRUCH: {text}\nDie Webseite wird nicht aktualisiert.")
    sys.exit(1)


def lade_daten(con) -> None:
    print("Baue Universum auf ...")
    baue_universum(con)
    uni = con.execute("SELECT COUNT(*) FROM universum").fetchone()[0]
    if uni < MIN_UNIVERSUM:
        fehler_abbruch(f"Nur {uni} Werte im Universum (Aktienlisten nicht ladbar).")

    aktualisiere(con)  # volle Historie, die Datenbank ist in der Cloud jedes Mal leer

    heute = date.today()
    for runde in range(1, 4):
        fehlend = [r[0] for r in con.execute(
            "SELECT ticker FROM universum WHERE ticker NOT IN (SELECT DISTINCT ticker FROM kurse)")]
        if len(fehlend) <= (1 - MIN_ABDECKUNG) / 4 * uni:
            break
        print(f"Runde {runde}: {len(fehlend)} Werte ohne Daten, warte kurz und versuche es erneut ...")
        time.sleep(60)
        _lade_batches(con, fehlend, heute - timedelta(days=365 * HISTORIE_JAHRE), batch=20)

    mit_daten = con.execute("SELECT COUNT(DISTINCT ticker) FROM kurse").fetchone()[0]
    if mit_daten < MIN_ABDECKUNG * uni:
        fehler_abbruch(f"Nur {mit_daten} von {uni} Werten haben Kursdaten (Datenquelle blockiert?).")

    letzte = con.execute(
        "SELECT MAX(k.datum) FROM kurse k JOIN universum u ON u.ticker = k.ticker "
        "WHERE u.gruppe NOT IN ('KRYPTO', 'ROHSTOFF') AND k.datum < ?",
        (heute.isoformat(),)).fetchone()[0]
    if not letzte or (heute - date.fromisoformat(letzte)).days > MAX_KERZE_ALTER:
        fehler_abbruch(f"Die neueste Aktien-Kerze ({letzte}) ist zu alt.")

    berechne_liquiditaet(con)


def journal_fuehren(con):
    journal.tabelle_anlegen(con)
    if JOURNAL_CSV.exists():
        alt = pd.read_csv(JOURNAL_CSV)
        if not alt.empty:
            alt.to_sql("journal", con, if_exists="append", index=False)
    signale = journal.finde_signale(con)
    neue = journal.trage_signale_ein(con, signale)
    abgeschlossen = journal.aktualisiere_ergebnisse(con)
    pd.read_sql_query("SELECT * FROM journal ORDER BY id", con).to_csv(JOURNAL_CSV, index=False)
    return signale, neue, abgeschlossen


def backtest_holen(con):
    heute = date.today()
    if BACKTEST_CSV.exists() and BACKTEST_STAND.exists():
        try:
            stand = date.fromisoformat(BACKTEST_STAND.read_text().strip())
            if (heute - stand).days < BACKTEST_ALTER_TAGE:
                return pd.read_csv(BACKTEST_CSV), stand
        except Exception as fehler:
            print(f"Gespeicherter Backtest nicht lesbar ({fehler}), rechne neu.")
    print("Rechne Backtest neu (dauert etwa 1 bis 2 Minuten) ...")
    kurse, klasse_von = bt.lade_kurse(con)
    alle = bt.baue_tabelle(kurse, klasse_von)
    erg = bt.analysiere(alle)
    if erg.empty:
        return erg, heute
    erg["streng"] = erg.apply(bt.streng_bestanden, axis=1)
    erg.to_csv(BACKTEST_CSV, index=False)
    BACKTEST_STAND.write_text(heute.isoformat())
    return erg, heute


def seiten_url() -> str | None:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo or "/" not in repo:
        return None
    besitzer, name = repo.split("/", 1)
    return f"https://{besitzer.lower()}.github.io/{name}/"


def main() -> None:
    start = time.time()
    DATEN.mkdir(exist_ok=True)
    DOCS.mkdir(exist_ok=True)
    if DB_DATEI.exists():
        DB_DATEI.unlink()
    con = verbinde()

    lade_daten(con)
    signale, neue, abgeschlossen = journal_fuehren(con)
    print(f"Signale: {len(signale)} | neu im Journal: {len(neue)} | abgeschlossen: {len(abgeschlossen)}")

    try:
        scan_tab = scanmodul.scan(con)
    except Exception as fehler:
        print(f"Scan fehlgeschlagen: {type(fehler).__name__}: {fehler}")
        scan_tab = None

    erg, bt_stand = backtest_holen(con)

    heute = date.today().isoformat()
    journal_df = pd.read_sql_query("SELECT * FROM journal ORDER BY signal_datum DESC, ticker", con)
    letzte_kurse = {}
    for ticker in journal_df.loc[journal_df["ausstieg5_datum"].isna(), "ticker"]:
        zeile = con.execute("SELECT close FROM kurse WHERE ticker = ? AND datum < ? "
                            "ORDER BY datum DESC LIMIT 1", (ticker, heute)).fetchone()
        if zeile:
            letzte_kurse[ticker] = zeile[0]

    uni = con.execute("SELECT COUNT(*) FROM universum").fetchone()[0]
    mit_daten = con.execute("SELECT COUNT(DISTINCT ticker) FROM kurse").fetchone()[0]
    ohne_ndx = con.execute("SELECT COUNT(*) FROM universum WHERE gruppe = 'NDX'").fetchone()[0] == 0
    letzte_kerze = con.execute(
        "SELECT MAX(k.datum) FROM kurse k JOIN universum u ON u.ticker = k.ticker "
        "WHERE u.gruppe NOT IN ('KRYPTO', 'ROHSTOFF') AND k.datum < ?", (heute,)).fetchone()[0]
    stand = datetime.now(ZoneInfo("Europe/Berlin"))
    status = {
        "Werte im Universum": f"{uni} (S&P 500, DAX, 15 Krypto, 13 Rohstoffe)",
        "Werte mit Kursdaten": str(mit_daten),
        "Letzte Aktien-Tageskerze": seite.datum(letzte_kerze),
        "Datenquelle": "yfinance (kostenlos, inoffiziell, kann Lücken haben)",
        "Nasdaq 100": "nicht im Universum (Liste nicht ladbar)" if ohne_ndx else "enthalten",
        "Bekannte Verzerrung": "Nur heutige Index-Mitglieder und Top-Coins (Überlebensverzerrung)",
        "Laufzeit": f"{time.time() - start:.0f} Sekunden",
    }

    html_text = seite.baue_seite(stand, signale, {s["ticker"] for s in neue}, journal_df,
                                 letzte_kurse, scan_tab, erg, bt_stand, status)
    (DOCS / "index.html").write_text(html_text, encoding="utf-8")
    print("Webseite geschrieben: docs/index.html")

    nachricht = journal.baue_nachricht(con, neue, abgeschlossen)
    if nachricht:
        titel, text = nachricht
        url = seiten_url()
        if url:
            text += f"\n\nDetails: {url}"
        journal.sende_push(titel, text)
    else:
        print("Keine neue Meldung nötig.")
    con.close()


if __name__ == "__main__":
    main()
