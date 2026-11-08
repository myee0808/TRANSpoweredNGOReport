from fastapi import FastAPI, Form
from typing import Dict

app = FastAPI()

database: Dict[str, str] = {}

@app.post("/storeval")
def store_val(tag: str = Form(...), value: str = Form(...)):
    database[tag] = value
    return ["stored", tag, value]

@app.post("/getvalue")
def get_val(tag: str = Form(...)):
    value = database.get(tag, "")
    return [tag, value]

@app.get("/")
def home():
    return {"status": "Backend is running!"}
