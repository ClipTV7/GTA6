#!/usr/bin/env python3
"""Télécharge UNIQUEMENT des visuels officiels Rockstar dans assets/official/.
- pack de captures officiel (zip) -> assets/official/screenshots/
- boucles vidéo muettes du site rockstargames.com/VI -> assets/official/loops/
Tout domaine autre que Rockstar est refusé.
"""
import io, os, re, sys, urllib.parse, urllib.request, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OFF = os.path.join(ROOT, "assets", "official")
ALLOWED = {"www.rockstargames.com", "media-rockstargames-com.akamaized.net"}
UA = {"User-Agent": "Mozilla/5.0"}


def get(url):
    host = urllib.parse.urlparse(url).hostname
    if host not in ALLOWED:
        sys.exit(f"[REFUS] domaine non officiel : {host}")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r:
        return r.read()


def main():
    os.makedirs(os.path.join(OFF, "screenshots"), exist_ok=True)
    os.makedirs(os.path.join(OFF, "loops"), exist_ok=True)
    html = get("https://www.rockstargames.com/VI").decode("utf-8", "ignore")
    html += get("https://www.rockstargames.com/VI/media/screenshots").decode("utf-8", "ignore")

    print("captures officielles...")
    z = zipfile.ZipFile(io.BytesIO(get(
        "https://media-rockstargames-com.akamaized.net/VI/downloads/screenshots/GTAVI_Screenshots.zip")))
    for m in z.infolist():
        name = os.path.normpath(m.filename)
        if m.is_dir() or name.startswith("..") or os.path.isabs(name) or not name.lower().endswith((".jpg", ".png")):
            continue
        dst = os.path.join(OFF, "screenshots", name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(z.read(m))

    print("boucles vidéo du site...")
    known = set(os.listdir(os.path.join(OFF, "loops")))
    for path in sorted(set(re.findall(r"/VI/_next/static/media/1280_hvec[^\"' ]*\.mp4", html))):
        name = "site_" + re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(path))
        if name not in known:
            with open(os.path.join(OFF, "loops", name), "wb") as f:
                f.write(get("https://www.rockstargames.com" + path))
            print("  +", name)

    # index pour le Scénariste
    rows = ["# Visuels officiels Rockstar (source : rockstargames.com)", "",
            "Crédit : © Rockstar Games / Take-Two Interactive", "", "| fichier | sujet |", "|---|---|"]
    for d, _, files in sorted(os.walk(OFF)):
        for fn in sorted(files):
            if fn.lower().endswith((".jpg", ".png", ".mp4")):
                rel = os.path.relpath(os.path.join(d, fn), OFF)
                rows.append(f"| {rel} | {os.path.basename(d)} |")
    open(os.path.join(OFF, "INDEX.md"), "w").write("\n".join(rows) + "\n")
    print("[OK] assets/official/INDEX.md à jour")


if __name__ == "__main__":
    main()
