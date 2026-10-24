from django.urls import path

from .views import mcp_endpoint

urlpatterns = [
    # Served at POST /mcp when included at the project root.
    path('mcp', mcp_endpoint, name='veilfile-mcp'),
]
