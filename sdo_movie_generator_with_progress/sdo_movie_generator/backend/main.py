import os, uuid, time, shutil, subprocess, threading
from pathlib import Path
from datetime import date, timedelta
from typing import Optional
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

app = FastAPI(title="SDO Daily Movie Generator")
allowed = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware, allow_origins=allowed, allow_credentials=False,
    allow_methods=["GET", "POST"], allow_headers=["*"]
)

NASA_ROOT = "https://sdo.gsfc.nasa.gov/assets/img/dailymov"
WORK = Path(os.getenv("WORK_DIR", "/tmp/sdo_jobs"))
WORK.mkdir(parents=True, exist_ok=True)
JOBS = {}
LOCK = threading.Lock()
MAX_DAYS = int(os.getenv("MAX_DAYS", "14"))
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "100"))

class Request(BaseModel):
    start_date: date
    end_date: date
    wavelength: str = Field(pattern=r"^(0094|0131|0171|0193|0211|0304|0335|1600|1700)$")
    resolution: str = Field(default="1024", pattern=r"^1024$")
    speed: float = Field(default=1.0, ge=0.5, le=10.0)

def update(job_id, **kwargs):
    with LOCK:
        JOBS[job_id].update(kwargs)
        j = JOBS[job_id]
        elapsed = time.time() - j["started"]
        p = max(0.0, min(99.0, float(j.get("progress", 0))))
        eta = int(elapsed * (100-p) / p) if p >= 2 and p < 99 else None
        j["eta_seconds"] = eta
        j["elapsed_seconds"] = int(elapsed)

def get_daily_url(day, resolution, wave):
    # NASA's documented legacy daily movie naming convention:
    # /YYYY/MM/DD/YYYYMMDD_1024_0193.ogv
    return f"{NASA_ROOT}/{day:%Y/%m/%d}/{day:%Y%m%d}_{resolution}_{wave}.ogv"

