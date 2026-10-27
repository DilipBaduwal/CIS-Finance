"""Standalone launcher for the client application.

Run from the repository root with: ``streamlit run client/client_app.py``.
Keeping the entry point under ``client/`` prevents the internal ``pages/``
navigation from appearing in the client-facing app.
"""
import runpy
from pathlib import Path


runpy.run_path(str(Path(__file__).resolve().parents[1] / "client_app.py"), run_name="__main__")
