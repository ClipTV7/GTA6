#!/usr/bin/env python3
"""GTA6 Studio : script.json -> vidéo verticale anglaise prête à poster.

Étapes : contrôle des règles -> voix (ElevenLabs ou voix de test) -> timing Whisper
-> sous-titres ASS -> segments visuels FFmpeg -> assemblage + mixage -> post.txt

Usage :
  python3 studio/make.py clips/<slug>               rendu complet (voix clone ElevenLabs)
  python3 studio/make.py clips/<slug> --test-voice  voix robot locale (gratuit, pour tester)
  python3 studio/make.py clips/<slug> --check       contrôle des règles uniquement
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OFFICIAL = os.path.join(ROOT, "assets", "official")
STATUSES = {"CONFIRMED", "RUMOR", "FALSE"}
BADGE = {"RUMOR": ("RUMOR", "&H0000D4FF", "&H00000000"),      # jaune, texte noir
         "FALSE": ("FAKE CLAIM", "&H003539E5", "&H00FFFFFF")}       # rouge, texte blanc


def die(msg):
    sys.exit(f"\n[ERREUR] {msg}")


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        die(f"commande échouée : {' '.join(cmd[:6])}...\n{r.stderr[-1500:]}")
    return r.stdout


def duration(path):
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "csv=p=0", path]).strip())


# ---------------------------------------------------------------- 1. règles
def load_facts():
    import csv, datetime
    path = os.path.join(ROOT, "research", "facts.csv")
    if not os.path.isfile(path):
        die("research/facts.csv introuvable : le tableau de vérité est obligatoire")
    return {r["id"]: r for r in csv.DictReader(open(path, encoding="utf-8"))}


def check(script, cfg, test_voice):
    import datetime
    errs, warns = [], []
    facts = load_facts()
    today = datetime.date.today()
    lines = script.get("lines") or []
    av = os.path.join(OFFICIAL, "AVOID.txt")
    avoid = {l.strip() for l in open(av, encoding="utf-8") if l.strip() and not l.startswith("#")} if os.path.isfile(av) else set()
    if not 3 <= len(lines) <= 14:
        errs.append(f"{len(lines)} lignes : il en faut entre 3 et 14")
    for i, ln in enumerate(lines, 1):
        tag = f"ligne {i}"
        # tableau de vérité : chaque ligne renvoie à un fait ; statut et source viennent du tableau
        fid = ln.get("fact")
        if isinstance(fid, list):  # plusieurs faits sur une ligne : statut le plus prudent, toutes les sources
            bad = [x for x in fid if x not in facts]
            if bad:
                errs.append(f"{tag} : faits inconnus {bad}")
                continue
            rank = {"CONFIRMED": 0, "RUMOR": 1, "FALSE": 2}
            worst = max((facts[x] for x in fid), key=lambda r: rank[r["status"]])
            ln["status"], ln["source"] = worst["status"], facts[fid[0]]["source"]
            ln["sources"] = [facts[x]["source"] for x in fid]
            fid = None
        if fid is None and isinstance(ln.get("fact"), list):
            pass
        elif fid == "CTA":
            ln.setdefault("status", "CONFIRMED")
            ln.setdefault("source", "n/a")
        elif fid not in facts:
            errs.append(f"{tag} : 'fact' doit être un id de research/facts.csv (ou CTA), reçu {fid!r}")
        else:
            f = facts[fid]
            if ln.get("status") and ln["status"] != f["status"]:
                errs.append(f"{tag} : status {ln['status']} mais le tableau dit {f['status']} pour {fid}")
            ln["status"], ln["source"] = f["status"], f["source"]
            try:
                age = (today - datetime.date.fromisoformat(f["last_checked"])).days
                if age > 2:
                    warns.append(f"{tag} : {fid} vérifié il y a {age} jours, relancer l'Éplucheur")
            except ValueError:
                warns.append(f"{tag} : {fid} sans date de vérification")
        if not ln.get("text", "").strip():
            errs.append(f"{tag} : texte vide")
        if ln.get("status") not in STATUSES:
            errs.append(f"{tag} : status doit être CONFIRMED, RUMOR ou FALSE")
        src = (ln.get("source") or "").strip()
        is_cta = i == len(lines)
        if not src or (src == "n/a" and not is_cta):
            errs.append(f"{tag} : source manquante (seul l'appel à l'action final peut avoir 'n/a')")
        elif src != "n/a" and not src.startswith("http"):
            errs.append(f"{tag} : la source doit être une URL")
        if ln.get("status") == "RUMOR" and not re.search(
                r"\b(reported|reportedly|rumou?r|allegedly|leak|insiders?|unconfirmed|claims?|could|may|might)\b",
                ln.get("text", ""), re.I):
            errs.append(f"{tag} : une RUMOR doit le dire dans la phrase (reportedly, rumor, insiders...)")
        if ln.get("status") == "FALSE" and not re.search(
                r"\b(fake|false|not true|not yet|myth|debunked|hoax|scam|lie|wrong|no such|doesn't exist|isn't|is not|never)\b",
                ln.get("text", ""), re.I):
            errs.append(f"{tag} : une info FALSE doit être démentie dans la phrase (fake, false, myth, hoax...)")
        if re.search(r"[éèêàùçôîœ]", ln.get("text", "") + ln.get("headline", "")):
            warns.append(f"{tag} : accents détectés, le texte doit être en anglais")
        v = ln.get("visual")
        if not v:
            errs.append(f"{tag} : visual manquant")
        else:
            p = os.path.realpath(os.path.join(OFFICIAL, v))
            if not p.startswith(os.path.realpath(OFFICIAL) + os.sep):
                errs.append(f"{tag} : visuel hors de assets/official (interdit) : {v}")
            elif not os.path.isfile(p):
                errs.append(f"{tag} : visuel introuvable : {v}")
            elif v in avoid:
                errs.append(f"{tag} : visuel interdit (assets/official/AVOID.txt) : {v}")
    if not test_voice and (cfg.get("tts") or {}).get("engine", "elevenlabs") == "elevenlabs" and "PUT_YOUR" in cfg["elevenlabs"]["voice_id"]:
        errs.append("config.json : renseigne elevenlabs.voice_id (ton clone) ou utilise --test-voice")
    music = cfg["music"].get("file")
    if music:
        mp = os.path.join(ROOT, music)
        if not os.path.isfile(mp):
            errs.append(f"musique introuvable : {music}")
        elif not os.path.isfile(os.path.join(os.path.dirname(mp), "LICENSE.txt")):
            errs.append("musique sans LICENSE.txt à côté : licence libre de droits non prouvée")
    for w in warns:
        print("[ATTENTION]", w)
    if errs:
        die("le script ne respecte pas les règles du studio :\n  - " + "\n  - ".join(errs))
    print(f"[OK] règles respectées ({len(lines)} lignes)")


# ---------------------------------------------------------------- 2. voix
def voice_id_for(script, cfg):
    """Voix du clip : "voice" dans script.json -> elevenlabs.voices[...] ; sinon la voix principale."""
    e = cfg["elevenlabs"]
    name = script.get("voice", "main")
    vid = (e.get("voices") or {}).get(name) or (e["voice_id"] if name == "main" else None)
    if not vid:
        die(f"voix '{name}' non configurée dans config.json (elevenlabs.voices)")
    return vid


def tts_elevenlabs(text, prev, nxt, cfg, out_mp3):
    e = cfg["elevenlabs"]
    body = {"text": text, "model_id": e["model_id"], "previous_text": prev, "next_text": nxt,
            "voice_settings": {"stability": e["stability"], "similarity_boost": e["similarity_boost"],
                               "style": e["style"], "speed": e["speed"]}}
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{cfg['_voice_id']}?output_format=mp3_44100_128",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r, open(out_mp3, "wb") as f:
            f.write(r.read())
    except urllib.error.HTTPError as err:
        die(f"ElevenLabs a refusé ({err.code}) : {err.read().decode()[:400]}")


_KOKORO = None


def tts_kokoro(text, cfg, out_wav):
    """Voix libre (Kokoro, licence Apache 2.0) : gratuite, illimitée, calculée sur la machine."""
    global _KOKORO
    import soundfile as sf
    from kokoro_onnx import Kokoro
    kd = os.path.join(ROOT, ".cache", "kokoro")
    if _KOKORO is None:
        if not os.path.isfile(os.path.join(kd, "model.onnx")):
            die("modèle Kokoro absent : lance bash studio/setup.sh")
        _KOKORO = Kokoro(os.path.join(kd, "model.onnx"), os.path.join(kd, "voices.npz"))
    k = cfg["tts"]
    mix = k.get("kokoro_mix") or {k.get("kokoro_voice", "am_fenrir"): 1.0}
    style = sum(w * _KOKORO.get_voice_style(v) for v, w in mix.items())
    samples, sr = _KOKORO.create(text, voice=style, speed=k.get("kokoro_speed", 1.1), lang=k.get("kokoro_lang", "en-us"))
    sf.write(out_wav, samples, sr)


def resolve_voice(script, cfg, test_voice):
    """Renseigne cfg['_engine'] et cfg['_voice_id'] (moteur de voix utilisé pour ce clip)."""
    engine = "test" if test_voice else (cfg.get("tts") or {}).get("engine", "elevenlabs")
    cfg["_engine"] = engine
    if engine == "elevenlabs":
        cfg["_voice_id"] = voice_id_for(script, cfg)
    elif engine == "kokoro":
        cfg["_voice_id"] = "kokoro:" + json.dumps(cfg["tts"].get("kokoro_mix") or cfg["tts"].get("kokoro_voice"), sort_keys=True)
    else:
        cfg["_voice_id"] = "test"


def make_voice(lines, cfg, work, test_voice):
    cache = os.path.join(ROOT, ".cache", "tts")
    os.makedirs(cache, exist_ok=True)
    wavs = []
    for i, ln in enumerate(lines):
        text = ln["text"].strip()
        prev = lines[i - 1]["text"] if i else ""
        nxt = lines[i + 1]["text"] if i + 1 < len(lines) else ""
        key = hashlib.sha1(json.dumps(["v4-tails", text, prev, nxt, test_voice, cfg.get("_voice_id"),
                                       cfg["elevenlabs"] if cfg.get("_engine") == "elevenlabs" else cfg.get("tts")],
                                      sort_keys=True).encode()).hexdigest()[:16]
        cached = os.path.join(cache, key + ".wav")
        if not os.path.isfile(cached):
            if test_voice:
                tmp = cached + ".raw.wav"
                run(["espeak-ng", "-v", "en-us", "-s", "170", "-w", tmp, text])
            elif cfg.get("_engine") == "kokoro":
                tmp = cached + ".kokoro.wav"
                parts = [p for p in re.split(r"(?<=\?)\s+", text) if p.strip()]
                if len(parts) > 1:  # vraie pause (0,35 s) après chaque question, avant la chute
                    import numpy as np
                    import soundfile as sf
                    chunks, sr = [], 24000
                    for n, part in enumerate(parts):
                        pf = f"{cached}.p{n}.wav"
                        tts_kokoro(part, cfg, pf)
                        a, sr = sf.read(pf)
                        os.remove(pf)
                        chunks += [a, np.zeros(int(0.2 * sr))]
                    sf.write(tmp, np.concatenate(chunks[:-1]), sr)
                else:
                    tts_kokoro(text, cfg, tmp)
            else:
                tmp = cached + ".mp3"
                tts_elevenlabs(text, prev, nxt, cfg, tmp)
            # coupe les silences de début/fin, mono 44.1k
            run(["ffmpeg", "-y", "-v", "error", "-i", tmp, "-af",
                 "silenceremove=start_periods=1:start_threshold=-55dB:start_silence=0.06,areverse,"
                 "silenceremove=start_periods=1:start_threshold=-42dB:start_silence=0.06,areverse",
                 "-ar", "44100", "-ac", "1", cached])
            os.remove(tmp)
        dst = os.path.join(work, f"voice_{i:02d}.wav")
        shutil.copy(cached, dst)
        wavs.append(dst)
        print(f"  voix {i + 1}/{len(lines)} : {duration(dst):.2f}s")
    return wavs


# ---------------------------------------------------------------- 3. Whisper
NUMW = dict(zip("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split(),
                [str(n) for n in range(21)]))
ROMAN = {"iii": "3", "iv": "4", "v": "5", "vi": "6"}


def canon(w, heard=False):
    """Forme comparable d'un mot : chiffres = lettres (five = 5), GTA V = 5, $20 = 20 dollars."""
    raw = w.strip().lower()
    t = re.sub(r"[^a-z0-9]", "", raw)
    if heard and raw.startswith("$") and t.isdigit():
        return t + "dollars"
    if heard and raw.rstrip(".,!?").endswith("%") and t.isdigit():
        return t + "percent"
    if heard and raw.strip(".,!?") == "%":
        return "percent"
    if heard and t in ROMAN:
        return ROMAN[t]
    t = {"isnt": "isnot", "dont": "donot", "doesnt": "doesnot", "cant": "cannot", "wont": "willnot",
         "didnt": "didnot", "hasnt": "hasnot", "havent": "havenot", "wasnt": "wasnot", "arent": "arenot",
         "taketwo": "take2", "taketwos": "take2s"}.get(t, t)
    return NUMW.get(t, t)


