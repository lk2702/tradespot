"""
Baustein 1: Universum und lokale Kursdatenbank (kostenlos, nur Tagesdaten).

Installation:  pip install yfinance pandas lxml

Befehle:
    python daten_basis.py init     Universum aufbauen + erste Kursdaten laden
    python daten_basis.py update   neue Kurse nachladen (am besten nach Börsenschluss)
    python daten_basis.py status   Überblick über die Datenbank

Die Daten landen in kurse.db (im selben Ordner). Spätere Bausteine lesen
von dort, statt jedes Mal alles neu zu laden.

Hinweise:
- Ein Tageskerzen-Eintrag von heute kann unvollständig sein, solange die Börse
  offen ist. Beim nächsten update wird er automatisch überschrieben.
- Datenquelle yfinance ist kostenlos, aber inoffiziell und kann Lücken haben.

Lernprojekt, keine Anlageberatung.
"""
import io
import sqlite3
import ssl
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import certifi
import pandas as pd
import yfinance as yf

DB_DATEI = Path(__file__).with_name("kurse.db")
HISTORIE_JAHRE = 5
MIN_UMSATZ = 5_000_000  # durchschnittlicher Tagesumsatz (20 Tage) für Aktien

# Wikipedia-Listen: (Gruppe, URL)
AKTIEN_QUELLEN = [
    ("SP500", "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"),
    ("NDX", "https://en.wikipedia.org/wiki/Nasdaq-100"),
    ("DAX", "https://en.wikipedia.org/wiki/DAX"),
]

NOTFALL_AKTIEN = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "JPM", "XOM", "SAP.DE",
]

KRYPTO = {
    "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum", "BNB-USD": "BNB",
    "XRP-USD": "XRP", "SOL-USD": "Solana", "ADA-USD": "Cardano",
    "DOGE-USD": "Dogecoin", "TRX-USD": "TRON", "AVAX-USD": "Avalanche",
    "LINK-USD": "Chainlink", "DOT-USD": "Polkadot", "LTC-USD": "Litecoin",
    "BCH-USD": "Bitcoin Cash", "XLM-USD": "Stellar", "ATOM-USD": "Cosmos",
}

ROHSTOFFE = {
    "GC=F": "Gold", "SI=F": "Silber", "PL=F": "Platin", "HG=F": "Kupfer",
    "CL=F": "Öl (WTI)", "BZ=F": "Öl (Brent)", "NG=F": "Erdgas",
    "ZW=F": "Weizen", "ZC=F": "Mais", "ZS=F": "Sojabohnen",
    "KC=F": "Kaffee", "SB=F": "Zucker", "CT=F": "Baumwolle",
}


# ---------- Datenbank ----------
def verbinde() -> sqlite3.Connection:
    con = sqlite3.connect(DB_DATEI)
    con.execute(
        "CREATE TABLE IF NOT EXISTS universum ("
        "ticker TEXT PRIMARY KEY, name TEXT, gruppe TEXT, liquide INTEGER DEFAULT 1)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS kurse ("
        "ticker TEXT, datum TEXT, open REAL, high REAL, low REAL, close REAL, "
        "volume REAL, PRIMARY KEY (ticker, datum))"
    )
    return con


def lade_kurse(ticker: str, con: sqlite3.Connection | None = None) -> pd.DataFrame:
    """Für spätere Bausteine: Kurse eines Werts als DataFrame (Index = Datum)."""
    eigene = con is None
    con = con or verbinde()
    df = pd.read_sql_query(
        "SELECT datum, open AS Open, high AS High, low AS Low, close AS Close, "
        "volume AS Volume FROM kurse WHERE ticker = ? ORDER BY datum",
        con, params=(ticker,), parse_dates=["datum"], index_col="datum",
    )
    if eigene:
        con.close()
    return df


# ---------- Universum ----------
def _wiki_liste(url: str, gruppe: str) -> list[tuple[str, str]]:
    anfrage = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (trading-basis)"})
    kontext = ssl.create_default_context(cafile=certifi.where())
    html = urllib.request.urlopen(anfrage, timeout=30, context=kontext).read().decode("utf-8")
    for tab in pd.read_html(io.StringIO(html)):
        tab.columns = [c[0] if isinstance(c, tuple) else c for c in tab.columns]
        spalten = {str(c).strip().lower(): c for c in tab.columns}
        sym = next((c for k, c in spalten.items() if "ticker" in k or "symbol" in k), None)
        nam = next(
            (c for k, c in spalten.items() if "company" in k or "security" in k or k == "name"),
            None,
        )
        if sym is None or len(tab) < 20:
            continue
        ergebnis = []
        for _, zeile in tab.iterrows():
            t = str(zeile[sym]).strip()
            if ":" in t:
                t = t.split(":")[-1].strip()
            if not t or t.lower() == "nan":
                continue
            if gruppe == "DAX":
                if "." not in t:  # z. B. ADS -> ADS.DE, AIR.PA bleibt AIR.PA
                    t += ".DE"
            else:
                t = t.replace(".", "-")  # z. B. BRK.B -> BRK-B
            ergebnis.append((t, str(zeile[nam]) if nam is not None else t))
        return ergebnis
    raise ValueError("keine passende Tabelle gefunden")


