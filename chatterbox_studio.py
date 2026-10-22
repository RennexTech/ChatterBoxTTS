"""
chatterbox_studio.py  -  Chatterbox Studio

The full-featured Chatterbox ONNX app: parts list, built-in player (skip 5/10/30 s,
seek bar, follow-along highlighting), paste tools, part splitter, a serious text
cleaner, zero-shot voice selection, voice conversion and an Ember theme.

Output names:  001.default.2026-10-22.The_morning_the_river_froze.mp3
Launch with chatterbox_studio.vbs / chatterbox_studio.bat.
"""
APP_NAME = 'Chatterbox Studio'
APP_SLUG = 'chatterbox_studio'
SUBTITLE = 'zero-shot cloning  \u00b7  parts  \u00b7  reader  \u00b7  serious text cleaner'
TITLE_FONT = ('Georgia', 20, 'bold', 'italic')
TEXT_FONT = ('Georgia', 12)

# Ember theme: warm charcoal + burnt orange (distinct from Piper blue / Kokoro purple)
DARK = dict(
    bg='#140f0d', surface='#1f1714', surface2='#2b201b', border='#4a382f',
    fg='#f5ebe5', muted='#b8a396', accent='#ff7a1a', accent_hover='#ffa04d',
    accent_fg='#1a0e06', text_bg='#0f0b09', select='#7a3b14', hl='#5a3a1a',
    ok='#8fd18f', err='#ff6b6b', warn='#ffb347',
)
LIGHT = dict(
    bg='#fdf7f2', surface='#ffffff', surface2='#f5e8de', border='#e0cbbd',
    fg='#2a1a12', muted='#7a6357', accent='#c2410c', accent_hover='#ea580c',
    accent_fg='#ffffff', text_bg='#fffcf9', select='#fbd5b5', hl='#ffe0b2',
    ok='#15803d', err='#be123c', warn='#b45309',
)

import bisect
import importlib.util
import json
import math
import os
import queue
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import traceback
import unicodedata
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, font as tkfont

ROOT = Path(__file__).resolve().parent
CACHE = Path.home() / '.cache' / 'chatterbox_onnx'
VOICES = ROOT / 'voices'
SETTINGS_FILE = ROOT / f'{APP_SLUG}_settings.json'
SESSION_FILE = ROOT / f'{APP_SLUG}_session.json'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)

VOICES.mkdir(exist_ok=True)


def find_ffmpeg():
    local = ROOT / 'ffmpeg.exe'
    if local.exists():
        return str(local)
    return shutil.which('ffmpeg')


def fmt_time(sec):
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f'{h}:{m:02d}:{s:02d}' if h else f'{m:02d}:{s:02d}'


# ============================================================
#  TEXT CLEANER  (same proven cleaner as Kokoro Studio)
# ============================================================
LIGATURES = {'\ufb00': 'ff', '\ufb01': 'fi', '\ufb02': 'fl', '\ufb03': 'ffi',
             '\ufb04': 'ffl', '\ufb05': 'ft', '\ufb06': 'st'}

SMART = {
    '\u2018': "'", '\u2019': "'", '\u201a': "'", '\u201b': "'",
    '\u201c': '"', '\u201d': '"', '\u201e': '"', '\u201f': '"',
    '\u2032': "'", '\u2033': '"',
    '\u2013': '-', '\u2014': ' -- ', '\u2015': ' -- ', '\u2026': '...',
    '\u203c': '!!', '\u2049': '?!',
    '\u00a0': ' ', '\u2002': ' ', '\u2003': ' ', '\u2009': ' ', '\u200a': ' ',
    '\u202f': ' ', '\u3000': ' ',
    '\u200b': '', '\u200c': '', '\u2060': '', '\ufeff': '', '\u00ad': '',
    '\u00b0': ' degrees ',
    '\u2022': '. ', '\u25cf': '. ', '\u25aa': '. ', '\u00b7': ' ',
}

JUNK_RANGES = (
    (0x1F000, 0x1FAFF), (0x2190, 0x21FF), (0x2300, 0x23FF),
    (0x2600, 0x27BF), (0x2900, 0x297F), (0x2B00, 0x2BFF),
    (0xFE00, 0xFE0F), (0xE0000, 0xE01EF),
)
JUNK_SINGLE = {0x200D, 0x20E3, 0x00A9, 0x00AE, 0x2122, 0x2139, 0x24C2,
               0x3030, 0x303D, 0x3297, 0x3299}
JUNK_CATEGORIES = {'So', 'Sk', 'Cs', 'Co', 'Cn', 'Cc', 'Cf', 'Me'}


def is_junk(ch):
    if ch in '\n\t ':
        return False
    o = ord(ch)
    if o in JUNK_SINGLE:
        return True
    for lo, hi in JUNK_RANGES:
        if lo <= o <= hi:
            return True
    return unicodedata.category(ch) in JUNK_CATEGORIES


_HR = re.compile(r'^\s*(?:[-*_=~]\s*){3,}$')
_HEAD = re.compile(r'^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$')
_QUOTE = re.compile(r'^\s*(?:>\s?)+')
_BULLET = re.compile(r'^\s*[-*+\u2022\u25cf\u25aa\u25e6\u2023\u2219\u25a0\u25ba]\s+(.*\S)\s*$')
_ENDERS = '.!?:;"\')\u201d\u2026'


def _end_sentence(s):
    s = s.strip()
    if s and s[-1] not in _ENDERS:
        s += '.'
    return s


def _strip_leading_junk(s):
    i = 0
    while i < len(s) and (is_junk(s[i]) or s[i].isspace()):
        i += 1
    return s[i:]


def _structure(text):
    out = []
    for line in text.split('\n'):
        s = line.strip()
        if not s:
            out.append('')
            continue
        if _HR.match(s):
            out.append('')
            continue
        m = _HEAD.match(line)
        if m:
            out += ['', _end_sentence(_strip_leading_junk(m.group(1))), '']
            continue
        line = _QUOTE.sub('', line)
        s = line.strip()
        m = _BULLET.match(line)
        if m:
            out.append(_end_sentence(_strip_leading_junk(m.group(1))))
            continue
        if s and is_junk(s[0]):
            item = _strip_leading_junk(s)
            out.append(_end_sentence(item) if item else '')
            continue
        out.append(line)
    return '\n'.join(out)


CLEAN_DEFAULTS = dict(
    furniture=True, markdown=True, urls=True, citations=True, caps=True,
    abbrev=True, ampersand=True, reflow=True, paragraphs=True, finish=True,
)
CLEAN_LABELS = [
    ('furniture', 'Remove page numbers ("page 3/15", bare numbers) and repeating headers/footers'),
    ('markdown', 'Strip markdown: # headings, **bold**, bullets, links, HTML tags'),
    ('urls', 'Remove web links'),
    ('citations', 'Remove citation marks like [12]'),
    ('caps', 'Fix ALL CAPS lines to Title Case, Chapter XII to Chapter 12'),
    ('abbrev', 'Spell out e.g.  i.e.  etc.  vs.'),
    ('ampersand', 'Read & as "and"'),
    ('reflow', 'Join broken lines into flowing paragraphs (and fix hyphenated line ends)'),
    ('paragraphs', 'Detect paragraph and heading breaks inside wrapped text'),
    ('finish', 'End headings and paragraphs with a full stop so the voice pauses'),
]

_PAGE_LINE = re.compile(r'^\s*(?:page|pg\.?|p\.)\s*\d+(?:\s*(?:/|of)\s*\d+)?\s*$', re.I)
_FRAC_LINE = re.compile(r'^\s*\d{1,4}\s*(?:/|of)\s*\d{1,4}\s*$', re.I)
_NUM_LINE = re.compile(r'^\s*[-\u2013\u2014\[(]?\s*\d{1,3}\s*[-\u2013\u2014\])]?\s*$')
_ROMAN_VALID = re.compile(r'^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$')
_ROMAN_LOWER = {'i', 'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii', 'ix', 'x', 'xi', 'xii',
                'xiii', 'xiv', 'xv', 'xvi', 'xvii', 'xviii', 'xix', 'xx'}
_ROMAN_VALS = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
_CHAPTER_WORDS = r'(?:chapter|part|book|volume|section|act|appendix)'


def roman_to_int(s):
    if not s or not _ROMAN_VALID.match(s):
        return None
    total = 0
    for i, ch in enumerate(s):
        v = _ROMAN_VALS[ch]
        total += -v if i + 1 < len(s) and _ROMAN_VALS[s[i + 1]] > v else v
    return total


def _strip_furniture(text, rep):
    removed = 0
    text, n = re.subn(r'(?i)\b(?:this is )?page\s+\d+\s*(?:/|of)\s*\d+\b[.,]?', '', text)
    removed += n
    lines = text.split('\n')
    keep = []
    for ln in lines:
        s = ln.strip()
        if s and (_PAGE_LINE.match(s) or _FRAC_LINE.match(s) or _NUM_LINE.match(s)
                  or (s.isupper() and len(s) <= 6 and roman_to_int(s) is not None)
                  or (len(s) >= 2 and s in _ROMAN_LOWER)):
            removed += 1
            continue
        keep.append(ln)
    lines = keep
    nonblank = [l.strip() for l in lines if l.strip()]
    if len(nonblank) >= 40:
        norm = lambda s: re.sub(r'\d+', '0', s.lower())
        counts = {}
        for s in nonblank:
            if 3 <= len(s) <= 80:
                counts[norm(s)] = counts.get(norm(s), 0) + 1
        drop = set()
        for s in nonblank:
            k = norm(s)
            if (counts.get(k, 0) >= 3 and 3 <= len(s) <= 80
                    and s[-1] not in '.!?,:;"\'\u201d)'
                    and (len(s.split()) >= 2 or s.isupper())):
                drop.add(k)
        if drop:
            kept = []
            for l in lines:
                if l.strip() and norm(l.strip()) in drop:
                    removed += 1
                else:
                    kept.append(l)
            lines = kept
    rep['furniture'] = rep.get('furniture', 0) + removed
    return '\n'.join(lines)


def _titlecase_line(s):
    out, prev = [], ''
    for m in re.finditer(r"[A-Za-z]+(?:'[A-Za-z]+)?|[^A-Za-z]+", s):
        tok = m.group(0)
        if tok[0].isalpha():
            if _ROMAN_VALID.match(tok) and re.fullmatch(_CHAPTER_WORDS, prev, re.I):
                out.append(tok)
            else:
                out.append(tok.capitalize())
            prev = tok
        else:
            out.append(tok)
    return ''.join(out)


def _fix_caps(text):
    lines = []
    for ln in text.split('\n'):
        letters = [c for c in ln if c.isalpha()]
        words = re.findall(r"[A-Za-z']+", ln)
        if (letters and ln.upper() == ln and ln.lower() != ln
                and (len(words) >= 2 or len(letters) >= 6)):
            ln = _titlecase_line(ln)
        lines.append(ln)
    text = '\n'.join(lines)

    def roman(m):
        n = roman_to_int(m.group(2))
        return f'{m.group(1)} {n}' if n else m.group(0)
    return re.sub(rf'(?i:\b({_CHAPTER_WORDS}))\s+([IVXLCDM]{{1,8}})\b', roman, text)


def _is_break(prev, nxt, med):
    if len(prev) >= 0.7 * med or not nxt:
        return False
    last = prev[-1]
    if last in ',;:-\u2013':
        return False
    if last in '.!?"\'\u201d)\u2026':
        return nxt[0].isupper() or nxt[0] in '"\'(['
    return len(prev) <= 70 and (nxt[0].isupper() or nxt[0].isdigit())


def _join_lines(lines):
    joined = ''
    for ln in lines:
        if re.search(r'[a-z]-$', joined) and ln[:1].islower():
            joined = joined[:-1] + ln
        else:
            joined = (joined + ' ' + ln).strip()
    return re.sub(r' {2,}', ' ', joined).strip()


def _paragraphs(text, o, finish):
    blocks = re.split(r'\n\s*\n', text)
    lens = [len(l.strip()) for b in blocks for l in b.split('\n') if len(l.strip()) > 25]
    med = statistics.median(lens) if len(lens) >= 6 else 0
    unwrapped = med > 115
    paras = []
    for b in blocks:
        lines = [l.strip() for l in b.split('\n') if l.strip()]
        if not lines:
            continue
        if not o['reflow']:
            paras.append('\n'.join(lines))
            continue
        cur, groups = [], []
        for ln in lines:
            if cur and o['paragraphs'] and (unwrapped or (med and _is_break(cur[-1], ln, med))):
                groups.append(cur)
                cur = []
            cur.append(ln)
        groups.append(cur)
        paras += [_join_lines(g) for g in groups]
    if finish:
        paras = [p + '.' if p and p[-1].isalnum() else p for p in paras]
    return paras


