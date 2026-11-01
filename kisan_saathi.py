"""
Kisan Saathi - farmer weather, soil and crop advisory (Python version)

Live data only (no mock data):
  * Open-Meteo Geocoding + Forecast API  (weather, ET0)
  * ISRIC SoilGrids REST API             (clay, sand, pH, organic carbon, CEC)
If SoilGrids is down, pH/CEC/carbon are reported as None, never invented.

Install:   pip install requests flask
CLI:       python kisan_saathi.py --place "Bihta, Patna" --crop rice --ribbon 1 \
                --colour alluvial --drain 1 --cracks 0 --stage plan --irrigation yes
API:       python kisan_saathi.py --serve
           then open  http://localhost:5000/api/advice?place=Bihta&crop=rice
"""
import argparse
import json
from datetime import datetime

import requests

HEAVY_RAIN_MM = 64.5      # IMD "heavy rain" threshold (mm/day)
STRONG_RAIN_MM = 35.0

# Agronomy reference parameters (FAO-56 style), not weather/farmer data.
# topt = ideal temp range C, tcrit = (cold limit, heat limit) C,
# kc = crop coefficient, root = rooting depth m, wl = waterlogging sensitivity 0-1
CROPS = {
    "rice":     dict(name="Dhan (Rice)",       topt=(22, 32), tcrit=(15, 38), kc=1.15, root=0.3, wl=0.1),
    "wheat":    dict(name="Gehun (Wheat)",     topt=(15, 24), tcrit=(5, 32),  kc=1.10, root=0.8, wl=0.7),
    "maize":    dict(name="Makka (Maize)",     topt=(21, 30), tcrit=(10, 38), kc=1.10, root=0.8, wl=0.8),
    "cotton":   dict(name="Kapas (Cotton)",    topt=(25, 32), tcrit=(16, 40), kc=1.10, root=1.0, wl=0.6),
    "soy":      dict(name="Soybean",           topt=(22, 30), tcrit=(12, 36), kc=1.05, root=0.6, wl=0.8),
    "mustard":  dict(name="Sarson (Mustard)",  topt=(15, 25), tcrit=(5, 32),  kc=1.05, root=0.7, wl=0.6),
    "potato":   dict(name="Aloo (Potato)",     topt=(15, 22), tcrit=(7, 30),  kc=1.10, root=0.4, wl=0.9),
    "chickpea": dict(name="Chana (Chickpea)",  topt=(18, 26), tcrit=(8, 34),  kc=1.00, root=0.7, wl=0.8),
    "cane":     dict(name="Ganna (Sugarcane)", topt=(24, 32), tcrit=(15, 40), kc=1.20, root=1.0, wl=0.4),
}


# ----------------------------------------------------------------- live data
def _get(url, params):
    r = requests.get(url, params=params, timeout=25)
    r.raise_for_status()
    return r.json()


def geocode(place):
    d = _get("https://geocoding-api.open-meteo.com/v1/search",
             {"name": place, "count": 1, "country_code": "IN", "language": "en"})
    if not d.get("results"):
        raise ValueError(f"Jagah nahi mili: {place!r}")
    r = d["results"][0]
    return {"lat": r["latitude"], "lon": r["longitude"],
            "name": f"{r['name']}, {r.get('admin1', '')}"}


def fetch_weather(lat, lon):
    """30 past days + 16 forecast days of daily weather. Nulls are cleaned."""
    d = _get("https://api.open-meteo.com/v1/forecast", {
        "latitude": lat, "longitude": lon, "timezone": "auto",
        "past_days": 30, "forecast_days": 16,
        "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min,"
                 "et0_fao_evapotranspiration"})["daily"]

    def zero(a):
        return [0.0 if v is None else v for a_ in [a] for v in a_]

    def fill(a):
        first = next((v for v in a if v is not None), 25.0)
        out, prev = [], first
        for v in a:
            prev = prev if v is None else v
            out.append(prev)
        return out

    return {"time": d["time"],
            "rain": zero(d["precipitation_sum"]),
            "et0": zero(d["et0_fao_evapotranspiration"]),
            "tmax": fill(d["temperature_2m_max"]),
            "tmin": fill(d["temperature_2m_min"])}


