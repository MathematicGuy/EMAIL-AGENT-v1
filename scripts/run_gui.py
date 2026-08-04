"""Launcher script for Module Mail Streamlit GUI interface."""

import os
import subprocess
import sys


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)
    src_dir = os.path.join(root_dir, "src")

    # Set PYTHONPATH
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir};{existing_pythonpath}" if existing_pythonpath else src_dir

    print("🚀 Starting Module Mail Streamlit Test Suite...")
    print(f"📂 Root Directory: {root_dir}")
    print("🌐 Launching Streamlit on http://localhost:8501")

    # Command to run Streamlit
    streamlit_cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        os.path.join(src_dir, "mail_todo", "gui", "app.py"),
    ]

    try:
        subprocess.run(streamlit_cmd, env=env, check=True)
    except KeyboardInterrupt:
        print("\n👋 Streamlit GUI stopped.")
    except Exception as e:
        print(f"\n❌ Error launching Streamlit: {e}")


if __name__ == "__main__":
    main()
