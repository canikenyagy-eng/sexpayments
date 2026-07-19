from app.api.v1.endpoints import receipt_moderations
from app.common.enums.users import UserRole
from app.modules.users.permissions import require_admin_or_support


def _route(path: str, method: str = "GET"):
    method = method.upper()
    for route in receipt_moderations.router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route
    raise AssertionError(f"{method} {path} is not registered")


def _has_dependency(route, dependency) -> bool:
    if any(dep.dependency is dependency for dep in getattr(route, "dependencies", [])):
        return True
    dependant = getattr(route, "dependant", None)
    return any(dep.call is dependency for dep in getattr(dependant, "dependencies", []))


def test_receipt_moderation_endpoints_allow_admin_or_support():
    assert require_admin_or_support.allowed_roles == [UserRole.ADMIN, UserRole.SUPPORT]

    for path, method in (
        ("", "GET"),
        ("/order/{order_id}", "GET"),
        ("/{order_id}/decide", "POST"),
    ):
        assert _has_dependency(_route(path, method), require_admin_or_support), (
            f"{method} /receipt-moderations{path} must allow admin/support"
        )
