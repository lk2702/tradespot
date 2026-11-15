"""
Baustein 3: Backtest der Auffälligkeiten aus scan.py.

Frage: Hatten die Auffälligkeiten in den letzten Jahren einen Vorsprung
gegenüber einem beliebigen Tag, auch nach Abzug von Handelskosten?

Ablauf je Auffälligkeit, getrennt nach Aktien / Krypto / Rohstoffe:
- Signal entsteht am Tagesschluss, Einstieg zum Eröffnungskurs des Folgetags
  (kein Blick in die Zukunft).
- Ausstieg nach 1, 3 oder 5 Handelstagen zum Schlusskurs.
- Kosten pro Trade (Gebühr + Spread + Slippage) werden abgezogen.
- Vergleich mit dem Durchschnitt ALLER Tage derselben Gruppe (Basis).
- Die t-Werte fassen Ereignisse desselben Tages zusammen, damit ein einzelner
  Crash mit 9 betroffenen Coins nicht 9-mal zählt.
- Stabilitätscheck: Der Vorsprung wird für die erste und die zweite
  Zeithälfte getrennt gezeigt. Echte Vorteile sollten in beiden sichtbar sein.

Aufruf:   python backtest.py

GRENZEN (bitte ernst nehmen):
- Überlebensverzerrung: Das Universum besteht aus HEUTIGEN Index-Mitgliedern und
  heutigen Top-Coins. Pleitegegangene oder aussortierte Werte fehlen, das macht
  Ergebnisse tendenziell zu gut. Der Vergleich mit der Basis mildert das ab.
- Viele Tests auf einmal: Einige Treffer entstehen allein durch Zufall. Deshalb
  zählt nur, was streng bestanden wird (siehe STRENG_T und beide Hälften).
- Kostenannahmen sind grob. Leihkosten für Leerverkäufe von Aktien sind nicht
  enthalten. Positionsgrößen sind nicht berücksichtigt.
- Vergangenheit garantiert nichts für die Zukunft.

Lernprojekt, keine Anlageberatung.
"""
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from daten_basis import verbinde
from scan import GAP_SCHWELLE, GAP_VOLUMEN, RSI_UEBERVERKAUFT, VOLUMEN_SPITZE

HORIZONTE = [1, 3, 5]                      # Handelstage ab Einstieg
KOSTEN = {"Aktien": 0.0010, "Krypto": 0.0020, "Rohstoffe": 0.0015}  # je Trade, Hin+Zurück
MIN_EREIGNISSE = 30
MIN_HAELFTE = 10
STRENG_T = 2.5
MIN_TAGE = 20

KLASSEN = ["Aktien", "Krypto", "Rohstoffe"]
CSV_DATEI = Path(__file__).with_name("backtest_ergebnisse.csv")

# (Anzeigename, Spalte, Richtung: +1 long, -1 short)
VARIANTEN = [
    ("Ausbruch nach oben -> long", "oben", 1),
    ("Überverkauft im Aufwärtstrend -> long", "ueber", 1),
    ("Ausbruch nach unten -> short (Trend folgt)", "unten", -1),
    ("Ausbruch nach unten -> long (Erholung)", "unten", 1),
    ("Kurslücke nach oben, hohes Volumen -> long", "gap_auf", 1),
    ("Kurslücke nach unten, hohes Volumen -> short", "gap_ab", -1),
    ("Kurslücke nach unten, hohes Volumen -> long (Erholung)", "gap_ab", 1),
]


