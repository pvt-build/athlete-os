#!/usr/bin/env python3
"""
ATHLETE OS — Notion → dashboard sync
Lee las 4 bases del Athlete OS en Notion y regenera el bloque
<script id="athlete-data"> del index.html.

Uso local:  NOTION_TOKEN=secret_xxx python .privatebuild/sync.py
El workflow de GitHub lo corre cada sábado AM.
"""
import os, sys, json, re
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import requests
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "requests"], check=True)
    import requests

NOTION_TOKEN   = os.environ.get("NOTION_TOKEN", "")
NOTION_VERSION = "2022-06-28"
TZ             = ZoneInfo("America/Santiago")  # el sistema vive en hora Chile
BASE_DATE      = date(2026, 6, 19)          # día 1 del sistema
REPO_DIR       = Path(__file__).parent.parent
HTML_FILE      = REPO_DIR / "index.html"

DB = {
    "checkin": "efc6b59783374803bdb9ad223464d321",
    "comidas": "6d9e76b08235403da4b12e4f1bcdd20f",
    "entreno": "4178fed5284641b78a5d862e28b06617",
    "running": "edca04494509421fb0dbdf3d0b2a2f2e",
}
MESES = ["ene","feb","mar","abr","may","jun","jul","ago","sep","oct","nov","dic"]
SUPP_CAT   = {"Creatina":"Fuerza","Omega 3":"Recuperación","Ereboost":"Testosterona",
              "Energy Electrolitos":"Fatiga","Magnesio":"Sueño","QNT Proteina":"Proteína",
              "Melena de Leon":"Foco","Ashwagandha":"Cortisol","Adaptogenos":"Cortisol",
              "Eunoe Noche":"Sueño","Multivitaminico":"Recuperación"}
SUPP_LABEL = {"Energy Electrolitos":"Energy + Elec.","QNT Proteina":"QNT Isolate",
              "Eunoe Noche":"Eunoé","Melena de Leon":"Melena","Adaptogenos":"Adaptógenos"}
CAT_ORDER  = ["Fuerza","Recuperación","Testosterona","Fatiga","Sueño","Proteína","Foco","Cortisol"]

# ─── Notion helpers ──────────────────────────────────────────────────────────
def headers():
    return {"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json"}

def query_db(db_id):
    rows, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor: body["start_cursor"] = cursor
        r = requests.post(f"https://api.notion.com/v1/databases/{db_id}/query",
                          headers=headers(), json=body, timeout=30)
        r.raise_for_status()
        data = r.json()
        rows.extend(data["results"])
        if not data.get("has_more"): break
        cursor = data["next_cursor"]
    return rows

def num(p, n):   x = (p.get(n) or {}); return x.get("number")
def dt(p, n):    x = (p.get(n) or {}).get("date"); return x["start"][:10] if x and x.get("start") else None
def chk(p, n):   return bool((p.get(n) or {}).get("checkbox"))
def sel(p, n):   x = (p.get(n) or {}).get("select"); return x["name"] if x else None
def multi(p, n): return [o["name"] for o in ((p.get(n) or {}).get("multi_select") or [])]
def txt(p, n):
    x = (p.get(n) or {})
    return "".join(o.get("plain_text","") for o in (x.get("title") or x.get("rich_text") or []))
def now_cl():    return datetime.now(TZ)
def di(iso):     return (date.fromisoformat(iso) - BASE_DATE).days
def avg(xs):     xs = [x for x in xs if x is not None]; return sum(xs)/len(xs) if xs else 0

# ─── Normalización ───────────────────────────────────────────────────────────
# Las bases acumulan tres cosas que hay que limpiar antes de calcular:
#   1. filas marcadas a mano para descartar,
#   2. varias filas por día (el registro se cargó dos veces en agosto),
#   3. dos formatos distintos de Comidas conviviendo (ver daily_meals).
DROP_MARK = re.compile(r"^\s*\[(DUPLICADO|DESCARTADO|FUSIONADO)")

def kcal_of(r):
    """kcal de la fila; si no está cargada, se deriva de los macros (Atwater 4/4/9)."""
    if r["kcal"] is not None: return r["kcal"]
    if r["prot"] is None and r["carbs"] is None and r["grasa"] is None: return None
    return 4*(r["prot"] or 0) + 4*(r["carbs"] or 0) + 9*(r["grasa"] or 0)

