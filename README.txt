CHATTERBOX STUDIO — DAILY LAUNCHERS
===================================

THE MAIN GUI
------------
chatterbox_studio.py is the main application. The JSON files chatterbox_studio_session.json and chatterbox_studio_settings.json are generated and managed by the GUI. The launchers do not read, rewrite, delete, or regenerate them.

Adding custom voices: Drop compatible reference voice `.wav` files into `D:\files\chatterbox.v1\voices\` or whatever location you setup; they will appear in the GUI’s Voice dropdown automatically on startup, or click Refresh voices button, if you add them while the GUI is open. D: is my location, yours might be different.

DAILY LAUNCH
------------
- Double-click Chatterbox.bat to see checks, progress, and errors.
- Double-click Chatterbox.vbs to launch with the console hidden.
- Logs are written to logs\launcher.log.

The daily launcher only checks that the GUI, existing .venv, required backend
imports, and expected model-cache files exist. It then starts chatterbox_studio.py.
It does NOT create a venv, pip-install packages, download models, call
setup_chatterbox.py, or modify the verified backup. This keeps normal launches
lightweight and predictable.

SETUP / RECOVERY
----------------
Run INSTALL_AND_CHECK.bat explicitly when you intend to run the existing
setup_chatterbox.py. That script remains the authority for setup and its smoke
test. Keep your known-working copy; do not replace it with a different version
without checking it against your working Q4F16 setup.

PROJECT LAYOUT
--------------
D:\files\chatterbox.v1\
    .venv\
    chatterbox_studio.py
    chatterbox_studio_session.json       (GUI-generated)
    chatterbox_studio_settings.json      (GUI-generated)
    setup_chatterbox.py
    launcher.py
    Chatterbox.bat
    Chatterbox.vbs
    INSTALL_AND_CHECK.bat
    output\
    voices\
    logs\
    models_backup_old_001\
        chatterbox_cache_verified\

CACHE
-----
The verified runtime cache has been observed at:
%USERPROFILE%\.cache\chatterbox_onnx
The launcher checks that cache but never repairs it. It accepts a complete
language-model pair for Q4F16, Q4, or full model, including the external
.onnx_data sidecar. It does not rename model files.

TROUBLESHOOTING
---------------
1. Run Chatterbox.bat so errors remain visible.
2. Read logs\launcher.log.
3. If the venv or imports are missing, use the separate setup/recovery process.
4. If model files are missing, inspect the cache and verified backup before
   running setup/recovery. The daily launcher never downloads model files.

PUBLIC REPOSITORY NOTES
-----------------------
Do not commit .venv/, output audio, private reference voices, logs, the model
cache, or models_backup_old_001/. Recommended .gitignore entries:

.venv/
output/
voices/*
!voices/.gitkeep
logs/
models_backup_old_001/
.cache/
__pycache__/
*.pyc
