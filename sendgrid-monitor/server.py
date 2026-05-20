import json
import sqlite3
from fastapi import FastAPI, Request, Response

app = FastAPI()

DB = "sendgrid_events.db"

def init_db():
    with sqlite3.connect(DB) as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER,
            recipient TEXT,
            event TEXT,
            reason TEXT,
            sg_event_id TEXT UNIQUE,
            sg_message_id TEXT,
            raw_json TEXT
        )
        """)

@app.on_event("startup")
def startup():
    init_db()

@app.get("/")
def root():
    return {"status": "running"}

@app.post("/sendgrid/events")
async def receive_events(request: Request):

    events = await request.json()

    with sqlite3.connect(DB) as con:
        for event in events:

            # Only track events tied to documents@veroot.com
            source = event.get("source")

            if source != "documents@veroot.com":
                continue

            con.execute("""
            INSERT OR IGNORE INTO events
            (timestamp, recipient, event, reason,
             sg_event_id, sg_message_id, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                event.get("timestamp"),
                event.get("email"),
                event.get("event"),
                event.get("reason") or event.get("response"),
                event.get("sg_event_id"),
                event.get("sg_message_id"),
                json.dumps(event)
            ))

            print(
                f"[{event.get('event')}] "
                f"{event.get('email')}"
            )

    return Response(status_code=202)
