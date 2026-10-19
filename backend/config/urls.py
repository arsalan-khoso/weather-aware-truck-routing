"""URL map: the API under /api/, everything else handed to the single-page app."""
from django.urls import include, path, re_path

from routing import views

urlpatterns = [
    path("api/", include("routing.urls")),
    # Client-side routing means any other path has to reach index.html.
    re_path(r"^(?!api/|static/).*$", views.index, name="index"),
]
