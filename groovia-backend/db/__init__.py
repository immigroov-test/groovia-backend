from .mentors import *
from .bookings import *
from .chat import *


def client():
    from .mentors import _supabase
    return _supabase
