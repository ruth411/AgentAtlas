"""Direct-insert ffmpeg error + recipe claims."""

from datetime import datetime, timezone
from hashlib import sha256

from app.schemas.claim import KnowledgeClaim
from app.schemas.enums import (
    ClaimType,
    EvidenceType,
    RiskLevel,
    TrustLevel,
)
from app.schemas.evidence import Evidence
from app.services.claim_store import get_claim_store
from app.services.embedding_service import (
    EmbeddingService,
    claim_embedding_text,
    serialize_embedding,
)
from app.services.orchestrator import CanonOrchestrator

BASE = "https://ffmpeg.org"

CLAIMS = [
    # ============================================================
    # ffmpeg-errors — 30 common errors
    # ============================================================
    (
        "ffmpeg-errors", "ffmpeg error: moov atom not found",
        "MP4/MOV file is truncated or being written — the index ('moov atom') hasn't been written yet, "
        "or download/upload was cut off. "
        "Fix: (1) for downloads in progress, wait until complete; "
        "(2) recover with `untrunc <reference.mp4> <broken.mp4>` (open-source recovery tool); "
        "(3) try `ffmpeg -err_detect ignore_err -i broken.mp4 -c copy out.mp4` (sometimes recoverable); "
        "(4) for live recording, use `-movflags +faststart+frag_keyframe` so the moov is written incrementally.",
        f"{BASE}/ffmpeg-formats.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: Invalid data found when processing input",
        "Input file is corrupted, in an unrecognized format, or has a wrong extension. "
        "Fix: (1) confirm what it actually is: `ffprobe input.xyz` or `file input.xyz`; "
        "(2) force a format: `ffmpeg -f mpegts -i input.xyz out.mp4`; "
        "(3) for raw streams: `ffmpeg -f rawvideo -pix_fmt yuv420p -s 1920x1080 -i raw.yuv out.mp4`; "
        "(4) ignore decode errors: `-err_detect ignore_err`; "
        "(5) for partial recovery: `-fflags +discardcorrupt`.",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: Stream specifier 'X' in filtergraph matches no streams",
        "Your `-map` or filter references a stream index that doesn't exist (e.g. -map 0:1 when there's only 0:0). "
        "Fix: (1) list streams first: `ffprobe input.mp4` — note the [0:0], [0:1] indices; "
        "(2) use stream type instead of index: `-map 0:v:0` (first video), `-map 0:a:0` (first audio); "
        "(3) optional map (skip silently if missing): `-map 0:s?` (the ? makes it optional); "
        "(4) for filter_complex, label outputs: `[0:v]scale=...[vout]` then `-map '[vout]'`.",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: Encoder (codec h264) not found for output stream",
        "Your ffmpeg build doesn't include libx264 (H.264 encoder). Often happens with `--disable-nonfree` builds. "
        "Fix: (1) check available encoders: `ffmpeg -encoders | grep 264`; "
        "(2) macOS Homebrew: `brew install ffmpeg` (includes libx264); "
        "(3) Linux: `apt install ffmpeg` (Ubuntu/Debian default includes libx264); "
        "(4) static build: download from `johnvansickle.com/ffmpeg/`; "
        "(5) hardware fallback: `-c:v h264_videotoolbox` (macOS) or `-c:v h264_nvenc` (NVIDIA GPU).",
        f"{BASE}/ffmpeg-codecs.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: Unable to find a suitable output format",
        "ffmpeg couldn't guess output format — your filename has no extension or unknown extension. "
        "Fix: (1) add proper extension: `out.mp4`, `out.mkv`, `out.mp3`; "
        "(2) force format explicitly: `-f mp4 out.bin`; "
        "(3) list available muxers: `ffmpeg -formats | grep -E '^.E'`; "
        "(4) for stream output: `-f hls`, `-f flv` (rtmp), `-f rtsp`.",
        f"{BASE}/ffmpeg-formats.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: Permission denied / output file already exists",
        "Output path not writable, OR file exists and you didn't pass -y to overwrite. "
        "Fix: (1) overwrite: `ffmpeg -y -i in.mp4 out.mp4`; "
        "(2) never overwrite (safer for batch): `-n` (errors if exists); "
        "(3) write permission: `ls -ld <output-dir>` — should be writable; "
        "(4) macOS Gatekeeper: granted Full Disk Access to terminal; "
        "(5) if file is in use by player: close player first.",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: Could not write header (incorrect codec parameters?)",
        "Output muxer rejects the stream codec/pixel format. Most common cause: H.264 in MP4 needs yuv420p (not yuv444 or yuv422). "
        "Fix: (1) force pixel format: `-pix_fmt yuv420p`; "
        "(2) for libx264 mp4: add `-pix_fmt yuv420p -profile:v high -level 4.0`; "
        "(3) check what the codec needs: `ffmpeg -h encoder=libx264`; "
        "(4) switch container if codec is right: `out.mkv` accepts almost anything.",
        f"{BASE}/ffmpeg-formats.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: Conversion failed!",
        "Generic 'exit 1' — the real reason is in lines above. ffmpeg exits non-zero whenever any stream fails. "
        "Fix: (1) scroll up — the actual error is above this line; "
        "(2) re-run with `-loglevel debug` for more detail; "
        "(3) for batch scripts, capture stderr: `ffmpeg ... 2>&1 | tee log.txt`; "
        "(4) common root causes: unsupported codec, bad filter syntax, missing input.",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: Unknown encoder 'libx264' / 'libx265' / 'libfdk_aac'",
        "Your ffmpeg build was compiled without that library. Most static distributions exclude non-LGPL codecs. "
        "Fix: (1) homebrew: `brew install ffmpeg` (full build); "
        "(2) compile from source with `--enable-libx264 --enable-libx265 --enable-libfdk-aac --enable-gpl --enable-nonfree`; "
        "(3) alternative for AAC: built-in `aac` encoder (lower quality but works everywhere): `-c:a aac`; "
        "(4) alternative for H.264: `-c:v libopenh264` (Cisco's free build); "
        "(5) check what's available: `ffmpeg -encoders | grep -i <name>`.",
        f"{BASE}/ffmpeg-codecs.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: No such filter: 'X' / Filter 'overlay' not found",
        "Filter name typo, OR your build excluded it. "
        "Fix: (1) list available filters: `ffmpeg -filters | grep -i <name>`; "
        "(2) common typos: `scale` (not 'resize'), `crop` (not 'cut'), `atempo` (not 'speed'); "
        "(3) overlay needs `--enable-libavfilter` (always on in standard builds); "
        "(4) for subtitle filters: needs `--enable-libass`.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: Error opening filters / Failed to inject frame into filter network",
        "Bad `-vf` or `-filter_complex` syntax — usually a quoting / escaping problem. "
        "Fix: (1) wrap whole filter in single quotes: `-vf 'scale=1280:720'`; "
        "(2) inside filter, escape colon in args: `\\:` for ffmpeg, `\\\\:` inside shell; "
        "(3) for drawtext fontfile path: `fontfile=/path/to/font.ttf` (escape colons on Windows); "
        "(4) test filter alone: `ffmpeg -i in -vf '<your-filter>' -f null -`.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: Output file #0 does not contain any stream",
        "Your `-vf` / `-filter_complex` produced output but no stream was mapped to the output file. "
        "Fix: (1) for simple `-vf`: just check input has streams; "
        "(2) for `-filter_complex`: label your final pad and map it: `-filter_complex '[0:v]scale=1280:720[v]' -map '[v]' out.mp4`; "
        "(3) include audio too: `-map 0:a` after the video map; "
        "(4) for video-only output: `-an` is correct; for audio-only: `-vn`.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: Too many packets buffered for output stream",
        "Interleaving overflow — one stream is way ahead of others in PTS (presentation timestamp). "
        "Common with bad input or video+audio drift. "
        "Fix: (1) raise the buffer: `-max_muxing_queue_size 9999`; "
        "(2) for mismatched stream durations, sync with `-shortest` (truncate to shortest); "
        "(3) regen PTS: `-fflags +genpts`; "
        "(4) check input: `ffprobe -show_streams input.mp4` — look for r_frame_rate inconsistencies.",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: Could not find codec parameters for stream",
        "Input has missing or unreadable codec headers — ffmpeg needs to probe more bytes to identify the codec. "
        "Fix: (1) increase probe size: `-probesize 100M -analyzeduration 100M -i in.mp4`; "
        "(2) for live streams: `-probesize 32 -analyzeduration 0` (less wait); "
        "(3) for raw streams, specify everything: `-f rawvideo -pix_fmt yuv420p -s 1920x1080 -framerate 30 -i raw.yuv`; "
        "(4) try newer build — old ffmpeg may not recognize newer codecs (AV1, VVC).",
        f"{BASE}/ffmpeg-formats.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: Non-monotonous DTS in output stream",
        "Decode timestamps (DTS) went backward. Common with concat of files that have different timebases, or seeking issues. "
        "Fix: (1) regenerate PTS: `-fflags +genpts`; "
        "(2) reset timestamps after seek: `-vsync 1` or `-vsync vfr`; "
        "(3) for concat: ensure all inputs have identical codec/timebase: `ffprobe` each; "
        "(4) for live recording, use `-async 1` for audio sync.",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: av_interleaved_write_frame(): Broken pipe",
        "Downstream consumer of your output (player, ffplay, network) closed the connection. "
        "Fix: (1) for streaming: add retry: `-reconnect 1 -reconnect_at_eof 1 -reconnect_streamed 1`; "
        "(2) for piping to another tool: confirm the receiver is still running; "
        "(3) for RTMP/RTSP: check network stability, try `-flvflags no_duration_filesize`; "
        "(4) for `ffmpeg ... | something`, check the consumer's stderr.",
        f"{BASE}/ffmpeg-protocols.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: Unable to parse option value for -filter_complex",
        "Filter_complex syntax error — usually unbalanced brackets, missing semicolons, or shell-escaping. "
        "Fix: (1) use single quotes: `-filter_complex '[0:v][1:v]overlay=W-w-10:10[out]' -map '[out]'`; "
        "(2) separate filters with semicolons, chain with commas: `[0:v]scale=1280:720,format=yuv420p[v]`; "
        "(3) label EVERY output if you'll use it: `[v]`, `[a]`; "
        "(4) for multi-line readability, use `-filter_complex_script filters.txt`.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: rtsp:// timeout / Connection refused",
        "RTSP / network input failed. "
        "Fix: (1) set socket timeout: `-stimeout 5000000` (microseconds, so 5 sec); "
        "(2) force TCP transport (more reliable than UDP): `-rtsp_transport tcp`; "
        "(3) verify URL works: `ffplay rtsp://...` first; "
        "(4) for camera URLs with auth: `rtsp://user:pass@host:554/stream`; "
        "(5) for HTTP retries: `-reconnect 1 -reconnect_delay_max 5`.",
        f"{BASE}/ffmpeg-protocols.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: Invalid argument when using -ss before -i for fast seek",
        "`-ss <time>` placement matters: BEFORE `-i` is fast (keyframe-aligned, less accurate); AFTER `-i` is accurate (decodes from start, slow). "
        "Fix: (1) for keyframe seek (fast): `ffmpeg -ss 00:01:30 -i in.mp4 -c copy out.mp4` — works with `-c copy`; "
        "(2) for exact seek: `ffmpeg -i in.mp4 -ss 00:01:30 -t 30 out.mp4` — re-encodes, slower; "
        "(3) hybrid: `ffmpeg -ss 00:01:25 -i in.mp4 -ss 00:00:05 -t 30 out.mp4` — fast seek to near, then exact; "
        "(4) timestamp formats: `00:01:30.500`, `90.5`, or `90s`.",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: deprecated pixel format used, make sure you didn't mean yuvj420p",
        "Just a warning, not a failure. yuvj* formats are 'full-range' YUV (0-255), yuv* are 'limited' (16-235). "
        "Fix: (1) ignore — output works fine; "
        "(2) silence warning by using explicit `-pix_fmt yuv420p` + `-color_range tv`; "
        "(3) for full-range output (rare): `-pix_fmt yuv420p -color_range pc`; "
        "(4) most players assume limited range; full range can wash out colors.",
        f"{BASE}/ffmpeg-utils.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: Past duration X is too large",
        "Repeated warning during long encodes — audio/video PTS got out of sync. "
        "Fix: (1) audio resync: `-async 1` (legacy) or `aresample=async=1` filter; "
        "(2) video resync: `-vsync 1` (drop/duplicate to maintain fps) or `-vsync passthrough`; "
        "(3) if input has variable framerate: `-r 30` forces constant; "
        "(4) for live capture with clock drift, use `-use_wallclock_as_timestamps 1`.",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: [h264 @ ...] error while decoding MB / corrupt input",
        "Input video has corrupted macroblocks — green/glitchy frames in source. "
        "Fix: (1) tolerate errors: `-err_detect ignore_err -fflags +discardcorrupt -i bad.mp4`; "
        "(2) for severely corrupt input, only keyframes might recover: `ffmpeg -i bad.mp4 -vf select='eq(pict_type\\,I)' frames/%04d.png`; "
        "(3) for transport stream (.ts) errors, try `-fflags +discardcorrupt+nobuffer`; "
        "(4) sometimes re-muxing fixes it: `ffmpeg -i bad.mp4 -c copy fixed.mp4`.",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: hwaccel cuda / videotoolbox failed to initialize",
        "Hardware acceleration unavailable or misconfigured. "
        "Fix: (1) list available hwaccels: `ffmpeg -hwaccels`; "
        "(2) macOS: `-hwaccel videotoolbox` (built into all Apple Silicon/Intel Macs); "
        "(3) NVIDIA: install CUDA + nvidia-driver, then `-hwaccel cuda -c:v h264_cuvid -c:v h264_nvenc`; "
        "(4) Intel QuickSync: `-hwaccel qsv -c:v h264_qsv`; "
        "(5) fallback to software is safe: just drop the `-hwaccel` flag.",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: concat: unsafe filenames / Impossible to open",
        "Concat demuxer requires explicit safe mode for paths with special chars or absolute paths. "
        "Fix: (1) pass `-safe 0`: `ffmpeg -f concat -safe 0 -i list.txt -c copy out.mp4`; "
        "(2) list.txt format: each line `file '/abs/path/clip1.mp4'` with single quotes; "
        "(3) all files must have same codec/resolution/timebase for `-c copy`; otherwise use concat filter; "
        "(4) escape single quotes in filenames: `file 'O\\'Brien.mp4'`.",
        f"{BASE}/ffmpeg-formats.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: Output file is empty / 0 bytes",
        "Encoding produced no output. Multiple causes. "
        "Fix: (1) input was empty/unreadable — verify with `ffprobe`; "
        "(2) filter dropped everything — check filter logic; "
        "(3) format mismatch (e.g. encoding video to .mp3) — use right extension; "
        "(4) ran with `-vframes 0` or `-t 0` by accident; "
        "(5) Ctrl-C'd before any frame was written — wait longer or use a shorter clip for testing.",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: error opening input files: Operation not permitted (macOS)",
        "macOS privacy/security blocked access. "
        "Fix: (1) System Settings → Privacy → Files and Folders → grant Terminal/iTerm full disk access; "
        "(2) for screen recording: grant Screen Recording permission; "
        "(3) for camera: grant Camera permission to the binary; "
        "(4) try moving the file to ~/Desktop or ~/Movies (no-permission zones); "
        "(5) check with `ls -le file` for ACLs.",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: Buffer queue overflow, dropping",
        "A filter (often `overlay`, `xfade`, `concat`) can't process frames fast enough — input piles up. "
        "Fix: (1) for overlay: ensure both inputs have aligned PTS — add `setpts=PTS-STARTPTS` before overlay; "
        "(2) for concat filter: ensure all inputs have same resolution/fps (scale + fps filter first); "
        "(3) for slow filter chains: pre-render intermediate to file, then chain; "
        "(4) check CPU: filters are mostly single-threaded.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: Estimating duration from bitrate, this may be inaccurate",
        "Warning (not fatal) — input has no duration metadata, so ffmpeg guesses from bitrate. "
        "Fix: (1) ignore for static files (output will be correct); "
        "(2) for streams with no duration (RTMP, RTSP, MPEG-TS): expected; "
        "(3) to set explicit duration: `-t 60` (limit) or `-to 00:01:30` (until); "
        "(4) for accurate duration on a raw file, re-mux first: `ffmpeg -i raw.ts -c copy fixed.mp4`.",
        f"{BASE}/ffmpeg-formats.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: Cannot store exact duration in MP4 / Could not find tag for codec",
        "MP4 container can't hold a specific codec (e.g. AC3 audio, ProRes video in some builds). "
        "Fix: (1) switch container to MKV: `out.mkv` accepts almost everything; "
        "(2) transcode audio: `-c:a aac` (AC3→AAC for MP4); "
        "(3) check container support: `ffmpeg -h muxer=mp4` shows supported codecs; "
        "(4) for ProRes, output to MOV: `out.mov`.",
        f"{BASE}/ffmpeg-formats.html",
    ),
    (
        "ffmpeg-errors", "ffmpeg error: x265 [error]: input picture width/height not divisible by minimum CU size",
        "H.265 (libx265) needs dimensions divisible by 8 (or 16 for some profiles). "
        "Fix: (1) crop to nearest even: `-vf 'crop=trunc(iw/2)*2:trunc(ih/2)*2'`; "
        "(2) for divisible-by-8: `-vf 'crop=trunc(iw/8)*8:trunc(ih/8)*8'`; "
        "(3) pad instead of crop: `-vf 'pad=ceil(iw/8)*8:ceil(ih/8)*8'`; "
        "(4) common with iPhone screen recordings (390x844 etc) — needs cropping for H.265.",
        f"{BASE}/ffmpeg-codecs.html",
    ),

    # ============================================================
    # ffmpeg-recipes — 35 common workflows
    # ============================================================
    (
        "ffmpeg-recipes", "ffmpeg recipe: trim a clip without re-encoding (fast, lossless)",
        "Cut a section of video instantly. `ffmpeg -ss 00:01:30 -to 00:02:00 -i in.mp4 -c copy out.mp4`. "
        "`-c copy` skips re-encoding (no quality loss, near-instant). "
        "Caveat: cuts at nearest keyframe before -ss, so timing may be slightly off (typically <2s). "
        "For frame-accurate trim with re-encode: `ffmpeg -i in.mp4 -ss 00:01:30 -to 00:02:00 -c:v libx264 -c:a aac out.mp4` (place -ss after -i).",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: convert any video to MP4 (H.264 + AAC)",
        "Universal format. `ffmpeg -i in.mov -c:v libx264 -preset medium -crf 23 -c:a aac -b:a 128k -movflags +faststart out.mp4`. "
        "Tunable: `-crf 18` = visually lossless, `-crf 23` = good, `-crf 28` = small file. "
        "`-preset slow` = better compression, slower; `-preset ultrafast` = fast, larger. "
        "`-movflags +faststart` moves moov atom to start (web streaming).",
        f"{BASE}/ffmpeg-codecs.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: extract audio track to MP3 / AAC",
        "Audio only, drop video. MP3: `ffmpeg -i in.mp4 -vn -c:a libmp3lame -q:a 2 out.mp3` (q:a 0=best, 9=worst). "
        "AAC: `ffmpeg -i in.mp4 -vn -c:a aac -b:a 192k out.m4a`. "
        "Lossless extract (if input audio is already mp3/aac): `-vn -c:a copy out.aac`. "
        "Specific track when multiple: `-map 0:a:1` (second audio track).",
        f"{BASE}/ffmpeg-codecs.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: remove (mute) audio from video",
        "Drop audio track entirely. `ffmpeg -i in.mp4 -c copy -an out.mp4`. "
        "`-an` = no audio. `-c copy` keeps video as-is (instant). "
        "To replace silence (some players want a track): `ffmpeg -i in.mp4 -f lavfi -i anullsrc -c:v copy -c:a aac -shortest out.mp4`. "
        "To keep only specific audio language: `-map 0:v -map 0:a:m:language:eng`.",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: replace audio in video",
        "Swap soundtrack. `ffmpeg -i video.mp4 -i audio.mp3 -map 0:v -map 1:a -c:v copy -c:a aac -shortest out.mp4`. "
        "`-shortest` cuts output to whichever input ends first. "
        "For exact sync (audio shorter than video, pad silence): `-filter_complex '[1:a]apad' -shortest`. "
        "To delay audio by N seconds: `-itsoffset N -i audio.mp3` before -i.",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: concatenate multiple videos (same codec, fast)",
        "Demuxer concat for identical-codec inputs. "
        "(1) Create list.txt: `file 'part1.mp4'\\nfile 'part2.mp4'\\nfile 'part3.mp4'`. "
        "(2) Run: `ffmpeg -f concat -safe 0 -i list.txt -c copy out.mp4`. "
        "Requirements: all files MUST have same codec, resolution, fps, audio sample rate. "
        "Verify with `ffprobe`. For mixed: use concat filter (next recipe).",
        f"{BASE}/ffmpeg-formats.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: concatenate videos with different codecs (concat filter)",
        "Re-encode while joining (handles different codecs/resolutions). "
        "`ffmpeg -i a.mp4 -i b.mp4 -i c.mp4 -filter_complex "
        "'[0:v][0:a][1:v][1:a][2:v][2:a]concat=n=3:v=1:a=1[v][a]' -map '[v]' -map '[a]' out.mp4`. "
        "`n=3` is input count, `v=1` and `a=1` enable video/audio. "
        "Inputs are re-encoded — slower but works with any mix of formats.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: resize / scale video to specific dimensions",
        "Common operation. `ffmpeg -i in.mp4 -vf 'scale=1280:720' out.mp4`. "
        "Keep aspect ratio (auto width): `-vf 'scale=-2:720'` (-2 forces even number). "
        "Downscale only (skip if smaller): `-vf 'scale=min(iw\\,1280):-2'`. "
        "Common presets: 1080p=`1920:1080`, 720p=`1280:720`, 480p=`854:480`. "
        "For Apple devices, ensure dimensions are even: use `-2` not `-1`.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: crop video to specific region",
        "Cut a rectangle. `ffmpeg -i in.mp4 -vf 'crop=w:h:x:y' out.mp4` — w/h are output size, x/y is top-left of crop. "
        "Example crop center 1080x1080 square: `-vf 'crop=1080:1080:(iw-1080)/2:(ih-1080)/2'`. "
        "Crop to 16:9 from 4:3: `-vf 'crop=iw:iw*9/16'`. "
        "Auto-detect black bars (first ~3 sec): `ffmpeg -i in.mp4 -vf cropdetect -f null -` then use suggested crop.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: rotate video by 90/180/270 degrees",
        "Transpose filters. "
        "90° clockwise: `-vf 'transpose=1'`. "
        "90° counter-clockwise: `-vf 'transpose=2'`. "
        "180°: `-vf 'transpose=2,transpose=2'` or `-vf 'hflip,vflip'`. "
        "Arbitrary angle: `-vf 'rotate=30*PI/180:fillcolor=black'`. "
        "Some phones store rotation as metadata; strip it: `-metadata:s:v:0 rotate=0` or `-noautorotate`.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: change video speed (fast/slow motion)",
        "Speed up 2x (video + audio): `ffmpeg -i in.mp4 -filter_complex '[0:v]setpts=0.5*PTS[v];[0:a]atempo=2.0[a]' -map '[v]' -map '[a]' out.mp4`. "
        "Slow down 0.5x: `setpts=2.0*PTS` and `atempo=0.5`. "
        "`atempo` range is 0.5–2.0 — chain for more: `atempo=2.0,atempo=2.0` = 4x. "
        "Video-only fast: `-vf 'setpts=0.5*PTS' -an`.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: take a screenshot / single frame at timestamp",
        "Extract a frame. `ffmpeg -ss 00:01:30 -i in.mp4 -vframes 1 -q:v 2 frame.jpg`. "
        "`-q:v 2` = high JPEG quality (1=best, 31=worst). "
        "PNG (lossless): `frame.png`. "
        "First keyframe only (fastest): place -ss before -i (above). "
        "Frame-exact (slower): `ffmpeg -i in.mp4 -ss 00:01:30 -vframes 1 frame.png`.",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: generate thumbnail grid (contact sheet)",
        "Overview image. `ffmpeg -i in.mp4 -vf 'fps=1/60,scale=320:-1,tile=4x4' contact.png`. "
        "`fps=1/60` = one frame per minute, `tile=4x4` = 16-thumbnail grid. "
        "For longer videos: `fps=1/300` (every 5 min). "
        "Add timestamps: `-vf 'fps=1/60,scale=320:-1,drawtext=text=%{pts\\\\:hms}:fontsize=20:x=10:y=10:fontcolor=white,tile=4x4'`.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: convert video to animated GIF",
        "Two-pass for quality (single-pass gives banding). "
        "Pass 1 (palette): `ffmpeg -i in.mp4 -vf 'fps=10,scale=480:-1:flags=lanczos,palettegen' palette.png`. "
        "Pass 2 (use palette): `ffmpeg -i in.mp4 -i palette.png -filter_complex 'fps=10,scale=480:-1:flags=lanczos[x];[x][1:v]paletteuse' out.gif`. "
        "Smaller: lower fps (8-12), smaller scale (320), or convert to mp4 instead (most platforms autoplay).",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: reverse a video",
        "Play backward. `ffmpeg -i in.mp4 -vf reverse -af areverse out.mp4`. "
        "Both filters buffer entire input in RAM — for long videos: split into chunks first. "
        "Video-only reverse (silent): `-vf reverse -an`. "
        "Reverse loop effect (forward then backward): `ffmpeg -i in.mp4 -filter_complex '[0]reverse[r];[0][r]concat=n=2:v=1' out.mp4`.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: burn (hardcode) subtitles into video",
        "Permanent subtitles in pixels. From .srt: `ffmpeg -i in.mp4 -vf 'subtitles=subs.srt' -c:a copy out.mp4`. "
        "With styling: `-vf 'subtitles=subs.srt:force_style=Fontsize=24,PrimaryColour=&HFFFFFF,OutlineColour=&H000000'`. "
        "From .ass (advanced subs): `-vf 'ass=subs.ass'`. "
        "Burn one specific track from MKV: `-vf 'subtitles=in.mkv:si=0'` (subtitle index 0).",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: mux soft subtitles into MP4/MKV",
        "Embed subtitles as separate track (toggleable). "
        "MKV: `ffmpeg -i in.mp4 -i subs.srt -c:v copy -c:a copy -c:s srt out.mkv`. "
        "MP4 needs mov_text codec: `ffmpeg -i in.mp4 -i subs.srt -c:v copy -c:a copy -c:s mov_text -metadata:s:s:0 language=eng out.mp4`. "
        "Multiple languages: add `-i en.srt -i es.srt -map 0 -map 1 -map 2 -metadata:s:s:0 language=eng -metadata:s:s:1 language=spa`.",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: add watermark / overlay image on video",
        "Logo bottom-right. `ffmpeg -i in.mp4 -i logo.png -filter_complex 'overlay=W-w-10:H-h-10' out.mp4`. "
        "W/H = main video, w/h = overlay. Positions: top-left=`10:10`, top-right=`W-w-10:10`, bottom-left=`10:H-h-10`, center=`(W-w)/2:(H-h)/2`. "
        "Semi-transparent: pre-process logo with alpha or `-filter_complex '[1:v]format=rgba,colorchannelmixer=aa=0.5[lg];[0:v][lg]overlay=10:10'`. "
        "Timed (10-20s only): `overlay=10:10:enable='between(t,10,20)'`.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: HLS live streaming output",
        "Apple HTTP Live Streaming segments. "
        "`ffmpeg -i in.mp4 -c:v libx264 -c:a aac -hls_time 6 -hls_list_size 0 -f hls out.m3u8`. "
        "Produces out.m3u8 (playlist) + out0.ts, out1.ts ... segments. "
        "Live (keep rolling window): `-hls_list_size 5 -hls_flags delete_segments`. "
        "Multi-bitrate: needs `-filter_complex` with split + scale per quality level.",
        f"{BASE}/ffmpeg-formats.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: stream to RTMP (YouTube/Twitch live)",
        "Push to streaming endpoint. "
        "`ffmpeg -re -i in.mp4 -c:v libx264 -preset veryfast -b:v 3000k -c:a aac -b:a 160k -f flv rtmp://a.rtmp.youtube.com/live2/<stream-key>`. "
        "`-re` reads input at native rate (real-time, for VOD→live). "
        "Skip `-re` for live capture inputs. "
        "Twitch: `rtmp://live.twitch.tv/app/<stream-key>`.",
        f"{BASE}/ffmpeg-protocols.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: loop video N times",
        "Repeat input. Loop demuxer: `ffmpeg -stream_loop 4 -i in.mp4 -c copy out.mp4` (plays 5x — initial + 4 loops). "
        "Infinite: `-stream_loop -1` (only useful for streaming output). "
        "GIF loop forever: append `-loop 0` for the output. "
        "Loop concat (more reliable for re-encode): list same file N times in concat list.txt.",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: generate silent audio file",
        "Silence of specific duration. `ffmpeg -f lavfi -i anullsrc=channel_layout=stereo:sample_rate=44100 -t 60 silence.mp3`. "
        "For WAV: `silence.wav`. "
        "For specific format: `-f lavfi -i anullsrc=cl=mono:r=16000 -t 5 silence.wav` (16kHz mono). "
        "Useful for padding audio tracks or creating timing.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: generate test pattern video",
        "Synthetic video for testing. "
        "Color bars: `ffmpeg -f lavfi -i 'smptebars=size=1920x1080:rate=30' -t 10 bars.mp4`. "
        "Solid color: `-i 'color=c=red:size=1920x1080:rate=30'`. "
        "With test tone: `-f lavfi -i 'sine=frequency=1000:sample_rate=48000'`. "
        "Combine: `-f lavfi -i 'testsrc=size=1280x720:rate=30' -f lavfi -i 'sine=frequency=440' -t 10 test.mp4`.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: capture webcam on macOS (avfoundation)",
        "Mac built-in camera. List devices: `ffmpeg -f avfoundation -list_devices true -i ''`. "
        "Capture: `ffmpeg -f avfoundation -framerate 30 -i '0' -c:v libx264 -preset ultrafast cam.mp4`. "
        "Camera + mic: `-i '0:1'` (video device 0, audio device 1). "
        "Specific resolution: `-video_size 1280x720` before -i. "
        "Press 'q' to stop cleanly.",
        f"{BASE}/ffmpeg-devices.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: record screen on macOS",
        "Screen capture via avfoundation. List screens: `ffmpeg -f avfoundation -list_devices true -i ''`. "
        "Screen 1 is typically '1' or '2' (after cameras). "
        "Capture: `ffmpeg -f avfoundation -framerate 30 -i '1' -c:v libx264 -preset ultrafast -crf 23 screen.mp4`. "
        "With system audio (needs BlackHole/Loopback): `-i '1:2'`. "
        "Requires Screen Recording permission in System Settings → Privacy.",
        f"{BASE}/ffmpeg-devices.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: two-pass H.264 encode for target bitrate",
        "Better quality at a known file size. "
        "Pass 1: `ffmpeg -y -i in.mp4 -c:v libx264 -b:v 2M -pass 1 -an -f null /dev/null`. "
        "Pass 2: `ffmpeg -i in.mp4 -c:v libx264 -b:v 2M -pass 2 -c:a aac -b:a 128k out.mp4`. "
        "Useful when you NEED a specific file size (-b:v 2M ≈ ~15 MB/min). "
        "For quality-first, prefer single-pass CRF (`-crf 23`).",
        f"{BASE}/ffmpeg-codecs.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: convert to H.265 (HEVC) for smaller files",
        "About 50% smaller than H.264 at same quality. "
        "`ffmpeg -i in.mp4 -c:v libx265 -preset medium -crf 28 -c:a aac -b:a 128k out.mp4`. "
        "CRF guide: 18=lossless, 24=high, 28=default, 32=small. "
        "MP4 needs the HVC1 tag for Apple compat: `-tag:v hvc1`. "
        "Caveats: slower encode; older players don't support; iOS Photos prefers .mov container.",
        f"{BASE}/ffmpeg-codecs.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: hardware-accelerated encode (videotoolbox on macOS)",
        "Fast encoding using GPU. "
        "H.264: `ffmpeg -i in.mp4 -c:v h264_videotoolbox -b:v 5M -c:a aac out.mp4`. "
        "HEVC: `-c:v hevc_videotoolbox -b:v 3M`. "
        "Much faster than libx264 but lower compression efficiency (need higher bitrate for same quality). "
        "Decode also: `-hwaccel videotoolbox -i in.mp4`. "
        "NVIDIA equivalent: `-c:v h264_nvenc` (NVENC).",
        f"{BASE}/ffmpeg-codecs.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: inspect media file with ffprobe",
        "Show streams + metadata. Basic: `ffprobe in.mp4` (human-readable). "
        "JSON output: `ffprobe -v error -show_streams -show_format -of json in.mp4`. "
        "Just duration: `ffprobe -v error -show_entries format=duration -of csv=p=0 in.mp4`. "
        "Just video codec: `ffprobe -v error -select_streams v:0 -show_entries stream=codec_name -of csv=p=0 in.mp4`. "
        "Frame-by-frame: `ffprobe -show_frames in.mp4`.",
        f"{BASE}/ffprobe.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: fade in / fade out video and audio",
        "Smooth opening/closing. "
        "Video fade in (0-3s): `-vf 'fade=t=in:st=0:d=3'`. "
        "Video fade out (last 3s of 60s clip): `-vf 'fade=t=out:st=57:d=3'`. "
        "Both: `-vf 'fade=t=in:st=0:d=3,fade=t=out:st=57:d=3'`. "
        "Audio fade: `-af 'afade=t=in:st=0:d=3,afade=t=out:st=57:d=3'`. "
        "Combined: use `-filter_complex` and map both.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: mix two audio tracks together",
        "Overlay sound (e.g., music + voiceover). "
        "`ffmpeg -i voice.mp3 -i music.mp3 -filter_complex 'amix=inputs=2:duration=longest' mixed.mp3`. "
        "Volume control per input: `-filter_complex '[0:a]volume=1.0[a0];[1:a]volume=0.3[a1];[a0][a1]amix=inputs=2'`. "
        "Side-by-side stereo (no mix): `-filter_complex '[0:a][1:a]amerge=inputs=2'`. "
        "Duck music while voice plays: needs `sidechaincompress` filter.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: extract frames as PNG sequence",
        "Every frame. `ffmpeg -i in.mp4 frame_%04d.png` (frame_0001.png ...). "
        "Every 5 sec: `ffmpeg -i in.mp4 -vf 'fps=1/5' frame_%04d.png`. "
        "Only keyframes (I-frames): `ffmpeg -i in.mp4 -vf 'select=eq(pict_type\\,I)' -vsync vfr keyframe_%04d.png`. "
        "Specific range: `-ss 00:01:00 -t 10 -i in.mp4 frame_%04d.png`. "
        "Reverse (frames → video): `ffmpeg -framerate 30 -i frame_%04d.png out.mp4`.",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: normalize audio volume (loudness)",
        "Match broadcast loudness standard. "
        "One-pass: `ffmpeg -i in.mp4 -af 'loudnorm=I=-16:LRA=11:TP=-1.5' out.mp4` (target: -16 LUFS, common for web). "
        "Streaming defaults: YouTube/Spotify ~-14 LUFS, broadcast -23 LUFS. "
        "Two-pass (more accurate): pass 1 prints measurements (`-af 'loudnorm=I=-16:print_format=json'`), pass 2 uses them. "
        "Simple peak normalize: `-af 'dynaudnorm'` or `-af 'volume=2.0'`.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: change video frame rate (fps)",
        "Re-time without changing duration. To 30fps: `ffmpeg -i in.mp4 -vf 'fps=30' -c:a copy out.mp4`. "
        "For variable→constant fps: `-vf fps=30 -vsync cfr`. "
        "Slow without sound change (interpolated, smooth slo-mo): `-vf 'minterpolate=fps=60'` (slow, may artifact). "
        "Decimate (drop every other frame, half fps): `-vf 'decimate'`. "
        "For 24fps cinema: `-vf 'fps=24'`.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: split video into chunks of N seconds",
        "Segment muxer. `ffmpeg -i in.mp4 -c copy -map 0 -segment_time 60 -reset_timestamps 1 -f segment chunk_%03d.mp4`. "
        "Creates chunk_000.mp4, chunk_001.mp4 ... each ~60 sec. "
        "Cuts at keyframes (not exact). For exact cuts: re-encode (drop `-c copy`). "
        "By size: not directly supported — calculate time from bitrate. "
        "For HLS-style: see HLS recipe.",
        f"{BASE}/ffmpeg-formats.html",
    ),
    (
        "ffmpeg-recipes", "ffmpeg recipe: deinterlace footage",
        "Remove interlace artifacts (from old TV/camera). "
        "Simple: `-vf yadif`. "
        "Higher quality: `-vf bwdif` or `-vf 'yadif=mode=1:parity=auto'`. "
        "Double framerate (use both fields): `-vf 'yadif=mode=1'` then expect 60fps output from 30i. "
        "To detect interlacing: `ffmpeg -i in.mp4 -vf idet -f null -` (look for TFF/BFF/Progressive counts).",
        f"{BASE}/ffmpeg-filters.html",
    ),

    # ============================================================
    # ffmpeg-cli — 8 flag-specific claims (close index-page gaps)
    # ============================================================
    (
        "ffmpeg-cli", "ffmpeg -map (stream mapping)",
        "`-map [-]input_file_id[:stream_specifier]` selects which input streams go to which output. "
        "Examples: `-map 0:v` = all video from input 0, `-map 0:v:0` = first video stream, `-map 0:a:m:language:eng` = English audio. "
        "Negative excludes: `-map 0 -map -0:s` = everything except subtitles. "
        "For filter_complex outputs: `-map '[outlabel]'`. Without -map, ffmpeg picks one stream of each type automatically.",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-cli", "ffmpeg -y / -n (overwrite output)",
        "`-y` = overwrite output file without asking. `-n` = never overwrite (exit error if file exists). "
        "Default behavior is to prompt interactively, which breaks scripts. Always pass `-y` in CI/batch scripts. "
        "Example: `ffmpeg -y -i in.mp4 -c:v libx264 out.mp4`.",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-cli", "ffmpeg -b:v (set video bitrate)",
        "`-b:v 2M` sets target video bitrate to 2 megabits/sec. Suffixes: K, M, G. "
        "Per-stream variant: `-b:v:0 5M` (first video stream). "
        "Combine with `-maxrate` + `-bufsize` for streaming: `-b:v 3M -maxrate 3M -bufsize 6M`. "
        "Quality-first alternative (single-pass): `-crf 23` (libx264) — usually preferred over fixed bitrate.",
        f"{BASE}/ffmpeg-codecs.html",
    ),
    (
        "ffmpeg-cli", "ffmpeg -c / -codec (set codec)",
        "`-c:v libx264` = video codec, `-c:a aac` = audio codec, `-c:s mov_text` = subtitle codec. "
        "Stream-copy (no re-encode): `-c:v copy` or `-c copy` (all streams). "
        "List available: `ffmpeg -encoders` or `ffmpeg -decoders`. "
        "Per-stream: `-c:v:0 libx264 -c:v:1 libx265` (different per stream).",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-cli", "ffmpeg -ss (seek input)",
        "`-ss <time>` seeks to position. Placement matters: BEFORE `-i` = fast (keyframe-aligned), AFTER `-i` = accurate (decodes from start). "
        "Time formats: `00:01:30`, `00:01:30.500`, or `90.5` (seconds). "
        "For trim with stream-copy use before: `-ss 00:01:30 -i in.mp4 -c copy`. "
        "Pair with `-t <duration>` or `-to <endtime>`.",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-cli", "ffmpeg -t / -to (duration / end-time)",
        "`-t 30` = output 30 seconds. `-to 00:02:00` = output until timestamp 2:00. "
        "If both given, `-t` wins. Position relative to `-ss` if seeking. "
        "Example: `ffmpeg -ss 60 -t 30 -i in.mp4 out.mp4` = 30 sec starting at 1:00.",
        f"{BASE}/ffmpeg.html",
    ),
    (
        "ffmpeg-cli", "ffmpeg -vf / -af / -filter_complex (apply filters)",
        "`-vf <chain>` = video filters on default video stream. `-af <chain>` = audio filters. "
        "`-filter_complex <graph>` = multi-input / multi-output filters (overlay, concat, mix). "
        "Chain filters with comma: `-vf 'scale=1280:720,format=yuv420p'`. "
        "Sequence multiple chains with semicolon: `-filter_complex '[0:v]scale=1280:720[v];[v][1:v]overlay=10:10'`.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-cli", "ffmpeg -loglevel / -v (verbosity)",
        "`-loglevel <level>` or shorthand `-v <level>`. "
        "Levels: quiet, panic, fatal, error, warning, info (default), verbose, debug, trace. "
        "Silent for scripts: `-loglevel error` (errors only, no progress). "
        "Debug a problem: `-loglevel debug` or `-v debug`. "
        "Hide banner: `-hide_banner`. Combine: `-hide_banner -loglevel error`.",
        f"{BASE}/ffmpeg.html",
    ),

    # ============================================================
    # ffmpeg-filters — 4 specific filter claims (the index page is too thin)
    # ============================================================
    (
        "ffmpeg-filters", "ffmpeg scale filter (resize video)",
        "`scale=<width>:<height>` resizes video. "
        "Specific size: `scale=1280:720`. Auto width preserving aspect: `scale=-2:720` (-2 forces even, -1 may be odd). "
        "Max constraint: `scale='min(1920,iw)':-2`. With algorithm: `scale=1280:720:flags=lanczos` (lanczos = high quality). "
        "Available flags: fast_bilinear, bilinear, bicubic, lanczos (default for downscale), spline.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-filters", "ffmpeg overlay filter (composite video)",
        "`overlay=<x>:<y>` places one video on top of another. "
        "Top-left at (10,10): `overlay=10:10`. Bottom-right: `overlay=W-w-10:H-h-10` (W/H=main, w/h=overlay). "
        "Center: `overlay=(W-w)/2:(H-h)/2`. Time-gated: `overlay=10:10:enable='between(t,5,15)'` (only seconds 5-15). "
        "Used in `-filter_complex`: `[0:v][1:v]overlay=10:10[out]`.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-filters", "ffmpeg crop filter",
        "`crop=<w>:<h>:<x>:<y>` cuts a rectangle. w/h = output size, x/y = top-left of crop region. "
        "Center crop 1080x1080: `crop=1080:1080:(iw-1080)/2:(ih-1080)/2`. "
        "Crop to even dimensions (required for H.264): `crop=trunc(iw/2)*2:trunc(ih/2)*2`. "
        "Auto-detect black bars: run `ffmpeg -i in.mp4 -vf cropdetect -f null -` first.",
        f"{BASE}/ffmpeg-filters.html",
    ),
    (
        "ffmpeg-filters", "ffmpeg drawtext filter (text overlay)",
        "Add text on video. `drawtext=fontfile=/path/to/font.ttf:text='Hello':fontsize=48:fontcolor=white:x=20:y=20`. "
        "Use system font (macOS): `fontfile=/System/Library/Fonts/Helvetica.ttc`. "
        "Dynamic text: `text='%{pts\\\\:hms}'` (timestamp), `text='%{frame_num}'`. "
        "With background box: `drawtext=...:box=1:boxcolor=black@0.5:boxborderw=10`. "
        "Requires `--enable-libfreetype`.",
        f"{BASE}/ffmpeg-filters.html",
    ),
]