def fetch_soilgrids(lat, lon):
    """Real soil properties at 5-15 cm. Returns None if the service is down."""
    try:
        d = _get("https://rest.isric.org/soilgrids/v2.0/properties/query", {
            "lon": lon, "lat": lat, "depth": "5-15cm", "value": "mean",
            "property": ["clay", "sand", "phh2o", "soc", "cec"]})
        out = {}
        for layer in d["properties"]["layers"]:
            v = layer["depths"][0]["values"]["mean"]
            out[layer["name"]] = None if v is None else v / layer["unit_measure"]["d_factor"]
        return out if out.get("clay") is not None else None
    except Exception:
        return None


# ---------------------------------------------------------------- soil model
def soil_model(ans, sg):
    """ans: ribbon 0-2, colour (black/red/alluvial/pale), drain 0-2, cracks 0-2."""
    clay_f = [8, 24, 45][ans["ribbon"]]
    clay_f += {2: 8, 1: 3, 0: 0}[ans["cracks"]]
    clay_f += {2: 5, 1: 0, 0: -4}[ans["drain"]]
    clay_f += 6 if ans["colour"] == "black" else 0
    clay_f = max(3, min(65, clay_f))

    clay = (clay_f + sg["clay"]) / 2 if sg else clay_f
    sand = (sg["sand"] + max(10, 100 - clay_f - 25)) / 2 if sg else max(10, 100 - clay - 25)
    silt = max(0.0, 100 - clay - sand)

    colour = ans["colour"]
    if clay >= 40 and (colour == "black" or ans["cracks"] == 2):
        kind, mineral = "Kaali mitti (Regur / Vertisol)", "Montmorillonite (swelling 2:1 clay)"
    elif colour == "red":
        kind, mineral = "Laal mitti (Red / Alfisol)", "Kaolinite + hematite (Fe2O3)"
    elif clay < 15:
        kind, mineral = "Retili mitti (Sandy loam)", "Quartz (SiO2) dominant"
    else:
        kind, mineral = "Dua-sat mitti (Alluvial loam)", "Illite + quartz + feldspar"

    awc = 190 if clay >= 40 else 160 if clay >= 22 else 85 if clay < 15 else 140  # mm water / m soil
    drainage = "dheema" if clay >= 40 else "tez" if clay < 15 else "theek"
    return {"type": kind, "mineral": mineral,
            "clay": round(clay, 1), "sand": round(sand, 1), "silt": round(silt, 1),
            "awc_mm_per_m": awc, "drainage": drainage,
            "ph": sg.get("phh2o") if sg else None,
            "organic_carbon_g_kg": sg.get("soc") if sg else None,
            "cec_mmol_kg": sg.get("cec") if sg else None,
            "soilgrids_live": sg is not None}


