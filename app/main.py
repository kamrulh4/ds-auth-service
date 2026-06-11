import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv(override=True)

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from datetime import datetime
import logging
from typing import List

from app.database import init_db, get_db, DBUser
from app.models import (
    UserCreate, 
    User, 
    Token, 
    LicenseInfo, 
    LoginRequest,
    UserProfile
)
from app.auth_utils import (
    get_password_hash, 
    verify_password, 
    create_access_token, 
    get_current_active_user
)
from app.firestore_service import firestore_service

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Dataryx Auth Service")

# Add CORS middleware to allow all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    init_db()

@app.post("/register", response_model=User)
def register(user_in: UserCreate, db: Session = Depends(get_db)):
    """Simple JSON registration: Firestore first, then local DB."""
    # 1. Check local DB
    db_user = db.query(DBUser).filter(DBUser.email == user_in.email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # 2. Update Firestore FIRST
    success = firestore_service.ensure_default_subscription(user_in.email)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initialize account in Firestore."
        )
        
    # 3. Create local record
    try:
        hashed_pass = get_password_hash(user_in.password)
        new_user = DBUser(
            email=user_in.email,
            full_name=user_in.full_name,
            hashed_password=hashed_pass
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        return new_user
    except Exception as e:
        logger.error(f"Local DB Error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Local database update failed.")

@app.post("/login", response_model=Token)
def login(login_data: LoginRequest, db: Session = Depends(get_db)):
    """PURE JSON LOGIN: No OAuth2 forms, no extras."""
    user = db.query(DBUser).filter(DBUser.email == login_data.email).first()
    if not user or not verify_password(login_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Generate token with 'sub' as current user email
    access_token = create_access_token(data={"sub": user.email})
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/me", response_model=User)
def read_users_me(current_user: DBUser = Depends(get_current_active_user)):
    return current_user

def _get_user_subscription(email: str) -> LicenseInfo:
    sub_data = firestore_service.get_subscription(email)
    if not sub_data:
        firestore_service.ensure_default_subscription(email)
        sub_data = firestore_service.get_subscription(email)
    
    if not sub_data:
         raise HTTPException(status_code=404, detail="Subscription data not found")

    from app.firestore_service import SUBSCRIPTION_PLANS
    prod_name = sub_data.get("product", "Dataryx Free")
    plan = next((p for p in SUBSCRIPTION_PLANS.values() if p.product_name == prod_name), 
                SUBSCRIPTION_PLANS["free_trial"])
    
    from datetime import datetime
    expiry_str = sub_data.get("expiry")
    expiry = datetime.fromisoformat(expiry_str) if expiry_str else None
    
    # Simple naive comparison
    is_expired = False
    if expiry:
        is_expired = expiry.replace(tzinfo=None) < datetime.utcnow()

    return LicenseInfo(
        plan_id=plan.plan_id,
        product=prod_name,
        display_name=plan.display_name,
        conversions_remaining=sub_data.get("conversions"),
        max_file_size_mb=plan.max_file_size_mb,
        expiry=expiry,
        is_expired=is_expired,
        paypal_link=plan.paypal_link
    )

@app.get("/subscription", response_model=LicenseInfo)
def get_subscription(current_user: DBUser = Depends(get_current_active_user)):
    return _get_user_subscription(current_user.email)

@app.get("/profile", response_model=UserProfile)
def get_profile(current_user: DBUser = Depends(get_current_active_user)):
    """Consolidated profile endpoint for faster desktop app loading."""
    subscription = _get_user_subscription(current_user.email)
    return UserProfile(
        user=current_user,
        subscription=subscription
    )

@app.post("/subscription/decrement")
def decrement_run(current_user: DBUser = Depends(get_current_active_user)):
    success = firestore_service.decrement_run(current_user.email)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Could not decrement run. Limit reached or subscription expired."
        )
    return {"message": "Run decremented successfully"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
