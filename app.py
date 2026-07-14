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
        background: rgba(255, 255, 255, 0.05);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 15px;
        transition: all 0.3s ease;
    }
    
    .job-card:hover {
        border: 1px solid #ff4b4b;
        background: rgba(255, 255, 255, 0.08);
    }
    
    .status-badge {
        background: #ff4b4b;
        color: white;
        padding: 2px 10px;
        border-radius: 15px;
        font-size: 11px;
        font-weight: 700;
        text-transform: uppercase;
    }
    
    .salary-tag {
        color: #00ffa3;
        font-weight: 700;
        font-size: 14px;
    }

    /* Sidebar Styling */
    section[data-testid="stSidebar"] {
        background-color: #161b22;
    }
    </style>
""", unsafe_allow_html=True) # Fixed the typo here!

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
    try:
        with open(DB_PATH, "r") as f: return json.load(f)
    except:
        return {"applied": [], "cv_text": "", "preferences": {"role": "Software Engineer", "loc": "Europe"}}

def save_db(data):
    with open(DB_PATH, "w") as f: json.dump(data, f)

db = get_db()

# --- SEARCH ENGINE 2.0 ---
def ultra_scout(role, location):
    queries = [
        f"site:lever.co '{role}' {location} jobs",
        f"site:greenhouse.io '{role}' {location} jobs"
    ]
    all_results = []
    try:
        with DDGS() as ddgs:
            for q in queries:
                results = list(ddgs.text(q, max_results=5))
                if results:
                    all_results.extend(results)
    except:
        st.warning("Search limit reached. Please wait a minute.")
    return all_results

# --- DASHBOARD: MARKET INTELLIGENCE ---
def view_dashboard():
    st.title("📊 Market Intelligence")
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Apps Logged", len(db['applied']))
    col2.metric("Market Sentiment", "Bullish")
    col3.metric("AI Profile", "Synced" if db['cv_text'] else "Pending")

    st.subheader("Regional Tech Hub Score (Quality of Life)")
    hubs = ["Berlin", "London", "Amsterdam", "New York", "Singapore"]
    scores = []
    for city in hubs:
        try:
            res = requests.get(f"https://api.teleport.org/api/urban_areas/slug:{city.lower().replace(' ', '-')}/scores/", timeout=3).json()
            scores.append(res['teleport_city_score'])
        except: 
            scores.append(50)
    
    fig = go.Figure(data=[go.Bar(x=hubs, y=scores, marker_color='#ff4b4b')])
    fig.update_layout(template="plotly_dark", height=300, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig, use_container_width=True)

# --- SCOUTING: THE WTTJ EXPERIENCE ---
def view_companies():
    st.title("🔭 Real-Time Scouting")
    
    with st.sidebar:
        st.subheader("Search Filters")
        role_input = st.text_input("Role Title", db['preferences']['role'])
        loc_input = st.text_input("Location", db['preferences']['loc'])
        if st.button("Save & Update"):
            db['preferences'] = {"role": role_input, "loc": loc_input}
            save_db(db)

    with st.spinner("Analyzing job boards..."):
        jobs = ultra_scout(role_input, loc_input)
    
    if not jobs:
        st.info("Click 'Save & Update' or change filters to find roles.")
        
    for i, job in enumerate(jobs):
        st.markdown(f"""
            <div class="job-card">
                <span class="status-badge">Live Role</span>
                <h3 style="margin-top:10px;">{job['title']}</h3>
                <p style="color: #aaa; font-size: 14px;">{job['body'][:250]}...</p>
                <p class="salary-tag">Est. Market Rate: $85k - $140k • {loc_input}</p>
            </div>
        """, unsafe_allow_html=True)
        
        c1, c2, c3 = st.columns([1,1,2])
        
        if c1.button("✨ Tailor CV", key=f"tailor_{i}"):
            if not db['cv_text']:
                st.error("Go to Workspace and upload your CV first!")
            else:
                with st.spinner("AI Generating..."):
                    instr = "Rewrite my Summary and bullet points for this job. Use ONLY existing facts."
                    prompt = "MY CV:\n" + db['cv_text'][:2000] + "\n\nJOB:\n" + job['body'] + "\n\n" + instr
                    res = client.chat.completions.create(model="llama3-8b-8192", messages=[{"role": "user", "content": prompt}])
                    st.info(res.choices[0].message.content)
        
        c2.link_button("🚀 Open Job", job['href'])
        
        if c3.button("✅ Track Application", key=f"log_{i}"):
            db['applied'].append({"company": job['title'], "date": str(datetime.now().date())})
            save_db(db)
            st.toast("Application tracked!")

# --- PROFILE: CANONICAL SOURCE ---
def view_profile():
    st.title("🛠️ Personal Workspace")
    
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Source Material")
        up_pdf = st.file_uploader("Upload Master CV (PDF)", type="pdf")
        if up_pdf:
            reader = pypdf.PdfReader(up_pdf)
            text = "".join([p.extract_text() for p in reader.pages])
            db['cv_text'] = text
            save_db(db)
            st.success("Master CV Synced.")

    with col2:
        st.subheader("Log History")
        if db['applied']:
            st.dataframe(pd.DataFrame(db['applied']), use_container_width=True)
        else:
            st.info("No applications tracked yet.")

# --- NAVIGATION ---
pages = {"Dashboard": view_dashboard, "Scouting": view_companies, "Workspace": view_profile}
selected = st.sidebar.radio("Navigate", pages.keys())
pages[selected]()
