import streamlit as st
from google import genai
import io
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
import copy
import re
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# MyDietApp v66 SMART SHOPPING LIVE
# V57: next-week plan is a separate editable draft; active week stays untouched until activation.
# V50 FIX: sincronizzazione Home/Piano dello stato pasti e reset checkbox robusto
# V54: one primary meal-registration action in "Cosa mangio oggi?"; daily list is status/undo only.
# V47: meal-level registration in Piano uses the same eaten state as Home; no changes to Health, energy balance, water or pantry logic.
# - daily lunch/dinner recommendations linked to the active plan
# - generic fuori-casa configuration for lunch/dinner, independent from the canteen
# - recommendations adapt to the current dynamic calorie budget
# - Pasto fuori receives the planned meal and available calorie context
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
GEMINI_MODEL = "gemini-3.6-flash"
gemini_client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])

def gemini_interaction(prompt, image=None, thinking_level=None):
    """Call Gemini via the current Interactions API. Supports text and optional image input."""
    generation_config = {"thinking_level": thinking_level} if thinking_level else None
    if image is None:
        kwargs = {"model": GEMINI_MODEL, "input": prompt}
        if generation_config:
            kwargs["generation_config"] = generation_config
        interaction = gemini_client.interactions.create(**kwargs)
    else:
        if hasattr(image, "getvalue"):
            image_bytes = image.getvalue()
        else:
            buf = io.BytesIO()
            image.save(buf, format="JPEG")
            image_bytes = buf.getvalue()
        mime_type = getattr(image, "type", None) or "image/jpeg"
        if mime_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
            mime_type = "image/jpeg"
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        kwargs = {
            "model": GEMINI_MODEL,
            "input": [
                {"type": "text", "text": prompt},
                {"type": "image", "data": image_b64, "mime_type": mime_type},
            ],
        }
        if generation_config:
            kwargs["generation_config"] = generation_config
        interaction = gemini_client.interactions.create(**kwargs)
    return interaction.output_text.strip()



# ============================================================
# Smart Shopping live data
# ============================================================
SMART_SHOPPING_STORES = [
    "Conad", "Esselunga", "Carrefour", "Eurospin", "Tosano", "Lidl",
    "Iper", "Tigros", "Coop", "Penny", "Aldi", "PAM", "Bennet",
    "Sigma", "Il Gigante", "Famila"
]


def _money_value(value):
    try:
        return float(str(value).replace("€", "").replace(".", "").replace(",", ".").strip())
    except Exception:
        return None


def _comprissimo_search_url(product_name, sort="unit_price"):
    q = urllib.parse.quote_plus(str(product_name).strip())
    return (
        "https://comprissimo.ai/search?brand=&category=&has_price=True&on_sale=False"
        f"&page=1&per_page=24&q={q}&sort={sort}&supermarket="
    )


