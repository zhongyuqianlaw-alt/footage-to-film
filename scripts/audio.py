"""Film soundtrack: music bed (music.py), in-sync source sounds with ducking, two-pass loudness, loudness check."""
import os
import re
import subprocess
import sys

import common as C
import music


def music_window(P, edl, lo, hi, sel, total):
    """Music parts planned on the whole film, and the film seconds (w0, w1) this render covers."""
    durs = [sel[i - lo] if lo <= i <= hi else C.shot_duration(P, s) for i, s in enumerate(edl["shots"])]
    errs = music.check(P, edl, durs)
    if errs:
        sys.exit("music problems:\n- " + "\n- ".join(errs))
    parts, _ = music.plan(P, edl, durs)
    _, _, starts, _ = C.timeline(P, edl["shots"], durs, bool(edl.get("endcard")))
    return parts, round(starts[lo], 3), round(starts[lo] + total, 3)


def cut_source_audio(P, s):
    """Keep a meaningful source sound (e.g. a horn): high-pass the wind, cap its peak, short fades."""
    out = C.source_audio_path(P, s)
    if os.path.exists(out):
        return out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    sa, d = P["source_audio"], s["out_s"] - s["in_s"]
    src = ["-ss", s["in_s"], "-t", d, "-i", os.path.join(P["library_dir"], s["file"]), "-vn"]
    err = C.run([C.FFMPEG, "-hide_banner", *src, "-af", f"highpass=f={sa['highpass_hz']},volumedetect",
                 "-f", "null", "-"], quiet=True).stderr
    peak = float(err.split("max_volume:")[1].split("dB")[0])
    if peak < -60:
        sys.exit(f"shot {s['n']}: the source_audio range is silent (peak {peak} dB)")
    C.run([C.FFMPEG, "-y", "-hide_banner", *src, "-af",
           f"aresample=48000,highpass=f={sa['highpass_hz']},volume={sa['peak_dbfs'] - peak:.1f}dB,"
           f"afade=t=in:d=0.6,afade=t=out:st={d - 0.8:.3f}:d=0.8", "-ac", "2", "-c:a", "pcm_s16le", out])
    return out


def mix_audio(P, parts, w0, w1, sources, trim=0.0):
    """Music bed of film seconds w0..w1, ducked under every source sound [(wav, start, end)] in render time.

    The source sounds are added trim dB louder; trim None gives the ducked bed alone (for measuring). The mix
    is 32-bit float, so nothing clips before the loudness pass sets the final level.
    """
    duck, n = P["source_audio"]["duck"], len(parts)
    inputs, chain = music.bed(parts, w0, w1)
    if not sources:
        chain.append("[bed]anull[mix]")
    else:
        dips = "*".join(f"(1-{1 - duck}*clip((t-{a})/0.8,0,1)*clip(({b}-t)/0.8,0,1))" for _, a, b in sources)
        chain.append(f"[bed]volume='{dips}':eval=frame[{'mix' if trim is None else 'mus'}]")
    if sources and trim is not None:
        for j, (wav, a, _) in enumerate(sources):
            ms = round(a * 1000)
            chain.append(f"[{n + j}:a]volume={trim:.2f}dB,adelay={ms}|{ms}[s{j}]")
            inputs += ["-i", wav]
        labels = "".join(f"[s{j}]" for j in range(len(sources)))
        chain.append(f"[mus]{labels}amix=inputs={len(sources) + 1}:duration=first:normalize=0[mix]")
    mix = os.path.join(P["scratch_dir"], "mix_bed.wav" if trim is None else "mix.wav")
    C.run([C.FFMPEG, "-y", "-hide_banner", *inputs, "-filter_complex", ";".join(chain), "-map", "[mix]",
           "-c:a", "pcm_f32le", mix])
    return mix


def source_trim(P, parts, w0, w1, sources):
    """dB added to the source sounds before mixing: minus the gain the ducked music bed will get from the
    loudness pass, so a source sound peaks near source_audio.peak_dbfs in the finished film instead of
    being lifted by that gain along with a quiet music bed."""
    if not sources:
        return 0.0
    i, _, _ = ebur128(mix_audio(P, parts, w0, w1, sources, None))
    return 0.0 if i is None else round(i - P["loudness"]["I"], 2)


def source_peak_report(P, trim, gain):
    """Where the source sounds peak after the loudness gain, with a warning when that misses the target."""
    target = P["source_audio"]["peak_dbfs"]
    peak = round(target + trim + gain, 1)
    print(f"source sounds peak at about {peak} dBFS (source_audio.peak_dbfs {target})", flush=True)
    if abs(peak - target) > 2.0:
        print(f"WARNING: source sounds peak at about {peak} dBFS, {peak - target:+.1f} dB off source_audio.peak_dbfs: "
              "they carry much of this render's loudness (a short preview around them?); check the full film",
              file=sys.stderr)


