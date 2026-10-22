from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
GUI = ROOT / "chatterbox_studio.py"
CACHE = Path.home() / ".cache" / "chatterbox_onnx"
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "launcher.log"

# Only checks the existing environment. The daily launcher never pip-installs,
# downloads models, runs setup_chatterbox.py, or edits GUI-generated JSON files.
REQUIRED_IMPORTS = {
    "chatterbox_onnx": "chatterbox-onnx",
    "onnxruntime": "onnxruntime",
    "numpy": "numpy",
    "soundfile": "soundfile",
    "tokenizers": "tokenizers",
    "huggingface_hub": "huggingface-hub",
}


def log(message: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}"
    print(line, flush=True)
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
    except OSError:
        # Console output remains useful even if logging is unavailable.
        pass


def check_gui() -> None:
    if not GUI.is_file():
        raise RuntimeError(
            f"Main GUI not found: {GUI}\n"
            "Keep chatterbox_studio.py beside launcher.py in the project root."
        )


def check_venv() -> None:
    if not VENV_PY.is_file():
        raise RuntimeError(
            f"Project virtual environment not found: {VENV_PY}\n"
            "The daily launcher will not create an environment or install packages.\n"
            "Run your separate setup/recovery procedure first, then launch again."
        )


def check_imports() -> None:
    missing = [module for module in REQUIRED_IMPORTS
               if importlib.util.find_spec(module) is None]
    if missing:
        details = ", ".join(f"{name} (package: {REQUIRED_IMPORTS[name]})" for name in missing)
        raise RuntimeError(
            "Required backend imports are missing from .venv: " + details + "\n"
            "The daily launcher will not install dependencies. Run the separate "
            "setup/recovery procedure, then launch again."
        )


def check_cache() -> None:
    required = [
        "tokenizer.json",
        "default_voice.wav",
        "onnx/speech_encoder.onnx",
        "onnx/speech_encoder.onnx_data",
        "onnx/embed_tokens.onnx",
        "onnx/embed_tokens.onnx_data",
        "onnx/conditional_decoder.onnx",
        "onnx/conditional_decoder.onnx_data",
    ]
    missing = [name for name in required
               if not (CACHE / name).is_file() or (CACHE / name).stat().st_size == 0]

    variants = (
        ("onnx/language_model_q4f16.onnx", "onnx/language_model_q4f16.onnx_data"),
        ("onnx/language_model_q4.onnx", "onnx/language_model_q4.onnx_data"),
        ("onnx/language_model.onnx", "onnx/language_model.onnx_data"),
    )
    found_pair = any(
        all((CACHE / name).is_file() and (CACHE / name).stat().st_size > 0 for name in pair)
        for pair in variants
    )
    if not found_pair:
        missing.append("a complete language-model ONNX + .onnx_data pair (q4f16, q4, or full)")

    if missing:
        raise RuntimeError(
            f"The existing model cache appears incomplete: {CACHE}\n"
            "Missing: " + "; ".join(missing) + "\n"
            "The daily launcher will not download or repair models. Run the separate "
            "setup/recovery procedure after checking the cache and backup."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Chatterbox Studio daily launcher")
    parser.add_argument("--debug", action="store_true", help="Print extra launch diagnostics")
    parser.add_argument("--offline", action="store_true", help="Set Hugging Face offline mode for the GUI")
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log("=" * 60)
    log(f"Project: {ROOT}")
    log(f"Launcher interpreter: {sys.executable}")
    log(f"GUI: {GUI}")
    log(f"Model cache: {CACHE}")

    # Validate paths before checking packages/cache; never setup just because
    # the GUI or its state JSON files are absent. JSONs belong to the GUI.
    check_gui()
    check_venv()

    # If launched with system Python, re-run checks under the project's venv.
    if Path(sys.executable).resolve() != VENV_PY.resolve():
        command = [str(VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]]
        log("Switching to the project's existing virtual environment.")
        return subprocess.run(command, cwd=ROOT).returncode

    check_imports()
    check_cache()

    if args.offline:
        os.environ["HF_HUB_OFFLINE"] = "1"
    log("[OK] Environment and cached model files are present. Skipping setup.")
    log("Starting the main GUI; GUI-generated session/settings JSON files are left untouched.")
    command = [str(VENV_PY), str(GUI)]
    if args.debug:
        log("Debug mode enabled.")
    return subprocess.run(command, cwd=ROOT, env=os.environ.copy()).returncode


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        try:
            log(f"[FAILED] {exc}")
            with LOG_FILE.open("a", encoding="utf-8") as stream:
                traceback.print_exc(file=stream)
        except Exception:
            pass
        print(f"\n[FAILED] {exc}", file=sys.stderr, flush=True)
        print(f"Log: {LOG_FILE}", file=sys.stderr, flush=True)
        sys.exit(1)
