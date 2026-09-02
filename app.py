import streamlit as st
import google.generativeai as genai
import json
import pandas as pd
import urllib.parse
import requests
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
from PIL import Image
from collections import defaultdict
import uuid

# ============================================================
# MyDietApp v15
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

# ---------------- State ----------------
def sid(): return uuid.uuid4().hex[:10]
def today(): return datetime.now(ROME).date().isoformat()

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

def balance():
    h=st.session_state.health
    e=energy_profile()
    eaten=eaten_kcal()
    observed=float(h.get("calories_today") or 0) if h.get("calories_source_verified") else 0.0
    # Google Fit calories.expended is a total expenditure stream and may include
    # basal expenditure. Use the observed total only for the live food budget.
    live_target=round(max(1200, observed-e["deficit"])) if observed > 0 else e["target"]
    return {
        "target":e["target"], "live_target":live_target, "eaten":eaten,
        "remaining":live_target-eaten, "observed_burn":round(observed),
        "bmr_est":e["bmr_est"], "bmr_health":round(bmr_health) if bmr_health else None,
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
    status = "planned"

    def sync(self, days=14):
        raise RuntimeError(
            "Health Connect richiede il componente Android nativo: "
            "il server Streamlit non può leggere direttamente i dati locali del telefono."
        )

def get_health_provider():
    # V14: Google Fit remains the compatibility provider. The selector is the
    # only place that needs changing when the native Android bridge is ready.
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
    dist=next((x["value"] for x in hist["distance"] if x["date"]==t),None)
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
    b=balance()
    rem=b["remaining"]
    msg=f"Ti restano {rem:,} kcal".replace(",",".") if rem>=0 else f"Sei sopra il target di {abs(rem):,} kcal".replace(",",".")
    cls="ok" if rem>=0 else "bad"
    target_label="budget dinamico" if b["using_observed"] else "target stimato"
    st.markdown(f"""<div class="card"><div class="muted">CALORIE ASSUNTE / {target_label.upper()}</div>
    <div class="big">{b["eaten"]:,} / {b["live_target"]:,} kcal</div>
    <div class="muted">Target profilo {b["target"]:,} · deficit {b["deficit"]} kcal · BMR stimato {b["bmr_est"]} kcal</div>
    <div class="{cls}">{msg}</div></div>""".replace(",","."),unsafe_allow_html=True)
    st.progress(min(max(b["eaten"]/max(b["live_target"],1),0),1))
    st.subheader("🍽️ Oggi")
    d=current_day_name(); ms=st.session_state.meal_plan.get(d) or next(iter(st.session_state.meal_plan.values()))
    for mn,m in list(ms.items())[:2]:
        kcal=round(sum(float(i["kcal"])*float(st.session_state.overrides.get(i["id"],{}).get("multiplier",1)) for i in active_items(m)))
        st.write(f"**{mn}** · {m.get('name','Pasto')} · {kcal} kcal")
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
    st.caption("Dati reali separati dal target alimentare. La sincronizzazione è manuale per evitare chiamate inutili a Google Fit.")
    cid=st.secrets.get("GOOGLE_CLIENT_ID"); cs=st.secrets.get("GOOGLE_CLIENT_SECRET"); ru=st.secrets.get("REDIRECT_URI")
    if not cid or not cs or not ru: st.error("Mancano GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET o REDIRECT_URI nei secrets.")
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
        h=st.session_state.health
        provider_info=h.get("provider",{}) if isinstance(h,dict) else {}
        if provider_info:
            st.caption(f"Provider Health attivo: **{provider_info.get('name','—')}** · stato: **{provider_info.get('status','—')}**")
        if h:
            st.divider(); st.subheader("📊 Dati di oggi")
            cards=[("👣 Passi oggi",h.get("steps_today"),"passi"),("🔥 Calorie totali",h.get("calories_today"),"kcal"),("⚖️ Peso",h.get("weight"),"kg"),("🟠 Massa grassa",h.get("body_fat"),"%"),("📏 Distanza",h.get("distance_today"),"km"),("🧬 BMR",h.get("bmr"),"kcal/giorno")]
            cc=st.columns(3)
            for i,(lab,val,unit) in enumerate(cards):
                with cc[i%3]: st.metric(lab,"Non disponibile" if val is None else f"{val:.1f} {unit}")
            if h.get("calories_today") is not None:
                active=max(0,round(float(h["calories_today"])-effective_bmr()))
                b=balance()
                st.info(f"🔥 Google Fit rileva **{round(float(h['calories_today'])):,} kcal** totali oggi. Sopra il BMR stimato: circa **{active:,} kcal**. Budget alimentare dinamico: **{b['live_target']:,} kcal** (consumo osservato − deficit {b['deficit']} kcal).")
            if h.get("weight") is not None and h.get("body_fat") is not None:
                fat=h["weight"]*h["body_fat"]/100; lean=h["weight"]-fat
                c1,c2=st.columns(2); c1.metric("🟠 Massa grassa stimata",f"{fat:.1f} kg"); c2.metric("💪 Massa magra stimata",f"{lean:.1f} kg")
            st.divider(); st.subheader("🧪 Diagnostica")
            st.info("📱 V15: i dati Google Fit legacy restano disponibili solo per diagnosi. I passi derivati (es. 6.299) e le calorie legacy (es. 3.789 kcal) NON vengono usati nel bilancio perché non possiamo dimostrare che rappresentino correttamente il Galaxy Watch Ultra 2. Il BMR Google Fit non viene usato: per ora il calcolo usa il profilo. Il provider definitivo sarà Samsung Health → Health Connect → componente Android nativo.")
            diag_view=st.session_state.get("diagnostics",{})
            comp_steps=diag_view.get("_source_compare_steps",[])
            comp_cal=diag_view.get("_source_compare_calories",[])
            if comp_steps:
                with st.expander("📱 Confronto sorgenti — PASSI",expanded=True):
                    st.dataframe(pd.DataFrame(comp_steps),use_container_width=True,hide_index=True)
            if comp_cal:
                with st.expander("🔥 Confronto sorgenti — CALORIE"):
                    st.dataframe(pd.DataFrame(comp_cal),use_container_width=True,hide_index=True)
            if diag_view.get("_source_compare_error"):
                st.warning(f"⚠️ Errore confronto sorgenti: {diag_view['_source_compare_error']}")
            for k,x in st.session_state.diagnostics.items():
                # V11 adds special comparison entries whose values are lists.
                # They are rendered above in their own tables, so skip them
                # here instead of treating them as normal metric diagnostics.
                if k.startswith("_source_compare_"):
                    continue
                if not isinstance(x,dict) or "status" not in x:
                    continue
                if x["status"]=="available":
                    if x.get("source_id"):
                        st.success(f"✓ {k}: dati trovati · {x['type']} · {x.get('points',0)} punti")
                        st.caption(f"Sorgente usata: `{x.get('source_label',x['source_id'])}` · {x.get('source_reason','')}")
                        lq=x.get("live_query") or {}
                        if lq.get("method")=="raw_dataset":
                            st.caption(f"📡 Live oggi: dataset grezzo · {lq.get('points',0)} punti · valore {lq.get('value')}")
                            if lq.get("raw_rows"):
                                with st.expander(f"🔎 Punti grezzi di oggi — {k} ({len(lq['raw_rows'])})"):
                                    st.dataframe(pd.DataFrame(lq["raw_rows"]),use_container_width=True,hide_index=True)
                                    if k=="calories" and lq.get("raw_sum") is not None:
                                        st.caption(f"Somma grezza: {lq['raw_sum']:.1f} · valore usato nel live: {lq.get('value')}")
                        elif lq.get("method")=="raw_dataset_failed":
                            st.warning(f"⚠️ Dataset grezzo non leggibile (HTTP {lq.get('http')}).")
                    else:
                        st.success(f"✓ {k}: dati trovati · {x['type']} · {x.get('points',0)} punti")
                elif x["status"]=="no_data": st.warning(f"○ {k}: nessun dato restituito · {x['type']}")
                elif x["status"]=="source_error": st.error(f"✕ {k}: impossibile leggere l'elenco delle sorgenti · HTTP {x.get('http','')} · {x.get('detail','')}")
                else: st.error(f"✕ {k}: HTTP {x.get('http','')} · {x.get('detail','')}")
            hdiag=st.session_state.health
            if hdiag.get("steps_untrusted_value") is not None:
                st.warning(f"⚠️ Passi Google Fit non verificati: {round(float(hdiag['steps_untrusted_value'])):,}. Valore escluso dal Dashboard perché non attribuibile con certezza al Galaxy Watch.")
            if hdiag.get("calories_untrusted_value") is not None:
                st.warning(f"⚠️ Calorie Google Fit non verificate: {float(hdiag['calories_untrusted_value']):,.1f} kcal. Valore escluso dal budget alimentare finché non arriva da Health Connect.")
            source_tabs=[k for k in ("steps","calories") if st.session_state.health.get("source_catalogs",{}).get(k)]
            if source_tabs:
                with st.expander("🔎 Sorgenti rilevate da Google Fit"):
                    for key in source_tabs:
                        st.markdown(f"**{key}**")
                        rows=st.session_state.health["source_catalogs"][key]
                        st.dataframe(pd.DataFrame(rows)[["name","type","app","device","id"]],use_container_width=True,hide_index=True)
                    st.caption("Le sorgenti visibili dipendono dagli scope OAuth e dall'account. Google Fit documenta che l'aggregate per dataTypeName include tutte le sorgenti che forniscono quel tipo; per questo V6 preferisce uno stream derivato specifico quando disponibile.")
            source_values=st.session_state.health.get("source_values",{})
            if source_values:
                with st.expander("🧩 Valori per singola sorgente (diagnostica)"):
                    st.caption("Qui leggiamo separatamente le sorgenti visibili. Non vengono sommate: servono per capire da dove nasce il valore riconciliato di Google Fit.")
                    for key,rows in source_values.items():
                        if not rows: continue
                        unit="passi" if key=="steps" else "kcal"
                        st.markdown(f"**{key} — valori di oggi**")
                        display=[]
                        for row in rows:
                            display.append({
                                "device":row.get("device") or "—",
                                "app":row.get("app") or "—",
                                "nome":row.get("name") or "—",
                                "valore":("{:.1f} {}".format(row["value"],unit) if row.get("value") is not None else row.get("error","nessun dato")),
                            })
                        st.dataframe(pd.DataFrame(display),use_container_width=True,hide_index=True)
                        st.caption("La riga ✓ è lo stream attualmente scelto da MyDietApp. I dettagli sotto mostrano i singoli intervalli temporali restituiti da Google Fit.")
                        for row in rows:
                            pts=row.get("points_today",[])
                            if not pts: continue
                            label=row.get("name") or row.get("id") or "sorgente"
                            mark="✓ SCELTA" if row.get("selected") else ""
                            with st.expander(f"{label} {mark} · {len(pts)} intervalli"):
                                pdf=pd.DataFrame(pts)
                                if not pdf.empty:
                                    pdf["value"]=pdf["value"].round(1)
                                    st.dataframe(pdf,use_container_width=True,hide_index=True)
                                    st.caption(f"Somma degli intervalli mostrati: {pdf['value'].sum():,.1f}".replace(",","."))

            comparisons=st.session_state.health.get("source_comparisons",{})
            if comparisons:
                with st.expander("🧭 Confronto: stream scelto vs tutte le sorgenti"):
                    for key,cmp in comparisons.items():
                        sel=cmp.get("selected"); alls=cmp.get("all_sources"); diff=cmp.get("difference")
                        if sel is not None and alls is not None:
                            unit="passi" if key=="steps" else "kcal"
                            st.write(f"**{key}** — stream scelto: **{sel:,.1f} {unit}** · tutte le sorgenti: **{alls:,.1f} {unit}**".replace(",","."))
                            if diff and abs(diff)>0.1:
                                st.warning(f"Differenza rilevata: **{diff:,.1f} {unit}**. Questo è esattamente il caso che V6 vuole rendere visibile invece di sommare automaticamente.".replace(",","."))
                            else:
                                st.success("Nessuna differenza significativa tra i due risultati per oggi.")
            if h.get("weight") is None or h.get("body_fat") is None:
                st.caption("ℹ️ Peso e composizione corporea non risultano disponibili nella sorgente Google Fit attuale. Il profilo locale resta il fallback per il calcolo energetico.")
            with st.expander("🧱 Architettura Health — V14"):
                st.write("**Contratto normalizzato:** passi · calorie totali · calorie attive · distanza · peso · massa grassa · massa magra · allenamenti · frequenza cardiaca · sonno.")
                st.info("Dashboard, bilancio e attività ora dipendono dal concetto di **Health Provider**, non direttamente da Google Fit. Il provider attuale è Google Fit legacy; quello di produzione previsto è Health Connect tramite componente Android nativo.")
                st.caption("Quando il bridge Android sarà pronto, potremo sostituire il provider senza riscrivere la logica dell'app.")
            metric=st.selectbox("Storico",["steps","calories","weight","body_fat","distance"]); hh=st.session_state.health_history.get(metric,[])
            if hh:
                df=pd.DataFrame(hh); df["date"]=pd.to_datetime(df["date"]); st.line_chart(df.set_index("date")["value"])
        elif "access_token" in st.session_state:
            st.info("Premi 'Sincronizza dati reali' per leggere i dati disponibili.")

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
        st.info(f"🔥 Con i dati Health di oggi, il budget dinamico è circa **{b['live_target']:,} kcal/giorno**: consumo osservato {b['observed_burn']:,} − deficit {b['deficit']} kcal.")
    st.caption("Il target di profilo è una stima basata su Mifflin-St Jeor + livello di attività + deficit scelto. Il budget dinamico usa il consumo totale osservato da Health quando disponibile. Non è una prescrizione medica.")
