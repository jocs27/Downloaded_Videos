#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Local video downloader - single file (server + HTML/CSS/JS UI, AR / EN / FR).
Run:  python yt_downloader.py     (opens the browser automatically)
Needs: Python 3.8+ ; ffmpeg (for best quality merge + mp3). yt-dlp is auto-installed.
Use only for content you have the right to download.
"""
import base64, errno, glob, hmac, json, mimetypes, os, sys, tempfile, threading, time, uuid, shutil, webbrowser, subprocess
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote


WINDOWS_PYTHON = os.path.expanduser(r"~\AppData\Local\Python\pythoncore-3.14-64\python.exe")
WINDOWS_SITE_PACKAGES = os.path.expanduser(r"~\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages")

if os.path.exists(WINDOWS_SITE_PACKAGES) and WINDOWS_SITE_PACKAGES not in sys.path:
    sys.path.insert(0, WINDOWS_SITE_PACKAGES)


def locate_windows_python():
    explicit = [
        WINDOWS_PYTHON,
        os.path.expanduser(r"~\AppData\Local\Python\pythoncore-3.13-64\python.exe"),
        os.path.expanduser(r"~\AppData\Local\Python\pythoncore-3.12-64\python.exe"),
        os.path.expanduser(r"~\AppData\Local\Programs\Python\Python314\python.exe"),
        os.path.expanduser(r"~\AppData\Local\Programs\Python\Python313\python.exe"),
        os.path.expanduser(r"~\AppData\Local\Programs\Python\Python312\python.exe"),
    ]
    candidates = []
    for exe in [shutil.which("py"), os.environ.get("PYTHON_EXE"), shutil.which("python"), shutil.which("python3"), sys.executable] + explicit:
        if exe and os.path.exists(exe) and exe not in candidates:
            candidates.append(exe)
    return candidates


def ensure_yt_dlp():
    try:
        import yt_dlp
        return yt_dlp
    except ImportError:
        if os.path.exists(WINDOWS_SITE_PACKAGES) and WINDOWS_SITE_PACKAGES not in sys.path:
            sys.path.insert(0, WINDOWS_SITE_PACKAGES)
        try:
            import yt_dlp
            return yt_dlp
        except ImportError:
            pass

        python_candidates = locate_windows_python()
        for exe in python_candidates:
            if not exe:
                continue
            try:
                subprocess.check_call([exe, "-m", "pip", "install", "-U", "yt-dlp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if os.path.exists(WINDOWS_SITE_PACKAGES) and WINDOWS_SITE_PACKAGES not in sys.path:
                    sys.path.insert(0, WINDOWS_SITE_PACKAGES)
                import yt_dlp
                return yt_dlp
            except Exception:
                pass

        if os.path.exists(WINDOWS_PYTHON):
            try:
                subprocess.check_call([WINDOWS_PYTHON, "-m", "pip", "install", "-U", "yt-dlp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if os.path.exists(WINDOWS_SITE_PACKAGES) and WINDOWS_SITE_PACKAGES not in sys.path:
                    sys.path.insert(0, WINDOWS_SITE_PACKAGES)
                import yt_dlp
                return yt_dlp
            except Exception:
                pass
        raise RuntimeError("yt-dlp not installed. Run: py -m pip install -U yt-dlp")


def find_ffmpeg():
    for exe in ["ffmpeg", "ffmpeg.exe"]:
        p = shutil.which(exe)
        if p:
            return p
    paths = []
    for base in [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.path.expanduser(r"~\AppData\Local\Programs"),
    ]:
        if base:
            paths.append(base)
    for base in paths:
        for root, _, files in os.walk(base):
            for f in files:
                if f.lower() == "ffmpeg.exe":
                    return os.path.join(root, f)
    return None


yt_dlp = ensure_yt_dlp()
HAS_FFMPEG = find_ffmpeg() is not None
if HAS_FFMPEG:
    os.environ["FFMPEG_PATH"] = find_ffmpeg()
JOBS = {}
PUBLIC_MODE = (
    os.environ.get("PUBLIC_MODE") == "1"
    or os.environ.get("RENDER", "").lower() == "true"
    or bool(os.environ.get("RENDER_SERVICE_ID"))
)
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
DOWNLOAD_ROOT = os.path.abspath(os.environ.get(
    "DOWNLOAD_ROOT", os.path.join(tempfile.gettempdir(), "video-downloader")
))


# ---------------------------------------------------------------- logic
def fmt_size(f, dur):
    s = f.get("filesize") or f.get("filesize_approx")
    if not s and f.get("tbr") and dur:
        s = int(f["tbr"] * 1000 / 8 * dur)
    return int(s or 0)


def get_info(url):
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "extract_flat": False,
    }
    with yt_dlp.YoutubeDL(opts) as y:
        d = y.extract_info(url, download=False)

    if not isinstance(d, dict):
        raise ValueError("Unable to extract video information from the URL.")

    dur = d.get("duration") or 0
    fmts = d.get("formats") or []

    audios = [f for f in fmts if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    best_a = max(audios, key=lambda f: (f.get("abr") or f.get("tbr") or 0), default=None)
    a_size = fmt_size(best_a, dur) if best_a else 0

    by_h = {}
    for f in fmts:
        if f.get("vcodec") in (None, "none") or not f.get("height"):
            continue
        prog = f.get("acodec") not in (None, "none")
        if not HAS_FFMPEG and not prog:
            continue
        h = f["height"]
        score = (f.get("tbr") or 0, f.get("fps") or 0, f.get("filesize") or f.get("filesize_approx") or 0)
        if h not in by_h or score > by_h[h][0]:
            by_h[h] = (score, f, prog)

    videos = []
    for h in sorted(by_h, reverse=True):
        _, f, prog = by_h[h]
        sv = fmt_size(f, dur)
        videos.append({
            "id": f["format_id"], "h": h, "fps": int(f.get("fps") or 0),
            "codec": (f.get("vcodec") or "").split(".")[0], "ext": f.get("ext"),
            "prog": prog, "size_video": sv,
            "size_both": sv if prog else sv + a_size,
        })

    audio_opts = []
    if best_a:
        audio_opts.append({"id": "best", "label": "Original (" + str(best_a.get("ext")) + ")",
                           "size": a_size, "abr": int(best_a.get("abr") or 0)})
        if HAS_FFMPEG:
            audio_opts.append({"id": "mp3", "label": "MP3 192k", "size": int(dur * 192000 / 8), "abr": 192})

    return {
        "title": d.get("title"), "thumb": d.get("thumbnail"), "duration": dur,
        "channel": d.get("uploader"), "videos": videos, "audios": audio_opts,
        "ffmpeg": HAS_FFMPEG,
    }


def normalize_folder(folder):
    folder = (folder or "").strip()
    if not folder:
        folder = os.path.join(os.path.expanduser("~"), "Downloads")
    folder = os.path.abspath(os.path.expanduser(folder))
    os.makedirs(folder, exist_ok=True)
    return folder


class JobCancelled(Exception):
    pass


def cleanup_cancelled_job(job):
    folder = os.path.normcase(os.path.abspath(job["folder"]))
    paths = set(job["_cleanup_paths"]) | set(job["_started_files"]) | set(job["_completed_files"])
    candidates = set()
    for path in paths:
        path = os.path.abspath(path)
        try:
            inside_folder = os.path.normcase(os.path.commonpath((folder, path))) == folder
        except ValueError:
            inside_folder = False
        if not inside_folder:
            raise ValueError("Refusing to delete a download file outside its destination folder.")
        candidates.add(path)
        if path.endswith(".part"):
            base_path = path[:-5]
            candidates.add(base_path + ".ytdl")
            candidates.add(path + ".ytdl")
            candidates.update(glob.glob(path + "-Frag*"))
    for candidate in candidates:
        try:
            inside_folder = os.path.normcase(os.path.commonpath((folder, os.path.abspath(candidate)))) == folder
        except ValueError:
            inside_folder = False
        if not inside_folder:
            raise ValueError("Refusing to delete a download file outside its destination folder.")
        if os.path.isfile(candidate) or os.path.islink(candidate):
            for attempt in range(20):
                try:
                    os.remove(candidate)
                    break
                except OSError as e:
                    file_is_locked = getattr(e, "winerror", None) in (32, 33) or e.errno == errno.EBUSY
                    if not file_is_locked or attempt == 19:
                        raise
                    time.sleep(0.1)


def run_job(jid, p):
    job = JOBS[jid]

    def hook(d):
        with job["_control"]:
            filename = d.get("filename")
            tmpfilename = d.get("tmpfilename")
            if tmpfilename:
                job["_cleanup_paths"].add(os.path.abspath(tmpfilename))
            if filename and d.get("status") == "downloading":
                job["_started_files"].add(os.path.abspath(filename))
            elif filename and d.get("status") == "finished":
                filename = os.path.abspath(filename)
                if filename in job["_started_files"]:
                    job["_completed_files"].add(filename)

            if job["_cancel_requested"]:
                raise JobCancelled()
            if job["_pause_requested"]:
                job.update(status="paused", speed=0, eta=None)
                while job["_pause_requested"] and not job["_cancel_requested"]:
                    job["_control"].wait()
                if job["_cancel_requested"]:
                    raise JobCancelled()
                job.update(status="downloading")
            if d.get("status") == "downloading":
                tot = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                got = d.get("downloaded_bytes") or 0
                job.update(status="downloading", done=got, total=tot,
                           speed=d.get("speed") or 0, eta=d.get("eta"),
                           pct=(got / tot * 100) if tot else 0)

    def postprocessor_hook(d):
        with job["_control"]:
            if job["_cancel_requested"]:
                raise JobCancelled()
            if d.get("status") in ("started", "processing"):
                job["status"] = "processing"

    try:
        folder = normalize_folder(p.get("folder"))
        job["folder"] = folder
        job["title"] = p.get("title") or "Video"
        mode = p["mode"]
        ffmpeg_path = find_ffmpeg()
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "progress_hooks": [hook],
            "postprocessor_hooks": [postprocessor_hook],
            "paths": {"home": folder},
            "outtmpl": "%(title).150B.%(ext)s",
            "restrictfilenames": False,
            "windowsfilenames": True,
            "continuedl": True,
            "socket_timeout": 30,
            "retries": 10,
            "fragment_retries": 10,
            "nocheckcertificate": True,
            "http_headers": {"User-Agent": "Mozilla/5.0"},
        }
        if ffmpeg_path:
            opts["ffmpeg_location"] = os.path.dirname(ffmpeg_path)

        if mode == "audio":
            opts["format"] = "bestaudio/best"
            if p.get("audio") == "mp3" and HAS_FFMPEG:
                opts["postprocessors"] = [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192"
                }]
        else:
            vid = p["video_id"]
            if p.get("prog"):
                opts["format"] = vid
            elif mode == "video":
                opts["format"] = vid
            else:
                opts["format"] = vid + "+bestaudio"
                opts["merge_output_format"] = "mkv" if not p.get("avc") else "mp4"
                if p.get("avc"):
                    opts["format"] = vid + "+bestaudio[ext=m4a]/" + vid + "+bestaudio"

        with yt_dlp.YoutubeDL(opts) as y:
            with job["_control"]:
                if job["_cancel_requested"]:
                    raise JobCancelled()
            res = y.extract_info(p["url"], download=True)
        path = ""
        path_candidates = [res.get("filepath"), res.get("_filename")]
        requested_downloads = res.get("requested_downloads") or []
        path_candidates.extend(
            item.get("filepath")
            for item in requested_downloads
            if isinstance(item, dict)
        )
        path = next(
            (os.path.abspath(candidate) for candidate in path_candidates
             if candidate and os.path.isfile(candidate)),
            "",
        )
        with job["_control"]:
            if job["_cancel_requested"]:
                raise JobCancelled()
            if PUBLIC_MODE and not path:
                raise FileNotFoundError("Downloaded file could not be located on the server.")
            job["file_path"] = path
            job.update(status="done", pct=100, path=path or folder)
    except JobCancelled:
        try:
            cleanup_cancelled_job(job)
        except OSError as e:
            job.update(status="error", error="Cancellation cleanup failed: " + str(e)[:400])
        except ValueError as e:
            job.update(status="error", error=str(e)[:400])
        else:
            job.update(status="cancelled", path=job.get("folder", ""))
    except Exception as e:
        job.update(status="error", error=str(e)[:500])


def pick_folder():
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(parent=root)
        root.update(); root.destroy()
        return path or ""
    except Exception:
        default = os.path.join(os.path.expanduser("~"), "Downloads")
        os.makedirs(default, exist_ok=True)
        return default


# ---------------------------------------------------------------- http
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        try:
            if not isinstance(body, bytes):
                body = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    def _authorized(self):
        if not PUBLIC_MODE:
            return True
        if not APP_PASSWORD:
            self._send(503, {"error": "Set APP_PASSWORD in the hosting environment before starting the public service."})
            return False
        expected = "Basic " + base64.b64encode(
            ("downloader:" + APP_PASSWORD).encode("utf-8")
        ).decode("ascii")
        if hmac.compare_digest(self.headers.get("Authorization", ""), expected):
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Video Downloader", charset="UTF-8"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _send_file(self, path):
        filename = os.path.basename(path)
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as source:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(size))
                self.send_header(
                    "Content-Disposition",
                    "attachment; filename=\"download\"; filename*=UTF-8''" + quote(filename),
                )
                self.end_headers()
                shutil.copyfileobj(source, self.wfile, length=1024 * 1024)
        except FileNotFoundError:
            self._send(404, {"error": "Downloaded file is no longer available"})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/healthz":
            self._send(200, {"status": "ok"})
            return
        if not self._authorized():
            return
        q = parse_qs(u.query)
        if u.path == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif u.path == "/api/progress":
            job = JOBS.get(q.get("id", [""])[0])
            if job is None:
                self._send(200, {"status": "unknown"})
            else:
                fields = ("status", "pct", "done", "total", "speed", "eta", "title", "error")
                if not PUBLIC_MODE:
                    fields += ("path", "folder")
                result = {key: job[key] for key in fields if key in job}
                if PUBLIC_MODE and job.get("status") == "done" and job.get("file_path"):
                    result["file_url"] = "/api/file?id=" + quote(q.get("id", [""])[0])
                self._send(200, result)
        elif u.path == "/api/file":
            jid = q.get("id", [""])[0]
            job = JOBS.get(jid)
            if job is None or job.get("status") != "done" or not job.get("file_path"):
                self._send(404, {"error": "Downloaded file is no longer available"})
            else:
                path = os.path.abspath(job["file_path"])
                folder = os.path.abspath(job.get("folder", DOWNLOAD_ROOT))
                try:
                    inside_folder = os.path.normcase(os.path.commonpath((folder, path))) == os.path.normcase(folder)
                except ValueError:
                    inside_folder = False
                if not inside_folder or not os.path.isfile(path):
                    self._send(404, {"error": "Downloaded file is no longer available"})
                else:
                    self._send_file(path)
        elif u.path == "/api/browse":
            self._send(200, {"path": pick_folder()})
        elif u.path == "/api/disk":
            try:
                p = os.path.expanduser(q.get("path", ["~"])[0])
                while p and not os.path.exists(p):
                    p = os.path.dirname(p)
                self._send(200, {"free": shutil.disk_usage(p or "/").free})
            except Exception:
                self._send(200, {"free": 0})
        elif u.path == "/api/defaults":
            self._send(200, {"folder": "" if PUBLIC_MODE else os.path.join(os.path.expanduser("~"), "Downloads"),
                             "ffmpeg": HAS_FFMPEG, "public_mode": PUBLIC_MODE})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorized():
            return
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n)
        try:
            data = json.loads(raw or b"{}") if raw else {}
        except json.JSONDecodeError:
            self._send(400, {"error": "Invalid JSON body"})
            return

        if not isinstance(data, dict):
            self._send(400, {"error": "Request body must be a JSON object"})
            return

        if self.path == "/api/info":
            url = str(data.get("url", "")).strip()
            if not url:
                self._send(400, {"error": "Missing or empty URL"})
                return
            try:
                self._send(200, get_info(url))
            except Exception as e:
                self._send(400, {"error": str(e)[:400]})
        elif self.path == "/api/download":
            try:
                data = dict(data)
            except Exception as e:
                self._send(400, {"error": str(e)[:400]})
                return
            jid = uuid.uuid4().hex
            try:
                if PUBLIC_MODE:
                    data["folder"] = normalize_folder(os.path.join(DOWNLOAD_ROOT, jid))
                else:
                    data["folder"] = normalize_folder(data.get("folder"))
            except Exception as e:
                self._send(400, {"error": str(e)[:400]})
                return
            JOBS[jid] = {
                "status": "starting", "pct": 0,
                "folder": data["folder"], "title": data.get("title") or "Video",
                "_control": threading.Condition(), "_pause_requested": False,
                "_cancel_requested": False, "_cleanup_paths": set(),
                "_started_files": set(), "_completed_files": set(),
            }
            threading.Thread(target=run_job, args=(jid, data), daemon=True).start()
            self._send(200, {"id": jid})
        elif self.path in ("/api/pause", "/api/resume", "/api/cancel"):
            jid = str(data.get("id", "")).strip()
            job = JOBS.get(jid)
            if job is None:
                self._send(404, {"error": "Unknown download job"})
                return

            with job["_control"]:
                status = job["status"]
                if self.path == "/api/cancel":
                    if status not in ("starting", "downloading", "pausing", "paused", "cancelling"):
                        self._send(409, {"error": "This download can no longer be cancelled"})
                        return
                    job["_cancel_requested"] = True
                    job["_pause_requested"] = False
                    job["status"] = "cancelling"
                    job["_control"].notify_all()
                elif self.path == "/api/pause":
                    if status not in ("starting", "downloading", "pausing", "paused"):
                        self._send(409, {"error": "This download can no longer be paused"})
                        return
                    job["_pause_requested"] = True
                    if status != "paused":
                        job["status"] = "pausing"
                else:
                    if status not in ("pausing", "paused"):
                        self._send(409, {"error": "This download is not paused"})
                        return
                    job["_pause_requested"] = False
                    job["_control"].notify_all()
                    job["status"] = "downloading"
                self._send(200, {"status": job["status"]})
        else:
            self._send(404, {"error": "not found"})


# ---------------------------------------------------------------- UI
PAGE = r"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Video Downloader</title>
<style>
:root{--bg:#0f1115;--card:#181b22;--line:#2a2f3a;--txt:#e8eaf0;--mut:#8b93a5;--acc:#4f8cff;--ok:#2ecc71;--bad:#ff5d5d}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font-family:system-ui,-apple-system,"Segoe UI",Tahoma,sans-serif;line-height:1.5}
.wrap{max-width:820px;margin:0 auto;padding:24px 16px 60px}
header{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:20px}
h1{font-size:22px;margin:0}
select,input[type=text]{background:var(--card);color:var(--txt);border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:15px}
input[type=text]{width:100%}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:14px}
.row{display:flex;gap:8px}
button{background:var(--acc);color:#fff;border:0;border-radius:10px;padding:10px 18px;font-size:15px;cursor:pointer;font-weight:600}
button.sec{background:#2a2f3a}
button:disabled{opacity:.5;cursor:not-allowed}
.lbl{color:var(--mut);font-size:13px;margin:0 0 8px}
.meta{display:flex;gap:14px;align-items:center}
.meta img{width:160px;border-radius:10px;flex-shrink:0}
.meta .t{font-weight:700}
.meta .s{color:var(--mut);font-size:13px}
.tabs{display:flex;gap:8px;flex-wrap:wrap}
.tab{flex:1;min-width:130px;text-align:center;padding:10px;border:1px solid var(--line);border-radius:10px;cursor:pointer;background:transparent}
.tab.on{border-color:var(--acc);background:rgba(79,140,255,.14)}
.q{display:flex;justify-content:space-between;align-items:center;padding:11px 12px;border:1px solid var(--line);border-radius:10px;margin-bottom:8px;cursor:pointer}
.q.on{border-color:var(--acc);background:rgba(79,140,255,.14)}
.q small{color:var(--mut)}
.badge{background:var(--ok);color:#06210f;border-radius:6px;padding:1px 7px;font-size:11px;font-weight:700;margin-inline-start:8px}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;text-align:center}
.stats div{background:#10131a;border:1px solid var(--line);border-radius:10px;padding:10px}
.stats b{display:block;font-size:18px}
.stats span{color:var(--mut);font-size:12px}
.bar{height:12px;background:#10131a;border-radius:8px;overflow:hidden;border:1px solid var(--line)}
.bar i{display:block;height:100%;width:0;background:var(--acc);transition:width .3s}
.msg{margin-top:10px;font-size:14px}
.err{color:var(--bad)}.okc{color:var(--ok)}.warn{color:#ffb84d;font-size:13px}
.history-list{display:grid;gap:10px}
.history-item{border:1px solid var(--line);border-radius:10px;padding:12px}
.history-item b{display:block}
.history-item small{display:block;color:var(--mut);margin-top:4px}
.history-path{direction:ltr;overflow-wrap:anywhere;color:var(--mut);font-size:12px;margin-top:6px}
.hide{display:none}
footer{color:var(--mut);font-size:12px;text-align:center;margin-top:24px}
@media(max-width:560px){.meta{flex-direction:column;align-items:flex-start}.meta img{width:100%}.stats{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1 id="title"></h1>
    <select id="lang"><option value="ar">العربية</option><option value="en">English</option><option value="fr">Français</option></select>
  </header>

  <div class="card">
    <p class="lbl" id="l_url"></p>
    <div class="row"><input type="text" id="url" dir="ltr" placeholder="https://www.youtube.com/watch?v=..."><button id="btnInfo"></button></div>
    <div class="msg err hide" id="infoErr"></div>
    <div class="msg warn hide" id="noff"></div>
  </div>

  <div id="main" class="hide">
    <div class="card meta">
      <img id="thumb" alt="">
      <div><div class="t" id="vtitle"></div><div class="s" id="vsub"></div></div>
    </div>

    <div class="card">
      <p class="lbl" id="l_mode"></p>
      <div class="tabs" id="modes"></div>
    </div>

    <div class="card">
      <p class="lbl" id="l_quality"></p>
      <div id="qlist"></div>
    </div>

    <div class="card">
      <p class="lbl" id="l_folder"></p>
      <div class="row" id="folderRow"><input type="text" id="folder" dir="ltr"><button class="sec" id="btnBrowse"></button></div>
      <div class="msg hide" id="publicNotice"></div>
    </div>

    <div class="card">
      <div class="stats">
        <div><b id="s_net">-</b><span id="s_net_l"></span></div>
        <div><b id="s_disk">-</b><span id="s_disk_l"></span></div>
        <div><b id="s_free">-</b><span id="s_free_l"></span></div>
      </div>
      <div class="msg warn hide" id="spaceWarn"></div>
    </div>

    <button id="btnDl" style="width:100%;padding:14px;font-size:17px"></button>
    <button class="sec hide" id="btnPause" style="width:100%;margin-top:8px"></button>
    <button class="sec hide" id="btnCancel" style="width:100%;margin-top:8px"></button>

    <div class="card hide" id="prog" style="margin-top:14px">
      <div class="bar"><i id="pbar"></i></div>
      <div class="msg" id="ptxt"></div>
      <a class="msg hide" id="fileLink" href=""></a>
    </div>
  </div>

  <div class="card" id="historyCard">
    <p class="lbl" id="l_history"></p>
    <div class="history-list" id="historyList"></div>
  </div>

  <footer id="foot"></footer>
</div>

<script>
const I18N={
ar:{dir:"rtl",title:"محمّل الفيديو",url:"رابط الفيديو",fetch:"جلب المعلومات",loading:"جاري الجلب...",
 mode:"ماذا تريد أن تحمّل؟",both:"فيديو + صوت",video:"فيديو فقط (بدون صوت)",audio:"صوت فقط",
 quality:"الجودة",best:"الأعلى",audioq:"صيغة الصوت",folder:"مجلد الحفظ",browse:"تصفّح...",
 net:"استهلاك الإنترنت",disk:"المساحة المطلوبة",free:"المتوفر في القرص",
 dl:"تحميل",pause:"إيقاف مؤقت",resume:"استئناف",cancel:"إلغاء التحميل",cancelling:"جاري الإلغاء وحذف البيانات...",
 cancelled:"تم إلغاء التحميل وحذف ملفاته المؤقتة",confirmCancel:"هل تريد إلغاء هذا التحميل وحذف البيانات التي تم تنزيلها؟",
 history:"آخر التنزيلات",noHistory:"لا توجد تنزيلات بعد",saveToDevice:"تنزيل إلى هذا الجهاز",publicNotice:"سيُنزّل الملف على هذا الجهاز من الخادم بعد اكتمال التحميل.",
 pausing:"جاري الإيقاف المؤقت...",paused:"متوقف مؤقتاً — الملف الذي تم تنزيله محفوظ",
 starting:"جاري البدء...",downloading:"جاري التحميل",processing:"جاري الدمج/المعالجة...",
 done:"تم التحميل بنجاح ✔",saved:"المسار",speed:"السرعة",eta:"المتبقي",
 err:"خطأ",noff:"ffmpeg غير مثبّت: متاح فقط الجودات الجاهزة (غالباً حتى 720p) بدون دمج، ولا يوجد MP3. ثبّته للحصول على أعلى جودة (4K/1080p).",
 low:"المساحة المتوفرة قد لا تكفي (يحتاج مساحة مؤقتة إضافية أثناء الدمج).",
 approx:"الأحجام تقريبية",foot:"استخدمه فقط للمحتوى الذي تملك حق تحميله. الأحجام تقريبية.",dur:"المدة",min:"د",s:"ث",ch:"القناة",enter:"أدخل رابطاً"},
en:{dir:"ltr",title:"Video Downloader",url:"Video link",fetch:"Fetch info",loading:"Fetching...",
 mode:"What do you want to download?",both:"Video + Audio",video:"Video only (no audio)",audio:"Audio only",
 quality:"Quality",best:"Best",audioq:"Audio format",folder:"Save folder",browse:"Browse...",
 net:"Internet usage",disk:"Disk space needed",free:"Free on disk",
 dl:"Download",pause:"Pause",resume:"Resume",cancel:"Cancel download",cancelling:"Cancelling and deleting data...",
 cancelled:"Download cancelled and its partial files deleted",confirmCancel:"Cancel this download and delete its downloaded data?",
 history:"Recent downloads",noHistory:"No downloads yet",saveToDevice:"Download to this device",publicNotice:"The file will be downloaded to this device from the server when processing finishes.",
 pausing:"Pausing...",paused:"Paused — downloaded data is saved",
 starting:"Starting...",downloading:"Downloading",processing:"Merging / processing...",
 done:"Download complete ✔",saved:"Saved to",speed:"Speed",eta:"ETA",
 err:"Error",noff:"ffmpeg not installed: only ready-made qualities (usually up to 720p) are available, no MP3. Install it for the best quality (4K/1080p).",
 low:"Free space may not be enough (extra temporary space is needed while merging).",
 approx:"Sizes are approximate",foot:"Use only for content you have the right to download. Sizes are approximate.",dur:"Duration",min:"m",s:"s",ch:"Channel",enter:"Enter a link"},
fr:{dir:"ltr",title:"Téléchargeur vidéo",url:"Lien de la vidéo",fetch:"Analyser",loading:"Analyse...",
 mode:"Que voulez-vous télécharger ?",both:"Vidéo + Audio",video:"Vidéo seule (sans audio)",audio:"Audio seul",
 quality:"Qualité",best:"Meilleure",audioq:"Format audio",folder:"Dossier d'enregistrement",browse:"Parcourir...",
 net:"Consommation internet",disk:"Espace disque requis",free:"Libre sur le disque",
 dl:"Télécharger",pause:"Pause",resume:"Reprendre",cancel:"Annuler le téléchargement",cancelling:"Annulation et suppression des données...",
 cancelled:"Téléchargement annulé et fichiers partiels supprimés",confirmCancel:"Annuler ce téléchargement et supprimer les données téléchargées ?",
 history:"Téléchargements récents",noHistory:"Aucun téléchargement pour le moment",saveToDevice:"Télécharger sur cet appareil",publicNotice:"Le fichier sera téléchargé sur cet appareil depuis le serveur une fois le traitement terminé.",
 pausing:"Mise en pause...",paused:"En pause — les données téléchargées sont conservées",
 starting:"Démarrage...",downloading:"Téléchargement",processing:"Fusion / traitement...",
 done:"Téléchargement terminé ✔",saved:"Enregistré dans",speed:"Vitesse",eta:"Reste",
 err:"Erreur",noff:"ffmpeg n'est pas installé : seules les qualités prêtes (souvent jusqu'à 720p) sont disponibles, pas de MP3. Installez-le pour la meilleure qualité (4K/1080p).",
 low:"L'espace libre peut être insuffisant (espace temporaire supplémentaire requis lors de la fusion).",
 approx:"Tailles approximatives",foot:"À utiliser uniquement pour du contenu dont vous avez le droit de téléchargement. Tailles approximatives.",dur:"Durée",min:"min",s:"s",ch:"Chaîne",enter:"Entrez un lien"}
};
const $=id=>document.getElementById(id);
let L="ar",info=null,mode="both",sel=0,free=0,poll=null,jobId=null,jobStatus=null,publicMode=false;
let historyItems=[];
try{
  const savedHistory=JSON.parse(localStorage.getItem("downloadHistory")||"[]");
  if(Array.isArray(savedHistory))historyItems=savedHistory;
}catch(e){console.warn("Could not read download history:",e)}

function T(k){return I18N[L][k]||k}
function bytes(n){if(!n)return "?";const u=["B","KB","MB","GB","TB"];let i=0;while(n>=1024&&i<4){n/=1024;i++}return n.toFixed(i>=2?1:0)+" "+u[i]}
function dur(s){const m=Math.floor(s/60);return m+" "+T("min")+" "+(s%60)+" "+T("s")}
function saveHistory(){
  historyItems=historyItems.slice(0,20);
  localStorage.setItem("downloadHistory",JSON.stringify(historyItems));
  renderHistory();
}
function updateHistory(id,updates){
  const item=historyItems.find(entry=>entry.id===id);
  if(!item)return;
  let changed=false;
  Object.keys(updates).forEach(key=>{
    if(updates[key]!==undefined&&item[key]!==updates[key]){item[key]=updates[key];changed=true}
  });
  if(changed)saveHistory();
}
function renderHistory(){
  const list=$("historyList");list.replaceChildren();
  if(!historyItems.length){list.textContent=T("noHistory");return}
  historyItems.forEach(item=>{
    const row=document.createElement("div");row.className="history-item";
    const title=document.createElement("b");title.textContent=item.title||T("title");
    const status=document.createElement("small");
    const statusKey=item.status==="error"?"err":item.status==="cancelled"?"cancelled":item.status||"starting";
    status.textContent=T(statusKey);
    const path=document.createElement("div");path.className="history-path";
    if(item.fileUrl){
      const link=document.createElement("a");link.href=item.fileUrl;link.textContent=T("saveToDevice");link.download="";path.appendChild(link);
    }else{
      path.textContent=item.deviceDownload?T("saveToDevice"):item.path||item.folder||"";
    }
    const date=document.createElement("small");date.textContent=item.createdAt?new Date(item.createdAt).toLocaleString():"";
    row.append(title,status,path,date);
    if(item.error){const error=document.createElement("small");error.className="err";error.textContent=item.error;row.append(error)}
    list.appendChild(row);
  });
}
function updatePauseButton(status){
  jobStatus=status;
  const b=$("btnPause");
  const active=["starting","downloading","pausing","paused"].includes(status);
  b.classList.toggle("hide",!active);
  b.disabled=false;
  b.textContent=status==="paused"||status==="pausing"?T("resume"):T("pause");
  const cancel=$("btnCancel");
  const cancellable=["starting","downloading","pausing","paused","cancelling"].includes(status);
  cancel.classList.toggle("hide",!cancellable);
  cancel.disabled=status==="cancelling";
  cancel.textContent=status==="cancelling"?T("cancelling"):T("cancel");
}

function applyLang(){
  const d=I18N[L];
  document.documentElement.lang=L;document.documentElement.dir=d.dir;
  $("title").textContent=T("title");$("l_url").textContent=T("url");$("btnInfo").textContent=T("fetch");
  $("l_mode").textContent=T("mode");$("l_quality").textContent=T("quality");$("l_folder").textContent=T("folder");
  $("btnBrowse").textContent=T("browse");$("btnDl").textContent=T("dl");
  updatePauseButton(jobStatus);
  $("l_history").textContent=T("history");
  $("publicNotice").textContent=T("publicNotice");
  if($("fileLink").href)$("fileLink").textContent=T("saveToDevice");
  $("s_net_l").textContent=T("net");$("s_disk_l").textContent=T("disk");$("s_free_l").textContent=T("free");
  $("foot").textContent=T("foot");$("noff").textContent=T("noff");
  document.title=T("title");
  renderHistory();
  if(info){renderModes();renderQ();updateStats();showMeta()}
}

function showMeta(){
  $("vtitle").textContent=info.title||"";
  $("vsub").textContent=(info.channel?T("ch")+": "+info.channel+" • ":"")+T("dur")+": "+dur(info.duration||0);
}

function renderModes(){
  const m=[["both","both"],["video","video"],["audio","audio"]];
  $("modes").innerHTML="";
  m.forEach(([k,lab])=>{
    const b=document.createElement("div");b.className="tab"+(mode===k?" on":"");b.textContent=T(lab);
    b.onclick=()=>{mode=k;sel=0;renderModes();renderQ();updateStats()};
    $("modes").appendChild(b);
  });
}

function renderQ(){
  const box=$("qlist");box.innerHTML="";
  if(mode==="audio"){
    $("l_quality").textContent=T("audioq");
    info.audios.forEach((a,i)=>{
      const e=document.createElement("div");e.className="q"+(sel===i?" on":"");
      e.innerHTML="<span>"+a.label+(i===0?'<span class="badge">'+T("best")+"</span>":"")+"</span><small>"+bytes(a.size)+"</small>";
      e.onclick=()=>{sel=i;renderQ();updateStats()};box.appendChild(e);
    });
  }else{
    $("l_quality").textContent=T("quality");
    info.videos.forEach((v,i)=>{
      const e=document.createElement("div");e.className="q"+(sel===i?" on":"");
      const sz=mode==="both"?v.size_both:v.size_video;
      e.innerHTML="<span><b>"+v.h+"p"+(v.fps>30?" "+v.fps+"fps":"")+"</b> <small>"+v.codec+" • "+v.ext+"</small>"+(i===0?'<span class="badge">'+T("best")+"</span>":"")+"</span><small>"+bytes(sz)+"</small>";
      e.onclick=()=>{sel=i;renderQ();updateStats()};box.appendChild(e);
    });
  }
}

function curSize(){
  if(!info)return 0;
  if(mode==="audio")return (info.audios[sel]||{}).size||0;
  const v=info.videos[sel];if(!v)return 0;
  return mode==="both"?v.size_both:v.size_video;
}

async function updateStats(){
  const s=curSize();
  $("s_net").textContent="≈ "+bytes(s);
  $("s_disk").textContent="≈ "+bytes(s);
  try{const r=await (await fetch("/api/disk?path="+encodeURIComponent($("folder").value))).json();free=r.free;$("s_free").textContent=bytes(free)}catch(e){}
  const w=$("spaceWarn");
  if(free&&s*2>free){w.textContent=T("low");w.classList.remove("hide")}else w.classList.add("hide");
}

$("btnInfo").onclick=async()=>{
  const url=$("url").value.trim();const err=$("infoErr");err.classList.add("hide");
  if(!url){err.textContent=T("enter");err.classList.remove("hide");return}
  $("btnInfo").disabled=true;$("btnInfo").textContent=T("loading");
  try{
    const r=await fetch("/api/info",{method:"POST",body:JSON.stringify({url})});
    const j=await r.json();
    if(!r.ok)throw new Error(j.error||"error");
    info=j;mode="both";sel=0;
    $("thumb").src=info.thumb||"";
    $("noff").classList.toggle("hide",!!info.ffmpeg);
    $("main").classList.remove("hide");$("prog").classList.add("hide");
    showMeta();renderModes();renderQ();updateStats();
  }catch(e){err.textContent=T("err")+": "+e.message;err.classList.remove("hide")}
  $("btnInfo").disabled=false;$("btnInfo").textContent=T("fetch");
};

$("btnBrowse").onclick=async()=>{
  const r=await (await fetch("/api/browse")).json();
  if(r.path){$("folder").value=r.path;updateStats()}
};
$("folder").onchange=updateStats;

$("btnDl").onclick=async()=>{
  const p={url:$("url").value.trim(),title:info.title||T("title"),mode,folder:$("folder").value};
  if(mode==="audio"){p.audio=(info.audios[sel]||{}).id}
  else{const v=info.videos[sel];p.video_id=v.id;p.prog=v.prog;p.avc=(v.codec||"").startsWith("avc1")}
  $("btnDl").disabled=true;$("prog").classList.remove("hide");
  $("pbar").style.width="0%";$("ptxt").className="msg";$("ptxt").textContent=T("starting");
  $("fileLink").classList.add("hide");$("fileLink").removeAttribute("href");
  try{
    const response=await fetch("/api/download",{method:"POST",body:JSON.stringify(p)});
    const created=await response.json();
    if(!response.ok)throw new Error(created.error||"error");
    jobId=created.id;updatePauseButton("starting");
    historyItems.unshift({id:jobId,title:p.title,status:"starting",folder:p.folder,path:p.folder,createdAt:Date.now()});
    saveHistory();
    clearInterval(poll);
    poll=setInterval(async()=>{
      const j=await (await fetch("/api/progress?id="+jobId)).json();
      updatePauseButton(j.status);
      const historyUpdate={status:j.status};
      if(j.path)historyUpdate.path=j.path;
      if(j.file_url){historyUpdate.fileUrl=j.file_url;historyUpdate.deviceDownload=publicMode}
      if(j.error)historyUpdate.error=j.error;
      updateHistory(jobId,historyUpdate);
      if(j.status==="downloading"){
        $("pbar").style.width=(j.pct||0).toFixed(1)+"%";
        $("ptxt").textContent=T("downloading")+" "+(j.pct||0).toFixed(1)+"% • "+bytes(j.done)+" / "+bytes(j.total)+" • "+T("speed")+": "+bytes(j.speed)+"/s"+(j.eta!=null?" • "+T("eta")+": "+j.eta+T("s"):"");
      }else if(j.status==="pausing"){
        $("ptxt").textContent=T("pausing")+" "+bytes(j.done)+" / "+bytes(j.total);
      }else if(j.status==="paused"){
        $("ptxt").textContent=T("paused")+" • "+bytes(j.done)+" / "+bytes(j.total);
      }else if(j.status==="cancelling"){
        $("ptxt").textContent=T("cancelling");
      }else if(j.status==="processing"){$("ptxt").textContent=T("processing")}
      else if(j.status==="done"){
        clearInterval(poll);$("pbar").style.width="100%";$("ptxt").className="msg okc";
        $("ptxt").textContent=T("done")+"  —  "+T("saved")+": "+(publicMode?T("saveToDevice"):(j.path||""));
        if(j.file_url){$("fileLink").href=j.file_url;$("fileLink").textContent=T("saveToDevice");$("fileLink").classList.remove("hide")}
        $("btnDl").disabled=false;updatePauseButton("done");
      }else if(j.status==="cancelled"){
        clearInterval(poll);$("ptxt").className="msg";$("ptxt").textContent=T("cancelled");$("btnDl").disabled=false;updatePauseButton("cancelled");
      }else if(j.status==="error"){
        clearInterval(poll);$("ptxt").className="msg err";$("ptxt").textContent=T("err")+": "+j.error;$("btnDl").disabled=false;updatePauseButton("error");
      }
    },700);
  }catch(e){
    $("ptxt").className="msg err";$("ptxt").textContent=T("err")+": "+e.message;$("btnDl").disabled=false;
  }
};

$("btnPause").onclick=async()=>{
  if(!jobId)return;
  const action=jobStatus==="paused"||jobStatus==="pausing"?"resume":"pause";
  const button=$("btnPause");
  button.disabled=true;
  try{
    const r=await fetch("/api/"+action,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id:jobId})});
    const j=await r.json();
    if(!r.ok)throw new Error(j.error||"error");
    updatePauseButton(j.status);
  }catch(e){
    $("ptxt").className="msg err";$("ptxt").textContent=T("err")+": "+e.message;
  }finally{
    button.disabled=false;
  }
};

$("btnCancel").onclick=async()=>{
  if(!jobId||!confirm(T("confirmCancel")))return;
  const button=$("btnCancel");
  button.disabled=true;
  try{
    const r=await fetch("/api/cancel",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id:jobId})});
    const j=await r.json();
    if(!r.ok)throw new Error(j.error||"error");
    updatePauseButton(j.status);
  }catch(e){
    $("ptxt").className="msg err";$("ptxt").textContent=T("err")+": "+e.message;
    button.disabled=false;
  }
};

$("lang").onchange=e=>{L=e.target.value;localStorage.setItem("lang",L);applyLang()};
(async()=>{
  const sv=localStorage.getItem("lang");if(sv&&I18N[sv]){L=sv;$("lang").value=L}
  const d=await (await fetch("/api/defaults")).json();publicMode=!!d.public_mode;$("folder").value=d.folder;
  if(d.public_mode){
    $("folderRow").classList.add("hide");
    $("publicNotice").classList.remove("hide");
  }
  applyLang();
})();
</script>
</body>
</html>
"""


def main():
    if PUBLIC_MODE and not APP_PASSWORD:
        raise RuntimeError("Set APP_PASSWORD before running the public downloader.")
    host = "0.0.0.0" if PUBLIC_MODE else "127.0.0.1"
    port = int(os.environ.get("PORT", "10000" if PUBLIC_MODE else "0"))
    srv = ThreadingHTTPServer((host, port), H)
    port = srv.server_address[1]
    url = "http://%s:%d/" % (host, port)
    print("Running at", url, "(close this window to stop)")
    if not HAS_FFMPEG:
        print("WARNING: ffmpeg not found - best quality merge and MP3 are disabled.")
    if not PUBLIC_MODE:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
