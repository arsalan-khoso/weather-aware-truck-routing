"""WSGI entrypoint, also used as the serverless handler in production.

The backend package lives in a subdirectory, and the host may import this file
with any working directory, so `backend/` is put on the path explicitly rather
than assumed.
"""
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
app = application          # some hosts look for `app`
