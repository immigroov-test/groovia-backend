from unittest.mock import patch

import db
import pytest
from pydantic import ValidationError

from core.auth import AuthUser
from routers.careers import (
    CareerRoleBody,
    CareerSettingsBody,
    admin_create_role,
    admin_delete_role,
    admin_settings,
    admin_update_role,
)


def _user() -> AuthUser:
    return AuthUser(id="admin-1", email="admin@example.com", role="authenticated")


def _role() -> CareerRoleBody:
    return CareerRoleBody(
        department="Tech",
        department_order=1,
        title="Frontend Engineer",
        summary="Build thoughtful product experiences.",
        description="Work with the product team.",
        expectations="Strong React fundamentals.",
        location="Remote",
        employment_type="Internship",
        sort_order=2,
    )


def test_settings_requires_web_url():
    with pytest.raises(ValidationError, match="http"):
        CareerSettingsBody(apply_url="forms.gle/example")


def test_admin_saves_shared_application_link():
    body = CareerSettingsBody(apply_url="https://forms.gle/example")
    with patch.object(db, "save_career_settings", return_value={"apply_url": body.apply_url}) as save:
        result = admin_settings(body, user=_user())
    assert result["apply_url"] == body.apply_url
    save.assert_called_once_with(body.apply_url)


def test_admin_creates_configurable_role():
    body = _role()
    with patch.object(db, "create_career_role", return_value={"id": "role-1", **body.model_dump()}) as create:
        result = admin_create_role(body, user=_user())
    assert result["department"] == "Tech"
    create.assert_called_once_with(body.model_dump())


def test_admin_updates_existing_role():
    body = _role()
    with patch.object(db, "get_career_role", return_value={"id": "role-1"}), \
         patch.object(db, "update_career_role", return_value={"id": "role-1"}) as update:
        result = admin_update_role("role-1", body, user=_user())
    assert result["id"] == "role-1"
    update.assert_called_once_with("role-1", body.model_dump())


def test_admin_deletes_existing_role():
    with patch.object(db, "get_career_role", return_value={"id": "role-1"}), \
         patch.object(db, "delete_career_role") as delete:
        assert admin_delete_role("role-1", user=_user()) == {"ok": True}
    delete.assert_called_once_with("role-1")