# ------------------------------------------------------------- analysis core
def analyse(w, soil, crop, irrigation, planning):
    n = 30                                    # first 30 entries are past days
    rain, et0, tmax, tmin, days = w["rain"], w["et0"], w["tmax"], w["tmin"], w["time"]
    taw = soil["awc_mm_per_m"] * crop["root"] * 1000 / 1000   # total available water, mm
    mad = 0.5 * taw                                           # allowed depletion

    # Bucket model: Dr = root-zone depletion (0 = field capacity)
    kc = 0.6 if planning else crop["kc"]
    dr, irr_plan = taw * 0.5, []
    for i in range(len(days)):
        dr = dr + et0[i] * kc - rain[i]
        dr = max(0.0, dr)
        if i >= n and irrigation and dr > mad:
            irr_plan.append({"date": days[i], "mm": round(dr)})
            dr = 0.0
        dr = min(dr, taw)
    dr_end = dr

    fut = slice(n, None)
    heavy = [days[n + i] for i, p in enumerate(rain[fut]) if p >= HEAVY_RAIN_MM]
    strong = [days[n + i] for i, p in enumerate(rain[fut]) if STRONG_RAIN_MM <= p < HEAVY_RAIN_MM]
    rain_p, rain_f = sum(rain[:n]), sum(rain[fut])
    et_p, et_f = sum(et0[:n]), sum(et0[fut])
    dryness = (rain_p + rain_f) / max(1.0, et_p + et_f)
    heat = sum(t > crop["tcrit"][1] for t in tmax[fut])
    cold = sum(t < crop["tcrit"][0] for t in tmin[fut])

    # sowing window (next 10 days)
    good = []
    for i in range(10):
        j = n + i
        tmean = (tmax[j] + tmin[j]) / 2
        r3 = sum(rain[j:j + 3])
        t_ok = crop["topt"][0] - 3 <= tmean <= crop["topt"][1] + 2
        w_ok = r3 >= 8 or dr_end < taw * 0.6 or irrigation
        if t_ok and rain[j] < 15 and r3 < 30 and w_ok:
            good.append(days[j])

    # risk heuristics 0-100
    drain_mult = 1.5 if soil["drainage"] == "dheema" else 1.0
    r_water = min(100, len(heavy) * 28 * (0.4 + crop["wl"]) + len(strong) * 10 * crop["wl"] * drain_mult)
    r_dry = min(100, max(0.0, 0.7 - dryness) * 140 * (0.5 if irrigation else 1.1))
    r_temp = min(100, heat * 10 + cold * 10)
    risk = round(min(100, max(r_water, r_dry, r_temp) * 0.7 + (r_water + r_dry + r_temp) / 3 * 0.3))

    return {"taw_mm": round(taw), "mad_mm": round(mad), "depletion_mm": round(dr_end),
            "rain_past30_mm": round(rain_p), "rain_next16_mm": round(rain_f),
            "et0_next16_mm": round(et_f), "dryness_index": round(dryness, 2),
            "heavy_rain_days": heavy, "strong_rain_days": strong,
            "heat_days": heat, "cold_days": cold,
            "good_sowing_days": good if planning else None,
            "irrigation_plan": irr_plan if irrigation else None,
            "risk": {"overall": risk, "waterlogging": round(r_water),
                     "drought": round(r_dry), "temperature": round(r_temp)}}


def advice_lines(a, crop, planning, irrigation):
    out = []
    if a["heavy_rain_days"]:
        out.append(f"BHARI BAARISH ({HEAVY_RAIN_MM}mm+): {', '.join(a['heavy_rain_days'])}. "
                   "Nikasi ki nali saaf rakhein, khad/dawa chhidakne se bachein.")
    if a["strong_rain_days"]:
        out.append(f"Tez baarish (35-64mm): {', '.join(a['strong_rain_days'])}.")
    if a["dryness_index"] < 0.35:
        out.append("SOOKHE ka khatra: baarish zarurat ka bahut kam hai. Paani bachayein, mulching karein.")
    elif a["dryness_index"] < 0.6:
        out.append("Baarish kam hai. Sinchai ka dhyan rakhein.")
    if a["heat_days"]:
        out.append(f"{a['heat_days']} din garmi {crop['tcrit'][1]}C se upar.")
    if a["cold_days"]:
        out.append(f"{a['cold_days']} din raat {crop['tcrit'][0]}C se neeche (paala/thand).")
    if not out:
        out.append("Agle 16 din me koi bada mausam khatra nahi dikh raha.")
    if planning:
        g = a["good_sowing_days"]
        out.append("Bone ke achhe din: " + ", ".join(g) if g else
                   "Agle 10 din bone ke liye theek nahi, 3-4 din ruk kar dubara dekhein.")
    if irrigation:
        plan = a["irrigation_plan"]
        out += [f"{p['date']}: lagbhag {p['mm']} mm sinchai dein" for p in plan] or \
               ["Agle 16 din sinchai ki zarurat nahi dikh rahi."]
    else:
        out.append("Sinchai ka saadhan nahi: baarish par nirbhar, mulching karein.")
    return out