def apply_replacements(text, spec):
    for raw in (spec or '').split('\n'):
        raw = raw.strip()
        if not raw or raw.startswith('#') or '=>' not in raw:
            continue
        find, rep = (x.strip() for x in raw.split('=>', 1))
        if not find:
            continue
        pat = re.escape(find)
        if find[0].isalnum() and find[-1].isalnum():
            pat = rf'\b{pat}\b'
        text = re.sub(pat, lambda m, r=rep: r, text, flags=re.I)
    return text


def clean_text(text, opts=None, finish=True, report=None, repl=None):
    o = dict(CLEAN_DEFAULTS)
    o.update(opts or {})
    rep = report if report is not None else {}
    if not text:
        return ''
    text = unicodedata.normalize('NFC', text)
    text = text.replace('\r\n', '\n').replace('\r', '\n').replace('\t', ' ').replace('\f', '\n\n')
    for k, v in LIGATURES.items():
        text = text.replace(k, v)

    if o['furniture']:
        text = _strip_furniture(text, rep)
    if o['markdown']:
        text = _structure(text)
        text = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', text)
        text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
        text = re.sub(r'</?[A-Za-z][^>]{0,60}>', ' ', text)
        text = re.sub(r'\*+|~~', '', text)
        text = re.sub(r'(?<![A-Za-z0-9])_+|_+(?![A-Za-z0-9])', '', text)
        text = re.sub(r'[|~]', ' ', text)
    if o['urls']:
        text, n = re.subn(r'(?:https?://|www\.)\S+', '', text)
        rep['urls'] = rep.get('urls', 0) + n
    if o['citations']:
        text = re.sub(r'\u3010[^\u3011]*\u3011', '', text)
        text = re.sub(r'\[\d{1,3}\]', '', text)

    for k, v in SMART.items():
        text = text.replace(k, v)
    if o['ampersand']:
        text = re.sub(r'\s&\s', ' and ', text)
    if o['abbrev']:
        text = re.sub(r'(?i)\be\.g\.,?', 'for example,', text)
        text = re.sub(r'(?i)\bi\.e\.,?', 'that is,', text)
        text = re.sub(r'\b(?i:etc)\.(?=\s+[A-Z"\'(]|\s*$)', 'et cetera.', text)
        text = re.sub(r'(?i)\betc\.', 'et cetera', text)
        text = re.sub(r'(?i)\bvs\.?(?=\s)', 'versus', text)

    junk = 0
    chars = []
    for ch in text:
        if is_junk(ch):
            junk += 1
            chars.append(' ')
        else:
            chars.append(ch)
    text = ''.join(chars)
    rep['symbols'] = rep.get('symbols', 0) + junk

    text = '\n'.join(re.sub(r'[ ]{2,}', ' ', l).strip() for l in text.split('\n'))

    text = re.sub(r'[ ]+([,.;:!?])', r'\1', text)
    text = re.sub(r'([,;:!?])(?=[A-Za-z])', r'\1 ', text)
    text = re.sub(r'(?<=[a-z]{2})\.(?=[A-Z][a-z])', '. ', text)
    text = re.sub(r'([.!?,])\1{3,}', r'\1\1\1', text)
    text = re.sub(r'([.!?])(?:\s+\.)+(?=\s|$)', r'\1', text)
    text = re.sub(r'\s*--\s*', ' -- ', text)
    text = re.sub(r'(?m)^ -- ', '-- ', text)

    if o['caps']:
        text = _fix_caps(text)

    paras = _paragraphs(text, o, finish and o['finish'])
    text = '\n\n'.join(paras).strip()
    if repl:
        text = apply_replacements(text, repl)
    return text


def report_text(rep):
    bits = []
    if rep.get('symbols'):
        bits.append(f"{rep['symbols']} emoji/symbols")
    if rep.get('furniture'):
        bits.append(f"{rep['furniture']} page-number/header lines")
    if rep.get('urls'):
        bits.append(f"{rep['urls']} links")
    return 'Cleaned.' + (' Removed ' + ', '.join(bits) + '.' if bits else '')


def estimate_seconds(text, wpm=150):
    return len(re.findall(r'\S+', text)) / wpm * 60


def start_slug(text, max_words=7, max_len=40):
    out = ''
    for w in re.findall(r"[^\W_]+", text[:300])[:max_words]:
        cand = f'{out}_{w}' if out else w
        if len(cand) > max_len:
            if not out:
                out = w[:max_len]
            break
        out = cand
    return out or 'untitled'


def next_number(folder):
    top = 0
    try:
        for p in Path(folder).iterdir():
            m = re.match(r'(\d+)\.', p.name)
            if m:
                top = max(top, int(m.group(1)))
    except OSError:
        pass
    return top + 1


# ============================================================
#  SENTENCE CHUNKER  (Chatterbox needs short chunks: 250-280 chars)
# ============================================================
ABBREV = {'mr', 'mrs', 'ms', 'dr', 'prof', 'sr', 'jr', 'st', 'vs', 'etc', 'inc', 'ltd', 'co',
          'no', 'fig', 'vol', 'approx', 'dept', 'est', 'mt', 'gen', 'col', 'sgt', 'capt',
          'lt', 'rev', 'hon', 'e.g', 'i.e'}


def split_sentences(par):
    spans, start = [], 0
    for m in re.finditer(r'[.!?\u2026]+["\')\]\u201d]*(?=\s)', par):
        end = m.end()
        nxt = par[end:].lstrip()[:1]
        if not nxt or not (nxt.isupper() or nxt.isdigit() or nxt in '"\'([\u201c'):
            continue
        tok = re.findall(r'[A-Za-z.]+$', par[:m.start()])
        if tok and '?' not in m.group(0) and '!' not in m.group(0):
            t = tok[0].strip('.').lower()
            if t in ABBREV or (len(t) == 1 and tok[0][0].isupper()):
                continue
        spans.append((start, end))
        start = end
    if start < len(par.rstrip()):
        spans.append((start, len(par.rstrip())))
    return [(a, b) for a, b in spans if par[a:b].strip()]


def _split_long(text, a, b, max_chars):
    out = []
    while b - a > max_chars:
        window = text[a:a + max_chars]
        cut = max(window.rfind(', '), window.rfind('; '), window.rfind(': '))
        if cut < max_chars * 0.4:
            cut = window.rfind(' ')
        if cut < max_chars * 0.2:
            cut = max_chars - 1
        out.append((a, a + cut + 1))
        a += cut + 1
    if b > a:
        out.append((a, b))
    return out


@dataclass
class Chunk:
    a: int
    b: int
    text: str
    para_end: bool


def chunk_text(text, max_chars=280):
    chunks = []
    for pm in re.finditer(r'(?:[^\n]|\n(?!\s*\n))+', text):
        raw = pm.group(0)
        lead = len(raw) - len(raw.lstrip())
        para = raw.strip()
        if not para:
            continue
        base = pm.start() + lead
        atoms = []
        for a, b in split_sentences(para):
            if b - a > max_chars:
                atoms += _split_long(text, base + a, base + b, max_chars)
            else:
                atoms.append((base + a, base + b))
        cur_a = cur_b = None
        para_chunks = []
        for a, b in atoms:
            if cur_a is None:
                cur_a, cur_b = a, b
            elif b - cur_a > max_chars:
                para_chunks.append((cur_a, cur_b))
                cur_a, cur_b = a, b
            else:
                cur_b = b
        if cur_a is not None:
            para_chunks.append((cur_a, cur_b))
        for i, (a, b) in enumerate(para_chunks):
            t = re.sub(r'\s+', ' ', text[a:b]).strip()
            if t:
                chunks.append(Chunk(a, b, t, i == len(para_chunks) - 1))
    return chunks


# ============================================================
#  PART SPLITTER
# ============================================================
CHAPTER_RE = re.compile(r'^\s*(?:chapter|part|book|section|prologue|epilogue|introduction|'
                        r'preface|appendix)\b.{0,70}$', re.I)


def _wc(s):
    return len(s.split())


def _pack(paras, max_words):
    pieces = []
    for p in paras:
        if _wc(p) <= max_words:
            pieces.append(p)
            continue
        buf, n = [], 0
        for a, b in split_sentences(p):
            s = p[a:b].strip()
            if buf and n + _wc(s) > max_words:
                pieces.append(' '.join(buf))
                buf, n = [], 0
            buf.append(s)
            n += _wc(s)
        if buf:
            pieces.append(' '.join(buf))
    total = sum(_wc(p) for p in pieces)
    k = max(1, math.ceil(total / max_words))
    target = total / k
    out, buf, n = [], [], 0
    for p in pieces:
        w = _wc(p)
        if buf and n + w / 2 > target and len(out) < k - 1:
            out.append(buf)
            buf, n = [], 0
        buf.append(p)
        n += w
    if buf:
        out.append(buf)
    return ['\n\n'.join(g) for g in out]


def split_parts(text, max_words=1200, by_chapter=False):
    paras = [p.strip() for p in re.split(r'\n\s*\n', text.strip()) if p.strip()]
    if not paras:
        return []
    sections = [paras]
    if by_chapter:
        sections, cur = [], []
        for p in paras:
            if CHAPTER_RE.match(p) and cur:
                sections.append(cur)
                cur = []
            cur.append(p)
        if cur:
            sections.append(cur)
    out = []
    for sec in sections:
        out += _pack(sec, max_words)
    return out


# ============================================================
#  AUDIO FILE HELPERS
# ============================================================
def load_pcm_file(path, ffmpeg=None):
    path = str(path)
    if path.lower().endswith('.wav'):
        try:
            with wave.open(path, 'rb') as w:
                if w.getsampwidth() == 2 and w.getnchannels() == 1:
                    return w.readframes(w.getnframes()), w.getframerate()
        except Exception:
            pass
    try:
        import soundfile as sf
        data, sr = sf.read(path, dtype='int16', always_2d=True)
        if data.shape[1] > 1:
            data = data.mean(axis=1).astype('int16')
        else:
            data = data[:, 0]
        return data.tobytes(), int(sr)
    except Exception as e:
        err = e
    if ffmpeg:
        sr = 24000
        p = subprocess.run([ffmpeg, '-v', 'error', '-i', path, '-f', 's16le', '-ac', '1',
                            '-ar', str(sr), '-'], capture_output=True, creationflags=NO_WINDOW)
        if p.returncode == 0 and p.stdout:
            return p.stdout, sr
    raise RuntimeError(f'Could not decode {Path(path).name}: {err}')


def write_wav(path, pcm, sr):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)


# ============================================================
#  PLAYER  (pygame; seek/pause/skip work for mp3 and wav)
# ============================================================
class Player:
    def __init__(self):
        self.pg = None
        self.pcm = b''
        self.sr = 24000
        self.duration = 0.0
        self.state = 'stopped'
        self.base = 0.0
        self._acc = 0.0
        self._t0 = 0.0
        self.volume = 0.9
        self.sound = None
        self.path = None

    def _init_mixer(self, sr):
        if self.pg is None:
            os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
            import pygame
            self.pg = pygame
        m = self.pg.mixer.get_init()
        if not m or m[0] != sr or m[2] != 1:
            self.pg.mixer.quit()
            self.pg.mixer.init(frequency=sr, size=-16, channels=1, buffer=2048,
                               allowedchanges=0)

    @property
    def loaded(self):
        return bool(self.pcm)

    def load(self, pcm, sr, path):
        self.stop()
        self._init_mixer(sr)
        self.pcm, self.sr, self.path = pcm, sr, str(path)
        self.duration = len(pcm) / 2 / sr
        self.base = 0.0

    def _start(self, pos):
        pos = max(0.0, min(pos, max(0.0, self.duration - 0.05)))
        off = int(pos * self.sr) * 2
        snd = self.pg.mixer.Sound(buffer=memoryview(self.pcm)[off:])
        ch = self.pg.mixer.Channel(0)
        ch.set_volume(self.volume)
        ch.play(snd)
        self.sound = snd
        self.base, self._acc, self._t0 = pos, 0.0, time.monotonic()
        self.state = 'playing'

    def play(self, pos=None):
        if self.loaded:
            self._start(self.base if pos is None else pos)

    def pause(self):
        if self.state == 'playing':
            self.pg.mixer.Channel(0).pause()
            self._acc += time.monotonic() - self._t0
            self.state = 'paused'

    def resume(self):
        if self.state == 'paused':
            self.pg.mixer.Channel(0).unpause()
            self._t0 = time.monotonic()
            self.state = 'playing'

    def toggle(self):
        if self.state == 'playing':
            self.pause()
        elif self.state == 'paused':
            self.resume()
        else:
            self.play()

    def position(self):
        if self.state == 'playing':
            p = self.base + self._acc + (time.monotonic() - self._t0)
        else:
            p = self.base + self._acc
        return max(0.0, min(p, self.duration))

    def seek(self, pos):
        if not self.loaded:
            return
        pos = max(0.0, min(pos, self.duration))
        if self.state in ('playing', 'paused'):
            self._start(pos)
            if self.state == 'paused':
                self.pause()
        else:
            self.base, self._acc = pos, 0.0

    def skip(self, delta):
        self.seek(self.position() + delta)

    def stop(self):
        if self.pg is not None and self.pg.mixer.get_init():
            try:
                self.pg.mixer.Channel(0).stop()
            except Exception:
                pass
        self.state = 'stopped'
        self.base, self._acc = 0.0, 0.0

    def set_volume(self, v):
        self.volume = max(0.0, min(1.0, v))
        if self.pg is not None and self.pg.mixer.get_init():
            try:
                self.pg.mixer.Channel(0).set_volume(self.volume)
            except Exception:
                pass

    def poll_ended(self):
        if self.state == 'playing' and not self.pg.mixer.Channel(0).get_busy():
            self.state = 'stopped'
            self.base, self._acc = 0.0, 0.0
            return True
        return False


