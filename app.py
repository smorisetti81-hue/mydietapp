import streamlit as st
import google.generativeai as genai
import json
import pandas as pd
import urllib.parse
import requests
import time
from datetime import datetime, timedelta, timezone
from PIL import Image
from streamlit_option_menu import option_menu

# --- CONFIGURAZIONE PAGINA E API ---
st.set_page_config(page_title="MyDietApp", page_icon="💪", layout="wide", initial_sidebar_state="expanded")

# Inserisci qui la tua API KEY di Gemini
genai.configure(api_key=st.secrets["GEMINI_API_KEY"])

# --- MENU LATERALE PREMIUM ---
with st.sidebar:
    st.markdown("<h2 style='text-align: center; color: #ff4b4b;'>MyDietApp 💪</h2>", unsafe_allow_html=True)
    st.divider()
    
    menu = option_menu(
        menu_title="Navigazione",  
        options=["Dashboard Progressi", "Carica Dati (Health)", "Mensa Smart", "Piano Alimentare & Spesa", "Tracking Attività"],
        icons=["house", "cloud-upload", "camera", "cart", "activity"],  
        menu_icon="compass", 
        default_index=0,
        styles={
            "container": {"padding": "0!important", "background-color": "transparent"},
            "icon": {"color": "#ff4b4b", "font-size": "18px"}, 
            "nav-link": {"font-size": "16px", "text-align": "left", "margin":"0px", "--hover-color": "#333333"},
            "nav-link-selected": {"background-color": "#ff4b4b", "color": "white", "font-weight": "bold"},
        }
    )

