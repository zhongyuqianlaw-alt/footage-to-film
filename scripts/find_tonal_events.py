#!/usr/bin/env python3
"""Find sustained tonal events (ship horns, whistles, bells) in a clip's soundtrack.

Audio is decoded mono at 16 kHz and analysed with an STFT (256 ms Hann window, 50 ms hop).
A frame is "tonal" when >= 85 % of its 200-1200 Hz band energy sits either on one harmonic series or on
its three strongest tones (chime whistles with unrelated notes), and it counts when that band energy is
also >= 9 dB above the background: the median band energy of the clip's non-tonal frames, so a horn that
fills most of a short clip is still measured against the quieter rest. f0 is estimated from the spacing
of the spectral peaks (a gap between two partials, or a lone partial itself), refined by a least-squares
fit over the partials that sit on its harmonics, down to 50 Hz; so a large ship's horn (fundamental
70-200 Hz, often below the band) is found through its overtones, and a series whose upper partial is the
loudest still reports its true fundamental. Runs of tonal frames lasting >= 0.8 s are reported with
start / end / peak (the strongest frequency of the event's average spectrum in the band) / estimated f0
(null for chords). --band takes two frequencies between 0 and 8000 Hz at least 20 Hz apart. Steady
pitches only: a fast pitch glide (a siren sweep) is found in parts at best. When the clip is tonal almost
throughout there is no background to compare with: a warning says so (cut a clip with a few seconds before
or after the sound).
Clips without an audio stream are reported as such; unreadable files exit non-zero.

usage: find_tonal_events.py clip.mp4 [clip2.mov ...] [--min-db 9 --min-conc 0.85 --min-dur 0.8] [--json]
"""
import argparse
import json
import subprocess
import sys

import deps  # noqa: F401
import numpy as np

import common as C
from common import FFMPEG

SR, NFFT, HOP = 16000, 4096, 800
F0_MIN, MIN_BAND_HZ = 50.0, 20.0


