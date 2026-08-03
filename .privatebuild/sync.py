#!/usr/bin/env python3
"""
ATHLETE OS — Notion → dashboard sync
Lee las 4 bases del Athlete OS en Notion y regenera el bloque
<script id="athlete-data"> del index.html.

Uso local:  NOTION_TOKEN=secret_xxx python .privatebuild/sync.py
El workflow de GitHub lo corre cada sábado AM.
"""
import os, sys, json, re, math
from datetime import datetime, date, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "requests"], check=True)
    import requests

NOTION_TOKEN   = os.environ.get("NOTION_TOKEN", "")
NOTION_VERSION = "2022-06-28"
BASE_DATE      = date(2026, 6, 19)          # día 1 del sistema
REPO_DIR       = Path(__file__).parent.parent
HTML_FILE      = REPO_DIR / "index.html"

# IDs de las bases (no son secretos)
DB = {
    "checkin": "efc6b59783374803bdb9ad223464d321",
    "comidas": "6d9e76b08235403da4b12e4f1bcdd20f",
    "entreno": "4178fed5284641b78a5d862e28b06617",
    "running": "edca04494509421fb0dbdf3d0b2a2f2e",
}

MESES = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"]

# ─── Notion helpers ──────────────────────────────────────────────────────────
def headers():
    return {"Authorization": f"Bearer {NOTION_TOKEN}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json"}