def loudnorm(P, src, dst):
    """Two-pass loudness: measure the mix (EBU R128 integrated loudness and true peak), then one linear gain.

    loudnorm's own second pass silently switches to dynamic mode when the mix's loudness range exceeds the
    LRA target (short previews, quiet passages, ducking) and then misses the integrated target by LUs, and
    its first-pass input_i can sit about 1 LU away from ebur128 on very dynamic mixes; a gain from the
    ebur128 reading lands on target. A 4x-oversampled limiter is added only when the gain would push the
    true peak over TP (then the gain is re-measured, because limiting loud peaks lowers the loudness), and
    TP keeps aac_headroom_db spare because the AAC encode adds a few tenths of a dB.
    """
    L = P["loudness"]
    tp = L["TP"] - L.get("aac_headroom_db", 0.5)
    i0, peak, _ = ebur128(src)
    if i0 is None:
        sys.exit(f"could not measure the loudness of {src}")
    gain = L["I"] - i0
    limit = f"alimiter=limit={10 ** (tp / 20):.4f}:attack=1:release=60:level=0:latency=1"
    for _ in range(4):
        af = f"volume={gain:.2f}dB"
        if peak + gain > tp:
            af = f"aresample=192000,{af},{limit}"
        C.run([C.FFMPEG, "-y", "-hide_banner", "-i", src, "-af", af + ",aresample=48000", "-c:a", "pcm_s24le", dst],
              quiet=True)
        if peak + gain <= tp:
            break
        i, _, _ = ebur128(dst)
        if abs(i - L["I"]) <= 0.2:
            break
        gain += L["I"] - i
    return i0, gain


def make_audio(P, edl, lo, hi, tl):
    """Final soundtrack wav of the selected shots, and the music parts it used (for the credit)."""
    sel, durs, starts, total = tl
    parts, w0, w1 = music_window(P, edl, lo, hi, sel, total)
    shots = edl["shots"][lo:hi + 1]
    srcs = [(cut_source_audio(P, s), starts[i], round(starts[i] + durs[i], 3))
            for i, s in enumerate(shots) if s.get("source_audio")]
    trim = source_trim(P, parts, w0, w1, srcs)
    out = os.path.join(P["scratch_dir"], "audio_final.wav")
    i, gain = loudnorm(P, mix_audio(P, parts, w0, w1, srcs, trim), out)
    print(f"music: {music.describe(parts)}; this render covers film {w0}-{w1} s; source sounds "
          f"{[(a, b) for _, a, b in srcs]}; mix {i} LUFS, gain {gain:+.2f} dB", flush=True)
    if srcs:
        source_peak_report(P, trim, gain)
    return out, parts


def ebur128(path):
    """(integrated LUFS, true peak dBTP, loudness range LU) of a file's audio, or (None, None, None)."""
    err = subprocess.run([C.FFMPEG, "-hide_banner", "-nostats", "-i", path, "-vn", "-af", "ebur128=peak=true",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    m = re.search(r"Integrated loudness:\s+I:\s+([-\d.]+) LUFS.*?LRA:\s+([-\d.]+) LU.*?"
                  r"True peak:\s+Peak:\s+([-\d.inf]+) dBFS", err[err.rfind("Summary:"):], re.S)
    return (float(m.group(1)), float(m.group(3)), float(m.group(2))) if m else (None, None, None)


def measure_loudness(P, path):
    """Check the encoded AAC against the loudness targets; warn when it misses, note a wide loudness range."""
    L = P["loudness"]
    i, tp, lra = ebur128(path)
    print(f"loudness of {os.path.basename(path)}: {i} LUFS integrated, true peak {tp} dBTP, range {lra} LU",
          flush=True)
    if i is None or abs(i - L["I"]) > 1.0 or tp > L["TP"]:
        print(f"WARNING: loudness misses the target {L['I']} LUFS / {L['TP']} dBTP", file=sys.stderr)
    elif lra is not None and lra > L["LRA"]:
        print(f"note: loudness range {lra} LU is wider than loudness.LRA {L['LRA']} LU (quiet passages, ducking or "
              "dynamic music); the gain is not changed by it - lift quiet parts with music gain_db if the client "
              "needs a steadier level", file=sys.stderr)
    return i, tp


if __name__ == "__main__":
    print("audio.py is a library module used by build_film.py, not a command: run build_film.py --help")
    sys.exit(2)
