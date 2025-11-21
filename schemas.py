"""
Database Schemas for Multi‑Vendor Digital Marketplace

Each Pydantic model below maps to a MongoDB collection using the lowercase
class name as the collection name (e.g., User -> "user").

These schemas are used for validation at your API boundaries and to keep
collections consistent.
"""
from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field, EmailStr
from datetime import datetime

# ---------------------------------------------------------------------------
# Core Users and Roles
# ---------------------------------------------------------------------------
class User(BaseModel):
    name: str = Field(..., description="Full name")
    email: EmailStr
    role: Literal["buyer", "seller", "admin"] = "buyer"
    avatar_url: Optional[str] = None
    is_active: bool = True
    provider: Literal["email", "google"] = "email"

class Seller(BaseModel):
    user_id: str
    display_name: str
    bio: Optional[str] = None
    stripe_connect_id: Optional[str] = None
    status: Literal["pending", "approved", "suspended"] = "pending"

# ---------------------------------------------------------------------------
# Products and Listings
# ---------------------------------------------------------------------------
class Product(BaseModel):
    seller_id: str = Field(..., description="Reference to seller.user_id or user id")
    title: str
    description: Optional[str] = None
    price: float = Field(..., ge=0)
    currency: str = "usd"
    category: Optional[str] = None
    tags: List[str] = []
    preview_media_url: Optional[str] = None
    file_storage_key: Optional[str] = Field(None, description="Internal file key/path")
    status: Literal["draft", "active", "suspended"] = "active"
    stats: Dict[str, int] = Field(default_factory=lambda: {"views": 0, "sales": 0})

# ---------------------------------------------------------------------------
# Orders / Purchases
# ---------------------------------------------------------------------------
class CartItem(BaseModel):
    product_id: str
    quantity: int = Field(1, ge=1)

class CheckoutSession(BaseModel):
    buyer_email: EmailStr
    items: List[CartItem]
    provider: Literal["stripe", "paypal"] = "stripe"

class Purchase(BaseModel):
    buyer_email: EmailStr
    items: List[Dict[str, Any]]  # denormalized snapshot of product data
    total_amount: float
    currency: str = "usd"
    provider: Literal["stripe", "paypal"] = "stripe"
    payment_status: Literal["pending", "paid", "refunded", "failed"] = "pending"
    transaction_id: Optional[str] = None
    download_limit: int = 10

# ---------------------------------------------------------------------------
# Payouts / Accounting
# ---------------------------------------------------------------------------
class Payout(BaseModel):
    seller_id: str
    amount: float
    currency: str = "usd"
    status: Literal["pending", "in_transit", "paid", "failed"] = "pending"
    stripe_transfer_id: Optional[str] = None

# ---------------------------------------------------------------------------
# Platform Settings & Logs
# ---------------------------------------------------------------------------
class Settings(BaseModel):
    commission_percent: float = Field(10.0, ge=0, le=100)
    payments: Dict[str, bool] = Field(default_factory=lambda: {
        "stripe": True,
        "paypal": False,
        "klarna": True,
        "sofort": True,
        "giropay": True
    })

class Notification(BaseModel):
    user_id: Optional[str] = None
    type: Literal["sale", "payout", "system"] = "system"
    title: str
    message: str
    is_read: bool = False

class AuditLog(BaseModel):
    actor_id: Optional[str] = None
    action: str
    resource_type: str
    resource_id: Optional[str] = None
    metadata: Dict[str, Any] = {}
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
