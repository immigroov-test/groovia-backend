# Groovia / Immigroov — System Flows

Living reference for the interdependent flows in the platform (mentor lifecycle, admin
review, revisions, booking). Diagrams are written in **Mermaid** (diagrams-as-code) so
they live in version control, diff in pull requests, and can be edited by anyone.

> Keep these in sync with the code. When you change a status, an endpoint, or a
> transition, update the relevant diagram in the same PR.

## How to view and edit

- **GitHub**: renders ` ```mermaid ` code blocks automatically — just open this file.
- **VS Code**: install the *Markdown Preview Mermaid Support* extension, then open the
  Markdown preview.
- **Live editor**: paste a diagram into <https://mermaid.live> to edit it visually and
  export PNG/SVG.
- **Export from CLI**: `npx @mermaid-js/mermaid-cli -i FLOWS.md -o flows.pdf`.

Authoritative sources in code:
- Mentor status enum: `migrations/testing_db_setup.sql` (`mentor_status`)
- Status transitions: `db/mentors.py` (`set_mentor_status`, `save_mentor_profile_edit`,
  `apply_pending_changes`, `discard_pending_changes`)
- Admin actions: `routers/admin.py` · Mentor actions: `routers/mentor.py`

---

## 1. System context (who talks to whom)

```mermaid
flowchart LR
    U["Browser (mentee / mentor / admin)"]
    FE["Next.js on Vercel<br/>(BFF: app/api/** proxies)"]
    BE["FastAPI on Render<br/>(service-role key)"]
    DB[("Supabase<br/>Postgres + Auth + Storage")]
    EM["Resend<br/>(transactional email)"]

    U --> FE
    FE -->|"bearer token"| BE
    BE --> DB
    BE --> EM
    U -. "Supabase Auth: magic link + session cookie" .-> DB
    FE -. "reads public mentor data / auth" .-> DB
```

The frontend never calls the backend directly from the browser: every call goes through
a `app/api/**` BFF route that attaches the user's bearer token. The backend uses the
Supabase service-role key and is the only tier that writes privileged data.

---

## 2. Mentor status lifecycle (state machine)

The core of the interdependence: a mentor row moves between these states, driven by
either the mentor or an admin. `pending_changes` is a *staged revision* on an already
`approved` mentor — the live profile keeps serving while the edit waits for review.

```mermaid
stateDiagram-v2
    [*] --> pending_review : submits application

    pending_review --> approved : admin approves
    pending_review --> changes_requested : admin requests changes
    pending_review --> rejected : admin declines

    changes_requested --> pending_review : mentor edits + resubmits
    rejected --> pending_review : mentor edits + re-applies

    approved --> suspended : admin suspends
    suspended --> approved : admin reinstates

    state approved {
        [*] --> live
        live --> revision_pending : mentor edits profile (staged)
        revision_pending --> live : admin applies or discards
    }

    note left of pending_review
        Profile is read-only for the mentor
        while pending_review or suspended.
        The reviewer note (rejection_reason)
        is shown for changes_requested / rejected.
    end note
```

Editing rules by state (`db/mentors.py::save_mentor_profile_edit`):

| State | Can the mentor edit? | Where do edits go? |
|---|---|---|
| `pending_review` | No (locked) | — |
| `suspended` | No (locked) | — |
| `changes_requested` | Yes | live row → resubmits (`→ pending_review`) |
| `rejected` | Yes | live row → re-applies (`→ pending_review`) |
| `approved` | Yes | `pending_changes` (staged); live stays up |

---

## 3. New application review (sequence)

```mermaid
sequenceDiagram
    actor M as Mentor
    participant FE as Next.js (BFF)
    participant BE as FastAPI
    participant DB as Supabase
    participant EM as Resend
    actor A as Admin

    M->>FE: Submit onboarding form
    FE->>BE: POST /mentor/signup
    BE->>DB: create mentor (status = pending_review)
    BE-->>EM: mentor_application_received
    BE-->>FE: 201 { id }
    Note over M: Dashboard shows "under review"; profile locked

    A->>FE: Open Admin → Review tab
    FE->>BE: GET /admin/mentors/pending
    BE->>DB: list pending
    BE-->>A: pending applications (photo + full detail)

    alt Approve
        A->>BE: POST /admin/mentors/{id}/approve
        BE->>DB: status = approved
        BE-->>EM: mentor_approved + welcome_mentor
    else Request changes
        A->>BE: POST /admin/mentors/{id}/request-changes (note)
        BE->>DB: status = changes_requested, rejection_reason = note
        BE-->>EM: mentor_changes_requested
    else Decline
        A->>BE: POST /admin/mentors/{id}/reject (note)
        BE->>DB: status = rejected, rejection_reason = note
        BE-->>EM: mentor_rejected
    end
    Note over M: Sees result + reviewer note; if changes_requested / rejected,<br/>edits and resubmits → back to pending_review
```

---

## 4. Approved-mentor profile revision (sequence)

The "edit a live profile" loop. The public profile is never taken down; the edit is
staged and reviewed separately from the mentor list.

```mermaid
sequenceDiagram
    actor M as Mentor (approved)
    participant BE as FastAPI
    participant DB as Supabase
    actor A as Admin

    M->>BE: POST /mentor/profile (edited fields)
    BE->>DB: save_mentor_profile_edit → pending_changes staged, pending_submitted_at set
    Note over M,DB: Live profile unchanged and still bookable

    A->>BE: GET /admin/mentors/revisions
    BE->>DB: approved mentors WHERE pending_changes IS NOT NULL
    BE-->>A: proposed changes (diff view)

    alt Approve revision
        A->>BE: POST /admin/mentors/{id}/revision/approve
        BE->>DB: apply_pending_changes → copy to live, clear staging
    else Request changes
        A->>BE: POST /admin/mentors/{id}/revision/request-changes (note)
        BE->>DB: discard_pending_changes → drop staged edit, set rejection_reason
    end
    Note over M: Dashboard reflects outcome (applied, or "changes not applied" + note)
```

---

## 5. Booking a session (flowchart)

```mermaid
flowchart TD
    A[Mentee opens mentor profile] --> B[Choose a service]
    B --> C["Load slots: GET /api/booking/slots/{mentor}/{service}"]
    C --> D[Pick a date and time]
    D --> E{Logged in?}
    E -->|Yes| H[Confirm - books as the signed-in user]
    E -->|No| F[Enter email]
    F --> G{"Email already has an account? (POST /api/auth/check-email)"}
    G -->|Yes| I[Login popup, then auto-submit the booking]
    G -->|No| H2[Confirm as guest]
    I --> J
    H --> J
    H2 --> J[POST /api/booking]
    J --> K[Booking confirmed + emails to mentee and mentor]

    %% Payment is a future step that will slot in before POST /api/booking.
```

> **Future:** payment sits between "Confirm" and `POST /api/booking`. When it lands,
> add a `Pay` node on the confirm path and a `payment.succeeded` webhook branch — the
> rest of the flow is unchanged.

---

## Keeping diagrams honest

A diagram that drifts from the code is worse than none. Two habits keep them accurate:

1. **Change the diagram in the same PR as the flow.** Treat it like a test.
2. **Name real endpoints and DB functions** (as above) so a reader can jump from the
   diagram straight to the code.