def word_timings(wavs, lines, cfg, gap):
    from faster_whisper import WhisperModel
    model = WhisperModel(cfg["whisper_model"], device="cpu", compute_type="int8")
    words, segs, t0 = [], [], 0.0
    for i, (wav, ln) in enumerate(zip(wavs, lines)):
        d = duration(wav)
        seg_len = d + gap + (0.3 if i == len(lines) - 1 else 0)
        segs.append((t0, seg_len))
        import numpy as np  # décodage via ffmpeg (évite les soucis de version PyAV)
        pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", wav, "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                             capture_output=True, check=True).stdout
        audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
        result, _ = model.transcribe(audio, language="en", word_timestamps=True, beam_size=5)
        heard = [w for s in result for w in s.words]
        script_words = ln["text"].split()
        nz = lambda x: canon(x)
        heard_join = "".join(canon(w.word, heard=True) for w in heard)
        hj2 = heard_join.replace("dollars", "").replace("percent", "")
        missing = [w for w in script_words if nz(w) and nz(w).rstrip("s") not in heard_join and nz(w).rstrip("s") not in hj2]
        heard_txt = "".join(w.word for w in heard)
        hq = [(nz(w.word), w.word.strip().endswith("?")) for w in heard]
        for sw in [w for w in script_words if w.endswith("?")]:  # chaque question doit être entendue comme une question
            k = nz(sw)
            if not any(h == k and q for h, q in hq):
                print(f"  [INTONATION ?] ligne {i + 1} : « {sw} » n'est pas entendu comme une question : « {heard_txt.strip()} »")
        if heard and script_words and not nz(script_words[0]).startswith(nz(heard[0].word)) and not (nz(script_words[0]) == "6" or nz(heard[0].word) == "vi"):
            print(f"  [VOIX ?] ligne {i + 1} : premier mot « {script_words[0]} » entendu « {heard[0].word.strip()} » (p={heard[0].probability:.2f})")
        elif heard and heard[0].probability < 0.4:
            print(f"  [VOIX ?] ligne {i + 1} : premier mot « {script_words[0]} » peu net (p={heard[0].probability:.2f})")
        if missing:  # contrôle automatique : un mot que Whisper n'entend pas est souvent mal prononcé
            print(f"  [VOIX ?] ligne {i + 1} : Whisper n'entend pas « {' '.join(missing)} » (entendu : {' '.join(w.word.strip() for w in heard)})")
        if heard and len(heard) == len(script_words):
            times = [(w.start, w.end) for w in heard]
        else:  # Whisper a entendu autrement : on répartit le texte du script sur la parole détectée
            a = heard[0].start if heard else 0.0
            b = heard[-1].end if heard else d
            total = sum(len(w) + 1 for w in script_words)
            times, c = [], a
            for w in script_words:
                span = (b - a) * (len(w) + 1) / total
                times.append((c, c + span))
                c += span
        for w, (s, e) in zip(script_words, times):
            words.append({"w": w, "s": t0 + s, "e": t0 + e, "line": i})
        t0 += seg_len
    return words, segs


