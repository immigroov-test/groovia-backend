# Test-compatibility shim: maps `import backend` to `ai.graph` so that
# patch("backend.primary_llm") targets the real module-level object in ai.graph.
import sys
import ai.graph

sys.modules[__name__] = ai.graph
