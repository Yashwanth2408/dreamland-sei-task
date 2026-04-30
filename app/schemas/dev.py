"""Dev-only API schemas."""
from typing import Optional
from pydantic import BaseModel, Field


class SeedUserRequest(BaseModel):
    timezone: Optional[str] = Field(default=None, description="IANA timezone")
    region: Optional[str] = Field(default=None, description="Data residency region")
    username: Optional[str] = Field(default=None, description="Optional username override")
    email: Optional[str] = Field(default=None, description="Optional email override")
    external_id: Optional[str] = Field(default=None, description="Optional external ID override")


class SeedUserResponse(BaseModel):
    id: str
    external_id: str
    username: str
    email: str
    timezone: str
    region: str
    is_active: bool
