"""codemap: turn code (or what an AI agent changed) into layered, human-sized maps.

Standard library only. Every stage is deterministic; the LLM is only ever asked
to name pre-chunked units through a small worksheet.
"""

VERSION = "0.3.0-beta.1"

# A view never shows more than MAX_VIEW boxes (Miller's 7 +/- 2).
MAX_VIEW = 9
# Extra dimmed "neighbour" boxes a view may show for context.
MAX_GHOSTS = 3
