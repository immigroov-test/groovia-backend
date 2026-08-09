"""Refresh ppp_factors from World Bank data. Run every 6-12 months (PPP moves slowly).

factor = price level ratio = PPP conversion factor (PA.NUS.PPP) / official FX rate (PA.NUS.FCRF),
US-based so the US is ~1.0. A country at 0.30 is ~30% as expensive as the US, so its mentors are
priced at ~30% when PPP is on. This is the same number the World Bank publishes as
"Price level ratio of PPP conversion factor to market exchange rate" (that indicator was archived,
so we derive it from the two live indicators).

Values are clamped to [PPP_MIN, PPP_MAX]. A handful of countries (Iran, Sudan, Venezuela,
Turkmenistan, Liberia...) run heavily distorted or multiple official FX rates, which makes the raw
ratio meaningless (Iran comes out at 3.5). The clamp keeps those from producing absurd prices; every
real market we serve sits comfortably inside the band.

Usage:
  python -m scripts.refresh_ppp_factors --print-sql   # print the SQL VALUES block for the setup scripts
  python -m scripts.refresh_ppp_factors               # upsert straight into the DB (needs SUPABASE_URL
                                                       #   + SUPABASE_SERVICE_ROLE_KEY in env/.env)
"""
import argparse
import json
import sys
import urllib.request

WB = "https://api.worldbank.org/v2/country/all/indicator/{code}?format=json&per_page=400&mrnev=1"
PPP_MIN = 0.10   # below this is almost always an FX artefact; the DB ppp_floor also lifts the low side
PPP_MAX = 1.50   # above this too (Iran/Sudan); the priciest real markets (CH, IS, BM) sit ~1.1-1.2


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "groovia-ppp-refresh"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


def _series(code: str, real: set[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    j = _get(WB.format(code=code))
    if len(j) < 2 or not j[1]:
        raise RuntimeError(f"World Bank returned no data for {code}: {j[0]}")
    for d in j[1]:
        iso3 = (d.get("countryiso3code") or "").upper()
        v = d.get("value")
        if iso3 in real and v:
            out[iso3] = float(v)
    return out


def compute_factors() -> dict[str, float]:
    """{ISO2: clamped price-level ratio}. US pinned to exactly 1.0000."""
    cmeta = _get("https://api.worldbank.org/v2/country?format=json&per_page=400")[1]
    iso3_to_iso2, real = {}, set()
    for c in cmeta:
        iso2 = (c.get("iso2Code") or "").upper()
        iso3 = (c.get("id") or "").upper()
        region = ((c.get("region") or {}).get("value") or "")
        iso3_to_iso2[iso3] = iso2
        if region and region != "Aggregates" and len(iso2) == 2 and iso2.isalpha():
            real.add(iso3)

    ppp = _series("PA.NUS.PPP", real)     # PPP conversion factor, GDP (LCU per international $)
    fx = _series("PA.NUS.FCRF", real)     # Official exchange rate (LCU per US$, period average)

    out: dict[str, float] = {}
    for iso3, p in ppp.items():
        f = fx.get(iso3)
        iso2 = iso3_to_iso2.get(iso3, "")
        if f and f > 0 and len(iso2) == 2:
            ratio = p / f
            out[iso2] = round(min(PPP_MAX, max(PPP_MIN, ratio)), 4)
    out["US"] = 1.0000
    return dict(sorted(out.items()))


def to_sql_values(factors: dict[str, float]) -> str:
    line, chunks = [], []
    for cc, v in factors.items():
        line.append(f"('{cc}',{v:.4f})")
        if len(line) == 7:
            chunks.append("  " + ",".join(line))
            line = []
    if line:
        chunks.append("  " + ",".join(line))
    return ",\n".join(chunks)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print-sql", action="store_true",
                    help="print the SQL VALUES block instead of writing to the DB")
    args = ap.parse_args()

    factors = compute_factors()
    if args.print_sql:
        print(to_sql_values(factors))
        print(f"-- {len(factors)} countries, World Bank PA.NUS.PPP / PA.NUS.FCRF, clamped [{PPP_MIN}, {PPP_MAX}]",
              file=sys.stderr)
        return 0

    # Upsert into the live table.
    import os
    from dotenv import load_dotenv
    from supabase import create_client
    load_dotenv()
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set (or pass --print-sql).", file=sys.stderr)
        return 1
    sb = create_client(url, key)
    payload = [{"country_code": cc, "factor": v} for cc, v in factors.items()]
    sb.table("ppp_factors").upsert(payload, on_conflict="country_code").execute()
    print(f"Upserted {len(payload)} ppp_factors rows from World Bank data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