def worker(job_id, req):
    folder = WORK / job_id
    folder.mkdir(parents=True, exist_ok=True)
    downloaded = []
    try:
        days = [(req.start_date + timedelta(days=i)) for i in range((req.end_date-req.start_date).days+1)]
        update(job_id, status="running", stage="check", stage_label="Checking NASA archive", detail=f"Checking {len(days)} requested dates", progress=2)
        session = requests.Session()
        session.headers.update({"User-Agent":"CosmicToolStation-SDO-MovieBuilder/1.0 (educational project)"})
        urls = [(d, get_daily_url(d, req.resolution, req.wavelength)) for d in days]
        available = []
        missing = []
        for i,(d,url) in enumerate(urls):
            try:
                head = session.head(url, timeout=25, allow_redirects=True)
                if head.status_code == 200:
                    available.append((d,url))
                else:
                    missing.append(d.isoformat())
            except requests.RequestException:
                missing.append(d.isoformat())
            update(job_id, progress=3 + 12*(i+1)/len(days),
                   detail=f"Archive check: {i+1}/{len(days)} dates checked")
        if not available:
            raise RuntimeError("No matching NASA daily movies were found for this date range and wavelength. Try another date range or channel.")
        update(job_id, stage="download", stage_label="Downloading daily movies",
               detail=f"{len(available)} clips found; {len(missing)} dates unavailable",
               progress=15, missing_dates=missing, available_count=len(available))
        listfile = folder / "clips.txt"
        with listfile.open("w", encoding="utf-8") as listing:
            for i,(d,url) in enumerate(available):
                dest = folder / f"{d:%Y%m%d}.ogv"
                with session.get(url, stream=True, timeout=(20,180)) as response:
                    response.raise_for_status()
                    total = int(response.headers.get("content-length", "0"))
                    if total and total > MAX_FILE_MB*1024*1024:
                        raise RuntimeError(f"{d.isoformat()} movie exceeds the {MAX_FILE_MB} MB per-file safety limit.")
                    size = 0
                    with dest.open("wb") as f:
                        for chunk in response.iter_content(1024*1024):
                            if chunk:
                                size += len(chunk)
                                if size > MAX_FILE_MB*1024*1024:
                                    raise RuntimeError(f"{d.isoformat()} movie exceeded the {MAX_FILE_MB} MB safety limit.")
                                f.write(chunk)
                downloaded.append(dest)
                listing.write(f"file '{dest.as_posix()}'\n")
                p = 15 + 50*(i+1)/len(available)
                update(job_id, progress=p, detail=f"Downloaded {i+1}/{len(available)} clips")
        update(job_id, stage="encode", stage_label="Combining and encoding MP4",
               detail="FFmpeg is joining the daily clips and applying playback speed", progress=68)
        output = folder / f"SDO_AIA_{req.start_date}_{req.end_date}_{req.wavelength}.mp4"
        # concat demuxer joins same-format NASA OGV daily clips; re-encode creates a browser-friendly MP4.
        # Estimate total input duration with ffprobe so encoding progress can be measured.
        total_duration = 0.0
        for clip in downloaded:
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                    "-of", "default=noprint_wrappers=1:nokey=1", str(clip)],
                                   capture_output=True, text=True, timeout=30)
            if probe.returncode == 0:
                try:
                    total_duration += float(probe.stdout.strip())
                except ValueError:
                    pass
        cmd = ["ffmpeg","-y","-f","concat","-safe","0","-i",str(listfile),
               "-vf",f"setpts=PTS/{req.speed}","-an","-c:v","libx264","-preset","veryfast",
               "-crf","23","-pix_fmt","yuv420p","-movflags","+faststart",
               "-progress","pipe:1","-nostats",str(output)]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, bufsize=1)
        for line in proc.stdout:
            if line.startswith("out_time=") and total_duration > 0:
                stamp = line.strip().split("=",1)[1]
                try:
                    hh, mm, ss = stamp.split(":")
                    elapsed_video = int(hh)*3600 + int(mm)*60 + float(ss)
                    fraction = min(1.0, elapsed_video / total_duration)
                    update(job_id, progress=68 + 28*fraction,
                           detail=f"Encoding MP4: {int(fraction*100)}% of video processed")
                except (ValueError, TypeError):
                    pass
        return_code = proc.wait(timeout=900)
        if return_code != 0:
            raise RuntimeError("FFmpeg could not combine the archive clips. The source clips may have incompatible encoding.")
        update(job_id, stage="ready", stage_label="Preparing MP4 download",
               detail="Video successfully generated", progress=99, status="completed",
               output=str(output), filename=output.name)
    except Exception as exc:
        update(job_id, status="failed", stage_label="Generation failed", error=str(exc), progress=0)
    finally:
        # Keep output for download; remove source clips after a short retention window in production.
        pass

@app.get("/")
def health():
    return {"service":"SDO Daily Movie Generator","status":"online"}

@app.post("/api/generate")
def generate(req: Request):
    if req.end_date < req.start_date:
        raise HTTPException(400, "End date must be on or after start date.")
    if req.start_date < date(2010,5,20):
        raise HTTPException(400, "NASA daily movies begin on 2010-05-20.")
    if req.end_date > date.today():
        raise HTTPException(400, "End date cannot be in the future.")
    count = (req.end_date-req.start_date).days+1
    if count > MAX_DAYS:
        raise HTTPException(400, f"Please select no more than {MAX_DAYS} days per generation.")
    job_id = uuid.uuid4().hex
    with LOCK:
        JOBS[job_id] = {"job_id":job_id,"status":"queued","stage":"check",
            "stage_label":"Queued","detail":"Waiting for worker","progress":0,
            "started":time.time(),"eta_seconds":None}
    threading.Thread(target=worker,args=(job_id,req),daemon=True).start()
    return {"job_id":job_id}

@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404,"Job not found. The backend may have restarted.")
        return {k:v for k,v in job.items() if k not in ("started","output")}

@app.get("/api/jobs/{job_id}/download")
def download(job_id: str):
    with LOCK:
        job = JOBS.get(job_id)
        if not job or job.get("status") != "completed":
            raise HTTPException(409,"The video is not ready yet.")
        path = Path(job["output"])
        filename = job["filename"]
    if not path.exists():
        raise HTTPException(404,"Generated file is no longer available.")
    return FileResponse(path, media_type="video/mp4", filename=filename)
