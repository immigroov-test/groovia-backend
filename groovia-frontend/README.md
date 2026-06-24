# Groovia Frontend

Next.js frontend for Groovia, an AI-powered career and immigration assistant. It provides a chat interface that communicates with a decoupled FastAPI backend through a server-side proxy.

## Tech Stack
* Framework: Next.js 16 (App Router, Turbopack)
* Styling: Tailwind CSS v4
* Language: TypeScript
* Markdown: react-markdown + remark-gfm

## Local Development

1. Install dependencies
   ```
   npm install
   ```

2. Create `.env.local` pointing to the running backend:
   ```
   BACKEND_URL=http://localhost:8000/chat
   ```

3. Start the dev server
   ```
   npm run dev
   ```

   Open http://localhost:3000.

## Architecture

* `app/page.tsx` — Layout, sidebar, and viewport shell
* `app/api/chat/route.ts` — Server-side proxy to the FastAPI backend (keeps backend URL off the client)
* `components/ChatInterface.tsx` — Stateful chat component (file upload, intent selection, markdown rendering)
* `public/` — Static assets (logo)

## Deployment (Vercel)

1. Import this repository in the Vercel dashboard.
2. Under **Environment Variables**, set `BACKEND_URL` to the production backend URL (e.g. `https://your-backend.onrender.com/chat`). Do not prefix with `NEXT_PUBLIC_` — it must stay server-side.
3. Deploy.
