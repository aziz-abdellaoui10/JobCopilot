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
st.set_page_config(page_title="Job Copilot PRO", layout="wide")

if "GROQ_API_KEY" in st.secrets:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
else:
    st.error("Please add GROQ_API_KEY to Streamlit Secrets.")
    st.stop()

# --- DATABASE SETUP ---
DB_PATH = "user_data.json"
if not os.path.exists(DB_PATH):
    with open(DB_PATH, "w") as f:
        json.dump({"applied": [], "profile_summary": "", "skills": []}, f)

def get_db():
    with open(DB_PATH, "r") as f: return json.load(f)

def save_db(data):
    with open(DB_PATH, "w") as f: json.dump(data, f)

# --- CORE ENGINE: THE SCOUTER ---
def scout_real_jobs(role, country):
    with DDGS() as ddgs:
        query = f"site:lever.co OR site:greenhouse.io OR site:workable.com '{role}' in {country} jobs"
        results = [r for r in ddgs.text(query, max_results=5)]
    return results

# --- VIEW 1: DASHBOARD ---
def view_dashboard():
    st.title("🌍 Market Intelligence")
    
    # Pre-defined hubs for the table
    hubs = ["Berlin", "London", "Amsterdam", "New York", "Singapore"]
    market_stats = []
    
    for city in hubs:
        # Fetching live scores from Teleport
        slug = city.lower().replace(" ", "-")
        try:
            res = requests.get(f"https://api.teleport.org/api/urban_areas/slug:{slug}/scores/").json()
            score = round(res['teleport_city_score'], 1)
            # Dummy salary math for demo
            avg_sal = 85000 if score > 60 else 60000 
            market_stats.append({
                "City": city, 
                "Market Fit %": f"{int(score)}%", 
                "Avg Salary": f"${avg_sal:,}", 
                "Quality of Life": score
            })
        except:
            continue
            
    if market_stats:
        st.table(pd.DataFrame(market_stats))
    else:
        st.warning("Could not load market data. Check your internet connection.")

# --- VIEW 2: COMPANIES & TAILORING ---
def view_companies():
    st.title("🏢 Real-Time Scouting")
    
    col1, col2 = st.columns([2,1])
    with col1:
        target_role = st.text_input("Target Job Title", "Software Engineer")
    with col2:
        target_loc = st.text_input("Target Location", "Europe")

    if st.button("🔍 Scout Live Roles"):
        with st.spinner("Browsing career pages..."):
            jobs = scout_real_jobs(target_role, target_loc)
            if not jobs:
                st.error("No roles found. Try a broader search.")
            
            for job in jobs:
                with st.expander(f"📌 {job['title']}"):
                    st.write(f"**Source:** {job['href']}")
                    st.write(job['body'][:300] + "...")
                    
                    c1, c2, c3 = st.columns(3)
                    if c1.button("✨ Optimize CV", key=job['href']):
                        # AI Tailoring Logic
                        prompt = f"Optimize this CV for: {job['title']}. Use only existing facts. Output 3 strong bullet points."
                        chat = client.chat.completions.create(
                            model="llama3-8b-8192",
                            messages=[{"role": "user", "content": prompt}]
                        )
                        st.info("Tailored Highlights for your CV:")
                        st.write(chat.choices[0].message.content)
                    
                    c2.link_button("🔗 Open Career Page", job['href'])
                    
                    if c3.button("✅ I Applied", key=f"app_{job['href']}"):
                        db = get_db()
                        db['applied'].append({"job": job['title'], "date": str(datetime.now())})
                        save_db(db)
                        st.balloons()

# --- VIEW 3: PROFILE ---
def view_profile():
    st.title("👤 Canonical Profile")
    
    up_pdf = st.file_uploader("Upload Original PDF", type="pdf")
    if up_pdf:
        reader = pypdf.PdfReader(up_pdf)
        text = ""
        for page in reader.pages: text += page.extract_text()
        st.success("PDF Parsed!")
        with st.expander("Show Extracted Text"):
            st.write(text)

    st.divider()
    st.subheader("Your Application Journey")
    db = get_db()
    if db['applied']:
        st.dataframe(pd.DataFrame(db['applied']))
    else:
        st.write("You haven't applied to any roles yet.")

# --- NAVIGATION ---
page = st.sidebar.radio("Navigate", ["Dashboard", "Companies", "Profile"])
if page == "Dashboard": view_dashboard()
elif page == "Companies": view_companies()
elif page == "Profile": view_profile()
