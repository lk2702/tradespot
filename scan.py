"""
Baustein 2: Scan mit Kurzfrist-Kennzahlen und Kandidatenliste.

Liest die Kurse aus kurse.db (siehe daten_basis.py), berechnet für alle
liquiden Werte Kennzahlen auf Tagesbasis und markiert Auffälligkeiten.

Aufrufe:
    python scan.py            Scan mit den vorhandenen Daten
    python scan.py update     erst neue Kurse laden, dann scannen

Ergebnis: Ausgabe im Terminal, kandidaten.csv (öffnet sich in Numbers/Excel)
und ein Eintrag pro Auffälligkeit in der Tabelle scan_log der Datenbank.
Das Protokoll ist der Anfang des Signal-Journals: Später lässt sich damit
prüfen, was aus den Auffälligkeiten tatsächlich geworden ist.

WICHTIG: Das sind Auffälligkeiten, keine Kauf- oder Verkaufsempfehlungen. Die
Schwellenwerte unten sind Annahmen. Ob sie in der Vergangenheit einen
Vorsprung hatten, prüft erst der Backtest in Baustein 3.

Lernprojekt, keine Anlageberatung.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from daten_basis import aktualisiere, berechne_liquiditaet, verbinde

# ---------- Einstellungen ----------
HEUTE_EINBEZIEHEN = False   # False: nur abgeschlossene Tageskerzen (bis gestern)
RUECKBLICK_TAGE = 450       # Kalendertage, genug für die 200-Tage-Linie
TOP_N = 30                  # so viele Kandidaten werden im Terminal gezeigt

VOLUMEN_SPITZE = 1.5        # Volumen mindestens das 1,5-Fache des 20-Tage-Schnitts
RSI_UEBERVERKAUFT = 30
GAP_SCHWELLE = 0.03         # Kurslücke ab 3 %
GAP_VOLUMEN = 2.0

CSV_DATEI = Path(__file__).with_name("kandidaten.csv")


# ---------- Kennzahlen ----------
def berechne_features(df: pd.DataFrame) -> dict | None:
    if len(df) < 60:
        return None
    o, h, l, c, v = df["open"], df["high"], df["low"], df["close"], df["volume"]
    vortag = c.shift(1)

    tr = pd.concat([h - l, (h - vortag).abs(), (l - vortag).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()

    delta = c.diff()
    gewinn = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    verlust = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + gewinn / verlust)

    hoch20 = h.iloc[-21:-1].max()   # die 20 Tage vor dem letzten Tag
    tief20 = l.iloc[-21:-1].min()
    vol_schnitt = v.iloc[-21:-1].mean()
    sma50 = c.rolling(50).mean().iloc[-1]
    sma200 = c.rolling(200).mean().iloc[-1]

    return {
        "datenstand": df.index[-1],
        "close": c.iloc[-1],
        "ret_1d": c.iloc[-1] / c.iloc[-2] - 1,
        "ret_3d": c.iloc[-1] / c.iloc[-4] - 1,
        "ret_5d": c.iloc[-1] / c.iloc[-6] - 1,
        "ret_20d": c.iloc[-1] / c.iloc[-21] - 1,
        "gap": o.iloc[-1] / c.iloc[-2] - 1,
        "vol_ratio": v.iloc[-1] / vol_schnitt if vol_schnitt and vol_schnitt > 0 else np.nan,
        "atr_pct": atr.iloc[-1] / c.iloc[-1],
        "tr_ratio": tr.iloc[-1] / atr.iloc[-2],
        "rsi": rsi.iloc[-1],
        "ueber_sma50": bool(c.iloc[-1] > sma50),
        "ueber_sma200": bool(c.iloc[-1] > sma200),
        "ausbruch_hoch": bool(c.iloc[-1] > hoch20),
        "ausbruch_tief": bool(c.iloc[-1] < tief20),
    }


def finde_setups(f) -> list[str]:
    vr = f["vol_ratio"]
    s = []
    if f["ausbruch_hoch"] and vr >= VOLUMEN_SPITZE and f["ueber_sma50"]:
        s.append("Ausbruch nach oben")
    if f["rsi"] < RSI_UEBERVERKAUFT and f["ueber_sma200"]:
        s.append("Überverkauft im Aufwärtstrend")
    if f["ausbruch_tief"] and vr >= VOLUMEN_SPITZE and not f["ueber_sma50"]:
        s.append("Ausbruch nach unten")
    if abs(f["gap"]) >= GAP_SCHWELLE and vr >= GAP_VOLUMEN:
        s.append("Kurslücke mit hohem Volumen")
    return s


# ---------- Daten und Scan ----------
def lade_alle(con) -> pd.DataFrame:
    seit = (date.today() - timedelta(days=RUECKBLICK_TAGE)).isoformat()
    df = pd.read_sql_query(
        "SELECT k.ticker, k.datum, k.open, k.high, k.low, k.close, k.volume "
        "FROM kurse k JOIN universum u ON u.ticker = k.ticker "
        "WHERE u.liquide = 1 AND k.datum >= ? ORDER BY k.ticker, k.datum",
        con, params=(seit,), parse_dates=["datum"],
    )
    if not HEUTE_EINBEZIEHEN:
        df = df[df["datum"] < pd.Timestamp(date.today())]
    return df


def scan(con) -> pd.DataFrame:
    kurse = lade_alle(con)
    info = {t: (n, g) for t, n, g in con.execute("SELECT ticker, name, gruppe FROM universum")}
    veraltet = pd.Timestamp(date.today() - timedelta(days=7))

    zeilen = []
    for ticker, df in kurse.groupby("ticker"):
        df = df.set_index("datum").dropna(subset=["close"])
        f = berechne_features(df)
        if f is None or f["datenstand"] < veraltet:
            continue
        f.update(ticker=ticker, name=info[ticker][0], gruppe=info[ticker][1])
        zeilen.append(f)

    tab = pd.DataFrame(zeilen)
    # Relative Stärke: Rang der 20-Tage-Rendite innerhalb der eigenen Gruppe (0-100)
    tab["rs_rang"] = tab.groupby("gruppe")["ret_20d"].rank(pct=True) * 100
    tab["setups"] = [finde_setups(z) for _, z in tab.iterrows()]
    return tab


# ---------- Ausgabe und Protokoll ----------
def protokolliere(con, kand: pd.DataFrame) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS scan_log ("
        "scan_datum TEXT, datenstand TEXT, ticker TEXT, gruppe TEXT, setup TEXT, "
        "close REAL, rsi REAL, vol_ratio REAL, ret_5d REAL, "
        "PRIMARY KEY (datenstand, ticker, setup))"
    )
    heute = date.today().isoformat()
    for _, z in kand.iterrows():
        for s in z["setups"]:
            con.execute(
                "INSERT OR IGNORE INTO scan_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (heute, z["datenstand"].strftime("%Y-%m-%d"), z["ticker"], z["gruppe"],
                 s, float(z["close"]), float(z["rsi"]), float(z["vol_ratio"]),
                 float(z["ret_5d"])),
            )
    con.commit()


def zeige(tab: pd.DataFrame) -> pd.DataFrame:
    kand = tab[tab["setups"].map(len) > 0].copy()
    kand["anzahl"] = kand["setups"].map(len)
    kand = kand.sort_values(["anzahl", "vol_ratio"], ascending=False)

    print(f"\nDatenstand: bis {tab['datenstand'].max().date()} | "
          f"geprüft: {len(tab)} Werte | mit Auffälligkeit: {len(kand)}")
    haeufigkeit = pd.Series([s for liste in kand["setups"] for s in liste]).value_counts()
    for name, n in haeufigkeit.items():
        print(f"  {name}: {n}")

    print(f"\nTop {min(TOP_N, len(kand))} (nach Anzahl der Auffälligkeiten, dann Volumen):\n")
    for _, z in kand.head(TOP_N).iterrows():
        print(f"{z['ticker']:<10} {str(z['name'])[:24]:<24} {z['gruppe']:<8} "
              f"{' + '.join(z['setups'])}")
        print(f"           Kurs {z['close']:.2f} | 1T {z['ret_1d']:+.1%} | 5T {z['ret_5d']:+.1%} "
              f"| RSI {z['rsi']:.0f} | Volumen x{z['vol_ratio']:.1f} | RS-Rang {z['rs_rang']:.0f}")
    return kand


def main() -> None:
    con = verbinde()
    if len(sys.argv) > 1 and sys.argv[1] == "update":
        print("Lade neue Kurse ...")
        aktualisiere(con)
        berechne_liquiditaet(con)

    tab = scan(con)
    kand = zeige(tab)
    protokolliere(con, kand)

    export = kand.copy()
    export["setups"] = export["setups"].map(" + ".join)
    spalten = ["ticker", "name", "gruppe", "datenstand", "setups", "close", "ret_1d",
               "ret_3d", "ret_5d", "ret_20d", "gap", "vol_ratio", "atr_pct", "rsi",
               "rs_rang", "ueber_sma50", "ueber_sma200"]
    export[spalten].to_csv(CSV_DATEI, index=False, sep=";", decimal=",", encoding="utf-8-sig")
    print(f"\nGespeichert: {CSV_DATEI.name} und Protokoll in scan_log. "
          "Keine Anlageberatung.")
    con.close()


if __name__ == "__main__":
    main()