# ---------------------------------------------------------------- 4. sous-titres
def ass_time(t):
    t = max(t, 0)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def esc(s):
    return s.replace("{", "(").replace("}", ")").replace("\\", "/")


def build_ass(words, segs, lines, cfg, path):
    font = cfg["font"]
    total = segs[-1][0] + segs[-1][1]
    out = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{font},92,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,3,5,60,60,0,1",
        f"Style: Head,{font},74,&H00000000,&H00000000,&H00FFFFFF,&H00000000,-1,0,0,0,100,100,0,0,3,18,0,5,60,60,0,1",
        f"Style: Badge,{font},50,&H00000000,&H00000000,&H0000D4FF,&H00000000,-1,0,0,0,100,100,2,0,3,12,0,5,60,60,0,1",
        f"Style: Small,{font},22,&H40FFFFFF,&H30FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,5,60,60,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    ev = lambda layer, s, e, st, txt: out.append(
        f"Dialogue: {layer},{ass_time(s)},{ass_time(e)},{st},,0,0,0,,{txt}")
    # mentions permanentes (sous les onglets TikTok/YouTube, hors zones d'interface)
    ev(0, 0, total, "Small", r"{\pos(540,222)}" + esc(cfg["ai_label_text"]))
    ev(0, 0, total, "Small", r"{\pos(540,250)}" + esc(cfg["credit_text"]))
    # titres et badges par ligne
    for i, ln in enumerate(lines):
        s, d = segs[i]
        if ln.get("headline"):
            # 1re ligne : titre visible dès la 1re image (accroche), sans fondu
            anim = r"{\pos(540,400)\fscx118\fscy118\t(0,180,\fscx100\fscy100)}" if i == 0 else \
                r"{\pos(540,400)\fad(60,0)\t(0,120,\fscx104\fscy104)\t(120,220,\fscx100\fscy100)}"
            ev(1, s, s + d, "Head", anim + esc(ln["headline"].upper()))
        if ln["status"] in BADGE:
            label, box, txt = BADGE[ln["status"]]
            label = ln.get("badge", label)
            ev(1, s, s + d, "Badge", r"{\pos(540,%d)\3c%s\1c%s}%s" % (520 if ln.get("headline") else 400, box, txt, label))
            if ln["status"] == "FALSE":  # le badge vise l'affirmation, pas l'image officielle
                ev(1, s, s + d, "Small", r"{\pos(540,%d)}Background: real GTA 6 image (Rockstar)" % (590 if ln.get("headline") else 470))
    # sous-titres 1 à 3 mots, mot prononcé en jaune
    chunks, cur = [], []
    for w in words:
        if cur and (len(cur) == 3 or cur[-1]["line"] != w["line"] or re.search(r"[.,!?;:]$", cur[-1]["w"])):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    for ci, ch in enumerate(chunks):
        end_chunk = chunks[ci + 1][0]["s"] if ci + 1 < len(chunks) and chunks[ci + 1][0]["line"] == ch[0]["line"] \
            else ch[-1]["e"] + 0.25
        for wi, w in enumerate(ch):
            s = w["s"]
            e = ch[wi + 1]["s"] if wi + 1 < len(ch) else end_chunk
            txt = " ".join((r"{\fscx115\fscy115}" + esc(x["w"].upper()) + r"{\fscx100\fscy100}") if x is w
                           else esc(x["w"].upper()) for x in ch)
            pop = r"{\fscx112\fscy112\t(0,90,\fscx100\fscy100)}" if wi == 0 else ""
            ev(2, s, max(e, s + 0.05), "Cap", r"{\pos(540,1180)}" + pop + txt)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


