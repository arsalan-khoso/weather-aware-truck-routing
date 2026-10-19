"""WSGI entrypoint, also used as the serverless handler in production."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

application = get_wsgi_application()
app = application          # some hosts look for `app`
