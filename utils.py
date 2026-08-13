# Test-compatibility shim: maps `import utils` to `ai.tools` so that
# patch("utils._tavily") targets the real object.
import sys
import ai.tools

sys.modules[__name__] = ai.tools
