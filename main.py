import os
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from bson import ObjectId

# Database helpers
from database import db, create_document, get_documents

# Stripe (optional for now)
import stripe

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

app = FastAPI(title="Multi‑Vendor Digital Marketplace API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------- Utilities ------------------------------------
class PyObjectId(ObjectId):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid objectid")
        return ObjectId(v)


def oid(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")


# --------------------------- Schemas (DTOs) ---------------------------------
class ProductIn(BaseModel):
    seller_id: str
    title: str
    description: Optional[str] = None
    price: float = Field(..., ge=0)
    currency: str = "usd"
    category: Optional[str] = None
    tags: List[str] = []
    preview_media_url: Optional[str] = None
    file_storage_key: Optional[str] = None


class ProductOut(BaseModel):
    id: str
    seller_id: str
    title: str
    description: Optional[str]
    price: float
    currency: str
    category: Optional[str]
    tags: List[str] = []
    preview_media_url: Optional[str]
    status: str
    stats: Dict[str, int]


class CheckoutItem(BaseModel):
    product_id: str
    quantity: int = 1


class CheckoutRequest(BaseModel):
    buyer_email: str
    items: List[CheckoutItem]
    provider: str = "stripe"  # stripe or paypal


# ---------------------------- Root & Health --------------------------------
@app.get("/")
def read_root():
    return {"message": "Marketplace backend running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    if db is not None:
        response["database"] = "✅ Available"
        try:
            collections = db.list_collection_names()
            response["collections"] = collections[:10]
            response["connection_status"] = "Connected"
            response["database"] = "✅ Connected & Working"
            response["database_url"] = "✅ Set"
            response["database_name"] = getattr(db, "name", "✅ Set")
        except Exception as e:
            response["database"] = f"⚠️ Connected but Error: {str(e)[:80]}"
    return response


# ---------------------------- Public Catalog -------------------------------
@app.get("/api/products", response_model=List[ProductOut])
def list_products(q: Optional[str] = None, category: Optional[str] = None, seller_id: Optional[str] = None):
    flt: Dict[str, Any] = {"status": {"$ne": "suspended"}}
    if q:
        flt["title"] = {"$regex": q, "$options": "i"}
    if category:
        flt["category"] = category
    if seller_id:
        flt["seller_id"] = seller_id
    docs = db.product.find(flt).limit(50)
    out: List[ProductOut] = []
    for d in docs:
        out.append(ProductOut(
            id=str(d.get("_id")),
            seller_id=d.get("seller_id"),
            title=d.get("title"),
            description=d.get("description"),
            price=d.get("price"),
            currency=d.get("currency", "usd"),
            category=d.get("category"),
            tags=d.get("tags", []),
            preview_media_url=d.get("preview_media_url"),
            status=d.get("status", "active"),
            stats=d.get("stats", {"views": 0, "sales": 0}),
        ))
    return out


@app.get("/api/products/{product_id}", response_model=ProductOut)
def get_product(product_id: str):
    d = db.product.find_one({"_id": oid(product_id)})
    if not d:
        raise HTTPException(status_code=404, detail="Product not found")
    # increment view count
    db.product.update_one({"_id": d["_id"]}, {"$inc": {"stats.views": 1}})
    return ProductOut(
        id=str(d.get("_id")),
        seller_id=d.get("seller_id"),
        title=d.get("title"),
        description=d.get("description"),
        price=d.get("price"),
        currency=d.get("currency", "usd"),
        category=d.get("category"),
        tags=d.get("tags", []),
        preview_media_url=d.get("preview_media_url"),
        status=d.get("status", "active"),
        stats=d.get("stats", {"views": 0, "sales": 0}),
    )


# ---------------------------- Seller endpoints -----------------------------
@app.post("/api/seller/products", response_model=Dict[str, str])
def create_product(product: ProductIn):
    data = product.model_dump()
    data.update({
        "status": "active",
        "stats": {"views": 0, "sales": 0}
    })
    new_id = create_document("product", data)
    # audit
    create_document("auditlog", {
        "action": "create_product",
        "resource_type": "product",
        "resource_id": new_id,
        "metadata": {"seller_id": product.seller_id}
    })
    return {"id": new_id}


@app.put("/api/seller/products/{product_id}")
def update_product(product_id: str, product: ProductIn):
    result = db.product.update_one({"_id": oid(product_id)}, {"$set": product.model_dump()})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    create_document("auditlog", {
        "action": "update_product",
        "resource_type": "product",
        "resource_id": product_id
    })
    return {"updated": True}


@app.delete("/api/seller/products/{product_id}")
def delete_product(product_id: str):
    result = db.product.delete_one({"_id": oid(product_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    create_document("auditlog", {
        "action": "delete_product",
        "resource_type": "product",
        "resource_id": product_id
    })
    return {"deleted": True}


@app.get("/api/seller/analytics")
def seller_analytics(seller_id: str):
    pipeline = [
        {"$match": {"seller_id": seller_id}},
        {"$group": {
            "_id": None,
            "revenue": {"$sum": {"$ifNull": ["$price", 0]}},
            "products": {"$sum": 1},
            "views": {"$sum": {"$ifNull": ["$stats.views", 0]}},
            "sales": {"$sum": {"$ifNull": ["$stats.sales", 0]}},
        }}
    ]
    agg = list(db.product.aggregate(pipeline))
    base = agg[0] if agg else {"revenue": 0, "products": 0, "views": 0, "sales": 0}
    conv = 0.0
    if base.get("views", 0) > 0:
        conv = (base.get("sales", 0) / base.get("views", 1)) * 100
    return {
        "revenue": base.get("revenue", 0),
        "products": base.get("products", 0),
        "views": base.get("views", 0),
        "sales": base.get("sales", 0),
        "conversion_rate": round(conv, 2)
    }


@app.get("/api/seller/payouts")
def seller_payouts(seller_id: str):
    payouts = get_documents("payout", {"seller_id": seller_id})
    # stringify ids
    for p in payouts:
        p["id"] = str(p.pop("_id"))
    return {"payouts": payouts}


@app.post("/api/seller/stripe/onboard")
def seller_stripe_onboard(seller_id: str):
    if not stripe.api_key:
        # Demo link fallback
        return {"url": "https://dashboard.stripe.com/register"}
    account = stripe.Account.create(type="express")
    link = stripe.AccountLink.create(
        account=account.id,
        refresh_url="https://example.com/reauth",
        return_url="https://example.com/return",
        type="account_onboarding",
    )
    db.seller.update_one({"user_id": seller_id}, {"$set": {"stripe_connect_id": account.id}}, upsert=True)
    return {"url": link.url}


# ------------------------------ Checkout -----------------------------------
@app.post("/api/checkout/create-session")
def create_checkout(req: CheckoutRequest):
    # Load product data and prepare line items
    product_ids = [oid(i.product_id) for i in req.items]
    docs = list(db.product.find({"_id": {"$in": product_ids}}))
    if not docs:
        raise HTTPException(status_code=400, detail="No valid items")

    total = 0.0
    line_items = []
    id_to_qty = {i.product_id: i.quantity for i in req.items}
    for d in docs:
        qty = id_to_qty.get(str(d["_id"])) or 1
        price_cents = int(float(d.get("price", 0)) * 100)
        total += float(d.get("price", 0)) * qty
        line_items.append({
            "price_data": {
                "currency": d.get("currency", "usd"),
                "product_data": {"name": d.get("title"), "metadata": {"pid": str(d["_id"]) }},
                "unit_amount": price_cents,
            },
            "quantity": qty,
        })

    # Create a purchase record pending
    purchase_id = create_document("purchase", {
        "buyer_email": req.buyer_email,
        "items": [{
            "product_id": str(d["_id"]),
            "title": d.get("title"),
            "price": d.get("price"),
            "seller_id": d.get("seller_id")
        } for d in docs],
        "total_amount": total,
        "currency": docs[0].get("currency", "usd"),
        "provider": req.provider,
        "payment_status": "pending"
    })

    if req.provider == "stripe" and stripe.api_key:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            customer_email=req.buyer_email,
            line_items=line_items,
            success_url=f"https://example.com/success?purchase_id={purchase_id}",
            cancel_url=f"https://example.com/cancel?purchase_id={purchase_id}",
        )
        db.purchase.update_one({"_id": oid(purchase_id)}, {"$set": {"transaction_id": session.id}})
        return {"provider": "stripe", "session_id": session.id, "url": session.url}
    else:
        # Fallback demo flow when Stripe key not set or using PayPal placeholder
        db.purchase.update_one({"_id": oid(purchase_id)}, {"$set": {"payment_status": "paid", "transaction_id": "demo_txn"}})
        # Increment product sales
        for d in docs:
            db.product.update_one({"_id": d["_id"]}, {"$inc": {"stats.sales": 1}})
        return {"provider": req.provider, "demo": True, "purchase_id": purchase_id}


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    if not STRIPE_WEBHOOK_SECRET:
        return {"received": True}  # ignore in demo
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        purchase = db.purchase.find_one({"transaction_id": session.get("id")})
        if purchase:
            db.purchase.update_one({"_id": purchase["_id"]}, {"$set": {"payment_status": "paid"}})
            for it in purchase.get("items", []):
                db.product.update_one({"_id": oid(it.get("product_id"))}, {"$inc": {"stats.sales": 1}})
            # TODO: create transfers to sellers via Stripe Connect
    return {"received": True}


# ------------------------------- Buyer -------------------------------------
@app.get("/api/me/downloads")
def my_downloads(email: str):
    purchases = list(db.purchase.find({"buyer_email": email, "payment_status": "paid"}).sort("created_at", -1))
    results = []
    for p in purchases:
        for it in p.get("items", []):
            results.append({
                "title": it.get("title"),
                "product_id": it.get("product_id"),
                "download_url": f"/api/downloads/{it.get('product_id')}?token=demo",
            })
    return {"downloads": results}


# ------------------------------- Admin -------------------------------------
@app.get("/api/admin/settings")
def get_settings():
    s = db.settings.find_one({})
    if not s:
        # default settings
        s_id = create_document("settings", {
            "commission_percent": 10.0,
            "payments": {"stripe": True, "paypal": False, "klarna": True, "sofort": True, "giropay": True}
        })
        s = db.settings.find_one({"_id": oid(s_id)})
    s["id"] = str(s.pop("_id"))
    return s


class UpdateSettings(BaseModel):
    commission_percent: Optional[float] = None
    payments: Optional[Dict[str, bool]] = None


@app.put("/api/admin/settings")
def update_settings(body: UpdateSettings):
    updates = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    db.settings.update_one({}, {"$set": updates}, upsert=True)
    create_document("auditlog", {"action": "update_settings", "resource_type": "settings", "metadata": updates})
    return {"updated": True}


@app.get("/api/admin/stats")
def admin_stats():
    sellers = db.seller.count_documents({})
    products = db.product.count_documents({})
    total_revenue = 0.0
    paid = db.purchase.find({"payment_status": "paid"})
    for p in paid:
        total_revenue += float(p.get("total_amount", 0))
    return {
        "sellers": sellers,
        "products": products,
        "platform_revenue": round(total_revenue, 2),
    }


@app.get("/api/admin/sellers")
def list_sellers():
    sellers = list(db.seller.find({}).limit(100))
    for s in sellers:
        s["id"] = str(s.pop("_id"))
    return {"sellers": sellers}


@app.put("/api/admin/sellers/{seller_user_id}/status")
def set_seller_status(seller_user_id: str, status: str):
    if status not in ("pending", "approved", "suspended"):
        raise HTTPException(status_code=400, detail="Invalid status")
    db.seller.update_one({"user_id": seller_user_id}, {"$set": {"status": status}}, upsert=True)
    return {"updated": True}


@app.get("/api/admin/logs")
def get_logs(limit: int = 50):
    logs = list(db.auditlog.find({}).sort("created_at", -1).limit(limit))
    for l in logs:
        l["id"] = str(l.pop("_id"))
    return {"logs": logs}


# ------------------------------- Schemas info ------------------------------
@app.get("/schema")
def schema_info():
    # expose simple schema info for tooling
    return {
        "collections": [
            "user", "seller", "product", "purchase", "payout", "settings", "notification", "auditlog"
        ]
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
