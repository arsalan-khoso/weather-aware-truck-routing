"""Django settings.

The app stores nothing, so there is no database and no migrations: a trip is
planned from live provider data on each request. Forecasts are cached in memory
per grid cell, which is what keeps a repeated or overlapping trip fast.
"""

from pathlib import Path

from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BASE_DIR.parent

load_dotenv(REPO_DIR / ".env")

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure-key")
DEBUG = os.environ.get("DEBUG", "0") == "1"

ALLOWED_HOSTS = ["*"] if DEBUG else [
    h.strip() for h in os.environ.get("ALLOWED_HOSTS", ".vercel.app,localhost,127.0.0.1").split(",") if h.strip()
]
CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "https://*.vercel.app").split(",") if o.strip()
]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "corsheaders",
    "routing",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    # The built single-page app is served from here in production.
    "DIRS": [REPO_DIR / "frontend" / "dist"],
    "APP_DIRS": False,
    "OPTIONS": {"context_processors": []},
}]

# No models, so no database. Django is happy with none configured.
DATABASES = {}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "forecasts",
        # A long corridor is a few hundred cells; this holds several trips.
        "OPTIONS": {"MAX_ENTRIES": 5000, "CULL_FREQUENCY": 4},
    }
}

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# The built app's assets, kept under an "assets/" prefix so the URLs Vite wrote
# into index.html resolve as /static/assets/... .
_DIST_ASSETS = REPO_DIR / "frontend" / "dist" / "assets"
STATICFILES_DIRS = [("assets", _DIST_ASSETS)] if _DIST_ASSETS.exists() else []

# Compressed, but deliberately not the manifest storage: Vite already
# content-hashes every filename, and the manifest pass cannot rewrite ES module
# imports inside a bundled chunk, so it would fail on a correct build.
STORAGES = {
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# The Vite dev server runs on another port, so development needs CORS. Production
# serves the built app from this same origin and needs none.
CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
CORS_ALLOW_CREDENTIALS = False

USE_TZ = True
TIME_ZONE = "UTC"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {"routing": {"handlers": ["console"], "level": "INFO", "propagate": False}},
}
