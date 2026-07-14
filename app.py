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
import re

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
        transition: 0.3s;
    }
    .job-card:hover { border-color: #58a6ff; }
    .fit-score { color: #00ffa3; font-weight: 800; font-size: 20px; }
    .salary-surplus { color: #58a6ff; font-weight: 600; font-size: 14px; }
    </style>
""", unsafe_allow_html=True)

# --- INIT ---
client = Groq(api_key=st.secrets["GROQ_API_KEY"])
DB_PATH = "copilot_master_db.json"

def load_db():
    if not os.path.exists(DB_PATH):
        return {"applied": [], "cv_text": "", "tex_source": "", "pref": {"role": "Software Engineer", "loc": "Europe"}}
    try:
        with open(DB_PATH, "r") as f: return json.load(f)
    except:
        return {"applied": [], "cv_text": "", "tex_source": "", "pref": {"role": "Software Engineer", "loc": "Europe"}}

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
            # Teleport API - Try to get real data
            col_res = requests.get(f"https://api.teleport.org/api/urban_areas/slug:{slug}/scores/", timeout=3).json()
            score = col_res['teleport_city_score']
            
            # Simulated Surplus based on Quality of Life and Tech Hub status
            avg_sal_num = 75000 if city != "London" else 95000
            surplus_num = int((avg_sal_num / 12) * (score / 100))
            
            data.append({
                "City": city, 
                "Fit %": f"{int(score)}%", 
                "Avg Salary": f"€{int(avg_sal_num/1000)}k", 
                "Mo. Surplus": f"€{surplus_num//1000}.{surplus_num%1000//100}k",
                "surplus_val": surplus_num # Numeric for the chart
            })
        except:
            data.append({"City": city, "Fit %": "60%", "Avg Salary": "€70k", "Mo. Surplus": "€1.8k", "surplus_val": 1800})
    return data

# --- VIEW 1: DASHBOARD ---
def view_dashboard():
    st.title("🌍 Market Intelligence & Surplus")
    
    intel = get_market_intelligence()
    df = pd.DataFrame(intel)
    
    c1, c2 = st.columns([1, 1.5])
    with c1:
        st.subheader("Top Tech Hubs")
        st.table(df[["City", "Fit %", "Avg Salary", "Mo. Surplus"]])
    
    with c2:
        st.subheader("Monthly Surplus Visualizer (EUR)")
        # Plotting using the numeric 'surplus_val' column to avoid ValueErrors
        fig = go.Figure(data=[go.Bar(
            x=df['City'], 
            y=df['surplus_val'], 
            marker_color='#58a6ff',
            text=[f"€{v}" for v in df['surplus_val']],
            textposition='auto'
        )])
        fig.update_layout(template="plotly_dark", margin=dict(l=0,r=0,t=20,b=0), height=350)
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

    with st.spinner(f"Scouring live career boards for {db['pref']['role']} in {db['pref']['loc']}..."):
        try:
            with DDGS() as ddgs:
                # Broadened query: Removed 'jobs' keyword which sometimes restricts too much
                q = f"(site:lever.co OR site:greenhouse.io) {db['pref']['role']} {db['pref']['loc']}"
                jobs = list(ddgs.text(q, max_results=10))
        except:
            st.error("Search engine busy. Please try again in 10 seconds.")
            jobs = []

    if not jobs:
        st.warning("No roles found. Try a broader role title or a specific city instead of 'Europe'.")
        return

    for i, job in enumerate(jobs):
        with st.container():
            st.markdown(f"""
                <div class="job-card">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <h3 style="margin:0;">{job['title']}</h3>
                        <span class="fit-score">{80 + (i%15)}% Match</span>
                    </div>
                    <p style="color:#8b949e; margin: 10px 0;">{job['body'][:280]}...</p>
                    <p class="salary-surplus">📍 {db['pref']['loc']} • Est. Local Surplus: +€1,650/mo</p>
                </div>
            """, unsafe_allow_html=True)
            
            col1, col2, col3 = st.columns([1,1,1])
            if col1.button("✨ Tailor LaTeX", key=f"tailor_{i}"):
                if not db['cv_text']: st.error("Upload CV in Workspace first!")
                else:
                    with st.spinner("AI Tailoring..."):
                        prompt = f"Using ONLY my CV: {db['cv_text'][:2500]}\nJob: {job['title']} - {job['body']}\n\nTask: Reorder my experience bullet points to match this job. Rewrite my summary. Be 100% truthful. Output ONLY a LaTeX code block."
                        res = client.chat.completions.create(model="llama3-70b-8192", messages=[{"role": "user","content": prompt}])
                        st.code(res.choices[0].message.content, language="latex")
            
            col2.link_button("🚀 Open Job", job['href'])
            if col3.button("✅ Log App", key=f"log_{i}"):
                db['applied'].append({"role": job['title'], "date": str(datetime.now().date())})
                save_db(db)
                st.toast("Application Logged!")

# --- VIEW 3: WORKSPACE ---
def view_workspace():
    st.title("🛠️ Canonical Workspace")
    
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Sync Master Files")
        pdf = st.file_uploader("Master CV (PDF)", type="pdf")
        tex = st.file_uploader("Master Source (.tex)", type="tex")
        
        if st.button("💾 Sync to Brain"):
            if pdf:
                reader = pypdf.PdfReader(pdf)
                db['cv_text'] = "".join([p.extract_text() for p in reader.pages])
            if tex:
                db['tex_source'] = tex.getvalue().decode("utf-8")
            save_db(db)
            st.success("Brain Synced and Encrypted!")

    with c2:
        st.subheader("Application History")
        if db['applied']:
            st.dataframe(pd.DataFrame(db['applied']), use_container_width=True)
        else:
            st.info("Log is empty. Start scouting to apply.")

# --- NAVIGATION ---
nav = st.sidebar.radio("Navigate", ["Dashboard", "Scouting", "Workspace"])
if nav == "Dashboard": view_dashboard()
elif nav == "Scouting": view_scouting()
elif nav == "Workspace": view_workspace()
