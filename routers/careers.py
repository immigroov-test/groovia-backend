from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

import db
from core.auth import AuthUser, require_admin

router = APIRouter(tags=["careers"])


class CareerSettingsBody(BaseModel):
    apply_url: str = Field(default="", max_length=2000)

    @field_validator("apply_url")
    @classmethod
    def valid_apply_url(cls, value: str) -> str:
        value = value.strip()
        if value and not value.startswith(("https://", "http://")):
            raise ValueError("Application link must start with http:// or https://")
        return value


class CareerRoleBody(BaseModel):
    department: str = Field(min_length=2, max_length=80)
    department_order: int = Field(default=0, ge=0, le=10000)
    title: str = Field(min_length=2, max_length=140)
    summary: str = Field(default="", max_length=500)
    description: str = Field(default="", max_length=20000)
    expectations: str = Field(default="", max_length=20000)
    location: Optional[str] = Field(default=None, max_length=120)
    employment_type: Optional[str] = Field(default=None, max_length=120)
    sort_order: int = Field(default=0, ge=0, le=10000)
    is_active: bool = True

    @field_validator("department", "title")
    @classmethod
    def clean_required_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("summary", "description", "expectations")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("location", "employment_type")
    @classmethod
    def clean_optional_text(cls, value: Optional[str]) -> Optional[str]:
        value = value.strip() if value else ""
        return value or None


@router.get("/careers")
def public_list():
    return db.public_careers()


@router.get("/admin/careers")
def admin_list(user: AuthUser = Depends(require_admin)):
    return db.admin_careers()


@router.post("/admin/careers/settings")
def admin_settings(body: CareerSettingsBody, user: AuthUser = Depends(require_admin)):
    return db.save_career_settings(body.apply_url)


@router.post("/admin/careers/roles")
def admin_create_role(body: CareerRoleBody, user: AuthUser = Depends(require_admin)):
    return db.create_career_role(body.model_dump())


@router.post("/admin/careers/roles/{role_id}")
def admin_update_role(role_id: str, body: CareerRoleBody, user: AuthUser = Depends(require_admin)):
    if not db.get_career_role(role_id):
        raise HTTPException(status_code=404, detail="Career role not found")
    return db.update_career_role(role_id, body.model_dump())


@router.post("/admin/careers/roles/{role_id}/delete")
def admin_delete_role(role_id: str, user: AuthUser = Depends(require_admin)):
    if not db.get_career_role(role_id):
        raise HTTPException(status_code=404, detail="Career role not found")
    db.delete_career_role(role_id)
    return {"ok": True}
