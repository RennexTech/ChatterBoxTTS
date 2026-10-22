from __future__ import annotations
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import wave

ROOT = Path(__file__).resolve().parent
CACHE = Path.home() / '.cache' / 'chatterbox_onnx'
BACKUP_ROOT = ROOT / 'models_backup_old_001'
BACKUP = BACKUP_ROOT / 'chatterbox_cache_verified'
OUTPUT = ROOT / 'output'
LOGS = ROOT / 'logs'
REQUIRED = ['chatterbox_onnx', 'onnxruntime', 'librosa', 'numpy', 'soundfile', 'tqdm', 'tokenizers', 'huggingface_hub']
PACKAGES = ['chatterbox-onnx', 'onnxruntime', 'librosa', 'numpy', 'soundfile', 'tqdm', 'tokenizers', 'huggingface_hub']
FILES = ['tokenizer.json', 'default_voice.wav'] + [f'onnx/{name}{ext}' for name in ('speech_encoder', 'embed_tokens', 'language_model_q4', 'conditional_decoder') for ext in ('.onnx', '.onnx_data')]

def log(message):
    print(message, flush=True)

def run(cmd):
    log('Running: ' + ' '.join(map(str, cmd)))
    subprocess.check_call(cmd)

def complete(folder):
    return all((folder / name).is_file() and (folder / name).stat().st_size > 0 for name in FILES)

def copy_tree(source, dest):
    log(f'Copying {source} -> {dest}')
    shutil.copytree(source, dest, dirs_exist_ok=True, copy_function=shutil.copy2)

def ensure_deps(offline):
    missing = [m for m in REQUIRED if importlib.util.find_spec(m) is None]
    if missing:
        if offline:
            raise RuntimeError('Missing packages in offline mode: ' + ', '.join(missing))
        log('Missing imports: ' + ', '.join(missing))
        # Installing the project package pulls its declared dependencies.
        run([sys.executable, '-m', 'pip', 'install', *PACKAGES])
    else:
        log('[OK] Required packages are installed; existing venv preserved.')

def diagnose(synth):
    session = synth.llama_with_past_session
    log('Language model input types:')
    for inp in session.get_inputs():
        log(f'  {inp.name}: {inp.type} {inp.shape}')

def main():
    parser = argparse.ArgumentParser(description='Chatterbox ONNX install, smoke test, and backup')
    parser.add_argument('--offline', action='store_true', help='Restore and run without any network access')
    parser.add_argument('--skip-backup', action='store_true', help='Do not write a verified backup')
    args = parser.parse_args()
    OUTPUT.mkdir(exist_ok=True)
    LOGS.mkdir(exist_ok=True)
    log(f'Project: {ROOT}\nCache: {CACHE}\nBackup: {BACKUP}\nOutput: {OUTPUT}')
    ensure_deps(args.offline)
    if not complete(CACHE) and complete(BACKUP):
        log('[*] Restoring complete backed-up model cache...')
        copy_tree(BACKUP, CACHE)
    if args.offline:
        if not complete(CACHE):
            raise RuntimeError('Offline cache is incomplete. Run online once to fetch correct Q4 model files.')
        # The package calls hf_hub_download on startup. Force local-only resolution.
        os.environ['HF_HUB_OFFLINE'] = '1'
    from chatterbox_onnx import ChatterboxOnnx
    log('[*] Loading Chatterbox ONNX (standard Q4, normal user cache)...')
    synth = ChatterboxOnnx(quantized=True, cache_dir=str(CACHE))
    diagnose(synth)
    out = OUTPUT / 'chatterbox_test.wav'
    log('[*] Generating test WAV (this may take a while on CPU)...')
    synth.synthesize(
        text='Hello Nick. Chatterbox is working.',
        target_voice_path=str(CACHE / 'default_voice.wav'),
        exaggeration=0.5,
        output_file_name=str(out),
        apply_watermark=False,
    )
    if not out.is_file() or out.stat().st_size <= 44:
        raise RuntimeError('Synthesis returned but did not produce a usable WAV file.')
    with wave.open(str(out), 'rb') as audio:
        duration = audio.getnframes() / audio.getframerate()
        if duration <= 0:
            raise RuntimeError('WAV file contains no audio frames.')
    log(f'[SUCCESS] {out} ({duration:.2f}s)')
    if not args.skip_backup:
        if not complete(CACHE):
            log('[WARNING] Cache files incomplete: skipping backup.')
        else:
            BACKUP_ROOT.mkdir(exist_ok=True)
            copy_tree(CACHE, BACKUP)
            manifest = {
                'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
                'python': sys.version,
                'cache': str(CACHE),
                'files': {str(p.relative_to(BACKUP)): p.stat().st_size for p in BACKUP.rglob('*') if p.is_file()},
            }
            (BACKUP_ROOT / 'verified_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
            log(f'[BACKUP COMPLETE] {BACKUP}')
    log('[READY] Play the WAV. If it sounds correct, the setup is verified.')

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('\n[FAILED]', exc, '\n', file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)
