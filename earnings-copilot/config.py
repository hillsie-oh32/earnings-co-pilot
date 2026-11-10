# Settings for the whole project.

import os
from pathlib import Path

from dotenv import load_dotenv

# Private settings (your Claude API key, your email) live in a file called
# .env next to this one, so they never end up in the code or on GitHub.
# See .env.example. On Streamlit Cloud they go in the app's "Secrets" instead.
load_dotenv(Path(__file__).parent / ".env")

# The SEC asks everyone using its free data to identify themselves with a
# contact email.
SEC_CONTACT_EMAIL = os.environ.get("SEC_CONTACT_EMAIL", "").strip()
SEC_USER_AGENT = f"EarningsCopilot student project {SEC_CONTACT_EMAIL or 'no-contact-set'}"
