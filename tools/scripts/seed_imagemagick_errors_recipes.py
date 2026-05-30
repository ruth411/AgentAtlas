"""Direct-insert imagemagick error + recipe claims."""

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

BASE = "https://imagemagick.org/script"

CLAIMS = [
    # ============================================================
    # imagemagick-errors — 30 common errors
    # ============================================================
    (
        "imagemagick-errors", "imagemagick error: unable to open image '<file>': No such file or directory",
        "Input file missing or wrong path. "
        "Fix: (1) check spelling: `ls <file>`; "
        "(2) absolute path is safest: `/full/path/to/img.png`; "
        "(3) for piped input: `cat img.png | magick - out.jpg` (use `-` for stdin); "
        "(4) for batch: `magick *.png out.jpg` requires shell glob to actually match files; "
        "(5) permissions: `ls -l` — magick can't read unreadable files.",
        f"{BASE}/command-line-tools.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: no decode delegate for this image format",
        "ImageMagick built without support for that format (libpng, libjpeg, libwebp, libheif, libraw etc missing at build). "
        "Fix: (1) check delegates: `magick -list format` (or `magick -version` shows linked libraries); "
        "(2) install missing: macOS `brew install imagemagick --with-webp --with-heif`; Linux apt `imagemagick libmagickwand-dev libwebp-dev libheif-dev`; "
        "(3) for HEIC (iPhone): needs libheif + ghostscript-fonts; "
        "(4) reinstall after deps: `brew reinstall imagemagick`.",
        f"{BASE}/formats.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: cache resources exhausted / memory allocation failed",
        "Resource limit hit (memory, map, disk, or area). Default limits are conservative. "
        "Fix: (1) check current: `magick -list resource`; "
        "(2) raise via env: `MAGICK_MEMORY_LIMIT=2GiB MAGICK_MAP_LIMIT=4GiB magick big.tif out.png`; "
        "(3) edit policy.xml: `<policy domain=\"resource\" name=\"memory\" value=\"4GiB\"/>`; "
        "(4) for huge images: stream-process via `-limit thread 1 -define stream:buffer-size=256m`; "
        "(5) for OCR/PDF: usually need bigger disk limit (`MAGICK_DISK_LIMIT`).",
        f"{BASE}/resources.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: not authorized 'PDF' / 'PS' / 'EPS' (PolicyError)",
        "Famous post-CVE-2016 default policy.xml blocks PDF/PS/EPS reads. "
        "Fix: (1) find policy.xml: `magick -list policy | head` or `find / -name policy.xml 2>/dev/null`; "
        "(2) if you trust input, comment out: `<!-- <policy domain=\"coder\" rights=\"none\" pattern=\"PDF\" /> -->`; "
        "(3) safer: allow only specific operations: `rights=\"read|write\"`; "
        "(4) for one-off: env override `MAGICK_CONFIGURE_PATH=/path/to/custom-policy/`; "
        "(5) NEVER disable on a server that accepts untrusted user uploads — policy exists for security reasons.",
        f"{BASE}/security-policy.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: delegate failed (Ghostscript / pdf2png)",
        "ImageMagick uses external tools (Ghostscript for PDF, libheif via heif-convert) and the delegate isn't installed/findable. "
        "Fix: (1) for PDF/PS: `brew install ghostscript` (macOS) or `apt install ghostscript` (Linux); "
        "(2) check delegates: `magick -list delegate | grep pdf`; "
        "(3) for SVG: install `librsvg` or `inkscape`; "
        "(4) for HEIC: `brew install libheif`; "
        "(5) verify gs is on PATH: `which gs`.",
        f"{BASE}/formats.php",
    ),
    (
        "imagemagick-errors", "imagemagick warning: The convert command is deprecated in IMv7",
        "Informational (not an error). IM 7 unified all tools into `magick`. `convert` still works but is legacy. "
        "Fix: (1) replace `convert` with `magick`: `magick in.png out.jpg`; "
        "(2) for identify: `magick identify` (or just `identify` if you alias); "
        "(3) for compatibility on systems with both: `magick -compatibility` shows IM6/IM7 difference; "
        "(4) for old scripts: `convert` → `magick` is a 1:1 swap in 99% of cases.",
        f"{BASE}/magick.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: missing an image filename",
        "Forgot input or output file. magick needs at least input + output paths. "
        "Fix: (1) basic: `magick input.png output.jpg`; "
        "(2) for processing pipelines: input first, output last: `magick input.png -resize 50% output.png`; "
        "(3) for stdin: `magick - output.jpg < input.png`; "
        "(4) for in-place modification: use `mogrify` (NOT `magick`).",
        f"{BASE}/command-line-processing.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: unrecognized option '-X' / unknown option",
        "Option typo, or version mismatch (IM7 added/renamed some options). "
        "Fix: (1) list all options: `magick -list option`; "
        "(2) check syntax: `magick -help`; "
        "(3) common confusion: `-resize` (not `--resize`); "
        "(4) option ordering MATTERS in IM — `-density 300 input.pdf` works but `input.pdf -density 300` doesn't (density must precede the input it affects).",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: improper image header / unknown image format",
        "File is corrupt, truncated, or not actually the format claimed by extension. "
        "Fix: (1) check actual type: `file input.jpg` or `magick identify input.jpg`; "
        "(2) for download issues: re-fetch source; "
        "(3) repair partial JPEG: `jpegtran -copy none input.jpg > out.jpg` (external tool); "
        "(4) for unknown extension: force format: `magick TIFF:input.dat out.png` (prefix `FORMAT:`).",
        f"{BASE}/formats.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: JPEG datastream contains no image / premature end of JPEG file",
        "JPEG is corrupt — truncated mid-data or has no actual image data. "
        "Fix: (1) check file size: `ls -l file.jpg` — under ~1KB is suspicious; "
        "(2) re-download or re-export from source; "
        "(3) repair tools: `jpeginfo -c file.jpg` (third-party); "
        "(4) sometimes recoverable: `magick -define jpeg:ignore-warnings=true input.jpg out.png`.",
        f"{BASE}/formats.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: width or height exceeds limit",
        "Image is bigger than policy.xml's `width`/`height` limit (default 16KP × 16KP). "
        "Fix: (1) check: `magick -list resource`; "
        "(2) bump policy.xml: `<policy domain=\"resource\" name=\"width\" value=\"64KP\"/>` and `name=\"height\"`; "
        "(3) env: `MAGICK_AREA_LIMIT=8GP MAGICK_WIDTH_LIMIT=64KP magick big.tif out.png`; "
        "(4) untrusted input: leave low for security — DoS protection.",
        f"{BASE}/security-policy.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: TIFF Invalid TIFF directory / IFD",
        "TIFF metadata is corrupted or uses a feature your libtiff doesn't support. "
        "Fix: (1) try forcing strip: `magick input.tif -strip out.tif`; "
        "(2) repair with tiffinfo / tiffcp (external tools): `tiffcp -c none input.tif fixed.tif`; "
        "(3) for proprietary TIFF variants (BigTIFF, geo-TIFF): need libtiff 4+ with extensions; "
        "(4) try converting to intermediate format: `magick input.tif intermediate.png && magick intermediate.png final.tif`.",
        f"{BASE}/formats.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: unable to extend pixel cache",
        "Disk-based pixel cache ran out of space (large images get spilled to disk). "
        "Fix: (1) check `MAGICK_TMPDIR` (defaults to /tmp): `echo $MAGICK_TMPDIR`; "
        "(2) point to a bigger filesystem: `MAGICK_TMPDIR=/var/big-disk magick big.tif out.png`; "
        "(3) raise disk limit in policy.xml: `<policy domain=\"resource\" name=\"disk\" value=\"50GiB\"/>`; "
        "(4) clean up: `rm /tmp/magick-*` (leftover from prior crashes).",
        f"{BASE}/resources.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: no images defined",
        "No input image was successfully read before an output operation. Usually means input failed silently. "
        "Fix: (1) check that the input load worked: `magick identify input.png` first; "
        "(2) for piped: ensure stdin had data: `cat input.png | magick - out.jpg`; "
        "(3) common cause: shell glob matched zero files: `magick *.png out.jpg` with no PNGs; "
        "(4) ordering: input file must come before output operations.",
        f"{BASE}/command-line-processing.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: unable to set font / font not found",
        "Font specified in `-font` doesn't exist. "
        "Fix: (1) list available: `magick -list font` (or `convert -list font`); "
        "(2) use direct path: `-font /path/to/font.ttf`; "
        "(3) common system paths: macOS `/System/Library/Fonts/`, Linux `/usr/share/fonts/`; "
        "(4) install Ghostscript fonts for default labels: `brew install ghostscript-fonts` / `apt install gsfonts`; "
        "(5) for Linux+Docker: explicitly mount fonts or install in image.",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: MissingDelegateError / 'png' / 'jpeg' / 'webp' not supported",
        "Specific format library wasn't linked at build time. "
        "Fix: (1) check: `magick -list format | grep -i jpeg` shows whether JPG read/write is supported; "
        "(2) macOS rebuild: `brew reinstall imagemagick`; "
        "(3) Linux apt: `apt install libwebp-dev libheif-dev libjpeg-dev libpng-dev` then rebuild source; "
        "(4) for Docker: use `dpokidov/imagemagick` (full-featured); "
        "(5) verify: `magick -version` should list `webp` etc in 'Delegates' line.",
        f"{BASE}/formats.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: geometry does not contain image",
        "Crop/extract geometry would produce zero-size output (negative offset, too-large offset, etc). "
        "Fix: (1) syntax: `-crop WxH+X+Y` (X,Y are offsets from top-left); "
        "(2) check image dimensions: `identify input.png`; "
        "(3) for relative: `-crop 50%x100%+0+0` (half-width strip); "
        "(4) for tiles: `-crop 256x256` (no +X+Y produces N tiles).",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: colorspace error / unable to convert to CMYK",
        "Conversion between colorspaces without ICC profile, or profile-less RGB→CMYK request. "
        "Fix: (1) explicit profile: `magick rgb.png -profile /path/to/CMYK.icc -colorspace CMYK out.tif`; "
        "(2) for screen→print: get the printer's ICC profile or use FOGRA39/SWOP defaults; "
        "(3) basic naive (good enough for proofs): `magick rgb.png -colorspace CMYK out.tif`; "
        "(4) check current colorspace: `identify -format '%[colorspace]' input.png`.",
        f"{BASE}/color-management.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: negative or zero image size",
        "Operation produced 0-pixel result (crop way out of bounds, resize to 0%, etc.). "
        "Fix: (1) re-check `-crop` / `-resize` geometry; "
        "(2) for `-resize 0` (typo): use percent: `-resize 50%`; "
        "(3) for crop: must overlap with actual image; "
        "(4) for tiled montage: ensure each cell is non-zero.",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: image sequence is required",
        "Operation needs multiple input images (montage, animate, fx multi-frame). "
        "Fix: (1) pass multiple: `magick a.png b.png c.png -append column.png`; "
        "(2) for animated GIF: `magick frame*.png -delay 10 -loop 0 out.gif`; "
        "(3) for montage: `magick montage 'frame*.png' -tile 4x4 grid.png`; "
        "(4) for `compare`: needs exactly 2: `magick compare a.png b.png diff.png`.",
        f"{BASE}/command-line-processing.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: unable to read configure file",
        "ImageMagick can't find magick.xml / policy.xml / delegates.xml. "
        "Fix: (1) `magick -list configure` shows where it looks; "
        "(2) reinstall: `brew reinstall imagemagick` or `apt reinstall imagemagick`; "
        "(3) point to custom: `MAGICK_CONFIGURE_PATH=/path/to/configs/`; "
        "(4) Docker: ensure /etc/ImageMagick-7/ is present in image; "
        "(5) check perms: configure dir must be readable by magick user.",
        f"{BASE}/architecture.php",
    ),
    (
        "imagemagick-errors", "imagemagick warning: -density / -units must come BEFORE input",
        "Some options affect how input is INTERPRETED — they must precede the input filename. "
        "Fix: (1) correct: `magick -density 300 input.pdf -resize 50% output.png`; "
        "(2) wrong: `magick input.pdf -density 300 out.png` (density applied AFTER load — too late for PDF rasterization); "
        "(3) general rule: 'global' options like -density, -depth, -units come first; transformation options come after input.",
        f"{BASE}/command-line-processing.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: WebP coder error / no webp encoder",
        "libwebp not linked. "
        "Fix: (1) check: `magick -list format | grep -i webp`; "
        "(2) install: macOS `brew install webp && brew reinstall imagemagick`; Linux `apt install libwebp-dev` + rebuild; "
        "(3) for quick alternative: use `cwebp` external tool for WebP encoding, then move on with magick for the rest; "
        "(4) Docker: `dpokidov/imagemagick` includes webp.",
        f"{BASE}/formats.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: HEIC / HEIF input failed",
        "Apple HEIC needs libheif at build time. "
        "Fix: (1) check: `magick -list format | grep -i heic`; "
        "(2) macOS: `brew install libheif && brew reinstall imagemagick`; "
        "(3) Linux: `apt install libheif-dev` + rebuild from source; "
        "(4) Docker (recommended): `dpokidov/imagemagick:7-bookworm` ships heif; "
        "(5) fallback: convert HEIC to JPG with `sips -s format jpeg in.heic --out tmp.jpg` (macOS only) then process tmp.jpg.",
        f"{BASE}/formats.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: unable to open file '/tmp/magick-XXXXX'",
        "Temp directory unwritable or full. "
        "Fix: (1) check: `df /tmp` — should have space; `ls -ld /tmp` — should be `drwxrwxrwt`; "
        "(2) use different temp: `MAGICK_TMPDIR=/var/big-tmp magick big.tif out.png`; "
        "(3) clean orphans: `rm /tmp/magick-*` (from prior crashes); "
        "(4) Docker: ensure /tmp is writeable + has space.",
        f"{BASE}/resources.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: insufficient image data on read",
        "Input stream cut off before all pixel data arrived (often piped input from a process that died). "
        "Fix: (1) for HTTP-fed input: ensure full download before pipe: `curl -fsSL url | magick - out.jpg` (the `-f` fails on 4xx so pipe doesn't get partial); "
        "(2) for compressed input: ensure decompression completes — pipe order matters: `cat raw.gz | gunzip - | magick - out.jpg`; "
        "(3) check the file actually has data: `wc -c input.png`.",
        f"{BASE}/formats.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: cannot allocate pixel cache / OutOfMemory",
        "Operation exceeded available RAM (and possibly disk fallback too). "
        "Fix: (1) check resource limits: `magick -list resource`; "
        "(2) reduce: use `-thumbnail` instead of `-resize` (cheaper, streams in chunks); "
        "(3) for very large input: pre-shrink: `magick big.tif -define jpeg:size=2000x2000 small.jpg`; "
        "(4) raise limits: env `MAGICK_MEMORY_LIMIT=8GiB MAGICK_MAP_LIMIT=16GiB` or policy.xml.",
        f"{BASE}/resources.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: ImageMagick is not installed / command not found",
        "Not installed or not on PATH. "
        "Fix: (1) verify: `magick --version` or `convert --version`; "
        "(2) install macOS: `brew install imagemagick`; "
        "(3) Linux: `apt install imagemagick` or `dnf install ImageMagick`; "
        "(4) IM7 binary is `magick`; IM6 was `convert`/`identify`/`mogrify` (separate). Some distros still install IM6 by default; "
        "(5) for Docker: `dpokidov/imagemagick` is a popular IM7 image.",
        f"{BASE}/binary-releases.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: -auto-orient not respected / EXIF orientation ignored",
        "ImageMagick reads EXIF orientation but only rotates pixels if you explicitly ask. "
        "Fix: (1) add `-auto-orient`: `magick input.jpg -auto-orient out.jpg`; "
        "(2) then strip: `-auto-orient -strip` (removes EXIF after rotation); "
        "(3) for batch: `mogrify -auto-orient *.jpg`; "
        "(4) verify: `identify -format '%[orientation]' out.jpg` should say 'Undefined' or 'TopLeft' after auto-orient.",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-errors", "imagemagick error: animation timing wrong / frames out of order",
        "Animated GIF/WebP frame order or delays don't match expectation. "
        "Fix: (1) explicit ordering: pass files in sequence: `magick frame_01.png frame_02.png ... -loop 0 out.gif` (NOT a wildcard that might re-sort); "
        "(2) consistent delays: `-delay 10` before all frames (= 100ms); "
        "(3) per-frame: `-delay 50 frame_01.png -delay 10 frame_02.png ...`; "
        "(4) loop forever: `-loop 0`; once: `-loop 1`; "
        "(5) dispose method: `-dispose previous` between frames to avoid ghosting.",
        f"{BASE}/command-line-options.php",
    ),

    # ============================================================
    # imagemagick-recipes — 36 common workflows
    # ============================================================
    (
        "imagemagick-recipes", "imagemagick recipe: resize while maintaining aspect ratio",
        "`magick input.jpg -resize 800x600 output.jpg` resizes to FIT in 800x600 (smaller dim wins). "
        "Width-only: `-resize 800x` (height auto). "
        "Height-only: `-resize x600`. "
        "Percent: `-resize 50%`. "
        "Don't upscale: `-resize 800x600\\>` (the `\\>` means 'only if larger'). "
        "Ignore aspect (force exact): `-resize 800x600!` (the `!` distorts).",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: convert image format (PNG ↔ JPG ↔ WebP)",
        "`magick input.png output.jpg` — extension determines format. "
        "Set JPG quality: `-quality 85` (default 92; 75 is common 'web' compromise). "
        "WebP: `magick input.jpg output.webp -quality 80`. "
        "Preserve alpha (PNG → WebP): WebP supports alpha; JPG doesn't (will flatten to white). "
        "Force format: `magick input.dat JPEG:output.jpg`.",
        f"{BASE}/formats.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: crop to specific region",
        "`magick input.png -crop WxH+X+Y output.png` where X,Y is the top-left of the crop. "
        "Center crop to square: `magick input.png -gravity center -crop 1000x1000+0+0 +repage output.png` (the `+repage` resets the offset metadata). "
        "Crop to aspect ratio: `-crop 16:9+0+0` (modern syntax; or compute dimensions). "
        "Tile crop (each chunk becomes a separate output): `-crop 256x256 tile_%d.png`.",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: rotate by N degrees",
        "`magick input.png -rotate 90 output.png` (90 = clockwise; -90 = counter). "
        "Fill background for non-90° rotations: `-background white -rotate 30 -alpha remove output.png`. "
        "Keep transparent BG: omit `-alpha remove`. "
        "Auto-rotate by EXIF: `-auto-orient` (handles iPhone portrait/landscape).",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: strip metadata (EXIF, IPTC, color profile)",
        "Remove all: `magick input.jpg -strip output.jpg`. "
        "Smaller file + privacy: also kills GPS coords, camera info, thumbnail. "
        "Keep ICC profile (color management): `-profile /path/to/sRGB.icc -strip` then re-apply. "
        "For batch: `mogrify -strip *.jpg`. "
        "Check before/after: `identify -verbose input.jpg | grep -E 'Exif|GPS'`.",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: reduce JPG file size with quality",
        "`magick input.jpg -quality 75 -strip output.jpg` typically halves file size with minimal visible loss. "
        "More aggressive: `-quality 60`. "
        "Progressive (better for web): `-interlace Plane`. "
        "Better encoder: `-sampling-factor 4:2:0` (chroma subsampling = standard web). "
        "Combined: `magick input.jpg -strip -interlace Plane -sampling-factor 4:2:0 -quality 80 output.jpg`.",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: generate thumbnails in batch",
        "Single dir: `for f in *.jpg; do magick \"$f\" -thumbnail 300x300\\> -quality 85 \"thumbs/$f\"; done`. "
        "Native batch: `mogrify -path thumbs/ -thumbnail 300x300\\> -quality 85 *.jpg`. "
        "Faster (parallel): `find . -name '*.jpg' | xargs -P 4 -I {} magick {} -thumbnail 300x300\\> thumbs/{}`. "
        "`-thumbnail` is faster than `-resize` for downscales (uses pre-blur + nearest-neighbor).",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: composite watermark / logo overlay",
        "Bottom-right with margin: `magick input.png logo.png -gravity southeast -geometry +20+20 -composite output.png`. "
        "Top-left: `-gravity northwest`. "
        "Center: `-gravity center -geometry +0+0`. "
        "Semi-transparent: pre-process logo: `magick logo.png -alpha set -channel A -evaluate set 50% +channel logo-50.png`. "
        "Tile across whole image: `-composite -tile`.",
        f"{BASE}/composite.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: add text overlay (label / caption)",
        "Simple text: `magick input.png -gravity south -pointsize 36 -fill white -annotate +0+20 'My Caption' output.png`. "
        "Multi-line: use `caption:` or `label:`: `magick -size 800x -background transparent -fill white -font /System/Library/Fonts/Helvetica.ttc -pointsize 40 caption:'Multi\\nline\\ntext' caption.png` then composite. "
        "Specific font: `-font /path/to/font.ttf`. "
        "List available: `magick -list font`.",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: create animated GIF from frames",
        "`magick frame_*.png -delay 10 -loop 0 animation.gif` (delay in centiseconds; 10 = 100ms = 10 fps). "
        "Per-frame delay: `magick -delay 50 frame_1.png -delay 10 frame_2.png ... animation.gif`. "
        "Optimize size: `-layers Optimize animation.gif`. "
        "Reduce palette: `-colors 64`. "
        "Reverse: `-reverse`.",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: extract frames from animated GIF",
        "`magick animation.gif frame_%03d.png` produces frame_000.png, frame_001.png, etc. "
        "First frame only: `magick 'animation.gif[0]' first.png`. "
        "Specific range: `magick 'animation.gif[0-5]' frame_%d.png`. "
        "With composited differences (full frames): `magick animation.gif -coalesce frame_%03d.png` (handles GIF disposal correctly).",
        f"{BASE}/command-line-processing.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: convert PDF to PNG (with high DPI)",
        "`magick -density 300 input.pdf output.png`. "
        "`-density` MUST come before input for PDF rasterization. "
        "Multi-page → individual images: `magick -density 300 input.pdf page_%03d.png`. "
        "Specific page: `magick -density 300 'input.pdf[2]' page_3.png` (zero-indexed). "
        "Better quality (slower): `-density 600`. "
        "May require Ghostscript: install with `brew install ghostscript` or `apt install ghostscript`.",
        f"{BASE}/formats.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: PDF to single multi-page TIFF or stacked PNG",
        "`magick -density 300 input.pdf -alpha remove -compress lzw multipage.tif` (TIFF supports multi-page). "
        "Stacked PNG (montage of pages): `magick -density 200 input.pdf -resize 50% -append all-pages.png`. "
        "Side-by-side: `+append` (instead of `-append`). "
        "For thumbnails of every page: `magick -density 100 input.pdf -resize 200x -tile 4x -montage all.png`.",
        f"{BASE}/formats.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: sharpen or blur an image",
        "Sharpen: `magick input.png -sharpen 0x1 output.png` (sigma=1.0 — subtle). "
        "Unsharp mask (more control): `-unsharp 0x1+1+0.05` (radius x sigma + amount + threshold). "
        "Blur: `-blur 0x3` (Gaussian, sigma=3). "
        "Motion blur: `-motion-blur 0x10+45` (direction = 45°). "
        "Radial: `-radial-blur 45`. "
        "Mild noise reduction: `-despeckle`.",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: adjust brightness, contrast, saturation",
        "Brightness/Contrast (separate): `magick input.png -brightness-contrast 10x20 output.png` (+10% brightness, +20% contrast). "
        "Modulate (brightness × saturation × hue %): `-modulate 110,150,100` (110% brightness, 150% saturation, hue unchanged). "
        "Auto-level: `-auto-level` (stretches dynamic range). "
        "Specific channel: `-channel R -level 0%,80%` (red channel only). "
        "Gamma: `-gamma 1.5` (>1 brighter, <1 darker).",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: convert to grayscale",
        "Simple: `magick input.png -colorspace Gray output.png`. "
        "Preserve original colorspace metadata but desaturate: `-modulate 100,0,100` (sets saturation to 0). "
        "Weighted (perceptual): `-grayscale Rec709Luma`. "
        "Average channels (faster, less accurate): `-grayscale Average`. "
        "Single channel: `-channel R -separate` (just red channel as grayscale).",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: apply sepia or vintage tone",
        "Sepia: `magick input.png -sepia-tone 80% output.png` (intensity 0-100). "
        "Old photo (combined): `magick input.png -modulate 90,10,100 -sepia-tone 50% -fill '#704214' -tint 30 -border 20x20 -bordercolor '#3a2412' output.png`. "
        "Black + white + grain: `-colorspace Gray +noise Gaussian -attenuate 0.1`.",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: pad / extend image to specific dimensions",
        "Add transparent padding: `magick input.png -gravity center -background none -extent 1200x1200 output.png`. "
        "White background: `-background white`. "
        "Specific color: `-background '#cccccc'`. "
        "Off-center: change `-gravity` (north, south, east, west, center, etc). "
        "For exact dimensions WITH resize fit: `-resize 1200x1200 -gravity center -extent 1200x1200` (resizes to fit, then pads).",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: compare two images (perceptual difference)",
        "Numeric distance: `magick compare -metric AE a.png b.png difference.png` prints differing pixel count + writes red highlight image. "
        "Other metrics: `RMSE` (root mean square error), `PSNR` (peak signal-to-noise), `SSIM` (structural). "
        "Just the number: `magick compare -metric AE a.png b.png null: 2>&1`. "
        "Side-by-side viewer: `magick a.png b.png +append comparison.png`.",
        f"{BASE}/compare.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: identify image info (dimensions, format, color depth)",
        "`magick identify input.png` (or `identify input.png`). "
        "Verbose: `identify -verbose input.png | less` (full EXIF + properties). "
        "Specific format: `identify -format 'width=%w height=%h format=%m colorspace=%[colorspace] depth=%[depth]\\n' input.png`. "
        "All images in dir: `identify *.png`. "
        "JSON: `magick identify -format '%[json]' input.png`.",
        f"{BASE}/identify.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: bulk-process a directory (resize/convert all)",
        "`mogrify -resize 1200\\> -quality 85 -format jpg *.png` (in-place, but `-format jpg` writes new .jpg files). "
        "To different dir: `mogrify -path resized/ -resize 1200\\> *.jpg`. "
        "Recursive: `find . -name '*.jpg' -exec magick {} -resize 1200\\> {} \\;` (overwrites originals). "
        "Parallel: `find . -name '*.jpg' | xargs -P 4 -I {} magick {} -resize 1200\\> resized/{}`.",
        f"{BASE}/mogrify.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: generate a favicon (multi-resolution ICO)",
        "`magick input.png -define icon:auto-resize=64,48,32,16 favicon.ico`. "
        "Produces ICO with all common sizes embedded. "
        "Modern web favicon (PNG): single 32x32 or 192x192 PNG often sufficient. "
        "For Apple touch icon: `magick input.png -resize 180x180 apple-touch-icon.png`.",
        f"{BASE}/formats.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: create a flat-color canvas",
        "`magick -size 1200x800 xc:'#3a5f8f' canvas.png`. "
        "`xc:` = X canvas. Color names also work: `xc:red`, `xc:'rgb(255,128,64)'`. "
        "Transparent: `xc:transparent` or `xc:none`. "
        "Gradient: `magick -size 800x600 gradient:blue-red gradient.png`. "
        "Radial: `-size 800x600 radial-gradient:white-black radial.png`.",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: add a border / frame around image",
        "Solid color: `magick input.png -bordercolor '#333' -border 10 output.png` (10px on all sides). "
        "Asymmetric: `-border 20x10` (20 horizontal, 10 vertical). "
        "Beveled (3D look): `-frame 20x20+5+5`. "
        "Rounded corners: `magick input.png \\( +clone -alpha extract -draw 'fill black polygon 0,0 0,20 20,0 fill white circle 20,20 20,0' \\( +clone -flip \\) -compose Multiply -composite \\( +clone -flop \\) -compose Multiply -composite \\) -alpha off -compose CopyOpacity -composite output.png` (complex but works).",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: join images side-by-side or vertically",
        "Horizontal (side-by-side): `magick a.png b.png c.png +append row.png`. "
        "Vertical (stacked): `magick a.png b.png c.png -append column.png`. "
        "Match heights first (for varying inputs): `magick a.png b.png -resize x500 -append col.png`. "
        "Grid (montage): `magick montage a.png b.png c.png d.png -tile 2x2 -geometry 400x400+5+5 grid.png`.",
        f"{BASE}/montage.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: generate noise / patterns",
        "Gaussian noise: `magick -size 800x600 xc:gray +noise Gaussian noise.png`. "
        "Random colors: `-size 800x600 plasma: plasma.png` (smooth gradient noise). "
        "Checkerboard: `-size 800x600 pattern:checkerboard checker.png`. "
        "Striped: `pattern:horizontal2`. "
        "List all patterns: `magick -list pattern`.",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: convert HEIC (iPhone) to JPG",
        "Direct: `magick input.heic output.jpg -quality 85`. "
        "Requires libheif at build (most homebrew/apt installs include it). "
        "Batch (works for whole iPhone photo dump): `mogrify -format jpg -quality 85 *.HEIC`. "
        "Preserve EXIF (rotation, date): `magick input.heic -auto-orient output.jpg`. "
        "macOS fallback: `sips -s format jpeg input.heic --out output.jpg` (built into macOS).",
        f"{BASE}/formats.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: optimize PNG (reduce palette / strip)",
        "Strip + interlace: `magick input.png -strip -interlace none output.png`. "
        "Reduce palette to 256: `-colors 256 -dither Riemersma` (gives indexed PNG, smaller). "
        "Drop alpha channel: `-alpha off -background white -flatten`. "
        "True optimization: pipe through external `pngquant`: `pngquant --quality=65-80 input.png` (lossy palette compression, often 50-70% smaller).",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: generate Open Graph / social preview image",
        "1200x630 with title text: ```\\nmagick -size 1200x630 -background '#1a1a2e' xc:'#1a1a2e' \\\\\\n  -font /System/Library/Fonts/Helvetica.ttc -pointsize 72 -fill white \\\\\\n  -gravity center -annotate +0-30 'My Article Title' \\\\\\n  -pointsize 32 -fill '#aaa' -annotate +0+50 'Subtitle here' \\\\\\n  og-image.png\\n```. "
        "With logo: composite a small logo: `-gravity southwest -geometry +30+30 logo.png -composite`.",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: apply ICC profile (color management)",
        "Embed a profile: `magick input.png -profile /path/to/AdobeRGB.icc output.png`. "
        "Convert between profiles: `magick input.png -profile sRGB.icc -profile CMYK.icc output.tif` (first applies the SOURCE profile, second the destination). "
        "Strip profile: `+profile '*'` (or just `-strip`). "
        "Check existing: `identify -verbose input.png | grep -i profile`.",
        f"{BASE}/color-management.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: edge detection / sharpening filters",
        "Edge: `magick input.png -edge 1 edges.png`. "
        "Stronger: `-edge 3`. "
        "Sobel-style: `-morphology Convolve Sobel output.png`. "
        "Laplacian: `-morphology Convolve Laplacian:0`. "
        "For sketch effect: `magick input.png -edge 1 -negate -modulate 100,0,100 sketch.png`.",
        f"{BASE}/morphology.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: tint / colorize image",
        "Tint (preserves luminance): `magick input.png -fill '#3a7fc4' -tint 50 output.png`. "
        "Hard colorize (overrides): `-fill '#3a7fc4' -colorize 100`. "
        "Channel-specific: `-channel R -evaluate Multiply 1.2 +channel` (boost red 20%). "
        "Replace specific color: `-fuzz 10% -fill blue -opaque red` (replace all red-ish pixels with blue).",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: stream very large images (avoid OOM)",
        "For multi-gigapixel TIFFs that don't fit in RAM. "
        "`magick stream -map rgb -storage-type char big.tif - | other-tool` streams raw pixels. "
        "Or in pipeline: `magick big.tif -define stream:buffer-size=256m -resize 50% small.tif`. "
        "Raise disk limit for cache spill: `MAGICK_MAP_LIMIT=32GiB`. "
        "Use `-thumbnail` instead of `-resize` for downscale (loads pre-decimated subimage if format supports it — TIFF/JPEG).",
        f"{BASE}/architecture.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: write multiple sizes at once (responsive images)",
        "One command produces multiple outputs: ```\\nmagick input.jpg \\\\\\n  \\\\( +clone -resize 320x \\\\) -write small.jpg +delete \\\\\\n  \\\\( +clone -resize 800x \\\\) -write medium.jpg +delete \\\\\\n  \\\\( +clone -resize 1920x \\\\) -write large.jpg +delete \\\\\\n  null:\\n```. "
        "Or just call magick N times — simpler if not in a hot loop. "
        "For srcset HTML: combine with web framework's image helpers.",
        f"{BASE}/command-line-processing.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: blur background while keeping subject (vignette)",
        "Vignette (darken edges): `magick input.png -background black -vignette 0x100 vignette.png`. "
        "Bigger sigma = wider fade. "
        "For 'background blur' (subject in focus): masks the subject + blurs everything else — complex; usually easier to use a portrait-mode app. "
        "Simple radial darkening: `-fill black -draw 'fill-opacity 0.5 circle 200,200 200,400'`.",
        f"{BASE}/command-line-options.php",
    ),
    (
        "imagemagick-recipes", "imagemagick recipe: protect against malicious uploads (production-safe magick)",
        "Apply size + resource limits: `magick -limit width 8000 -limit height 8000 -limit area 256MB -limit memory 256MiB -limit map 512MiB -limit disk 1GiB -limit time 30 untrusted.png out.jpg`. "
        "Or set in policy.xml: `<policy domain=\"resource\" name=\"width\" value=\"8000\"/>`. "
        "Disable risky coders: keep policy.xml denying PDF/PS/EPS/MSL/MVG for untrusted input. "
        "Run in container with no network + read-only FS + minimal user. "
        "Validate before processing: `identify input` and check format/dimensions before deeper ops.",
        f"{BASE}/security-policy.php",
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
            evidence_id=f"ev-im-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-im-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="imagemagick-catalog",
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