def baue_universum(con: sqlite3.Connection) -> None:
    eintraege: dict[str, tuple[str, str]] = {}
    for gruppe, url in AKTIEN_QUELLEN:
        try:
            liste = _wiki_liste(url, gruppe)
            print(f"  {gruppe}: {len(liste)} Werte gefunden")
            for ticker, name in liste:
                eintraege.setdefault(ticker, (name, gruppe))
        except Exception as fehler:
            print(f"  Warnung: {gruppe}-Liste konnte nicht geladen werden "
                  f"[{type(fehler).__name__}: {fehler}]")

    if not eintraege:
        print("  Keine Aktienlisten verfügbar, nutze kleine Notfall-Liste.")
        for ticker in NOTFALL_AKTIEN:
            eintraege[ticker] = (ticker, "NOTFALL")

    for ticker, name in KRYPTO.items():
        eintraege[ticker] = (name, "KRYPTO")
    for ticker, name in ROHSTOFFE.items():
        eintraege[ticker] = (name, "ROHSTOFF")

    con.execute("DELETE FROM universum")
    con.executemany(
        "INSERT INTO universum (ticker, name, gruppe) VALUES (?, ?, ?)",
        [(t, n, g) for t, (n, g) in eintraege.items()],
    )
    con.commit()
    print(f"Universum: {len(eintraege)} Werte insgesamt.")


