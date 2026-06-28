# generate_architecture.py
# Produces system_architecture.png using the `diagrams` library.
# Run: pip install diagrams && python generate_architecture.py
# Requires Graphviz on PATH.

from diagrams import Diagram, Cluster, Edge
from diagrams.programming.framework import React, FastAPI
from diagrams.onprem.compute import Server
from diagrams.onprem.database import Postgresql
from diagrams.onprem.network import Internet

graph_attr = {
    "fontsize": "15",
    "bgcolor": "#FAFBFD",
    "splines": "ortho",
    "nodesep": "0.8",
    "ranksep": "1.2",
    "pad": "0.6",
    "labelloc": "t",
}

with Diagram(
    "Groovia — System Architecture",
    show=False,
    filename="system_architecture",
    direction="LR",
    graph_attr=graph_attr,
):
    user = Server("Candidate\n(browser)")

    with Cluster("Frontend  ·  Vercel  ·  Next.js 16"):
        frontend = React("Next.js App\n+ BFF proxy /api/*")

    with Cluster("Backend  ·  Fly.io  ·  FastAPI + LangGraph"):
        api = FastAPI("FastAPI\nrouters")
        agent_llm = Server("Agent LLM\nLlama-3.3-70b")
        reviewer_llm = Server("Reviewer LLM\nLlama-3.1-8b")

    with Cluster("Supabase  ·  EU West"):
        db = Postgresql("Postgres\n(profiles, mentors,\nbookings, threads)")
        sb_auth = Server("Auth\n(email + Google OAuth)")
        storage = Server("Storage\n(mentor-photos)")

    groq = Internet("Groq Cloud")
    tavily = Internet("Tavily\n(web search)")
    exa = Internet("Exa\n(neural search)")

    # User → Frontend
    user >> Edge(color="#1D4ED8") >> frontend

    # Frontend ↔ Supabase Auth (browser-side)
    frontend >> Edge(label="auth", color="#F59E0B", style="dashed") >> sb_auth

    # Frontend → Backend (BFF)
    frontend >> Edge(label="HTTPS + JWT", color="#0F2C6B") >> api

    # Backend → Supabase
    api >> Edge(label="service role", color="#dc2626") >> db
    api >> Edge(color="#dc2626") >> storage

    # Backend LangGraph internals
    api >> Edge(color="#10b981") >> agent_llm
    agent_llm >> Edge(color="#10b981") >> reviewer_llm

    # LLMs → Groq
    agent_llm >> Edge(color="#7c3aed") >> groq
    reviewer_llm >> Edge(color="#7c3aed") >> groq

    # Agent tools
    agent_llm >> Edge(color="#059669") >> tavily
    agent_llm >> Edge(color="#059669") >> exa
