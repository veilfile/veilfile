"""The install line's three tabs (spec 4.2): what each shows, what Copy copies, and the line after Copy.
These are words a visitor reads; edit them here, keeping each command exactly as it runs."""
from pages.registry import SITE_ORIGIN

INSTALL_TABS = (
    {"key": "mcp", "label": "MCP", "prompt": "", "command": f"{SITE_ORIGIN}/mcp",
     "then": "Add this address to your agent as a remote MCP server, and sign in if the agent asks.", "then_code": ""},
    {"key": "npm", "label": "npm", "prompt": "$", "command": "npm install -g veilfile",
     "then": "Then run", "then_code": "veilfile login"},
    {"key": "pip", "label": "pip", "prompt": "$", "command": "pip install veilfile",
     "then": "Then run", "then_code": "veilfile login"},
)
