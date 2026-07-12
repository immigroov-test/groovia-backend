"""Small-talk short-circuit for the chat agent.

Users often open with a greeting ("hi"), sign off ("bye"), or say "thanks" - each of
which would otherwise cost a full LLM (Groq) call. We match those purely-social
messages and reply with a canned response plus a gentle nudge that every message uses
credits, so the model is never invoked for them.

Deliberately conservative: only UNAMBIGUOUS social messages are short-circuited.
Answers like "yes" / "no" / "ok" / "sure" are left alone because they may be replies
to a question the agent just asked, and anything longer than a few words falls through
to the real agent.
"""
import re
from typing import Optional

# ── Phrase sets (compared against a normalized: lowercased, de-punctuated message) ──
_GREETINGS = {
    "hi", "hii", "hiii", "hey", "heyy", "heyyy", "hello", "helloo", "hellooo",
    "hiya", "yo", "hola", "namaste", "howdy", "hi there", "hey there",
    "hello there", "good morning", "good afternoon", "good evening", "good day",
    "gm", "morning", "greetings", "hi groovia", "hello groovia", "hey groovia",
}
_FAREWELLS = {
    "bye", "byee", "byeee", "bye bye", "goodbye", "good bye", "see you",
    "see ya", "see you later", "cya", "take care", "later", "catch you later",
    "gtg", "got to go", "adios", "ciao", "peace out", "thank you bye", "thanks bye",
}
_THANKS = {
    "thanks", "thank you", "thankyou", "thx", "ty", "tysm", "thanks a lot",
    "thanks so much", "thank you so much", "thanks a ton", "many thanks",
    "much appreciated", "appreciate it", "cheers", "thanks groovia",
    "great thanks", "ok thanks", "okay thanks", "cool thanks", "thanks a bunch",
}
_PLEASANTRIES = {
    "how are you", "how are you doing", "how r u", "hru", "how do you do",
    "hows it going", "how is it going", "how you doing", "whats up", "sup",
    "wassup", "you good", "you there", "are you there", "is anyone there",
    "hello?", "hi?", "test", "testing", "ping", "are you a bot", "are you real",
    "are you human", "good and you",
}
_CAPABILITIES = {
    "who are you", "what are you", "what can you do", "what do you do",
    "what is this", "whats this", "what is groovia", "who is groovia",
    "help", "help me", "how does this work", "how do you work",
}

_NUDGE = ("Quick note: every message uses AI credits, so please use Groovia for your "
          "career, job, and immigration questions, and ask them directly.")

# Shown when someone types before uploading a resume/profile: asks them to upload first,
# and carries the same credits + relevance reminder as the generic-term warning.
RESUME_GATE_REPLY = (
    "To give you advice that truly fits your situation, please upload your resume or "
    "profile using the button below first. Once I can see your background, I'll help with "
    "your career, job, and immigration questions. Every message uses AI credits, so please "
    "keep your questions relevant to careers, jobs, and immigration."
)

_REPLIES: dict[str, str] = {
    "greeting": (f"Hi! 👋 What would you like help with today? {_NUDGE}"),
    "farewell": ("Take care! 👋 Come back anytime with your career, job or immigration questions."),
    "thanks":   (f"You're welcome! 😊 Whenever you're ready, just ask your next question. {_NUDGE}"),
    "pleasantry": (f"Doing well and ready to help! 🙂 What's your career, job or immigration question? {_NUDGE}"),
    "capabilities": ("Here's what I can help with:\n"
                     "• 🌍 Find countries that fit your skills, goals and budget\n"
                     "• 📋 Explain visas and the steps to move\n"
                     "• 🤝 Match you with mentors who've already made the move\n"
                     "• 💼 Plan your job search abroad\n\n"
                     f"{_NUDGE}"),
}

# Order matters: check the more specific sets (capabilities/thanks) before greetings.
_CATEGORY_SETS: list[tuple[str, set[str]]] = [
    ("capabilities", _CAPABILITIES),
    ("thanks", _THANKS),
    ("farewell", _FAREWELLS),
    ("pleasantry", _PLEASANTRIES),
    ("greeting", _GREETINGS),
]

_MAX_WORDS = 5   # anything longer is treated as a real message


def _normalize(message: str) -> str:
    s = (message or "").strip().lower()
    s = re.sub(r"\s+", " ", s)                    # collapse inner whitespace
    s = re.sub(r"[!.,~\-?]+$", "", s).strip()     # drop trailing filler punctuation
    return s


def smalltalk_reply(message: str) -> Optional[str]:
    """Return a canned reply if `message` is purely small talk, else None.

    Only fires for short, unambiguous social messages so real questions (and short
    answers like 'yes'/'no') always reach the agent."""
    norm = _normalize(message)
    if not norm or len(norm.split()) > _MAX_WORDS:
        return None
    for category, phrases in _CATEGORY_SETS:
        if norm in phrases:
            return _REPLIES[category]
    return None
