# SDO Daily Movie Generator — starter deployment package

This package contains:
- `frontend/sdo-movie-builder.html`: the Cosmic Tool Station page, with a live progress bar and ETA.
- `backend/main.py`: FastAPI service that checks NASA daily movie URLs, downloads available AIA clips, joins them with FFmpeg, and serves an MP4.
- `backend/Dockerfile`: container build with FFmpeg installed.

## NASA archive format verified for the legacy archive

NASA's older daily movies use URLs like:

`https://sdo.gsfc.nasa.gov/assets/img/dailymov/2012/11/11/20121111_1024_0304.ogv`

This implementation uses that verified 1024×1024 OGV format. It does not claim that every day/channel exists. Missing dates are skipped and listed in the job status. NASA also has a newer `movies_1080` area, but it is not mixed into this first implementation.

## Deploy backend

Deploy the `backend` folder as a Docker web service on a host that supports persistent processes, outbound HTTPS, and FFmpeg. Set the service start command to the Dockerfile default. Configure:
- `MAX_DAYS=14` (default; increase only after testing server capacity)
- `MAX_FILE_MB=100` (default)
- `ALLOWED_ORIGINS=https://dharanesh186.github.io` (recommended; replace with your exact GitHub Pages origin)

The backend must remain running while a job is being processed. This starter keeps job state and temporary files in process memory and `/tmp`; a restart loses jobs and generated files. For a public production service, add per-IP rate limits, a queue, disk cleanup, persistent job storage, and a storage/download expiry policy.

## Connect frontend

Open `frontend/sdo-movie-builder.html` and replace:

`https://YOUR-BACKEND-URL`

with the deployed backend base URL, without a trailing slash.

Upload this HTML file to your repository's `tools/` directory. Its navigation links assume the page is located at `/tools/sdo-movie-builder.html` and the main site pages are at repository root.

## Important limitations

- One wavelength per generated video.
- 1024×1024 legacy daily movies only in this version.
- Up to 14 calendar days per job by default.
- Playback speed is applied during MP4 encoding.
- This combines NASA's pre-generated daily visual movies; it does not download calibrated science FITS data or make arbitrary time-cadence movies.
- The progress bar is based on real backend stages and downloaded-clip counts. FFmpeg encoding is shown as a processing stage; exact encoding percentage/ETA is not available in this starter implementation.
- Test the archive and backend on a short date range before publishing the tool to visitors.

NASA daily movies are available from 2010-05-20. Credit: Courtesy of NASA/SDO and the AIA science team.
