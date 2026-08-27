#!/usr/bin/env python3
"""Put a talk, lecture or recorded stream into an agent's knowledge base.

Two things this deliberately does NOT do.

It does not write to reference/. That tree is codified rules retrieved by exact
headword or citation - a statute, a rule set, a dictionary. A recorded talk has
no citations to key on, and filing commentary where the agent looks for
authority is how an opinion gets read back as law.

It does not claim the material is true. `--stance` is recorded with every
chunk and shown to the model alongside the text, because CLAUDE.md's rule is
that nothing reaches a model as authority without its provenance attached.

Captions first, audio only if there are none: a 110-minute transcription on a
CPU box costs hours, and the auto-captions are already there.

    ingest_media.py <url|file> --agent legal_agent \
        --title "..." --stance commentary --source "..."
"""
import argparse, json, os, re, sys, subprocess, tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STANCES = ("authority", "commentary", "advocacy", "primary_source", "unknown")


def fetch_captions(url, lang="en"):
    """Auto-captions as plain text, or None."""
    try:
        import yt_dlp
    except ImportError:
        sys.exit("yt-dlp not installed:  pip install yt-dlp")
    out = tempfile.mkdtemp(prefix="mycelial_caps_")
    opts = {"quiet": True, "no_warnings": True, "skip_download": True,
            "writeautomaticsub": True, "writesubtitles": True,
            "subtitleslangs": [lang, f"{lang}-orig", f"{lang}.*"],
            "subtitlesformat": "vtt",
            "outtmpl": os.path.join(out, "%(id)s.%(ext)s")}
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=True)
    vtts = [f for f in os.listdir(out) if f.endswith(".vtt")]
    if not vtts:
        return None, info
    raw = open(os.path.join(out, vtts[0]), errors="replace").read()
    return vtt_to_text(raw), info


def vtt_to_text(raw):
    """WEBVTT to prose. Auto-captions repeat each line as a rolling window, so
    the same sentence appears two or three times; dedupe on the way out or the
    cache scores a transcript three times heavier than it should."""
    lines, seen, out = raw.splitlines(), set(), []
    for ln in lines:
        ln = ln.strip()
        if (not ln or ln.startswith(("WEBVTT", "Kind:", "Language:", "NOTE"))
                or "-->" in ln or re.fullmatch(r"\d+", ln)):
            continue
        ln = re.sub(r"<[^>]+>", "", ln).strip()
        if ln and ln not in seen:
            seen.add(ln)
            out.append(ln)
    return " ".join(out)


def transcribe(path):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("faster-whisper not installed and no captions were available")
    print("  no captions - transcribing locally (slow on CPU)...")
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(path)
    return " ".join(s.text.strip() for s in segments)


def chunk(text, size=1800):
    """Paragraph-sized pieces. A 110-minute transcript as one blob is a single
    cache entry that matches everything and locates nothing."""
    words, buf, out = text.split(), [], []
    for w in words:
        buf.append(w)
        if sum(len(x) + 1 for x in buf) >= size:
            out.append(" ".join(buf))
            buf = []
    if buf:
        out.append(" ".join(buf))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="a URL, or a local audio/video file")
    ap.add_argument("--agent", required=True, help="e.g. legal_agent")
    ap.add_argument("--title", required=True)
    ap.add_argument("--stance", required=True, choices=STANCES,
                    help="What this material IS, judged from its CONTENT. Recorded with "
                         "every chunk and shown to the model. If you have not read it, "
                         "the honest value is 'unknown' - see --why-that-stance.")
    ap.add_argument("--why-that-stance", default="",
                    help="One line of evidence FROM THE TEXT for the stance you chose. "
                         "Required for anything other than 'unknown', because a stance set "
                         "from a title or a channel name launders a guess into metadata the "
                         "reasoning layer trusts.")
    ap.add_argument("--source", required=True, help="Provenance and rights")
    ap.add_argument("--lang", default="en")
    args = ap.parse_args()

    if args.stance != "unknown" and not args.why_that_stance.strip():
        sys.exit(
            f"  --stance {args.stance} needs --why-that-stance: one line of evidence from\n"
            "  the text itself. A stance taken from the title, the channel or the filename\n"
            "  is a guess wearing the costume of provenance. If you have not read it, pass\n"
            "  --stance unknown, which is a known gap rather than a wrong answer.")

    info = {}
    if re.match(r"https?://", args.target):
        text, info = fetch_captions(args.target, args.lang)
        if not text:
            sys.exit("no captions for that video; download the audio and pass the file")
    else:
        if not os.path.exists(args.target):
            sys.exit(f"not found: {args.target}")
        text = transcribe(args.target)

    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 500:
        sys.exit(f"only {len(text)} characters recovered - refusing to file that as a transcript")

    chunks = chunk(text)
    root = os.path.join(BASE, "knowledge_base", args.agent, "transcripts")
    os.makedirs(root, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", args.title.lower()).strip("_")[:60]
    path = os.path.join(root, f"{slug}.md")

    header = [f"# {args.title}", ""]
    header.append(f"**Stance: {args.stance.upper()}** — this is "
                  + {"authority": "binding or persuasive authority.",
                     "commentary": "commentary about the law, not the law.",
                     "advocacy": "an argument being made, recorded as an argument. "
                                 "It is not a statement of what the law is.",
                     "primary_source": "a primary source document.",
                     "unknown": "of unestablished standing."}[args.stance])
    header.append("")
    header.append(f"**Source:** {args.source}")
    if args.why_that_stance.strip():
        header.append("")
        header.append(f"**Why that stance:** {args.why_that_stance.strip()}")
    if info.get("duration"):
        header.append(f"**Runtime:** {round(info['duration']/60)} min")
    if info.get("uploader"):
        header.append(f"**Speaker/channel:** {info['uploader']}")
    header += ["", "---", ""]

    with open(path, "w") as fh:
        fh.write("\n".join(header))
        for i, c in enumerate(chunks, 1):
            fh.write(f"\n## Part {i}\n\n{c}\n")

    print(f"  {len(text):,} characters, {len(chunks)} chunks -> {path}")
    print(f"  stance recorded as: {args.stance}")
    print(f"  next:  curl -X POST localhost:<port>/execute "
          f"-d '{{\"task\":\"refresh_cache\",\"args\":{{}}}}'")


if __name__ == "__main__":
    main()
