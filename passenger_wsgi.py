"""
cPanel "Setup Python App" (Passenger) entry point. Set Application startup file to
passenger_wsgi.py and Application Entry point to ``application``. See DEPLOYMENT.md.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "horilla.settings")

from horilla.wsgi import application  # noqa: E402,F401
