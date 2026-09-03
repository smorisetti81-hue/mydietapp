import streamlit as st
import google.generativeai as genai
import json
import pandas as pd
import urllib.parse
import base64
import requests
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
from PIL import Image
from collections import defaultdict
import uuid

# ============================================================
# MyDietApp v21
# Health-first release:
# - real Google Fit data diagnostics
# - robust aggregation for cumulative vs point data
# - automatic BMR / calorie target calculation
# - Home separates intake, target and observed expenditure
# - Python owns local food/calorie state
# - source-aware Google Fit diagnostics
# - prefer Google Fit derived/reconciled streams instead of summing every source
# ============================================================
st.set_page_config(page_title="MyDietApp", page_icon="💪", layout="wide", initial_sidebar_state="collapsed")
genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

FIT_BASE = "https://www.googleapis.com/fitness/v1/users/me"
FIT_SCOPES = (
    "https://www.googleapis.com/auth/fitness.activity.read "
    "https://www.googleapis.com/auth/fitness.body.read "
    "https://www.googleapis.com/auth/fitness.nutrition.read"
)
ROME = ZoneInfo("Europe/Rome")

def today(): return datetime.now(ROME).date().isoformat()

st.markdown("""
<style>
#MainMenu, footer, header {visibility:hidden;}
.block-container {max-width:1050px;padding-top:1rem;padding-bottom:5rem;}
.card {border:1px solid rgba(128,128,128,.22);border-radius:18px;padding:18px;margin:8px 0;background:rgba(128,128,128,.06);}
.hero {border-radius:24px;padding:22px;background:linear-gradient(135deg,rgba(255,75,75,.18),rgba(128,128,128,.06));border:1px solid rgba(255,75,75,.25);}
.muted {color:#888;font-size:.88rem}.big {font-size:2.2rem;font-weight:800}.ok {color:#22a06b;font-weight:700}.bad {color:#d64545;font-weight:700}
.small {font-size:.82rem;color:#888;}
</style>
""", unsafe_allow_html=True)

# ---------------- Native Health Connect bridge ----------------
HEALTH_BRIDGE_PARAM = "mydiet_health"

def _decode_health_bridge_payload(raw):
    """Decode a compact URL-safe Health Connect snapshot from the Android bridge."""
    if not raw:
        return None
    try:
        pad = "=" * (-len(raw) % 4)
        data = base64.urlsafe_b64decode((raw + pad).encode("ascii"))
        obj = json.loads(data.decode("utf-8"))
        if obj.get("schema") != "mydietapp.health.v1":
            return None
        return obj
    except Exception:
        return None

def _ingest_native_health_bridge():
    raw = st.query_params.get(HEALTH_BRIDGE_PARAM)
    if not raw:
        return False
    payload = _decode_health_bridge_payload(raw)
    if not payload:
        return False
    # Avoid rewriting state on every Streamlit rerun. The payload itself remains
    # in the URL so a browser refresh can restore the latest bridge snapshot.
    fingerprint = raw[:32]
    if st.session_state.get("health_bridge_fingerprint") == fingerprint:
        return False
    metrics = payload.get("metrics", {})
    health = {
        "provider": {
            "key": "health_connect_native",
            "name": "Health Connect (Android nativo)",
            "status": "active",
            "schema": payload.get("schema"),
            "received_at": datetime.now(ROME).isoformat(),
            "bridge_version": payload.get("bridge_version"),
        },
        "date": payload.get("date") or today(),
        "steps_today": metrics.get("steps"),
        "calories_today": metrics.get("total_calories"),
        "active_calories_today": metrics.get("active_calories"),
        "distance_today": metrics.get("distance_km"),
        "weight": metrics.get("weight_kg"),
        "body_fat": metrics.get("body_fat_percent"),
        "lean_mass": metrics.get("lean_mass_kg"),
        "bmr": metrics.get("bmr_kcal_per_day"),
        "workouts_today": metrics.get("workouts"),
        "workout_details_today": metrics.get("workout_details", []),
        "heart_rate_avg": metrics.get("heart_rate_avg"),
        "heart_rate_min": metrics.get("heart_rate_min"),
        "heart_rate_max": metrics.get("heart_rate_max"),
        "heart_rate_samples": metrics.get("heart_rate_samples"),
        "sleep_minutes": metrics.get("sleep_minutes"),
        "steps_source_verified": bool(payload.get("trust", {}).get("steps", False)),
        "calories_source_verified": bool(payload.get("trust", {}).get("total_calories", False)),
        "native_health_snapshot": True,
        "native_health_payload": payload,
    }
    # Keep the history contract simple for the first native bridge release.
    hist = {}
    for key, value in (("steps", metrics.get("steps")), ("calories", metrics.get("total_calories")),
                       ("weight", metrics.get("weight_kg")), ("body_fat", metrics.get("body_fat_percent")),
                       ("distance", metrics.get("distance_km"))):
        if value is not None:
            hist[key] = [{"date": health["date"], "value": value}]
    st.session_state.health = health
    st.session_state.health_history = hist
    st.session_state.diagnostics = {
        "native_bridge": {
            "status": "available",
            "type": "Health Connect native snapshot",
            "bridge_version": payload.get("bridge_version"),
            "received_at": health["provider"]["received_at"],
        }
    }
    st.session_state.last_sync = datetime.now(ROME).strftime("%d/%m/%Y %H:%M")
    st.session_state.health_bridge_fingerprint = fingerprint
    return True

_ingest_native_health_bridge()

# ---------------- State ----------------
def sid(): return uuid.uuid4().hex[:10]

def init_plan():
    def ing(n,q,u,k): return {"id":sid(),"name":n,"qty":q,"unit":u,"kcal":k}
    return {"Lunedì": {
        "☕ Colazione":{"name":"Yogurt, avena e frutta","ingredients":[ing("Yogurt greco",250,"g",180),ing("Avena",50,"g",190),ing("Miele",10,"g",30)]},
        "🍎 Spuntino":{"name":"Mela e mandorle","ingredients":[ing("Mela",180,"g",95),ing("Mandorle",15,"g",90)]},
        "🍽️ Pranzo":{"name":"Riso, pollo e verdure","ingredients":[ing("Riso basmati",90,"g",320),ing("Petto di pollo",180,"g",300),ing("Zucchine",250,"g",45),ing("Olio EVO",10,"g",90)]},
        "🌙 Cena":{"name":"Uova, pane e verdure","ingredients":[ing("Uova",2,"pz",150),ing("Pane integrale",70,"g",175),ing("Insalata mista",250,"g",50),ing("Olio EVO",10,"g",90)]}
    }}

_defaults = {
    "page":"Home", "meal_plan":init_plan(), "overrides":{}, "eaten":{}, "manual_foods":[],
    "health":{}, "health_history":{}, "diagnostics":{}, "last_sync":None
}
for k,v in _defaults.items(): st.session_state.setdefault(k,v)
for k,v in {
    "name":"Stefano", "weight":135.0, "height":180.0, "age":40, "sex":"male",
    "activity_level":"moderata", "deficit":500
}.items(): st.session_state.setdefault("p_"+k,v)


def meals():
    for day, ms in st.session_state.meal_plan.items():
        for name, meal in ms.items(): yield day,name,meal

