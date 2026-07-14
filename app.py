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

# --- CONFIG & STYLING ---
st.set_page_config(page_title="Job Copilot | Hub", layout="wide", page_icon="🎯")

# Custom CSS for "Welcome to the Jungle" Aesthetic
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap');
    
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }
    
    .stApp {
        background-color: #0e1117;
    }
    
    /* Job Card Styling */
    .job-card {
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 15px;
        padding: 25px;
        margin-bottom: 20px;
        transition: transform 0.2s, border 0.2s;
    }
    
    .job-card:hover {
        border: 1px solid #ff4b4b;
        transform: translateY(-5px);
    }
    
    .status-badge {
        background: #ff4b4b;
        color: white;
        padding: 4px 12px;
        border-radius: 20px;
        font-size: 12px;
        font-weight: 600;
    }
    
    .salary-tag {
        color: #00ffa3;
        font-weight: 700;
    }
    </style>
""", unsafe_allow_stdio=True)

# --- SYSTEM INITIALIZATION ---
if "GROQ_API_KEY" in st.secrets:
    client = Groq(api_key=st.secrets["GROQ_API_KEY"])
else:
    st.error("🔑 Please add GROQ_API_KEY to Streamlit Secrets.")
    st.stop()

DB_PATH = "copilot_storage.json"

def get_db():
    if not os.path.exists(DB_PATH):
        return {"applied": [], "cv_text": "", "preferences": {"role": "Software Engineer", "loc": "Europe"}}
    with open(DB_PATH, "r") as f: return json.load(f)

def save_db(data):
    with open(DB_PATH, "w") as f: json.dump(data, f)

db = get_db()

# --- SEARCH ENGINE 2.0 ---
def ultra_scout(role, location):
    # Specialized queries to find actual job boards, not just aggregators
    queries = [
        f"site:lever.co '{role}' {location}",
        f"site:greenhouse.io '{role}' {location}",
        f"site:workable.com '{role}' {location}"
    ]
    all_results = []
    with DDGS() as ddgs:
        for q in queries:
            results = list(ddgs.text(q, max_results=5))
            all_results.extend(results)
    return all_results

# --- DASHBOARD: MARKET INTELLIGENCE ---
def view_dashboard():
    st.title("📊 Personal Career Dashboard")
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Applications", len(db['applied']), "+1 this week")
    col2.metric("Market Sentiment", "Bullish", "Tech Hubs")
    col3.metric("Tailored CVs", "Ready", "Sync Active")

    st.subheader("Global Surplus Index (Salary - Cost of Living)")
    hubs = ["Berlin", "London", "Amsterdam", "New York", "Singapore"]
    scores = []
    for city in hubs:
        try:
            res = requests.get(f"https://api.teleport.org/api/urban_areas/slug:{city.lower().replace(' ', '-')}/scores/").json()
            scores.append(res['teleport_city_score'])
        except: scores.append(50)
    
    fig = go.Figure(data=[go.Bar(x=hubs, y=scores, marker_color='#ff4b4b')])
    fig.update_layout(template="plotly_dark", height=300, margin=dict(l=20, r=20, t=20, b=20))
    st.plotly_chart(fig, use_container_width=True)

# --- SCOUTING: THE WTTJ EXPERIENCE ---
def view_companies():
    st.title("🔭 Role Discovery")
    
    with st.sidebar:
        st.subheader("Search Filters")
        role = st.text_input("Role Title", db['preferences']['role'])
        loc = st.text_input("Location", db['preferences']['loc'])
        if st.button("Update Preferences"):
            db['preferences'] = {"role": role, "loc": loc}
            save_db(db)

    jobs = ultra_scout(role, loc)
    
    for i, job in enumerate(jobs):
        st.markdown(f"""
            <div class="job-card">
                <span class="status-badge">New Opening</span>
                <h3 style="margin-top:10px;">{job['title']}</h3>
                <p style="color: #aaa;">{job['body'][:200]}...</p>
                <p class="salary-tag">Estimated: $90k - $130k • {loc}</p>
            </div>
        """, unsafe_allow_stdio=True)
        
        c1, c2, c3 = st.columns([1,1,2])
        
        if c1.button("✨ Tailor CV", key=f"tailor_{i}"):
            if not db['cv_text']:
                st.error("Please upload CV in Profile first.")
            else:
                with st.spinner("AI Analysis..."):
                    prompt = f"Using ONLY my CV: {db['cv_text'][:2000]}. Rewrite my summary for {job['title']} and explain why I am a fit based on specific keywords."
                    res = client.chat.completions.create(model="llama3-8b-8192", messages=[{"role": "user", "content": prompt}])
                    st.info(res.choices[0].message.content)
        
        c2.link_button("🚀 Apply", job['href'])
        
        if c3.button("✅ Log Application", key=f"log_{i}"):
            db['applied'].append({"company": job['title'], "date": str(datetime.now().date())})
            save_db(db)
            st.toast("Application tracked!")

# --- PROFILE: CANONICAL SOURCE ---
def view_profile():
    st.title("👤 Canonical Workspace")
    
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Source Materials")
        up_pdf = st.file_uploader("Original CV (PDF)", type="pdf")
        if up_pdf:
            reader = pypdf.PdfReader(up_pdf)
            text = "".join([p.extract_text() for p in reader.pages])
            db['cv_text'] = text
            save_db(db)
            st.success("CV Synced to local Brain.")

    with col2:
        st.subheader("Application Tracker")
        if db['applied']:
            st.table(pd.DataFrame(db['applied']))
        else:
            st.info("Your journey starts here.")

# --- NAVIGATION ---
pages = {"Dashboard": view_dashboard, "Scouting": view_companies, "Workspace": view_profile}
selected = st.sidebar.selectbox("Navigate", pages.keys())
pages[selected]()