def _clean_spaces(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _tokens(text):
    return set(re.findall(r"[a-zàèéìòù0-9]+", str(text).lower()))


def _match_score(query, product):
    q = _tokens(query)
    p = _tokens(product)
    if not q or not p:
        return 0.0
    return len(q & p) / max(1, len(q))


def _parse_comprissimo_search(html, query, limit=8):
    """Parse the public Comprissimo catalog page without inventing prices.

    The parser is deliberately conservative: a row is returned only when a
    product name, a euro price and a unit price are all visible in the page.
    """
    soup = BeautifulSoup(html, "html.parser")
    products = []
    seen = set()
    for h3 in soup.find_all("h3"):
        name = _clean_spaces(h3.get_text(" ", strip=True))
        if not name or name.lower() in {"catalogo prodotti", "confronto prezzi"}:
            continue
        card = h3
        card_text = ""
        # Walk up only a few levels: this avoids swallowing the whole page.
        for parent in h3.parents:
            if parent.name not in {"div", "article", "li", "section"}:
                continue
            txt = _clean_spaces(parent.get_text(" ", strip=True))
            if len(txt) <= 1800 and "Aggiungi" in txt and re.search(r"\d+[,.]\d+\s*€/\s*(kg|L|pz)", txt, re.I):
                card = parent
                card_text = txt
                break
        if not card_text:
            continue

        unit_match = re.search(r"(\d+[,.]\d+)\s*€/\s*(kg|L|pz)", card_text, re.I)
        if not unit_match:
            continue
        unit_price = _money_value(unit_match.group(1))
        unit_kind = unit_match.group(2)
        if unit_price is None:
            continue

        before_unit = card_text[:unit_match.start()]
        prices = re.findall(r"(?<!\d)(\d+[,.]\d{2})\s*€", before_unit)
        if not prices:
            continue
        price = _money_value(prices[-1])
        if price is None:
            continue

        stores = [store for store in SMART_SHOPPING_STORES if re.search(rf"\b{re.escape(store)}\b", card_text, re.I)]
        store = stores[0] if stores else ""
        compare_url = ""
        for a in card.find_all("a", href=True):
            href = str(a.get("href", ""))
            if "/compare/" in href:
                compare_url = urllib.parse.urljoin("https://comprissimo.ai", href)
                break
        score = _match_score(query, name)
        # Avoid irrelevant catalog noise.
        if score < 0.34:
            continue
        key = (name.lower(), store.lower(), round(price, 2), round(unit_price, 2))
        if key in seen:
            continue
        seen.add(key)
        products.append({
            "name": name,
            "store": store,
            "price": price,
            "unit_price": unit_price,
            "unit_kind": unit_kind,
            "compare_url": compare_url,
            "search_url": _comprissimo_search_url(query),
            "score": score,
            "source": "Comprissimo",
        })

    products.sort(key=lambda x: (-x["score"], x["unit_price"], x["price"]))
    return products[:limit]


@st.cache_data(ttl=900, show_spinner=False)
def fetch_comprissimo_live(product_name):
    """Fetch current public Comprissimo search data. Cached for 15 minutes."""
    url = _comprissimo_search_url(product_name)
    try:
        response = requests.get(
            url,
            timeout=15,
            headers={"User-Agent": "MyDietApp/66 (+smart-shopping)"},
        )
        response.raise_for_status()
        return _parse_comprissimo_search(response.text, product_name, limit=8)
    except Exception:
        return []


def _shopping_live_for_items(items, max_items=10):
    """Fetch live results concurrently, but cap requests to keep the app responsive."""
    selected = items[:max_items]
    out = {}
    if not selected:
        return out
    with ThreadPoolExecutor(max_workers=min(5, len(selected))) as pool:
        futures = {pool.submit(fetch_comprissimo_live, str(r["name"]).strip()): r["name"] for r in selected}
        for future in as_completed(futures):
            name = futures[future]
            try:
                out[name] = future.result()
            except Exception:
                out[name] = []
    return out


def _shopping_cart_summary(live_results, items):
    """Build a conservative store summary from products actually matched."""
    store_totals = defaultdict(float)
    store_products = defaultdict(int)
    for r in items:
        matches = live_results.get(r["name"], [])
        if not matches:
            continue
        best_by_store = {}
        for m in matches:
            store = m.get("store") or ""
            if not store:
                continue
            current = best_by_store.get(store)
            if current is None or m["unit_price"] < current["unit_price"]:
                best_by_store[store] = m
        # We cannot know how many packages are needed without packaging data;
        # therefore use the observed unit-price ranking only and never invent a cart total.
        for store, m in best_by_store.items():
            store_products[store] += 1
    ranking = sorted(store_products.items(), key=lambda x: (-x[1], x[0]))
    return [{"store": store, "matched": count} for store, count in ranking]

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
        "active_calories_source_verified": bool(payload.get("trust", {}).get("active_calories", False)),
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
    # Piano demo completo per tutti i 7 giorni. In questo modo la Home non rimane
    # senza il giorno corrente quando una nuova sessione Streamlit viene aperta.
    return {
        "Lunedì": {
            "☕ Colazione":{"name":"Yogurt, avena e frutta","ingredients":[ing("Yogurt greco",250,"g",180),ing("Avena",50,"g",190),ing("Miele",10,"g",30)]},
            "🍎 Spuntino":{"name":"Mela e mandorle","ingredients":[ing("Mela",180,"g",95),ing("Mandorle",15,"g",90)]},
            "🍽️ Pranzo":{"name":"Riso, pollo e verdure","ingredients":[ing("Riso basmati",90,"g",320),ing("Petto di pollo",180,"g",300),ing("Zucchine",250,"g",45),ing("Olio EVO",10,"g",90)]},
            "🌙 Cena":{"name":"Uova, pane e verdure","ingredients":[ing("Uova",2,"pz",150),ing("Pane integrale",70,"g",175),ing("Insalata mista",250,"g",50),ing("Olio EVO",10,"g",90)]}
        },
        "Martedì": {
            "☕ Colazione":{"name":"Yogurt, banana e avena","ingredients":[ing("Yogurt greco",250,"g",180),ing("Banana",120,"g",105),ing("Avena",40,"g",152)]},
            "🍎 Spuntino":{"name":"Frutta e noci","ingredients":[ing("Pera",180,"g",100),ing("Noci",15,"g",98)]},
            "🍽️ Pranzo":{"name":"Pasta al pomodoro e tacchino","ingredients":[ing("Pasta",90,"g",320),ing("Passata di pomodoro",150,"g",45),ing("Fesa di tacchino",180,"g",200),ing("Olio EVO",10,"g",90)]},
            "🌙 Cena":{"name":"Salmone, patate e verdure","ingredients":[ing("Salmone",160,"g",330),ing("Patate",250,"g",190),ing("Verdure miste",250,"g",70),ing("Olio EVO",5,"g",45)]}
        },
        "Mercoledì": {
            "☕ Colazione":{"name":"Pane, ricotta e frutta","ingredients":[ing("Pane integrale",80,"g",200),ing("Ricotta",100,"g",170),ing("Frutta",150,"g",80)]},
            "🍎 Spuntino":{"name":"Yogurt e frutta","ingredients":[ing("Yogurt greco",170,"g",120),ing("Frutti di bosco",100,"g",45)]},
            "🍽️ Pranzo":{"name":"Riso, tacchino e broccoli","ingredients":[ing("Riso basmati",90,"g",320),ing("Fesa di tacchino",180,"g",200),ing("Broccoli",250,"g",85),ing("Olio EVO",10,"g",90)]},
            "🌙 Cena":{"name":"Frittata e pane","ingredients":[ing("Uova",3,"pz",225),ing("Pane integrale",70,"g",175),ing("Spinaci",250,"g",60),ing("Olio EVO",5,"g",45)]}
        },
        "Giovedì": {
            "☕ Colazione":{"name":"Yogurt, avena e mela","ingredients":[ing("Yogurt greco",250,"g",180),ing("Avena",50,"g",190),ing("Mela",150,"g",80)]},
            "🍎 Spuntino":{"name":"Banana e mandorle","ingredients":[ing("Banana",120,"g",105),ing("Mandorle",15,"g",90)]},
            "🍽️ Pranzo":{"name":"Pollo, riso e verdure","ingredients":[ing("Petto di pollo",180,"g",300),ing("Riso basmati",80,"g",285),ing("Verdure miste",250,"g",70),ing("Olio EVO",10,"g",90)]},
            "🌙 Cena":{"name":"Merluzzo, patate e insalata","ingredients":[ing("Merluzzo",200,"g",170),ing("Patate",250,"g",190),ing("Insalata mista",250,"g",50),ing("Olio EVO",10,"g",90)]}
        },
        "Venerdì": {
            "☕ Colazione":{"name":"Yogurt, banana e avena","ingredients":[ing("Yogurt greco",250,"g",180),ing("Banana",120,"g",105),ing("Avena",40,"g",152)]},
            "🍎 Spuntino":{"name":"Mela e noci","ingredients":[ing("Mela",180,"g",95),ing("Noci",15,"g",98)]},
            "🍽️ Pranzo":{"name":"Pasta, tonno e pomodoro","ingredients":[ing("Pasta",90,"g",320),ing("Tonno al naturale",120,"g",130),ing("Passata di pomodoro",150,"g",45),ing("Olio EVO",10,"g",90)]},
            "🌙 Cena":{"name":"Pollo, pane e verdure","ingredients":[ing("Petto di pollo",180,"g",300),ing("Pane integrale",70,"g",175),ing("Verdure miste",300,"g",80),ing("Olio EVO",10,"g",90)]}
        },
        "Sabato": {
            "☕ Colazione":{"name":"Uova, pane e frutta","ingredients":[ing("Uova",2,"pz",150),ing("Pane integrale",70,"g",175),ing("Frutta",150,"g",80)]},
            "🍎 Spuntino":{"name":"Yogurt e mandorle","ingredients":[ing("Yogurt greco",170,"g",120),ing("Mandorle",15,"g",90)]},
            "🍽️ Pranzo":{"name":"Pasta al ragù leggero e insalata","ingredients":[ing("Pasta",90,"g",320),ing("Carne macinata magra",150,"g",250),ing("Passata di pomodoro",150,"g",45),ing("Insalata mista",200,"g",40)]},
            "🌙 Cena":{"name":"Salmone, riso e verdure","ingredients":[ing("Salmone",150,"g",310),ing("Riso basmati",70,"g",250),ing("Verdure miste",250,"g",70),ing("Olio EVO",5,"g",45)]}
        },
        "Domenica": {
            "☕ Colazione":{"name":"Yogurt, avena e frutta","ingredients":[ing("Yogurt greco",250,"g",180),ing("Avena",50,"g",190),ing("Frutta",150,"g",80)]},
            "🍎 Spuntino":{"name":"Frutta e noci","ingredients":[ing("Pera",180,"g",100),ing("Noci",15,"g",98)]},
            "🍽️ Pranzo":{"name":"Pollo al forno, patate e verdure","ingredients":[ing("Petto di pollo",200,"g",330),ing("Patate",250,"g",190),ing("Verdure miste",250,"g",70),ing("Olio EVO",10,"g",90)]},
            "🌙 Cena":{"name":"Uova, pane e insalata","ingredients":[ing("Uova",2,"pz",150),ing("Pane integrale",70,"g",175),ing("Insalata mista",300,"g",60),ing("Olio EVO",10,"g",90)]}
        }
    }


_defaults = {
    "page":"Home", "meal_plan":init_plan(), "overrides":{}, "eaten":{}, "manual_foods":[],
    "health":{}, "health_history":{}, "diagnostics":{}, "last_sync":None, "water_history":{},
    "plan_week_start":None, "plan_history":{}, "out_lunch_days":["Giovedì"], "out_dinner_days":["Giovedì"],
    "next_meal_plan":None, "next_overrides":{}, "next_week_start":None,
    "next_out_lunch_days":[], "next_out_dinner_days":[],
    "mensa_menus":{}, "next_mensa_menus":{},
    "plan_generation_status":"idle", "plan_generation_message":"", "plan_generation_time":None,
    "plan_editor_selection":"current", "_plan_editor_next":False,
    "pantry":{}, "shopping_checked":{}, "pantry_consumed_by_meal":{}, "smart_food_advice":None, "registered_meals":{}, "shopping_source":"Tutte", "shopping_strategy":"⚖️ Qualità / prezzo", "shopping_radius":5
}
for k,v in _defaults.items(): st.session_state.setdefault(k,v)
for k,v in {
    "name":"Stefano", "weight":135.0, "height":180.0, "age":40, "sex":"male",
    "activity_level":"moderata", "deficit":500, "goal_weight":135.0, "water_goal_ml":2500, "quantity_mode":"porzioni"
}.items(): st.session_state.setdefault("p_"+k,v)
# Compatibilità: se il profilo arriva da una versione precedente, il peso desiderato parte dal peso attuale.
st.session_state.setdefault("p_goal_weight", float(st.session_state.get("p_weight", 135.0)))


def meals():
    for day, ms in st.session_state.meal_plan.items():
        for name, meal in ms.items(): yield day,name,meal

def week_start(d=None):
    d = d or datetime.now(ROME).date()
    return (d - timedelta(days=d.weekday())).isoformat()

def week_label(start_iso):
    start=date.fromisoformat(start_iso)
    end=start+timedelta(days=6)
    return f"{start.strftime('%d/%m/%Y')} – {end.strftime('%d/%m/%Y')}"

def ensure_plan_metadata():
    if not st.session_state.get("plan_week_start"):
        st.session_state.plan_week_start=week_start()

def mensa_key(day, meal_name):
    return f"{day}::{meal_name}"

def current_mensa_menu(day, meal_name):
    return st.session_state.get("mensa_menus", {}).get(mensa_key(day, meal_name))

def set_mensa_menu(day, meal_name, result_text, analyzed_at=None):
    st.session_state.setdefault("mensa_menus", {})[mensa_key(day, meal_name)] = {
        "day": day,
        "meal": meal_name,
        "result": result_text,
        "analyzed_at": analyzed_at or datetime.now(ROME).strftime("%d/%m/%Y %H:%M"),
    }

def save_next_editor_context():
    if not st.session_state.get("_plan_editor_next"):
        return
    st.session_state.next_meal_plan=st.session_state.meal_plan
    st.session_state.next_overrides=st.session_state.overrides
    st.session_state.next_out_lunch_days=copy.deepcopy(st.session_state.get("out_lunch_days", []))
    st.session_state.next_out_dinner_days=copy.deepcopy(st.session_state.get("out_dinner_days", []))
    st.session_state.next_mensa_menus=copy.deepcopy(st.session_state.get("mensa_menus", {}))

def restore_current_plan_context():
    if not st.session_state.get("_plan_editor_next"):
        return
    save_next_editor_context()
    current=st.session_state.get("_editor_current_meal_plan")
    current_overrides=st.session_state.get("_editor_current_overrides")
    if current is not None:
        st.session_state.meal_plan=current
    if current_overrides is not None:
        st.session_state.overrides=current_overrides
    st.session_state.out_lunch_days=copy.deepcopy(st.session_state.get("_editor_current_out_lunch_days", []))
    st.session_state.out_dinner_days=copy.deepcopy(st.session_state.get("_editor_current_out_dinner_days", []))
    st.session_state.mensa_menus=copy.deepcopy(st.session_state.get("_editor_current_mensa_menus", {}))
    st.session_state.eaten=st.session_state.get("_editor_current_eaten", {})
    st.session_state.registered_meals=st.session_state.get("_editor_current_registered_meals", {})
    st.session_state._plan_editor_next=False

def enter_next_plan_editor():
    if not st.session_state.get("next_meal_plan"):
        return False
    if st.session_state.get("_plan_editor_next"):
        return True
    st.session_state._editor_current_meal_plan=st.session_state.meal_plan
    st.session_state._editor_current_overrides=st.session_state.overrides
    st.session_state._editor_current_out_lunch_days=copy.deepcopy(st.session_state.get("out_lunch_days", []))
    st.session_state._editor_current_out_dinner_days=copy.deepcopy(st.session_state.get("out_dinner_days", []))
    st.session_state._editor_current_mensa_menus=copy.deepcopy(st.session_state.get("mensa_menus", {}))
    st.session_state._editor_current_eaten=st.session_state.eaten
    st.session_state._editor_current_registered_meals=st.session_state.registered_meals
    st.session_state.meal_plan=st.session_state.next_meal_plan
    st.session_state.overrides=st.session_state.next_overrides
    st.session_state.out_lunch_days=copy.deepcopy(st.session_state.get("next_out_lunch_days", []))
    st.session_state.out_dinner_days=copy.deepcopy(st.session_state.get("next_out_dinner_days", []))
    st.session_state.mensa_menus=copy.deepcopy(st.session_state.get("next_mensa_menus", {}))
    st.session_state.eaten={}
    st.session_state.registered_meals={}
    st.session_state._plan_editor_next=True
    return True

def maybe_activate_next_plan():
    next_start=st.session_state.get("next_week_start")
    if not next_start or not st.session_state.get("next_meal_plan"):
        return False
    if date.fromisoformat(next_start) > date.fromisoformat(week_start(datetime.now(ROME).date())):
        return False
    restore_current_plan_context()
    archive_current_plan(reason="Settimana precedente sostituita dal piano preparato")
    st.session_state.meal_plan=st.session_state.next_meal_plan
    st.session_state.plan_week_start=next_start
    st.session_state.overrides=st.session_state.next_overrides or {}
    st.session_state.out_lunch_days=copy.deepcopy(st.session_state.next_out_lunch_days)
    st.session_state.out_dinner_days=copy.deepcopy(st.session_state.next_out_dinner_days)
    st.session_state.mensa_menus=copy.deepcopy(st.session_state.next_mensa_menus)
    st.session_state.next_meal_plan=None
    st.session_state.next_overrides={}
    st.session_state.next_week_start=None
    st.session_state.next_out_lunch_days=[]
    st.session_state.next_out_dinner_days=[]
    st.session_state.next_mensa_menus={}
    st.session_state.eaten={}
    st.session_state.registered_meals={}
    for key in list(st.session_state.keys()):
        if str(key).startswith("eat_"):
            st.session_state.pop(key,None)
    return True

def archive_current_plan(reason="Nuovo piano"):
    ensure_plan_metadata()
    start=st.session_state.plan_week_start
    if not st.session_state.meal_plan:
        return None
    archive_id=f"{start}_{datetime.now(ROME).strftime('%Y%m%d%H%M%S')}"
    st.session_state.plan_history[archive_id]={
        "week_start":start,
        "label":week_label(start),
        "created_at":datetime.now(ROME).isoformat(),
        "reason":reason,
        "out_lunch_days":copy.deepcopy(st.session_state.get("out_lunch_days", [])),
        "out_dinner_days":copy.deepcopy(st.session_state.get("out_dinner_days", [])),
        "plan":copy.deepcopy(st.session_state.meal_plan),
    }
    return archive_id


maybe_activate_next_plan()

def normalize_ai_plan(raw):
    """Normalize Gemini's weekly-plan JSON into the internal MyDiet structure."""
    if isinstance(raw, str):
        raw=raw.strip()
        if raw.startswith("```"):
            raw=raw.replace("```json","").replace("```","").strip()
        raw=json.loads(raw)

    if isinstance(raw, dict):
        for wrapper in ("piano","plan","days","settimana","weekly_plan"):
            candidate=raw.get(wrapper)
            if isinstance(candidate,(dict,list)):
                raw=candidate
                break

    day_names=["Lunedì","Martedì","Mercoledì","Giovedì","Venerdì","Sabato","Domenica"]
    day_aliases={d.lower():d for d in day_names}
    out={}

    def normalize_meal(meal):
        if not isinstance(meal,dict):
            raise ValueError("Un pasto AI non è un oggetto JSON valido.")
        ingredients=meal.get("ingredients",[])
        if ingredients is None:
            ingredients=[]
        if not isinstance(ingredients,list):
            raise ValueError("Gli ingredienti di un pasto devono essere una lista.")
        normalized=[]
        for x in ingredients:
            if not isinstance(x,dict):
                continue
            normalized.append({
                "id":sid(),
                "name":str(x.get("name","Alimento")).strip() or "Alimento",
                "qty":float(x.get("qty",1)),
                "unit":str(x.get("unit","g")),
                "kcal":round(float(x.get("kcal",0))),
            })
        return {
            "name":str(meal.get("name","Pasto")).strip() or "Pasto",
            "ingredients":normalized,
        }

    if isinstance(raw,dict):
        for raw_day, raw_meals in raw.items():
            canonical=day_aliases.get(str(raw_day).strip().lower())
            if not canonical or not isinstance(raw_meals,dict):
                continue
            out[canonical]={}
            for mn,m in raw_meals.items():
                if isinstance(m,dict):
                    out[canonical][str(mn)]=normalize_meal(m)

    elif isinstance(raw,list):
        for entry in raw:
            if not isinstance(entry,dict):
                continue
            raw_day=entry.get("day") or entry.get("giorno") or entry.get("name")
            canonical=day_aliases.get(str(raw_day).strip().lower()) if raw_day else None
            raw_meals=entry.get("meals") or entry.get("pasti") or entry.get("plan")
            if canonical and isinstance(raw_meals,dict):
                out[canonical]={str(mn):normalize_meal(m) for mn,m in raw_meals.items() if isinstance(m,dict)}
            elif canonical:
                meal_map={}
                labels={
                    "breakfast":"☕ Colazione","colazione":"☕ Colazione",
                    "snack":"🍎 Spuntino","spuntino":"🍎 Spuntino",
                    "lunch":"🍽️ Pranzo","pranzo":"🍽️ Pranzo",
                    "dinner":"🌙 Cena","cena":"🌙 Cena",
                }
                for k,v in entry.items():
                    label=labels.get(str(k).strip().lower())
                    if label and isinstance(v,dict):
                        meal_map[label]=normalize_meal(v)
                if meal_map:
                    out[canonical]=meal_map

    missing=[d for d in day_names if d not in out]
    if missing:
        raise ValueError("Gemini ha restituito un piano incompleto. Giorni mancanti: "+", ".join(missing))

    meal_order=["☕ Colazione","🍎 Spuntino","🍽️ Pranzo","🌙 Cena"]
    for day in day_names:
        normalized_day={}
        for mn in meal_order:
            if mn in out[day]:
                normalized_day[mn]=out[day][mn]
        for mn,m in out[day].items():
            if mn not in normalized_day:
                normalized_day[mn]=m
        if len(normalized_day)<4:
            raise ValueError(f"Il giorno {day} non contiene tutti i 4 pasti previsti.")
        out[day]=normalized_day

    return out

def historical_food_library(limit=None):
    foods={}
    def add_from_plan(plan, source):
        for day, ms in plan.items():
            for meal_name, meal in ms.items():
                for item in meal.get("ingredients",[]):
                    name=str(item.get("name","Alimento")).strip()
                    if not name: continue
                    key=(name.lower(),str(item.get("unit","g")))
                    if key not in foods:
                        foods[key]={"name":name,"qty":float(item.get("qty",1)),"unit":str(item.get("unit","g")),"kcal":float(item.get("kcal",0)),"uses":0,"sources":set()}
                    foods[key]["uses"]+=1; foods[key]["sources"].add(source)
    add_from_plan(st.session_state.meal_plan,"Piano attuale")
    for rec in st.session_state.plan_history.values(): add_from_plan(rec.get("plan",{}),f"Storico {rec.get('label','')}")
    vals=list(foods.values())
    vals.sort(key=lambda x:(-x["uses"],x["name"].lower()))
    for x in vals: x["sources"]=list(x["sources"])
    return vals[:limit] if limit else vals

ensure_plan_metadata()

def item_multiplier(item):
    return float(st.session_state.overrides.get(item["id"],{}).get("multiplier",1))

def item_qty(item):
    return float(item.get("qty",0)) * item_multiplier(item)

def item_kcal(item):
    return float(item.get("kcal",0)) * item_multiplier(item)

def quantity_mode():
    return st.session_state.get("p_quantity_mode", "porzioni")

def quantity_label(item):
    """Human-friendly quantity according to the user's preferred display mode.
    Internal grams/ml/units remain available to the calculation engine.
    """
    mode = quantity_mode()
    q = item_qty(item)
    unit = str(item.get("unit", "g")).lower()
    if mode == "precise":
        return f"{q:g}{unit}"
    portions = item_multiplier(item)
    if abs(portions - 1) < 0.01:
        return "1 porzione"
    if abs(portions - 0.5) < 0.01:
        return "½ porzione"
    if abs(portions - 1.5) < 0.01:
        return "1½ porzioni"
    if abs(portions - 2) < 0.01:
        return "2 porzioni"
    return f"{portions:g} porzioni"

def quantity_caption(item):
    mode = quantity_mode()
    label = quantity_label(item)
    if mode == "both":
        return f"{label} · {item_qty(item):g}{item.get('unit','g')}"
    return label

def qty_step(unit, qty):
    unit=str(unit).lower()
    if unit == "pz": return 1.0
    if unit in ("ml", "g"): return 10.0 if float(qty) >= 20 else 1.0
    return 1.0

def set_item_qty(item, new_qty):
    base=float(item.get("qty",0))
    if base <= 0: return
    minimum=1.0 if str(item.get("unit","g")).lower()=="pz" else 0.1
    q=max(minimum, float(new_qty))
    st.session_state.overrides[item["id"]]={
        "multiplier": q/base
    }

def active_items(meal):
    out=[]
    for item in meal.get("ingredients",[]):
        ov=st.session_state.overrides.get(item["id"],{})
        if ov.get("removed"): continue
        x=dict(item); x["qty"]=item_qty(item); x["kcal"]=item_kcal(item); out.append(x)
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

def plan_food_suggestions(current_day=None, current_meal=None, limit=8):
    """Suggest foods from current and historical plans, preferring current plan and recent reuse."""
    foods={}
    def collect(plan, source_rank, source_label):
        for day, ms in plan.items():
            for meal_name, meal in ms.items():
                for item in meal.get("ingredients",[]):
                    if source_rank==0 and day==current_day and meal_name==current_meal: continue
                    name=str(item.get("name","Alimento")).strip()
                    if not name: continue
                    key=(name.lower(),str(item.get("unit","g")))
                    if key not in foods:
                        foods[key]={"name":name,"qty":float(item.get("qty",1)),"unit":str(item.get("unit","g")),"kcal":float(item.get("kcal",0)),"current":source_rank==0,"uses":0}
                    foods[key]["uses"]+=1
                    if source_rank==0: foods[key]["current"]=True
    collect(st.session_state.meal_plan,0,"Piano attuale")
    for rec in sorted(st.session_state.plan_history.values(), key=lambda x:x.get("created_at", ""), reverse=True):
        collect(rec.get("plan",{}),1,rec.get("label","Storico"))
    values=list(foods.values())
    values.sort(key=lambda x:(not x["current"],-x["uses"],x["name"].lower()))
    return values[:limit]


def _meal_kcal_for_day(day, meal_name):
    ms=st.session_state.meal_plan.get(day,{})
    meal=ms.get(meal_name)
    if not meal:
        return 0
    return round(sum(item_kcal(i) for i in active_items(meal)))


def _planned_remaining_after_meal(day, meal_name):
    """Calories planned for meals after the selected meal, in displayed order."""
    order=["☕ Colazione","🍎 Spuntino","🍽️ Pranzo","🌙 Cena"]
    if meal_name not in order:
        return 0
    idx=order.index(meal_name)
    return sum(_meal_kcal_for_day(day, name) for name in order[idx+1:])


def out_of_home_meal_configured(day, meal_name):
    """Return True when the selected weekday/meal is configured as fuori casa."""
    if meal_name == "🍽️ Pranzo":
        return day in st.session_state.get("out_lunch_days", [])
    if meal_name == "🌙 Cena":
        return day in st.session_state.get("out_dinner_days", [])
    return False

def meal_recommendation(day, meal_name, balance_data=None):
    """Build a deterministic recommendation from the active plan and live calorie budget."""
    ms=st.session_state.meal_plan.get(day,{})
    meal=ms.get(meal_name)
    if not meal:
        return None

    b=balance_data or balance()
    items=active_items(meal)
    planned_kcal=round(sum(item_kcal(i) for i in items))
    planned_name=str(meal.get("name") or meal_name)
    remaining=max(0,int(b.get("remaining") or 0))
    future_planned=_planned_remaining_after_meal(day, meal_name)
    budget_for_meal=max(0, remaining-future_planned)
    after_meal=max(0, remaining-planned_kcal)
    out_of_home=out_of_home_meal_configured(day, meal_name) or "UFFICIO" in planned_name.upper() or "FUORI CASA" in planned_name.upper()

    if out_of_home:
        status="mensa"
        advice="Il piano prevede un pasto fuori casa: se hai un menu disponibile, possiamo confrontarlo con il piano e con il budget di oggi."
    elif planned_kcal <= 0:
        status="unknown"
        advice="Il piano non contiene ancora calorie per questo pasto: usa il budget disponibile come riferimento."
    elif planned_kcal <= budget_for_meal:
        status="good"
        advice=f"🟢 Segui il piano: questo pasto rientra nel budget disponibile. Dopo il pasto resteranno circa {after_meal:,} kcal per il resto della giornata.".replace(",",".")
    elif planned_kcal <= remaining:
        status="adapt"
        advice=f"🟡 Il pasto rientra ancora nel budget giornaliero, ma lascia meno spazio ai pasti successivi. Se vuoi restare più vicino al piano della giornata, valuta una porzione leggermente più leggera." 
    else:
        status="over"
        advice=f"🔴 Il pasto supera di circa {planned_kcal-remaining} kcal il budget ancora disponibile. Meglio ridurre una componente calorica oppure scegliere un'alternativa."

    return {
        "name":planned_name,
        "planned_kcal":planned_kcal,
        "remaining":remaining,
        "future_planned":future_planned,
        "budget_for_meal":budget_for_meal,
        "after_meal":after_meal,
        "advice":advice,
        "status":status,
        "out_of_home":out_of_home,
        "items":items,
        "registered": bool(items) and all(st.session_state.eaten.get(i["id"],False) for i in items),
    }


def eaten_items_today():
    """Return foods actually registered as eaten today, for the smart assistant context."""
    d=current_day_name()
    out=[]
    for meal_name, meal in st.session_state.meal_plan.get(d, {}).items():
        for item in active_items(meal):
            if st.session_state.eaten.get(item["id"], False):
                out.append({"meal":meal_name,"name":item["name"],"kcal":round(item_kcal(item)),"quantity":quantity_caption(item)})
    for x in st.session_state.manual_foods:
        if x.get("date")==today():
            out.append({"meal":"Registrato manualmente","name":x.get("name","Alimento"),"kcal":round(float(x.get("kcal",0))),"quantity":""})
    return out


def _meal_key(day, meal_name):
    return f"{day}::{meal_name}"

def _meal_is_registered(day, meal_name):
    """Single canonical meal-level state shared by Home and Piano."""
    meal=st.session_state.meal_plan.get(day,{}).get(meal_name)
    if not meal:
        return False

    key=_meal_key(day,meal_name)

    # Explicit meal registration is authoritative.
    if key in st.session_state.registered_meals:
        return bool(st.session_state.registered_meals[key])

    # Compatibility with state created before this version.
    items=active_items(meal)
    registered=bool(items) and all(
        st.session_state.eaten.get(i["id"],False)
        for i in items
    )
    st.session_state.registered_meals[key]=registered
    return registered

def _consume_meal_from_pantry(day, meal_name):
    """Consume from pantry only what was actually available when the meal was registered.
    The exact consumed quantities are snapshotted so Undo can restore the same stock.
    """
    meal_key=_meal_key(day,meal_name)
    if meal_key in st.session_state.get("pantry_consumed_by_meal",{}):
        return

    consumed=[]
    meal=st.session_state.meal_plan.get(day,{}).get(meal_name)
    if not meal:
        return

    for item in active_items(meal):
        name=str(item.get("name","Alimento")).strip()
        unit=str(item.get("unit","g")).strip()
        required=max(0.0,float(item.get("qty",0) or 0))
        if required<=0:
            continue
        key=_pantry_key(name,unit)
        stock=max(0.0,float(st.session_state.get("pantry",{}).get(key,{}).get("qty",0) or 0))
        used=min(stock,required)
        if used>0:
            set_pantry_qty(name,unit,stock-used)
            consumed.append({"name":name,"unit":unit,"qty":used})

    st.session_state.setdefault("pantry_consumed_by_meal",{})[meal_key]=consumed

def _restore_meal_to_pantry(day, meal_name):
    """Restore exactly the pantry quantities consumed when this meal was registered."""
    meal_key=_meal_key(day,meal_name)
    consumed=st.session_state.get("pantry_consumed_by_meal",{}).pop(meal_key,None)
    if consumed is None:
        return
    for item in consumed:
        add_pantry_qty(item["name"],item["unit"],float(item["qty"]))

def set_meal_registered(day, meal_name, registered):
    """Register or undo an entire meal from either Home and Piano."""
    meal=st.session_state.meal_plan.get(day,{}).get(meal_name)
    if not meal:
        return

    value=bool(registered)
    meal_key=_meal_key(day,meal_name)
    current=bool(st.session_state.registered_meals.get(meal_key,False))

    # Avoid consuming/restoring pantry twice if the same state is requested again.
    if not st.session_state.get("_plan_editor_next", False):
        if value and not current:
            _consume_meal_from_pantry(day,meal_name)
        elif not value and current:
            _restore_meal_to_pantry(day,meal_name)

    st.session_state.registered_meals[meal_key]=value

    # Keep ingredient state synchronized with the meal state.
    for item in active_items(meal):
        iid=item["id"]
        st.session_state.eaten[iid]=value
        st.session_state.pop(f"eat_{iid}",None)

def _sync_eaten_from_widget(iid):
    """Copy an ingredient checkbox into eaten and update its meal state."""
    value=bool(st.session_state.get(f"eat_{iid}",False))
    st.session_state.eaten[iid]=value

    for day, meal_name, meal in meals():
        items=active_items(meal)
        if any(item["id"]==iid for item in items):
            registered=bool(items) and all(
                st.session_state.eaten.get(item["id"],False)
                for item in items
            )
            meal_key=_meal_key(day,meal_name)
            previous=bool(st.session_state.registered_meals.get(meal_key,False))
            if not st.session_state.get("_plan_editor_next", False):
                if registered and not previous:
                    _consume_meal_from_pantry(day,meal_name)
                elif not registered and previous:
                    _restore_meal_to_pantry(day,meal_name)
            st.session_state.registered_meals[meal_key]=registered
            break

def _next_meal_for_today(day):
    """Return the first planned meal that has not actually been registered.

    The clock must never imply that a meal was eaten. Registration is the
    authoritative state, so even in the evening the app will not jump to
    dinner when breakfast/lunch have not been registered yet.
    """
    ms=st.session_state.meal_plan.get(day,{})
    order=["☕ Colazione","🍎 Spuntino","🍽️ Pranzo","🌙 Cena"]
    if not ms:
        return None

    for name in order:
        if name in ms and not _meal_is_registered(day,name):
            return ms[name] | {"_meal_name":name}

    return None


def _next_meal_context(day, balance_data):
    b=balance_data or balance()
    next_meal=_next_meal_for_today(day)
    if not next_meal:
        return {"meal":None,"meal_budget_kcal":max(0,int(b.get("remaining",0))),"future_planned_kcal":0}
    meal_name=next_meal.get("_meal_name")
    planned_kcal=round(sum(item_kcal(i) for i in active_items(next_meal)))
    future_planned=round(_planned_remaining_after_meal(day,meal_name))
    meal_budget=max(0,int(b.get("remaining",0))-future_planned)
    return {
        "meal":meal_name,
        "name":str(next_meal.get("name") or meal_name),
        "planned_kcal":planned_kcal,
        "meal_budget_kcal":meal_budget,
        "future_planned_kcal":future_planned,
        "out_of_home":out_of_home_meal_configured(day,meal_name) or "FUORI CASA" in str(next_meal.get("name","")).upper() or "UFFICIO" in str(next_meal.get("name","")).upper(),
        "items":[i.get("name") for i in active_items(next_meal)],
    }


def smart_assistant_context(balance_data=None):
    """Build factual context. Python determines budget and next meal; Gemini only interprets it."""
    b=balance_data or balance()
    d=current_day_name()
    ms=st.session_state.meal_plan.get(d,{})
    planned=[]
    for meal_name in ["☕ Colazione","🍎 Spuntino","🍽️ Pranzo","🌙 Cena"]:
        meal=ms.get(meal_name)
        if not meal: continue
        items=active_items(meal)
        planned.append({
            "meal":meal_name,
            "name":meal.get("name",meal_name),
            "kcal":round(sum(item_kcal(i) for i in items)),
            "registered":_meal_is_registered(d,meal_name),
            "out_of_home":out_of_home_meal_configured(d,meal_name) or "FUORI CASA" in str(meal.get("name","")).upper() or "UFFICIO" in str(meal.get("name","")).upper()
        })
    next_ctx=_next_meal_context(d,b)
    return {
        "day":d,
        "budget_is_dynamic":bool(b.get("using_observed")),
        "budget_label":"budget dinamico da Health Connect" if b.get("using_observed") else "target alimentare stimato dal profilo",
        "remaining_kcal":int(b.get("remaining",0)),
        "daily_food_target":int(b.get("live_target") or b.get("target") or 0),
        "eaten_kcal":int(b.get("eaten",0)),
        "deficit_target":int(b.get("deficit",0)),
        "observed_burn":int(b.get("observed_burn",0) or 0),
        "projected_burn":int(b.get("projected_burn",0) or 0),
        "steps":int(b.get("steps",0) or 0),
        "next_meal":next_ctx,
        "planned_meals":planned,
        "eaten_items":eaten_items_today(),
    }


def run_smart_food_advice(balance_data=None):
    """Ask Gemini to explain a deterministic next-meal decision; never let it recalculate the budget."""
    ctx=smart_assistant_context(balance_data)
    next_ctx=ctx.get("next_meal", {})

    # Fully registered day: this is a terminal state, not an AI recommendation.
    if not next_ctx.get("meal"):
        remaining=int(ctx.get("remaining_kcal", 0))
        eaten=int(ctx.get("eaten_kcal", 0))
        source_label="budget dinamico residuo" if ctx.get("budget_is_dynamic") else "target stimato residuo"
        return (
            "🍽️ GIORNATA ALIMENTARE COMPLETATA: tutti i pasti principali previsti per oggi risultano già registrati.\n"
            "💡 CONSIGLIO: Non devi mangiare altro per 'recuperare' le calorie rimaste. "
            "Se hai fame puoi scegliere liberamente uno spuntino leggero; se non hai fame, puoi semplicemente chiudere la giornata.\n"
            f"🔥 MARGINE: {remaining} kcal {('di budget dinamico' if ctx.get('budget_is_dynamic') else 'rispetto al target alimentare stimato')}.\n"
            f"📌 OGGI: circa {eaten} kcal registrate e nessun altro pasto pianificato da completare."
        )

    prompt=f'''Sei l'assistente alimentare di MyDietApp. Devi dare un consiglio pratico sul PROSSIMO pasto, usando esclusivamente il contesto fornito.

CONTESTO CALCOLATO DA MYDIETAPP:
{json.dumps(ctx,ensure_ascii=False,indent=2)}

REGOLE NON NEGOZIABILI:
- MyDietApp calcola gia tutti i numeri. NON ricalcolare, correggere o inventare calorie.
- "remaining_kcal" e il numero di kcal alimentari che restano oggi secondo MyDietApp.
- Se "budget_is_dynamic" e false, il valore e un TARGET STIMATO dal profilo: chiamalo sempre "target stimato" o "target alimentare stimato", MAI "budget dinamico" e MAI dire che deriva da Health Connect.
- Se "budget_is_dynamic" e true, puoi chiamare il valore BUDGET DINAMICO e collegarlo al consumo Health osservato.
- Non chiamare mai "disponibili" le kcal del target stimato come se fossero un dato misurato dal dispositivo.
- Il campo "next_meal" identifica il pasto piu rilevante in questo momento: NON scegliere un altro pasto.
- Se non esiste un prossimo pasto, dillo chiaramente.
- Se il prossimo pasto e gia registrato, dillo chiaramente invece di suggerire di mangiarlo di nuovo.
- Confronta "planned_kcal" con "meal_budget_kcal". Se rientra, consiglia semplicemente di seguire il piano.
- Se supera il budget del pasto, proponi una modifica semplice usando gli alimenti gia presenti nel piano quando possibile.
- Se "out_of_home" e true, non inventare un piatto: suggerisci di usare **Pasto fuori** per scegliere dal menu reale.
- L'utente odia pesare gli alimenti: usa SOLO concetti come porzione normale, mezza porzione, porzione abbondante. MAI grammi.
- Se l'utente ha gia registrato un alimento manualmente (es. pizza), consideralo nel consiglio.
- Non inventare proteine o altri valori nutrizionali non presenti.
- Non dare consigli medici.

Rispondi in massimo 5 righe, in questo formato:
🍽️ PROSSIMO PASTO: ...
💡 CONSIGLIO: ...
🔥 BUDGET DINAMICO: ... kcal  (solo se budget_is_dynamic=true)
🔥 TARGET STIMATO: ... kcal  (solo se budget_is_dynamic=false)
📌 MOTIVO: ...'''
    return gemini_interaction(prompt, thinking_level="minimal")

def show_daily_meal_recommendation(meal_name, day, balance_data):
    rec=meal_recommendation(day,meal_name,balance_data)
    if not rec:
        return
    title="Pranzo" if meal_name=="🍽️ Pranzo" else "Cena"
    icon="📍" if rec["out_of_home"] else ("🍽️" if meal_name=="🍽️ Pranzo" else "🌙")
    status_labels={"good":"🟢 Segui il piano","adapt":"🟡 Adatta leggermente","over":"🔴 Da adattare","mensa":"📍 Fuori casa","unknown":"⚪ Calorie non definite"}
    with st.container(border=True):
        st.markdown(f"### {icon} {title}")
        if rec["out_of_home"]:
            st.markdown("**📍 Oggi mangi fuori casa**")
            st.info("Se hai un menu, analizzalo in **Pasto fuori**: confronteremo le proposte con il tuo piano e con il budget calorico disponibile.")
            source_label="Budget dinamico" if balance_data.get("using_observed") else "Target stimato"
            if rec["planned_kcal"]:
                st.caption(f"Pasto previsto dal piano: circa {rec['planned_kcal']} kcal · {source_label} giornaliero rimasto: {rec['remaining']} kcal")
            else:
                st.caption(f"{source_label} giornaliero rimasto: {rec['remaining']} kcal")
        else:
            st.markdown(f"**{rec['name']}**")
            if rec["items"]:
                st.caption(" · ".join(f"{i['name']} {item_qty(i):g}{i['unit']}" for i in rec["items"]))
            st.caption(f"Piano: {rec['planned_kcal']} kcal · Budget per questo pasto: circa {rec['budget_for_meal']} kcal")
            if rec["registered"]:
                st.success("✅ Questo pasto risulta già registrato come mangiato.")
        st.markdown(f"**{status_labels.get(rec['status'], '💡 Suggerimento')}**")
        st.write("💡 " + rec["advice"])

        # Primary registration action for the next meal.
        # Home and Piano share the same canonical meal state.
        if st.button(
            "🍴 Ho mangiato",
            key=f"home_recommendation_eat_{day}_{meal_name}",
            use_container_width=True,
            type="primary"
        ):
            set_meal_registered(day, meal_name, True)
            st.rerun()

def grocery():
    """Aggregate quantities required by the active weekly plan."""
    d=defaultdict(lambda:[0,"",""])
    for _,_,m in meals():
        for i in active_items(m):
            key=(i["name"].strip().lower(),i["unit"]); d[key][0]+=float(i["qty"]); d[key][1]=i["unit"]; d[key][2]=i["name"]
    return sorted(d.values(),key=lambda x:x[2].lower())

def _pantry_key(name, unit):
    return f"{str(name).strip().lower()}|{str(unit).strip().lower()}"

def pantry_items():
    """Return pantry items as a sorted list with display names and quantities."""
    out=[]
    for key,item in st.session_state.get("pantry",{}).items():
        if not isinstance(item,dict):
            continue
        q=float(item.get("qty",0) or 0)
        if q <= 0:
            continue
        out.append({
            "key":key, "name":str(item.get("name", "Alimento")),
            "qty":q, "unit":str(item.get("unit", "g")),
        })
    return sorted(out,key=lambda x:x["name"].lower())

def set_pantry_qty(name, unit, qty):
    key=_pantry_key(name,unit)
    q=max(0.0,float(qty))
    if q <= 0:
        st.session_state.pantry.pop(key,None)
    else:
        st.session_state.pantry[key]={"name":str(name).strip(),"unit":str(unit).strip(),"qty":q}

def add_pantry_qty(name, unit, delta):
    key=_pantry_key(name,unit)
    current=float(st.session_state.get("pantry",{}).get(key,{}).get("qty",0) or 0)
    set_pantry_qty(name,unit,current+float(delta))

def shopping_list():
    """Return plan needs minus what is currently in the pantry."""
    rows=[]
    for required,unit,name in grocery():
        stock=float(st.session_state.get("pantry",{}).get(_pantry_key(name,unit),{}).get("qty",0) or 0)
        need=max(0.0,float(required)-stock)
        rows.append({"name":name,"unit":unit,"required":float(required),"pantry":stock,"need":need})
    return rows

def shopping_opportunity(required, pantry, unit):
    need=max(0.0,float(required)-float(pantry))
    if need<=0:
        return "covered"
    # Heuristic only: until we have a verified price feed, rank products by how much
    # of the planned requirement is still uncovered. Never invent a price.
    ratio=need/max(float(required),1.0)
    if ratio>=0.75:
        return "high"
    if ratio>=0.35:
        return "medium"
    return "low"

def water_today_ml():
    """Return today's water intake in ml, stored by calendar date."""
    return int(st.session_state.water_history.get(today(), 0) or 0)

def water_goal_ml():
    return max(500, int(st.session_state.get("p_water_goal_ml", 2500) or 2500))

def add_water_ml(delta):
    d=today()
    current=water_today_ml()
    st.session_state.water_history[d]=max(0, current + int(delta))

# V32: fixed the out_of_home variable mismatch in meal_recommendation/show_daily_meal_recommendation.
# V45: active-calorie display shows 'Non disponibili' when no reliable active value exists; energy engine unchanged.
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

def weight_projection():
    """Build a clean weekly reference trajectory from today's weight to the goal.

    This is only a planning reference based on the profile deficit (7700 kcal ≈ 1 kg).
    It deliberately contains no invented real-weight readings. Later, Health Connect
    measurements can be overlaid as the actual trajectory.
    """
    try:
        current=float(st.session_state.get("p_weight",0) or 0)
        goal=float(st.session_state.get("p_goal_weight",current) or current)
        deficit=max(0,int(st.session_state.get("p_deficit",500) or 500))
    except Exception:
        return None

    if current <= 0 or goal <= 0 or goal >= current or deficit <= 0:
        return None

    kg_per_week=(deficit*7)/7700.0
    total_kg=current-goal
    weeks=max(1,int((total_kg/kg_per_week)+0.999999))
    start=datetime.now(ROME).date()

    rows=[]
    for w in range(weeks+1):
        d=start+timedelta(days=7*w)
        projected=max(goal,current-(kg_per_week*w))
        if w==weeks:
            projected=goal
        rows.append({"Data":d,"Peso teorico":round(projected,1)})

    return {
        "current":current,
        "goal":goal,
        "deficit":deficit,
        "kg_per_week":kg_per_week,
        "weeks":weeks,
        "target_date":start+timedelta(days=7*weeks),
        "rows":rows,
    }


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
    native_active=round(float(h.get("active_calories_today") or 0))
    native_active_verified=bool(h.get("active_calories_source_verified")) and native_active > 0
    # If Health Connect does not expose active calories, use the same transparent
    # estimate already used by the energy balance instead of displaying 0.
    estimated_active=0
    try:
        b=balance()
        estimated_active=int(b.get("active_observed") or 0) if b.get("using_observed") else 0
    except Exception:
        estimated_active=0
    active=native_active if native_active_verified else estimated_active
    active_source="Samsung Health/Watch verificato" if native_active_verified else ("stima" if estimated_active > 0 else "non disponibile")
    return {
        "steps":int(float(h.get("steps_today") or 0)),
        "active_calories":active,
        "active_source":active_source,
        "distance_km":float(h.get("distance_today") or 0),
        "workouts":int(float(h.get("workouts_today") or len(normalized) or 0)),
        "details":normalized,
    }

def balance():
    h=st.session_state.health
    e=energy_profile()
    eaten=eaten_kcal()

    # Only a verified native Health Connect total from TODAY can drive the
    # production budget. A stale snapshot or legacy/unverified value falls back
    # to the profile estimate instead of silently presenting a false live budget.
    snapshot_date=str(h.get("date") or "")
    today_iso=today()
    native_verified=bool(h.get("native_health_snapshot")) and bool(h.get("calories_source_verified"))
    observed=float(h.get("calories_today") or 0) if native_verified and snapshot_date==today_iso else 0.0

    # Samsung Health total calories are cumulative from local midnight to now.
    # We therefore add ONLY the BMR/resting expenditure still expected until
    # midnight. We never add active calories or workout calories again: they are
    # already components of the observed total.
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
    native_active=float(h.get("active_calories_today") or 0)
    active_verified=bool(h.get("active_calories_source_verified")) and native_active > 0
    estimated_active=max(0,round(observed-bmr_for_projection*(elapsed/86400.0))) if observed > 0 else 0
    active_observed=round(native_active) if active_verified else estimated_active

    source="Health Connect nativo · Samsung Health/Watch verificato" if observed > 0 else "Profilo · stima Mifflin + livello attività"
    return {
        "target":e["target"], "live_target":live_target, "eaten":eaten,
        "remaining":live_target-eaten, "observed_burn":round(observed),
        "projected_burn":projected_burn, "remaining_rest":remaining_rest,
        "active_observed":active_observed,
        "active_verified":active_verified,
        "bmr_est":e["bmr_est"], "bmr_health":round(bmr_health) if bmr_health > 0 else None,
        "maintenance":e["maintenance_est"], "deficit":e["deficit"],
        "using_observed":observed > 0, "source":source,
        "snapshot_date":snapshot_date, "snapshot_is_today":snapshot_date==today_iso,
        "native_verified":native_verified
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
            if st.session_state.page=="Piano" and name!="Piano":
                restore_current_plan_context()
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
    <div class="muted">Consumo previsto: {b["projected_burn"]:,} kcal · deficit: {b["deficit"]:,} kcal · BMR: {b["bmr_health"] or b["bmr_est"]} kcal/giorno</div>
    <div class="{cls}">{msg}</div></div>""".replace(",","."),unsafe_allow_html=True)
    st.progress(min(max(b["eaten"]/max(b["live_target"],1),0),1))
    if b["using_observed"]:
        c1,c2,c3=st.columns(3)
        c1.metric("🔥 Consumo finora",f"{b['observed_burn']:,} kcal".replace(",","."))
        active_display = f"{b['active_observed']:,} kcal".replace(",",".") if b.get("active_observed", 0) > 0 else "Non disponibili"
        active_label = "⚡ Calorie attive" if b.get("active_verified") else "⚡ Attive stimate finora"
        c2.metric(active_label,active_display)
        c3.metric("🎯 Consumo stimato oggi",f"{b['projected_burn']:,} kcal".replace(",","."))
        st.caption(
            f"Il budget dinamico usa il consumo Health osservato ({b['observed_burn']} kcal) "
            f"e aggiunge solo il consumo a riposo residuo fino a mezzanotte ({b['remaining_rest']} kcal). "
            "Non vengono inventate attività future."
        )
        st.caption(f"Fonte del bilancio: {b['source']}. Snapshot Health: {b['snapshot_date'] or 'nessuno'}.")
        a=activity_summary()
        with st.container(border=True):
            st.markdown("**🏃 Attività di oggi**")
            ac1,ac2,ac3,ac4=st.columns(4)
            ac1.metric("👣 Passi", f"{a['steps']:,}".replace(",","."))
            ac2.metric("⚡ Calorie attive" if a["active_source"] != "stima" else "⚡ Calorie attive stimate", f"{a['active_calories']:,} kcal".replace(",","."))
            ac3.metric("📏 Distanza", f"{a['distance_km']:.2f} km")
            ac4.metric("🏋️ Allenamenti", str(a['workouts']))
            if a["details"]:
                for w in a["details"]:
                    st.write(f"• **{w['name']}** · {w['duration_minutes']} min")
            else:
                st.caption("Nessuna sessione di allenamento registrata. I passi e le calorie attive continuano comunque ad aggiornarsi.")
            st.caption(f"Fonte calorie attive: {a['active_source']}. Sono già comprese nel consumo totale Health osservato e non vengono sommate una seconda volta.")
    # ---------------- Water tracking ----------------
    st.divider()
    st.subheader("💧 Acqua")
    water=water_today_ml()
    goal=water_goal_ml()
    pct=min(max(water/max(goal,1),0.0),1.0)
    wc1,wc2=st.columns([5,2])
    with wc1:
        st.markdown(f"### {water/1000:.2f} L / {goal/1000:.2f} L")
        st.progress(pct)
        if water >= goal:
            st.success("🎉 Obiettivo acqua raggiunto oggi.")
        else:
            st.caption(f"Ti mancano {(goal-water)/1000:.2f} L per raggiungere l'obiettivo di oggi.")
    with wc2:
        b1,b2=st.columns(2)
        with b1:
            if st.button("− 250 ml",key="water_minus",use_container_width=True):
                add_water_ml(-250)
                st.rerun()
        with b2:
            if st.button("+ 250 ml",key="water_plus",use_container_width=True,type="primary"):
                add_water_ml(250)
                st.rerun()
        if st.button("Azzera oggi",key="water_reset",use_container_width=True):
            st.session_state.water_history[today()]=0
            st.rerun()
    st.caption("Registrazione manuale · storico conservato per data in questa sessione.")

    # ---------------- What should I eat today? ----------------
    st.divider()
    st.subheader("🍴 Cosa mangio oggi?")

    d=current_day_name()
    next_meal=_next_meal_for_today(d)
    meal_order=["☕ Colazione","🍎 Spuntino","🍽️ Pranzo","🌙 Cena"]
    registered_count=sum(1 for mn in meal_order if mn in st.session_state.meal_plan.get(d,{}) and _meal_is_registered(d,mn))

    if next_meal:
        next_name=next_meal.get("_meal_name")

        if registered_count == 0:
            st.caption("Non hai ancora registrato un pasto oggi: partiamo dal primo pasto previsto, senza dedurre nulla dall'orario.")
        else:
            st.caption("Il prossimo pasto da registrare viene determinato dai pasti che hai effettivamente registrato.")

        if next_name in ("🍽️ Pranzo","🌙 Cena"):
            show_daily_meal_recommendation(next_name,d,b)
        else:
            meal=st.session_state.meal_plan.get(d,{}).get(next_name)

            if meal:
                items=active_items(meal)
                kcal=round(sum(item_kcal(i) for i in items))

                with st.container(border=True):
                    st.markdown(f"### {next_name}")
                    st.markdown(f"**{meal.get('name','Pasto')}**")
                    st.caption(f"{kcal} kcal · prossimo pasto da registrare")

                    if items:
                        with st.expander("Dettagli"):
                            for item in items:
                                st.write(
                                    f"• {item['name']} — "
                                    f"{quantity_caption(item)} · "
                                    f"{round(item_kcal(item))} kcal"
                                )

                    if st.button(
                        "🍴 Ho mangiato",
                        key=f"home_next_eat_{d}_{next_name}",
                        use_container_width=True,
                        type="primary"
                    ):
                        set_meal_registered(d,next_name,True)
                        st.rerun()
    else:
        if registered_count == 0:
            st.info("🌅 **Inizia dalla colazione.** Quando registrerai un pasto, MyDiet passerà automaticamente al successivo. Nessun pasto viene considerato mangiato solo in base all'orario.")
        else:
            st.success(
                "🎉 **Giornata alimentare completata!** "
                "Hai registrato tutti i pasti previsti per oggi."
            )

    with st.container(border=True):
        st.markdown("### 🤖 Consiglio intelligente")
        source_note=("budget dinamico calcolato dai dati Health osservati" if b["using_observed"] else "target alimentare stimato dal profilo; non è un dato misurato da Health")
        st.caption(f"Il consiglio usa il prossimo pasto reale della giornata, ciò che hai già mangiato e il {source_note}. Gemini interpreta i dati: non calcola le calorie.")
        if st.button("✨ Dammi un consiglio per il prossimo pasto", key="smart_food_advice_btn", use_container_width=True):
            with st.spinner("Sto valutando il tuo piano di oggi…"):
                try:
                    advice=run_smart_food_advice(b)
                    st.session_state.smart_food_advice=advice
                except Exception as e:
                    st.error(f"Errore nel consiglio AI: {e}")
        if st.session_state.get("smart_food_advice"):
            st.info(st.session_state.smart_food_advice)

    st.subheader("🍽️ Oggi")
    st.caption("Registra i pasti quando li mangi: il totale in alto si aggiorna automaticamente.")
    d=current_day_name(); ms=st.session_state.meal_plan.get(d)

    if not ms:
        st.info(f"Non hai ancora un piano alimentare per {d}. Vai in **Piano** e genera il piano settimanale.")
    else:
        for idx,(mn,m) in enumerate(ms.items()):
            items=active_items(m)
            kcal=round(sum(item_kcal(i) for i in items))
            registered=_meal_is_registered(d,mn)
            status="✅ Registrato" if registered else "○ Non registrato"
            with st.container(border=True):
                c1,c2,c3=st.columns([5,2,1])
                with c1:
                    st.markdown(f"**{mn}**")
                    st.caption(f"{m.get('name','Pasto')} · {kcal} kcal · {status}")
                with c2:
                    # Registration is handled in "Cosa mangio oggi?".
                    # Keep only an undo action here once a meal has been registered.
                    if registered:
                        if st.button("↩ Annulla",key=f"home_undo_{d}_{idx}",use_container_width=True):
                            set_meal_registered(d,mn,False)
                            st.rerun()
                    else:
                        st.caption("Da registrare")
                with c3:
                    st.metric("kcal",kcal)
                with st.expander("Dettagli"):
                    for item in items:
                        st.write(f"• {item['name']} — {quantity_caption(item)} · {round(item_kcal(item))} kcal")

    # ---------------- Live energy balance ----------------
    st.divider()
    st.subheader("⚡ Bilancio energetico di oggi")
    if b["using_observed"]:
        net_so_far=b["eaten"]-b["observed_burn"]
        c1,c2,c3,c4=st.columns(4)
        c1.metric("🍽️ Assunte",f"{b['eaten']:,} kcal".replace(",","."))
        c2.metric("🔥 Consumate finora",f"{b['observed_burn']:,} kcal".replace(",","."))
        c3.metric("🎯 Target alimentare",f"{b['live_target']:,} kcal".replace(",","."))
        c4.metric("📉 Deficit obiettivo",f"{b['deficit']:,} kcal".replace(",","."))
        if net_so_far < 0:
            st.success(f"Sei attualmente a **{abs(net_so_far):,} kcal sotto il consumo osservato**. Il deficit obiettivo di oggi è **{b['deficit']} kcal**. Il dato continua ad aggiornarsi con Health.".replace(",","."))
        elif net_so_far > 0:
            st.warning(f"Sei attualmente a **{net_so_far:,} kcal sopra il consumo osservato**. Il deficit obiettivo di oggi è **{b['deficit']} kcal**. È un dato provvisorio della giornata.".replace(",","."))
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

    with st.expander("🍴 Registra qualcosa che hai mangiato fuori dal piano", expanded=False):
        st.caption("Utile, ad esempio, se hai mangiato una pizza o un pasto diverso da quello previsto.")
        c1,c2=st.columns([3,1])
        with c1:n=st.text_input("Alimento",placeholder="Pizza margherita",key="manual_food_name_home")
        with c2:k=st.number_input("kcal",0,3000,500,10,key="manual_food_kcal_home")
        if st.button("Registra",type="primary",key="manual_food_register_home") and n.strip():
            st.session_state.manual_foods.append({"name":n.strip(),"kcal":k,"date":today()})
            st.rerun()

    if st.session_state.last_sync: st.caption("Ultima sincronizzazione Health: "+st.session_state.last_sync)

# ---------------- Piano ----------------
elif st.session_state.page=="Piano":
    ensure_plan_metadata()
    st.title("🍽️ Il tuo piano")
    st.caption("Un unico posto per vedere, preparare e modificare il tuo piano settimanale.")

    days_week=["Lunedì","Martedì","Mercoledì","Giovedì","Venerdì","Sabato","Domenica"]
    has_next=bool(st.session_state.get("next_meal_plan"))

    # ------------------------------------------------------------------
    # Navigazione principale del Piano: niente più sezioni chilometriche.
    # ------------------------------------------------------------------
    current_label=f"📌 Questa settimana · {week_label(st.session_state.plan_week_start)}"
    next_label=f"✨ Prossima settimana · {week_label(st.session_state.next_week_start)}" if has_next else "✨ Prossima settimana · da preparare"
    history_label="📚 Storico"

    choices=[current_label,next_label,history_label]
    default_view=st.session_state.get("plan_view_mode","current")
    if default_view=="next" and not has_next:
        default_view="current"
    view_index={"current":0,"next":1,"history":2}[default_view]
    selected_view=st.radio(
        "",
        choices,
        index=view_index,
        horizontal=True,
        key="plan_main_view",
        label_visibility="collapsed",
    )

    # Mantieni sempre disponibile lo stato canonico usato dal resto della
    # pagina. Nella V62 questo valore non veniva inizializzato prima del
    # riepilogo giornaliero, causando AttributeError al primo caricamento.
    if selected_view==next_label:
        st.session_state.plan_view_mode="next"
    elif selected_view==history_label:
        st.session_state.plan_view_mode="history"
    else:
        st.session_state.plan_view_mode="current"

    # ------------------------------------------------------------------
    # STORICO: consultazione separata, senza mischiarla con il piano.
    # ------------------------------------------------------------------
    if selected_view==history_label:
        st.session_state.plan_view_mode="history"
        restore_current_plan_context()

        st.subheader("📚 Storico dei piani")
        st.caption("Le settimane concluse vengono conservate come istantanee. Non possono essere modificate accidentalmente.")
        history_items=sorted(
            st.session_state.plan_history.values(),
            key=lambda x:x.get("created_at",""),
            reverse=True,
        )
        if not history_items:
            st.info("Ancora nessun piano storico. Il primo verrà archiviato quando la prossima settimana diventerà attiva.")
        else:
            for rec in history_items:
                label=rec.get("label","Settimana")
                created=rec.get("created_at","—")[:16].replace("T"," ")
                with st.expander(f"📅 {label}", expanded=False):
                    st.caption(f"Archiviato: {created} · {rec.get('reason','')}")
                    for hday,hms in rec.get("plan",{}).items():
                        day_kcal=round(sum(float(i.get("kcal",0)) for _,hm in hms.items() for i in hm.get("ingredients",[])))
                        st.markdown(f"**{hday}** · {day_kcal} kcal")
                        for hmn,hm in hms.items():
                            names=", ".join(
                                f"{i.get('name','Alimento')} · {i.get('qty',1):g}{i.get('unit','g')}"
                                for i in hm.get("ingredients",[])
                            ) or "Fuori casa"
                            st.caption(f"{hmn}: {names}")
        st.stop()

    # ------------------------------------------------------------------
    # Preparazione della prossima settimana: configurazione fuori casa + AI
    # nello stesso punto, così è immediatamente chiaro cosa si sta preparando.
    # ------------------------------------------------------------------
    current_start=st.session_state.plan_week_start
    next_start=(date.fromisoformat(current_start)+timedelta(days=7)) if current_start else (date.today()+timedelta(days=7))
    with st.expander(f"✨ Prepara la prossima settimana · {week_label(next_start.isoformat())}", expanded=False):
        st.caption("Configura qui i pasti fuori casa e genera il piano. Il piano attuale resta invariato.")

        prep_cols=st.columns([2.2,1.4])
        with prep_cols[0]:
            st.markdown("**📍 Pasti fuori casa**")
            st.caption("Pranzo e cena possono essere fuori casa indipendentemente. La scelta vale per la prossima settimana che stai preparando.")
            next_lunch_set=set(st.session_state.get("next_out_lunch_days",[]))
            next_dinner_set=set(st.session_state.get("next_out_dinner_days",[]))
            # Se non esiste ancora una bozza, parti dalle impostazioni correnti come comodo punto di partenza.
            if not has_next and not next_lunch_set and not next_dinner_set:
                next_lunch_set=set(st.session_state.get("out_lunch_days",[]))
                next_dinner_set=set(st.session_state.get("out_dinner_days",[]))
            for od in days_week:
                oc1,oc2,oc3=st.columns([1.1,2.0,2.0])
                with oc1: st.markdown(f"**{od[:3]}**")
                with oc2:
                    lunch_on=st.checkbox("📍 Pranzo",value=od in next_lunch_set,key=f"prep_next_lunch_{od}")
                with oc3:
                    dinner_on=st.checkbox("📍 Cena",value=od in next_dinner_set,key=f"prep_next_dinner_{od}")
                if lunch_on: next_lunch_set.add(od)
                else: next_lunch_set.discard(od)
                if dinner_on: next_dinner_set.add(od)
                else: next_dinner_set.discard(od)
            st.session_state.next_out_lunch_days=sorted(next_lunch_set,key=days_week.index)
            st.session_state.next_out_dinner_days=sorted(next_dinner_set,key=days_week.index)
            lunch_txt=", ".join(st.session_state.next_out_lunch_days) if st.session_state.next_out_lunch_days else "nessuno"
            dinner_txt=", ".join(st.session_state.next_out_dinner_days) if st.session_state.next_out_dinner_days else "nessuna"
            st.success(f"Pranzi fuori: {lunch_txt} · Cene fuori: {dinner_txt}")

        with prep_cols[1]:
            st.markdown("**🤖 Piano AI**")
            if has_next:
                st.success("Bozza già pronta")
                st.caption("Puoi aprirla, modificarla e rigenerarla senza toccare il piano attuale.")
                if st.button("👀 Apri prossima settimana",key="open_next_from_prep",use_container_width=True,type="primary"):
                    st.session_state.plan_view_mode="next"
                    st.rerun()
                if st.button("🔄 Rigenera piano",key="regen_next_from_prep",use_container_width=True):
                    # Le impostazioni appena selezionate sono già nella bozza: la generazione le userà.
                    st.session_state.force_next_generation=True
                    st.rerun()
            else:
                st.caption("Genera una bozza separata: prima la controlli e la modifichi, poi diventerà il piano attivo.")
                if st.button("✨ Genera piano",key="generate_next_from_prep",use_container_width=True,type="primary"):
                    st.session_state.force_next_generation=True
                    st.rerun()

    # Feedback della generazione sempre in alto, vicino alla preparazione.
    if st.session_state.plan_generation_status=="running":
        st.info("🤖 **Sto generando il piano…** Verifico i 7 giorni prima di salvare la bozza.")
    elif st.session_state.plan_generation_status=="success":
        st.success(st.session_state.plan_generation_message or "✓ Piano generato con successo.")
        if st.session_state.plan_generation_time:
            st.caption(f"Ultima generazione: {st.session_state.plan_generation_time}")
    elif st.session_state.plan_generation_status=="error":
        st.error(st.session_state.plan_generation_message or "La generazione non è riuscita.")

    # ------------------------------------------------------------------
    # Generazione AI: eseguita solo quando richiesta dal pannello azioni.
    # ------------------------------------------------------------------
    if st.session_state.pop("force_next_generation",False):
        current_start=st.session_state.plan_week_start
        next_start=(date.fromisoformat(current_start)+timedelta(days=7)) if current_start else (date.today()+timedelta(days=7))
        st.session_state.plan_generation_status="running"
        st.session_state.plan_generation_message="Sto generando il piano e verificando la struttura ricevuta dall'AI…"
        st.session_state.plan_generation_time=datetime.now(ROME).strftime("%d/%m/%Y %H:%M")
        try:
            ep=energy_profile()
            lunch_days=copy.deepcopy(st.session_state.get("next_out_lunch_days", st.session_state.get("out_lunch_days",[])))
            dinner_days=copy.deepcopy(st.session_state.get("next_out_dinner_days", st.session_state.get("out_dinner_days",[])))
            prompt=f"""Crea un piano alimentare italiano di 7 giorni per la settimana {week_label(next_start.isoformat())}. Profilo: {st.session_state.p_weight} kg, {st.session_state.p_height} cm, {st.session_state.p_age} anni, sesso {st.session_state.p_sex}. Target alimentare stimato: {ep['target']} kcal/giorno.
GIORNI PRANZO FUORI CASA DELLA PROSSIMA SETTIMANA: {', '.join(lunch_days) if lunch_days else 'nessuno'}.
GIORNI CENA FUORI CASA DELLA PROSSIMA SETTIMANA: {', '.join(dinner_days) if dinner_days else 'nessuno'}.
Per ogni giorno crea esattamente 4 pasti con queste chiavi: "☕ Colazione", "🍎 Spuntino", "🍽️ Pranzo", "🌙 Cena".
Nei pasti segnati come fuori casa NON inventare un piatto domestico: usa name="📍 FUORI CASA: scegli dal menu disponibile" e ingredients=[].
Negli altri pasti crea ricette domestiche con ingredienti reali.
Restituisci SOLO JSON. La struttura preferita è un oggetto con le chiavi Lunedì, Martedì, Mercoledì, Giovedì, Venerdì, Sabato, Domenica; ogni giorno contiene i 4 pasti; ogni pasto contiene name + ingredients; ogni ingredient contiene name, qty, unit, kcal."""
            with st.spinner("🤖 Sto generando il piano… verifico i 7 giorni prima di salvarlo."):
                raw=gemini_interaction(prompt)
                out=normalize_ai_plan(raw)
            st.session_state.next_meal_plan=out
            st.session_state.next_overrides={}
            st.session_state.next_week_start=next_start.isoformat()
            st.session_state.next_out_lunch_days=copy.deepcopy(lunch_days)
            st.session_state.next_out_dinner_days=copy.deepcopy(dinner_days)
            st.session_state.next_mensa_menus={}
            st.session_state.plan_view_mode="next"
            st.session_state.plan_generation_status="success"
            st.session_state.plan_generation_message=f"✓ Piano {week_label(next_start.isoformat())} generato con successo. È una bozza separata: puoi modificarla senza alterare il piano attuale."
            st.session_state.plan_generation_time=datetime.now(ROME).strftime("%d/%m/%Y %H:%M")
            st.session_state.eaten={}
            st.session_state.registered_meals={}
            st.rerun()
        except Exception as e:
            st.session_state.plan_generation_status="error"
            st.session_state.plan_generation_message=f"✕ Generazione non riuscita: {e}"
            st.session_state.plan_generation_time=datetime.now(ROME).strftime("%d/%m/%Y %H:%M")
            st.error(f"Errore AI: {e}")


    # ------------------------------------------------------------------
    # Giorno + riepilogo del giorno.
    # ------------------------------------------------------------------
    days=list(st.session_state.meal_plan.keys())
    current_day=current_day_name()
    day_index=days.index(current_day) if current_day in days else 0
    day=st.selectbox("Giorno",days,index=day_index,key="plan_day_selector")
    day_meals=st.session_state.meal_plan.get(day,{})
    day_total=round(sum(
        item_kcal(i)
        for m in day_meals.values()
        for i in active_items(m)
    ))
    registered_count=sum(
        1 for mn in day_meals
        if st.session_state.plan_view_mode=="next" or _meal_is_registered(day,mn)
    )
    st.markdown(
        f"**{day}** · {day_total} kcal · "
        f"{'piano in preparazione' if st.session_state.plan_view_mode=='next' else f'{registered_count}/{len(day_meals)} pasti registrati'}"
    )

    editing_next=st.session_state.plan_view_mode=="next"

    # ------------------------------------------------------------------
    # Editor pasti: card pulite. I controlli avanzati compaiono solo
    # quando servono.
    # ------------------------------------------------------------------
    for mn,m in day_meals.items():
        out_of_home_day=out_of_home_meal_configured(day,mn)
        items=active_items(m)
        kcal=round(sum(item_kcal(i) for i in items))
        meal_registered=False if editing_next else _meal_is_registered(day,mn)
        status="📝 Da preparare" if editing_next else ("✅ Registrato" if meal_registered else "○ Da registrare")
        title=f"{mn}  ·  {kcal} kcal"

        with st.container(border=True):
            top1,top2=st.columns([6,1.7])
            with top1:
                st.markdown(f"### {title}")
                if out_of_home_day:
                    st.caption("📍 **Fuori casa** · scegli dal menu disponibile")
                else:
                    st.caption(f"{m.get('name','Pasto')} · {status}")
            with top2:
                if editing_next:
                    st.caption("Bozza")
                elif meal_registered:
                    if st.button("↩ Annulla",key=f"plan_undo_meal_{day}_{mn}",use_container_width=True):
                        set_meal_registered(day,mn,False); st.rerun()
                else:
                    if st.button("✓ Registra",key=f"plan_eat_meal_{day}_{mn}",use_container_width=True,type="primary"):
                        set_meal_registered(day,mn,True); st.rerun()

            if items:
                for item in items:
                    mult=item_multiplier(item)
                    current_qty=item_qty(item)
                    current_kcal=item_kcal(item)
                    step=qty_step(item.get("unit","g"),current_qty)
                    with st.container(border=True):
                        i1,i2,i3,i4=st.columns([5.3,0.8,1.1,1.5])
                        with i1:
                            if quantity_mode()=="precise":
                                qty_txt=f"{current_qty:g} {item.get('unit','g')}"
                            elif quantity_mode()=="both":
                                qty_txt=f"1 porzione · {current_qty:g} {item.get('unit','g')}"
                            else:
                                qty_txt="1 porzione"
                            st.markdown(f"**{item['name']}**")
                            st.caption(f"{qty_txt} · {round(current_kcal)} kcal")
                        with i2:
                            if st.button("−",key="minus_"+item['id'],use_container_width=True):
                                if quantity_mode()=="precise": set_item_qty(item,current_qty-step)
                                else: set_item_qty(item,max(0.5,mult-0.5)*float(item.get('qty',1)))
                                st.rerun()
                        with i3:
                            if st.button("+",key="plus_"+item['id'],use_container_width=True):
                                if quantity_mode()=="precise": set_item_qty(item,current_qty+step)
                                else: set_item_qty(item,(mult+0.5)*float(item.get('qty',1)))
                                st.rerun()
                        with i4:
                            if st.button("✏️ Modifica",key="edit_"+item['id'],use_container_width=True):
                                st.session_state[f"edit_open_{item['id']}"]=not st.session_state.get(f"edit_open_{item['id']}",False)
                                st.rerun()

                    if st.session_state.get(f"edit_open_{item['id']}",False):
                        with st.expander("Modifica alimento",expanded=True):
                            a,b,c,d=st.columns([3,1,1,1])
                            with a: new_name=st.text_input("Alimento",value=item['name'],key=f"rn_{item['id']}")
                            with b: new_qty=st.number_input("Qtà",min_value=.1,value=float(current_qty),step=step,key=f"rq_{item['id']}")
                            with c: new_unit=st.selectbox("Unità",["g","ml","pz"],index=["g","ml","pz"].index(item.get('unit','g')) if item.get('unit','g') in ["g","ml","pz"] else 0,key=f"ru_{item['id']}")
                            with d: new_kcal=st.number_input("kcal",min_value=0,value=int(round(current_kcal)),step=5,key=f"rk_{item['id']}")
                            e1,e2=st.columns(2)
                            with e1:
                                if st.button("💾 Salva",key=f"save_edit_{item['id']}",use_container_width=True,type="primary") and new_name.strip():
                                    item['name']=new_name.strip(); item['unit']=new_unit; item['qty']=float(new_qty); item['kcal']=int(new_kcal)
                                    st.session_state.overrides[item['id']]={"multiplier":1}
                                    st.session_state.eaten[item['id']]=False
                                    st.session_state[f"eat_{item['id']}"]=False
                                    for _day,_mn,_meal in meals():
                                        if any(x['id']==item['id'] for x in active_items(_meal)):
                                            st.session_state.registered_meals[_meal_key(_day,_mn)]=False; break
                                    st.session_state[f"edit_open_{item['id']}"]=False; st.rerun()
                            with e2:
                                if st.button("✕ Rimuovi",key=f"remove_{item['id']}",use_container_width=True):
                                    st.session_state.overrides[item['id']]={"removed":True,"multiplier":mult}
                                    st.session_state.eaten[item['id']]=False
                                    st.session_state[f"eat_{item['id']}"]=False
                                    for _day,_mn,_meal in meals():
                                        if any(x['id']==item['id'] for x in active_items(_meal)):
                                            st.session_state.registered_meals[_meal_key(_day,_mn)]=False; break
                                    st.rerun()

            else:
                menu=current_mensa_menu(day,mn)
                st.info("📍 Fuori casa — scegli dal menu reale.")
                with st.expander(f"🍴 Pasto fuori · {day} · {mn}", expanded=bool(menu)):
                    st.caption("Questo menu è associato esclusivamente a questo giorno e a questo pasto. Un altro menu per la cena o per un altro giorno resterà separato.")
                    if menu:
                        st.success(f"✅ Menu associato · analizzato {menu.get('analyzed_at','—')}")
                        st.info(menu.get("result", "Menu analizzato."))
                        if st.button("📷 Sostituisci menu", key=f"mensa_replace_{day}_{mn}_{st.session_state.plan_view_mode}", use_container_width=True):
                            st.session_state[f"mensa_replace_open_{day}_{mn}_{st.session_state.plan_view_mode}"]=True
                            st.rerun()
                    if (not menu) or st.session_state.get(f"mensa_replace_open_{day}_{mn}_{st.session_state.plan_view_mode}",False):
                        img=st.camera_input("Scatta il menu", key=f"mensa_camera_{st.session_state.plan_view_mode}_{day}_{mn}") or st.file_uploader("Carica una foto",type=["jpg","jpeg","png"],key=f"mensa_upload_{st.session_state.plan_view_mode}_{day}_{mn}")
                        if img:
                            st.image(img,width=420)
                            if st.button("✨ Analizza e associa a questo pasto",type="secondary",key=f"analyze_mensa_{st.session_state.plan_view_mode}_{day}_{mn}"):
                                try:
                                    b=balance()
                                    rec=meal_recommendation(day,mn,b)
                                    planned=rec["name"] if rec else "nessun piatto domestico previsto"
                                    planned_kcal=rec["planned_kcal"] if rec else 0
                                    if editing_next:
                                        budget_label=f"target alimentare stimato: {energy_profile()['target']} kcal/giorno"
                                    else:
                                        budget_label=f"calorie ancora disponibili oggi: {b['remaining']} kcal"
                                    prompt=f"""Analizza questo menu fuori casa per il pasto {mn} del giorno {day}.
Piano previsto: {planned}; calorie previste dal piano: {planned_kcal} kcal; {budget_label}.
Confronta solo le alternative realmente presenti nella foto. Non inventare piatti.
Rispondi in modo breve e pratico con:
🟢 COSA ORDINARE: piatti esatti dalla foto
💡 PERCHÉ: una frase collegata al piano e al budget
⚠️ COSA LIMITARE: eventuali elementi più calorici
"""
                                    r=gemini_interaction(prompt,image=img)
                                    set_mensa_menu(day,mn,r)
                                    st.session_state[f"mensa_replace_open_{day}_{mn}_{st.session_state.plan_view_mode}"]=False
                                    st.success(f"✅ Menu associato a {day} · {mn}.")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Errore analisi menu: {e}")

            with st.expander("➕ Aggiungi alimento",expanded=False):
                suggestions=plan_food_suggestions(day,mn,limit=8)
                if suggestions:
                    st.caption("Alimenti già presenti nella settimana")
                    for idx,sug in enumerate(suggestions):
                        s1,s2=st.columns([5,1.4])
                        with s1:
                            sug_label=(f"{sug['qty']:g} {sug['unit']}" if quantity_mode()=="precise" else (f"1 porzione · {sug['qty']:g} {sug['unit']}" if quantity_mode()=="both" else "1 porzione"))
                            st.markdown(f"**{sug['name']}** · {sug_label} · {round(sug['kcal'])} kcal")
                        with s2:
                            if st.button("+ Aggiungi",key=f"suggest_{day}_{mn}_{idx}",use_container_width=True):
                                st.session_state.meal_plan[day][mn]['ingredients'].append({"id":sid(),"name":sug['name'],"qty":sug['qty'],"unit":sug['unit'],"kcal":sug['kcal']})
                                st.rerun()
                    st.divider()
                a,b,c,d=st.columns([3,1,1,1])
                with a: n=st.text_input("Nome",key=f"n_{day}_{mn}")
                with b: q=st.number_input("Qtà",min_value=.1,value=10.,step=1.,key=f"q_{day}_{mn}")
                with c: u=st.selectbox("Unità",["g","ml","pz"],key=f"u_{day}_{mn}")
                with d: k=st.number_input("kcal",min_value=0,value=50,step=5,key=f"k_{day}_{mn}")
                if st.button("Aggiungi al pasto",key=f"add_{day}_{mn}") and n.strip():
                    st.session_state.meal_plan[day][mn]['ingredients'].append({"id":sid(),"name":n.strip(),"qty":q,"unit":u,"kcal":k}); st.rerun()

    if editing_next:
        save_next_editor_context()



# ---------------- Dispensa ----------------
elif st.session_state.page=="Dispensa":
    st.title("🛒 Spesa & Dispensa")
    st.caption("Piano → Dispensa → Spesa. Ora MyDiet prepara anche il confronto intelligente dei prodotti da acquistare.")

    rows=shopping_list()
    to_buy=[r for r in rows if r["need"]>0]
    covered=[r for r in rows if r["need"]<=0]
    pantry=pantry_items()

    c1,c2,c3=st.columns(3)
    with c1: st.metric("🛒 Da comprare",len(to_buy))
    with c2: st.metric("📦 In dispensa",len(pantry))
    with c3: st.metric("✅ Già coperti",len(covered))

    tab_shop, tab_smart, tab_pantry=st.tabs(["🛒 Da comprare","💰 Risparmio","📦 In casa"])

    with tab_shop:
        st.subheader("🛒 Lista della spesa")
        if not rows:
            st.info("Il piano non contiene ancora alimenti da acquistare.")
        elif not to_buy:
            st.success("🎉 Hai già tutto quello che serve per il piano.")
            if covered: st.caption("I prodotti già disponibili in dispensa sono coperti automaticamente dal piano.")
        else:
            st.caption("La quantità da comprare tiene già conto di quello che hai in casa.")
            for r in to_buy:
                key=_pantry_key(r["name"],r["unit"])
                with st.container(border=True):
                    c1,c2=st.columns([4,1.35])
                    with c1:
                        st.markdown(f"**{r['name']}**")
                        st.caption(f"Servono {r['required']:g} {r['unit']} · hai {r['pantry']:g} {r['unit']} · **mancano circa {r['need']:g} {r['unit']}**")
                    with c2:
                        buy_open_key=f"shopping_buy_open_{key.replace('|','_')}"
                        if not st.session_state.get(buy_open_key,False):
                            if st.button("✓ Ho comprato",key="buy_"+key.replace("|","_"),use_container_width=True):
                                st.session_state[buy_open_key]=True
                                st.rerun()
                        else:
                            st.markdown("**Quanto hai comprato?**")
                            default_qty=max(0.1,float(r["need"]))
                            qty=st.number_input(
                                f"Quantità ({r['unit']})",
                                min_value=0.1,
                                value=default_qty,
                                step=0.1 if float(r["need"]) < 10 else 50.0,
                                key="shopping_qty_"+key.replace("|","_"),
                                label_visibility="collapsed"
                            )
                            b1,b2=st.columns(2)
                            with b1:
                                if st.button("Conferma",key="confirm_buy_"+key.replace("|","_"),use_container_width=True,type="primary"):
                                    add_pantry_qty(r["name"],r["unit"],float(qty))
                                    st.session_state.shopping_checked[key]=True
                                    st.session_state[buy_open_key]=False
                                    st.rerun()
                            with b2:
                                if st.button("Annulla",key="cancel_buy_"+key.replace("|","_"),use_container_width=True):
                                    st.session_state[buy_open_key]=False
                                    st.rerun()
            st.divider()
            st.caption("💡 La quantità indicata è solo il fabbisogno stimato: quando fai la spesa puoi inserire **meno, uguale o più** di quella quantità. MyDiet aggiungerà alla dispensa esattamente quanto hai acquistato.")

    with tab_smart:
        st.subheader("💰 Spesa intelligente")
        st.caption("Confronta dati reali quando disponibili, senza inventare prezzi. MyDiet separa il fabbisogno del piano dall'acquisto reale.")

        if not to_buy:
            st.success("🎉 Nessun acquisto scoperto: il piano è già completamente coperto dalla dispensa.")
        else:
            pc1, pc2 = st.columns([2.2, 1])
            with pc1:
                strategy = st.selectbox(
                    "Come vuoi risparmiare?",
                    ["⚖️ Qualità / prezzo", "💰 Prezzo più basso", "⭐ Mantieni le marche preferite"],
                    key="shopping_strategy",
                    help="La strategia cambia il modo in cui MyDiet interpreta i risultati, non i dati ricevuti.",
                )
            with pc2:
                radius = st.number_input(
                    "📍 Raggio",
                    min_value=1,
                    max_value=30,
                    value=int(st.session_state.get("shopping_radius", 5) or 5),
                    step=1,
                    key="shopping_radius",
                )

            st.markdown("### 🧺 Il tuo paniere")
            total_need = len(to_buy)
            high = [r for r in to_buy if shopping_opportunity(r["required"], r["pantry"], r["unit"]) == "high"]
            medium = [r for r in to_buy if shopping_opportunity(r["required"], r["pantry"], r["unit"]) == "medium"]
            m1, m2, m3 = st.columns(3)
            with m1: st.metric("Prodotti da comprare", total_need)
            with m2: st.metric("Priorità alta", len(high))
            with m3: st.metric("Coperti", len(covered))

            st.info(
                "💡 **Ora MyDiet può interrogare dati pubblici aggiornati di Comprissimo.** "
                "I risultati sono indicativi e vanno sempre verificati nel punto vendita; il prezzo al kg/litro è usato quando la fonte lo pubblica."
            )

            st.markdown("### 🔎 Confronto prezzi reale")
            st.caption(f"Raggio preferito: {radius} km · fonte live: Comprissimo · cache di 15 minuti")

            fetch_count = min(10, len(to_buy))
            if st.button(f"🔄 Cerca prezzi aggiornati · {fetch_count} prodotti", type="primary", use_container_width=True):
                with st.spinner("Sto confrontando i prodotti del tuo paniere…"):
                    live = _shopping_live_for_items(to_buy, max_items=fetch_count)
                st.session_state.shopping_live_results = live
                st.session_state.shopping_live_time = datetime.now(ROME).strftime("%d/%m/%Y %H:%M")
                st.rerun()

            live_results = st.session_state.get("shopping_live_results", {})
            live_time = st.session_state.get("shopping_live_time")
            if live_time:
                st.caption(f"Ultimo confronto: {live_time}")

            if not live_results:
                st.markdown(
                    "<div class='card'><b>👆 Premi “Cerca prezzi aggiornati”</b><br>"
                    "MyDiet cercherà i prodotti più rilevanti nel catalogo pubblico di Comprissimo e mostrerà i risultati trovati.</div>",
                    unsafe_allow_html=True,
                )
            else:
                matched_products = sum(1 for r in to_buy if live_results.get(r["name"]))
                st.success(f"✅ Trovati risultati per **{matched_products}/{min(fetch_count, len(to_buy))}** prodotti analizzati.")

                for r in to_buy[:fetch_count]:
                    product_name = str(r["name"]).strip()
                    matches = live_results.get(product_name, [])
                    with st.container(border=True):
                        st.markdown(f"**{product_name}**")
                        st.caption(f"Da acquistare: **{r['need']:g} {r['unit']}** · in dispensa: {r['pantry']:g} {r['unit']}")
                        if not matches:
                            st.warning("Nessun abbinamento sufficientemente affidabile trovato nel catalogo live.")
                            q = urllib.parse.quote_plus(product_name)
                            st.link_button("🔎 Apri ricerca Comprissimo", f"https://comprissimo.ai/search?brand=&category=&has_price=True&on_sale=False&page=1&per_page=24&q={q}&sort=unit_price&supermarket=", use_container_width=True)
                        else:
                            best = sorted(matches, key=lambda x: (x["unit_price"], x["price"]))[:3]
                            b = best[0]
                            st.markdown(f"🏆 **Miglior prezzo unitario trovato: {b['unit_price']:.2f} €/{b['unit_kind']}** · {b['price']:.2f} € · **{b['store'] or 'negozio non indicato'}**")
                            if len(best) > 1:
                                for idx, alt in enumerate(best[1:], start=2):
                                    st.caption(f"{idx}. {alt['unit_price']:.2f} €/{alt['unit_kind']} · {alt['price']:.2f} € · {alt['store'] or 'negozio non indicato'}")
                            if b.get("compare_url"):
                                st.link_button("📊 Vedi confronto completo", b["compare_url"], use_container_width=True)
                            else:
                                st.link_button("🔎 Verifica su Comprissimo", b["search_url"], use_container_width=True)

                if len(to_buy) > fetch_count:
                    st.caption(f"ℹ️ Per velocità sono stati analizzati i primi {fetch_count} prodotti. I restanti {len(to_buy)-fetch_count} possono essere cercati al prossimo aggiornamento.")

                cart_summary = _shopping_cart_summary(live_results, to_buy[:fetch_count])
                if cart_summary:
                    st.markdown("### 🏪 Dove sto trovando più prodotti")
                    for item in cart_summary[:5]:
                        st.write(f"**{item['store']}** · {item['matched']} prodotti del paniere trovati")
                    st.caption("Questo è un indicatore di copertura del catalogo, non un totale della spesa: senza quantità/confezioni omogenee MyDiet non inventa un costo complessivo.")

            st.markdown("### 🧠 Strategia MyDiet")
            if strategy == "💰 Prezzo più basso":
                st.write("💰 Priorità al prezzo unitario pubblicato dalla fonte. Quando possibile, confronta €/kg, €/L o €/pz invece del solo prezzo della confezione.")
            elif strategy == "⭐ Mantieni le marche preferite":
                st.write("⭐ Quando il risultato contiene una marca riconoscibile, puoi privilegiarla; MyDiet non sostituisce automaticamente una marca con un'altra.")
            else:
                st.write("⚖️ Il miglior acquisto non è sempre il prezzo più basso: considera prezzo unitario, marca e presenza di un'offerta.")

            if high:
                names = ", ".join(r["name"] for r in high[:5])
                st.success(f"🔥 **Priorità:** {names}. Sono i prodotti per cui una quota importante del fabbisogno è ancora scoperta.")

            with st.expander("ℹ️ Come funzionano i prezzi", expanded=False):
                st.write(
                    "I dati live mostrati qui provengono dal catalogo pubblico di Comprissimo, che dichiara prezzi aggiornati quotidianamente e confronto tra più catene. "
                    "Il prezzo può comunque variare per punto vendita, giorno e disponibilità. SpesaChiara resta disponibile come seconda verifica, ma MyDiet non ne effettua scraping automatico."
                )
                st.caption("Obiettivo successivo: collegare il paniere a negozi realmente vicini all'utente e calcolare un totale solo quando abbiamo quantità/confezioni confrontabili.")

    with tab_pantry:
        st.subheader("📦 Cosa hai in casa")
        if pantry:
            for item in pantry:
                with st.container(border=True):
                    c1,c2,c3,c4=st.columns([4,1.2,0.8,0.8])
                    with c1:
                        st.markdown(f"**{item['name']}**")
                        matching=next((r for r in rows if _pantry_key(r["name"],r["unit"])==item["key"]),None)
                        if matching:
                            if item["qty"]>=matching["required"]:
                                st.caption("🟢 Sufficiente per il piano")
                            else:
                                missing=matching["required"]-item["qty"]
                                st.caption(f"🟡 Per il piano ne servono ancora {missing:g} {item['unit']}")
                        else:
                            st.caption("Non richiesto dal piano attuale")
                    with c2: st.markdown(f"**{item['qty']:g} {item['unit']}**")
                    with c3:
                        if st.button("−",key="pantry_minus_"+item["key"].replace("|","_"),use_container_width=True):
                            step=1 if item["unit"]=="pz" else 50
                            add_pantry_qty(item["name"],item["unit"],-step); st.rerun()
                    with c4:
                        if st.button("+",key="pantry_plus_"+item["key"].replace("|","_"),use_container_width=True):
                            step=1 if item["unit"]=="pz" else 50
                            add_pantry_qty(item["name"],item["unit"],step); st.rerun()
        else:
            st.info("La dispensa è vuota. Puoi aggiungere qui quello che hai già in casa.")

        st.divider()
        with st.expander("➕ Aggiungi alimento alla dispensa",expanded=False):
            suggestions=[{"name":r["name"],"unit":r["unit"]} for r in shopping_list()]
            names=sorted({x["name"] for x in suggestions})
            c1,c2,c3=st.columns([3,1,1])
            with c1: selected=st.selectbox("Alimento",["Nuovo alimento…"]+names,key="pantry_select")
            with c2: qty=st.number_input("Quantità",min_value=0.0,value=0.0,step=50.0,key="pantry_qty")
            with c3: unit=st.selectbox("Unità",["g","ml","pz"],key="pantry_unit")
            if selected=="Nuovo alimento…":
                custom_name=st.text_input("Nome alimento",placeholder="es. Pasta")
            else:
                custom_name=selected
                suggested_unit=next((x["unit"] for x in suggestions if x["name"]==selected),None)
                if suggested_unit in ("g","ml","pz"): st.caption(f"Unità suggerita dal piano: **{suggested_unit}**")
            if st.button("Salva in dispensa",type="primary") and custom_name.strip() and qty>0:
                add_pantry_qty(custom_name.strip(),unit,qty); st.rerun()

    with st.expander("ℹ️ Come funziona la dispensa",expanded=False):
        st.write("Quando registri un pasto come mangiato, MyDietApp scala dalla dispensa solo la quantità che era effettivamente presente. Se annulli il pasto, quella quantità viene ripristinata.")
        st.write("Nella sezione **Risparmio**, MyDiet evidenzia cosa conviene confrontare. I prezzi mostrati dai comparatori esterni restano la fonte verificata finché non integriamo un feed prezzi direttamente nell'app.")

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
        ac2.metric("⚡ Calorie attive" if a["active_source"] != "stima" else "⚡ Calorie attive stimate", f"{a['active_calories']:,} kcal".replace(",","."))
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
            weight=st.number_input("Peso attuale (kg)",30.,300.,float(st.session_state.p_weight),.1)
            goal_weight=st.number_input("Peso desiderato (kg)",30.,300.,float(st.session_state.get("p_goal_weight",st.session_state.p_weight)),.1)
            height=st.number_input("Altezza (cm)",100.,230.,float(st.session_state.p_height),.5)
        with c2:
            age=st.number_input("Età",13,100,int(st.session_state.p_age))
            sex=st.selectbox("Sesso",["male","female"],index=0 if st.session_state.p_sex=="male" else 1)
            activity=st.selectbox("Attività abituale",list(ACTIVITY_FACTORS.keys()),index=list(ACTIVITY_FACTORS.keys()).index(st.session_state.p_activity_level))
            water_goal=st.select_slider("Obiettivo acqua",options=list(range(1500,4001,250)),value=int(st.session_state.p_water_goal_ml),format_func=lambda x:f"{x/1000:.2f} L/giorno")
            quantity_mode_value=st.radio(
                "Come vuoi vedere le quantità?",
                ["porzioni","both","precise"],
                index=["porzioni","both","precise"].index(st.session_state.get("p_quantity_mode","porzioni")),
                format_func=lambda x: {
                    "porzioni":"👌 Porzioni — niente bilancia",
                    "both":"⚖️ Porzioni + grammature",
                    "precise":"⚖️ Preciso — mostra le grammature"
                }[x],
                help="Le grammature restano comunque nel motore per calcolare calorie e lista della spesa. Cambia solo ciò che vedi.",
            )
        if st.form_submit_button("Salva",type="primary"):
            st.session_state.p_name=name; st.session_state.p_weight=weight; st.session_state.p_goal_weight=goal_weight; st.session_state.p_height=height; st.session_state.p_age=age; st.session_state.p_sex=sex; st.session_state.p_activity_level=activity; st.session_state.p_water_goal_ml=water_goal; st.session_state.p_quantity_mode=quantity_mode_value; st.success("Profilo aggiornato")
    st.caption({
        "porzioni":"👌 Modalità quantità: **Porzioni** — niente bilancia. MyDietApp calcola comunque le quantità in background.",
        "both":"⚖️ Modalità quantità: **Porzioni + grammature**.",
        "precise":"⚖️ Modalità quantità: **Preciso** — grammature visibili."
    }.get(quantity_mode(), "👌 Modalità quantità: **Porzioni**."))

    # ---------------- Percorso peso ----------------
    st.divider()
    st.subheader("🎯 Il tuo obiettivo di peso")
    projection=weight_projection()
    if projection:
        target_date=projection["target_date"].strftime("%d/%m/%Y")
        st.caption(f"Da **{projection['current']:.1f} kg** a **{projection['goal']:.1f} kg** · stima indicativa con il deficit attuale di {projection['deficit']} kcal/giorno.")
        c1,c2,c3=st.columns(3)
        c1.metric("Peso attuale",f"{projection['current']:.1f} kg")
        c2.metric("Peso desiderato",f"{projection['goal']:.1f} kg")
        c3.metric("Stima obiettivo",target_date)
        chart_df=pd.DataFrame(projection["rows"])
        chart_df["Data"]=pd.to_datetime(chart_df["Data"])
        st.line_chart(chart_df,x="Data",y="Peso teorico",use_container_width=True,height=360)
        st.caption(f"Ogni punto rappresenta una settimana. Ritmo teorico: circa {projection['kg_per_week']:.2f} kg/settimana. È una **proiezione orientativa**, non una previsione garantita: il peso reale può scendere più velocemente o più lentamente.")
    else:
        current=float(st.session_state.get("p_weight",0) or 0)
        goal=float(st.session_state.get("p_goal_weight",current) or current)
        if goal >= current:
            st.info("Imposta un **peso desiderato inferiore al peso attuale** per visualizzare il percorso di discesa.")
        else:
            st.info("Completa i dati del profilo per visualizzare il percorso di peso.")

    ep=energy_profile()
    st.subheader("⚡ Obiettivo energetico")
    st.metric("Target alimentare stimato",f"{ep['target']:,} kcal/giorno".replace(",","."))
    st.metric("💧 Obiettivo acqua",f"{water_goal_ml()/1000:.2f} L/giorno")
    c1,c2=st.columns(2); c1.metric("BMR stimato",f"{ep['bmr_est']:,} kcal".replace(",",".")); c2.metric("Mantenimento stimato",f"{ep['maintenance_est']:,} kcal".replace(",","."))
    b=balance()
    if b["using_observed"]:
        st.info(f"🔥 Con i dati Health di oggi, il budget dinamico è circa **{b['live_target']:,} kcal**: consumo osservato {b['observed_burn']:,} + riposo residuo {b['remaining_rest']:,} → stima fine giornata {b['projected_burn']:,}, meno deficit {b['deficit']}.")
    st.caption(f"Acqua di oggi: {water_today_ml()/1000:.2f} L / {water_goal_ml()/1000:.2f} L. La registrazione è separata dalle calorie e viene conservata per data nella sessione corrente.")
    st.caption("Il target alimentare resta calcolato dal motore energetico. Il peso desiderato serve ora anche a costruire un percorso visivo orientativo verso l’obiettivo.")