def main() -> None:
    store = get_claim_store()
    orchestrator = CanonOrchestrator(store)
    svc = EmbeddingService.get_default()
    now = datetime.now(timezone.utc)
    ok = 0
    failed = 0
    for i, (tool_id, subject, statement, evidence_url) in enumerate(CLAIMS):
        suffix = f"{i:03d}"
        body_for_hash = f"{subject}\n{statement}"
        evidence = Evidence(
            evidence_id=f"ev-ffmpeg-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-ffmpeg-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="ffmpeg-catalog",
            evidence=[evidence],
            risk_level=RiskLevel.LOW,
            created_at=now,
        )
        try:
            store.create(claim)
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ #{suffix} create: {type(exc).__name__}: {exc}")
            failed += 1
            continue
        try:
            verification = orchestrator.verify_claim(claim)
            store.save_verification_result(verification)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ #{suffix} verify: {type(exc).__name__}: {exc}")
        text = claim_embedding_text(subject=subject, statement=statement)
        vec = svc.embed_one(text)
        store.set_embedding(claim.claim_id, serialize_embedding(vec))
        ok += 1
        print(f"  ✓ #{suffix} [{tool_id}] {subject[:70]}")
    print(f"\nDone: {ok} created / {failed} failed")


if __name__ == "__main__":
    main()