def load(path):
    """Mono float samples, or None when the clip has no audio stream."""
    if not C.has_audio(path):
        return None
    r = subprocess.run([FFMPEG, "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                       capture_output=True)
    if r.returncode != 0:
        raise C.ProbeError(f"ffmpeg could not decode the audio of {path}: {r.stderr.decode(errors='replace')[-300:]}")
    return np.frombuffer(r.stdout, np.float32)


def band_power(x, band):
    """Per-frame power spectrum restricted to the band, computed in chunks to bound memory."""
    freqs = np.fft.rfftfreq(NFFT, 1 / SR)
    sel = (freqs >= band[0]) & (freqs <= band[1])
    n = max(0, 1 + (len(x) - NFFT) // HOP)
    win, out = np.hanning(NFFT), []
    for a in range(0, n, 512):
        idx = np.arange(NFFT)[None, :] + HOP * np.arange(a, min(n, a + 512))[:, None]
        out.append((np.abs(np.fft.rfft(x[idx] * win, axis=1)) ** 2)[:, sel])
    return (np.concatenate(out) if out else np.zeros((0, sel.sum()))), freqs[sel]


def share_near(p, f, tones, f0=None):
    df = f[1] - f[0]
    tol = np.maximum(2 * df, 0.015 * tones)
    if f0:
        tol = np.minimum(tol, f0 / 4)
    mask = (np.abs(f[:, None] - tones[None, :]) <= tol[None, :]).any(1)
    return float(p[mask].sum() / (p.sum() + 1e-30))


def strongest_tones(p, f, k=3):
    peaks = np.flatnonzero((p[1:-1] > p[:-2]) & (p[1:-1] >= p[2:])) + 1
    return f[peaks[np.argsort(p[peaks])[::-1][:k]]]


def spectral_peaks(p, f, k=6, floor=0.01):
    """(Hz, power) of the k strongest local maxima within 20 dB of the strongest, frequency refined by a
    parabola through the log power of the three bins around each maximum."""
    i = np.flatnonzero((p[1:-1] > p[:-2]) & (p[1:-1] >= p[2:])) + 1
    i = i[p[i] >= floor * p.max()]
    i = i[np.argsort(p[i])[::-1][:k]]
    a, b, c = (np.log(p[j] + 1e-30) for j in (i - 1, i, i + 1))
    d = a - 2 * b + c
    off = np.where(d < 0, np.clip(0.5 * (a - c) / np.where(d < 0, d, -1), -0.5, 0.5), 0.0)
    return f[i] + off * (f[1] - f[0]), p[i]


def harmonic_share(p, f, f0):
    hs = np.arange(max(1, int(np.ceil(f[0] / f0 - 0.5))), int(f[-1] / f0 + 0.5) + 1) * f0
    return share_near(p, f, hs, f0)


def fit_f0(pk, pw, f0):
    """Least-squares f0 over the peaks that sit on its harmonics, weighted by their power."""
    h = np.round(pk / f0)
    ok = (h >= 1) & (np.abs(pk - h * f0) <= 0.03 * h * f0)
    return float((pw[ok] * h[ok] * pk[ok]).sum() / (pw[ok] * h[ok] ** 2).sum()) if ok.any() else f0


def concentration(p, f):
    """(share of band energy on the best harmonic series or the 3 strongest tones, f0 Hz).

    f0 candidates come from harmonic spacing: every gap between two spectral peaks and every peak itself.
    f0 is 0 when a few unrelated tones explain the energy clearly better than any harmonic series (chords).
    """
    pk, pw = spectral_peaks(p, f)
    cands = sorted({float(c) for c in [*pk, *(abs(x - y) for j, x in enumerate(pk) for y in pk[j + 1:])]
                    if c >= F0_MIN}, reverse=True)
    best, f0_best = 0.0, 0.0
    for f0 in cands:
        sh = harmonic_share(p, f, f0)
        if sh > best + 0.02:
            best, f0_best = sh, f0
    if f0_best:
        f0_best = fit_f0(pk, pw, f0_best)
        best = max(best, harmonic_share(p, f, f0_best))
    tones = share_near(p, f, strongest_tones(p, f))
    if tones > best + 0.02:
        f0_best = 0.0
    return max(best, tones), float(f0_best)


def runs(flags, max_gap):
    out, start, last = [], None, None
    for i in np.flatnonzero(flags):
        if start is None or i - last > max_gap + 1:
            if start is not None:
                out.append((start, last))
            start = i
        last = i
    return out + ([(start, last)] if start is not None else [])


def concentrations(P, f, idx, conc, f0s, done):
    for i in idx[~done[idx]]:
        conc[i], f0s[i] = concentration(P[i], f)
        done[i] = True


def tonal_share(P, f, conc, f0s, done, min_conc):
    """Share of tonal frames in up to 60 frames spread over the whole clip."""
    idx = np.unique(np.linspace(0, len(P) - 1, min(60, len(P))).astype(int))
    concentrations(P, f, idx, conc, f0s, done)
    return float((conc[idx] >= min_conc).mean())


def find_events(x, min_db=9.0, min_conc=0.85, min_dur=0.8, band=(200, 1200)):
    """(events, warning or None)."""
    P, f = band_power(x, band)
    if not len(P):
        return [], None
    e_db = 10 * np.log10(P.sum(1) + 1e-8)
    conc, f0s, done = np.zeros(len(P)), np.zeros(len(P)), np.zeros(len(P), bool)
    floor = float(np.percentile(e_db, 5))
    concentrations(P, f, np.flatnonzero(e_db >= floor + min_db), conc, f0s, done)
    quiet = ~(done & (conc >= min_conc))
    base = float(np.median(e_db[quiet])) if quiet.any() else floor
    concentrations(P, f, np.flatnonzero(e_db >= base + min_db), conc, f0s, done)
    flags = (e_db >= base + min_db) & (conc >= min_conc)
    share = tonal_share(P, f, conc, f0s, done, min_conc)
    warning = (f"tonal sound in {share:.0%} of the clip: little background to measure against, check by ear"
               if share >= 0.8 else None)
    return runs_to_events(flags, e_db, base, conc, f0s, (P, f), min_dur), warning


def peak_of(p, f):
    """Strongest frequency of a power spectrum, refined between bins (its strongest interior maximum)."""
    pk, _ = spectral_peaks(p, f, k=1)
    return float(pk[0]) if len(pk) else float(f[np.argmax(p)])


def runs_to_events(flags, e_db, base, conc, f0s, spectra, min_dur):
    """Events from runs of tonal frames; peak_hz is the strongest frequency of the event's mean spectrum."""
    t = lambda i: (i * HOP + NFFT / 2) / SR
    events = []
    for a, b in runs(flags, 2):
        start, end = t(a) - HOP / SR / 2, t(b) + HOP / SR / 2
        if end - start < min_dur:
            continue
        hot = [i for i in range(a, b + 1) if flags[i]]
        events.append({"start_s": round(start, 2), "end_s": round(end, 2), "dur_s": round(end - start, 2),
                       "peak_hz": round(peak_of(spectra[0][hot].mean(0), spectra[1]), 1),
                       "f0_hz": round(float(np.median(f0s[hot])), 1) or None,
                       "over_background_db": round(float(e_db[hot].max() - base), 1),
                       "concentration": round(float(conc[hot].mean()), 3)})
    return events


def show(p, ev, warning, as_json):
    if as_json:
        print(json.dumps({"file": p, "audio": ev is not None, "events": ev or [], "warning": warning},
                         ensure_ascii=False), flush=True)
        return
    if ev is None:
        print(f"{p}: no audio stream")
        return
    print(f"{p}: {len(ev)} tonal event(s)" + (f"  WARNING: {warning}" if warning else ""))
    for e in ev:
        print(f"  {e['start_s']:8.2f} - {e['end_s']:8.2f} s  peak {e['peak_hz']:7.1f} Hz  "
              f"f0 {e['f0_hz'] or '-':>6} Hz  +{e['over_background_db']} dB  conc {e['concentration']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--min-db", type=float, default=9.0)
    ap.add_argument("--min-conc", type=float, default=0.85)
    ap.add_argument("--min-dur", type=float, default=0.8)
    ap.add_argument("--band", type=float, nargs=2, default=(200, 1200), metavar=("LO", "HI"),
                    help="analysed band in Hz (default 200 1200)")
    ap.add_argument("--json", action="store_true", help="one JSON line per file")
    a = ap.parse_args()
    lo, hi = a.band
    if not 0 < lo < hi <= SR / 2 or hi - lo < MIN_BAND_HZ:
        ap.error(f"--band LO HI: 0 < LO < HI <= {SR // 2} Hz and at least {MIN_BAND_HZ:g} Hz wide, got {lo:g} {hi:g}")
    failed = 0
    for p in a.files:
        try:
            x = load(p)
        except C.ProbeError as e:
            print(f"error: {e}", file=sys.stderr)
            failed += 1
            continue
        ev, warning = (None, None) if x is None else find_events(x, a.min_db, a.min_conc, a.min_dur, tuple(a.band))
        show(p, ev, warning, a.json)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
