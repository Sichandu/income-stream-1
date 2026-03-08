from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client
import os
from typing import Optional, List

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Replace with your Netlify URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Supabase client — get these from supabase.com (free)
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


class LinkItem(BaseModel):
    label: str
    url: str


class PageData(BaseModel):
    username: str
    name: str
    bio: Optional[str] = ""
    emoji: Optional[str] = "😊"
    theme_index: Optional[int] = 0
    links: Optional[List[LinkItem]] = []
    upi_id: Optional[str] = ""
    whatsapp: Optional[str] = ""
    is_active: Optional[bool] = False  # True after payment


@app.get("/")
def root():
    return {"status": "LinkDesi API running"}


@app.get("/page/{username}")
def get_page(username: str):
    """Get a published page by username."""
    result = supabase.table("pages").select("*").eq("username", username.lower()).eq("is_active", True).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Page not found or not active.")
    return result.data[0]


@app.post("/page")
def create_or_update_page(data: PageData):
    """Save page data (draft). Called from builder."""
    username = data.username.lower().strip()

    if not username or len(username) < 3:
        raise HTTPException(status_code=400, detail="Username must be at least 3 characters.")
    if not username.isalnum() and not all(c.isalnum() or c == '_' for c in username):
        raise HTTPException(status_code=400, detail="Username can only contain letters, numbers, and underscores.")

    # Check if username is taken by someone else
    existing = supabase.table("pages").select("username").eq("username", username).execute()
    if existing.data:
        # Update existing
        result = supabase.table("pages").update({
            "name": data.name,
            "bio": data.bio,
            "emoji": data.emoji,
            "theme_index": data.theme_index,
            "links": [l.dict() for l in data.links],
            "upi_id": data.upi_id,
            "whatsapp": data.whatsapp,
        }).eq("username", username).execute()
    else:
        # Create new
        result = supabase.table("pages").insert({
            "username": username,
            "name": data.name,
            "bio": data.bio,
            "emoji": data.emoji,
            "theme_index": data.theme_index,
            "links": [l.dict() for l in data.links],
            "upi_id": data.upi_id,
            "whatsapp": data.whatsapp,
            "is_active": False,
        }).execute()

    return {"status": "saved", "username": username}


@app.post("/activate/{username}")
def activate_page(username: str):
    """
    Activate a page after payment.
    In production: verify Razorpay webhook before calling this.
    """
    result = supabase.table("pages").update({"is_active": True}).eq("username", username.lower()).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Username not found.")
    return {"status": "activated", "url": f"linkdesi.in/{username}"}


@app.get("/check/{username}")
def check_username(username: str):
    """Check if username is available."""
    result = supabase.table("pages").select("username").eq("username", username.lower()).execute()
    return {"available": len(result.data) == 0}