# ---------- Daten und Kennzahlen ----------
def lade_kurse(con) -> tuple[pd.DataFrame, dict]:
    kurse = pd.read_sql_query(
        "SELECT k.ticker, k.datum, k.open, k.high, k.low, k.close, k.volume "
        "FROM kurse k JOIN universum u ON u.ticker = k.ticker "
        "WHERE u.liquide = 1 ORDER BY k.ticker, k.datum",
        con, parse_dates=["datum"],
    )
    kurse = kurse[kurse["datum"] < pd.Timestamp(date.today())]  # nur fertige Tageskerzen
    klasse_von = {}
    for ticker, gruppe in con.execute("SELECT ticker, gruppe FROM universum"):
        klasse_von[ticker] = {"KRYPTO": "Krypto", "ROHSTOFF": "Rohstoffe"}.get(gruppe, "Aktien")
    return kurse, klasse_von


def baue_tabelle(kurse: pd.DataFrame, klasse_von: dict) -> pd.DataFrame:
    teile = []
    for ticker, df in kurse.groupby("ticker"):
        df = df.sort_values("datum").dropna(subset=["close"]).reset_index(drop=True)
        if len(df) < 260:
            continue
        o, h, l, c, v = (df[x] for x in ("open", "high", "low", "close", "volume"))

        delta = c.diff()
        gewinn = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        verlust = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        rsi = 100 - 100 / (1 + gewinn / verlust)

        hoch20 = h.shift(1).rolling(20).max()
        tief20 = l.shift(1).rolling(20).min()
        vs = v.shift(1).rolling(20).mean()
        vr = v / vs.where(vs > 0)
        sma50 = c.rolling(50).mean()
        sma200 = c.rolling(200).mean()
        gap = o / c.shift(1) - 1
        gap_gross = (gap.abs() >= GAP_SCHWELLE) & (vr >= GAP_VOLUMEN)

        t = pd.DataFrame({"datum": df["datum"], "ticker": ticker, "klasse": klasse_von[ticker]})
        t["oben"] = (c > hoch20) & (vr >= VOLUMEN_SPITZE) & (c > sma50)
        t["ueber"] = (rsi < RSI_UEBERVERKAUFT) & (c > sma200)
        t["unten"] = (c < tief20) & (vr >= VOLUMEN_SPITZE) & (c <= sma50)
        t["gap_auf"] = gap_gross & (gap > 0)
        t["gap_ab"] = gap_gross & (gap < 0)

        einstieg = o.shift(-1)  # Eröffnungskurs des Folgetags
        for hz in HORIZONTE:
            t[f"fwd{hz}"] = c.shift(-hz) / einstieg - 1
        teile.append(t[sma200.notna()])
    return pd.concat(teile, ignore_index=True)


# ---------- Auswertung ----------
def kennzahlen(g_alle, ev, col, richtung, kosten, mitte) -> dict | None:
    ev = ev.dropna(subset=[col])
    g = g_alle.dropna(subset=[col])
    if len(ev) < MIN_EREIGNISSE:
        return None

    roh = richtung * ev[col]
    netto = roh - kosten
    basis = (richtung * g[col]).mean()
    vorsprung = roh.mean() - basis

    pro_tag = (roh - basis).groupby(ev["datum"]).mean()  # Cluster: ein Wert je Tag
    sd = pro_tag.std(ddof=1)
    t = pro_tag.mean() / (sd / np.sqrt(len(pro_tag))) if len(pro_tag) > 2 and sd > 0 else np.nan

    def haelfte(spalte_ev, spalte_g):
        e = ev[spalte_ev]
        b = g[spalte_g]
        if len(e) < MIN_HAELFTE or b.empty:
            return np.nan
        return richtung * e[col].mean() - (richtung * b[col]).mean()

    return {
        "n": len(ev),
        "tage": ev["datum"].nunique(),
        "mean_netto": netto.mean(),
        "median_netto": netto.median(),
        "treffer": (netto > 0).mean(),
        "fall5": netto.quantile(0.05),
        "vorsprung": vorsprung,
        "t": t,
        "v_h1": haelfte(ev["datum"] <= mitte, g["datum"] <= mitte),
        "v_h2": haelfte(ev["datum"] > mitte, g["datum"] > mitte),
    }