# ---------------------------------------------------------------- 5. visuels
def render_segment(ln, dur, cfg, out):
    W, H, fps = cfg["video"]["width"], cfg["video"]["height"], cfg["video"]["fps"]
    src = os.path.realpath(os.path.join(OFFICIAL, ln["visual"]))
    focus = float(ln.get("focus", 0.5))
    frames = max(int(round(dur * fps)), 1)
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(fps), "-an"]
    if src.lower().endswith(".mp4"):  # boucle vidéo officielle (muette)
        vf = (f"scale=w='if(gt(a,{W}/{H}),-2,{W})':h='if(gt(a,{W}/{H}),{H},-2)',"
              f"crop={W}:{H}:(iw-{W})/2:(ih-{H})*{focus},setsar=1")
        run(["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-i", src, "-t", f"{dur:.3f}",
             "-vf", vf] + enc + [out])
    elif ln.get("fit") == "blur":  # image entière sur fond flouté
        fc = (f"[0:v]scale=-2:{H},crop={W}:{H},boxblur=30:2,eq=brightness=-0.12[bg];"
              f"[0:v]scale={int(W * 1.0)}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2-80,"
              f"zoompan=z='1+0.04*on/{frames}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={frames}:s={W}x{H}:fps={fps}")
        run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", src, "-frames:v", str(frames),
             "-filter_complex", fc] + enc + [out])
    else:  # recadrage vertical + zoom lent (Ken Burns), préparé en 2x pour éviter les saccades
        vf = (f"scale=-2:{H * 2},crop={W * 2}:{H * 2}:(iw-{W * 2})*{focus}:0,"
              f"zoompan=z='1+0.07*on/{frames}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={frames}:s={W}x{H}:fps={fps}")
        run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", src, "-frames:v", str(frames),
             "-vf", vf] + enc + [out])


