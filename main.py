from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from typing import Dict

app = FastAPI(title="NGO Transparency Portal")

# In-memory database for MIT App Inventor TinyWebDB
database: Dict[str, str] = {}

# --- 1. FRONTEND WEBSITE (HUMAN UI) ---
@app.get("/", response_class=HTMLResponse)
def home():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>NGO Transparency Platform</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f4f6f9; margin: 0; padding: 40px; color: #333; }
            .container { max-width: 800px; margin: auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }
            h1 { color: #1a365d; border-bottom: 2px solid #e2e8f0; padding-bottom: 10px; }
            p { font-size: 1.1rem; line-height: 1.6; }
            .status-badge { display: inline-block; background-color: #38a169; color: white; padding: 6px 12px; border-radius: 20px; font-weight: bold; font-size: 0.9rem; }
            .api-card { margin-top: 20px; padding: 15px; background: #edf2f7; border-radius: 8px; }
            a.button { display: inline-block; margin-top: 15px; background-color: #3182ce; color: white; text-decoration: none; padding: 10px 20px; border-radius: 6px; font-weight: bold; }
            a.button:hover { background-color: #2b6cb0; }
        </style>
    </head>
    <body>
        <div class="container">
            <span class="status-badge">System Online</span>
            <h1>NGO Transparency & Accountability Portal</h1>
            <p>Welcome to the custom cloud backend for the NGO Transparency Platform. This platform connects public records and AI-assisted search tools with mobile applications.</p>
            
            <div class="api-card">
                <h3>Backend API Services</h3>
                <p>This service provides persistent cloud database storage via custom <code>/storeval</code> and <code>/getvalue</code> endpoints integrated with MIT App Inventor.</p>
                <a href="/docs" class="button">Open Interactive API Documentation</a>
            </div>
        </div>
    </body>
    </html>
    """

# --- 2. BACKEND API FOR MIT APP INVENTOR (TINYWEBDB) ---
@app.post("/storeval")
def store_val(tag: str = Form(...), value: str = Form(...)):
    database[tag] = value
    return ["stored", tag, value]

@app.post("/getvalue")
def get_val(tag: str = Form(...)):
    value = database.get(tag, "")
    return [tag, value]
