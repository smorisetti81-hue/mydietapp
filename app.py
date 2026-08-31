import streamlit as st
import google.generativeai as genai
import json
import pandas as pd
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
    st.title("📥 Importa Dati Samsung Health")
    st.write("Puoi selezionare **anche più file contemporaneamente** dalle cartelle del telefono per unificarli.")

    col_up1, col_up2 = st.columns(2)

    with col_up1:
        st.subheader("🏋️ Allenamenti")
        st.write("Seleziona i file JSON dalla cartella `com.samsung.shealth.exercise`")
        uploaded_exercises = st.file_uploader("JSON Allenamenti (puoi sceglierne più di uno)", type=["json"], accept_multiple_files=True, key="ex_multi")
        
        if uploaded_exercises:
            all_ex_data = []
            for file in uploaded_exercises:
                try:
                    content = json.load(file)
                    if isinstance(content, list):
                        all_ex_data.extend(content)
                    elif isinstance(content, dict):
                        all_ex_data.append(content)
                except Exception as e:
                    st.error(f"Errore nel file {file.name}: {e}")
            
            if all_ex_data:
                df_ex = pd.DataFrame(all_ex_data)
                st.success(f"File allenamenti caricato con successo! {len(df_ex)} record totali.")
                st.dataframe(df_ex.head(10), use_container_width=True)

    with col_up2:
        st.subheader("⚖️ Composizione Corporea")
        st.write("Seleziona i file JSON dalla cartella `com.samsung.shealth.body_composition`")
        uploaded_bodies = st.file_uploader("JSON Composizione (puoi sceglierne più di uno)", type=["json"], accept_multiple_files=True, key="body_multi")
        
        if uploaded_bodies:
            all_body_data = []
            for file in uploaded_bodies:
                try:
                    content = json.load(file)
                    if isinstance(content, list):
                        all_body_data.extend(content)
                    elif isinstance(content, dict):
                        all_body_data.append(content)
                except Exception as e:
                    st.error(f"Errore nel file {file.name}: {e}")
            
            if all_body_data:
                df_body = pd.DataFrame(all_body_data)
                st.success(f"File composizione caricato con successo! {len(df_body)} record totali.")
                st.dataframe(df_body.head(10), use_container_width=True)

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
                    model = genai.GenerativeModel('gemini-3.6-flash')
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

                model = genai.GenerativeModel('gemini-3.6-flash')
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
                
                testo_json = response.text.replace('```json', '').replace('```', '').strip()
                st.session_state['dati_generati'] = json.loads(testo_json)
                
                for key in list(st.session_state.keys()):
                    if key.startswith("chk_"):
                        del st.session_state[key]
                        
            except Exception as e:
                st.error(f"Errore durante l'elaborazione: {e}")

    if 'dati_generati' in st.session_state:
        dati = st.session_state['dati_generati']
        
        st.subheader("🛒 Dispensa & Spesa")
        st.write("Spunta gli ingredienti per 'accendere' i pasti.")
        
        spesa = dati.get('spesa', {})
        for categoria, ingredienti in spesa.items():
            if ingredienti: 
                st.markdown(f"**{categoria}**")
                for ingrediente in ingredienti:
                    if f"chk_{ingrediente}" not in st.session_state:
                        st.session_state[f"chk_{ingrediente}"] = False
                    st.checkbox(ingrediente, key=f"chk_{ingrediente}")
        
        st.divider()

        st.subheader("🗓️ Il tuo Menu Settimanale")
        
        piano = dati.get('piano', {})
        giorni_totali = list(piano.keys())
        
        for giorno, pasti in piano.items():
            with st.expander(f"📌 {giorno}", expanded=True):
                if isinstance(pasti, dict):
                    for nome_pasto, info in pasti.items():
                        if isinstance(info, dict):
                            testo = info.get('testo', '')
                            reqs = info.get('req', [])
                            mancanti = [r for r in reqs if not st.session_state.get(f"chk_{r}", False)]
                            
                            is_ufficio = "UFFICIO" in testo
                            status_icon = "✅" if (is_ufficio or not mancanti) else "🔒"
                            
                            # CAMPO DI TESTO MODIFICABILE
                            nuovo_testo = st.text_input(
                                f"{status_icon} {nome_pasto}", 
                                value=testo, 
                                key=f"edit_{giorno}_{nome_pasto}",
                                disabled=is_ufficio
                            )
                            
                            if nuovo_testo != testo:
                                st.session_state['dati_generati']['piano'][giorno][nome_pasto]['testo'] = nuovo_testo
                                
                            if mancanti and not is_ufficio:
                                st.caption(f"*(Manca in dispensa: {', '.join(mancanti)})*")
                        else:
                            st.markdown(f"**{nome_pasto}:** {info}")
                
                st.write("")
                altri_giorni = [g for g in giorni_totali if g != giorno]
                col_swap1, col_swap2 = st.columns([2, 1])
                with col_swap1:
                    target_swap = st.selectbox(f"Sposta il menu di {giorno} a:", altri_giorni, key=f"sel_swap_{giorno}")
                with col_swap2:
                    st.write("") 
                    if st.button("🔄 Scambia", key=f"btn_swap_{giorno}"):
                        temp_menu = st.session_state['dati_generati']['piano'][giorno]
                        st.session_state['dati_generati']['piano'][giorno] = st.session_state['dati_generati']['piano'][target_swap]
                        st.session_state['dati_generati']['piano'][target_swap] = temp_menu
                        st.rerun()

        st.divider()
        
        st.subheader("🔄 Sincronizza la Spesa")
        st.write("Hai fatto modifiche manuali ai piatti? Clicca qui sotto per rigenerare la lista della spesa.")
        
        if st.button("Ricalcola Spesa con IA", type="secondary"):
            with st.spinner("Analisi del nuovo menu e aggiornamento della lista spesa..."):
                try:
                    menu_attuale = json.dumps(st.session_state['dati_generati']['piano'], ensure_ascii=False)
                    
                    model = genai.GenerativeModel('gemini-3.6-flash')
                    prompt_ricalcolo = f"""
                    Agisci come un nutrizionista. L'utente ha modificato manualmente il suo menu settimanale. 
                    Ecco il menu attuale in formato JSON:
                    {menu_attuale}
                    
                    IL TUO COMPITO:
                    1. Leggi i piatti elencati nella chiave "testo" di ogni pasto.
                    2. Genera una NUOVA lista della spesa unificata (con quantità e alternative economiche). Ignora i pasti in UFFICIO.
                    3. Aggiorna l'array "req" di OGNI pasto nel piano in modo che contenga le nuove stringhe ESATTE della lista della spesa appena generata.
                    
                    RESTITUISCI ESCLUSIVAMENTE JSON CON QUESTA STRUTTURA:
                    {{
                        "piano": <IL PIANO CON GLI ARRAY 'req' AGGIORNATI>,
                        "spesa": {{
                            "Ortofrutta": [],
                            "Carne e Pesce": [],
                            "Latticini e Frigo": [],
                            "Dispensa": []
                        }}
                    }}
                    """
                    response_ric = model.generate_content(prompt_ricalcolo)
                    testo_json_ric = response_ric.text.replace('```json', '').replace('```', '').strip()
                    dati_aggiornati = json.loads(testo_json_ric)
                    
                    st.session_state['dati_generati'] = dati_aggiornati
                    
                    for key in list(st.session_state.keys()):
                        if key.startswith("chk_"):
                            del st.session_state[key]
                            
                    st.rerun()
                except Exception as e:
                    st.error(f"Errore durante il ricalcolo: {e}. Riprova.")

# --- SEZIONE 5: TRACKING ATTIVITÀ ---
elif menu == "Tracking Attività":
    st.title("Tracking Attività 🏋️")
    st.info("Qui potremo inserire l'interfaccia per loggare i nuovi allenamenti e collegarci a un database.")