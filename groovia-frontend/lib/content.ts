export const UI_CONTENT = {
  welcomeMessage: "Hi, I'm Groovia. Attach your resume below and we'll get started.",
  uploadIndicator: '📎 Resume uploaded',
  intentPrompt: 'What would you like to do next?',
  inputPlaceholder: 'Ask about your career...',
  inputPlaceholderLocked: 'Login to continue…',
  disclaimer:
    'This is AI-generated content based on publicly available information. It is not legal advice. Always consult a qualified immigration professional before making decisions.',
  signInToContinue: 'Login to continue the conversation',
  errors: {
    backendUnreachable: 'Error: Could not connect to backend.',
    noResponse: 'No response received.',
  },
  tooltips: {
    attachResume: 'Attach resume (PDF or DOCX)',
    resumeAlreadyUploaded: 'Resume already uploaded',
  },
  hero: {
    title: "Hi, I'm Groovia.",
    tagline: 'The AI assistant of Immigroov.',
    intro:
      "Tell me where you want to go — I'll plan the route and connect you with people who've already made the move.",
    movement:
      "Immigroov isn't just a platform — it's a movement. A global community of people helping people move smarter, together.",
    features: [
      'Discover 3–5 countries that fit your skills and budget.',
      "Book meetings with real mentors who've already made the move.",
      'Get instant real-time answers on visas, jobs, housing and daily life.',
    ],
    scrollCta: 'Scroll down to start your journey',
  },
  sidebar: {
    newChat: 'New chat',
    chat: 'Chat',
    mentors: 'Mentors',
    account: 'Account',
    mentorPortal: 'Join as Mentor',
    admin: 'Admin',
    history: 'Recent chats',
    historyEmpty: 'No previous chats yet.',
    signIn: 'Login',
    signOut: 'Sign out',
  },
  admin: {
    title: 'Admin',
    subtitle: 'Manage mentors and review applications.',
    pendingTitle: 'Pending Applications',
    pendingSubtitle: 'Review and approve mentors who have applied to join Immigroov.',
    activeTitle: 'Active Mentors',
    activeSubtitle: 'Currently approved and visible to candidates.',
    suspendedTitle: 'Suspended Mentors',
    suspendedSubtitle: 'Paused profiles — not visible to candidates.',
    empty: 'None.',
    approve: 'Approve',
    reject: 'Reject',
    suspend: 'Suspend',
    reinstate: 'Reinstate',
  },
  booking: {
    videoCall: 'Video call',
    confirmedTitle: 'Session confirmed!',
    confirmedBody: (mentorName: string, email: string) =>
      `Your session with ${mentorName} is confirmed. A confirmation email has been sent to ${email}.`,
    joinVideoCall: 'Join video call',
    addToCalendar: 'Add to Google Calendar',
    noSlotsTitle: (firstName: string) => `${firstName} has no open slots in the next two weeks.`,
    noSlotsBody: 'Please check back later.',
    loadingSlots: 'Loading available times…',
    form: {
      namePlaceholder: 'Jane Doe',
      emailPlaceholder: 'jane@example.com',
      notesLabel: 'Additional notes',
      notesPlaceholder: 'Please share anything that will help prepare for our meeting.',
      termsPrefix: "By proceeding, you agree to Immigroov's",
      terms: 'Terms',
      privacy: 'Privacy Policy',
      back: 'Back',
      confirm: 'Confirm',
    },
  },
  signupModal: {
    title: 'Save your progress to continue',
    subtitle:
      'Create a free account (or login) to keep your report, conversation and country recommendations.',
    createAccount: 'Create free account',
    haveAccount: 'I already have an account',
    requireAccount: 'We need an account before continuing.',
    noCreditCard: 'No credit card needed',
  },
} as const;

export const INTENT_OPTIONS = [
  { label: '📊 Generate a Career Report', message: 'I want to generate a career report.' },
  { label: '🤝 Find me a Mentor',         message: 'I want to find a mentor.'           },
  { label: '💬 Ask a Question',           message: 'I just want to ask some questions.' },
] as const;
