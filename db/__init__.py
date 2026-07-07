from .mentors import *
from .bookings import *
from .chat import *
from .direct_booking import *
from .storage import *


def client():
    from .mentors import _supabase
    return _supabase
