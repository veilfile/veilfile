from django.urls import path

from .views import mcp_endpoint

urlpatterns = [
    # Served at POST /mcp when included at the project root.
    path('mcp', mcp_endpoint, name='veilfile-mcp'),
    # Same endpoint with a trailing slash: the MCP registry treats remote
    # URLs as globally unique strings, and https://veilfile.com/mcp is
    # already claimed by the old listing in the previous namespace, so the
    # new org-namespace listing publishes /mcp/ instead.
    path('mcp/', mcp_endpoint, name='veilfile-mcp-slash'),
]