# ------------------------------------------------------------------- driver
def build_report(place, crop_key, ans, planning=True, irrigation=True, coords=None):
    crop = CROPS[crop_key]
    loc = coords or geocode(place)
    weather = fetch_weather(loc["lat"], loc["lon"])
    sg = fetch_soilgrids(loc["lat"], loc["lon"])
    soil = soil_model(ans, sg)
    a = analyse(weather, soil, crop, irrigation, planning)
    return {"location": loc, "crop": crop["name"], "soil": soil, "analysis": a,
            "advice": advice_lines(a, crop, planning, irrigation),
            "sources": {"open_meteo": True, "soilgrids": sg is not None},
            "generated_at": datetime.now().isoformat(timespec="seconds")}


def make_app():
    from flask import Flask, jsonify, request
    app = Flask(__name__)

    @app.get("/")
    def home():
        import os
        from flask import send_from_directory
        return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "index.html")

    @app.get("/api/advice")
    def advice():
        q = request.args
        try:
            ans = {"ribbon": int(q.get("ribbon", 1)), "colour": q.get("colour", "alluvial"),
                   "drain": int(q.get("drain", 1)), "cracks": int(q.get("cracks", 0))}
            coords = None
            if q.get("lat") and q.get("lon"):
                coords = {"lat": float(q["lat"]), "lon": float(q["lon"]), "name": "GPS location"}
            rep = build_report(q.get("place", ""), q.get("crop", "rice"), ans,
                               q.get("stage", "plan") == "plan", q.get("irrigation", "1") in ("1", "yes"), coords)
            return jsonify(rep)
        except (ValueError, KeyError) as e:
            return jsonify({"error": str(e)}), 400
        except requests.RequestException as e:
            return jsonify({"error": f"Live data service error: {e}"}), 502

    return app


app = make_app()   # gunicorn entry point: gunicorn kisan_saathi:app


def main():
    p = argparse.ArgumentParser(description="Kisan Saathi")
    p.add_argument("--place", default="Patna")
    p.add_argument("--crop", default="rice", choices=CROPS)
    p.add_argument("--ribbon", type=int, default=1, choices=[0, 1, 2], help="0 none, 1 short, 2 long ribbon")
    p.add_argument("--colour", default="alluvial", choices=["black", "red", "alluvial", "pale"])
    p.add_argument("--drain", type=int, default=1, choices=[0, 1, 2], help="0 fast, 1 1-2 days, 2 many days")
    p.add_argument("--cracks", type=int, default=0, choices=[0, 1, 2])
    p.add_argument("--stage", default="plan", choices=["plan", "sown"])
    p.add_argument("--irrigation", default="yes", choices=["yes", "no"])
    p.add_argument("--json", action="store_true", help="print raw JSON")
    p.add_argument("--serve", action="store_true", help="run Flask API on :5000")
    a = p.parse_args()

    if a.serve:
        import os
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
        return
    ans = {"ribbon": a.ribbon, "colour": a.colour, "drain": a.drain, "cracks": a.cracks}
    rep = build_report(a.place, a.crop, ans, a.stage == "plan", a.irrigation == "yes")
    if a.json:
        print(json.dumps(rep, indent=2, ensure_ascii=False))
        return
    s, an = rep["soil"], rep["analysis"]
    print(f"\n{rep['location']['name']}  |  {rep['crop']}")
    print(f"Mitti: {s['type']} (clay {s['clay']}%, sand {s['sand']}%, silt {s['silt']}%)")
    print(f"Mineral: {s['mineral']}  |  pH: {s['ph']}  |  CEC: {s['cec_mmol_kg']}")
    print(f"Baarish: pichhle 30 din {an['rain_past30_mm']} mm, agle 16 din {an['rain_next16_mm']} mm")
    print(f"Fasal ko nuksan ka jokhim: {an['risk']['overall']}/100  {an['risk']}\n")
    for line in rep["advice"]:
        print(" -", line)
    print(f"\nData: {rep['sources']}")


if __name__ == "__main__":
    main()