def query_db(db_id):
    rows, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(f"https://api.notion.com/v1/databases/{db_id}/query",
                          headers=headers(), json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        rows.extend(data["results"])
        if not data.get("has_more"):
            break
        cursor = data["next_cursor"]
    return rows

def num(props, name):
    p = props.get(name) or {}
    return p.get("number")

def dt(props, name):
    p = props.get(name) or {}
    d = p.get("date")
    return d["start"][:10] if d and d.get("start") else None

def check(props, name):
    p = props.get(name) or {}
    return bool(p.get("checkbox"))

def sel(props, name):
    p = props.get(name) or {}
    s = p.get("select")
    return s["name"] if s else None

def di(iso):
    return (date.fromisoformat(iso) - BASE_DATE).days

# ─── Extracción ──────────────────────────────────────────────────────────────
def load():
    checkin = [{"f": dt(p["properties"], "Fecha"),
                "peso": num(p["properties"], "Peso Corporal kg"),
                "sueno": num(p["properties"], "Horas de Sueno"),
                "energia": num(p["properties"], "Energia AM"),
                "entreno": check(p["properties"], "Entreno Hecho")}
               for p in query_db(DB["checkin"])]
    comidas = [{"f": dt(p["properties"], "Fecha"),
                "kcal": num(p["properties"], "Total Calorías"),
                "prot": num(p["properties"], "Total Proteína (g)"),
                "carbs": num(p["properties"], "Total Carbs (g)"),
                "grasa": num(p["properties"], "Total Grasa (g)")}
               for p in query_db(DB["comidas"])]
    entreno = [{"f": dt(p["properties"], "Fecha"),
                "tipo": sel(p["properties"], "Tipo"),
                "vol": num(p["properties"], "Volumen Total kg"),
                "emax": num(p["properties"], "Esfuerzo Máx (kg)")}
               for p in query_db(DB["entreno"])]
    running = [{"f": dt(p["properties"], "Fecha"),
                "km": num(p["properties"], "Distancia (km)"),
                "pace": num(p["properties"], "Ritmo (min/km)"),
                "fc": num(p["properties"], "FC Promedio (lpm)")}
               for p in query_db(DB["running"])]
    return checkin, comidas, entreno, running

# ─── Cálculo del bloque de datos ─────────────────────────────────────────────
def avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else 0

def build(checkin, comidas, entreno, running):
    today = date.today()
    span = di(today.isoformat()) + 1

    # ---- peso ----
    peso_by = {}
    for r in checkin:
        if r["f"] and r["peso"] is not None and r["f"] not in peso_by:
            peso_by[r["f"]] = r["peso"]
    weight = sorted([[f, v] for f, v in peso_by.items()])

    # ---- comidas dedup (por fecha, fila de mayor kcal) ----
    best = {}
    for r in comidas:
        if not r["f"] or r["kcal"] is None:
            continue
        if r["f"] not in best or r["kcal"] > best[r["f"]]["kcal"]:
            best[r["f"]] = r
    nutri = sorted([[f, round(r["kcal"]), round(r["prot"] or 0)] for f, r in best.items()])
    macros = sorted([[f, round(r["prot"]), round(r["carbs"]), round(r["grasa"])]
                     for f, r in best.items()
                     if r["prot"] is not None and r["carbs"] is not None and r["grasa"] is not None])

    # ---- fuerza ----
    def maxes(tipo):
        return sorted([[r["f"], round(r["emax"])] for r in entreno
                       if r["tipo"] == tipo and r["f"] and r["emax"] is not None])
    pushMax, pullMax, legsMax = maxes("Push"), maxes("Pull"), maxes("Legs")

    # ---- semanas ----
    nweeks = (di(today.isoformat()) // 7) + 1
    volW = [0.0] * nweeks
    weeks = [{"w": f"S{i+1}", "p": 0, "u": 0, "l": 0} for i in range(nweeks)]
    for r in entreno:
        if not r["f"]:
            continue
        w = di(r["f"]) // 7
        if 0 <= w < nweeks:
            if r["vol"]:
                volW[w] += r["vol"]
            if r["tipo"] == "Push":  weeks[w]["p"] += 1
            elif r["tipo"] == "Pull": weeks[w]["u"] += 1
            elif r["tipo"] == "Legs": weeks[w]["l"] += 1
    volWeek = [round(v / 1000, 1) for v in volW]

    # proteína / energía / km por semana (para pilares)
    protW = [[] for _ in range(nweeks)]
    enerW = [[] for _ in range(nweeks)]
    kmW   = [0.0] * nweeks
    for f, r in best.items():
        w = di(f) // 7
        if 0 <= w < nweeks and r["prot"] is not None:
            protW[w].append(r["prot"])
    for r in checkin:
        if r["f"] and r["energia"] is not None:
            w = di(r["f"]) // 7
            if 0 <= w < nweeks:
                enerW[w].append(r["energia"])
    for r in running:
        if r["f"] and r["km"]:
            w = di(r["f"]) // 7
            if 0 <= w < nweeks:
                kmW[w] += r["km"]

    pil_ent = [round(min(150, volW[i] / 40000 * 100)) for i in range(nweeks)]
    pil_nut = [round(min(150, avg(protW[i]) / 180 * 100)) for i in range(nweeks)]
    pil_ene = [round(min(150, avg(enerW[i]) / 5 * 100)) for i in range(nweeks)]
    pillars = [pil_ent, pil_nut, pil_ene]

    # ---- sueño ----
    sleep = sorted([[r["f"], r["sueno"]] for r in checkin if r["f"] and r["sueno"] is not None])

    # ---- runs (últimas 6) ----
    runs_all = sorted([r for r in running if r["f"] and r["km"] is not None], key=lambda r: r["f"])
    runs = [{"d": f"{r['f'][8:10]}/{r['f'][5:7]}", "k": round(r["km"], 2),
             "pc": round(r["pace"], 2) if r["pace"] else 0,
             "f": round(r["fc"]) if r["fc"] else 0} for r in runs_all[-6:]]

    # ---- hábitos (últimos 7 días) ----
    last7 = [today - timedelta(days=i) for i in range(6, -1, -1)]
    ci_by = {}
    for r in checkin:
        if r["f"]:
            ci_by.setdefault(r["f"], r)
            # preferir la fila con más datos
            cur = ci_by[r["f"]]
            if (r["peso"] is not None) and cur["peso"] is None:
                ci_by[r["f"]] = r
    ent_days = {r["f"] for r in entreno if r["f"]}
    com_days = set(best.keys())
    hab = {"Peso": [], "Sueño": [], "Energía": [], "Entreno": [], "Comida": []}
    for d in last7:
        iso = d.isoformat()
        c = ci_by.get(iso, {})
        hab["Peso"].append(1 if c.get("peso") is not None else 0)
        hab["Sueño"].append(1 if c.get("sueno") is not None else 0)
        hab["Energía"].append(1 if c.get("energia") is not None else 0)
        hab["Entreno"].append(1 if (c.get("entreno") or iso in ent_days) else 0)
        hab["Comida"].append(1 if iso in com_days else 0)
    habDays = [d.day for d in last7]

    # ---- radar ----
    def recent(vals_by_date, days=14):
        cutoff = today - timedelta(days=days)
        return [v for f, v in vals_by_date if date.fromisoformat(f) >= cutoff]
    prot_recent = recent([[f, r["prot"]] for f, r in best.items() if r["prot"] is not None])
    ener_recent = recent([[r["f"], r["energia"]] for r in checkin if r["f"] and r["energia"] is not None])
    sleep_recent = recent([[f, v] for f, v in sleep])
    last_km = kmW[nweeks - 1] if nweeks else 0
    def clamp(x): return round(max(0.1, min(1.0, x)), 2)
    cur_radar = [
        clamp((pullMax[-1][1] if pullMax else 120) / 160),
        clamp((volW[nweeks - 1] if nweeks else 0) / 60000),
        clamp(last_km / 8),
        clamp(avg(prot_recent) / 180),
        clamp(avg(sleep_recent) / 8),
        clamp(avg(ener_recent) / 5),
    ]
    radar = [{"l": l, "c": c, "p": clamp(c * 0.9)} for l, c in
             zip(["Fuerza", "Volumen", "Running", "Nutrición", "Sueño", "Energía"], cur_radar)]

    # ---- KPIs ----
    prot_avg = round(avg([r[2] for r in nutri]))
    kcal_avg = round(avg([r[1] for r in nutri]))
    sleep_avg = round(avg([r[1] for r in sleep]), 1)
    ener_avg = round(avg([r["energia"] for r in checkin]), 1)
    peso_now = weight[-1][1] if weight else 0
    peso_first = weight[0][1] if weight else 0
    peso_delta = round(peso_first - peso_now, 1)
    reg_days = len({r["f"] for r in checkin if r["f"]})
    vol_now = volWeek[-1] if volWeek else 0
    vol_prev = volWeek[-2] if len(volWeek) > 1 else 0
    vol_pct = round((vol_now - vol_prev) / vol_prev * 100) if vol_prev else 0

    kpis = [
        {"lbl": "Peso corporal", "pd": "good", "badge": f"▼ {peso_delta}", "bcls": "up",
         "val": f"{peso_now:.1f}", "unit": " kg", "meta": f"objetivo 95 · faltan {round(peso_now-95,1)}"},
        {"lbl": "Proteína prom", "pd": "danger" if prot_avg < 160 else ("warn" if prot_avg < 180 else "good"),
         "badge": (f"▼ {180-prot_avg}" if prot_avg < 180 else "✓"), "bcls": "down" if prot_avg < 180 else "up",
         "val": f"{prot_avg}", "unit": " g", "meta": "base 180 · ideal 200"},
        {"lbl": "Calorías prom", "pd": "warn", "badge": "déficit" if kcal_avg < 2500 else "ok", "bcls": "warnb",
         "val": f"{kcal_avg}", "unit": "", "meta": "target 2500–2700"},
        {"lbl": "Volumen semanal", "pd": "good", "badge": (f"▲ {vol_pct}%" if vol_pct >= 0 else f"▼ {abs(vol_pct)}%"),
         "bcls": "up" if vol_pct >= 0 else "down", "val": f"{vol_now:.1f}", "unit": "k kg", "meta": "meta ~40k"},
        {"lbl": "Running semanal", "pd": "good" if last_km > 0 else "warn",
         "badge": f"{len([r for r in running if r['f']])} tot", "bcls": "up",
         "val": f"{round(last_km,1)}", "unit": " km", "meta": "meta 8/sem"},
        {"lbl": "Sueño prom", "pd": "warn" if sleep_avg < 8 else "good",
         "badge": (f"▼ {round(8-sleep_avg,1)}h" if sleep_avg < 8 else "✓"), "bcls": "warnb" if sleep_avg < 8 else "up",
         "val": f"{sleep_avg}", "unit": " h", "meta": "target 8h corrido"},
        {"lbl": "Energía AM prom", "pd": "good" if ener_avg >= 4 else "warn",
         "badge": "✓" if ener_avg >= 4 else "▼", "bcls": "up" if ener_avg >= 4 else "warnb",
         "val": f"{ener_avg}", "unit": " /5", "meta": "target ≥4/5"},
        {"lbl": "Registro diario", "pd": "good", "badge": "▲", "bcls": "up",
         "val": f"{round(reg_days/span*100)}", "unit": " %", "meta": f"{reg_days}/{span} días"},
    ]

    rng = f"19 jun → {today.day} {MESES[today.month-1]} {today.year}"
    return {
        "meta": {"base": BASE_DATE.isoformat(), "day": span, "span": span, "range": rng,
                 "lastSync": datetime.now().strftime("%Y-%m-%d %H:%M")},
        "kpis": kpis, "weight": weight, "pushMax": pushMax, "pullMax": pullMax, "legsMax": legsMax,
        "volWeek": volWeek, "nutri": nutri, "macros": macros, "sleep": sleep, "weeks": weeks,
        "pillars": pillars, "pillarWeeks": [f"Sem {i+1}" for i in range(nweeks)],
        "runs": runs, "hab": hab, "habDays": habDays, "radar": radar,
        "hyps": HYPS,
    }

# hipótesis: cualitativas, se editan a mano (no se recalculan por ahora)
HYPS = [
    {"s":"watch","st":"OBSERVANDO · 2 datos","q":"¿Más proteína el día previo = más volumen al día siguiente?","d":"+42 g el día antes se tradujo en <b>+1.715 kg de volumen</b> (8 jul, PR).","a":"Cargar proteína el día ANTES de Push/Pull pesado."},
    {"s":"strong","st":"CONFIRMADO · 8+ datos","q":"¿Sueño fragmentado = energía baja al día siguiente?","d":"Sin excepción: noche partida → energía ≤3.","a":"Priorizar sueño CORRIDO sobre horas totales."},
    {"s":"watch","st":"OBSERVANDO","q":"¿Peso estancado con fuerza subiendo = músculo?","d":"Peso plano con PRs subiendo huele a <b>recomposición</b>, sin confirmar.","a":"Medir cuello/cintura/cadera (Navy) 1 vez al mes."},
    {"s":"watch","st":"OBSERVANDO","q":"¿Los días <160 g son los sin proteína animal al mediodía?","d":"Desayuno sólido y almuerzo con proteína animal <b>anclan</b> el día.","a":"Proteína animal visible al almuerzo = piso 180 g."},
    {"s":"pending","st":"SIN DATOS SUFICIENTES","q":"¿El déficit sostenido frena energía/recuperación?","d":"Si la energía no sube con buen sueño, el techo puede ser el déficit.","a":"Registrar energía PM para cruzarla vs calorías."},
    {"s":"watch","st":"OBSERVANDO","q":"¿Registrar en tiempo real sube la calidad del dato?","d":"Los días cargados en bloque quedan <b>'estimados'</b> y ensucian los macros.","a":"Registrar la comida en el momento, aunque sea aproximada."},
]

def inject(data):
    html = HTML_FILE.read_text(encoding="utf-8")
    block = '<script id="athlete-data" type="application/json">\n' + json.dumps(data, ensure_ascii=False, indent=0) + '\n</script>'
    new = re.sub(r'<script id="athlete-data" type="application/json">.*?</script>',
                 block.replace('\\', '\\\\'), html, flags=re.DOTALL)
    HTML_FILE.write_text(new, encoding="utf-8")

def main():
    if not NOTION_TOKEN:
        print("❌ Falta NOTION_TOKEN. Configúralo como secret del repo (o export local).")
        sys.exit(1)
    print("📥 Leyendo Notion…")
    checkin, comidas, entreno, running = load()
    print(f"   check-in:{len(checkin)}  comidas:{len(comidas)}  entreno:{len(entreno)}  running:{len(running)}")
    data = build(checkin, comidas, entreno, running)
    inject(data)
    print(f"✅ index.html actualizado · día {data['meta']['day']} · {data['meta']['lastSync']}")

if __name__ == "__main__":
    main()