# ---------------------------------------------------------------- 6. post
def write_post(script, cfg, path):
    lines = script["lines"]
    srcs = list(dict.fromkeys(u for l in lines for u in (l.get("sources") or [l["source"]]) if u.startswith("http")))
    rumors = [l["text"] for l in lines if l["status"] == "RUMOR"]
    tags = script.get("hashtags", ["#GTA6", "#GTAVI", "#GTA"])
    yt_title = script.get("yt_title", script.get("title", script["slug"]))
    if "#shorts" not in yt_title.lower():
        yt_title = (yt_title + " #Shorts")[:100]
    desc = script.get("description", lines[0]["text"])
    credit = cfg["credit_text"] + ". Grand Theft Auto VI is a trademark of Take-Two Interactive."
    rumor_txt = ([script["rumor_note"], ""] if script.get("rumor_note") else
                 (["Unconfirmed (rumor): " + " / ".join(rumors), ""] if rumors else []))
    tk = script.get("tiktok_caption", desc)
    txt = ["===== YOUTUBE SHORTS =====",
           "TITRE :", yt_title, "",
           "DESCRIPTION :", desc, ""] + rumor_txt + ["Sources:"] + [f"- {u}" for u in srcs] + [
           "", credit, "", " ".join(tags + ["#Shorts"]), "",
           "===== TIKTOK =====",
           "LÉGENDE :", f"{tk} {' '.join(tags)}", "",
           "(TikTok n'affiche pas les liens : les sources sont dans la description YouTube.)", "",
           "===== CHECKLIST AVANT PUBLICATION =====",
           "[ ] YouTube : « Non, elle n'est pas conçue pour les enfants », visibilité Publique, publier maintenant",
           "[ ] TikTok : ajouter un son de la bibliothèque TikTok adapté au sujet ou à l'ambiance, publier maintenant",
           "[ ] Musique : uniquement la bibliothèque libre de droits de l'appli (ou aucune)",
           "[ ] Infos RUMOR / dates : toujours d'actualité ?"]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(txt) + "\n")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("clip_dir")
    ap.add_argument("--test-voice", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(ROOT, "config.json")))
    clip_dir = os.path.abspath(a.clip_dir)
    script = json.load(open(os.path.join(clip_dir, "script.json")))
    slug = script["slug"]
    check(script, cfg, a.test_voice)
    if a.check:
        return
    work = os.path.join(clip_dir, "build")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    lines, gap = script["lines"], cfg["gap_seconds"]
    resolve_voice(script, cfg, a.test_voice)

    print(f"[1/5] voix ({cfg['_engine']})")
    wavs = make_voice(lines, cfg, work, a.test_voice)

    print("[2/5] timing des mots (Whisper)")
    words, segs = word_timings(wavs, lines, cfg, gap)

    print("[3/5] sous-titres")
    ass = os.path.join(work, "subs.ass")
    build_ass(words, segs, lines, cfg, ass)

    print("[4/5] visuels")
    parts = []
    for i, (ln, (s, d)) in enumerate(zip(lines, segs)):
        p = os.path.join(work, f"seg_{i:02d}.mp4")
        render_segment(ln, d, cfg, p)
        parts.append(p)
    with open(os.path.join(work, "concat.txt"), "w") as f:
        f.writelines(f"file '{p}'\n" for p in parts)
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", os.path.join(work, "concat.txt"),
         "-c", "copy", os.path.join(work, "video.mp4")])
    # piste voix calée sur les segments
    vparts = []
    for i, (wav, (s, d)) in enumerate(zip(wavs, segs)):
        p = os.path.join(work, f"vpad_{i:02d}.wav")
        run(["ffmpeg", "-y", "-v", "error", "-i", wav, "-af", f"apad=whole_dur={d:.3f}", "-ar", "44100", "-ac", "1", p])
        vparts.append(p)
    with open(os.path.join(work, "vconcat.txt"), "w") as f:
        f.writelines(f"file '{p}'\n" for p in vparts)
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", os.path.join(work, "vconcat.txt"),
         os.path.join(work, "voice.wav")])

    print("[5/5] assemblage + mixage")
    os.makedirs(os.path.join(ROOT, "out"), exist_ok=True)
    out = os.path.join(ROOT, "out", f"{slug}.mp4")
    lufs = cfg["loudness_lufs"]
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", os.path.join(work, "video.mp4"), "-i", os.path.join(work, "voice.wav")]
    ass_f = ass.replace("\\", "/").replace(":", r"\:")
    music = cfg["music"].get("file")
    if music:
        cmd += ["-stream_loop", "-1", "-i", os.path.join(ROOT, music)]
        fc = (f"[0:v]ass='{ass_f}'[v];[2:a]volume={cfg['music']['volume_db']}dB,aformat=channel_layouts=stereo[m];"
              f"[1:a]aformat=channel_layouts=stereo,asplit[vo][sc];[m][sc]sidechaincompress=threshold=0.05:ratio=6[md];"
              f"[vo][md]amix=inputs=2:duration=first:normalize=0,loudnorm=I={lufs}:TP=-1.5:LRA=9[a]")
    else:
        fc = f"[0:v]ass='{ass_f}'[v];[1:a]aformat=channel_layouts=stereo,loudnorm=I={lufs}:TP=-1.5:LRA=9[a]"
    cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium",
            "-crf", "19", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", "-shortest", out]
    run(cmd)
    write_post(script, cfg, os.path.join(ROOT, "out", f"{slug}_post.txt"))
    print(f"\n[FINI] {out}  ({duration(out):.1f}s)\n       {os.path.join(ROOT, 'out', slug + '_post.txt')}")


if __name__ == "__main__":
    main()
