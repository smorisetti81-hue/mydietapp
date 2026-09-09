import base64, json, os, sqlite3, time
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

DB_PATH = os.getenv("MYDIET_DB_PATH", "./mydiet_health.db")
SYNC_TOKEN = os.getenv("MYDIET_SYNC_TOKEN", "").strip()
app = FastAPI(title="MyDiet Health Sync API", version="1.0.0")

class SyncBody(BaseModel):
    profile_id: str = Field(min_length=1, max_length=128)
    payload: str = Field(min_length=20)
    schema: str = "mydietapp.health.v1"
    sent_at_ms: int | None = None

def db():
    c=sqlite3.connect(DB_PATH)
    c.execute("CREATE TABLE IF NOT EXISTS health_snapshots (profile_id TEXT PRIMARY KEY, payload TEXT NOT NULL, received_at_ms INTEGER NOT NULL, schema TEXT NOT NULL)")
    c.commit(); return c

def auth(token: str | None):
    if not SYNC_TOKEN or token != SYNC_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid sync token")

@app.get("/health")
def health():
    return {"ok": True, "service": "mydiet-health-sync"}

@app.post("/v1/health/sync")
def sync(body: SyncBody, x_mydiet_token: str | None = Header(default=None)):
    auth(x_mydiet_token)
    if body.schema != "mydietapp.health.v1":
        raise HTTPException(status_code=400, detail="Unsupported schema")
    try:
        raw=base64.urlsafe_b64decode(body.payload + "=" * (-len(body.payload)%4))
        obj=json.loads(raw.decode("utf-8"))
        if obj.get("schema") != "mydietapp.health.v1": raise ValueError()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid health payload")
    now=int(time.time()*1000)
    c=db(); c.execute("INSERT INTO health_snapshots(profile_id,payload,received_at_ms,schema) VALUES(?,?,?,?) ON CONFLICT(profile_id) DO UPDATE SET payload=excluded.payload,received_at_ms=excluded.received_at_ms,schema=excluded.schema", (body.profile_id,body.payload,now,body.schema)); c.commit(); c.close()
    return {"ok": True, "profile_id": body.profile_id, "received_at_ms": now}

@app.get("/v1/health/latest/{profile_id}")
def latest(profile_id: str, x_mydiet_token: str | None = Header(default=None)):
    auth(x_mydiet_token)
    c=db(); row=c.execute("SELECT payload,received_at_ms,schema FROM health_snapshots WHERE profile_id=?",(profile_id,)).fetchone(); c.close()
    if not row: raise HTTPException(status_code=404, detail="No snapshot")
    return {"payload":row[0],"received_at_ms":row[1],"schema":row[2]}
