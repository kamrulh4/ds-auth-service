from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime

class Token(BaseModel):
    access_token: str
    token_type: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class TokenData(BaseModel):
    email: Optional[str] = None

class UserBase(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None

class UserCreate(UserBase):
    password: str

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    disabled: Optional[bool] = None
    password: Optional[str] = None

class User(UserBase):
    id: int
    disabled: bool = False
    is_admin: bool = False

    class Config:
        from_attributes = True

class LicenseInfo(BaseModel):
    plan_id: str
    product: str
    display_name: str
    conversions_remaining: Optional[int]
    max_file_size_mb: int
    expiry: Optional[datetime]
    is_expired: bool
    paypal_link: Optional[str]

class UserProfile(BaseModel):
    user: User
    subscription: LicenseInfo