def analysiere(alle: pd.DataFrame) -> pd.DataFrame:
    mitte = alle["datum"].sort_values().iloc[len(alle) // 2]
    zeilen = []
    for klasse in KLASSEN:
        teil = alle[alle["klasse"] == klasse]
        if teil.empty:
            continue
        for name, spalte, richtung in VARIANTEN:
            ev = teil[teil[spalte]]
            for hz in HORIZONTE:
                k = kennzahlen(teil, ev, f"fwd{hz}", richtung, KOSTEN[klasse], mitte)
                if k:
                    zeilen.append({"klasse": klasse, "variante": name, "h": hz, **k})
    return pd.DataFrame(zeilen)


def streng_bestanden(z) -> bool:
    return bool(
        z["n"] >= MIN_EREIGNISSE and z["tage"] >= MIN_TAGE
        and z["mean_netto"] > 0 and z["vorsprung"] > 0
        and z["t"] >= STRENG_T and z["v_h1"] > 0 and z["v_h2"] > 0
    )


def zeige(erg: pd.DataFrame, alle: pd.DataFrame) -> None:
    print(f"Zeitraum: {alle['datum'].min().date()} bis {alle['datum'].max().date()} | "
          f"Werte: {alle['ticker'].nunique()} | Tageszeilen: {len(alle):,}".replace(",", "."))
    for klasse in KLASSEN:
        teil = erg[erg["klasse"] == klasse] if not erg.empty else erg
        if teil.empty:
            print(f"\n=== {klasse}: zu wenige Ereignisse für eine Aussage ===")
            continue
        print(f"\n=== {klasse} (angenommene Kosten {KOSTEN[klasse]:.2%} pro Trade) ===")
        for name, grp in teil.groupby("variante", sort=False):
            print(f"\n{name}")
            print("  Halte  Ereign.  Tage  Ø netto  Median  Treffer  5%-Fall  Vorsprung     t  1.Hälfte  2.Hälfte")
            for _, z in grp.iterrows():
                print(f"  {z['h']:>2} T  {z['n']:>7} {z['tage']:>5} {z['mean_netto']:>8.2%} "
                      f"{z['median_netto']:>7.2%} {z['treffer']:>7.0%} {z['fall5']:>8.2%} "
                      f"{z['vorsprung']:>9.2%} {z['t']:>5.1f} {z['v_h1']:>8.2%} {z['v_h2']:>8.2%}")

    print("\n" + "=" * 60)
    print("Strenger Test (Ø netto > 0, Vorsprung > 0, t >= "
          f"{STRENG_T}, in BEIDEN Zeithälften positiv):")
    if erg.empty:
        print("  Keine auswertbaren Ergebnisse.")
        return
    bestanden = erg[erg.apply(streng_bestanden, axis=1)]
    if bestanden.empty:
        print("  Keine Variante besteht den Test. Das ist ein ehrliches Ergebnis:\n"
              "  Die Auffälligkeiten allein hatten hier keinen verlässlichen Vorsprung.")
    else:
        for _, z in bestanden.iterrows():
            print(f"  {z['klasse']}: {z['variante']}, Haltedauer {z['h']} T "
                  f"(Vorsprung {z['vorsprung']:+.2%}, t {z['t']:.1f}, n {z['n']})")
        print("  Auch das ist nur ein Hinweis, kein Beweis (Zufall, Überlebensverzerrung).")


def main() -> None:
    con = verbinde()
    kurse, klasse_von = lade_kurse(con)
    con.close()
    print("Berechne Kennzahlen für alle Werte ...")
    alle = baue_tabelle(kurse, klasse_von)
    erg = analysiere(alle)
    zeige(erg, alle)
    if not erg.empty:
        erg.to_csv(CSV_DATEI, index=False, sep=";", decimal=",", encoding="utf-8-sig")
        print(f"\nGespeichert: {CSV_DATEI.name}. Keine Anlageberatung.")


if __name__ == "__main__":
    main()