def daily_meals(co):
    """Un registro de nutrición por día.

    Comidas tiene dos formatos encima: hasta el 28-jul una fila por día con los
    totales (titulada con la fecha ISO), y desde el 29-jul una fila por comida.
    Con totales se toma la fila de totales; con filas por comida se suman."""
    by = {}
    for r in co:
        if not r["f"] or DROP_MARK.match(r.get("t") or ""): continue
        by.setdefault(r["f"], []).append(r)
    out = {}
    for f, rows in by.items():
        totales = [r for r in rows if (r.get("t") or "").strip() == f]
        if totales:
            r = max(totales, key=lambda r: r["kcal"] or 0)
            k = kcal_of(r)
            if k is None: continue
            out[f] = {"kcal": k, "prot": r["prot"], "carbs": r["carbs"], "grasa": r["grasa"]}
        else:
            ks = [k for k in (kcal_of(r) for r in rows) if k is not None]
            if not ks: continue
            def suma(n):
                vs = [r[n] for r in rows if r[n] is not None]
                return sum(vs) if vs else None
            out[f] = {"kcal": sum(ks), "prot": suma("prot"),
                      "carbs": suma("carbs"), "grasa": suma("grasa")}
    return out

def dedup_checkin(ci):
    """Un check-in por día. Los días cargados dos veces se fusionan campo a campo
    (gana el primer valor no vacío) en vez de contarse dos veces en los promedios."""
    out = {}
    for r in ci:
        if not r["f"]: continue
        cur = out.setdefault(r["f"], {"f": r["f"], "peso": None, "sueno": None,
                                      "energia": None, "entreno": False, "supp": []})
        peso = r["peso"] if r["peso"] else None      # 0 kg = "no me pesé", no un peso
        for k, v in (("peso", peso), ("sueno", r["sueno"]), ("energia", r["energia"])):
            if cur[k] is None: cur[k] = v
        cur["entreno"] = cur["entreno"] or r["entreno"]
        for s in r.get("supp", []):
            if s not in cur["supp"]: cur["supp"].append(s)
    return sorted(out.values(), key=lambda r: r["f"])

def dedup_entreno(en):
    """Una sesión de fuerza por día y tipo.

    Agosto quedó cargado dos veces y hay días con el mismo Push/Pull/Legs repetido
    con volúmenes distintos (13-ago: 17.773 y 19.965). Son la misma sesión estimada
    dos veces, así que se conserva la de mayor volumen. Hybrid/Otro no se colapsan:
    ahí sí puede haber varias sesiones distintas el mismo día."""
    mejor, out = {}, []
    for r in en:
        if r["tipo"] not in ("Push", "Pull", "Legs"):
            out.append(r); continue
        k = (r["f"], r["tipo"])
        if k not in mejor or (r["vol"] or 0) > (mejor[k]["vol"] or 0): mejor[k] = r
    return out + list(mejor.values())

# ─── Carga ───────────────────────────────────────────────────────────────────
def load():
    ci = [{"f": dt(p["properties"],"Fecha"), "peso": num(p["properties"],"Peso Corporal kg"),
           "sueno": num(p["properties"],"Horas de Sueno"), "energia": num(p["properties"],"Energia AM"),
           "entreno": chk(p["properties"],"Entreno Hecho"), "supp": multi(p["properties"],"Suplementos Tomados")}
          for p in query_db(DB["checkin"])]
    co = [{"f": dt(p["properties"],"Fecha"), "t": txt(p["properties"],"Comida"),
           "kcal": num(p["properties"],"Total Calorías"),
           "prot": num(p["properties"],"Total Proteína (g)"), "carbs": num(p["properties"],"Total Carbs (g)"),
           "grasa": num(p["properties"],"Total Grasa (g)")} for p in query_db(DB["comidas"])]
    en = [{"f": dt(p["properties"],"Fecha"), "tipo": sel(p["properties"],"Tipo"),
           "vol": num(p["properties"],"Volumen Total kg"), "emax": num(p["properties"],"Esfuerzo Máx (kg)")}
          for p in query_db(DB["entreno"])]
    ru = [{"f": dt(p["properties"],"Fecha"), "km": num(p["properties"],"Distancia (km)"),
           "pace": num(p["properties"],"Ritmo (min/km)"), "fc": num(p["properties"],"FC Promedio (lpm)")}
          for p in query_db(DB["running"])]
    # Deriva kcal cuando falta pero hay macros (4·P + 4·C + 9·G): un día con comida no se cae del dashboard por un campo kcal vacío
    for r in co:
        if r["kcal"] is None and r["prot"] is not None and r["carbs"] is not None and r["grasa"] is not None:
            r["kcal"] = round(4*r["prot"] + 4*r["carbs"] + 9*r["grasa"])
    return ci, co, en, ru

