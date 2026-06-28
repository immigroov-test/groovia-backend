# Test-compatibility shim: maps `import utils` to `ai.tools` so that
# patch("utils._tavily") and patch("utils.exa.search") target the real objects.
import sys
import ai.tools

sys.modules[__name__] = ai.tools
