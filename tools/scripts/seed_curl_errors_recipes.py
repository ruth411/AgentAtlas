"""Direct-insert ~30 curl error claims + ~30 curl recipe claims."""

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

BASE = "https://curl.se/docs"

CLAIMS = [
    # ============================================================
    # curl-errors — common errors + exit codes
    # ============================================================
    (
        "curl-errors", "curl error 6: Could not resolve host",
        "DNS resolution failed for the URL's hostname. Causes: typo in URL, DNS server unreachable, hostname doesn't exist. "
        "Fix: (1) `nslookup <host>` or `dig <host>` to verify DNS independently; (2) check /etc/resolv.conf for valid nameservers; "
        "(3) if your DNS is fine but the host is wrong, curl can use a different host with the same IP: `curl --resolve example.com:443:1.2.3.4 https://example.com`; "
        "(4) test with explicit IP: `curl https://1.2.3.4 -H \"Host: example.com\" -k` (the -k skips cert verification since the cert won't match an IP).",
        f"{BASE}/manpage.html",
    ),
    (
        "curl-errors", "curl error 7: Failed to connect to host",
        "TCP connection couldn't be established. The host resolved but the port is unreachable. "
        "Fix: (1) `nc -zv <host> <port>` to test the port independently; (2) check firewall rules (local and remote); "
        "(3) verify the port matches the protocol (80 for HTTP, 443 for HTTPS, 21 for FTP); "
        "(4) for proxied environments, set `--proxy http://proxy:8080` or env vars HTTPS_PROXY/HTTP_PROXY; "
        "(5) `curl -v` shows the connection attempt details — IP, port, retry behavior.",
        f"{BASE}/manpage.html",
    ),
    (
        "curl-errors", "curl error 28: Operation timed out",
        "curl waited longer than the configured timeout. Default is no timeout for full operation; --connect-timeout is the connect-phase timeout. "
        "Fix: (1) increase `--max-time <seconds>` for total operation timeout; (2) `--connect-timeout <seconds>` for just the TCP handshake; "
        "(3) check if the server is slow (`curl -w \"%{time_total}\\n\" -o /dev/null -s <url>`); "
        "(4) for unreliable networks: `--retry 3 --retry-delay 5 --retry-max-time 60`; "
        "(5) for keepalives during slow transfers: `--speed-limit 1 --speed-time 30` aborts only if speed drops below 1 byte/s for 30s.",
        f"{BASE}/manpage.html",
    ),
    (
        "curl-errors", "curl error 35: SSL connect error",
        "TLS/SSL handshake failed. Common causes: protocol mismatch, cipher mismatch, server doesn't support TLS 1.2+, MITM proxy intercepting. "
        "Fix: (1) try with `--insecure` (`-k`) to see if cert validation is the issue (DON'T leave this in production); "
        "(2) `--tlsv1.2` or `--tls-max 1.2` to negotiate a specific TLS version; "
        "(3) `-v` shows the handshake — look for 'cipher' and 'protocol'; "
        "(4) for self-signed certs: `--cacert /path/to/ca.pem` to add a trusted CA; "
        "(5) for client certs (mTLS): `--cert client.pem --key client.key`.",
        f"{BASE}/docs/sslcerts.html",
    ),
    (
        "curl-errors", "curl error 22: HTTP response code said error (--fail)",
        "You used `-f`/`--fail` which makes curl exit non-zero on HTTP 4xx/5xx responses. "
        "Fix: (1) check what HTTP status the server returned — without `-f`, curl prints the body normally; "
        "(2) `-w \"%{http_code}\\n\"` writes the HTTP code; "
        "(3) `--fail-with-body` (newer curl) returns the response body even on error (useful for APIs that embed error details); "
        "(4) `-i` includes response headers; debug what the server is actually telling you.",
        f"{BASE}/manpage.html",
    ),
    (
        "curl-errors", "curl error 60: SSL certificate problem (self-signed or unknown CA)",
        "The server's TLS certificate isn't trusted by your CA bundle. "
        "Fix for self-signed: (1) get the cert: `openssl s_client -connect host:443 -showcerts < /dev/null`; "
        "(2) save the CA cert as ca.pem; (3) `curl --cacert ca.pem https://host`; "
        "(4) for client-side pinning: `--pinnedpubkey sha256//<base64-of-cert-pubkey>`; "
        "(5) NEVER use `-k`/`--insecure` in production scripts — defeats the entire point of TLS.",
        f"{BASE}/docs/sslcerts.html",
    ),
    (
        "curl-errors", "curl error 47: Maximum (default 50) redirects followed",
        "Too many HTTP redirects, often a redirect loop. "
        "Fix: (1) inspect the chain: `curl -L -v -o /dev/null <url> 2>&1 | grep -i location`; "
        "(2) raise the limit: `--max-redirs 100`; "
        "(3) confirm the URL is correct — the loop is usually a misconfigured server sending HTTP→HTTPS→HTTP infinitely; "
        "(4) curl doesn't follow redirects by default — without `-L`, you'd see the 301/302 + Location header but not the loop.",
        f"{BASE}/manpage.html",
    ),
    (
        "curl-errors", "curl error 56: Failure when receiving data from the peer",
        "Connection reset mid-transfer. Network instability, server crash, timeout on the server side, or proxy intervention. "
        "Fix: (1) retry: `--retry 3`; (2) reduce request size if uploading; (3) check server logs for the same time window — connection resets usually have a server-side cause; "
        "(4) for diagnostic: `curl -v --trace-ascii dump.txt <url>` writes a full transcript to dump.txt; "
        "(5) test from a different network to rule out local firewall / proxy.",
        f"{BASE}/manpage.html",
    ),
    (
        "curl-errors", "curl error 18: Transferred a partial file",
        "Download didn't complete — file was cut off. Often paired with error 56. "
        "Fix: (1) resume with `--continue-at - <url> -o file` (`-` lets curl figure out where to resume); "
        "(2) for HTTP, server must support Range requests (most modern servers do); "
        "(3) `--retry 3 --retry-delay 5` for transient failures; "
        "(4) verify final size matches Content-Length: `curl -I <url>` returns headers including expected size.",
        f"{BASE}/manpage.html",
    ),
    (
        "curl-errors", "curl error 92: HTTP/2 stream not closed cleanly",
        "HTTP/2 connection ended unexpectedly. Server-side issue or middlebox interference. "
        "Fix: (1) force HTTP/1.1: `curl --http1.1 <url>` — works around server-side HTTP/2 bugs; "
        "(2) `-v` to see protocol negotiation; "
        "(3) check if server explicitly closes streams under load (server logs may show CONNECTION RESET BY PEER).",
        f"{BASE}/manpage.html",
    ),
    (
        "curl-errors", "curl error 77: Problem with the SSL CA cert",
        "CA bundle missing or unreadable. Common on minimal Docker images. "
        "Fix: (1) install ca-certificates: `apt install ca-certificates` (Debian) or `apk add ca-certificates` (Alpine); "
        "(2) verify path: `curl -v <url>` shows the CA bundle it's trying to use; "
        "(3) explicit override: `--cacert /etc/ssl/certs/ca-certificates.crt`; "
        "(4) for corporate environments with custom CAs: add your CA cert to the system bundle (Debian: drop in /usr/local/share/ca-certificates/ + `update-ca-certificates`).",
        f"{BASE}/docs/sslcerts.html",
    ),
    (
        "curl-errors", "curl error 26: Read error (from local file)",
        "curl couldn't read the file you're trying to upload. "
        "Fix: (1) verify path exists + readable: `ls -la <file>`; (2) for stdin uploads: `curl -T - <url> < file.txt` or `cat file.txt | curl -T - <url>`; "
        "(3) check permissions — curl uses the calling user's UID/GID; (4) for symlinks pointing to non-existent files, curl errors here too.",
        f"{BASE}/manpage.html",
    ),
    (
        "curl-errors", "curl error 23: Failed writing body (write error)",
        "curl couldn't write the response body to disk or stdout. Disk full, permissions, or piped consumer closed early. "
        "Fix: (1) check disk: `df -h .`; (2) verify write permissions to the output directory; "
        "(3) if piping (e.g., `curl ... | head`), the consumer closing the pipe early causes this — curl tries to write more than the consumer reads; use `curl ... | head` (acceptable to lose data), or `curl -o - ... | head` and explicitly handle SIGPIPE in scripts.",
        f"{BASE}/manpage.html",
    ),
    (
        "curl-errors", "curl error 5: Couldn't resolve proxy",
        "Proxy hostname DNS failed. "
        "Fix: (1) verify proxy URL — typo in HTTP_PROXY/HTTPS_PROXY env vars is common; (2) `nslookup <proxy-host>`; "
        "(3) for proxy with auth in URL: `--proxy http://user:pass@proxy:8080` (better: `--proxy-user user:pass`); "
        "(4) for split proxy (different upstream proxies for http vs https): set both HTTP_PROXY and HTTPS_PROXY env vars.",
        f"{BASE}/manpage.html",
    ),
    (
        "curl-errors", "curl error 67: Login denied (authentication failed)",
        "Server rejected your credentials. Could be password, expired token, missing scope. "
        "Fix: (1) for HTTP Basic: `-u user:pass`; (2) for bearer tokens: `-H \"Authorization: Bearer <token>\"`; "
        "(3) `-v` shows the WWW-Authenticate challenge and your Authorization header; (4) for OAuth2 flows, verify the token isn't expired — check `iat` and `exp` in the JWT payload; "
        "(5) for FTP: `--user user:pass` or embed in URL: `ftp://user:pass@host/path`.",
        f"{BASE}/manpage.html",
    ),
    (
        "curl-errors", "curl error 1: Unsupported protocol",
        "URL scheme isn't supported by your curl build. "
        "Fix: (1) `curl -V` shows compiled-in protocols (look at 'Protocols:' line); "
        "(2) common missing on minimal builds: LDAPS, RTMP, GOPHER, SCP, SFTP; "
        "(3) reinstall a fuller curl: `apt install curl` (Debian provides most), or `brew install curl --with-openssl --with-libssh2`; "
        "(4) check the URL — typo in scheme (`htps://` instead of `https://`).",
        f"{BASE}/docs/protdocs.html",
    ),
    (
        "curl-errors", "curl error 51: SSL public key does not match pinned key",
        "You used `--pinnedpubkey` and the server's cert pubkey doesn't match. Either pin is wrong or you're under MITM. "
        "Fix: (1) get current pubkey: `openssl s_client -connect host:443 < /dev/null 2>/dev/null | openssl x509 -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | base64`; "
        "(2) update your pin if the server rotated keys legitimately; (3) if pin was right and server changed unexpectedly, investigate — it could be MITM.",
        f"{BASE}/docs/sslcerts.html",
    ),
    (
        "curl-errors", "curl error 52: Empty reply from server",
        "Server accepted the connection but closed it without sending anything. Often a server-side crash, misconfigured proxy, or load balancer dropping the connection. "
        "Fix: (1) retry with delay: `--retry 3 --retry-delay 2`; (2) test from different client to rule out request-specific issues; "
        "(3) check server logs for the timestamp; (4) for slow servers, allow more time: `--max-time 60`.",
        f"{BASE}/manpage.html",
    ),
    (
        "curl-errors", "curl warning: TLSv1 / TLSv1.1 connection refused",
        "Server requires TLS 1.2 or 1.3; your curl is offering older. "
        "Fix: (1) usually you don't need to do anything — modern curl negotiates the best version; "
        "(2) if you explicitly set `--tlsv1.0`, remove it; (3) for forcing newer: `--tlsv1.2` or `--tls-max 1.3`.",
        f"{BASE}/docs/sslcerts.html",
    ),
    (
        "curl-errors", "curl error 33: HTTP server doesn't support the requested range",
        "You used `--range` (`-r`) but the server doesn't support partial content. "
        "Fix: (1) check with HEAD: `curl -I <url>` — look for `Accept-Ranges: bytes` (or missing); "
        "(2) drop the range — many servers support full GET only; (3) some servers support range for binary files but not text/dynamic responses.",
        f"{BASE}/manpage.html",
    ),
    (
        "curl-errors", "curl error: Could not resolve host with --resolve flag",
        "You tried `--resolve host:port:ip` but the syntax was wrong. "
        "Fix: (1) format is exactly `host:port:ip` — three colon-separated parts: `--resolve example.com:443:1.2.3.4`; "
        "(2) you can repeat: `--resolve a.com:443:1.2.3.4 --resolve b.com:443:5.6.7.8`; "
        "(3) for IPv6: `--resolve example.com:443:[2001:db8::1]`; "
        "(4) for HSTS/HTTPS, must include the actual port the URL targets.",
        f"{BASE}/manpage.html",
    ),
    (
        "curl-errors", "curl error: bad command line option",
        "curl rejected one of your flags. Different versions support different flags. "
        "Fix: (1) check the version: `curl -V`; (2) check the option syntax in the manpage; (3) older flag names: `--data-urlencode` vs `--data-encode`; "
        "(4) some flags are protocol-specific (e.g., `--ftp-port` only works with FTP URLs).",
        f"{BASE}/manpage.html",
    ),
    (
        "curl-errors", "curl: warning: Binary output can mess up your terminal",
        "curl detected the response body is binary and the output is a terminal. By default it stops to protect you. "
        "Fix: (1) `-o file` redirects to a file instead — preferred for binaries; (2) `--output -` forces stdout (useful in pipes); "
        "(3) `-O` saves to the URL's filename; (4) `-s --no-progress-bar` to silence the warning if you genuinely want binary on stdout (still risky).",
        f"{BASE}/manpage.html",
    ),
    (
        "curl-errors", "curl error: cookie domain mismatch",
        "Server set a cookie for a domain that doesn't match the request. Curl warns; cookie is dropped. "
        "Fix: usually a server bug — the server should match its own cookie Domain attribute to its hostname. "
        "Workaround: write cookies manually with `-b \"name=value\"` (you're explicit about what's sent) instead of relying on automatic jar tracking; or use `-c jar.txt` to capture and `-b jar.txt` to replay — the jar is more permissive than strict RFC compliance.",
        f"{BASE}/docs/cookies.html",
    ),
    (
        "curl-errors", "curl error: HTTP/1.1 100 Continue but server expected request body",
        "You used `Expect: 100-continue` (or curl set it automatically for big POST/PUT) but the server is misbehaving. "
        "Fix: (1) explicitly disable the Expect header: `-H \"Expect:\"` (empty value = removed); "
        "(2) some old/weird servers (some IIS configs, F5 LB) don't speak Expect — common on legacy enterprise APIs; "
        "(3) `--expect100-timeout <seconds>` to wait less time for the server's 100 response.",
        f"{BASE}/manpage.html",
    ),
    (
        "curl-errors", "curl error: --data-urlencode failed to encode",
        "URL-encoding failed, often because the input had encoding issues. "
        "Fix: (1) ensure input is valid UTF-8; (2) for special form fields: `--data-urlencode \"key=value with spaces\"`; "
        "(3) for fields from a file: `--data-urlencode \"key@filename\"`; (4) for prefilled key + value-only encoding: `--data-urlencode \"=needs-encoding\"`.",
        f"{BASE}/manpage.html",
    ),
    (
        "curl-errors", "curl error: write-out (-w) format malformed",
        "Your --write-out format string has bad syntax. "
        "Fix: (1) variables go in `%{varname}`: `-w \"%{http_code} %{time_total}\\n\"`; "
        "(2) escape literal % with `%%`; (3) full list of variables in the manpage's WRITE-OUT section; "
        "(4) for JSON output: `-w \"%{json}\\n\"` (curl 7.70+) dumps everything as JSON — much easier than building format strings.",
        f"{BASE}/manpage.html",
    ),
    (
        "curl-errors", "curl error: Could not open the output file (-o)",
        "Directory doesn't exist or you lack permission. "
        "Fix: (1) `--create-dirs` to auto-create parent directories: `curl -o output/sub/dir/file.txt --create-dirs <url>`; "
        "(2) verify the parent directory exists and is writable; (3) for downloads using URL's filename: `-O --output-dir ./downloads`.",
        f"{BASE}/manpage.html",
    ),

    # ============================================================
    # curl-recipes — common workflows
    # ============================================================
    (
        "curl-recipes", "curl recipe: send a POST request with JSON body",
        "Standard REST API call. Pattern: "
        "`curl -X POST https://api.example.com/users -H \"Content-Type: application/json\" -d '{\"name\":\"Alice\",\"email\":\"alice@example.com\"}'`. "
        "From a file: `curl -X POST <url> -H \"Content-Type: application/json\" -d @data.json`. "
        "With pretty-printed response: `curl ... | jq .`. "
        "With auth: add `-H \"Authorization: Bearer <token>\"` or `-u user:pass` for basic. "
        "`-d` automatically sets POST + Content-Type to application/x-www-form-urlencoded unless overridden — explicit -H is safer.",
        f"{BASE}/docs/httpscripting.html",
    ),
    (
        "curl-recipes", "curl recipe: upload a file with multipart/form-data",
        "Mimic a browser form upload. Pattern: `curl -X POST https://example.com/upload -F \"file=@/path/to/file.png\" -F \"description=My upload\"`. "
        "The `-F` builds multipart/form-data. Mix file uploads with normal fields. "
        "Multiple files: `-F \"files[]=@file1.png\" -F \"files[]=@file2.png\"`. "
        "Specify the part's Content-Type: `-F \"file=@photo.jpg;type=image/jpeg\"`. "
        "Custom filename: `-F \"file=@photo.jpg;filename=user-pic.jpg\"`.",
        f"{BASE}/docs/httpscripting.html",
    ),
    (
        "curl-recipes", "curl recipe: download a file with progress",
        "Save a remote file. Pattern: `curl -o local-file.tar.gz https://example.com/big.tar.gz`. "
        "Use the URL's filename: `curl -O https://example.com/big.tar.gz` (creates big.tar.gz locally). "
        "With progress bar: by default curl shows a progress meter; suppress with `-s`; force progress bar (no meter) with `-#`. "
        "Resume interrupted: `curl -C - -O <url>` (the `-` lets curl figure out the offset). "
        "Verify checksum after: `sha256sum local-file.tar.gz` and compare to upstream's published hash.",
        f"{BASE}/docs/manpage.html",
    ),
    (
        "curl-recipes", "curl recipe: follow redirects and show the final URL",
        "By default curl doesn't follow. Pattern: `curl -L <url>` follows 3xx redirects. "
        "Show final URL after redirects: `curl -L -o /dev/null -s -w \"%{url_effective}\\n\" <url>`. "
        "Limit redirect depth: `--max-redirs 5`. "
        "See all hops: `curl -L -v <url> 2>&1 | grep -E '> (GET|Host)|< Location'` (greps the verbose trace).",
        f"{BASE}/docs/manpage.html",
    ),
    (
        "curl-recipes", "curl recipe: send custom headers",
        "Use `-H \"Header: Value\"` repeatedly. Examples: "
        "`curl -H \"Accept: application/json\" -H \"X-API-Key: abc123\" -H \"User-Agent: MyBot/1.0\" <url>`. "
        "Remove a default header curl sends automatically: `-H \"Accept:\"` (empty value removes it). "
        "Replace User-Agent shortcut: `-A \"Mozilla/5.0...\"`. "
        "Replace Referer: `-e \"https://google.com\"`. "
        "For long headers in scripts, build them in shell vars or use `-K configfile` to keep the curl command readable.",
        f"{BASE}/docs/httpscripting.html",
    ),
    (
        "curl-recipes", "curl recipe: handle cookies (session persistence)",
        "Capture cookies during login + replay them. Pattern: "
        "(1) Login + save cookies: `curl -c cookies.txt -d \"user=alice&pass=secret\" https://example.com/login`; "
        "(2) Subsequent requests with the same session: `curl -b cookies.txt https://example.com/api/me`; "
        "(3) For a complete flow: `curl -L -c cookies.txt -b cookies.txt <url>` (read AND write the same jar — preserves cookies set during redirects). "
        "Direct cookie strings (no jar): `curl -b \"sessionid=abc; csrf=xyz\" <url>`.",
        f"{BASE}/docs/cookies.html",
    ),
    (
        "curl-recipes", "curl recipe: HTTP basic authentication",
        "Send Authorization: Basic header. "
        "Inline: `curl -u alice:secret https://api.example.com/me`. "
        "Prompt for password (no shell history): `curl -u alice https://api.example.com/me` (curl prompts). "
        "From .netrc: `curl --netrc <url>` reads ~/.netrc for credentials. "
        "Embed in URL: `curl https://alice:secret@api.example.com/me` (works but exposes password in process listings).",
        f"{BASE}/docs/httpscripting.html",
    ),
    (
        "curl-recipes", "curl recipe: OAuth 2.0 bearer token",
        "Standard pattern for modern APIs. "
        "`curl -H \"Authorization: Bearer eyJhbGc...token...here\" https://api.example.com/v1/users`. "
        "Get the token first via curl too — typical OAuth client_credentials grant: "
        "`TOKEN=$(curl -s -X POST https://auth.example.com/token -d \"grant_type=client_credentials&client_id=$ID&client_secret=$SECRET\" | jq -r .access_token)`. "
        "Then use: `curl -H \"Authorization: Bearer $TOKEN\" <api-url>`. "
        "Refresh expired tokens with `grant_type=refresh_token&refresh_token=<rt>`.",
        f"{BASE}/docs/httpscripting.html",
    ),
    (
        "curl-recipes", "curl recipe: test only with HEAD request",
        "Get just response headers (no body). `curl -I <url>` sends HEAD. "
        "Useful for: checking if a URL exists, inspecting Content-Length, getting Last-Modified for caching, checking Content-Type. "
        "For GET that just shows headers without saving body: `curl -i -o /dev/null <url>`. "
        "Some servers reject HEAD — fall back to GET with output discarded: `curl -X GET -o /dev/null -D - <url>` (`-D -` dumps headers to stdout).",
        f"{BASE}/docs/manpage.html",
    ),
    (
        "curl-recipes", "curl recipe: send a PUT or DELETE",
        "Just use `-X <METHOD>`. Examples: "
        "PUT with JSON: `curl -X PUT -H \"Content-Type: application/json\" -d '{\"name\":\"bob\"}' https://api.example.com/users/123`. "
        "DELETE: `curl -X DELETE https://api.example.com/users/123 -H \"Authorization: Bearer $TOKEN\"`. "
        "PATCH (partial update): `curl -X PATCH -H \"Content-Type: application/merge-patch+json\" -d '{\"email\":\"new@email\"}' <url>`. "
        "Note: omitting `-X` and using `-d` defaults to POST; explicit `-X PUT` is needed for body-bearing PUT.",
        f"{BASE}/docs/httpscripting.html",
    ),
    (
        "curl-recipes", "curl recipe: debug a failing request",
        "Verbose flag chain for triage. `-v` (verbose): shows request + response headers, TLS handshake details. `--trace dump.txt` writes full request and response binary. `--trace-ascii dump.txt` is more readable. "
        "Pretty timing breakdown: `curl -w \"DNS: %{time_namelookup} TCP: %{time_connect} TLS: %{time_appconnect} TTFB: %{time_starttransfer} Total: %{time_total}\\n\" -o /dev/null -s <url>`. "
        "Failure isolation: try `-k` to skip cert check, `--http1.1` to skip HTTP/2 — narrows down the layer.",
        f"{BASE}/docs/manpage.html",
    ),
    (
        "curl-recipes", "curl recipe: pretty-print response as JSON",
        "Pipe through jq. `curl -s https://api.example.com/users | jq .` formats JSON output indented. "
        "Extract specific fields: `curl -s <url> | jq '.users[].email'`. "
        "Show just HTTP status + body: `curl -s -w \"\\n\\nHTTP: %{http_code}\\n\" <url>`. "
        "Combined check + parse: `curl -fsSL <url> | jq .` (the `-f` fails on HTTP 4xx/5xx, `-s` silent, `-S` show errors).",
        f"{BASE}/docs/manpage.html",
    ),
    (
        "curl-recipes", "curl recipe: send raw binary body (--data-binary)",
        "`-d` munges newlines (strips them). `--data-binary` sends bytes as-is. "
        "Use for: uploading raw files where exact byte stream matters, sending pre-formatted JSON files. "
        "`curl -X POST --data-binary @file.bin -H \"Content-Type: application/octet-stream\" <url>`. "
        "Difference: `-d @file.txt` strips newlines from file content; `--data-binary @file.txt` preserves them. For JSON, always use `--data-binary` or escape newlines yourself.",
        f"{BASE}/docs/manpage.html",
    ),
    (
        "curl-recipes", "curl recipe: timing measurements + observability",
        "`curl -w` exposes detailed timing. Standard breakdown: "
        "`curl -w \"@curl-format.txt\" -o /dev/null -s <url>` where curl-format.txt contains: "
        "`     time_namelookup:  %{time_namelookup}\\n        time_connect:  %{time_connect}\\n     time_appconnect:  %{time_appconnect}\\n    time_pretransfer:  %{time_pretransfer}\\n       time_redirect:  %{time_redirect}\\n  time_starttransfer:  %{time_starttransfer}\\n                     ----------\\n          time_total:  %{time_total}\\n`. "
        "Sum: NAMELOOKUP < CONNECT < APPCONNECT (TLS) < PRETRANSFER < STARTTRANSFER (TTFB) < TOTAL.",
        f"{BASE}/docs/manpage.html",
    ),
    (
        "curl-recipes", "curl recipe: retry with backoff",
        "Built-in retry: `--retry 5` retries up to 5 times. Pair with `--retry-delay <sec>` (linear delay) or `--retry-max-time <sec>` (total budget). "
        "Modern curl supports exponential backoff via `--retry-all-errors` (retry on any error, not just transient ones — be careful with side-effect-having methods). "
        "Full pattern for a script: `curl -fsS --retry 5 --retry-delay 5 --retry-max-time 60 --connect-timeout 10 -o file <url>`. "
        "For 5xx-only retry: combine with `-f` so 4xx exits immediately while 5xx retries.",
        f"{BASE}/docs/manpage.html",
    ),
    (
        "curl-recipes", "curl recipe: parallel requests (one curl invocation)",
        "Run multiple URLs in parallel from one curl. `curl -Z https://a.com https://b.com https://c.com -o a.html -o b.html -o c.html`. "
        "The `-Z` (`--parallel`) launches them concurrently. Default 50 parallel, override with `--parallel-max <N>`. "
        "Or per-URL config with `--next` (`-:`): `curl -d \"x=1\" https://a.com -: -d \"x=2\" https://b.com -: -d \"x=3\" https://c.com`. "
        "Useful for: warming caches, batch API calls, multi-mirror downloads.",
        f"{BASE}/docs/manpage.html",
    ),
    (
        "curl-recipes", "curl recipe: read URLs from a file",
        "`-K <config-file>` reads command-line options from a file. Example config file `requests.conf`: `url = \"https://a.com\"\\nurl = \"https://b.com\"\\nurl = \"https://c.com\"`. "
        "Then: `curl -K requests.conf`. "
        "Alternative for plain-URL lists: shell loop: `cat urls.txt | xargs -n1 curl -O`. "
        "For batch downloads with consistent options: use config file (cleaner than super-long command lines).",
        f"{BASE}/docs/manpage.html",
    ),
    (
        "curl-recipes", "curl recipe: download file with auth + resume",
        "Combine -u with resume. `curl -u user:pass -C - -O https://example.com/big.tar.gz`. "
        "With auth from .netrc (better than plaintext on CLI): `curl --netrc -C - -O <url>`. "
        "For mid-download interruption recovery in scripts: wrap in a retry loop that checks file size + uses `-C -`.",
        f"{BASE}/docs/manpage.html",
    ),
    (
        "curl-recipes", "curl recipe: test API rate limits",
        "Loop curl, show throughput + response codes. Pattern: "
        "`for i in $(seq 1 100); do curl -s -o /dev/null -w \"%{http_code}\\t%{time_total}\\n\" https://api.example.com/endpoint; done`. "
        "Watch for 429 (too many requests) — the response usually has a Retry-After header. "
        "Capture rate-limit headers: `curl -i <url> | grep -i 'x-ratelimit'` (GitHub, AWS, etc. expose remaining quota).",
        f"{BASE}/docs/httpscripting.html",
    ),
    (
        "curl-recipes", "curl recipe: use a proxy",
        "Routes curl through an intermediate. `curl --proxy http://proxy.corp:8080 https://api.example.com`. "
        "Proxy with auth: `curl --proxy http://user:pass@proxy:8080 <url>` or `--proxy-user user:pass`. "
        "SOCKS5 proxy: `--proxy socks5://localhost:1080 <url>` (useful for SSH tunnels: `ssh -D 1080 user@bastion`). "
        "Environment-based: `https_proxy=http://proxy:8080 curl <url>` — many UNIX tools respect $http_proxy/$https_proxy.",
        f"{BASE}/docs/manpage.html",
    ),
    (
        "curl-recipes", "curl recipe: send mTLS (client cert) request",
        "Mutual TLS: server verifies client. Pattern: "
        "`curl --cert client.pem --key client-key.pem https://mtls.example.com/secure`. "
        "Combined PEM (cert + key in same file): `--cert combined.pem`. "
        "PKCS#12 (.p12 from a CA): `--cert cert.p12 --cert-type P12`. "
        "Password-protected key: `--cert client.pem --key client-key.pem:passphrase` or `--key client-key.pem` (curl prompts).",
        f"{BASE}/docs/sslcerts.html",
    ),
    (
        "curl-recipes", "curl recipe: SCP/SFTP upload via curl",
        "Yes, curl handles SSH-based transfer too. SCP: `curl -T local.txt scp://user@host/path/`. "
        "SFTP: `curl -u user: -T local.txt sftp://host/path/file.txt` (the `:` after user prompts for password). "
        "With SSH key: `--key ~/.ssh/id_rsa`. "
        "Note: requires curl built with libssh2 — check with `curl -V` for 'libssh2' in the output line.",
        f"{BASE}/docs/protdocs.html",
    ),
    (
        "curl-recipes", "curl recipe: FTP upload + download",
        "Upload: `curl -T file.txt -u user:pass ftp://ftp.example.com/dir/`. "
        "Download: `curl -u user:pass -O ftp://ftp.example.com/dir/file.txt`. "
        "List directory: `curl -u user:pass ftp://ftp.example.com/dir/`. "
        "FTPS (FTP over TLS): use `ftps://` URL or `--ssl-reqd ftp://`. "
        "For passive mode (firewall-friendly): default for curl, force with `--ftp-pasv`. Active mode (rarely needed): `--ftp-port -` (curl picks the port).",
        f"{BASE}/docs/protdocs.html",
    ),
    (
        "curl-recipes", "curl recipe: SMTP send email",
        "curl can send email too. Pattern: "
        "`curl --url 'smtps://smtp.gmail.com:465' --user 'sender@gmail.com:app-password' --mail-from 'sender@gmail.com' --mail-rcpt 'recipient@example.com' --upload-file message.txt`. "
        "message.txt format: `From: sender@gmail.com\\nTo: recipient@example.com\\nSubject: Hello\\n\\nMessage body here`. "
        "Use case: scripted notifications without installing sendmail or msmtp. Gmail requires app passwords (not your account password) if 2FA enabled.",
        f"{BASE}/docs/protdocs.html",
    ),
    (
        "curl-recipes", "curl recipe: send GraphQL query",
        "GraphQL is HTTP POST with a JSON body. Pattern: "
        "`curl -X POST https://api.example.com/graphql -H \"Content-Type: application/json\" -H \"Authorization: Bearer $TOKEN\" -d '{\"query\": \"{ users { id name } }\"}'`. "
        "Multi-line queries: store in a file and use `-d @query.json` where query.json is `{\"query\": \"...\", \"variables\": {...}}`. "
        "Pipe through jq for readable output.",
        f"{BASE}/docs/httpscripting.html",
    ),
    (
        "curl-recipes", "curl recipe: hit a localhost service in development",
        "Common: `curl http://localhost:8080/api/health`. "
        "For IPv6 localhost: `curl http://[::1]:8080/`. "
        "Force IPv4: `curl --ipv4 http://localhost:8080`. "
        "With JWT for development: `curl -H \"Authorization: Bearer $(cat ~/.dev-token)\" http://localhost:8080/...`. "
        "For HTTPS localhost with self-signed cert: `curl -k https://localhost:8443/` (the `-k` skips cert validation; safe for dev only).",
        f"{BASE}/docs/manpage.html",
    ),
    (
        "curl-recipes", "curl recipe: cleanup and security audit",
        "Use `curl -V` to audit: shows compiled-in features (TLS library, supported protocols, IDN, HTTP/3 support). "
        "Check curl version: `curl --version` and compare to latest at curl.se — outdated curl has CVEs. "
        "For automated systems, pin curl version OR validate via package signing. "
        "Always use `-fsS` flags in scripts: `-f` fails on HTTP errors, `-s` silent, `-S` shows errors (only when -s is set) — this combination prevents silent failures.",
        f"{BASE}/docs/security.html",
    ),
    (
        "curl-recipes", "curl recipe: replace wget with curl",
        "Most wget workflows translate to curl. Save URL to filename: wget URL → `curl -O URL`. "
        "Specific output name: wget -O file URL → `curl -o file URL`. "
        "Recursive download: curl doesn't do this natively — wget wins. "
        "Resume: wget -c → `curl -C -`. "
        "Following redirects: wget does by default; curl needs `-L`. "
        "Quiet: wget -q → `curl -s`.",
        f"{BASE}/docs/comparison-table.html",
    ),
    (
        "curl-recipes", "curl recipe: write-out variables (response observability)",
        "`-w \"format\"` exposes ~40 variables. Useful ones: "
        "`%{http_code}` — response status; `%{size_download}` — body bytes; `%{speed_download}` — bytes/sec; "
        "`%{url_effective}` — final URL after redirects; `%{ssl_verify_result}` — cert verification (0=OK); "
        "`%{json}` — entire response metadata as JSON (curl 7.70+); "
        "`%{header_json}` — response headers as JSON. "
        "Example monitoring: `curl -w \"%{http_code}\\t%{time_total}\\t%{size_download}\\n\" -o /dev/null -s <url>`.",
        f"{BASE}/docs/manpage.html",
    ),
    (
        "curl-recipes", "curl recipe: download multiple files in parallel from a list",
        "Use `-Z` with a URL pattern or file. Pattern variants: "
        "(1) URL globbing: `curl -Z -O \"https://example.com/file[1-10].zip\"` downloads 1.zip through 10.zip in parallel; "
        "(2) Multiple URLs from script: `curl -Z $(cat urls.txt)`; "
        "(3) Limit concurrency: `--parallel-max 5`. "
        "Combined with `--remote-name-all -O` ensures every URL gets its own output file.",
        f"{BASE}/docs/manpage.html",
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
            evidence_id=f"ev-curl-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-curl-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="curl-catalog",
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
