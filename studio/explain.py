#!/usr/bin/env python3
"""GTA6 Studio — format EXPLAINED : l'image vulgarise l'info (jauge, schéma, comparaison, carte des régions...)
sur des visuels officiels Rockstar. Même voix, même Whisper, mêmes garde-fous que make.py.

Usage :
  python3 studio/explain.py clips/<slug>                  rendu complet
  python3 studio/explain.py clips/<slug> --test-voice     voix robot locale (gratuit)
  python3 studio/explain.py clips/<slug> --stills 0,2,5   captures fixes pour contrôle (après un rendu ou avec --test-voice)
"""
import argparse, json, os, re, shutil, subprocess, sys
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make  # noqa: E402
from PIL import Image  # noqa: E402

ROOT, OFFICIAL, HERE = make.ROOT, make.OFFICIAL, os.path.dirname(os.path.abspath(__file__))
TYPES = {"meter", "list", "split", "branch", "route", "bars", "grid", "date", "slam", "cards", "stat", "quote", "photo"}
MASCOTS = json.load(open(os.path.join(OFFICIAL, "loops", "MASCOTS.json"))) if os.path.isfile(os.path.join(OFFICIAL, "loops", "MASCOTS.json")) else {}
SFX = {  # bruitages synthétisés maison (aucune licence tierce)
    "whoosh": "anoisesrc=color=pink:duration=0.45:amplitude=0.5,highpass=f=350,lowpass=f=5500,afade=t=in:st=0:d=0.2:curve=exp,afade=t=out:st=0.2:d=0.25",
    "impact": "sine=frequency=58:duration=0.55,afade=t=out:st=0.03:d=0.5,volume=2.2",
    "tick": "sine=frequency=1900:duration=0.03,afade=t=out:st=0:d=0.03,volume=0.6"}



def avoid_set():
    av = os.path.join(OFFICIAL, "AVOID.txt")
    return {l.strip() for l in open(av, encoding="utf-8") if l.strip() and not l.startswith("#")} if os.path.isfile(av) else set()


def official(rel, avoid):
    p = os.path.realpath(os.path.join(OFFICIAL, rel))
    if not p.startswith(os.path.realpath(OFFICIAL) + os.sep) or not os.path.isfile(p):
        make.die(f"visuel non officiel ou introuvable : {rel}")
    if rel in avoid:
        make.die(f"visuel interdit (AVOID.txt) : {rel}")
    return p