# ============================================================
#  THEME
# ============================================================
def system_prefers_dark():
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize')
        return winreg.QueryValueEx(key, 'AppsUseLightTheme')[0] == 0
    except Exception:
        return True


# ============================================================
#  MODELS
# ============================================================
class Cancelled(Exception):
    pass


class Part:
    _n = 1

    def __init__(self, text=''):
        self.uid = f'p{Part._n}'
        Part._n += 1
        self.text = text
        self.state = 'idle'
        self.audio = None
        self.number = None
        self.error = ''
        self.progress = 0.0
        self.eta = ''
        self.map = []
        self.starts = []
        self.map_text = None
        self.duration = 0.0
        self.job = None

    @property
    def map_valid(self):
        return bool(self.map) and self.map_text == self.text


class Job:
    def __init__(self, part, text, voice, exaggeration, max_tokens, mp3, watermark):
        self.part, self.text, self.voice = part, text, voice
        self.exaggeration = exaggeration
        self.max_tokens = max_tokens
        self.mp3 = mp3
        self.watermark = watermark
        self.slug = start_slug(text)
        self.cancel = threading.Event()


# ============================================================
#  CHATTERBOX BACKEND
# ============================================================
class ChatterboxBackend:
    name = 'Chatterbox'
    default_voice = 'default'
    max_chars = 280
    gap_sentence = 0.18
    gap_para = 0.55

    def __init__(self, app):
        self.app = app
        self.ready = threading.Event()
        self.engine = None
        self.loading = False
        self.voice_paths = {}
        self.emb_cache = {}
        self._lock = threading.Lock()

    def short(self, voice):
        return re.sub(r'[^\w\-]', '', voice or 'voice')

    def start(self):
        if self.loading:
            return
        self.loading = True
        self.ready.clear()
        self.app.set_status('Loading Chatterbox models ...')
        threading.Thread(target=self._load, daemon=True).start()

    def scan_voices(self):
        self.voice_paths = {}
        default = CACHE / 'default_voice.wav'
        if default.is_file():
            self.voice_paths['default'] = default
        for wav in sorted(VOICES.glob('*.wav')):
            self.voice_paths[wav.stem] = wav

    def _load(self):
        app = self.app
        try:
            if not (CACHE / 'onnx' / 'speech_encoder.onnx').is_file():
                raise FileNotFoundError(
                    f'Models not found in {CACHE}.\nRun setup_chatterbox.bat first.')
            from chatterbox_onnx import ChatterboxOnnx
            self.engine = ChatterboxOnnx(quantized=True, cache_dir=str(CACHE))
            self.scan_voices()
            if not self.voice_paths:
                raise RuntimeError('No voices found: default_voice.wav missing in cache.')

            def ready():
                app.fill_voices()
                self.ready.set()
                app.status.set(f'Ready. {len(self.voice_paths)} voices available.')
            app.ui(ready)
        except Exception as e:
            err = str(e)
            app.ui(lambda: messagebox.showerror('Model error', err[-1500:]))
        finally:
            app.ui(lambda: setattr(self, 'loading', False))

    def get_embedding(self, voice_path):
        key = str(voice_path)
        with self._lock:
            if key in self.emb_cache:
                return self.emb_cache[key]
            emb = self.engine.embed_speaker(str(voice_path))
            self.emb_cache[key] = emb
            return emb

    def synth(self, text, voice, exaggeration, max_tokens):
        if voice not in self.voice_paths:
            raise RuntimeError(f'Voice "{voice}" is no longer available. Refresh voices.')
        emb = self.get_embedding(self.voice_paths[voice])
        cond_emb, prompt_token, ref_x_vector, prompt_feat = emb
        with self._lock:
            wav = self.engine._generate_waveform(
                text=text,
                cond_emb=cond_emb,
                prompt_token=prompt_token,
                ref_x_vector=ref_x_vector,
                prompt_feat=prompt_feat,
                max_new_tokens=int(max_tokens),
                exaggeration=float(exaggeration),
            )
        import numpy as np
        pcm = (np.clip(wav, -1.0, 1.0) * 32767).astype('<i2').tobytes()
        return pcm, 24000

    def voice_convert(self, source_path, target_voice, exaggeration, max_tokens,
                      out_path, watermark):
        if target_voice not in self.voice_paths:
            raise RuntimeError(f'Target voice "{target_voice}" not available.')
        self.engine.voice_convert(
            source_audio_path=str(source_path),
            target_voice_path=str(self.voice_paths[target_voice]),
            output_file_name=str(out_path),
            exaggeration=float(exaggeration),
            max_new_tokens=int(max_tokens),
            apply_watermark=bool(watermark),
        )
        return out_path


