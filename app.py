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
# MyDietApp v3
# Health-first release:
# - real Google Fit data diagnostics
# - robust aggregation for cumulative vs point data
# - automatic BMR / calorie target calculation
# - Home separates intake, target and observed expenditure
# - Python owns local food/calorie state
# ============================================================
st.set_page_config(page_title="MyDietApp", page_icon="💪", layout="wide")
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
    observed=float(h.get("calories_today") or 0)
    bmr_health=float(h.get("bmr") or 0)
    return {
        "target":e["target"], "eaten":eaten, "remaining":e["target"]-eaten,
        "observed_burn":round(observed), "bmr_est":e["bmr_est"],
        "bmr_health":round(bmr_health) if bmr_health else None,
        "maintenance":e["maintenance_est"], "deficit":e["deficit"]
    }

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

def aggregate(dtype,start_ms,end_ms):
    body={"aggregateBy":[{"dataTypeName":dtype}],"bucketByTime":{"durationMillis":86400000},"startTimeMillis":start_ms,"endTimeMillis":end_ms}
    r=fit("POST",FIT_BASE+"/dataset:aggregate",json=body)
    if r.status_code!=200:return None,r
    return r.json(),r

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

def sync_health(days=14):
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
    data={}; hist={}; diag={}
    for key,(dtype,mode) in specs.items():
        try:
            payload,r=aggregate(dtype,sm,em)
            if payload is None:
                data[key]=None; hist[key]=[]; diag[key]={"status":"error","http":r.status_code,"type":dtype,"detail":r.text[:500]}
            else:
                h=daily_sum(payload) if mode=="sum" else daily_latest(payload)
                hist[key]=h
                data[key]=h[-1]["value"] if h else None
                diag[key]={"status":"available" if h else "no_data","http":200,"type":dtype,"points":len(points_from(payload))}
        except Exception as e:
            data[key]=None; hist[key]=[]; diag[key]={"status":"error","type":dtype,"detail":str(e)}
    t=today()
    data["steps_today"]=next((x["value"] for x in hist["steps"] if x["date"]==t),None)
    data["calories_today"]=next((x["value"] for x in hist["calories"] if x["date"]==t),None)
    dist=next((x["value"] for x in hist["distance"] if x["date"]==t),None)
    data["distance_today"]=dist/1000 if dist is not None else None
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
    b=balance(); cls="ok" if b["remaining"]>=0 else "bad"
    msg=(f"Ti restano {b['remaining']:,} kcal" if b["remaining"]>=0 else f"{abs(b['remaining']):,} kcal oltre il budget").replace(",",".")
    st.markdown(f'<div class="hero"><div class="muted">BENVENUTO</div><h1>Ciao {st.session_state.p_name} 👋</h1><b>Dimagrimento con attenzione alla massa muscolare</b></div>',unsafe_allow_html=True)
    st.subheader("🔥 Bilancio di oggi")
    st.markdown(f'''<div class="card"><div class="muted">CALORIE ASSUNTE / OBIETTIVO</div>
    <div class="big">{b["eaten"]:,} / {b["target"]:,} kcal</div>
    <div class="muted">Obiettivo calcolato · deficit {b["deficit"]} kcal · BMR stimato {b["bmr_est"]} kcal</div>
    <div class="{cls}">{msg}</div></div>'''.replace(",","."),unsafe_allow_html=True)
    st.progress(min(max(b["eaten"]/max(b["target"],1),0),1))
    c1,c2,c3=st.columns(3)
    with c1: st.metric("⚖️ Peso",f"{st.session_state.health['weight']:.1f} kg" if st.session_state.health.get("weight") else "—")
    with c2: st.metric("👣 Passi",f"{int(st.session_state.health.get('steps_today') or 0):,}".replace(",","."))
    with c3: st.metric("🔥 Consumo rilevato",f"{int(st.session_state.health.get('calories_today') or 0):,} kcal".replace(",","."))
    if b["observed_burn"]:
        st.caption(f"Google Fit ha rilevato {b['observed_burn']:,} kcal di consumo oggi. Questo dato è mostrato separatamente dal budget alimentare.")
    st.subheader("🍽️ Oggi")
    daynames=["Lunedì","Martedì","Mercoledì","Giovedì","Venerdì","Sabato","Domenica"]; d=daynames[date.today().weekday()]
    ms=st.session_state.meal_plan.get(d) or next(iter(st.session_state.meal_plan.values()))
    for mn,m in list(ms.items())[:2]:
        st.write(f"**{mn}** · {m.get('name','Pasto')} · {round(sum(float(i['kcal'])*float(st.session_state.overrides.get(i['id'],{}).get('multiplier',1)) for i in active_items(m)))} kcal")
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
    cid=st.secrets.get("GOOGLE_CLIENT_ID"); cs=st.secrets.get("GOOGLE_CLIENT_SECRET"); ru=st.secrets.get("REDIRECT_URI")
    if not cid or not cs or not ru: st.error("Mancano GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET o REDIRECT_URI nei secrets.")
    else:
        if "code" in st.query_params:
            r=requests.post("https://oauth2.googleapis.com/token",data={"client_id":cid,"client_secret":cs,"code":st.query_params["code"],"grant_type":"authorization_code","redirect_uri":ru},timeout=20)
            if r.status_code==200:
                x=r.json(); st.session_state.access_token=x["access_token"]
                if x.get("refresh_token"):st.session_state.refresh_token=x["refresh_token"]
                st.query_params.clear(); st.rerun()
            else: st.error(r.text[:500])
        if "access_token" not in st.session_state:
            url="https://accounts.google.com/o/oauth2/v2/auth?client_id="+urllib.parse.quote(cid.strip())+"&redirect_uri="+urllib.parse.quote(ru.strip(),safe="")+"&response_type=code&scope="+urllib.parse.quote(FIT_SCOPES,safe="")+"&access_type=offline&prompt=consent"
            st.link_button("🔗 Collega Google Health / Fit",url,use_container_width=True)
        else:
            st.success("Account collegato")
            if st.button("🔄 Sincronizza dati reali",type="primary",use_container_width=True):
                try:
                    data,hist,diag=sync_health(); st.session_state.health=data; st.session_state.health_history=hist; st.session_state.diagnostics=diag; st.session_state.last_sync=datetime.now(ROME).strftime("%d/%m/%Y %H:%M"); st.rerun()
                except Exception as e: st.error(f"Sincronizzazione fallita: {e}")
            h=st.session_state.health
            if h:
                cards=[("👣 Passi oggi",h.get("steps_today"),"passi"),("🔥 Calorie oggi",h.get("calories_today"),"kcal"),("⚖️ Peso",h.get("weight"),"kg"),("🟠 Massa grassa",h.get("body_fat"),"%"),("📏 Distanza",h.get("distance_today"),"km"),("🧬 BMR",h.get("bmr"),"kcal/giorno")]
                cc=st.columns(3)
                for i,(lab,val,unit) in enumerate(cards):
                    with cc[i%3]: st.metric(lab,"Non disponibile" if val is None else f"{val:.1f} {unit}")
                if h.get("weight") is not None and h.get("body_fat") is not None:
                    fat=h["weight"]*h["body_fat"]/100; lean=h["weight"]-fat
                    c1,c2=st.columns(2); c1.metric("🟠 Massa grassa stimata",f"{fat:.1f} kg"); c2.metric("💪 Massa magra stimata",f"{lean:.1f} kg")
                st.divider(); st.subheader("🧪 Diagnostica")
                for k,x in st.session_state.diagnostics.items():
                    if x["status"]=="available": st.success(f"✓ {k}: dati trovati · {x['type']} · {x.get('points',0)} punti")
                    elif x["status"]=="no_data": st.warning(f"○ {k}: nessun dato restituito · {x['type']}")
                    else: st.error(f"✕ {k}: HTTP {x.get('http','')} · {x.get('detail','')}")
                metric=st.selectbox("Storico",["steps","calories","weight","body_fat","distance"]); hh=st.session_state.health_history.get(metric,[])
                if hh:
                    df=pd.DataFrame(hh); df["date"]=pd.to_datetime(df["date"]); st.line_chart(df.set_index("date")["value"])
            else:
                st.info("Premi 'Sincronizza dati reali' per leggere i dati disponibili.")

# ---------------- Profilo ----------------
else:
    st.title("👤 Profilo")
    ep=energy_profile()
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
    st.subheader("🎯 Obiettivo energetico")
    st.metric("Target alimentare stimato",f"{ep['target']:,} kcal/giorno".replace(",","."))
    c1,c2=st.columns(2); c1.metric("BMR stimato",f"{ep['bmr_est']:,} kcal".replace(",",".")); c2.metric("Mantenimento stimato",f"{ep['maintenance_est']:,} kcal".replace(",","."))
    st.caption("Il target è una stima basata su Mifflin-St Jeor + livello di attività + deficit scelto. Non è una prescrizione medica. I dati reali di Health vengono mostrati separatamente e serviranno a rendere il motore più preciso nelle prossime versioni.")