def crop(src, out, w, h, focus=0.5):
    im = Image.open(src).convert("RGB")
    sw, sh = im.size
    if sw / sh > w / h:
        nw = int(sh * w / h)
        x = int((sw - nw) * focus)
        im = im.crop((x, 0, x + nw, sh))
    else:
        nh = int(sw * h / w)
        im = im.crop((0, (sh - nh) // 2, sw, (sh - nh) // 2 + nh))
    im.resize((w, h), Image.LANCZOS).save(out, quality=90)


def chunks_from(words):
    out, cur = [], []
    for w in words:
        if cur and (len(cur) == 3 or cur[-1]["line"] != w["line"] or re.search(r"[.,!?;:]$", cur[-1]["w"])):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    res = []
    for i, ch in enumerate(out):
        nxt = out[i + 1][0]["s"] if i + 1 < len(out) and out[i + 1][0]["line"] == ch[0]["line"] else ch[-1]["e"] + 0.25
        res.append({"s": ch[0]["s"], "e": nxt, "words": [{"w": re.sub(r"[<>&]", "", x["w"]), "s": x["s"]} for x in ch]})
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip_dir")
    ap.add_argument("--test-voice", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--stills", help="temps en secondes séparés par des virgules : captures de contrôle seulement")
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(ROOT, "config.json")))
    clip_dir = os.path.abspath(a.clip_dir)
    script = json.load(open(os.path.join(clip_dir, "script.json")))
    slug, lines = script["slug"], script["lines"]
    make.check(script, cfg, a.test_voice)
    avoid = avoid_set()
    for i, ln in enumerate(lines, 1):
        if (ln.get("scene") or {}).get("type") not in TYPES:
            make.die(f"ligne {i} : scene.type doit être parmi {sorted(TYPES)}")
    if a.check:
        return
    work = os.path.join(clip_dir, "build")
    if not a.stills:
        shutil.rmtree(work, ignore_errors=True)
    os.makedirs(os.path.join(work, "img"), exist_ok=True)
    gap = cfg["gap_seconds"]
    make.resolve_voice(script, cfg, a.test_voice)

    print(f"[1/5] voix ({cfg['_engine']})")
    wavs = make.make_voice(lines, cfg, work, a.test_voice)
    print("[2/5] timing des mots (Whisper)")
    words, segs = make.word_timings(wavs, lines, cfg, gap)

    print("[3/5] visuels officiels")
    scenes, sfx_events, stickers = [], [], []
    for i, (ln, (s, d)) in enumerate(zip(lines, segs)):
        sc = json.loads(json.dumps(ln["scene"]))
        bg = f"img/bg_{i:02d}.jpg"
        crop(official(ln["visual"], avoid), os.path.join(work, bg), 1188, 2112, float(ln.get("focus", 0.5)))
        if sc["type"] == "split":
            for k in ("top", "bottom"):
                f = f"img/split_{i:02d}_{k}.jpg"
                crop(official(sc[k]["img"], avoid), os.path.join(work, f), 1188, 1056, float(sc[k].get("focus", 0.5)))
                sc[k]["img"] = f
        if sc["type"] == "grid":
            for j, t in enumerate(sc["tiles"]):
                f = f"img/tile_{i:02d}_{j}.jpg"
                crop(official(t["img"], avoid), os.path.join(work, f), 744, 336, float(t.get("focus", 0.5)))
                t["img"] = f
        lw = [{"w": w["w"], "s": w["s"], "e": w["e"]} for w in words if w["line"] == i]
        # fonds multiples (coupes rapides) : "visuals": [...], coupes sur "cut_words" ou réparties
        bgs = [{"src": bg, "at": 0}]
        extra = ln.get("visuals") or []
        cut_words = ln.get("cut_words") or []
        for j, v in enumerate(extra):
            f = f"img/bg_{i:02d}_{j + 1}.jpg"
            vv = v if isinstance(v, dict) else {"img": v}
            crop(official(vv["img"], avoid), os.path.join(work, f), 1188, 2112, float(vv.get("focus", 0.5)))
            at = None
            if j < len(cut_words):
                k = re.sub(r"[^a-z0-9]", "", cut_words[j].lower())
                at = next((w["s"] - s for w in lw if re.sub(r"[^a-z0-9]", "", w["w"].lower()).startswith(k)), None)
            if at is None:
                at = d * (j + 1) / (len(extra) + 1)
            bgs.append({"src": f, "at": max(0.3, at - 0.05)})
            sfx_events.append(("whoosh", s + max(0.3, at - 0.05) - 0.12, 0.22))
        srcs = ln.get("sources") or [ln.get("source", "")]
        hosts = list(dict.fromkeys((urlparse(u).hostname or "").replace("www.", "") for u in srcs if u.startswith("http")))
        scenes.append({
            "start": s, "dur": d, "bg": bg, "bgs": bgs, "headline": ln.get("headline"), "scene": sc,
            "status": None if ln.get("fact") == "CTA" else ln["status"], "badge": ln.get("badge"),
            "receipt": ln.get("receipt", True),
            "source": " · ".join(hosts[:2]) if hosts else None, "sources_label": "SOURCES: " if len(hosts) > 1 else "SOURCE: ",
            "dim": float(ln.get("dim", 0.24)), "words": lw})
        # bruitages + mascottes (thème news)
        if i > 0:
            sfx_events.append(("whoosh", s - 0.1, 0.32))
        if sc["type"] == "slam":
            ems = {re.sub(r"[^a-z0-9]", "", e.lower()) for e in sc.get("em", [])}
            sfx_events.append(("impact", s + (lw[0]["s"] - s if i else 0.0), 0.5))
            for w in lw:
                if re.sub(r"[^a-z0-9]", "", w["w"].lower()) in ems:
                    sfx_events.append(("impact", w["s"] - 0.03, 0.45))
        if sc["type"] == "cards":
            for it in sc.get("items", []):
                k = re.sub(r"[^a-z0-9]", "", (it.get("word") or "").lower())
                t0 = next((w["s"] for w in lw if k and re.sub(r"[^a-z0-9]", "", w["w"].lower()).startswith(k)), None)
                if t0:
                    sfx_events.append(("tick", t0 - 0.12, 0.35))
        if ln.get("sticker"):
            st = ln["sticker"]
            if st["name"] not in MASCOTS:
                make.die(f"ligne {i + 1} : mascotte inconnue {st['name']} (voir assets/official/loops/MASCOTS.json)")
            k = re.sub(r"[^a-z0-9]", "", (st.get("word") or "").lower())
            t0 = next((w["s"] for w in lw if k and re.sub(r"[^a-z0-9]", "", w["w"].lower()).startswith(k)), s + 0.15)
            stickers.append({"file": os.path.join(OFFICIAL, "loops", MASCOTS[st["name"]]), "x": st.get("x", 60),
                             "y": st.get("y", 1300), "w": st.get("w", 300), "start": t0, "end": s + d})
    total = segs[-1][0] + segs[-1][1]
    data = {"scenes": scenes, "chunks": chunks_from(words), "total": total,
            "ai_label": cfg["ai_label_text"], "credit": cfg["credit_text"],
            "theme": script.get("theme", "explained"), "tag": script.get("tag", "BREAKING"), "kicker": script.get("kicker")}
    html = open(os.path.join(HERE, "explainer.html"), encoding="utf-8").read().replace("/*__DATA__*/", json.dumps(data))
    page = os.path.join(work, "index.html")
    open(page, "w", encoding="utf-8").write(html)

    if a.stills:
        out = os.path.join(work, "still_%t.jpg")
        make.run(["node", os.path.join(HERE, "render_frames.js"), page, out, "0", "stills:" + a.stills])
        print("[OK] captures dans", work)
        return

    print(f"[4/5] animation ({total:.1f}s, {int(total * 30)} images)")
    r = subprocess.run(["node", os.path.join(HERE, "render_frames.js"), page, os.path.join(work, "video.mp4"), f"{total:.3f}"])
    if r.returncode != 0:
        make.die("le rendu des images a échoué")
    if stickers:  # mascottes officielles animées (vidéo couleur en haut, masque alpha en bas)
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", os.path.join(work, "video.mp4")]
        fc, prev = [], "[0:v]"
        for n, st in enumerate(stickers, 1):
            cmd += ["-stream_loop", "-1", "-i", st["file"]]
            fc.append(f"[{n}:v]split[a{n}][b{n}];[a{n}]crop=iw:ih/2:0:0[c{n}];[b{n}]crop=iw:ih/2:0:ih/2,format=gray[m{n}];"
                      f"[c{n}][m{n}]alphamerge,scale={st['w']}:-2,setpts=PTS-STARTPTS+{st['start']:.3f}/TB,"
                      f"fade=t=in:st={st['start']:.3f}:d=0.2:alpha=1[s{n}];"
                      f"{prev}[s{n}]overlay={st['x']}:{st['y']}:enable='between(t,{st['start']:.3f},{st['end']:.3f})'[v{n}]")
            prev = f"[v{n}]"
        cmd += ["-filter_complex", ";".join(fc), "-map", prev, "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "medium",
                "-crf", "18", "-pix_fmt", "yuv420p", os.path.join(work, "video_st.mp4")]
        make.run(cmd)
        os.replace(os.path.join(work, "video_st.mp4"), os.path.join(work, "video.mp4"))
    vparts = []
    for i, (wav, (s, d)) in enumerate(zip(wavs, segs)):
        p = os.path.join(work, f"vpad_{i:02d}.wav")
        make.run(["ffmpeg", "-y", "-v", "error", "-i", wav, "-af", f"apad=whole_dur={d:.3f}", "-ar", "44100", "-ac", "1", p])
        vparts.append(p)
    with open(os.path.join(work, "vconcat.txt"), "w") as f:
        f.writelines(f"file '{p}'\n" for p in vparts)
    make.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", os.path.join(work, "vconcat.txt"),
              os.path.join(work, "voice.wav")])
    if sfx_events and script.get("theme") == "news":  # bruitages discrets sous la voix
        sd = os.path.join(ROOT, ".cache", "sfx")
        os.makedirs(sd, exist_ok=True)
        for n, spec in SFX.items():
            if not os.path.isfile(os.path.join(sd, n + ".wav")):
                make.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", spec, "-ar", "44100", "-ac", "1", os.path.join(sd, n + ".wav")])
        ev = [e for e in sfx_events if 0 <= e[1] < total][:40]
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", os.path.join(work, "voice.wav")]
        fc = []
        for n, (name, t0, vol) in enumerate(ev, 1):
            cmd += ["-i", os.path.join(sd, name + ".wav")]
            ms = int(t0 * 1000)
            fc.append(f"[{n}:a]adelay={ms}|{ms},volume={vol * 0.5:.3f}[e{n}]")
        fc.append("[0:a]" + "".join(f"[e{n}]" for n in range(1, len(ev) + 1)) +
                  f"amix=inputs={len(ev) + 1}:duration=first:normalize=0[a]")
        cmd += ["-filter_complex", ";".join(fc), "-map", "[a]", os.path.join(work, "voice_sfx.wav")]
        make.run(cmd)
        os.replace(os.path.join(work, "voice_sfx.wav"), os.path.join(work, "voice.wav"))

    print("[5/5] assemblage + mixage")
    os.makedirs(os.path.join(ROOT, "out"), exist_ok=True)
    out = os.path.join(ROOT, "out", f"{slug}.mp4")
    make.run(["ffmpeg", "-y", "-v", "error", "-i", os.path.join(work, "video.mp4"), "-i", os.path.join(work, "voice.wav"),
              "-filter_complex", f"[1:a]aformat=channel_layouts=stereo,loudnorm=I={cfg['loudness_lufs']}:TP=-1.5:LRA=9[a]",
              "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
              "-movflags", "+faststart", "-shortest", out])
    make.write_post(script, cfg, os.path.join(ROOT, "out", f"{slug}_post.txt"))
    print(f"\n[FINI] {out}  ({make.duration(out):.1f}s)")


if __name__ == "__main__":
    main()
