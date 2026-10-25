"""Small environment-backed login for the five-person local deployment."""

import hmac
import os

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - requirements include python-dotenv
    load_dotenv = None


def _load_environment():
    if load_dotenv is not None:
        load_dotenv(override=False)
        return
    # Keep the login screen usable even when the optional dotenv import is
    # unavailable during an early app import.
    env_path = os.path.join(os.getcwd(), ".env")
    try:
        with open(env_path, encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))
    except OSError:
        pass


def configured_users():
    _load_environment()
    users = {}
    for number in range(1, 6):
        username = os.getenv("CIS_FIN_USER_{}_USERNAME".format(number), "").strip()
        password = os.getenv("CIS_FIN_USER_{}_PASSWORD".format(number), "")
        if username and password:
            users[username] = password
    return users


def authenticate(username, password):
    expected = configured_users().get(username.strip())
    return bool(expected) and hmac.compare_digest(expected, password)
