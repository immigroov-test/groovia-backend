"""db/__init__ star-imports every module into one namespace. Two modules exporting the same name
means the later import silently wins, which is how a webinar helper replaced the booking payment
one and took every paid booking down. Any collision fails here first."""
import importlib
import pkgutil

import db


def _public_names(mod):
    return {n for n in vars(mod) if not n.startswith("_") and callable(vars(mod)[n])
            and getattr(vars(mod)[n], "__module__", None) == mod.__name__}


def test_no_public_name_is_defined_in_two_db_modules():
    owners: dict[str, list[str]] = {}
    for info in pkgutil.iter_modules(db.__path__):
        if info.name == "bug_board":      # imported as a module, deliberately not star-imported
            continue
        mod = importlib.import_module(f"db.{info.name}")
        for name in _public_names(mod):
            owners.setdefault(name, []).append(info.name)
    clashes = {n: m for n, m in owners.items() if len(m) > 1}
    assert not clashes, f"defined in more than one db module: {clashes}"


def test_booking_order_creation_is_the_payments_one():
    from db import payments
    assert db.create_razorpay_order is payments.create_razorpay_order
