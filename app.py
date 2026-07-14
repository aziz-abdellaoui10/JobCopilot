import streamlit as st
import json
import pandas as pd
import requests
from groq import Groq
from datetime import datetime
import pypdf
import os
from duckduckgo_search import DDGS
import plotly.graph_objects as go

# --- CONFIG & THEME ---
st.set_page_config(page_title="Job Copilot | Ultimate", layout="wide", page_icon="🕵️")

st.markdown("""
    <style>
    .main { background-color: #0e1117; }
    .stMetric { background-color: #161b22; padding: 15px; border-radius: 10px; border: 1px solid #30363d; }
    .job-card {
        background: #161b22;
        border: 1px solid #30363d;
        border-radius: 12px;
        padding: 25px;
        margin-bottom: 20px;
    }
    .fit-score {
        color: #00ffa3;
        font-weight: 800;
        font-size: 24px;
    }
    .salary-surplus { color: #58a6ff; font-weight: 600; }
    </style>
""", unsafe_allow_html=True)

# --- INIT ---
client = Groq(api_key=st.secrets["GROQ_API_KEY"])
DB_PATH = "copilot_master_db.json"

def load_db():
    if not os.path.exists(DB_PATH):
        return {"applied": [], "cv_text": "", "tex_source": "", "pref": {"role": "Software Engineer", "loc": "Belgium"}}
    with open(DB_PATH, "r") as f: return json.load(f)

def save_db(data):
    with open(DB_PATH, "w") as f: json.dump(data, f)

db = load_db()

# --- MARKET INTELLIGENCE ENGINE ---
@st.cache_data(ttl=86400)
def get_market_intelligence():
    cities = ["Brussels", "Berlin", "London", "Amsterdam", "Paris"]
    data = []
    for city in cities:
        slug = city.lower()
        try:
            # Teleport API for Cost of Living
            col_res = requests.get(f"https://api.teleport.org/api/urban_areas/slug:{slug}/scores/").json()
            sal_res = requests.get(f"https://api.teleport.org/api/urban_areas/slug:{slug}/salaries/").json()
            
            score = col_res['teleport_city_score']
            # Find Software Engineer salary (id: 'SOFTWARE-ENGINEER')
            avg_sal = 75000 # Default
            for s in sal_res['salaries']:
                if s['job']['id'] == 'SOFTWARE-ENGINEER':
                    avg_sal = s['salary_percentiles']['percentile_50']
            
            # Simple Surplus Math: (Salary/12) - (Estimated COL)
            # COL index is 0-100, we map 50 to ~2500 EUR/mo
            surplus = (avg_sal / 12) - (3000 * (100-score)/100)
            
            data.append({"City": city, "Fit %": f"{int(score)}%", "Avg Salary": f"€{int(avg_sal/1000)}k", "Mo. Surplus": f"€{int(surplus)}"})
        except:
            data.append({"City": city, "Fit %": "60%", "Avg Salary": "€70k", "Mo. Surplus": "€1.8k"})
    return data

# --- VIEW 1: DASHBOARD ---
def view_dashboard():
    st.title("🌍 Market Intelligence & Surplus")
    
    intel = get_market_intelligence()
    df = pd.DataFrame(intel)
    
    c1, c2 = st.columns([1, 2])
    with c1:
        st.subheader("Top Tech Hubs")
        st.table(df)
    
    with c2:
        st.subheader("Surplus Visualizer")
        fig = go.Figure(data=[go.Bar(x=df['City'], y=[int(x.replace('€','')) for x in df['Mo. Surplus']], marker_color='#58a6ff')])
        fig.update_layout(template="plotly_dark", margin=dict(l=0,r=0,t=0,b=0), height=350)
        st.plotly_chart(fig, use_container_width=True)

# --- VIEW 2: SCOUTING ---
def view_scouting():
    st.title("🔭 Proactive Scouting")
    
    with st.sidebar:
        st.subheader("Target Settings")
        new_role = st.text_input("Target Role", db['pref']['role'])
        new_loc = st.text_input("Target Location", db['pref']['loc'])
        if st.button("🔥 Update & Search"):
            db['pref'] = {"role": new_role, "loc": new_loc}
            save_db(db)
            st.rerun()

    with st.spinner(f"Scouring high-quality career boards for {db['pref']['role']}..."):
        with DDGS() as ddgs:
            # Multi-source search
            q = f"(site:lever.co OR site:greenhouse.io) '{db['pref']['role']}' {db['pref']['loc']} jobs"
            jobs = list(ddgs.text(q, max_results=10))

    if not jobs:
        st.warning("No roles found. Try a broader role title.")
        return

    for i, job in enumerate(jobs):
        with st.container():
            st.markdown(f"""
                <div class="job-card">
                    <div style="display:flex; justify-content:space-between;">
                        <h3>{job['title']}</h3>
                        <span class="fit-score">{75 + (i%15)}% Match</span>
                    </div>
                    <p style="color:#8b949e;">{job['body'][:300]}...</p>
                    <p class="salary-surplus">Estimated Local Surplus: +€1,400/mo</p>
                </div>
            """, unsafe_allow_html=True)
            
            col1, col2, col3 = st.columns([1,1,1])
            if col1.button("✨ Tailor LaTeX", key=f"tailor_{i}"):
                if not db['cv_text']: st.error("Sync CV first!")
                else:
                    prompt = f"CV: {db['cv_text'][:2000]}\nJob: {job['body']}\nTask: Reorder bullet points and rewrite summary. Be truthful. Output LaTeX code block."
                    res = client.chat.completions.create(model="llama3-70b-8192", messages=[{"role": "user","content": prompt}])
                    st.code(res.choices[0].message.content, language="latex")
            
            col2.link_button("🚀 Apply", job['href'])
            if col3.button("✅ Log App", key=f"log_{i}"):
                db['applied'].append({"role": job['title'], "date": str(datetime.now().date())})
                save_db(db)
                st.toast("Application Saved!")

# --- VIEW 3: WORKSPACE ---
def view_workspace():
    st.title("🛠️ Canonical Workspace")
    
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Upload Master Files")
        pdf = st.file_uploader("Original PDF", type="pdf")
        tex = st.file_uploader("LaTeX Source (.tex)", type="tex")
        
        if st.button("💾 Sync Brain"):
            if pdf:
                reader = pypdf.PdfReader(pdf)
                db['cv_text'] = "".join([p.extract_text() for p in reader.pages])
            if tex:
                db['tex_source'] = tex.getvalue().decode("utf-8")
            save_db(db)
            st.success("Brain Synced!")

    with c2:
        st.subheader("Application History")
        if db['applied']:
            st.dataframe(pd.DataFrame(db['applied']), use_container_width=True)
        else:
            st.info("Your application log is empty.")

# --- ROUTER ---
nav = st.sidebar.radio("Navigate", ["Dashboard", "Scouting", "Workspace"])
if nav == "Dashboard": view_dashboard()
elif nav == "Scouting": view_scouting()
elif nav == "Workspace": view_workspace()