# ─── Cálculo ─────────────────────────────────────────────────────────────────
def build(ci, co, en, ru):
    today = now_cl().date()
    span = di(today.isoformat()) + 1
    alldates = [r["f"] for r in (ci + co + en + ru) if r.get("f")]
    last_iso = max(alldates) if alldates else today.isoformat()
    nweeks = (di(last_iso) // 7) + 1  # hasta el último dato real, no la semana en curso vacía

    ci = dedup_checkin(ci)
    en = dedup_entreno(en)

    # peso
    weight = sorted([[r["f"], r["peso"]] for r in ci if r["peso"] is not None])

    # nutrición: un registro por día (ver daily_meals)
    best = daily_meals(co)
    nutri  = sorted([[f, round(r["kcal"]), round(r["prot"] or 0)] for f, r in best.items()])
    macros = sorted([[f, round(r["prot"]), round(r["carbs"]), round(r["grasa"])] for f, r in best.items()
                     if r["prot"] is not None and r["carbs"] is not None and r["grasa"] is not None])

    # fuerza
    mx = lambda t: sorted([[r["f"], round(r["emax"])] for r in en if r["tipo"]==t and r["f"] and r["emax"] is not None])
    pushMax, pullMax, legsMax = mx("Push"), mx("Pull"), mx("Legs")

    # semanas: volumen + sesiones + km
    volW = [0.0]*nweeks; weeks = [{"w":f"S{i+1}","p":0,"u":0,"l":0} for i in range(nweeks)]
    protW = [[] for _ in range(nweeks)]; enerW = [[] for _ in range(nweeks)]; kmW = [0.0]*nweeks
    for r in en:
        if not r["f"]: continue
        w = di(r["f"])//7
        if 0 <= w < nweeks:
            if r["vol"]: volW[w] += r["vol"]
            if r["tipo"]=="Push": weeks[w]["p"]+=1
            elif r["tipo"]=="Pull": weeks[w]["u"]+=1
            elif r["tipo"]=="Legs": weeks[w]["l"]+=1
    for f, r in best.items():
        w = di(f)//7
        if 0 <= w < nweeks and r["prot"] is not None: protW[w].append(r["prot"])
    for r in ci:
        if r["f"] and r["energia"] is not None:
            w = di(r["f"])//7
            if 0 <= w < nweeks: enerW[w].append(r["energia"])
    for r in ru:
        if r["f"] and r["km"]:
            w = di(r["f"])//7
            if 0 <= w < nweeks:
                kmW[w] += r["km"]
    week_runs = len([r for r in ru if r["f"] and r["km"] and 0 <= (today - date.fromisoformat(r["f"])).days < 7])
    volWeek = [round(v/1000, 1) for v in volW]
    pillars = [[round(min(150, volW[i]/40000*100)) for i in range(nweeks)],
               [round(min(150, avg(protW[i])/180*100)) for i in range(nweeks)],
               [round(min(150, avg(enerW[i])/5*100)) for i in range(nweeks)]]

    # sueño + energía diaria
    sleep = sorted([[r["f"], r["sueno"]] for r in ci if r["f"] and r["sueno"] is not None])
    ener_by = {}
    for r in ci:
        if r["f"] and r["energia"] is not None and r["f"] not in ener_by: ener_by[r["f"]] = r["energia"]
    energia = sorted([[f, v] for f, v in ener_by.items()])

    # runs (últimas 6)
    ra = sorted([r for r in ru if r["f"] and r["km"] is not None], key=lambda r: r["f"])
    runs = [{"d": f"{r['f'][8:10]}/{r['f'][5:7]}", "k": round(r["km"],2),
             "pc": round(r["pace"],2) if r["pace"] else 0, "f": round(r["fc"]) if r["fc"] else 0} for r in ra[-6:]]

    # patrón por día de la semana
    ent_days = {r["f"] for r in en if r["f"] and r["tipo"] in ("Push","Pull","Legs")}
    com_days = set(best.keys())
    den=[0]*7; nc=[0]*7; ne=[0]*7; d = BASE_DATE
    while d <= today:
        wd = d.weekday(); den[wd]+=1; iso = d.isoformat()
        if iso in com_days: nc[wd]+=1
        if iso in ent_days: ne[wd]+=1
        d += timedelta(days=1)
    weekday = {"dias":["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"],
               "comida":[round(nc[i]/den[i]*100) if den[i] else 0 for i in range(7)],
               "entreno":[round(ne[i]/den[i]*100) if den[i] else 0 for i in range(7)]}

    # suplementos
    scount = {}
    for r in ci:
        for s in r.get("supp", []): scount[s] = scount.get(s,0)+1
    supp = sorted([{"name":SUPP_LABEL.get(k,k),"cat":c,"days":scount.get(k,0)} for k,c in SUPP_CAT.items()],
                  key=lambda s:-s["days"])
    cat_days = {s["cat"]:s["days"] for s in supp}
    suppRadar = [{"l":c,"v":round(min(1.0, cat_days.get(c,0)/6),2)} for c in CAT_ORDER]

    # radar atlético
    prot_recent = [r["prot"] for f,r in best.items() if r["prot"] is not None and date.fromisoformat(f) >= today-timedelta(days=14)]
    ener_recent = [r["energia"] for r in ci if r["f"] and r["energia"] is not None and date.fromisoformat(r["f"]) >= today-timedelta(days=14)]
    sleep_recent = [v for f,v in sleep if date.fromisoformat(f) >= today-timedelta(days=14)]
    clamp = lambda x: round(max(0.1, min(1.0, x)), 2)
    lastmax = lambda a: a[-1][1] if a else 0
    kmrec = (kmW[nweeks-1] if nweeks else 0) + (kmW[nweeks-2] if nweeks > 1 else 0)
    cr = [clamp(max(lastmax(pushMax), lastmax(pullMax), lastmax(legsMax))/160),
          clamp((volW[nweeks-1] if nweeks else 0)/60000),
          clamp(lastmax(pushMax)/160), clamp(lastmax(pullMax)/160), clamp(lastmax(legsMax)/160),
          clamp(kmrec/16)]  # radar 100% atlético
    radar = [{"l":l,"c":c,"p":clamp(c*0.9)} for l,c in zip(["Fuerza","Volumen","Push","Pull","Legs","Running"], cr)]

    # KPIs
    prot_avg = round(avg([r[2] for r in nutri])); kcal_avg = round(avg([r[1] for r in nutri]))
    sleep_avg = round(avg([r[1] for r in sleep]),1); ener_avg = round(avg([r["energia"] for r in ci]),1)
    peso_now = weight[-1][1] if weight else 0; peso_first = weight[0][1] if weight else 0
    reg = len({r["f"] for r in ci if r["f"]}); vol_now = volWeek[-1] if volWeek else 0
    vol_prev = volWeek[-2] if len(volWeek)>1 else 0
    vol_pct = round((vol_now-vol_prev)/vol_prev*100) if vol_prev else 0
    kpis = [
        {"lbl":"Peso corporal","pd":"good","badge":f"▼ {round(peso_first-peso_now,1)}","bcls":"up",
         "val":f"{peso_now:.1f}","unit":" kg","meta":f"objetivo 95 · faltan {round(peso_now-95,1)}"},
        {"lbl":"Proteína prom","pd":"danger" if prot_avg<160 else ("warn" if prot_avg<180 else "good"),
         "badge":(f"▼ {180-prot_avg}" if prot_avg<180 else "✓"),"bcls":"down" if prot_avg<180 else "up",
         "val":f"{prot_avg}","unit":" g","meta":f"meta 180 · {prot_avg-180}"},
        {"lbl":"Calorías prom","pd":"warn" if kcal_avg>2800 else "good","badge":"sobre tope" if kcal_avg>2800 else "ok","bcls":"warnb",
         "val":f"{kcal_avg}","unit":"","meta":"meta ≤2.800 (tope)"},
        {"lbl":"Volumen semanal","pd":"good","badge":(f"▲ {vol_pct}%" if vol_pct>=0 else f"▼ {abs(vol_pct)}%"),
         "bcls":"up" if vol_pct>=0 else "down","val":f"{vol_now:.1f}","unit":"k kg","meta":"meta ~40k"},
        {"lbl":"Running semanal","pd":"good" if week_runs>=2 else "warn",
         "badge":"✓" if week_runs>=2 else f"▼ falta {2-week_runs}","bcls":"up" if week_runs>=2 else "warnb",
         "val":f"{week_runs}","unit":" /2","meta":"sesiones · meta 2/sem"},
        {"lbl":"Sueño prom","pd":"warn" if sleep_avg<8 else "good",
         "badge":(f"▼ {round(8-sleep_avg,1)}h" if sleep_avg<8 else "✓"),"bcls":"warnb" if sleep_avg<8 else "up",
         "val":f"{sleep_avg}","unit":" h","meta":"target 8h corrido"},
        {"lbl":"Energía AM prom","pd":"good" if ener_avg>=4 else "warn",
         "badge":"✓" if ener_avg>=4 else "▼","bcls":"up" if ener_avg>=4 else "warnb",
         "val":f"{ener_avg}","unit":" /5","meta":"target ≥4/5"},
        {"lbl":"Registro diario","pd":"good","badge":"▲","bcls":"up",
         "val":f"{round(reg/span*100)}","unit":" %","meta":f"{reg}/{span} días"},
    ]

    rng = f"19 jun → {today.day} {MESES[today.month-1]} {today.year}"
    return {
        "meta":{"base":BASE_DATE.isoformat(),"day":span,"span":span,"range":rng,
                "lastSync":now_cl().strftime("%Y-%m-%d %H:%M")},
        "kpis":kpis,"weight":weight,"pushMax":pushMax,"pullMax":pullMax,"legsMax":legsMax,
        "volWeek":volWeek,"nutri":nutri,"macros":macros,"sleep":sleep,"energia":energia,"weeks":weeks,
        "pillars":pillars,"pillarWeeks":[f"Sem {i+1}" for i in range(nweeks)],
        "runs":runs,"weekday":weekday,"supp":supp,"suppRadar":suppRadar,"radar":radar,"hyps":HYPS,
    }

HYPS = [
    {"s":"watch","st":"OBSERVANDO · 2 datos","q":"¿Más proteína el día previo = más volumen al día siguiente?","d":"+42 g el día antes se tradujo en <b>+1.715 kg de volumen</b> (8 jul, PR).","a":"Cargar proteína el día ANTES de Push/Pull pesado."},
    {"s":"strong","st":"CONFIRMADO · 8+ datos","q":"¿Sueño fragmentado = energía baja al día siguiente?","d":"Sin excepción: noche partida → energía ≤3.","a":"Priorizar sueño CORRIDO sobre horas totales."},
    {"s":"strong","st":"CONFIRMADO · datos propios","q":"¿El domingo es el día que rompe el sistema?","d":"Comida al <b>67%</b> y entreno al <b>17%</b> los domingos.","a":"Ritual de domingo: registrar comida + active rest fijo."},
    {"s":"watch","st":"OBSERVANDO","q":"¿Peso estancado con fuerza subiendo = músculo?","d":"Peso plano con PRs subiendo huele a <b>recomposición</b>, sin confirmar.","a":"Medir cuello/cintura/cadera (Navy) 1 vez al mes."},
    {"s":"watch","st":"OBSERVANDO","q":"¿Los días <160 g son los sin proteína animal al mediodía?","d":"Desayuno sólido y almuerzo con proteína animal <b>anclan</b> el día.","a":"Proteína animal visible al almuerzo = piso 180 g."},
    {"s":"pending","st":"SIN DATOS SUFICIENTES","q":"¿El déficit sostenido frena energía/recuperación?","d":"Si la energía no sube con buen sueño, el techo puede ser el déficit.","a":"Registrar energía PM para cruzarla vs calorías."},
]

def inject(data):
    html = HTML_FILE.read_text(encoding="utf-8")
    block = '<script id="athlete-data" type="application/json">\n' + json.dumps(data, ensure_ascii=False) + '\n</script>'
    new = re.sub(r'<script id="athlete-data" type="application/json">.*?</script>',
                 lambda m: block, html, flags=re.DOTALL)
    HTML_FILE.write_text(new, encoding="utf-8")

def main():
    if not NOTION_TOKEN:
        print("❌ Falta NOTION_TOKEN."); sys.exit(1)
    print("📥 Leyendo Notion…")
    ci, co, en, ru = load()
    print(f"   check-in:{len(ci)}  comidas:{len(co)}  entreno:{len(en)}  running:{len(ru)}")
    inject(build(ci, co, en, ru))
    print("✅ index.html actualizado.")

if __name__ == "__main__":
    main()
