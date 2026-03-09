from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client
import os, hmac, hashlib, json
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPABASE_URL            = os.environ.get("SUPABASE_URL")
SUPABASE_KEY            = os.environ.get("SUPABASE_KEY")
ADMIN_KEY               = os.environ.get("ADMIN_KEY", "linkdesi2026")
RAZORPAY_SECRET         = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


class LinkItem(BaseModel):
    label: str
    url: str

class ProductCard(BaseModel):
    title: str
    price: str
    upi_id: str

class PageData(BaseModel):
    username: str
    name: str
    bio: Optional[str] = ""
    emoji: Optional[str] = "😊"
    photo_url: Optional[str] = ""
    theme_index: Optional[int] = 0
    links: Optional[List[LinkItem]] = []
    social: Optional[Dict[str, str]] = {}
    products: Optional[List[ProductCard]] = []
    upi_id: Optional[str] = ""
    whatsapp: Optional[str] = ""
    support_wa: Optional[str] = ""
    is_active: Optional[bool] = False


def require_admin(x_admin_key: str = Header(None)):
    if x_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

def activate_username(username: str):
    expires_at = (datetime.utcnow() + timedelta(days=30)).isoformat()
    supabase.table("pages").update({
        "is_active": True,
        "expires_at": expires_at,
    }).eq("username", username.lower()).execute()
    return expires_at


@app.get("/")
def root():
    return {"status": "LinkDesi API running"}


@app.get("/page/{username}")
def get_page(username: str):
    result = supabase.table("pages").select("*").eq("username", username.lower()).eq("is_active", True).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Page not found or not active.")
    # increment visit count
    row = result.data[0]
    visits = (row.get("visits") or 0) + 1
    supabase.table("pages").update({"visits": visits}).eq("username", username.lower()).execute()
    row["visits"] = visits
    return row


@app.post("/page")
def create_or_update_page(data: PageData):
    username = data.username.lower().strip()
    if not username or len(username) < 3:
        raise HTTPException(status_code=400, detail="Username must be at least 3 characters.")
    if not all(c.isalnum() or c == '_' for c in username):
        raise HTTPException(status_code=400, detail="Username can only contain letters, numbers, and underscores.")

    existing = supabase.table("pages").select("username").eq("username", username).execute()
    page_data = {
        "name": data.name,
        "bio": data.bio,
        "emoji": data.emoji,
        "photo_url": data.photo_url,
        "theme_index": data.theme_index,
        "links": [l.dict() for l in data.links],
        "social": data.social,
        "products": [p.dict() for p in data.products],
        "upi_id": data.upi_id,
        "whatsapp": data.whatsapp,
        "support_wa": data.support_wa,
        "support_wa": data.support_wa,
    }

    if existing.data:
        supabase.table("pages").update(page_data).eq("username", username).execute()
    else:
        page_data["username"] = username
        page_data["is_active"] = False
        page_data["visits"] = 0
        supabase.table("pages").insert(page_data).execute()

    return {"status": "saved", "username": username}


@app.post("/activate/{username}")
def activate_page(username: str, x_admin_key: str = Header(None)):
    require_admin(x_admin_key)
    result = supabase.table("pages").select("username").eq("username", username.lower()).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Username not found.")
    expires_at = activate_username(username)
    return {"status": "activated", "expires_at": expires_at}


@app.post("/deactivate/{username}")
def deactivate_page(username: str, x_admin_key: str = Header(None)):
    require_admin(x_admin_key)
    result = supabase.table("pages").update({"is_active": False}).eq("username", username.lower()).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Username not found.")
    return {"status": "deactivated"}


@app.get("/admin/users")
def get_all_users(x_admin_key: str = Header(None)):
    require_admin(x_admin_key)
    result = supabase.table("pages").select("username,name,is_active,created_at,expires_at,visits").order("created_at", desc=True).execute()
    return result.data


@app.get("/check/{username}")
def check_username(username: str):
    result = supabase.table("pages").select("username").eq("username", username.lower()).execute()
    return {"available": len(result.data) == 0}


@app.get("/stats/{username}")
def get_stats(username: str, x_admin_key: str = Header(None)):
    """Let user see their own visit count."""
    result = supabase.table("pages").select("visits,name").eq("username", username.lower()).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Not found.")
    return result.data[0]


# ─── RAZORPAY WEBHOOK ─────────────────────────────────────────────────────────

@app.post("/webhook/razorpay")
async def razorpay_webhook(request: Request):
    body = await request.body()
    if RAZORPAY_SECRET:
        sig = request.headers.get("x-razorpay-signature", "")
        expected = hmac.new(RAZORPAY_SECRET.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            raise HTTPException(status_code=400, detail="Invalid signature")
    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = payload.get("event", "")
    if event not in ("payment.captured", "payment_link.paid", "payment_page.payment.captured"):
        return {"status": "ignored", "event": event}

    username = None
    try:
        if event == "payment_link.paid":
            notes = payload["payload"]["payment_link"]["entity"]["notes"]
        else:
            notes = payload["payload"]["payment"]["entity"]["notes"]
        username = notes.get("username") or notes.get("Username")
    except Exception:
        pass

    if not username:
        print(f"[WEBHOOK] No username in notes. Event: {event}")
        return {"status": "no_username"}

    username = username.lower().strip()
    result = supabase.table("pages").select("username").eq("username", username).execute()
    if not result.data:
        return {"status": "user_not_found", "username": username}

    expires_at = activate_username(username)
    print(f"[WEBHOOK] Auto-activated {username}, expires {expires_at}")
    return {"status": "activated", "username": username, "expires_at": expires_at}