# --- SEZIONE 1: DASHBOARD PROGRESSI ---
if menu == "Dashboard Progressi":
    st.title("I tuoi Progressi 📈")
    st.write("Monitora l'andamento del peso e le tue statistiche principali.")
    
    # Dati simulati per il grafico
    storico_dati = pd.DataFrame({
        'Data': pd.date_range(end=pd.Timestamp.today(), periods=5, freq='W'),
        'Peso (kg)': [135.0, 134.1, 133.5, 132.8, 131.9]
    }).set_index('Data')
    
    # Metriche
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(label="Peso Attuale", value="131.9 kg", delta="-3.1 kg", delta_color="inverse")
    with col2:
        st.metric(label="Massa Muscolare", value="44.3 kg", delta="0.4 kg", delta_color="normal")
    with col3:
        st.metric(label="Massa Grassa", value="52.0 kg", delta="-1.5 kg", delta_color="inverse")
    with col4:
        st.metric(label="Acqua Corporea", value="60.8 kg", delta="0.2 kg", delta_color="normal")
        
    st.divider()
    
    col_grafico, col_allenamenti = st.columns([3, 2])
    
    with col_grafico:
        st.subheader("📉 Curva del Peso")
        st.line_chart(storico_dati, y="Peso (kg)", color="#ff4b4b")
    
    with col_allenamenti:
        st.subheader("🏃‍♂️ Ultimi Allenamenti")
        # Dati simulati per le card degli allenamenti
        df_allenamenti = pd.DataFrame([
            {"data": "2026-08-31", "tipo": "Tapis Roulant (Salita)", "durata": 45, "calorie": 450},
            {"data": "2026-08-29", "tipo": "Pesi - Upper Body", "durata": 60, "calorie": 320},
            {"data": "2026-08-27", "tipo": "Camminata Outdoor", "durata": 50, "calorie": 380}
        ])
        
        for index, row in df_allenamenti.iterrows():
            st.markdown(f"""
            <div style='background-color: #1e1e24; padding: 15px; border-radius: 10px; border-left: 5px solid #ff4b4b; margin-bottom: 10px;'>
                <div style='display: flex; justify-content: space-between; align-items: center;'>
                    <div>
                        <p style='margin: 0; color: #888; font-size: 14px;'>🗓️ {row['data']}</p>
                        <h5 style='margin: 5px 0 0 0; color: white;'>{row['tipo']}</h5>
                    </div>
                    <div style='text-align: right;'>
                        <h4 style='margin: 0; color: #ff4b4b;'>{row['calorie']} <span style='font-size: 14px; color: #888;'>kcal</span></h4>
                        <p style='margin: 0; color: #bbb; font-size: 12px;'>⏱️ {row['durata']} min</p>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)


# --- SEZIONE: CARICA DATI (HEALTH) ---
elif menu == "Carica Dati (Health)":
    st.title("🔄 Sincronizzazione Google Fit")
    st.write("Collega il tuo account Google per scaricare in automatico i dati sanitari e gli allenamenti.")

    # Recupera le credenziali in modo sicuro
    client_id = st.secrets["GOOGLE_CLIENT_ID"]
    client_secret = st.secrets["GOOGLE_CLIENT_SECRET"]
    redirect_uri = st.secrets["REDIRECT_URI"]

    # Scope necessari per leggere attività (passi, allenamenti) e composizione corporea
    scopes = "https://www.googleapis.com/auth/fitness.activity.read https://www.googleapis.com/auth/fitness.body.read"

    # 1. Controlla se abbiamo appena ricevuto il codice di autorizzazione
    query_params = st.query_params
    
    if "code" in query_params:
        auth_code = query_params["code"]
        
        # 2. Scambia il codice con il Token
        token_url = "https://oauth2.googleapis.com/token"
        token_data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": auth_code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri
        }
        
        response = requests.post(token_url, data=token_data)
        if response.status_code == 200:
            token_info = response.json()
            st.session_state["access_token"] = token_info["access_token"]
            st.success("Autenticazione riuscita! Account collegato.")
            st.query_params.clear()
        else:
            st.error("Errore durante l'autenticazione. Riprova.")

    # 3. Mostra l'interfaccia in base allo stato del login
    if "access_token" not in st.session_state:
        # Pulisce le chiavi da spazi invisibili o invii accidentali
        clean_client_id = client_id.strip()
        clean_redirect = redirect_uri.strip()
        
        # Genera il link per il login con codifica URL rigorosa
        auth_base_url = "https://accounts.google.com/o/oauth2/v2/auth"
        auth_url = f"{auth_base_url}?client_id={clean_client_id}&redirect_uri={urllib.parse.quote(clean_redirect, safe='')}&response_type=code&scope={urllib.parse.quote(scopes, safe='')}&access_type=offline&prompt=select_account"
        
        # Stampa il link di debug a schermo
        st.write("URL generato (per controllo):")
        st.code(auth_url)
        
        # Usa un link Markdown che costringe l'apertura in una nuova scheda pulita
        st.markdown(f"### [👉 CLICCA QUI PER ACCEDERE CON GOOGLE FIT]({auth_url})")
    else:
        st.write("✅ **Account Google collegato con successo.**")
        
        if st.button("Scarica ultimi dati", type="primary"):
            with st.spinner("Connessione a Google Fit in corso..."):
                # Fuso orario Italiano (UTC+2 in estate) per allineare i server
                tz_ita = timezone(timedelta(hours=2))
                ora_attuale = datetime.now(tz_ita)
                
                # Partiamo esattamente dalla mezzanotte di 6 giorni fa
                mezzanotte_oggi = ora_attuale.replace(hour=0, minute=0, second=0, microsecond=0)
                inizio_settimana = mezzanotte_oggi - timedelta(days=6)
                
                start_millis = int(inizio_settimana.timestamp() * 1000)
                now_millis = int(ora_attuale.timestamp() * 1000)

                headers = {
                    "Authorization": f"Bearer {st.session_state['access_token']}",
                    "Content-Type": "application/json"
                }
                
                # Chiediamo a Google Fit la somma dei passi giornalieri allineati alla mezzanotte
                body = {
                    "aggregateBy": [{
                        "dataTypeName": "com.google.step_count.delta",
                        "dataSourceId": "derived:com.google.step_count.delta:com.google.android.gms:estimated_steps"
                    }],
                    "bucketByTime": { "durationMillis": 86400000 },
                    "startTimeMillis": start_millis,
                    "endTimeMillis": now_millis
                }
                
                fit_url = "https://www.googleapis.com/fitness/v1/users/me/dataset:aggregate"
                fit_response = requests.post(fit_url, headers=headers, json=body)
                
                if fit_response.status_code == 200:
                    st.success("✅ Dati scaricati con successo!")
                    dati_fit = fit_response.json()
                    
                    # Elaborazione del JSON per estrarre i passi
                    passi_giornalieri = []
                    for b in dati_fit.get("bucket", []):
                        # Convertiamo i millisecondi in data, forzando il fuso orario italiano
                        bucket_start = int(b.get("startTimeMillis", 0))
                        data_gg = pd.to_datetime(bucket_start, unit='ms').tz_localize('UTC').tz_convert(tz_ita).strftime('%d/%m')
                        
                        passi_totali = 0
                        for ds in b.get("dataset", []):
                            for p in ds.get("point", []):
                                for v in p.get("value", []):
                                    passi_totali += int(v.get("intVal", 0))
                        
                        passi_giornalieri.append({"Data": data_gg, "Passi": passi_totali})
                    
                    df_passi = pd.DataFrame(passi_giornalieri)
                    
                    st.divider()
                    st.subheader("👣 Andamento Passi (Ultimi 7 giorni)")
                    
                    # Mostriamo i passi di oggi (ultimo elemento della lista)
                    passi_oggi = df_passi.iloc[-1]['Passi']
                    st.metric(label="Passi rilevati oggi", value=f"{passi_oggi:,}".replace(',', '.'))
                    
                    # Disegniamo il grafico
                    st.bar_chart(df_passi.set_index("Data"), color="#ff4b4b")
                else:
                    st.error(f"Errore {fit_response.status_code} dal server Google.")

# --- SEZIONE 3: MENSA SMART ---
elif menu == "Mensa Smart":
    st.title("Scansione Menu Mensa 🍽️")
    st.write("Fotografa il menu in diretta o carica un'immagine: ti dirò esattamente cosa mettere sul vassoio per non sgarrare.")
    
    tab1, tab2 = st.tabs(["📸 Scatta Foto", "📁 Carica dalla Galleria"])
    
    menu_image = None
    
    with tab1:
        st.write("Usa la fotocamera del tuo smartphone per inquadrare il menu.")
        scatto_live = st.camera_input("Scatta foto al menu")
        if scatto_live is not None:
            menu_image = scatto_live
            
    with tab2:
        st.write("Oppure carica una foto che hai già scattato.")
        file_caricato = st.file_uploader("Carica file...", type=["jpg", "jpeg", "png"], key="mensa")
        if file_caricato is not None:
            menu_image = file_caricato
            
    if menu_image is not None:
        img_mensa = Image.open(menu_image)
        st.success("Immagine acquisita con successo!")
        
        st.image(img_mensa, caption='Menu pronto per l\'analisi', width=400)
        
        if st.button("Trova il pasto ideale", type="primary"):
            with st.spinner("Lettura del menu in corso..."):
                try:
                    model = genai.GenerativeModel('gemini-2.5-flash')
                    prompt = """
                    Sei un nutrizionista sportivo. L'utente pesa circa 135 kg e vuole dimagrire mantenendo massa muscolare.
                    Leggi il menu nella foto e fornisci il tuo SUGGERIMENTO DIRETTO su cosa ordinare oggi.
                    Sii estremamente conciso (deve leggerlo mentre è in fila alla mensa). Formatta la risposta ESATTAMENTE così:
                    
                    🟢 **COSA ORDINARE:** [Scrivi i piatti esatti da prendere, es. Secondo di carne + Contorno]
                    💡 **PERCHÉ:** [1 riga di motivazione nutrizionale]
                    ⚠️ **DA EVITARE:** [Cosa non chiedere al bancone, es. salse, pane]
                    """
                    response = model.generate_content([prompt, img_mensa])
                    st.markdown(response.text)
                except Exception as e:
                    st.error(f"Errore durante la lettura: {e}")

# --- SEZIONE 4: PIANO ALIMENTARE E SPESA ---
elif menu == "Piano Alimentare & Spesa":
    st.title("Piano Alimentare & Lista della Spesa 🛒")
    st.write("Accendi i giorni di ufficio. Modifica i piatti a casa come preferisci e poi ricalcola la spesa in fondo alla pagina!")
    
    st.subheader("🏢 Pianificazione Ufficio (Lun-Ven)")
    giorni_lavorativi = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì"]
    ufficio_days = []
    
    colonne = st.columns(5)
    for i, giorno in enumerate(giorni_lavorativi):
        default_on = True if giorno in ["Lunedì", "Mercoledì"] else False
        if colonne[i].toggle(giorno, value=default_on):
            ufficio_days.append(giorno)
            
    st.divider()
    
    if st.button("Genera Piano Iniziale", type="primary"):
        with st.spinner("Elaborazione menu base e spesa..."):
            try:
                if len(ufficio_days) > 0:
                    regola_ufficio = f"2. GIORNI IN UFFICIO: {', '.join(ufficio_days)}. IN QUESTI GIORNI L'UTENTE FA PRANZO E CENA IN MENSA. Per questi pasti scrivi SOLO '🏢 UFFICIO: Usa scanner Mensa' e lascia array 'req' vuoto []."
                else:
                    regola_ufficio = "2. GIORNI IN UFFICIO: Nessuno. L'utente mangia a casa TUTTI I GIORNI. Devi generare colazione, pranzo, spuntino e cena per tutti e 7 i giorni."

                model = genai.GenerativeModel('gemini-2.5-flash')
                prompt = f"""
                Agisci come un nutrizionista. Crea un piano settimanale per un uomo di 135 kg in deficit.
                
                REGOLE:
                1. Famiglia: Ha moglie e 2 bambini. I pasti a casa DEVONO essere per tutti.
                {regola_ufficio}
                3. Lista Spesa: Crea un'unica lista con quantità. PER ALIMENTI COSTOSI inserisci SEMPRE un'alternativa economica.
                4. COLLEGAMENTO: Ogni pasto deve avere un array "req" con l'elenco esatto delle stringhe degli ingredienti.
                
                RESTITUISCI ESCLUSIVAMENTE JSON CON QUESTA STRUTTURA:
                {{
                    "piano": {{
                        "Lunedì": {{
                            "☕ Colazione": {{"testo": "Yogurt con avena", "req": ["3x Yogurt greco", "500g Avena"]}},
                            "🍽️ Pranzo": {{"testo": "🏢 UFFICIO", "req": []}}
                        }}
                    }},
                    "spesa": {{
                        "Ortofrutta": ["1kg Zucchine"],
                        "Carne e Pesce": ["500g Salmone (🔄 o 500g Merluzzo)"]
                    }}
                }}
                """
                response = model.generate_content(prompt)
                
                testo_json = response.text.replace('```json', '').replace('