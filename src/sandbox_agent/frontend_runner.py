"""Entry point for the Streamlit frontend."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent.parent.parent
    app_path = root / "frontend" / "app.py"
    if not app_path.exists():
        print(f"Error: frontend app not found at {app_path}", file=sys.stderr)
        sys.exit(1)
    # Delegate to streamlit CLI
    sys.argv = ["streamlit", "run", str(app_path), "--server.port=8501"]
    import streamlit.web.cli as stcli

    stcli.main()


if __name__ == "__main__":
    main()