def active_items(meal):
    out=[]
    for item in meal.get("ingredients",[]):
        ov=st.session_state.overrides.get(item["id"],{})
        if ov.get("removed"): continue
        x=dict(item); x["qty"]=float(item.get("qty",0))*float(ov.get("multiplier",1)); out.append(x)
    return out

def eaten_kcal():
    lookup={i["id"]:i for _,_,m in meals() for i in m.get("ingredients",[])}
    total=0
    for iid,v in st.session_state.eaten.items():
        if v and iid in lookup:
            item=lookup[iid]
            mult=float(st.session_state.overrides.get(iid,{}).get("multiplier",1))
            if not st.session_state.overrides.get(iid,{}).get("removed"):
                total += float(item["kcal"])*mult
    total += sum(float(x["kcal"]) for x in st.session_state.manual_foods if x["date"]==today())
    return round(total)

def grocery():
    d=defaultdict(lambda:[0,"",""])
    for _,_,m in meals():
        for i in active_items(m):
            key=(i["name"].strip().lower(),i["unit"]); d[key][0]+=float(i["qty"]); d[key][1]=i["unit"]; d[key][2]=i["name"]
    return sorted(d.values(),key=lambda x:x[2].lower())

# ---------------- Energy model ----------------
def bmr_mifflin(weight, height, age, sex):
    # Mifflin-St Jeor. This is an estimate, not a medical measurement.
    value=10*weight + 6.25*height - 5*age + (5 if sex=="male" else -161)
    return round(value)

ACTIVITY_FACTORS={"sedentaria":1.20,"leggera":1.375,"moderata":1.55,"alta":1.725}

def energy_profile():
    p={k:st.session_state["p_"+k] for k in ["weight","height","age","sex","activity_level","deficit"]}
    bmr=bmr_mifflin(float(p["weight"]),float(p["height"]),int(p["age"]),p["sex"])
    maintenance=round(bmr*ACTIVITY_FACTORS[p["activity_level"]])
    target=max(1200, maintenance-int(p["deficit"]))
    return {"bmr_est":bmr,"maintenance_est":maintenance,"target":target,"deficit":int(p["deficit"]),"factor":ACTIVITY_FACTORS[p["activity_level"]]}

def activity_summary():
    """Summarize activity already contained in the observed Health total.

    Active calories and workouts are descriptive components of the observed day;
    they must not be added again to total_calories because that would double count.
    """
    h=st.session_state.get("health",{})
    details=h.get("workout_details_today") or []
    normalized=[]
    for w in details:
        if not isinstance(w,dict):
            continue
        try:
            normalized.append({
                "name":str(w.get("name") or "Attività"),
                "duration_minutes":round(float(w.get("duration_minutes") or 0)),
                "start":str(w.get("start") or ""),
                "end":str(w.get("end") or ""),
            })
        except Exception:
            continue
    return {
        "steps":int(float(h.get("steps_today") or 0)),
        "active_calories":round(float(h.get("active_calories_today") or 0)),
        "distance_km":float(h.get("distance_today") or 0),
        "workouts":int(float(h.get("workouts_today") or len(normalized) or 0)),
        "details":normalized,
    }