# ============================================================
#  APP
# ============================================================
class Studio(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry('1180x880')
        self.minsize(980, 720)

        self.cfg = self._load_cfg()
        self.ffmpeg = find_ffmpeg()
        self.out_dir = Path(self.cfg.get('output') or (ROOT / 'output'))
        self.parts = []
        self.sel = None
        self.player = Player()
        self.player_part = None
        self.jobs_q = queue.Queue()
        self.ui_q = queue.Queue()
        self.pending = 0
        self.active_job = None
        self._save_after = None
        self._dragging = False
        self._hl_idx = None
        self._loading_audio = False

        theme = self.cfg.get('theme', 'light')
        self.dark = theme == 'dark'
        self.mode = self.cfg.get('mode', 'parts')

        self._make_fonts()
        self.style = ttk.Style(self)
        self.style.theme_use('clam')
        self.backend = ChatterboxBackend(self)

        self.exaggeration = tk.DoubleVar(value=float(self.cfg.get('exaggeration', 0.5)))
        self.max_tokens = tk.IntVar(value=int(self.cfg.get('max_tokens', 512)))
        self.watermark = tk.BooleanVar(value=bool(self.cfg.get('watermark', False)))
        self.status = tk.StringVar(value='Starting ...')
        self.mp3 = tk.BooleanVar(value=bool(self.cfg.get('mp3', True)) and bool(self.ffmpeg))
        self.auto_clean = tk.BooleanVar(value=bool(self.cfg.get('auto_clean', True)))
        self.replace_old = tk.BooleanVar(value=bool(self.cfg.get('replace_old', True)))
        self.follow = tk.BooleanVar(value=bool(self.cfg.get('follow', True)))
        self.autonext = tk.BooleanVar(value=bool(self.cfg.get('autonext', False)))
        self.remember = tk.BooleanVar(value=bool(self.cfg.get('remember', True)))
        self.volume = tk.DoubleVar(value=float(self.cfg.get('volume', 0.9)))
        self.seek_var = tk.DoubleVar(value=0.0)
        self.menus = []
        self.voice_ids = []

        self._build_ui()
        self._apply_theme()
        self._load_session()
        self._apply_mode()
        self.player.set_volume(self.volume.get())

        self.protocol('WM_DELETE_WINDOW', self._on_close)
        self._bind_keys()
        threading.Thread(target=self._worker, daemon=True).start()
        self.after(80, self._pump)
        self.after(200, self._tick)
        self.backend.start()
        self.set_status('Paste text, then Generate (Ctrl+Enter).')

    # ---------- settings ----------
    @staticmethod
    def _load_cfg():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding='utf-8'))
        except Exception:
            return {}

    def _save_cfg(self):
        try:
            self.cfg.update(
                theme='dark' if self.dark else 'light', mode=self.mode,
                voice=self.voice_id(), exaggeration=round(self.exaggeration.get(), 2),
                max_tokens=int(self.max_tokens.get()), watermark=self.watermark.get(),
                mp3=self.mp3.get(), auto_clean=self.auto_clean.get(),
                replace_old=self.replace_old.get(), follow=self.follow.get(),
                autonext=self.autonext.get(), remember=self.remember.get(),
                volume=round(self.volume.get(), 2), output=str(self.out_dir))
            SETTINGS_FILE.write_text(json.dumps(self.cfg, indent=2), encoding='utf-8')
        except Exception:
            pass

    @property
    def clean_opts(self):
        o = dict(CLEAN_DEFAULTS)
        o.update(self.cfg.get('clean', {}))
        return o

    def _on_close(self):
        if self.pending and not messagebox.askyesno(
                'Still generating',
                f'{self.pending} job(s) still running or queued.\nQuit anyway?'):
            return
        self._sync_part_text()
        self._save_cfg()
        self._save_session(force=True)
        try:
            self.player.stop()
        except Exception:
            pass
        self.destroy()

    def report_callback_exception(self, exc, val, tb):
        messagebox.showerror(f'{APP_NAME} error',
                             ''.join(traceback.format_exception(exc, val, tb))[-1500:])

    # ---------- fonts ----------
    def _make_fonts(self):
        fam = 'Segoe UI'
        for n in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkHeadingFont'):
            try:
                tkfont.nametofont(n).configure(family=fam, size=10)
            except tk.TclError:
                pass
        self.f_ui = tkfont.Font(family=fam, size=10)
        self.f_bold = tkfont.Font(family=fam, size=10, weight='bold')
        self.f_small = tkfont.Font(family=fam, size=9)
        self.f_title = tkfont.Font(family=TITLE_FONT[0], size=TITLE_FONT[1],
                                   weight=TITLE_FONT[2], slant=TITLE_FONT[3])
        self.f_text = tkfont.Font(family=TEXT_FONT[0], size=TEXT_FONT[1])
        self.f_mono = tkfont.Font(family='Consolas', size=10)

    # ---------- UI ----------
    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        head = ttk.Frame(self, padding=(20, 12, 20, 9))
        head.grid(row=0, column=0, sticky='ew')
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text=APP_NAME, style='Title.TLabel').grid(row=0, column=0, sticky='w')
        ttk.Label(head, text=SUBTITLE, style='Muted.TLabel').grid(
            row=0, column=1, sticky='w', padx=14, pady=(8, 0))
        self.mode_btn = ttk.Button(head, command=self.toggle_mode, style='Ghost.TButton')
        self.mode_btn.grid(row=0, column=2, padx=(0, 8))
        self.theme_btn = ttk.Button(head, command=self._toggle_theme, style='Ghost.TButton')
        self.theme_btn.grid(row=0, column=3, padx=(0, 8))
        self.main_menu = tk.Menu(self, tearoff=0)
        self.menus.append(self.main_menu)
        self.main_menu.add_command(label='Open output folder', command=self._open_output)
        self.main_menu.add_command(label='Change output folder ...', command=self._change_output)
        self.main_menu.add_separator()
        self.main_menu.add_command(label='Add voice ...', command=self.add_voice)
        self.main_menu.add_command(label='Open voices folder', command=self.open_voices_folder)
        self.main_menu.add_command(label='Refresh voices', command=self.refresh_voices)
        self.main_menu.add_separator()
        self.main_menu.add_command(label='Voice conversion ...', command=self.open_voice_convert)
        self.main_menu.add_separator()
        self.main_menu.add_checkbutton(label='Remember parts between sessions',
                                       variable=self.remember, command=self._save_cfg)
        self.main_menu.add_command(label='Forget saved parts', command=self._forget_session)
        self.main_menu.add_separator()
        self.main_menu.add_command(label='Keyboard shortcuts', command=self._show_shortcuts)
        ttk.Menubutton(head, text='Menu', menu=self.main_menu, style='Ghost.TMenubutton').grid(
            row=0, column=4)

        self.accent_line = tk.Frame(self, height=3)
        self.accent_line.grid(row=1, column=0, sticky='ew')

        self.paned = tk.PanedWindow(self, orient='horizontal', sashwidth=8, bd=0,
                                    sashrelief='flat', opaqueresize=True)
        self.paned.grid(row=2, column=0, sticky='nsew', padx=16, pady=(12, 6))
        self.side = ttk.Frame(self.paned)
        self.editor = ttk.Frame(self.paned)
        self.paned.add(self.side, width=330, minsize=250, stretch='never')
        self.paned.add(self.editor, minsize=560, stretch='always')
        self._build_side()
        self._build_editor()
        self._build_player()

        bar = ttk.Frame(self, style='Status.TFrame', padding=(20, 7))
        bar.grid(row=4, column=0, sticky='ew')
        ttk.Label(bar, textvariable=self.status, style='Status.TLabel').pack(side='left')
        self.queue_lbl = ttk.Label(bar, style='StatusMuted.TLabel')
        self.queue_lbl.pack(side='right')

    def _build_side(self):
        s = self.side
        s.columnconfigure(0, weight=1)
        s.rowconfigure(1, weight=1)
        top = ttk.Frame(s)
        top.grid(row=0, column=0, sticky='ew', pady=(0, 6))
        top.columnconfigure(0, weight=1)
        ttk.Label(top, text='Parts', style='H.TLabel').grid(row=0, column=0, sticky='w')
        self.count_lbl = ttk.Label(top, style='Muted.TLabel')
        self.count_lbl.grid(row=0, column=1, sticky='e')

        box = ttk.Frame(s, style='Card.TFrame')
        box.grid(row=1, column=0, sticky='nsew')
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(box, columns=('n', 'name', 'status'), show='headings',
                                 selectmode='browse')
        self.tree.heading('n', text='#', anchor='w')
        self.tree.heading('name', text='Starts with', anchor='w')
        self.tree.heading('status', text='Status', anchor='w')
        self.tree.column('n', width=34, stretch=False, anchor='w')
        self.tree.column('name', width=150, anchor='w')
        self.tree.column('status', width=96, stretch=False, anchor='w')
        self.tree.grid(row=0, column=0, sticky='nsew', padx=(1, 0), pady=1)
        sb = ttk.Scrollbar(box, orient='vertical', command=self.tree.yview)
        sb.grid(row=0, column=1, sticky='ns', padx=(0, 1), pady=1)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind('<<TreeviewSelect>>', self._on_tree_select)
        self.tree.bind('<Double-1>', lambda e: self.listen_selected())

        btns = ttk.Frame(s)
        btns.grid(row=2, column=0, sticky='ew', pady=(8, 0))
        for c in range(2):
            btns.columnconfigure(c, weight=1, uniform='sb')
        ttk.Button(btns, text='Add part', command=self.add_part).grid(
            row=0, column=0, sticky='ew', padx=(0, 4), pady=(0, 6))
        ttk.Button(btns, text='Delete', command=self.delete_part).grid(
            row=0, column=1, sticky='ew', padx=(4, 0), pady=(0, 6))
        ttk.Button(btns, text='Move up', command=lambda: self.move_part(-1)).grid(
            row=1, column=0, sticky='ew', padx=(0, 4), pady=(0, 6))
        ttk.Button(btns, text='Move down', command=lambda: self.move_part(1)).grid(
            row=1, column=1, sticky='ew', padx=(4, 0), pady=(0, 6))
        ttk.Button(btns, text='Generate all', command=self.generate_all,
                   style='Accent.TButton').grid(row=2, column=0, sticky='ew', padx=(0, 4))
        ttk.Button(btns, text='Cancel all', command=self.cancel_all).grid(
            row=2, column=1, sticky='ew', padx=(4, 0))

    def _build_editor(self):
        e = self.editor
        e.columnconfigure(0, weight=1)
        e.rowconfigure(2, weight=1)

        card = ttk.Frame(e, style='Card.TFrame', padding=12)
        card.grid(row=0, column=0, sticky='ew', pady=(0, 8))
        card.columnconfigure(1, weight=1)
        ttk.Label(card, text='Voice', style='Card.TLabel').grid(row=0, column=0, sticky='w')
        self.voice_box = ttk.Combobox(card, state='readonly', style='Card.TCombobox',
                                      font=self.f_ui, height=16)
        self.voice_box.grid(row=0, column=1, sticky='ew', padx=(10, 8))
        self.voice_box.bind('<<ComboboxSelected>>', lambda e: (self.voice_box.selection_clear(),
                                                               self._save_cfg()))
        ttk.Button(card, text='\u21bb', width=3, style='Small.TButton',
                   command=self.refresh_voices).grid(row=0, column=2, padx=(0, 18))

        ttk.Label(card, text='Exaggeration', style='Card.TLabel').grid(row=0, column=3, sticky='w')
        ttk.Scale(card, from_=0.0, to=1.0, variable=self.exaggeration, length=150,
                  style='Card.Horizontal.TScale', command=self._on_exag).grid(
            row=0, column=4, padx=(10, 8))
        self.exag_lbl = ttk.Label(card, width=5, style='CardMuted.TLabel')
        self.exag_lbl.grid(row=0, column=5, padx=(0, 18))

        ttk.Label(card, text='Max tokens', style='Card.TLabel').grid(row=0, column=6)
        ttk.Spinbox(card, from_=128, to=2048, increment=64, textvariable=self.max_tokens,
                    width=6, style='Card.TSpinbox', font=self.f_ui).grid(
            row=0, column=7, padx=(8, 0))

        opts = ttk.Frame(card, style='CardInner.TFrame')
        opts.grid(row=1, column=0, columnspan=8, sticky='ew', pady=(10, 0))
        opts.columnconfigure(9, weight=1)
        cb = ttk.Checkbutton(opts, text='Save as MP3' if self.ffmpeg else 'WAV only (no ffmpeg)',
                             variable=self.mp3, style='Card.TCheckbutton')
        cb.grid(row=0, column=0, sticky='w')
        if not self.ffmpeg:
            cb.state(['disabled'])
        ttk.Checkbutton(opts, text='Auto-clean on paste', variable=self.auto_clean,
                        style='Card.TCheckbutton').grid(row=0, column=1, padx=(18, 0))
        ttk.Checkbutton(opts, text='Replace old audio when regenerating',
                        variable=self.replace_old, style='Card.TCheckbutton').grid(
            row=0, column=2, padx=(18, 0))
        ttk.Checkbutton(opts, text='Apply watermark (Perth)', variable=self.watermark,
                        style='Card.TCheckbutton').grid(row=0, column=3, padx=(18, 0))

        meta = ttk.Frame(e)
        meta.grid(row=1, column=0, sticky='ew', pady=(0, 4))
        meta.columnconfigure(0, weight=1)
        self.part_lbl = ttk.Label(meta, style='H.TLabel')
        self.part_lbl.grid(row=0, column=0, sticky='w')
        self.stats_lbl = ttk.Label(meta, style='Muted.TLabel')
        self.stats_lbl.grid(row=0, column=1, sticky='e')

        self.text_border = tk.Frame(e)
        self.text_border.grid(row=2, column=0, sticky='nsew')
        self.text_inner = tk.Frame(self.text_border)
        self.text_inner.pack(fill='both', expand=True, padx=1, pady=1)
        self.text_inner.columnconfigure(0, weight=1)
        self.text_inner.rowconfigure(0, weight=1)
        self.text = tk.Text(self.text_inner, wrap='word', font=self.f_text, undo=True,
                            maxundo=200, bd=0, highlightthickness=0, padx=18, pady=14,
                            spacing1=2, spacing2=5, spacing3=6)
        self.text.grid(row=0, column=0, sticky='nsew')
        tsb = ttk.Scrollbar(self.text_inner, orient='vertical', command=self.text.yview)
        tsb.grid(row=0, column=1, sticky='ns')
        self.text.configure(yscrollcommand=tsb.set)
        self.text.tag_configure('reading')
        self.text.bind('<<Paste>>', self._on_paste)
        self.text.bind('<<Modified>>', self._on_modified)
        self.text.bind('<Control-a>', self._select_all)
        self.text.bind('<Control-Button-1>', self._ctrl_click)
        self.text.bind('<Button-3>', self._context_menu)
        self.ctx = tk.Menu(self, tearoff=0)
        self.menus.append(self.ctx)
        for lab, ev in (('Cut', '<<Cut>>'), ('Copy', '<<Copy>>'), ('Paste', '<<Paste>>')):
            self.ctx.add_command(label=lab, command=lambda v=ev: self.text.event_generate(v))
        self.ctx.add_separator()
        self.ctx.add_command(label='Select all', command=self._select_all)

        act = ttk.Frame(e)
        act.grid(row=3, column=0, sticky='ew', pady=(8, 0))
        act.columnconfigure(5, weight=1)
        self.paste_menu = tk.Menu(self, tearoff=0)
        self.menus.append(self.paste_menu)
        pm = self.paste_menu
        pm.add_command(label='Paste clean at cursor    Ctrl+V', command=self.paste_cursor)
        pm.add_command(label='Replace everything with clipboard    Ctrl+Shift+V',
                       command=self.paste_replace)
        pm.add_command(label='Paste as a new part    Ctrl+N', command=self.paste_new_part)
        pm.add_command(label='Paste and split into parts ...', command=self.paste_split)
        pm.add_separator()
        pm.add_command(label='Load .txt file into this part ...', command=self.load_txt)
        pm.add_command(label='Load .txt file and split into parts ...', command=self.load_txt_split)
        ttk.Menubutton(act, text='Paste', menu=pm, width=6).grid(row=0, column=0, padx=(0, 6))
        self.clean_menu = tk.Menu(self, tearoff=0)
        self.menus.append(self.clean_menu)
        self.clean_menu.add_command(label='Clean this part now', command=self.clean_box)
        self.clean_menu.add_command(label='Cleaning options ...', command=self.open_clean_options)
        self.clean_menu.add_command(label='Pronunciation / replacements ...',
                                    command=self.open_replacements)
        ttk.Menubutton(act, text='Clean', menu=self.clean_menu, width=6).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(act, text='Split ...', command=self.split_current).grid(
            row=0, column=2, padx=(0, 6))
        ttk.Button(act, text='Clear', command=self._clear).grid(row=0, column=3, padx=(0, 6))
        ttk.Button(act, text='Tags', command=self._show_tags).grid(row=0, column=4, padx=(0, 6))
        ttk.Button(act, text='Listen', command=self.listen_selected).grid(
            row=0, column=6, padx=(0, 6))
        ttk.Button(act, text='Generate', command=self.generate_selected,
                   style='Accent.TButton').grid(row=0, column=7)

        pr = ttk.Frame(e)
        pr.grid(row=4, column=0, sticky='ew', pady=(8, 0))
        pr.columnconfigure(0, weight=1)
        self.pbar = ttk.Progressbar(pr, maximum=100, mode='determinate',
                                    style='Horizontal.TProgressbar')
        self.pbar.grid(row=0, column=0, sticky='ew', padx=(0, 10))
        self.prog_lbl = ttk.Label(pr, width=34, style='Muted.TLabel')
        self.prog_lbl.grid(row=0, column=1, padx=(0, 8))
        ttk.Button(pr, text='Cancel', command=self.cancel_selected).grid(row=0, column=2)

    def _build_player(self):
        pl = ttk.Frame(self, style='Card.TFrame', padding=(14, 10))
        pl.grid(row=3, column=0, sticky='ew', padx=16, pady=(4, 8))
        pl.columnconfigure(0, weight=1)
        top = ttk.Frame(pl, style='CardInner.TFrame')
        top.grid(row=0, column=0, sticky='ew')
        top.columnconfigure(0, weight=1)
        self.now_lbl = ttk.Label(top, text='Nothing loaded', style='Card.TLabel')
        self.now_lbl.grid(row=0, column=0, sticky='w')
        ttk.Checkbutton(top, text='Follow along', variable=self.follow,
                        style='Card.TCheckbutton', command=self._save_cfg).grid(row=0, column=1)
        ttk.Checkbutton(top, text='Auto-play next part', variable=self.autonext,
                        style='Card.TCheckbutton', command=self._save_cfg).grid(
            row=0, column=2, padx=(14, 18))
        ttk.Label(top, text='Volume', style='CardMuted.TLabel').grid(row=0, column=3)
        ttk.Scale(top, from_=0, to=1, variable=self.volume, length=100,
                  style='Card.Horizontal.TScale',
                  command=lambda v: self.player.set_volume(float(v))).grid(
            row=0, column=4, padx=(8, 0))

        row = ttk.Frame(pl, style='CardInner.TFrame')
        row.grid(row=1, column=0, sticky='ew', pady=(8, 0))
        row.columnconfigure(10, weight=1)
        col = 0
        for label, delta in (('-30s', -30), ('-10s', -10), ('-5s', -5)):
            ttk.Button(row, text=label, width=4, style='Small.TButton',
                       command=lambda d=delta: self.skip(d)).grid(row=0, column=col, padx=(0, 3))
            col += 1
        self.play_btn = ttk.Button(row, text='Play', width=6, command=self.toggle_play,
                                   style='Play.TButton')
        self.play_btn.grid(row=0, column=col, padx=(6, 3))
        col += 1
        ttk.Button(row, text='Stop', width=4, style='Small.TButton',
                   command=self.stop_play).grid(row=0, column=col, padx=(0, 6))
        col += 1
        for label, delta in (('+5s', 5), ('+10s', 10), ('+30s', 30)):
            ttk.Button(row, text=label, width=4, style='Small.TButton',
                       command=lambda d=delta: self.skip(d)).grid(row=0, column=col, padx=(3, 0))
            col += 1
        self.time_lbl = ttk.Label(row, text='00:00', style='CardMuted.TLabel', width=7)
        self.time_lbl.grid(row=0, column=col, padx=(14, 2))
        self.seek = ttk.Scale(row, from_=0, to=1, variable=self.seek_var,
                              style='Card.Horizontal.TScale')
        self.seek.grid(row=0, column=10, sticky='ew', padx=4)
        self.seek.bind('<ButtonPress-1>', self._seek_press)
        self.seek.bind('<B1-Motion>', self._seek_drag)
        self.seek.bind('<ButtonRelease-1>', self._seek_release)
        self.dur_lbl = ttk.Label(row, text='00:00', style='CardMuted.TLabel', width=7)
        self.dur_lbl.grid(row=0, column=11, padx=(2, 12))
        ttk.Button(row, text='Prev part', style='Small.TButton',
                   command=lambda: self.step_part(-1)).grid(row=0, column=12, padx=(0, 4))
        ttk.Button(row, text='Next part', style='Small.TButton',
                   command=lambda: self.step_part(1)).grid(row=0, column=13)

    # ---------- theming ----------
    def _apply_theme(self):
        P = self.P = DARK if self.dark else LIGHT
        s = self.style
        self.configure(bg=P['bg'])
        s.configure('.', background=P['bg'], foreground=P['fg'], font=self.f_ui,
                    bordercolor=P['border'], lightcolor=P['bg'], darkcolor=P['bg'],
                    troughcolor=P['surface2'], focuscolor=P['bg'], insertcolor=P['fg'])
        s.configure('TFrame', background=P['bg'])
        s.configure('Card.TFrame', background=P['surface'], bordercolor=P['border'],
                    lightcolor=P['border'], darkcolor=P['border'], relief='solid', borderwidth=1)
        s.configure('CardInner.TFrame', background=P['surface'])
        s.configure('Status.TFrame', background=P['surface2'])
        s.configure('TLabel', background=P['bg'], foreground=P['fg'])
        s.configure('H.TLabel', font=self.f_bold)
        s.configure('Title.TLabel', font=self.f_title, foreground=P['accent'])
        s.configure('Muted.TLabel', foreground=P['muted'], font=self.f_small)
        s.configure('Card.TLabel', background=P['surface'])
        s.configure('CardMuted.TLabel', background=P['surface'], foreground=P['muted'])
        s.configure('Status.TLabel', background=P['surface2'])
        s.configure('StatusMuted.TLabel', background=P['surface2'], foreground=P['muted'],
                    font=self.f_small)

        def button(name, bg, fg, hover, border, font=None, pad=(12, 6), cls='TButton'):
            s.configure(name, background=bg, foreground=fg, bordercolor=border,
                        lightcolor=bg, darkcolor=bg, focusthickness=0, relief='flat',
                        borderwidth=1, padding=pad, font=font or self.f_ui, arrowcolor=fg)
            s.map(name,
                  background=[('disabled', P['surface']), ('pressed', hover), ('active', hover)],
                  lightcolor=[('pressed', hover), ('active', hover)],
                  darkcolor=[('pressed', hover), ('active', hover)],
                  bordercolor=[('active', hover)],
                  foreground=[('disabled', P['muted'])])
        button('TButton', P['surface2'], P['fg'], P['border'], P['border'])
        button('Small.TButton', P['surface2'], P['fg'], P['border'], P['border'], pad=(4, 6))
        button('Play.TButton', P['accent'], P['accent_fg'], P['accent_hover'],
               P['accent'], font=self.f_bold, pad=(10, 6))
        button('Ghost.TButton', P['bg'], P['muted'], P['surface2'], P['border'])
        button('Accent.TButton', P['accent'], P['accent_fg'], P['accent_hover'],
               P['accent'], font=self.f_bold, pad=(22, 7))
        button('TMenubutton', P['surface2'], P['fg'], P['border'], P['border'], pad=(12, 6))
        button('Ghost.TMenubutton', P['bg'], P['muted'], P['surface2'], P['border'], pad=(12, 6))

        for name, bg in (('TCheckbutton', P['bg']), ('Card.TCheckbutton', P['surface'])):
            s.configure(name, background=bg, foreground=P['fg'],
                        indicatorbackground=P['surface2'], indicatorforeground=P['accent_fg'],
                        bordercolor=P['border'], lightcolor=P['surface2'],
                        darkcolor=P['surface2'], focuscolor=bg, padding=(2, 2))
            s.map(name, background=[('active', bg)],
                  indicatorbackground=[('selected', P['accent']), ('active', P['border'])],
                  foreground=[('disabled', P['muted'])])

        for name in ('Card.TCombobox', 'TCombobox'):
            s.configure(name, fieldbackground=P['surface2'], background=P['surface2'],
                        foreground=P['fg'], arrowcolor=P['fg'], bordercolor=P['border'],
                        lightcolor=P['surface2'], darkcolor=P['surface2'], padding=(8, 6),
                        selectbackground=P['surface2'], selectforeground=P['fg'], arrowsize=16)
            s.map(name, fieldbackground=[('readonly', P['surface2'])],
                  background=[('active', P['border']), ('readonly', P['surface2'])],
                  foreground=[('readonly', P['fg'])],
                  selectbackground=[('readonly', P['surface2'])],
                  selectforeground=[('readonly', P['fg'])],
                  bordercolor=[('focus', P['accent'])])
        s.configure('TSpinbox', fieldbackground=P['surface2'], background=P['surface2'],
                    foreground=P['fg'], arrowcolor=P['fg'], bordercolor=P['border'],
                    lightcolor=P['surface2'], darkcolor=P['surface2'], padding=(6, 5),
                    selectbackground=P['select'], selectforeground=P['fg'], arrowsize=14)
        s.configure('Card.TSpinbox', fieldbackground=P['surface2'], background=P['surface2'],
                    foreground=P['fg'], arrowcolor=P['fg'], bordercolor=P['border'],
                    lightcolor=P['surface2'], darkcolor=P['surface2'], padding=(6, 5),
                    selectbackground=P['select'], selectforeground=P['fg'], arrowsize=14)
        s.map('Card.TSpinbox', bordercolor=[('focus', P['accent'])])
        self.option_add('*TCombobox*Listbox.background', P['surface'])
        self.option_add('*TCombobox*Listbox.foreground', P['fg'])
        self.option_add('*TCombobox*Listbox.selectBackground', P['accent'])
        self.option_add('*TCombobox*Listbox.selectForeground', P['accent_fg'])
        self.option_add('*TCombobox*Listbox.font', self.f_ui)
        try:
            pop = self.tk.call('ttk::combobox::PopdownWindow', str(self.voice_box))
            self.tk.call(f'{pop}.f.l', 'configure', '-background', P['surface'],
                         '-foreground', P['fg'], '-selectbackground', P['accent'],
                         '-selectforeground', P['accent_fg'], '-font', self.f_ui.name,
                         '-borderwidth', 0, '-highlightthickness', 0)
        except tk.TclError:
            pass

        s.configure('Card.Horizontal.TScale', background=P['accent'], troughcolor=P['surface2'],
                    bordercolor=P['surface'], lightcolor=P['accent'], darkcolor=P['accent'])
        s.map('Card.Horizontal.TScale', background=[('active', P['accent_hover'])])
        s.configure('Horizontal.TProgressbar', background=P['accent'], troughcolor=P['surface2'],
                    bordercolor=P['border'], lightcolor=P['accent'], darkcolor=P['accent'],
                    thickness=10)
        for orient in ('Vertical', 'Horizontal'):
            n = f'{orient}.TScrollbar'
            s.configure(n, background=P['border'], troughcolor=P['surface'],
                        bordercolor=P['surface'], lightcolor=P['border'], darkcolor=P['border'],
                        arrowcolor=P['muted'], gripcount=0, arrowsize=16, relief='flat')
            s.map(n, background=[('pressed', P['accent']), ('active', P['muted'])],
                  lightcolor=[('pressed', P['accent']), ('active', P['muted'])],
                  darkcolor=[('pressed', P['accent']), ('active', P['muted'])])

        s.layout('Treeview', [('Treeview.treearea', {'sticky': 'nswe'})])
        s.configure('Treeview', background=P['surface'], fieldbackground=P['surface'],
                    foreground=P['fg'], rowheight=28, borderwidth=0, font=self.f_ui)
        s.configure('Treeview.Heading', background=P['surface2'], foreground=P['muted'],
                    relief='flat', font=self.f_small, padding=(8, 5))
        s.map('Treeview', background=[('selected', P['select'])],
              foreground=[('selected', P['fg'])])
        s.map('Treeview.Heading', background=[('active', P['border'])])
        self.tree.tag_configure('idle', foreground=P['fg'])
        self.tree.tag_configure('queued', foreground=P['muted'])
        self.tree.tag_configure('generating', foreground=P['accent'])
        self.tree.tag_configure('done', foreground=P['ok'])
        self.tree.tag_configure('error', foreground=P['err'])

        self.accent_line.configure(bg=P['accent'])
        self.paned.configure(bg=P['bg'])
        self.text_border.configure(bg=P['border'])
        self.text_inner.configure(bg=P['text_bg'])
        self.text.configure(bg=P['text_bg'], fg=P['fg'], insertbackground=P['accent'],
                            selectbackground=P['select'], selectforeground=P['fg'])
        self.text.tag_configure('reading', background=P['hl'], foreground=P['fg'])
        self.text.tag_raise('sel')
        for m in self.menus:
            m.configure(bg=P['surface'], fg=P['fg'], activebackground=P['accent'],
                        activeforeground=P['accent_fg'], bd=0, font=self.f_ui,
                        selectcolor=P['accent'])
        self.theme_btn.configure(text='Dark mode' if not self.dark else 'Light mode')
        self.exag_lbl.configure(text=f'{self.exaggeration.get():.2f}')

    def _toggle_theme(self):
        self.dark = not self.dark
        self._apply_theme()
        self._save_cfg()

    def toggle_mode(self):
        self._sync_part_text()
        self.mode = 'single' if self.mode == 'parts' else 'parts'
        self._apply_mode()
        self._save_cfg()

    def _apply_mode(self):
        shown = str(self.side) in self.paned.panes()
        if self.mode == 'parts' and not shown:
            self.paned.add(self.side, before=self.editor, width=330, minsize=250,
                           stretch='never')
        elif self.mode == 'single' and shown:
            self.paned.forget(self.side)
        self.mode_btn.configure(text='Switch to single view' if self.mode == 'parts'
                                else 'Switch to parts view')
        self._update_counts()

    # ---------- voices ----------
    def fill_voices(self):
        self.voice_ids = list(self.backend.voice_paths.keys())
        labels = []
        for name in self.voice_ids:
            desc = 'built-in reference' if name == 'default' else 'custom reference WAV'
            labels.append(f'{name}   -   {desc}')
        self.voice_box['values'] = labels
        cur = self.cfg.get('voice', self.backend.default_voice)
        idx = self.voice_ids.index(cur) if cur in self.voice_ids else 0
        if self.voice_ids:
            self.voice_box.current(idx)
        self.voice_box.selection_clear()

    def voice_id(self):
        i = self.voice_box.current()
        if 0 <= i < len(self.voice_ids):
            return self.voice_ids[i]
        return self.cfg.get('voice', self.backend.default_voice)

    def refresh_voices(self):
        self.backend.scan_voices()
        self.fill_voices()
        self.set_status(f'{len(self.backend.voice_paths)} voices available.')

    def add_voice(self):
        path = filedialog.askopenfilename(
            title='Choose reference voice WAV',
            filetypes=[('WAV files', '*.wav')])
        if not path:
            return
        name = Path(path).stem
        dest = VOICES / f'{name}.wav'
        if dest.exists():
            if not messagebox.askyesno('Exists', f'{dest.name} already exists. Overwrite?'):
                return
        shutil.copy2(path, dest)
        self.refresh_voices()
        idx = self.voice_ids.index(name) if name in self.voice_ids else 0
        self.voice_box.current(idx)

    def open_voices_folder(self):
        try:
            os.startfile(str(VOICES))
        except Exception as e:
            messagebox.showerror('Voices folder', str(e))

    def _on_exag(self, *_):
        v = round(self.exaggeration.get() * 20) / 20
        self.exaggeration.set(round(v, 2))
        self.exag_lbl.configure(text=f'{v:.2f}')

    # ---------- parts ----------
    def _new_part(self, text='', select=True, index=None):
        p = Part(text)
        if index is None:
            self.parts.append(p)
        else:
            self.parts.insert(index, p)
        self.tree.insert('', 'end' if index is None else index, iid=p.uid,
                         values=('', '', ''), tags=('idle',))
        self._renumber()
        self._refresh_row(p)
        if select:
            self.select_part(p)
        return p

    def add_part(self):
        self._sync_part_text()
        i = self.parts.index(self.sel) + 1 if self.sel in self.parts else None
        self._new_part('', index=i)
        self.text.focus_set()
        self._save_session_soon()

    def delete_part(self):
        p = self.sel
        if not p:
            return
        if p.state in ('queued', 'generating'):
            self.cancel_part(p)
        if p.text.strip() and len(self.parts) > 1 and not messagebox.askyesno(
                'Delete part', 'Delete this part? (the audio file stays in the output folder)'):
            return
        i = self.parts.index(p)
        self.parts.remove(p)
        self.tree.delete(p.uid)
        if self.player_part is p:
            self.player_part = None
        if not self.parts:
            self.sel = None
            self._new_part('')
        else:
            self.sel = None
            self.select_part(self.parts[min(i, len(self.parts) - 1)])
        self._renumber()
        self._save_session_soon()

    def move_part(self, d):
        p = self.sel
        if not p:
            return
        i = self.parts.index(p)
        j = i + d
        if not 0 <= j < len(self.parts):
            return
        self.parts[i], self.parts[j] = self.parts[j], self.parts[i]
        self.tree.move(p.uid, '', j)
        self._renumber()
        self._save_session_soon()

    def _renumber(self):
        for i, p in enumerate(self.parts):
            self.tree.set(p.uid, 'n', str(i + 1))
        self._update_counts()

    def _update_counts(self):
        n = len(self.parts)
        done = sum(1 for p in self.parts if p.state == 'done')
        self.count_lbl.configure(text=f'{done}/{n} generated')
        busy = self.pending
        self.queue_lbl.configure(text=f'{busy} in queue' if busy else '')
        self.mode_btn.configure(
            text=('Switch to single view' if self.mode == 'parts'
                  else f'Switch to parts view ({n})'))

    def _refresh_row(self, p):
        if not self.tree.exists(p.uid):
            return
        name = re.sub(r'\s+', ' ', p.text.strip())[:40] or '(empty)'
        st = {'idle': '', 'queued': 'queued', 'error': 'failed',
              'generating': f'{int(p.progress * 100)}%',
              'done': f'done  {fmt_time(p.duration)}' if p.duration else 'done'}[p.state]
        self.tree.set(p.uid, 'name', name)
        self.tree.set(p.uid, 'status', st)
        self.tree.item(p.uid, tags=(p.state,))

    def _on_tree_select(self, e=None):
        sel = self.tree.selection()
        if not sel:
            return
        part = next((p for p in self.parts if p.uid == sel[0]), None)
        if part and part is not self.sel:
            self.select_part(part)

    def select_part(self, part):
        if part is self.sel:
            return
        self._sync_part_text()
        self.sel = part
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', part.text)
        self.text.edit_reset()
        self.text.edit_modified(False)
        self._hl_idx = None
        self._clear_reading()
        if self.tree.exists(part.uid) and self.tree.selection() != (part.uid,):
            self.tree.selection_set(part.uid)
            self.tree.see(part.uid)
        self._update_stats()
        self._update_part_label()
        self._update_progress_ui()

    def _update_part_label(self):
        p = self.sel
        if not p:
            return
        i = self.parts.index(p) + 1
        self.part_lbl.configure(text=f'Part {i} of {len(self.parts)}')

    # ---------- editor text ----------
    def _sync_part_text(self):
        if self.sel is None:
            return
        self.sel.text = self.text.get('1.0', 'end-1c')

    def _on_modified(self, e=None):
        if self.text.edit_modified():
            self.text.edit_modified(False)
            self._sync_part_text()
            if self.sel:
                self._refresh_row(self.sel)
            self._update_stats()
            self._save_session_soon()

    def _set_editor_text(self, txt):
        self.text.edit_separator()
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', txt)
        self.text.edit_separator()
        self._sync_part_text()
        if self.sel:
            self._refresh_row(self.sel)
        self._update_stats()
        self._save_session_soon()

    def _clip(self):
        try:
            return self.clipboard_get()
        except tk.TclError:
            self.set_status('Clipboard is empty.')
            return ''

    def _clean_clip(self):
        raw = self._clip()
        if not raw:
            return ''
        if not self.auto_clean.get():
            return raw
        rep = {}
        txt = clean_text(raw, self.clean_opts, finish=False, report=rep,
                         repl=self.cfg.get('replacements', ''))
        self.set_status(report_text(rep))
        return txt

    def _on_paste(self, event=None):
        self.paste_cursor()
        return 'break'

    def paste_cursor(self):
        txt = self._clean_clip()
        if not txt:
            return
        self.text.edit_separator()
        try:
            self.text.delete('sel.first', 'sel.last')
        except tk.TclError:
            pass
        self.text.insert('insert', txt)
        self.text.edit_separator()
        self.text.see('insert')
        self._sync_part_text()
        if self.sel:
            self._refresh_row(self.sel)
        self._update_stats()
        self._save_session_soon()

    def paste_replace(self):
        txt = self._clean_clip()
        if txt:
            self._set_editor_text(txt)

    def paste_new_part(self):
        txt = self._clean_clip()
        if not txt:
            return
        self._sync_part_text()
        i = self.parts.index(self.sel) + 1 if self.sel in self.parts else None
        self._new_part(txt, index=i)
        self._save_session_soon()

    def paste_split(self):
        txt = self._clean_clip()
        if txt:
            self._open_split(txt, replace_current=False)

    def load_txt(self):
        txt = self._read_txt()
        if txt:
            self._set_editor_text(txt)

    def load_txt_split(self):
        txt = self._read_txt()
        if txt:
            self._open_split(txt, replace_current=False)

    def _read_txt(self):
        path = filedialog.askopenfilename(parent=self, title='Open text file',
                                          filetypes=[('Text', '*.txt *.md'), ('All', '*.*')])
        if not path:
            return ''
        raw = None
        for enc in ('utf-8-sig', 'utf-16', 'cp1252'):
            try:
                raw = Path(path).read_text(encoding=enc)
                break
            except Exception:
                continue
        if raw is None:
            messagebox.showerror('Open file', 'Could not read that file.')
            return ''
        rep = {}
        txt = clean_text(raw, self.clean_opts, finish=False, report=rep,
                         repl=self.cfg.get('replacements', ''))
        self.set_status(f'Loaded {Path(path).name}. ' + report_text(rep))
        return txt

    def _select_all(self, event=None):
        self.text.tag_add('sel', '1.0', 'end-1c')
        return 'break'

    def _context_menu(self, event):
        self.text.focus_set()
        self.ctx.tk_popup(event.x_root, event.y_root)

    def _update_stats(self):
        t = self.text.get('1.0', 'end-1c').strip()
        words = len(re.findall(r'\S+', t))
        secs = estimate_seconds(t)
        self.stats_lbl.configure(
            text=f'{len(t):,} chars   {words:,} words   about {fmt_time(secs)} of audio')

    def clean_box(self):
        raw = self.text.get('1.0', 'end-1c')
        if not raw.strip():
            return
        rep = {}
        txt = clean_text(raw, self.clean_opts, report=rep,
                         repl=self.cfg.get('replacements', ''))
        self._set_editor_text(txt)
        self.set_status(report_text(rep) + '  (Ctrl+Z undoes it)')

    def _clear(self):
        self._set_editor_text('')
        self.text.focus_set()

    # ---------- dialogs ----------
    def _dialog(self, title, width=560):
        top = tk.Toplevel(self)
        top.title(title)
        top.configure(bg=self.P['bg'])
        top.transient(self)
        top.resizable(False, False)
        body = ttk.Frame(top, padding=18)
        body.pack(fill='both', expand=True)
        self.update_idletasks()
        x = self.winfo_rootx() + max(20, (self.winfo_width() - width) // 2)
        top.geometry(f'+{x}+{self.winfo_rooty() + 90}')
        return top, body

    def open_clean_options(self):
        top, body = self._dialog('Cleaning options')
        ttk.Label(body, text='Emoji, symbols and pictographs are always removed.',
                  style='Muted.TLabel').pack(anchor='w', pady=(0, 10))
        vars_ = {}
        cur = self.clean_opts
        for key, label in CLEAN_LABELS:
            v = tk.BooleanVar(value=cur.get(key, True))
            vars_[key] = v
            ttk.Checkbutton(body, text=label, variable=v).pack(anchor='w', pady=2)

        def save():
            self.cfg['clean'] = {k: v.get() for k, v in vars_.items()}
            self._save_cfg()
            top.destroy()

        def reset():
            for k, v in vars_.items():
                v.set(CLEAN_DEFAULTS[k])
        row = ttk.Frame(body)
        row.pack(fill='x', pady=(14, 0))
        ttk.Button(row, text='Reset', command=reset).pack(side='left')
        ttk.Button(row, text='Save', command=save, style='Accent.TButton').pack(side='right')
        ttk.Button(row, text='Cancel', command=top.destroy).pack(side='right', padx=8)

    def open_replacements(self):
        top, body = self._dialog('Pronunciation / replacements', 520)
        ttk.Label(body, justify='left', style='Muted.TLabel', text=(
            'One per line:  word => how it should be said\n'
            'Examples:   Nairobi => Nye-robi      GPU => G P U      Mt. => Mount\n'
            'Whole words, any capitalisation. Applied every time text is cleaned.')).pack(
            anchor='w', pady=(0, 8))
        t = tk.Text(body, width=58, height=12, font=self.f_mono, bd=0, highlightthickness=1,
                    highlightbackground=self.P['border'], padx=10, pady=8, undo=True,
                    bg=self.P['text_bg'], fg=self.P['fg'], insertbackground=self.P['accent'],
                    selectbackground=self.P['select'])
        t.pack()
        t.insert('1.0', self.cfg.get('replacements', ''))

        def save():
            self.cfg['replacements'] = t.get('1.0', 'end-1c')
            self._save_cfg()
            top.destroy()
        row = ttk.Frame(body)
        row.pack(fill='x', pady=(12, 0))
        ttk.Button(row, text='Save', command=save, style='Accent.TButton').pack(side='right')
        ttk.Button(row, text='Cancel', command=top.destroy).pack(side='right', padx=8)

    def split_current(self):
        txt = self.text.get('1.0', 'end-1c').strip()
        if not txt:
            self.set_status('Nothing to split.')
            return
        self._open_split(clean_text(txt, self.clean_opts, finish=False,
                                    repl=self.cfg.get('replacements', '')), replace_current=True)

    def _open_split(self, text, replace_current):
        top, body = self._dialog('Split into parts', 520)
        total = len(text.split())
        ttk.Label(body, text=f'{total:,} words  (about {fmt_time(estimate_seconds(text))} of audio)',
                  style='H.TLabel').pack(anchor='w')
        row = ttk.Frame(body)
        row.pack(fill='x', pady=(12, 4))
        ttk.Label(row, text='Words per part').pack(side='left')
        words = tk.IntVar(value=int(self.cfg.get('split_words', 1200)))
        ttk.Spinbox(row, from_=100, to=20000, increment=100, textvariable=words,
                    width=7).pack(side='left', padx=10)
        chapters = tk.BooleanVar(value=bool(self.cfg.get('split_chapters', True)))
        ttk.Checkbutton(body, text='Start a new part at "Chapter / Part / Prologue ..." headings',
                        variable=chapters).pack(anchor='w', pady=4)
        gen_now = tk.BooleanVar(value=False)
        ttk.Checkbutton(body, text='Generate all parts right away',
                        variable=gen_now).pack(anchor='w', pady=4)
        info = ttk.Label(body, style='Muted.TLabel')
        info.pack(anchor='w', pady=(8, 0))

        def preview(*_):
            try:
                n = len(split_parts(text, max(50, words.get()), chapters.get()))
                info.configure(text=f'Will make {n} part{"s" if n != 1 else ""}.')
            except tk.TclError:
                pass
        words.trace_add('write', preview)
        chapters.trace_add('write', preview)
        preview()

        def go():
            try:
                w = max(50, int(words.get()))
            except tk.TclError:
                return
            pieces = split_parts(text, w, chapters.get())
            self.cfg['split_words'], self.cfg['split_chapters'] = w, chapters.get()
            top.destroy()
            self._apply_split(pieces, replace_current, gen_now.get())
        r2 = ttk.Frame(body)
        r2.pack(fill='x', pady=(14, 0))
        ttk.Button(r2, text='Split', command=go, style='Accent.TButton').pack(side='right')
        ttk.Button(r2, text='Cancel', command=top.destroy).pack(side='right', padx=8)

    def _apply_split(self, pieces, replace_current, gen_now):
        if not pieces:
            return
        self._sync_part_text()
        if self.mode == 'single':
            self.mode = 'parts'
            self._apply_mode()
        cur = self.sel
        created = []
        if cur is not None and (replace_current or not cur.text.strip()):
            self._set_editor_text(pieces[0])
            created.append(cur)
            rest = pieces[1:]
            at = self.parts.index(cur) + 1
        else:
            rest = pieces
            at = self.parts.index(cur) + 1 if cur in self.parts else len(self.parts)
        for k, txt in enumerate(rest):
            created.append(self._new_part(txt, select=False, index=at + k))
        self._renumber()
        self.set_status(f'Split into {len(pieces)} parts.')
        self._save_session_soon()
        if gen_now:
            for p in created:
                self.generate_part(p)

    def _show_tags(self):
        messagebox.showinfo('Punctuation, not tags', (
            'The standard English Chatterbox ONNX model does not support\n'
            'paralinguistic tags like [laugh] or [sigh] — those belong to\n'
            'the Turbo model.\n\n'
            'Punctuation controls pacing in this model:\n\n'
            '   ,        short pause\n'
            '   .        sentence pause\n'
            '   ...      longer pause\n'
            '   --       dramatic pause\n\n'
            'Use the Cleaner to turn emoji and decorative symbols into pauses.'))

    def _show_shortcuts(self):
        messagebox.showinfo('Keyboard shortcuts', (
            'Ctrl+Enter           Generate this part\n'
            'Ctrl+Shift+Enter     Generate all parts\n'
            'Ctrl+V               Paste clean at cursor\n'
            'Ctrl+Shift+V         Replace everything with clipboard\n'
            'Ctrl+N               Paste clipboard as a new part\n'
            'Ctrl+Space           Play / pause\n'
            'Alt+Left / Right     Back / forward 5 seconds\n'
            'Alt+Shift+Left/Right Back / forward 10 seconds\n'
            'Ctrl+click in text   Jump playback to that sentence\n'
            'Ctrl+Z               Undo (also undoes Clean)'))

    # ---------- keys ----------
    def _bind_keys(self):
        self.bind_all('<Control-Return>', lambda e: (self.generate_selected(), 'break')[1])
        self.bind_all('<Control-Shift-Return>', lambda e: (self.generate_all(), 'break')[1])
        self.bind_all('<Control-Shift-V>', lambda e: (self.paste_replace(), 'break')[1])
        self.bind_all('<Control-Shift-v>', lambda e: (self.paste_replace(), 'break')[1])
        self.bind_all('<Control-n>', lambda e: (self.paste_new_part(), 'break')[1])
        self.bind_all('<Control-space>', lambda e: (self.toggle_play(), 'break')[1])
        self.bind_all('<Alt-Left>', lambda e: (self.skip(-5), 'break')[1])
        self.bind_all('<Alt-Right>', lambda e: (self.skip(5), 'break')[1])
        self.bind_all('<Alt-Shift-Left>', lambda e: (self.skip(-10), 'break')[1])
        self.bind_all('<Alt-Shift-Right>', lambda e: (self.skip(10), 'break')[1])

    # ---------- session ----------
    def _save_session_soon(self):
        if self._save_after:
            self.after_cancel(self._save_after)
        self._save_after = self.after(1500, self._save_session)

    def _save_session(self, force=False):
        self._save_after = None
        if not self.remember.get() and not force:
            return
        if not self.remember.get():
            return
        try:
            data = {'parts': [{'text': p.text, 'audio': str(p.audio) if p.audio else '',
                               'number': p.number, 'duration': p.duration}
                              for p in self.parts],
                    'selected': self.parts.index(self.sel) if self.sel in self.parts else 0}
            SESSION_FILE.write_text(json.dumps(data), encoding='utf-8')
        except Exception:
            pass

    def _load_session(self):
        data = {}
        if self.remember.get():
            try:
                data = json.loads(SESSION_FILE.read_text(encoding='utf-8'))
            except Exception:
                data = {}
        for d in data.get('parts', []):
            p = self._new_part(d.get('text', ''), select=False)
            p.number = d.get('number')
            a = d.get('audio')
            if a and Path(a).exists():
                p.audio, p.state, p.duration = Path(a), 'done', float(d.get('duration') or 0)
            self._refresh_row(p)
        if not self.parts:
            self._new_part('', select=False)
        i = min(int(data.get('selected', 0)), len(self.parts) - 1)
        self.select_part(self.parts[i])
        self._update_counts()

    def _forget_session(self):
        try:
            SESSION_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        self.set_status('Saved parts forgotten (audio files are untouched).')

    # ---------- generate ----------
    def _prepare(self, text):
        return clean_text(text, self.clean_opts, repl=self.cfg.get('replacements', ''))

    def generate_selected(self):
        if self.sel:
            self.generate_part(self.sel)

    def generate_all(self):
        self._sync_part_text()
        n = 0
        for p in list(self.parts):
            if p.text.strip() and p.state not in ('queued', 'generating'):
                if self.generate_part(p, quiet=True):
                    n += 1
        self.set_status(f'Queued {n} part{"s" if n != 1 else ""}.')

    def generate_part(self, part, quiet=False):
        if part is self.sel:
            self._sync_part_text()
        if part.state in ('queued', 'generating'):
            return False
        text = self._prepare(part.text)
        if not text:
            if not quiet:
                self.set_status('Nothing to generate. Paste some text first.')
            return False
        part.text = text
        if part is self.sel:
            self._set_editor_text(text)
        job = Job(part, text, self.voice_id(), round(self.exaggeration.get(), 2),
                  int(self.max_tokens.get()), bool(self.mp3.get() and self.ffmpeg),
                  bool(self.watermark.get()))
        part.job, part.state, part.progress, part.error = job, 'queued', 0.0, ''
        self.pending += 1
        self.jobs_q.put(job)
        self._refresh_row(part)
        self._update_progress_ui()
        self._update_counts()
        self._save_cfg()
        if not quiet:
            self.set_status('Queued.')
        if self.mode == 'single' and part is self.sel:
            self._sync_part_text()
            self._new_part('', index=self.parts.index(part) + 1)
            self.text.focus_set()
        return True

    def cancel_part(self, part):
        if part.job and part.state in ('queued', 'generating'):
            part.job.cancel.set()
            if part.state == 'queued':
                part.state = 'idle'
                self._refresh_row(part)
                self._update_progress_ui()

    def cancel_selected(self):
        if self.sel:
            self.cancel_part(self.sel)

    def cancel_all(self):
        for p in self.parts:
            self.cancel_part(p)
        self.set_status('Cancelled the queue.')

    def _update_progress_ui(self):
        p = self.sel
        if not p:
            return
        if p.state == 'generating':
            self.pbar.configure(value=p.progress * 100)
            self.prog_lbl.configure(text=f'{int(p.progress * 100)}%   {p.eta}', foreground='')
        elif p.state == 'queued':
            self.pbar.configure(value=0)
            self.prog_lbl.configure(text='Waiting in queue ...')
        elif p.state == 'done':
            self.pbar.configure(value=100)
            self.prog_lbl.configure(text=f'Done  {fmt_time(p.duration)} of audio')
        elif p.state == 'error':
            self.pbar.configure(value=0)
            self.prog_lbl.configure(text='Failed. ' + p.error.splitlines()[0][:40])
        else:
            self.pbar.configure(value=0)
            self.prog_lbl.configure(text='')
        self._update_part_label()

    # ---------- worker ----------
    def _worker(self):
        while True:
            job = self.jobs_q.get()
            part = job.part
            try:
                if job.cancel.is_set():
                    continue
                self._run_job(job)
            except Cancelled:
                part.state = 'idle'
                self.set_status('Cancelled.')
            except Exception as e:
                part.state, part.error = 'error', str(e) or repr(e)
                self.set_status('Generation failed (double-click the part for details).')
            finally:
                def fin(part=part):
                    self.pending = max(0, self.pending - 1)
                    self._refresh_row(part)
                    if part is self.sel:
                        self._update_progress_ui()
                    self._update_counts()
                self.ui(fin)

    def _pick_number(self, part):
        n = part.number
        if n:
            pref = f'{n:03d}.'
            try:
                clash = any(p.name.startswith(pref) and p != part.audio
                            for p in self.out_dir.iterdir())
            except OSError:
                clash = False
            if not clash:
                return n
        return next_number(self.out_dir)

    def _run_job(self, job):
        part = job.part
        be = self.backend
        if not be.ready.is_set():
            self.set_status('Waiting for the engine ...')
            while not be.ready.wait(0.4):
                if job.cancel.is_set():
                    raise Cancelled()
        part.state, part.progress, part.eta = 'generating', 0.0, ''
        self.ui(lambda: (self._refresh_row(part), self._update_progress_ui()))
        t0 = time.time()
        chunks = chunk_text(job.text, be.max_chars)
        if not chunks:
            raise RuntimeError('No speakable text found.')
        total = sum(len(c.text) for c in chunks)
        pcm, mp, t, sr, done = [], [], 0.0, None, 0
        for i, c in enumerate(chunks):
            if job.cancel.is_set():
                raise Cancelled()
            if not re.search(r'\w', c.text):
                done += len(c.text)
                continue
            self.set_status(f'Generating part ... sentence {i + 1} of {len(chunks)}')
            data, sr_i = be.synth(c.text, job.voice, job.exaggeration, job.max_tokens)
            if sr is None:
                sr = sr_i
            dur = len(data) / 2 / sr
            last = i == len(chunks) - 1
            gap = 0.35 if last else (be.gap_para if c.para_end else be.gap_sentence)
            pcm.append(data)
            pcm.append(b'\x00\x00' * int(sr * gap))
            mp.append((t, t + dur + gap, c.a, c.b))
            t += dur + gap
            done += len(c.text)
            frac = done / total
            elapsed = time.time() - t0
            eta = elapsed * (1 - frac) / frac if frac > 0 else 0
            part.progress = frac
            part.eta = f'about {fmt_time(eta)} left' if frac < 1 else ''
            self.ui(lambda p=part: (self._refresh_row(p),
                                    self._update_progress_ui() if p is self.sel else None))
        if job.cancel.is_set():
            raise Cancelled()
        if sr is None:
            raise RuntimeError('No speakable text found.')
        audio = b''.join(pcm)

        if job.watermark:
            try:
                import perth
                import numpy as np
                wm = perth.PerthImplicitWatermarker()
                arr = np.frombuffer(audio, dtype='<i2').astype(np.float32) / 32768.0
                arr = wm.apply_watermark(arr, sample_rate=sr)
                audio = (np.clip(arr, -1.0, 1.0) * 32767).astype('<i2').tobytes()
            except Exception:
                pass

        self.out_dir.mkdir(parents=True, exist_ok=True)
        num = self._pick_number(part)
        stem = f'{num:03d}.{be.short(job.voice)}.{datetime.now():%Y-%m-%d}.{job.slug}'
        wav = self.out_dir / f'{stem}.wav'
        write_wav(wav, audio, sr)
        final, note = wav, ''
        if job.mp3:
            self.set_status('Encoding MP3 ...')
            mp3 = wav.with_suffix('.mp3')
            try:
                subprocess.run([self.ffmpeg, '-y', '-loglevel', 'error', '-i', str(wav),
                                '-codec:a', 'libmp3lame', '-qscale:a', '4', str(mp3)],
                               check=True, creationflags=NO_WINDOW)
                wav.unlink(missing_ok=True)
                final = mp3
            except Exception:
                note = ' (MP3 failed, kept WAV)'
        old = part.audio
        if (self.replace_old.get() and old and Path(old).exists() and Path(old) != final
                and Path(old).parent == self.out_dir):
            try:
                Path(old).unlink()
            except OSError:
                pass
        part.audio, part.number, part.duration = final, num, len(audio) / 2 / sr
        part.map, part.map_text = mp, job.text
        part.starts = [m[0] for m in mp]
        part.state, part.progress = 'done', 1.0
        self.set_status(f'Saved {final.name}{note}')

        def after():
            if self.mode == 'single' and self.player.state == 'stopped' and not self._loading_audio:
                self._play_part(part, autoplay=False)
            self._save_session_soon()
        self.ui(after)

    # ---------- voice conversion ----------
    def open_voice_convert(self):
        if not self.backend.voice_paths:
            messagebox.showinfo('Voice conversion', 'No voices loaded yet.')
            return
        top, body = self._dialog('Voice conversion', 580)
        ttk.Label(body, text=(
            'Re-voices a recording with a target voice, keeping the words and timing.\n'
            'The source can be WAV or MP3. A clean 5-30 s clip works best.'),
            style='Muted.TLabel', justify='left').pack(anchor='w', pady=(0, 12))

        src = tk.StringVar()
        row1 = ttk.Frame(body)
        row1.pack(fill='x', pady=4)
        ttk.Label(row1, text='Source audio', width=14).pack(side='left')
        ttk.Entry(row1, textvariable=src, width=42).pack(side='left', padx=(0, 6))

        def pick():
            p = filedialog.askopenfilename(
                title='Source audio',
                filetypes=[('Audio', '*.wav *.mp3 *.flac *.m4a'), ('All', '*.*')])
            if p:
                src.set(p)
        ttk.Button(row1, text='Browse', command=pick).pack(side='left')

        row2 = ttk.Frame(body)
        row2.pack(fill='x', pady=4)
        ttk.Label(row2, text='Target voice', width=14).pack(side='left')
        voice_labels = [f'{n}   -   {"built-in reference" if n == "default" else "custom WAV"}'
                        for n in self.backend.voice_paths]
        target = tk.StringVar()
        target_box = ttk.Combobox(row2, textvariable=target, values=voice_labels,
                                  state='readonly', width=42)
        target_box.pack(side='left')
        if voice_labels:
            target_box.current(0)

        row3 = ttk.Frame(body)
        row3.pack(fill='x', pady=4)
        ttk.Label(row3, text='Exaggeration', width=14).pack(side='left')
        ex = tk.DoubleVar(value=self.exaggeration.get())
        ttk.Scale(row3, from_=0.0, to=1.0, variable=ex, orient='horizontal',
                  length=220).pack(side='left', padx=6)
        ex_lbl = ttk.Label(row3, text=f'{ex.get():.2f}', width=5)
        ex_lbl.pack(side='left')
        ex.trace_add('write', lambda *_: ex_lbl.configure(text=f'{ex.get():.2f}'))

        row4 = ttk.Frame(body)
        row4.pack(fill='x', pady=4)
        ttk.Label(row4, text='Max tokens', width=14).pack(side='left')
        mt = tk.IntVar(value=self.max_tokens.get())
        ttk.Spinbox(row4, from_=128, to=2048, increment=64, textvariable=mt,
                    width=8).pack(side='left')

        row5 = ttk.Frame(body)
        row5.pack(fill='x', pady=4)
        wm = tk.BooleanVar(value=self.watermark.get())
        ttk.Checkbutton(row5, text='Apply watermark', variable=wm).pack(anchor='w', padx=(100, 0))

        status = ttk.Label(body, text='', style='Muted.TLabel')
        status.pack(anchor='w', pady=(8, 0))
        bar = ttk.Progressbar(body, mode='indeterminate')
        bar.pack(fill='x', pady=(6, 0))

        btn_row = ttk.Frame(body)
        btn_row.pack(fill='x', pady=(14, 0))
        run_btn = ttk.Button(btn_row, text='Convert', style='Accent.TButton')
        run_btn.pack(side='right')
        ttk.Button(btn_row, text='Close', command=top.destroy).pack(side='right', padx=8)

        def convert():
            if not src.get():
                messagebox.showwarning('Voice conversion', 'Choose a source audio file.')
                return
            if not target_box.current() >= 0:
                messagebox.showwarning('Voice conversion', 'Choose a target voice.')
                return
            target_name = self.backend.voice_paths and list(self.backend.voice_paths)[target_box.current()]
            out_dir = Path(self.outdir_var.get()) if hasattr(self, 'outdir_var') else self.out_dir
            out_dir.mkdir(parents=True, exist_ok=True)
            num = next_number(out_dir)
            stem = f'{num:03d}.vc.{target_name}.{datetime.now():%Y-%m-%d}.{Path(src.get()).stem}'
            out_path = out_dir / f'{stem}.wav'
            run_btn.state(['disabled'])
            bar.start(12)
            status.configure(text='Converting ...')

            def work():
                try:
                    self.backend.voice_convert(
                        src.get(), target_name, ex.get(), int(mt.get()),
                        out_path, wm.get())
                    self.ui(lambda: (bar.stop(), run_btn.state(['!disabled']),
                                     status.configure(text=f'Saved {out_path.name}'),
                                     messagebox.showinfo('Voice conversion',
                                                         f'Saved:\n{out_path}')))
                except Exception as e:
                    err = str(e)
                    self.ui(lambda: (bar.stop(), run_btn.state(['!disabled']),
                                     status.configure(text='Failed'),
                                     messagebox.showerror('Voice conversion', err[-1200:])))
            threading.Thread(target=work, daemon=True).start()

        run_btn.configure(command=convert)

    # ---------- player ----------
    def _play_part(self, part, start=0.0, autoplay=True):
        if not part.audio or not Path(part.audio).exists():
            self.set_status('This part has no audio yet. Generate it first.')
            return
        path = str(part.audio)
        if self.player.path == path and self.player.loaded:
            self._after_load(part, None, None, start, autoplay)
            return
        self._loading_audio = True
        self.set_status('Loading audio ...')

        def work():
            try:
                pcm, sr = load_pcm_file(path, self.ffmpeg)
                self.ui(lambda: self._after_load(part, pcm, sr, start, autoplay))
            except Exception as e:
                err = str(e)
                self._loading_audio = False
                self.ui(lambda: messagebox.showerror('Audio', err[-800:]))
        threading.Thread(target=work, daemon=True).start()

    def _after_load(self, part, pcm, sr, start, autoplay):
        self._loading_audio = False
        try:
            if pcm is not None:
                self.player.load(pcm, sr, part.audio)
            self.player_part = part
            self._hl_idx = None
            self.now_lbl.configure(text=f'Part {self.parts.index(part) + 1 if part in self.parts else "?"}'
                                        f'   {Path(part.audio).name}')
            self.dur_lbl.configure(text=fmt_time(self.player.duration))
            self.seek.configure(to=max(1.0, self.player.duration))
            if autoplay:
                self.player.play(start)
            else:
                self.player.seek(start)
            self.set_status('Playing.' if autoplay else 'Loaded. Press Play.')
        except Exception as e:
            messagebox.showerror('Audio', f'Could not start playback.\n\n{e}\n\n'
                                          'Is pygame installed in this environment?')

    def listen_selected(self):
        if not self.sel:
            return
        if self.sel.state == 'error':
            self.part_error_dialog()
        else:
            self._play_part(self.sel)

    def toggle_play(self):
        if self.player.loaded:
            self.player.toggle()
        elif self.sel:
            self._play_part(self.sel)

    def stop_play(self):
        self.player.stop()
        self._clear_reading()

    def skip(self, delta):
        if self.player.loaded:
            self.player.skip(delta)
        elif self.sel and self.sel.audio:
            self._play_part(self.sel, autoplay=False)

    def step_part(self, d):
        if not self.parts:
            return
        cur = self.player_part if self.player_part in self.parts else self.sel
        i = self.parts.index(cur)
        j = i + d
        while 0 <= j < len(self.parts):
            if self.parts[j].audio:
                self.select_part(self.parts[j])
                self._play_part(self.parts[j])
                return
            j += d
        self.set_status('No more generated parts that way.')

    def _seek_frac(self, event):
        w = max(1, self.seek.winfo_width() - 20)
        return max(0.0, min(1.0, (event.x - 10) / w))

    def _seek_press(self, event):
        self._dragging = True
        self.seek_var.set(self._seek_frac(event) * self.player.duration)
        return 'break'

    def _seek_drag(self, event):
        self.seek_var.set(self._seek_frac(event) * self.player.duration)
        self.time_lbl.configure(text=fmt_time(self.seek_var.get()))
        return 'break'

    def _seek_release(self, event):
        self._dragging = False
        if self.player.loaded:
            self.player.seek(self._seek_frac(event) * self.player.duration)
        return 'break'

    def _ctrl_click(self, event):
        part = self.sel
        if not part or not part.map_valid:
            return None
        idx = self.text.index(f'@{event.x},{event.y}')
        off = int(self.text.count('1.0', idx, 'chars')[0])
        k = 0
        for n, (t0, t1, a, b) in enumerate(part.map):
            if a <= off:
                k = n
        t = part.map[k][0]
        if self.player_part is part and self.player.loaded:
            self.player.seek(t)
            if self.player.state != 'playing':
                self.player.play(t)
        else:
            self._play_part(part, start=t)
        return 'break'

    def _clear_reading(self):
        self.text.tag_remove('reading', '1.0', 'end')
        self._hl_idx = None

    def _tick(self):
        pl = self.player
        try:
            if pl.loaded:
                pos = pl.position()
                if not self._dragging:
                    self.seek_var.set(pos)
                    self.time_lbl.configure(text=fmt_time(pos))
                self.play_btn.configure(text='Pause' if pl.state == 'playing' else 'Play')
                if pl.poll_ended():
                    self._on_play_end()
                self._update_reading(pos)
            else:
                self.play_btn.configure(text='Play')
        finally:
            self.after(200, self._tick)

    def _update_reading(self, pos):
        part = self.player_part
        if (not self.follow.get() or part is None or part is not self.sel
                or self.player.state == 'stopped' or not part.map_valid):
            if self._hl_idx is not None:
                self._clear_reading()
            return
        k = max(0, bisect.bisect_right(part.starts, pos) - 1)
        if k != self._hl_idx:
            self._hl_idx = k
            a, b = part.map[k][2], part.map[k][3]
            self.text.tag_remove('reading', '1.0', 'end')
            self.text.tag_add('reading', f'1.0+{a}c', f'1.0+{b}c')
            self.text.see(f'1.0+{a}c')

    def _on_play_end(self):
        self.seek_var.set(0)
        self.time_lbl.configure(text='00:00')
        self._clear_reading()
        if self.autonext.get() and self.player_part in self.parts:
            i = self.parts.index(self.player_part)
            for p in self.parts[i + 1:]:
                if p.audio:
                    self.select_part(p)
                    self._play_part(p)
                    return
        self.set_status('Finished.')

    # ---------- thread-safe helpers ----------
    def ui(self, fn):
        self.ui_q.put(fn)

    def set_status(self, msg):
        self.ui(lambda: self.status.set(msg))

    def _pump(self):
        try:
            while True:
                self.ui_q.get_nowait()()
        except queue.Empty:
            pass
        except Exception:
            traceback.print_exc()
        self.after(80, self._pump)

    # ---------- misc ----------
    def _open_output(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(self.out_dir)
        except AttributeError:
            subprocess.Popen(['xdg-open', str(self.out_dir)])

    def _change_output(self):
        d = filedialog.askdirectory(parent=self, title='Output folder',
                                    initialdir=str(self.out_dir))
        if d:
            self.out_dir = Path(d)
            self._save_cfg()
            self.set_status(f'Output folder: {d}')

    def part_error_dialog(self):
        p = self.sel
        if p and p.state == 'error':
            messagebox.showerror('Generation failed', p.error[-1500:])


# ============================================================
#  ENTRY POINT
# ============================================================
def _fatal(msg):
    try:
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror(f'{APP_NAME} crashed', msg[-1800:])
        r.destroy()
    except Exception:
        pass


def main():
    missing = [m for m in ('chatterbox_onnx', 'onnxruntime', 'librosa', 'numpy',
                            'soundfile', 'tokenizers', 'huggingface_hub', 'pygame')
               if importlib.util.find_spec(m) is None]
    if missing:
        py = ROOT / '.venv' / 'Scripts' / 'python.exe'
        _fatal('Missing Python packages: ' + ', '.join(missing) + '\n\nInstall with:\n\n'
               f'"{py if py.exists() else "python"}" -m pip install '
               'chatterbox-onnx onnxruntime librosa numpy soundfile tqdm tokenizers '
               'huggingface_hub pygame\n\n'
               'Or run setup_chatterbox.bat once.')
        sys.exit(1)
    Studio().mainloop()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception:
        _fatal(traceback.format_exc())
        sys.exit(1)