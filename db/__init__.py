from .mentors import *
from .bookings import *
from .chat import *
from .direct_booking import *
from .pricing import *
from .payments import *
from .reviews import *
from .referrals import *
from .webinars import *


def client():
    from .mentors import _supabase
    return _supabase
