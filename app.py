import streamlit as st
import json
import pandas as pd
import requests
from groq import Groq
from datetime import datetime
import pypdf
import os

# --- APP CONFIG ---
st.set_page_config(page_title="Personal Job Copilot", layout="wide", page_icon="🚀")

# Setup Groq Client (using Streamlit Secrets for security)
if "GROQ_API_KEY" in st.secrets:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
else:
    st.error("Please add your GROQ_API_KEY to Streamlit Secrets.")
    st.stop()

# --- DATA PERSISTENCE ---
# Since this is deployed, we store the "Database" in a local JSON file 
# Note: For production, you'd connect this to a DB, but for personal use, a file is fine.
DB_PATH = "application_tracker.json"
if not os.path.exists(DB_PATH):
    with open(DB_PATH, "w") as f:
        json.dump({"applied": [], "skipped": [], "liked": []}, f)

def load_db():
    with open(DB_PATH, "r") as f: return json.load(f)

def save_db(data):
    with open(DB_PATH, "w") as f: json.dump(data, f)

# --- VIEW 1: DASHBOARD (Market Data) ---
def view_dashboard():
    st.title("🌍 Global Market Dashboard")
    # Using Teleport API (Free/No Key)
    cities = ["berlin", "london", "amsterdam", "new-york-city", "singapore"]
    market_data = []
    
    for city in cities:
        try:
            res = requests.get(f"https://api.teleport.org/api/urban_areas/slug:{city}/scores/").json()
            score = round(res['teleport_city_score'], 1)
            market_data.append({"City": city.title(), "Fit %": "Calculated", "Market Score": score, "Status": "High Demand"})
        except:
            continue
            
    st.table(pd.DataFrame(market_data))

# --- VIEW 2: COMPANIES (The Scout) ---
def view_companies():
    st.title("🏢 Role Discovery & Tailoring")
    
    # Simple Job Search via DuckDuckGo (Free API workaround)
    role_query = st.text_input("What role should I scout for?", "Senior Software Engineer")
    
    if st.button("Search for Openings"):
        st.info(f"Searching for {role_query} roles...")
        # Mocking search results for the UI demo - in production, integrate Tavily/Serper
        results = [
            {"company": "Stripe", "role": "Senior Backend Engineer", "link": "https://stripe.com/jobs", "loc": "Remote/Berlin"},
            {"company": "Revolut", "role": "Lead Engineer", "link": "https://revolut.com/jobs", "loc": "London"}
        ]
        
        for job in results:
            with st.expander(f"{job['role']} @ {job['company']}"):
                st.write(f"Location: {job['loc']}")
                col1, col2, col3 = st.columns(3)
                if col1.button(f"Optimize CV", key=job['company']):
                    st.success("Generating LaTeX variant based on original source...")
                    # The Logic: AI takes job desc + original tex -> outputs modified tex
                    st.code("% Simplified LaTeX Output\n\\section{Summary}\nExpert in systems for " + job['company'])
                
                if col2.link_button("Apply Now", job['link']):
                    pass # Opens in new tab

                if col3.button("Mark as Applied", key=f"app_{job['company']}"):
                    db = load_db()
                    db['applied'].append({"company": job['company'], "date": str(datetime.now())})
                    save_db(db)
                    st.toast("Application Tracked!")

# --- VIEW 3: PROFILE ---
def view_profile():
    st.title("👤 My Profile")
    uploaded_pdf = st.file_uploader("Original CV (PDF)", type="pdf")
    uploaded_tex = st.file_uploader("Original Source (LaTeX)", type="tex")
    
    st.divider()
    db = load_db()
    st.subheader("Application History")
    st.write(pd.DataFrame(db['applied']))

# --- ROUTING ---
page = st.sidebar.selectbox("Go to", ["Dashboard", "Companies", "Profile"])
if page == "Dashboard": view_dashboard()
elif page == "Companies": view_companies()
elif page == "Profile": view_profile()