def balance():
    h=st.session_state.health
    e=energy_profile()
    eaten=eaten_kcal()
    observed=float(h.get("calories_today") or 0) if h.get("calories_source_verified") else 0.0

    # Health total calories are cumulative from midnight to now. Do not subtract
    # the deficit from the partial-day value directly: that would make the food
    # budget artificially tiny in the afternoon. Instead, project the remaining
    # resting expenditure (BMR) to midnight, while keeping the observed calories
    # already recorded by Samsung Health. This is a transparent estimate and does
    # not invent future workouts.
    bmr_health=float(h.get("bmr")) if h.get("bmr") is not None else 0.0
    bmr_for_projection=bmr_health if bmr_health > 0 else float(e["bmr_est"])
    now=datetime.now(ROME)
    midnight=(now+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
    day_start=now.replace(hour=0,minute=0,second=0,microsecond=0)
    elapsed=max(0.0,(now-day_start).total_seconds())
    remaining_seconds=max(0.0,(midnight-now).total_seconds())
    remaining_rest=round(bmr_for_projection*(remaining_seconds/86400.0))
    projected_burn=round(observed+remaining_rest) if observed > 0 else 0
    live_target=round(max(1200, projected_burn-e["deficit"])) if projected_burn > 0 else e["target"]
    active_observed=max(0,round(observed-bmr_for_projection*(elapsed/86400.0))) if observed > 0 else 0
    return {
        "target":e["target"], "live_target":live_target, "eaten":eaten,
        "remaining":live_target-eaten, "observed_burn":round(observed),
        "projected_burn":projected_burn, "remaining_rest":remaining_rest,
        "active_observed":active_observed,
        "bmr_est":e["bmr_est"], "bmr_health":round(bmr_health) if bmr_health > 0 else None,
        "maintenance":e["maintenance_est"], "deficit":e["deficit"],
        "using_observed":observed > 0
    }

def effective_bmr():
    # Google Fit legacy BMR is not trusted for production energy calculations.
    # Until Health Connect/native Android is active, use the profile estimate.
    return energy_profile()["bmr_est"]

def current_day_name():
    return ["Lunedì","Martedì","Mercoledì","Giovedì","Venerdì","Sabato","Domenica"][datetime.now(ROME).weekday()]

# ---------------- Health provider architecture ----------------
# The application consumes a normalized HealthProvider contract. The current
# provider is Google Fit legacy for compatibility/debug; production will use
# a native Android Health Connect bridge.
HEALTH_METRICS = (
    "steps", "total_calories", "active_calories", "distance",
    "weight", "body_fat", "lean_mass", "workouts", "heart_rate", "sleep"
)

class HealthProvider:
    key = "base"
    name = "Health provider"
    status = "unavailable"

    def sync(self, days=14):
        raise NotImplementedError

    def info(self):
        return {"key": self.key, "name": self.name, "status": self.status}

class GoogleFitProvider(HealthProvider):
    key = "google_fit_legacy"
    name = "Google Fit (legacy REST) · diagnostica"
    status = "legacy"

    def sync(self, days=14):
        return sync_google_fit_health(days)

class HealthConnectProvider(HealthProvider):
    key = "health_connect_native"
    name = "Health Connect (Android nativo)"
    status = "active"

    def sync(self, days=14):
        h = st.session_state.get("health", {})
        if not h.get("native_health_snapshot"):
            raise RuntimeError("Nessun snapshot Health Connect ricevuto dal bridge Android.")
        return h, st.session_state.get("health_history", {}), st.session_state.get("diagnostics", {})

def get_health_provider():
    # Native Health Connect snapshot wins whenever the Android bridge has pushed
    # data into this Streamlit session. Google Fit remains diagnostic fallback.
    if st.session_state.get("health", {}).get("native_health_snapshot"):
        return HealthConnectProvider()
    return GoogleFitProvider()


# ---------------- Google Fit legacy health layer ----------------
def refresh_token():
    rt=st.session_state.get("refresh_token")
    if not rt:return False
    r=requests.post("https://oauth2.googleapis.com/token",data={
        "client_id":st.secrets["GOOGLE_CLIENT_ID"],"client_secret":st.secrets["GOOGLE_CLIENT_SECRET"],
        "refresh_token":rt,"grant_type":"refresh_token"},timeout=20)
    if r.status_code==200:
        st.session_state.access_token=r.json()["access_token"]; return True
    return False

def fit(method,url,**kwargs):
    headers=kwargs.pop("headers",{}); headers["Authorization"]=f"Bearer {st.session_state.access_token}"; headers["Content-Type"]="application/json"
    r=requests.request(method,url,headers=headers,timeout=25,**kwargs)
    if r.status_code==401 and refresh_token():
        headers["Authorization"]=f"Bearer {st.session_state.access_token}"; r=requests.request(method,url,headers=headers,timeout=25,**kwargs)
    return r

def list_datasources(dtype=None):
    params={"dataTypeName":dtype} if dtype else {}
    r=fit("GET",FIT_BASE+"/dataSources",params=params)
    if r.status_code!=200:
        return None,r
    return r.json().get("dataSource",[]),r

def aggregate(dtype,start_ms,end_ms,data_source_id=None):
    spec={"dataTypeName":dtype} if not data_source_id else {"dataSourceId":data_source_id}
    body={"aggregateBy":[spec],"bucketByTime":{"durationMillis":86400000},"startTimeMillis":start_ms,"endTimeMillis":end_ms}
    r=fit("POST",FIT_BASE+"/dataset:aggregate",json=body)
    if r.status_code!=200:return None,r
    return r.json(),r

def read_raw_dataset(data_source_id,start_ms,end_ms):
    """Legge i punti grezzi di una singola sorgente Google Fit."""
    if not data_source_id:
        return None, None
    dataset_id=f"{int(start_ms)*1_000_000}-{int(end_ms)*1_000_000}"
    url=FIT_BASE+"/dataSources/"+urllib.parse.quote(str(data_source_id),safe="")+"/datasets/"+dataset_id
    r=fit("GET",url)
    if r.status_code!=200:
        return None,r
    return r.json(),r

def raw_points(payload):
    out=[]
    for p in (payload or {}).get("point",[]):
        v=point_value(p)
        if v is None:
            continue
        start_ns=int(p.get("startTimeNanos") or 0)
        end_ns=int(p.get("endTimeNanos") or start_ns)
        out.append({
            "start":start_ns/1_000_000_000,
            "end":end_ns/1_000_000_000,
            "value":v
        })
    return out

def compare_today_sources(catalogs, dtype, mode, day_start, day_end):
    """Read every visible source independently for today's interval."""
    rows=[]
    start_ms=int(day_start.timestamp()*1000)
    end_ms=int(day_end.timestamp()*1000)
    for src in catalogs.get("steps" if dtype=="com.google.step_count.delta" else "calories",[]):
        sid=src.get("id")
        payload,r=read_raw_dataset(sid,start_ms,end_ms)
        if payload is None:
            rows.append({
                "sorgente":src.get("name"),
                "app":src.get("app"),
                "device":src.get("device"),
                "tipo":"—",
                "punti":0,
                "valore":None,
                "http":r.status_code if r is not None else None
            })
            continue
        pts=raw_points(payload)
        pts=[p for p in pts if datetime.fromtimestamp(p["end"],ROME).date()==day_start.date()]
        value=sum(p["value"] for p in pts) if mode=="sum" else (pts[-1]["value"] if pts else None)
        rows.append({
            "sorgente":src.get("name"),
            "app":src.get("app"),
            "device":src.get("device"),
            "tipo":"delta" if mode=="sum" else "latest",
            "punti":len(pts),
            "valore":value,
            "http":200
        })
    return rows

def raw_point_rows(payload, day):
    """Return today's raw points with local timestamps and values."""
    rows=[]
    for p in raw_points(payload):
        start_dt=datetime.fromtimestamp(p["start"],ROME)
        end_dt=datetime.fromtimestamp(p["end"],ROME)
        if end_dt.date()==day:
            rows.append({
                "inizio":start_dt.strftime("%H:%M:%S"),
                "fine":end_dt.strftime("%H:%M:%S"),
                "valore":p["value"],
                "durata_min":round((p["end"]-p["start"])/60,1)
            })
    return sorted(rows,key=lambda x:(x["fine"],x["inizio"]))

def raw_today_sum(payload, day):
    """Somma solo i punti delta che terminano nel giorno locale richiesto."""
    pts=raw_points(payload)
    total=0.0
    used=0
    for p in pts:
        if datetime.fromtimestamp(p["end"],ROME).date()==day:
            total += p["value"]
            used += 1
    return (total if used else None), used

def source_label(src):
    app=(src.get("application") or {}).get("packageName","")
    dev=src.get("device") or {}
    devtxt=" ".join(str(dev.get(k,"")) for k in ("manufacturer","model","uid") if dev.get(k))
    name=src.get("dataStreamName") or src.get("dataStreamId") or "sorgente sconosciuta"
    bits=[name]
    if app: bits.append(app)
    if devtxt: bits.append(devtxt)
    return " · ".join(bits)

def choose_preferred_source(dtype,sources):
    """Choose a source without mislabeling phone data as Watch data.

    The current Google Fit catalog exposes Samsung top_level sources with
    phone model IDs (e.g. SM-S948B). These are not a verified Galaxy Watch
    stream, so they must not become the app's authoritative Watch steps.
    """
    if dtype=="com.google.step_count.delta":
        # Do not use Samsung top_level as the authoritative live Watch value.
        # Keep Google derived streams available for diagnostics only.
        preferred=[
            "derived:com.google.step_count.delta:com.google.android.gms:estimated_steps",
            "derived:com.google.step_count.delta:com.google.android.gms:merge_step_deltas",
        ]
        ids={s.get("id") for s in sources}
        for p in preferred:
            if p in ids:
                return p, "Google Fit derived — solo diagnostica, non Watch verificato"
        return None, "nessuna sorgente Watch verificata disponibile"

    preferred={
        "com.google.calories.expended":[
            "derived:com.google.calories.expended:com.google.android.gms:merge_calories_expended",
            "derived:com.google.calories.expended:com.google.android.gms:platform_calories_expended",
        ],
    }.get(dtype,[])
    ids={s.get("id") for s in sources}
    for p in preferred:
        if p in ids:
            return p, "stream derivato Google Fit"
    return (sources[0].get("id"), "prima sorgente disponibile") if sources else (None,"nessuna sorgente disponibile")

def source_catalog(dtype):
    sources,r=list_datasources(dtype)
    if sources is None:
        return [], {"status":"error","http":r.status_code,"type":dtype,"detail":r.text[:500]}
    rows=[]
    for src in sources:
        rows.append({
            "id":src.get("dataStreamId",""),
            "name":src.get("dataStreamName","") or "—",
            "type":src.get("type","") or "—",
            "app":(src.get("application") or {}).get("packageName","") or "—",
            "device":(src.get("device") or {}).get("model","") or "—",
        })
    return rows,{"status":"available","http":200,"type":dtype,"count":len(rows)}

def point_value(p):
    for v in p.get("value",[]):
        if "fpVal" in v:return float(v["fpVal"])
        if "intVal" in v:return float(v["intVal"])
    return None

def points_from(payload):
    points=[]
    for bucket in payload.get("bucket",[]):
        for ds in bucket.get("dataset",[]):
            for p in ds.get("point",[]):
                v=point_value(p)
                if v is not None:
                    ts=int(p.get("endTimeNanos") or p.get("startTimeNanos") or int(bucket.get("startTimeMillis",0))*1_000_000)/1_000_000_000
                    points.append({"ts":ts,"value":v})
    return points

def points_today_detailed(payload):
    """Return today's raw points with local start/end timestamps for debugging."""
    out=[]
    today_date=datetime.now(ROME).date()
    for bucket in payload.get("bucket",[]):
        for ds in bucket.get("dataset",[]):
            for p in ds.get("point",[]):
                v=point_value(p)
                if v is None: continue
                start_ns=int(p.get("startTimeNanos") or 0)
                end_ns=int(p.get("endTimeNanos") or 0)
                ts=end_ns/1_000_000_000 if end_ns else start_ns/1_000_000_000
                dt_end=datetime.fromtimestamp(ts,ROME)
                if dt_end.date()!=today_date: continue
                dt_start=datetime.fromtimestamp(start_ns/1_000_000_000,ROME) if start_ns else dt_end
                out.append({"start":dt_start.strftime("%H:%M:%S"),"end":dt_end.strftime("%H:%M:%S"),"value":v})
    return sorted(out,key=lambda x:(x["end"],x["start"]))

def daily_sum(payload):
    # For cumulative quantities such as steps, calories and distance, sum points per local day.
    d=defaultdict(float)
    for p in points_from(payload):
        day=datetime.fromtimestamp(p["ts"],ROME).date().isoformat(); d[day]+=p["value"]
    return [{"date":k,"value":v} for k,v in sorted(d.items())]

def daily_latest(payload):
    # For body measurements, use the latest reading of each local day.
    d={}
    for p in points_from(payload):
        day=datetime.fromtimestamp(p["ts"],ROME).date().isoformat()
        if day not in d or p["ts"]>d[day]["ts"]: d[day]=p
    return [{"date":k,"value":d[k]["value"]} for k in sorted(d)]

def sync_google_fit_health(days=14):
    now=datetime.now(ROME); start=(now-timedelta(days=days-1)).replace(hour=0,minute=0,second=0,microsecond=0)
    sm=int(start.timestamp()*1000); em=int(now.timestamp()*1000)
    specs={
        "steps":("com.google.step_count.delta","sum"),
        "calories":("com.google.calories.expended","sum"),
        "distance":("com.google.distance.delta","sum"),
        "weight":("com.google.weight","latest"),
        "body_fat":("com.google.body.fat.percentage","latest"),
        "bmr":("com.google.calories.bmr","latest")
    }
    data={}; hist={}; diag={}; catalogs={}; comparisons={}; source_values={}
    source_keys={"steps","calories"}
    for key,(dtype,mode) in specs.items():
        try:
            chosen=None; choice_reason=None
            if key in source_keys:
                sources,r=list_datasources(dtype)
                if sources is not None:
                    chosen,choice_reason=choose_preferred_source(dtype,sources)
                    catalogs[key]=[
                        {
                            "id":x.get("dataStreamId",""),
                            "name":x.get("dataStreamName","") or "—",
                            "type":x.get("type","") or "—",
                            "app":(x.get("application") or {}).get("packageName","") or "—",
                            "device":(x.get("device") or {}).get("model","") or "—",
                        } for x in sources
                    ]
                    # Debug: read EVERY visible source independently for today,
                    # including the selected derived stream. Never sum these values.
                    per_source=[]
                    for src in sources:
                        src_id=src.get("dataStreamId")
                        if not src_id:
                            continue
                        try:
                            spayload,sr=aggregate(dtype,sm,em,src_id)
                            if spayload is not None:
                                sh=daily_sum(spayload)
                                sval=next((z["value"] for z in sh if z["date"]==today()),None)
                                per_source.append({
                                    "name":src.get("dataStreamName","") or "—",
                                    "app":(src.get("application") or {}).get("packageName","") or "—",
                                    "device":(src.get("device") or {}).get("model","") or "—",
                                    "value":sval,
                                    "id":src_id,
                                    "selected":src_id==chosen,
                                    "points_today":points_today_detailed(spayload)
                                })
                            else:
                                per_source.append({
                                    "name":src.get("dataStreamName","") or "—",
                                    "app":(src.get("application") or {}).get("packageName","") or "—",
                                    "device":(src.get("device") or {}).get("model","") or "—",
                                    "value":None,
                                    "id":src_id,
                                    "selected":src_id==chosen,
                                    "error":f"HTTP {sr.status_code}",
                                    "points_today":[]
                                })
                        except Exception as se:
                            per_source.append({
                                "name":src.get("dataStreamName","") or "—",
                                "app":(src.get("application") or {}).get("packageName","") or "—",
                                "device":(src.get("device") or {}).get("model","") or "—",
                                "value":None,
                                "id":src_id,
                                "selected":src_id==chosen,
                                "error":str(se)[:150],
                                "points_today":[]
                            })
                    source_values[key]=per_source
                else:
                    catalogs[key]=[]
                    diag[key]={"status":"source_error","http":r.status_code,"type":dtype,"detail":r.text[:500]}
            payload,r=aggregate(dtype,sm,em,chosen)
            if payload is None:
                # If a preferred derived stream is unavailable/forbidden, fall back
                # to the dataType aggregate so the user still gets diagnostics.
                fallback_used=False
                if chosen:
                    payload,r=aggregate(dtype,sm,em,None); fallback_used=True
                if payload is None:
                    data[key]=None; hist[key]=[]; diag[key]={"status":"error","http":r.status_code,"type":dtype,"detail":r.text[:500],"source_id":chosen}
                    continue
                choice_reason=(choice_reason or "sorgente selezionata")+" · fallback aggregate"
            h=daily_sum(payload) if mode=="sum" else daily_latest(payload)
            hist[key]=h
            if key in source_keys:
                # Keep the old all-source aggregate only as a diagnostic comparator.
                # It is NOT used for the value shown by MyDietApp when a preferred
                # derived stream is available.
                all_payload,all_r=aggregate(dtype,sm,em,None)
                if all_payload is not None:
                    all_hist=daily_sum(all_payload) if mode=="sum" else daily_latest(all_payload)
                    selected_today=next((z["value"] for z in h if z["date"]==today()),None)
                    all_today=next((z["value"] for z in all_hist if z["date"]==today()),None)
                    comparisons[key]={"selected":selected_today,"all_sources":all_today,"difference":(all_today-selected_today) if all_today is not None and selected_today is not None else None}
                else:
                    comparisons[key]={"selected":next((z["value"] for z in h if z["date"]==today()),None),"all_sources":None,"difference":None,"error":all_r.text[:300]}
            # IMPORTANT: the aggregate endpoint is NOT used for the live
            # value of steps/calories. In our account it returned 6267 steps
            # even though that value represented a wider historical aggregate.
            live_value = h[-1]["value"] if h else None
            live_info = {}
            if key in {"steps","calories"} and chosen:
                live_start=datetime.now(ROME).replace(hour=0,minute=0,second=0,microsecond=0)
                live_end=datetime.now(ROME)
                lsm=int(live_start.timestamp()*1000)
                lem=int(live_end.timestamp()*1000)
                raw_payload,raw_r=read_raw_dataset(chosen,lsm,lem)
                if raw_payload is not None:
                    raw_rows=raw_point_rows(raw_payload,live_start.date())
                    if mode=="sum":
                        pts=[p for p in raw_points(raw_payload) if datetime.fromtimestamp(p["end"],ROME).date()==live_start.date()]
                        raw_count=len(pts)
                        raw_sum=sum(p["value"] for p in pts) if pts else None
                        if key=="calories":
                            # calories.expended can be cumulative/overlapping.
                            # Do not blindly sum it for the live dashboard.
                            live_value=pts[-1]["value"] if pts else None
                        else:
                            live_value=raw_sum
                    else:
                        pts=raw_points(raw_payload)
                        live_value=pts[-1]["value"] if pts else None
                        raw_count=len(pts)
                        raw_sum=None
                    live_info={
                        "method":"raw_dataset",
                        "points":raw_count,
                        "start":live_start.strftime("%d/%m/%Y %H:%M:%S"),
                        "end":live_end.strftime("%d/%m/%Y %H:%M:%S"),
                        "value":live_value,
                        "raw_sum":raw_sum,
                        "raw_rows":raw_rows
                    }
                else:
                    live_info={
                        "method":"raw_dataset_failed",
                        "http":raw_r.status_code if raw_r is not None else None,
                        "detail":raw_r.text[:250] if raw_r is not None else "nessuna risposta"
                    }

            data[key]=live_value
            diag[key]={
                "status":"available" if h else "no_data",
                "http":200,
                "type":dtype,
                "points":len(points_from(payload)),
                "source_id":chosen,
                "source_reason":choice_reason,
                "source_label":next((f"{x['name']} · {x['app']} · {x.get('device') or 'device n/d'}" for x in catalogs.get(key,[]) if x["id"]==chosen), chosen or "aggregate di tutte le sorgenti"),
                "live_query":live_info
            }
        except Exception as e:
            data[key]=None; hist[key]=[]; diag[key]={"status":"error","type":dtype,"detail":str(e)}
    # These are the exact live values calculated above from today's raw
    # dataset for the selected Google Fit derived streams.
    # V13: the Google derived step stream is retained for diagnostics, but is
    # not treated as Galaxy Watch data. A phone Samsung top_level source is
    # explicitly excluded from the authoritative dashboard value.
    step_reason=str(diag.get("steps",{}).get("source_reason","") or "")
    step_app=str(diag.get("steps",{}).get("source_label","") or "")
    # No Google Fit legacy value is considered an authoritative Galaxy Watch
    # value. A verified value will only be enabled by the native Health Connect
    # provider, which will set steps_source_verified=True explicitly.
    data["steps_today"]=None
    data["steps_source_verified"]=False
    data["steps_untrusted_value"]=data.get("steps")
    data["steps_untrusted_reason"]=step_reason or "Google Fit legacy: sorgente Watch non verificata"
    # Never expose an obviously impossible calorie value to the food budget.
    # Keep the raw value in diagnostics for investigation.
    cal=data.get("calories")
    # Google Fit legacy calories.expended can contain cumulative/overlapping
    # intervals. Until Health Connect provides a normalized total-calorie
    # record, never use this value for the live food budget.
    data["calories_today"]=None
    data["calories_untrusted_value"]=cal if cal is not None else None
    data["calories_source_verified"]=False
    dist=next((x["value"] for x in hist["distance"] if x["date"]==today()),None)
    data["distance_today"]=dist/1000 if dist is not None else None
    data["source_catalogs"]=catalogs
    data["source_comparisons"]=comparisons
    data["source_values"]=source_values
    # V11: compare every visible source independently for today's interval.
    try:
        live_start=datetime.now(ROME).replace(hour=0,minute=0,second=0,microsecond=0)
        live_end=datetime.now(ROME)
        diag["_source_compare_steps"]=compare_today_sources(
            catalogs,"com.google.step_count.delta","sum",live_start,live_end
        )
        diag["_source_compare_calories"]=compare_today_sources(
            catalogs,"com.google.calories.expended","sum",live_start,live_end
        )
    except Exception as e:
        diag["_source_compare_error"]=str(e)

    return data,hist,diag

# ---------------- Navigation ----------------
pages={"Home":"🏠","Piano":"🍽️","Dispensa":"🛒","Attività":"🏃","Profilo":"👤"}
cols=st.columns(5)
for col,(name,icon) in zip(cols,pages.items()):
    with col:
        if st.button(f"{icon} {name}",key="nav_"+name,use_container_width=True,type="primary" if st.session_state.page==name else "secondary"):
            st.session_state.page=name; st.rerun()

# ---------------- Home ----------------
if st.session_state.page=="Home":
    # Health snapshot received from the Android bridge for this session.
    # Keep this local variable available to the whole Home block.
    h=st.session_state.get("health",{})
    b=balance()
    rem=b["remaining"]
    msg=f"Ti restano {rem:,} kcal".replace(",",".") if rem>=0 else f"Sei sopra il target di {abs(rem):,} kcal".replace(",",".")
    cls="ok" if rem>=0 else "bad"
    target_label="budget dinamico" if b["using_observed"] else "target stimato"
    st.markdown(f"""<div class="card"><div class="muted">CALORIE ASSUNTE / {target_label.upper()}</div>
    <div class="big">{b["eaten"]:,} / {b["live_target"]:,} kcal</div>
    <div class="muted">Target profilo {b["target"]:,} · deficit {b["deficit"]} kcal · BMR {b["bmr_health"] or b["bmr_est"]} kcal/giorno</div>
    <div class="{cls}">{msg}</div></div>""".replace(",","."),unsafe_allow_html=True)
    st.progress(min(max(b["eaten"]/max(b["live_target"],1),0),1))
    if b["using_observed"]:
        c1,c2,c3=st.columns(3)
        c1.metric("🔥 Consumo finora",f"{b['observed_burn']:,} kcal".replace(",","."))
        c2.metric("⚡ Attive stimate finora",f"{b['active_observed']:,} kcal".replace(",","."))
        c3.metric("🎯 Consumo stimato oggi",f"{b['projected_burn']:,} kcal".replace(",","."))
        st.caption(
            f"Il budget dinamico usa il consumo Health osservato ({b['observed_burn']} kcal) "
            f"e aggiunge solo il consumo a riposo residuo fino a mezzanotte ({b['remaining_rest']} kcal). "
            "Non vengono inventate attività future."
        )
        a=activity_summary()
        with st.container(border=True):
            st.markdown("**🏃 Attività di oggi**")
            ac1,ac2,ac3,ac4=st.columns(4)
            ac1.metric("👣 Passi", f"{a['steps']:,}".replace(",","."))
            ac2.metric("⚡ Calorie attive", f"{a['active_calories']:,} kcal".replace(",","."))
            ac3.metric("📏 Distanza", f"{a['distance_km']:.2f} km")
            ac4.metric("🏋️ Allenamenti", str(a['workouts']))
            if a["details"]:
                for w in a["details"]:
                    st.write(f"• **{w['name']}** · {w['duration_minutes']} min")
            else:
                st.caption("Nessuna sessione di allenamento registrata. I passi e le calorie attive continuano comunque ad aggiornarsi.")
            st.caption("Le calorie attive sono già comprese nel consumo totale Health osservato: non vengono sommate una seconda volta.")
    st.subheader("🍽️ Oggi")
    st.caption("Registra i pasti quando li mangi: il totale in alto si aggiorna automaticamente.")
    d=current_day_name(); ms=st.session_state.meal_plan.get(d)

    if not ms:
        st.info(f"Non hai ancora un piano alimentare per {d}. Vai in **Piano** e genera il piano settimanale.")
    else:
        for idx,(mn,m) in enumerate(ms.items()):
            items=active_items(m)
            kcal=round(sum(float(i["kcal"])*float(st.session_state.overrides.get(i["id"],{}).get("multiplier",1)) for i in items))
            meal_ids=[i["id"] for i in items]
            registered=bool(meal_ids) and all(st.session_state.eaten.get(iid,False) for iid in meal_ids)
            status="✅ Registrato" if registered else "○ Non registrato"
            with st.container(border=True):
                c1,c2,c3=st.columns([5,2,1])
                with c1:
                    st.markdown(f"**{mn}**")
                    st.caption(f"{m.get('name','Pasto')} · {kcal} kcal · {status}")
                with c2:
                    if not registered:
                        if st.button("🍴 Ho mangiato",key=f"home_eat_{d}_{idx}",use_container_width=True):
                            for iid in meal_ids: st.session_state.eaten[iid]=True
                            st.rerun()
                    else:
                        if st.button("↩ Annulla",key=f"home_undo_{d}_{idx}",use_container_width=True):
                            for iid in meal_ids: st.session_state.eaten[iid]=False
                            st.rerun()
                with c3:
                    st.metric("kcal",kcal)
                with st.expander("Dettagli"):
                    for item in items:
                        mult=float(st.session_state.overrides.get(item["id"],{}).get("multiplier",1))
                        st.write(f"• {item['name']} — {item['qty']*mult:g}{item['unit']} · {round(item['kcal']*mult)} kcal")

    # ---------------- Live energy balance ----------------
    st.divider()
    st.subheader("⚡ Bilancio energetico di oggi")
    if b["using_observed"]:
        net_so_far=b["eaten"]-b["observed_burn"]
        projected_deficit=b["projected_burn"]-b["eaten"]
        c1,c2,c3,c4=st.columns(4)
        c1.metric("🍽️ Assunte",f"{b['eaten']:,} kcal".replace(",","."))
        c2.metric("🔥 Consumate finora",f"{b['observed_burn']:,} kcal".replace(",","."))
        c3.metric("🎯 Target alimentare",f"{b['live_target']:,} kcal".replace(",","."))
        c4.metric("📉 Deficit previsto",f"{projected_deficit:,} kcal".replace(",","."))
        if net_so_far < 0:
            st.success(f"Sei attualmente a **{abs(net_so_far):,} kcal sotto il consumo osservato**. Il dato continua ad aggiornarsi con Health.".replace(",","."))
        elif net_so_far > 0:
            st.warning(f"Sei attualmente a **{net_so_far:,} kcal sopra il consumo osservato**. È un dato provvisorio della giornata.".replace(",","."))
        else:
            st.info("Assunte e consumate sono momentaneamente allo stesso livello.")
        if h.get("active_calories_today") is not None:
            st.caption(f"👣 {int(h.get('steps_today') or 0):,} passi · ⚡ {float(h['active_calories_today']):.0f} kcal attive · 🔥 {b['projected_burn']:,} kcal consumo stimato a fine giornata.".replace(",","."))
    else:
        st.info("Collega il bridge Health Connect per trasformare il target stimato in un budget dinamico basato sul consumo reale di oggi.")

    manual_today=[x for x in st.session_state.manual_foods if x["date"]==today()]
    if manual_today:
        with st.container(border=True):
            st.markdown("**🍴 Alimenti registrati manualmente**")
            for j,x in enumerate(manual_today):
                st.write(f"• {x['name']} · {round(float(x['kcal']))} kcal")

    if st.session_state.last_sync: st.caption("Ultima sincronizzazione Health: "+st.session_state.last_sync)

# ---------------- Piano ----------------
elif st.session_state.page=="Piano":
    st.title("🍽️ Il tuo piano")
    days=list(st.session_state.meal_plan.keys()); day=st.selectbox("Giorno",days)
    for mn,m in st.session_state.meal_plan[day].items():
        kcal=sum(float(i["kcal"])*float(st.session_state.overrides.get(i["id"],{}).get("multiplier",1)) for i in active_items(m))
        with st.container(border=True):
            st.markdown(f"### {mn}"); st.caption(f"{m.get('name','Pasto')} · **{round(kcal)} kcal**")
            for item in list(m.get("ingredients",[])):
                ov=st.session_state.overrides.get(item["id"],{}); mult=float(ov.get("multiplier",1))
                if ov.get("removed"): continue
                c1,c2,c3,c4=st.columns([5,1,1,1])
                with c1:
                    eaten=st.checkbox(f"{item['name']} — {item['qty']*mult:g}{item['unit']} · {round(item['kcal']*mult)} kcal",value=st.session_state.eaten.get(item['id'],False),key="eat_"+item['id']); st.session_state.eaten[item['id']]=eaten
                with c2:
                    if st.button("−",key="minus_"+item['id'],use_container_width=True): st.session_state.overrides[item['id']]={"multiplier":max(.25,mult-.25)}; st.rerun()
                with c3: st.write(f"x{mult:g}")
                with c4:
                    if st.button("+",key="plus_"+item['id'],use_container_width=True): st.session_state.overrides[item['id']]={"multiplier":min(3,mult+.25)}; st.rerun()
                if st.button("✕ Rimuovi",key="remove_"+item['id']): st.session_state.overrides[item['id']]={"removed":True,"multiplier":mult}; st.rerun()
            with st.expander("➕ Aggiungi alimento"):
                a,b,c,d=st.columns([3,1,1,1])
                with a:n=st.text_input("Nome",key=f"n_{day}_{mn}")
                with b:q=st.number_input("Qtà",min_value=.1,value=10.,step=1.,key=f"q_{day}_{mn}")
                with c:u=st.selectbox("Unità",["g","ml","pz"],key=f"u_{day}_{mn}")
                with d:k=st.number_input("kcal",min_value=0,value=50,step=5,key=f"k_{day}_{mn}")
                if st.button("Aggiungi al pasto",key=f"add_{day}_{mn}") and n.strip():
                    st.session_state.meal_plan[day][mn]["ingredients"].append({"id":sid(),"name":n.strip(),"qty":q,"unit":u,"kcal":k}); st.rerun()
    st.divider(); st.subheader("🤖 Generazione AI")
    if st.button("Genera / rigenera piano settimanale",type="primary"):
        try:
            ep=energy_profile(); model=genai.GenerativeModel("gemini-2.5-flash")
            prompt=f'''Crea un piano alimentare italiano di 7 giorni. Profilo: {st.session_state.p_weight} kg, {st.session_state.p_height} cm, {st.session_state.p_age} anni, sesso {st.session_state.p_sex}. Target alimentare stimato: {ep["target"]} kcal/giorno. Restituisci SOLO JSON con giorni Lunedì-Domenica, 4 pasti al giorno e per ogni pasto name + ingredients. Ogni ingredient deve avere name, qty, unit, kcal.'''
            raw=model.generate_content(prompt).text.replace("```json","").replace("```","").strip(); gen=json.loads(raw); out={}
            for day,ms in gen.items():
                out[day]={}
                for mn,m in ms.items(): out[day][mn]={"name":m.get("name","Pasto"),"ingredients":[{"id":sid(),"name":str(x.get("name","Alimento")),"qty":float(x.get("qty",1)),"unit":str(x.get("unit","g")),"kcal":round(float(x.get("kcal",0)))} for x in m.get("ingredients",[])]}
            st.session_state.meal_plan=out; st.session_state.overrides={}; st.session_state.eaten={}; st.rerun()
        except Exception as e: st.error(f"Errore AI: {e}")
    st.subheader("📷 Mensa Smart")
    img=st.camera_input("Scatta il menu") or st.file_uploader("Carica una foto",type=["jpg","jpeg","png"],key="mensa3")
    if img:
        im=Image.open(img); st.image(im,width=420)
        if st.button("✨ Analizza menu",type="secondary"):
            try:
                b=balance(); r=genai.GenerativeModel("gemini-2.5-flash").generate_content([f"Analizza questo menu. L'utente ha {b['remaining']} kcal disponibili oggi. Rispondi con COSA ORDINARE, PERCHÉ, COSA LIMITARE.",im]); st.info(r.text)
            except Exception as e: st.error(str(e))

# ---------------- Dispensa ----------------
elif st.session_state.page=="Dispensa":
    st.title("🛒 Dispensa")
    st.caption("La lista viene calcolata localmente dal piano: rimuovi o aggiungi un alimento e cambia subito.")
    for q,u,n in grocery(): st.checkbox(f"{n} — {q:g} {u}",key="g_"+n+u)
    st.divider(); st.subheader("🍴 Registra qualcosa che hai mangiato")
    c1,c2=st.columns([3,1])
    with c1:n=st.text_input("Alimento",placeholder="Pizza margherita")
    with c2:k=st.number_input("kcal",0,3000,500,10)
    if st.button("Registra",type="primary") and n.strip(): st.session_state.manual_foods.append({"name":n.strip(),"kcal":k,"date":today()}); st.rerun()
    for x in reversed(st.session_state.manual_foods):
        if x["date"]==today(): st.write(f"🍴 {x['name']} · {x['kcal']} kcal")

# ---------------- Attività / Health ----------------
elif st.session_state.page=="Attività":
    st.title("🏃 Attività & Health")
    h=st.session_state.get("health",{})
    native=bool(h.get("native_health_snapshot"))

    if native:
        p=h.get("provider",{})
        st.success(f"✓ Health Connect nativo attivo · snapshot ricevuto {st.session_state.get('last_sync') or '—'}")
        st.caption(f"Bridge Android {p.get('bridge_version') or '—'} · Samsung Health → Health Connect → MyDietApp")
        trust=h.get("native_health_payload",{}).get("trust",{})
        st.write("**Fonte produttiva:** Health Connect nativo")
        c1,c2,c3=st.columns(3)
        c1.metric("Passi verificati", "Sì" if trust.get("steps") else "No")
        c2.metric("Calorie totali verificate", "Sì" if trust.get("total_calories") else "No")
        c3.metric("Snapshot", h.get("date") or "—")
    else:
        st.caption("Dati reali separati dal target alimentare. Health Connect nativo è la fonte produttiva; Google Fit resta solo diagnostica.")

        cid=st.secrets.get("GOOGLE_CLIENT_ID"); cs=st.secrets.get("GOOGLE_CLIENT_SECRET"); ru=st.secrets.get("REDIRECT_URI")
        if not cid or not cs or not ru:
            st.error("Mancano GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET o REDIRECT_URI nei secrets.")
        else:
            if "code" in st.query_params:
                r=requests.post("https://oauth2.googleapis.com/token",data={"client_id":cid,"client_secret":cs,"code":st.query_params["code"],"grant_type":"authorization_code","redirect_uri":ru},timeout=20)
                if r.status_code==200:
                    x=r.json(); st.session_state.access_token=x["access_token"]
                    if x.get("refresh_token"): st.session_state.refresh_token=x["refresh_token"]
                    st.query_params.clear(); st.rerun()
                else: st.error("Autorizzazione Google non riuscita: "+r.text[:500])
            if "access_token" not in st.session_state:
                url="https://accounts.google.com/o/oauth2/v2/auth?client_id="+urllib.parse.quote(cid.strip())+"&redirect_uri="+urllib.parse.quote(ru.strip(),safe="")+"&response_type=code&scope="+urllib.parse.quote(FIT_SCOPES,safe="")+"&access_type=offline&prompt=consent"
                st.warning("🔗 Google non è collegato in questa sessione. Dopo l'autorizzazione, torna qui.")
                st.link_button("🔗 Collega Google Health / Fit",url,use_container_width=True)
            else:
                st.success("Account Google collegato in questa sessione")
                if st.button("🔄 Sincronizza dati reali",type="primary",use_container_width=True):
                    try:
                        provider=get_health_provider()
                        data,hist,diag=provider.sync()
                        data["provider"]=provider.info()
                        st.session_state.health=data; st.session_state.health_history=hist; st.session_state.diagnostics=diag
                        st.session_state.last_sync=datetime.now(ROME).strftime("%d/%m/%Y %H:%M")
                        st.rerun()
                    except Exception as e: st.error(f"Sincronizzazione fallita: {e}")

    # IMPORTANT: Native bridge data is rendered outside the Google-auth branch.
    # This keeps Health Connect values visible when the app is opened directly
    # from the Android bridge.
    h=st.session_state.get("health",{})
    provider_info=h.get("provider",{}) if isinstance(h,dict) else {}
    if provider_info:
        st.caption(f"Provider Health attivo: **{provider_info.get('name','—')}** · stato: **{provider_info.get('status','—')}**")

    if h:
        st.divider(); st.subheader("📊 Dati di oggi")
        cards=[
            ("👣 Passi oggi",h.get("steps_today"),"passi"),
            ("🔥 Calorie totali",h.get("calories_today"),"kcal"),
            ("⚡ Calorie attive",h.get("active_calories_today"),"kcal"),
            ("⚖️ Peso",h.get("weight"),"kg"),
            ("🟠 Massa grassa",h.get("body_fat"),"%"),
            ("📏 Distanza",h.get("distance_today"),"km"),
            ("🧬 BMR",h.get("bmr"),"kcal/giorno"),
            ("❤️ FC media",h.get("heart_rate_avg"),"bpm"),
            ("😴 Sonno",h.get("sleep_minutes"),"min"),
        ]
        cc=st.columns(3)
        for i,(lab,val,unit) in enumerate(cards):
            with cc[i%3]:
                if val is None:
                    st.metric(lab,"Non disponibile")
                else:
                    try:
                        if unit in ("passi","kcal","min"): display=f"{float(val):,.0f} {unit}".replace(",",".")
                        elif unit=="bpm": display=f"{float(val):.0f} {unit}"
                        else: display=f"{float(val):.1f} {unit}"
                    except Exception: display=f"{val} {unit}"
                    st.metric(lab,display)

        a=activity_summary()
        st.divider(); st.subheader("🏃 Attività di oggi")
        ac1,ac2,ac3=st.columns(3)
        ac1.metric("👣 Passi", f"{a['steps']:,}".replace(",","."))
        ac2.metric("⚡ Calorie attive", f"{a['active_calories']:,} kcal".replace(",","."))
        ac3.metric("📏 Distanza", f"{a['distance_km']:.2f} km")
        if a["details"]:
            for w in a["details"]:
                st.write(f"🏃 **{w['name']}** · {w['duration_minutes']} min")
        else:
            st.caption("Nessuna sessione ExerciseSessionRecord registrata oggi.")
        st.caption("Le calorie attive descrivono l'attività già inclusa nel consumo totale Health Connect; non vengono aggiunte nuovamente al bilancio.")

        if native:
            st.info("I dati mostrati sopra arrivano direttamente dal bridge Android tramite Health Connect. Google Fit non viene interrogato per il bilancio.")
            if h.get("calories_today") is not None and h.get("calories_source_verified"):
                b=balance()
                st.info(f"🔥 Consumo osservato **{b['observed_burn']:,} kcal**. Stima fine giornata **{b['projected_burn']:,} kcal**. Budget alimentare dinamico **{b['live_target']:,} kcal**.".replace(",","."))
            elif h.get("calories_today") is not None:
                st.warning("⚠️ Le calorie totali Health Connect sono ricevute, ma non sono ancora considerate verificate per il calcolo del budget. Prima validiamo la provenienza Galaxy Watch.")
        else:
            st.info("Google Fit legacy è usato solo come diagnostica; i valori non verificati non entrano nel bilancio produttivo.")

        if h.get("weight") is not None and h.get("body_fat") is not None:
            fat=float(h["weight"])*float(h["body_fat"])/100; lean=float(h["weight"])-fat
            c1,c2=st.columns(2); c1.metric("🟠 Massa grassa stimata",f"{fat:.1f} kg"); c2.metric("💪 Massa magra stimata",f"{lean:.1f} kg")

        if native:
            payload=h.get("native_health_payload",{})
            with st.expander("🔎 Dettagli snapshot Health Connect"):
                st.write("**Schema:**",payload.get("schema","—"))
                st.write("**Bridge:**",payload.get("bridge_version","—"))
                st.write("**Data:**",payload.get("date","—"))
                st.write("**Trust:**",payload.get("trust",{}))
                st.caption("Il payload compatto contiene solo metriche normalizzate e informazioni di trust; le sorgenti dettagliate restano nel bridge Android.")
        else:
            st.divider(); st.subheader("🧪 Diagnostica")
            st.info("Google Fit legacy resta disponibile solo come diagnostica. I passi derivati e le calorie legacy non vengono usati nel bilancio perché non possiamo dimostrare che rappresentino correttamente il Galaxy Watch Ultra 2.")

            diag_view=st.session_state.get("diagnostics",{})
            comp_steps=diag_view.get("_source_compare_steps",[])
            comp_cal=diag_view.get("_source_compare_calories",[])
            if comp_steps:
                with st.expander("📱 Confronto sorgenti — PASSI",expanded=True): st.dataframe(pd.DataFrame(comp_steps),use_container_width=True,hide_index=True)
            if comp_cal:
                with st.expander("🔥 Confronto sorgenti — CALORIE"): st.dataframe(pd.DataFrame(comp_cal),use_container_width=True,hide_index=True)
            if diag_view.get("_source_compare_error"):
                st.warning(f"⚠️ Errore confronto sorgenti: {diag_view['_source_compare_error']}")

            for k,x in st.session_state.diagnostics.items():
                if k.startswith("_source_compare_"): continue
                if not isinstance(x,dict) or "status" not in x: continue
                if x["status"]=="available":
                    if x.get("source_id"):
                        st.success(f"✓ {k}: dati trovati · {x['type']} · {x.get('points',0)} punti")
                        st.caption(f"Sorgente usata: `{x.get('source_label',x['source_id'])}` · {x.get('source_reason','')}")
                    else: st.success(f"✓ {k}: dati trovati · {x['type']} · {x.get('points',0)} punti")
                elif x["status"]=="no_data": st.warning(f"○ {k}: nessun dato restituito · {x['type']}")
                elif x["status"]=="source_error": st.error(f"✕ {k}: impossibile leggere l'elenco delle sorgenti · HTTP {x.get('http','')} · {x.get('detail','')}")
                else: st.error(f"✕ {k}: HTTP {x.get('http','')} · {x.get('detail','')}")

    elif native:
        st.warning("Il bridge è stato rilevato ma non contiene metriche leggibili. Ritorna al bridge Android, premi 'Leggi dati di oggi' e poi 'Invia dati'.")
    else:
        st.info("Nessun dato Health disponibile in questa sessione.")

# ---------------- Profilo ----------------

else:
    st.title("👤 Profilo")
    with st.form("profile"):
        c1,c2=st.columns(2)
        with c1:
            name=st.text_input("Nome",st.session_state.p_name)
            weight=st.number_input("Peso (kg)",30.,300.,float(st.session_state.p_weight),.1)
            height=st.number_input("Altezza (cm)",100.,230.,float(st.session_state.p_height),.5)
        with c2:
            age=st.number_input("Età",13,100,int(st.session_state.p_age))
            sex=st.selectbox("Sesso",["male","female"],index=0 if st.session_state.p_sex=="male" else 1)
            activity=st.selectbox("Attività abituale",list(ACTIVITY_FACTORS.keys()),index=list(ACTIVITY_FACTORS.keys()).index(st.session_state.p_activity_level))
            deficit=st.select_slider("Deficit desiderato",options=[300,500,700],value=int(st.session_state.p_deficit),format_func=lambda x:f"{x} kcal/giorno")
        if st.form_submit_button("Salva",type="primary"):
            st.session_state.p_name=name; st.session_state.p_weight=weight; st.session_state.p_height=height; st.session_state.p_age=age; st.session_state.p_sex=sex; st.session_state.p_activity_level=activity; st.session_state.p_deficit=deficit; st.success("Profilo aggiornato")
    ep=energy_profile()
    st.subheader("🎯 Obiettivo energetico")
    st.metric("Target alimentare stimato",f"{ep['target']:,} kcal/giorno".replace(",","."))
    c1,c2=st.columns(2); c1.metric("BMR stimato",f"{ep['bmr_est']:,} kcal".replace(",",".")); c2.metric("Mantenimento stimato",f"{ep['maintenance_est']:,} kcal".replace(",","."))
    b=balance()
    if b["using_observed"]:
        st.info(f"🔥 Con i dati Health di oggi, il budget dinamico è circa **{b['live_target']:,} kcal**: consumo osservato {b['observed_burn']:,} + riposo residuo {b['remaining_rest']:,} → stima fine giornata {b['projected_burn']:,}, meno deficit {b['deficit']}.")
    st.caption("Il target di profilo è una stima basata su Mifflin-St Jeor + livello di attività + deficit scelto. Il budget dinamico usa il consumo totale osservato da Health quando disponibile; l’attività viene mostrata separatamente senza doppio conteggio. Non è una prescrizione medica.")
