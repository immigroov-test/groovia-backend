from .mentors import *
from .bookings import *
from .chat import *
from .direct_booking import *
from .pricing import *
from .payments import *
from .jobs import *
from .notifications import *
from .bank import *
from .referrals import *
from .reviews import *
from .legal import *
# BUG-162: imported as a module, not star-imported - it talks to a DIFFERENT Supabase project and
# its generic names (list_bugs, enabled) should stay behind `db.bug_board.` rather than landing in
# the same namespace as the platform's own helpers.
from . import bug_board  # noqa: F401


def client():
    from .mentors import _supabase
    return _supabase
