import streamlit as st
import json
import pandas as pd
import requests
from groq import Groq
from datetime import datetime
import pypdf
import os
from duckduckgo_search import DDGS

# --- APP CONFIG & SECRETS ---
st.set_page_config(page_title="Job Copilot PRO", layout="wide", page_icon="🚀")

# Groq Setup
if "GROQ_API_KEY" in st.secrets:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
else:
    st.error("Missing GROQ_API_KEY in Secrets!")
    st.stop()

# --- DATABASE LOGIC ---
DB_PATH = "user_data.json"

def init_db():
    if not os.path.exists(DB_PATH):
        with open(DB_PATH, "w") as f:
            json.dump({"applied": [], "cv_text": "", "skills": [], "target_role": ""}, f)

def get_db():
    with open(DB_PATH, "r") as f: return json.load(f)

def save_db(data):
    with open(DB_PATH, "w") as f: json.dump(data, f)

init_db()

# --- HELPER: MARKET DATA ---
@st.cache_data(ttl=3600)
def fetch_market_data():
    hubs = ["Berlin", "London", "Amsterdam", "New York", "Singapore"]
    results = []
    for city in hubs:
        slug = city.lower().replace(" ", "-")
        try:
            # Attempt API call
            res = requests.get(f"https://api.teleport.org/api/urban_areas/slug:{slug}/scores/", timeout=5).json()
            score = round(res['teleport_city_score'], 1)
            results.append({"City": city, "Fit %": f"{int(score)}%", "COL Score": score, "Status": "Live Data"})
        except:
            # Fail-safe Hardcoded Data (Realistic benchmarks)
            defaults = {"Berlin": 68.2, "London": 58.9, "Amsterdam": 65.4, "New York": 52.1, "Singapore": 62.5}
            results.append({"City": city, "Fit %": "Calculated", "COL Score": defaults.get(city, 50), "Status": "Benchmark"})
    return results

# --- VIEW 1: DASHBOARD ---
def view_dashboard():
    st.title("🌍 Global Market Intelligence")
    st.caption(f"Last sync: {datetime.now().strftime('%H:%M')}")
    
    with st.spinner("Fetching market benchmarks..."):
        data = fetch_market_data()
        df = pd.DataFrame(data)
        st.table(df)

# --- VIEW 2: COMPANIES & SCOUTER ---
def view_companies():
    st.title("🏢 Real-Time Job Scouting")
    db = get_db()
    
    c1, c2 = st.columns(2)
    role = c1.text_input("Target Job Title", value=db.get("target_role", "Software Engineer"))
    loc = c2.text_input("Preferred Region", "Europe")

    if st.button("🔍 Scout Live Roles"):
        if role != db.get("target_role"):
            db["target_role"] = role
            save_db(db)
            
        with st.spinner(f"Searching for {role} roles in {loc}..."):
            try:
                with DDGS() as ddgs:
                    query = f"site:lever.co OR site:greenhouse.io '{role}' {loc} jobs"
                    results = list(ddgs.text(query, max_results=8))
                
                if results:
                    for i, job in enumerate(results):
                        with st.container(border=True):
                            st.subheader(job['title'])
                            st.write(f"🔗 [View Career Page]({job['href']})")
                            st.write(job['body'][:250] + "...")
                            
                            col_a, col_b = st.columns(2)
                            if col_a.button(f"✨ Optimize CV", key=f"opt_{i}"):
                                if not db['cv_text']:
                                    st.warning("Please upload your CV in the Profile tab first!")
                                else:
                                    # Strict AI CV Optimization
                                    prompt = f"""
                                    JOB DESCRIPTION: {job['body']}
                                    MY ORIGINA