# ---------- Kurse laden ----------
def _zerlege(df: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    ergebnis: dict[str, pd.DataFrame] = {}
    if df is None or df.empty:
        return ergebnis
    if isinstance(df.columns, pd.MultiIndex):
        if "Close" in df.columns.get_level_values(0):  # Felder oben (ein Wert)
            einzeln = df.copy()
            einzeln.columns = einzeln.columns.get_level_values(0)
            ergebnis[tickers[0]] = einzeln.dropna(how="all")
        else:
            vorhanden = set(df.columns.get_level_values(0))
            for t in tickers:
                if t in vorhanden:
                    teil = df[t].dropna(how="all")
                    if not teil.empty:
                        ergebnis[t] = teil
    else:
        ergebnis[tickers[0]] = df.dropna(how="all")
    return ergebnis


def _speichere(con: sqlite3.Connection, ticker: str, df: pd.DataFrame) -> None:
    df = df.dropna(subset=["Close"])
    if df.empty:
        return
    idx = pd.DatetimeIndex(df.index).tz_localize(None)
    zeilen = [
        (ticker, d.strftime("%Y-%m-%d"), float(o), float(h), float(l), float(c), float(v))
        for d, o, h, l, c, v in zip(
            idx, df["Open"], df["High"], df["Low"], df["Close"], df["Volume"]
        )
    ]
    con.executemany(
        "INSERT OR REPLACE INTO kurse (ticker, datum, open, high, low, close, volume) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        zeilen,
    )


def _anpassung_geaendert(con: sqlite3.Connection, ticker: str, df: pd.DataFrame,
                         letzte_datum: str) -> bool:
    """Prüft, ob sich bereits gespeicherte Kurse rückwirkend verändert haben
    (Aktiensplit oder Dividende). Dann muss die Historie neu geladen werden,
    sonst entstehen künstliche Kurslücken in den Daten."""
    idx = pd.DatetimeIndex(df.index).tz_localize(None).strftime("%Y-%m-%d")
    neu = pd.Series(df["Close"].values, index=idx)
    grenze = (date.fromisoformat(letzte_datum) - timedelta(days=3)).isoformat()
    ab = idx.min()
    gespeichert = con.execute(
        "SELECT datum, close FROM kurse WHERE ticker = ? AND datum >= ? AND datum <= ?",
        (ticker, ab, grenze),
    ).fetchall()
    for datum, close_alt in gespeichert:
        close_neu = neu.get(datum)
        if (close_neu is not None and pd.notna(close_neu) and close_alt
                and abs(close_neu / close_alt - 1) > 0.001):
            return True
    return False


def _lade_batches(con: sqlite3.Connection, tickers: list[str], start: date,
                  batch: int = 50, letzte: dict | None = None) -> list[str]:
    geaendert: list[str] = []
    for i in range(0, len(tickers), batch):
        teil = tickers[i:i + batch]
        print(f"  Lade Werte {i + 1}-{i + len(teil)} von {len(tickers)} ...")
        try:
            df = yf.download(
                teil, start=start.isoformat(), auto_adjust=True,
                group_by="ticker", threads=True, progress=False,
            )
        except Exception as fehler:
            print(f"  Fehler bei diesem Paket: {fehler}")
            continue
        for ticker, daten in _zerlege(df, teil).items():
            if letzte and ticker in letzte and _anpassung_geaendert(
                    con, ticker, daten, letzte[ticker]):
                geaendert.append(ticker)
            _speichere(con, ticker, daten)
        con.commit()
        time.sleep(1)  # freundlich zur Datenquelle
    return geaendert


def aktualisiere(con: sqlite3.Connection) -> None:
    tickers = [r[0] for r in con.execute("SELECT ticker FROM universum")]
    letzte = dict(con.execute(
        "SELECT ticker, MAX(datum) FROM kurse GROUP BY ticker").fetchall())
    neu = [t for t in tickers if t not in letzte]
    alt = [t for t in tickers if t in letzte]
    heute = date.today()

    if neu:
        print(f"Volle Historie für {len(neu)} Werte ({HISTORIE_JAHRE} Jahre) ...")
        _lade_batches(con, neu, heute - timedelta(days=365 * HISTORIE_JAHRE))
    if alt:
        start = min(date.fromisoformat(letzte[t]) for t in alt) - timedelta(days=10)
        print(f"Update für {len(alt)} Werte ab {start} ...")
        geaendert = _lade_batches(con, alt, start, letzte=letzte)
        if geaendert:
            print(f"Kursanpassung (Split/Dividende) bei {len(geaendert)} Werten erkannt, "
                  "lade deren Historie komplett neu ...")
            _lade_batches(con, geaendert, heute - timedelta(days=365 * HISTORIE_JAHRE))


# ---------- Liquidität und Status ----------
def berechne_liquiditaet(con: sqlite3.Connection) -> None:
    """Aktien mit zu wenig Tagesumsatz werden als nicht liquide markiert."""
    for ticker, gruppe in con.execute("SELECT ticker, gruppe FROM universum").fetchall():
        if gruppe in ("KRYPTO", "ROHSTOFF"):
            liquide = 1
        else:
            zeilen = con.execute(
                "SELECT close, volume FROM kurse WHERE ticker = ? "
                "ORDER BY datum DESC LIMIT 20", (ticker,)).fetchall()
            umsaetze = [c * v for c, v in zeilen if c is not None and v is not None]
            liquide = int(bool(umsaetze) and sum(umsaetze) / len(umsaetze) >= MIN_UMSATZ)
        con.execute("UPDATE universum SET liquide = ? WHERE ticker = ?", (liquide, ticker))
    con.commit()


def status(con: sqlite3.Connection) -> None:
    print("\nGruppe      Werte   liquide")
    for gruppe, n, l in con.execute(
            "SELECT gruppe, COUNT(*), SUM(liquide) FROM universum GROUP BY gruppe"):
        print(f"{gruppe:<10} {n:>6} {l or 0:>9}")
    n_kurse, anzahl, letzte = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT ticker), MAX(datum) FROM kurse").fetchone()
    print(f"\nKurszeilen: {n_kurse} | Werte mit Daten: {anzahl} | neuester Tag: {letzte}")
    ohne = [r[0] for r in con.execute(
        "SELECT ticker FROM universum WHERE ticker NOT IN (SELECT DISTINCT ticker FROM kurse)")]
    if ohne:
        print(f"Ohne Kursdaten ({len(ohne)}): {', '.join(ohne[:15])}" + (" ..." if len(ohne) > 15 else ""))


def main() -> None:
    befehl = sys.argv[1] if len(sys.argv) > 1 else "hilfe"
    con = verbinde()
    if befehl == "init":
        print("Baue Universum auf ...")
        baue_universum(con)
        aktualisiere(con)
        berechne_liquiditaet(con)
        status(con)
    elif befehl == "update":
        aktualisiere(con)
        berechne_liquiditaet(con)
        status(con)
    elif befehl == "status":
        status(con)
    else:
        print("Befehle: init | update | status")
    con.close()


if __name__ == "__main__":
    main()
