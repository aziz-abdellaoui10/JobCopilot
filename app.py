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
    st.error("Missing GROQ_API_KEY in Streamlit Secrets!")
    st.stop()

# --- DATABASE LOGIC ---
DB_PATH = "user_data.json"

def init_db():
    if not os.path.exists(DB_PATH):
        with open(DB_PATH, "w") as f:
            json.dump({"applied": [], "cv_text": "", "target_role": ""}, f)

def get_db():
    try:
        with open(DB_PATH, "r") as f: 
            return json.load(f)
    except:
        return {"applied": [], "cv_text": "", "target_role": ""}

def save_db(data):
    with open(DB_PATH, "w") as f: 
        json.dump(data, f)

init_db()

# --- HELPER: MARKET DATA ---
@st.cache_data(ttl=3600)
def fetch_market_data():
    hubs = ["Berlin", "London", "Amsterdam", "New York", "Singapore"]
    results = []
    for city in hubs:
        slug = city.lower().replace(" ", "-")
        try:
            res = requests.get(f"https://api.teleport.org/api/urban_areas/slug:{slug}/scores/", timeout=5).json()
            score = round(res['teleport_city_score'], 1)
            results.append({"City": city, "Fit %": f"{int(score)}%", "COL Score": score, "Status": "Live Data"})
        except:
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
        db["target_role"] = role
        save_db(db)
            
        with st.spinner(f"Searching for {role} roles in {loc}..."):
            try:
                with DDGS() as ddgs:
                    search_query = f"site:lever.co OR site:greenhouse.io '{role}' {loc} jobs"
                    results = list(ddgs.text(search_query, max_results=8))
                
                if results:
                    for i, job in enumerate(results):
                        with st.container(border=True):
                            st.subheader(job['title'])
                            st.write(f"🔗 [View Career Page]({job['href']})")
                            st.write(job['body'][:250] + "...")
                            
                            col_a, col_b = st.columns(2)
                            
                            if col_a.button(f"✨ Optimize CV", key=f"opt_{i}"):
                                if not db.get('cv_text'):
                                    st.warning("Please upload your CV in the Profile tab first!")
                                else:
                                    # SAFER PROMPT CONSTRUCTION (Avoiding f-string curly brace errors)
                                    instruction = "TASK: Rewrite my 'Professional Summary' and suggest 3 bullet points to highlight for this specific job. CONSTRAINT: Do NOT invent new skills or experience. Use only what is in my CV."
                                    full_prompt = "JOB DESCRIPTION:\n" + job['body'] + "\n\nMY ORIGINAL CV:\n" + db['cv_text'][:3000] + "\n\n" + instruction
                                    
                                    response = client.chat.completions.create(
                                        model="llama3-8b-8192",
                                        messages=[{"role": "user", "content": full_prompt}]
                                    )
                                    st.info("Tailored CV Adjustments:")
                                    st.write(response.choices[0].message.content)
                                    
                            if col_b.button(f"✅ Track Application", key=f"track_{i}"):
                                db['applied'].append({"role": job['title'], "date": datetime.now().strftime("%Y-%m-%d")})
                                save_db(db)
                                st.success("Logged to Application Journey!")
                else:
                    st.warning("No roles found. Try a broader search term (e.g., just 'Developer').")
            except Exception as e:
                st.error(f"Search failed. Please try again in a moment.")

# --- VIEW 3: PROFILE ---
def view_profile():
    st.title("👤 Canonical Profile")
    db = get_db()

    uploaded_file = st.file_uploader("Update your Original CV (PDF)", type="pdf")
    if uploaded_file:
        try:
            reader = pypdf.PdfReader(uploaded_file)
            full_text = ""
            for page in reader.pages:
                full_text += page.extract_text()
            db['cv_text'] = full_text
            save_db(db)
            st.success("CV Processed and Stored!")
        except Exception as e:
            st.error(f"Failed to read PDF: {e}")

    st.divider()
    st.subheader("Your Application Journey")
    if db.get('applied'):
        st.table(pd.DataFrame(db['applied']))
    else:
        st.info("No applications tracked yet.")
    
    if st.checkbox("Show Stored CV Data"):
        st.text(db.get("cv_text", "No CV data stored yet."))

# --- MAIN NAV ---
nav = st.sidebar.radio("Navigate", ["Dashboard", "Companies", "Profile"])
if nav == "Dashboard": view_dashboard()
elif nav == "Companies": view_companies()
elif nav == "Profile": view_profile()
