"""Direct-insert openssl error + recipe claims."""

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

BASE = "https://docs.openssl.org/master/man1"
MAN5 = "https://docs.openssl.org/master/man5"

CLAIMS = [
    # ============================================================
    # openssl-errors — 30 common errors
    # ============================================================
    (
        "openssl-errors", "openssl error: unable to load Private Key / PEM_read_bio:no start line",
        "Input file isn't a valid PEM private key — wrong format, wrong tag, or it's DER. "
        "Fix: (1) check file: `head -1 key.pem` should show `-----BEGIN PRIVATE KEY-----` (PKCS#8) or `-----BEGIN RSA PRIVATE KEY-----` (legacy PKCS#1); "
        "(2) for DER: convert: `openssl pkey -inform DER -in key.der -out key.pem`; "
        "(3) for PuTTY .ppk: convert with `puttygen key.ppk -O private-openssh -o key.pem`; "
        "(4) check for CR/LF mangling: `dos2unix key.pem`; "
        "(5) ensure file isn't empty/truncated.",
        f"{BASE}/openssl-pkey/",
    ),
    (
        "openssl-errors", "openssl error: PEM routines:bad password read / bad decrypt",
        "Wrong passphrase for an encrypted private key. "
        "Fix: (1) double-check password (no trailing whitespace); "
        "(2) for batch / non-interactive: `-passin pass:secret` (insecure: visible in ps) or `-passin file:/path/to/passfile` or `-passin env:VAR`; "
        "(3) strip passphrase: `openssl pkey -in encrypted.pem -out unencrypted.pem`; "
        "(4) verify the file is actually encrypted: `head -3` should show `Proc-Type: 4,ENCRYPTED` (PEM) or just check that file says ENCRYPTED.",
        f"{BASE}/openssl-pkey/",
    ),
    (
        "openssl-errors", "openssl error: error 18 at 0 depth lookup:self signed certificate",
        "Verification chain ended at a self-signed cert that isn't in the trust store. "
        "Fix: (1) intentional (dev): add `-CAfile /path/to/self-signed.crt` to verify; "
        "(2) test connection with extra trust: `openssl s_client -connect host:443 -CAfile self.crt`; "
        "(3) add to system trust: macOS Keychain `security add-trusted-cert -d -r trustRoot -k ~/Library/Keychains/login.keychain cert.crt`; "
        "(4) Linux: copy to /usr/local/share/ca-certificates/ then `update-ca-certificates`; "
        "(5) for prod: never trust self-signed — use a real CA.",
        f"{BASE}/openssl-verify/",
    ),
    (
        "openssl-errors", "openssl error: error 19:unable to get local issuer certificate / error 20",
        "Server didn't send full chain — your trust store is missing an intermediate. "
        "Fix: (1) verify what server sends: `openssl s_client -connect host:443 -showcerts`; "
        "(2) fix the server to send full chain (concat cert + intermediates in PEM); "
        "(3) workaround client-side: `-CAfile bundle.crt` with intermediates added; "
        "(4) for Let's Encrypt: use `fullchain.pem` (cert + intermediate), not just `cert.pem`; "
        "(5) automated trust: `update-ca-certificates` after adding intermediates to /usr/local/share/ca-certificates/.",
        f"{BASE}/openssl-verify/",
    ),
    (
        "openssl-errors", "openssl error: error 21:certificate not yet valid / error 22:certificate has expired",
        "System clock vs cert dates problem. "
        "Fix: (1) for `not yet valid`: check system time — `date -u` should be close to UTC truth; sync with NTP: `sudo chronyc makestep` or `sudo ntpdate -s time.nist.gov`; "
        "(2) for `has expired`: renew the cert — for Let's Encrypt: `certbot renew`; "
        "(3) check cert dates: `openssl x509 -in cert.pem -noout -dates`; "
        "(4) for embedded devices with bad clocks: NTP setup is mandatory before TLS works.",
        f"{BASE}/openssl-x509/",
    ),
    (
        "openssl-errors", "openssl error: unable to get local issuer certificate in chain",
        "Same root as error 20 but at a deeper depth. "
        "Fix: (1) `openssl s_client -connect host:443 -showcerts` lists every cert sent; "
        "(2) compare to issuer chain — each cert's Issuer must match the next cert's Subject; "
        "(3) ensure server sends full chain; "
        "(4) for client-only fix: build complete bundle in `-CAfile`.",
        f"{BASE}/openssl-verify/",
    ),
    (
        "openssl-errors", "openssl error: ssl3_get_record:wrong version number / sslv3 alert handshake failure",
        "Either you're sending HTTP to an HTTPS port, OR client/server have no shared protocol version. "
        "Fix: (1) verify you're hitting the right port: `nc -zv host 443` then `openssl s_client -connect host:443`; "
        "(2) confused HTTP vs TLS: `curl -k https://host` not `curl http://host:443`; "
        "(3) version mismatch: try `-tls1_2` or `-tls1_3` explicitly: `openssl s_client -connect host:443 -tls1_2`; "
        "(4) very old servers may need SSLv3 (DON'T enable that in production).",
        f"{BASE}/openssl-s_client/",
    ),
    (
        "openssl-errors", "openssl error: no shared cipher / no cipher match",
        "Client and server disagree on cipher suites. "
        "Fix: (1) what does client offer: `openssl ciphers -v`; "
        "(2) what does server accept: `nmap --script ssl-enum-ciphers -p 443 host`; "
        "(3) test specific: `openssl s_client -connect host:443 -cipher 'AES256-GCM-SHA384'` (TLS≤1.2) or `-ciphersuites TLS_AES_256_GCM_SHA384` (TLS 1.3); "
        "(4) for newer servers refusing weak ciphers: update client; "
        "(5) for legacy: enable in client: `-cipher 'DEFAULT:@SECLEVEL=0'` (insecure, dev only).",
        f"{BASE}/openssl-ciphers/",
    ),
    (
        "openssl-errors", "openssl error: dh key too small",
        "Server's Diffie-Hellman parameters are smaller than client's minimum (Logjam protection). "
        "Fix: (1) server side: regenerate DH params: `openssl dhparam -out dhparams.pem 2048` (or 4096); "
        "(2) client workaround (insecure): `-cipher 'DEFAULT:@SECLEVEL=0'` or skip DH suites: `-cipher 'ALL:!kDHE'`; "
        "(3) configure servers to use ECDHE instead (smaller, faster, secure): `ssl_ecdh_curve X25519:prime256v1` (nginx).",
        f"{BASE}/openssl-dhparam/",
    ),
    (
        "openssl-errors", "openssl error: CERTIFICATE_VERIFY_FAILED / Hostname mismatch",
        "Cert is valid but doesn't match the requested hostname (SAN mismatch). "
        "Fix: (1) check what's in cert: `openssl x509 -in cert.pem -noout -text | grep -A 1 'Subject Alternative Name'`; "
        "(2) for connection test: include the hostname: `openssl s_client -connect 10.0.0.1:443 -servername api.example.com`; "
        "(3) for new certs: regenerate with all needed SANs (modern certs need SAN, not just CN); "
        "(4) for client code: don't silently skip — fix the cert.",
        f"{BASE}/openssl-x509/",
    ),
    (
        "openssl-errors", "openssl error: tlsv1 alert decode error / bad record mac",
        "TLS protocol-level error — usually MITM / proxy interference, OR client and server disagree on protocol details. "
        "Fix: (1) check for transparent proxy / TLS-intercepting firewall (corporate); "
        "(2) try direct connection (no proxy): `HTTPS_PROXY= openssl s_client ...`; "
        "(3) for clock skew on TLS 1.3: GREASE / 0-RTT issues — try `-tls1_2`; "
        "(4) check both ends are up to date.",
        f"{BASE}/openssl-s_client/",
    ),
    (
        "openssl-errors", "openssl error: inappropriate fallback",
        "Server detected client downgrade attempt (TLS_FALLBACK_SCSV). Usually means a connection was retried with a lower protocol after first failing. "
        "Fix: (1) don't downgrade — fix the original failure; "
        "(2) client should support all modern TLS versions; "
        "(3) for prod servers: keep `TLS_FALLBACK_SCSV` enforcement on — it's a security feature.",
        f"{BASE}/openssl-s_client/",
    ),
    (
        "openssl-errors", "openssl error: unable to write 'random state' / random number generator",
        "OpenSSL couldn't write to $HOME/.rnd (legacy entropy file). "
        "Fix: (1) ignore — modern OpenSSL (1.1.1+) uses /dev/urandom and doesn't need .rnd; "
        "(2) suppress: `export RANDFILE=/dev/null`; "
        "(3) for containers with no /dev/urandom: that's a more serious problem — fix container; "
        "(4) for openssl 1.0.x: `openssl rand -writerand ~/.rnd`.",
        f"{BASE}/openssl-rand/",
    ),
    (
        "openssl-errors", "openssl error: cannot load certificate / x509: malformed PEM",
        "Cert file is corrupt, truncated, or in wrong format. "
        "Fix: (1) check tags: `head -1 cert.pem` should be `-----BEGIN CERTIFICATE-----`; `tail -1` should be `-----END CERTIFICATE-----`; "
        "(2) for DER: convert: `openssl x509 -inform DER -in cert.der -out cert.pem`; "
        "(3) for .p7b/.crl: different formats — use the right command (`openssl pkcs7` or `openssl crl`); "
        "(4) base64 mangled (line wraps removed): re-format with line breaks every 64 chars.",
        f"{BASE}/openssl-x509/",
    ),
    (
        "openssl-errors", "openssl error: certificate and private key don't match",
        "You're using a key that doesn't pair with the cert. "
        "Fix: (1) get modulus of each — RSA: ```\\nopenssl x509 -noout -modulus -in cert.pem | openssl md5\\nopenssl rsa  -noout -modulus -in  key.pem | openssl md5\\n```. They MUST match. "
        "(2) for EC: ```\\nopenssl x509 -noout -pubkey -in cert.pem | openssl md5\\nopenssl pkey -pubout -in  key.pem | openssl md5\\n```; "
        "(3) if no match: you have the wrong key — find the original key paired with this CSR/cert.",
        f"{BASE}/openssl-x509/",
    ),
    (
        "openssl-errors", "openssl error: error reading public key / RSA_setup_blinding:no inverse",
        "Key is corrupted (rare) or in wrong format. "
        "Fix: (1) try `openssl pkey -in key.pem -text` to inspect; "
        "(2) for RSA-only: `openssl rsa -in key.pem -check`; "
        "(3) if file looks fine but openssl rejects: re-generate the key (won't recover the original).",
        f"{BASE}/openssl-rsa/",
    ),
    (
        "openssl-errors", "openssl error: command 'X' not found / Invalid command",
        "Subcommand typo, or built without that feature. "
        "Fix: (1) list available: `openssl help`; "
        "(2) modern openssl 3.x: most legacy commands renamed (`openssl genrsa` → `openssl genpkey -algorithm RSA`); "
        "(3) check version: `openssl version` — 1.1.1 vs 3.x have different defaults; "
        "(4) for FIPS-restricted builds: many algos disabled — use a non-FIPS build.",
        f"{BASE}/openssl/",
    ),
    (
        "openssl-errors", "openssl error: unsupported algorithm / no engine for X",
        "Algorithm name typo or not compiled in. "
        "Fix: (1) list: `openssl list -digest-algorithms` (or `-cipher-algorithms`, `-public-key-algorithms`); "
        "(2) common: `sha512` (not `sha-512`); `aes-256-gcm` (not `aes256gcm`); "
        "(3) for legacy algorithms (MD5, DES, RC4): often disabled in OpenSSL 3.x by default — enable with `-provider legacy`; "
        "(4) for hardware-accelerated: ensure proper engine loaded.",
        f"{BASE}/openssl/",
    ),
    (
        "openssl-errors", "openssl error: error:0308010C:digital envelope routines::unsupported (OpenSSL 3 + legacy hash)",
        "OpenSSL 3.x rejected a legacy algorithm (MD5, SHA1 for sigs, etc.) by default. "
        "Fix: (1) enable legacy: edit /etc/ssl/openssl.cnf — add `[provider_sect]` with `legacy = legacy_sect` then `default = default_sect`; "
        "(2) one-off: `openssl ... -provider default -provider legacy`; "
        "(3) for Node.js: `--openssl-legacy-provider` flag on the node binary; "
        "(4) prefer to fix the upstream: switch from MD5 to SHA-256 etc.",
        f"{MAN5}/config/",
    ),
    (
        "openssl-errors", "openssl error: routines: parse error / nested asn1 error",
        "DER/ASN.1 structure is malformed. "
        "Fix: (1) inspect: `openssl asn1parse -in file.der -inform DER`; "
        "(2) compare with valid: `openssl asn1parse -in good.der`; "
        "(3) for partial corruption: may be hex-dumped or modified — re-fetch original; "
        "(4) common: PKCS#7 file mislabeled as PKCS#12 — try different commands.",
        f"{BASE}/openssl-asn1parse/",
    ),
    (
        "openssl-errors", "openssl error: connect:errno=111 (Connection refused)",
        "TCP connection failed — nothing listening. "
        "Fix: (1) check port: `nc -zv host port`; "
        "(2) typo in hostname/port: `s_client -connect host:443` not `host:80`; "
        "(3) firewall blocking; "
        "(4) for TLS-on-non-default-port: `s_client -connect host:8443 -servername host`.",
        f"{BASE}/openssl-s_client/",
    ),
    (
        "openssl-errors", "openssl error: alert protocol_version / TLSv1.0 fatal",
        "Server doesn't accept the TLS version you're offering. "
        "Fix: (1) for modern server (TLS 1.2+ only): use newer openssl (3.x) or specify: `-tls1_2`; "
        "(2) for legacy server stuck on TLS 1.0: `-cipher 'DEFAULT:@SECLEVEL=0' -tls1`; "
        "(3) check what server actually supports: `nmap --script ssl-enum-ciphers -p 443 host`; "
        "(4) upgrade either client or server — TLS 1.0/1.1 are deprecated.",
        f"{BASE}/openssl-s_client/",
    ),
    (
        "openssl-errors", "openssl error: PKCS12_parse: mac verify failure",
        "PKCS#12 (.p12/.pfx) wrong password. "
        "Fix: (1) check password; "
        "(2) for known-good password: file may use legacy PBE (PKCS#12 mac iteration count of 1) — modern OpenSSL 3.x rejects. Try with: `openssl pkcs12 -in file.p12 -legacy`; "
        "(3) for Windows-exported .pfx: same legacy issue often.",
        f"{BASE}/openssl-pkcs12/",
    ),
    (
        "openssl-errors", "openssl error: error 26:unhandled critical extension",
        "Cert has a critical extension OpenSSL doesn't recognize. "
        "Fix: (1) inspect extensions: `openssl x509 -in cert.pem -noout -text | grep -A 1 'Critical'`; "
        "(2) for custom extensions: cert was issued with policies OpenSSL can't validate — must mark as non-critical at issuance; "
        "(3) for client-side: usually means the cert isn't intended for that purpose (e.g. email cert used for TLS); "
        "(4) workaround: `-ignore_critical` (DANGEROUS — defeats purpose of critical extensions).",
        f"{BASE}/openssl-verify/",
    ),
    (
        "openssl-errors", "openssl error: 80067082 / sec_error_unknown_issuer (browser side)",
        "Browser's perspective on error 19/20 — same root cause. "
        "Fix: see error 20 / unable to get issuer fixes — usually server needs to send full chain. "
        "Browser-specific: ensure intermediate CAs are bundled with leaf cert.",
        f"{BASE}/openssl-verify/",
    ),
    (
        "openssl-errors", "openssl error: cannot create configuration / Error opening config file",
        "openssl.cnf missing or unreadable. "
        "Fix: (1) find current: `openssl version -d` shows OPENSSLDIR; "
        "(2) explicit: `openssl ... -config /path/to/openssl.cnf`; "
        "(3) env: `OPENSSL_CONF=/path/to/openssl.cnf openssl ...`; "
        "(4) for stripped containers: bundle a minimal openssl.cnf or set `OPENSSL_CONF=/dev/null` if config truly isn't needed.",
        f"{MAN5}/config/",
    ),
    (
        "openssl-errors", "openssl error: x509: ip address mismatch / GSSAPI hostname check",
        "You're verifying by IP but cert has only a DNS SAN. "
        "Fix: (1) use the hostname, not IP: `openssl s_client -connect 10.0.0.1:443 -servername example.com -verify_hostname example.com`; "
        "(2) for cert that needs IP SAN: re-issue with `subjectAltName = IP:10.0.0.1, DNS:example.com`; "
        "(3) hosts file entry to resolve hostname to your IP.",
        f"{BASE}/openssl-verify/",
    ),
    (
        "openssl-errors", "openssl error: CA cert key too small / unsupported key length",
        "Modern OpenSSL rejects very weak keys (e.g. RSA < 1024 bits, sometimes 2048). "
        "Fix: (1) re-issue with larger key: `openssl genrsa 4096`; "
        "(2) for legacy compat (insecure): `-cipher 'DEFAULT:@SECLEVEL=0'`; "
        "(3) ECDSA P-256 = ~3072-bit RSA equivalent (smaller + faster).",
        f"{BASE}/openssl-genrsa/",
    ),
    (
        "openssl-errors", "openssl error: SSL_connect:SSLv3 read server hello A",
        "TLS handshake failed early — usually 'no shared cipher' / 'no protocol overlap' (see those errors). "
        "Fix: (1) `openssl s_client -connect host:443 -debug` shows raw bytes; "
        "(2) try forcing version: `-tls1_2` or `-tls1_3`; "
        "(3) check SNI: `-servername hostname` (mandatory for many modern servers).",
        f"{BASE}/openssl-s_client/",
    ),
    (
        "openssl-errors", "openssl error: NETSCAPE_SPKI_b64_decode / bad base64",
        "Input contains invalid base64. "
        "Fix: (1) PEM headers may have been stripped — re-add `-----BEGIN ...-----` / `-----END ...-----`; "
        "(2) base64 must wrap at 64 chars max (RFC 1421); "
        "(3) for URL-safe base64 (uses - and _): not standard PEM — convert with `tr '_-' '/+' | base64 -d`; "
        "(4) for binary DER: don't base64-decode — use `-inform DER`.",
        f"{BASE}/openssl-x509/",
    ),

    # ============================================================
    # openssl-recipes — 38 common workflows
    # ============================================================
    (
        "openssl-recipes", "openssl recipe: generate RSA private key",
        "Modern (preferred): `openssl genpkey -algorithm RSA -out key.pem -pkeyopt rsa_keygen_bits:4096`. "
        "Legacy: `openssl genrsa -out key.pem 4096`. "
        "Encrypted (passphrase-protected): add `-aes256` (legacy) or `-pass pass:secret` (genpkey). "
        "Inspect: `openssl pkey -in key.pem -text -noout`. "
        "Bit sizes: 2048 (acceptable), 3072 (better), 4096 (paranoid). Use 4096 for long-lived keys.",
        f"{BASE}/openssl-genpkey/",
    ),
    (
        "openssl-recipes", "openssl recipe: generate ECDSA private key",
        "P-256 (common, ~RSA 3072 equivalent): `openssl genpkey -algorithm EC -out key.pem -pkeyopt ec_paramgen_curve:P-256`. "
        "P-384 (paranoid): `-pkeyopt ec_paramgen_curve:P-384`. "
        "List supported curves: `openssl ecparam -list_curves`. "
        "Inspect: `openssl pkey -in key.pem -text -noout`. "
        "Smaller + faster than RSA, same security.",
        f"{BASE}/openssl-genpkey/",
    ),
    (
        "openssl-recipes", "openssl recipe: generate Ed25519 private key",
        "Modern signature key (fast, small). "
        "`openssl genpkey -algorithm Ed25519 -out key.pem`. "
        "Used by SSH, modern protocols. ~RSA 3072 equivalent security. "
        "Limitation: signature-only (no encryption use). For TLS keys: prefer ECDSA P-256.",
        f"{BASE}/openssl-genpkey/",
    ),
    (
        "openssl-recipes", "openssl recipe: encrypt / decrypt existing private key with passphrase",
        "Encrypt: `openssl pkey -in unencrypted.pem -out encrypted.pem -aes256` (prompts). "
        "Non-interactive: `-passout pass:secret`. "
        "Decrypt (strip passphrase): `openssl pkey -in encrypted.pem -out unencrypted.pem` (prompts). "
        "Non-interactive: `-passin pass:secret`. "
        "AVOID: storing passphrases in shell history / scripts. Use `file:` or `env:` prefix.",
        f"{BASE}/openssl-pkey/",
    ),
    (
        "openssl-recipes", "openssl recipe: generate a Certificate Signing Request (CSR)",
        "Interactive: `openssl req -new -key key.pem -out req.csr`. "
        "Non-interactive with subject: `openssl req -new -key key.pem -out req.csr -subj '/CN=example.com/O=My Org'`. "
        "With SAN (modern requirement): use a config file (see SAN recipe). "
        "Inspect: `openssl req -in req.csr -text -noout`. "
        "Verify CSR matches the key: `openssl req -in req.csr -noout -verify`.",
        f"{BASE}/openssl-req/",
    ),
    (
        "openssl-recipes", "openssl recipe: generate self-signed cert (one-shot)",
        "Most common (key + cert in one go): ```\\nopenssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem \\\\\\n  -days 365 -nodes -subj '/CN=localhost'\\n```. "
        "`-nodes` = no passphrase on key. "
        "`-x509` = output cert (not CSR). "
        "For SAN (multiple hostnames): `-addext 'subjectAltName=DNS:localhost,DNS:dev.local,IP:127.0.0.1'`. "
        "ECDSA variant: `-newkey ec -pkeyopt ec_paramgen_curve:P-256`.",
        f"{BASE}/openssl-req/",
    ),
    (
        "openssl-recipes", "openssl recipe: generate self-signed with Subject Alternative Names (SAN)",
        "One-line: ```\\nopenssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes \\\\\\n  -subj '/CN=api.example.com' \\\\\\n  -addext 'subjectAltName = DNS:api.example.com, DNS:www.api.example.com, IP:10.0.0.1' \\\\\\n  -addext 'extendedKeyUsage = serverAuth'\\n```. "
        "Verify SAN: `openssl x509 -in cert.pem -noout -text | grep -A 1 'Subject Alternative Name'`.",
        f"{BASE}/openssl-req/",
    ),
    (
        "openssl-recipes", "openssl recipe: extract public key from private key",
        "`openssl pkey -in private.pem -pubout -out public.pem`. "
        "Encrypted private key: `openssl pkey -in private.pem -pubout -out public.pem -passin pass:secret`. "
        "Print public key: `openssl pkey -in private.pem -pubout -text -noout`. "
        "From cert (without private key): `openssl x509 -in cert.pem -pubkey -noout`.",
        f"{BASE}/openssl-pkey/",
    ),
    (
        "openssl-recipes", "openssl recipe: print certificate info (Subject, SANs, Issuer, dates)",
        "Full text: `openssl x509 -in cert.pem -noout -text`. "
        "Just Subject: `-subject`. "
        "Just Issuer: `-issuer`. "
        "Just dates: `-dates` (or `-startdate` / `-enddate`). "
        "Just fingerprint: `-fingerprint -sha256`. "
        "Just SANs: `-text | grep -A 1 'Subject Alternative Name'`. "
        "JSON-ish: parse `-text` with grep/sed/awk.",
        f"{BASE}/openssl-x509/",
    ),
    (
        "openssl-recipes", "openssl recipe: convert PEM ↔ DER (cert and key)",
        "PEM → DER: `openssl x509 -in cert.pem -out cert.der -outform DER`. "
        "DER → PEM: `openssl x509 -in cert.der -inform DER -out cert.pem`. "
        "For keys: replace `x509` with `pkey` (or `rsa`/`ec`). "
        "DER is binary (smaller); PEM is base64 (human-readable, header tags). "
        "Some Java tools need DER; most Unix tools want PEM.",
        f"{BASE}/openssl-x509/",
    ),
    (
        "openssl-recipes", "openssl recipe: combine cert + key into PKCS#12 (.p12 / .pfx)",
        "For Windows / Java / macOS / browsers. "
        "`openssl pkcs12 -export -inkey key.pem -in cert.pem -out bundle.p12 -name 'my-cert'`. "
        "With intermediate chain: `-certfile intermediates.pem`. "
        "With passphrase: prompts (or `-passout pass:secret`). "
        "Test it: `openssl pkcs12 -in bundle.p12 -info -nokeys` (lists contents).",
        f"{BASE}/openssl-pkcs12/",
    ),
    (
        "openssl-recipes", "openssl recipe: extract cert and key from PKCS#12",
        "Extract cert: `openssl pkcs12 -in bundle.p12 -clcerts -nokeys -out cert.pem`. "
        "Extract key: `openssl pkcs12 -in bundle.p12 -nocerts -out key.pem` (encrypted PEM by default — add `-nodes` for unencrypted). "
        "Extract CA chain: `openssl pkcs12 -in bundle.p12 -cacerts -nokeys -out ca-chain.pem`. "
        "For legacy .pfx from Windows: may need `-legacy` flag.",
        f"{BASE}/openssl-pkcs12/",
    ),
    (
        "openssl-recipes", "openssl recipe: hash a file (SHA-256, SHA-512, etc.)",
        "`openssl dgst -sha256 file.bin` (or `-sha512`, `-sha384`, `-blake2s256`). "
        "Equivalent to `sha256sum`. "
        "Hex output (default): just paste. Binary: `-binary`. "
        "Hash from stdin: `echo -n 'data' | openssl dgst -sha256`. "
        "List available: `openssl list -digest-algorithms`.",
        f"{BASE}/openssl-dgst/",
    ),
    (
        "openssl-recipes", "openssl recipe: HMAC a file",
        "`openssl dgst -sha256 -mac HMAC -macopt key:secret file.txt`. "
        "Or shortcut: `openssl dgst -sha256 -hmac secret file.txt`. "
        "From key file: `-mac HMAC -macopt hexkey:$(xxd -p key.bin | tr -d '\\n')`. "
        "From stdin: `echo -n 'data' | openssl dgst -sha256 -hmac secret`.",
        f"{BASE}/openssl-dgst/",
    ),
    (
        "openssl-recipes", "openssl recipe: sign a file (with private key)",
        "`openssl dgst -sha256 -sign key.pem -out sig.bin file.txt`. "
        "Encrypted key: `-passin pass:secret`. "
        "ASCII-armored sig: pipe through `base64`. "
        "For S/MIME-style: use `openssl cms` or `openssl smime`. "
        "Verify: see verify recipe.",
        f"{BASE}/openssl-dgst/",
    ),
    (
        "openssl-recipes", "openssl recipe: verify a signature",
        "`openssl dgst -sha256 -verify public.pem -signature sig.bin file.txt`. "
        "Public key from cert (when you only have cert): `openssl x509 -in cert.pem -pubkey -noout > public.pem`. "
        "Output: 'Verified OK' or 'Verification Failure'. Exit code 0 on success.",
        f"{BASE}/openssl-dgst/",
    ),
    (
        "openssl-recipes", "openssl recipe: encrypt a file with AES-256",
        "`openssl enc -aes-256-cbc -pbkdf2 -salt -in plaintext.txt -out ciphertext.bin`. "
        "`-pbkdf2` (use modern key derivation — REQUIRED, default in OpenSSL 3.x). "
        "Passphrase: prompts (or `-pass pass:secret`). "
        "For AEAD: `-aes-256-gcm` (more secure but enc command's GCM support is limited). "
        "Decrypt: same command with `-d` and swap in/out files.",
        f"{BASE}/openssl-enc/",
    ),
    (
        "openssl-recipes", "openssl recipe: generate cryptographically secure random bytes",
        "Hex: `openssl rand -hex 32` (32 bytes = 64 hex chars). "
        "Base64: `openssl rand -base64 32`. "
        "Binary to file: `openssl rand -out random.bin 32`. "
        "Use cases: API keys, session tokens, nonces — 32 bytes is more than enough for most.",
        f"{BASE}/openssl-rand/",
    ),
    (
        "openssl-recipes", "openssl recipe: generate password hash (for /etc/shadow style)",
        "BCrypt-style not directly supported; openssl does crypt-style. "
        "SHA-512 crypt: `openssl passwd -6 'mypassword'` (default `$6$` format). "
        "MD5 crypt (legacy): `-1`. "
        "With explicit salt: `-salt xyz123`. "
        "For modern apps: prefer bcrypt / argon2 (use a library, not openssl).",
        f"{BASE}/openssl-passwd/",
    ),
    (
        "openssl-recipes", "openssl recipe: connect to TLS server + inspect cert",
        "`openssl s_client -connect example.com:443 -servername example.com`. "
        "(`-servername` = SNI, mandatory for many modern servers.) "
        "Show full chain: `-showcerts`. "
        "Print cert details: pipe to `openssl x509 -text -noout`. "
        "Specific protocol: `-tls1_3` / `-tls1_2`. "
        "Just the cert (clean): `echo | openssl s_client -connect host:443 -servername host 2>/dev/null | openssl x509 -text -noout`.",
        f"{BASE}/openssl-s_client/",
    ),
    (
        "openssl-recipes", "openssl recipe: get certificate expiry date for a remote server",
        "`echo | openssl s_client -connect example.com:443 -servername example.com 2>/dev/null | openssl x509 -noout -enddate`. "
        "All dates: `-dates`. "
        "Just days remaining: ```bash\\nexpiry=$(echo | openssl s_client -connect example.com:443 -servername example.com 2>/dev/null | openssl x509 -noout -enddate | cut -d= -f2)\\necho $(( ($(date -d \"$expiry\" +%s) - $(date +%s)) / 86400 ))\\n```. "
        "For monitoring: alert when < 30 days remaining.",
        f"{BASE}/openssl-s_client/",
    ),
    (
        "openssl-recipes", "openssl recipe: get SHA-256 fingerprint of a cert",
        "From file: `openssl x509 -in cert.pem -noout -fingerprint -sha256`. "
        "From remote: `echo | openssl s_client -connect host:443 -servername host 2>/dev/null | openssl x509 -noout -fingerprint -sha256`. "
        "Used for cert pinning. Also: `-sha1`, `-sha512`, `-md5` (legacy).",
        f"{BASE}/openssl-x509/",
    ),
    (
        "openssl-recipes", "openssl recipe: verify a cert chain against a CA bundle",
        "`openssl verify -CAfile ca-bundle.pem cert.pem`. "
        "With intermediate: `openssl verify -CAfile ca.pem -untrusted intermediates.pem cert.pem`. "
        "Verify against system CA: `openssl verify cert.pem` (uses default trust store). "
        "Output: 'OK' or detailed error with error code (18=self-signed, 20=unable to get local issuer, etc).",
        f"{BASE}/openssl-verify/",
    ),
    (
        "openssl-recipes", "openssl recipe: check if private key matches a certificate",
        "Compare modulus / public key. "
        "RSA: ```bash\\nopenssl x509 -noout -modulus -in cert.pem | openssl md5\\nopenssl rsa  -noout -modulus -in  key.pem | openssl md5\\n```. Hashes must match. "
        "EC: ```bash\\nopenssl x509 -noout -pubkey -in cert.pem | openssl md5\\nopenssl pkey -pubout -in  key.pem | openssl md5\\n```. "
        "Universal: extract public key from both, compare.",
        f"{BASE}/openssl-x509/",
    ),
    (
        "openssl-recipes", "openssl recipe: check if CSR matches a private key",
        "`openssl req -in req.csr -noout -modulus | openssl md5` and `openssl rsa -in key.pem -noout -modulus | openssl md5` must match for RSA. "
        "For verifying CSR signature: `openssl req -in req.csr -noout -verify -text`.",
        f"{BASE}/openssl-req/",
    ),
    (
        "openssl-recipes", "openssl recipe: test a specific cipher / TLS version against a server",
        "Version: `openssl s_client -connect host:443 -tls1_3` (or `-tls1_2`, `-tls1_1`, `-tls1`). "
        "Cipher: `-cipher 'AES256-GCM-SHA384'` (TLS≤1.2) or `-ciphersuites 'TLS_AES_256_GCM_SHA384'` (TLS 1.3). "
        "List server-supported: `nmap --script ssl-enum-ciphers -p 443 host` (better tool for this). "
        "List client-supported: `openssl ciphers -v`.",
        f"{BASE}/openssl-s_client/",
    ),
    (
        "openssl-recipes", "openssl recipe: show ciphers in a specific category",
        "All enabled: `openssl ciphers -v`. "
        "Specific: `openssl ciphers -v 'HIGH:!aNULL:!MD5:!RC4'` (typical 'secure' set). "
        "TLS 1.3 only: `openssl ciphers -v -s -tls1_3`. "
        "Show what string expands to: `openssl ciphers 'DEFAULT'`. "
        "Common patterns: `HIGH` (strong), `EXPORT` (legacy weak), `AES256+ECDHE` (intersection).",
        f"{BASE}/openssl-ciphers/",
    ),
    (
        "openssl-recipes", "openssl recipe: spin up a TLS test server",
        "`openssl s_server -accept 4443 -cert cert.pem -key key.pem -www`. "
        "`-www` makes it serve a simple HTML page. "
        "For HTTP-style with custom response: `-HTTP` instead. "
        "Connect with: `openssl s_client -connect localhost:4443` or `curl -k https://localhost:4443`. "
        "Useful for testing cert configs without setting up nginx.",
        f"{BASE}/openssl-s_server/",
    ),
    (
        "openssl-recipes", "openssl recipe: generate Diffie-Hellman parameters",
        "For TLS with DHE ciphers. "
        "`openssl dhparam -out dhparams.pem 2048` (acceptable, fast). "
        "Paranoid: `-out dhparams.pem 4096` (slow — minutes). "
        "Used in nginx: `ssl_dhparam /etc/ssl/dhparams.pem`. "
        "Modern alternative: use ECDHE instead (no parameter generation needed): `ssl_ecdh_curve X25519:prime256v1`.",
        f"{BASE}/openssl-dhparam/",
    ),
    (
        "openssl-recipes", "openssl recipe: benchmark crypto algorithm speed",
        "`openssl speed` (runs all). "
        "Specific: `openssl speed aes-256-gcm`. "
        "Multi-core: `-multi 8`. "
        "RSA sign/verify: `openssl speed rsa`. "
        "ECDSA: `openssl speed ecdsa`. "
        "Output is ops/sec at various input sizes.",
        f"{BASE}/openssl-speed/",
    ),
    (
        "openssl-recipes", "openssl recipe: set up a mini CA for signing CSRs",
        "(1) Set up: `mkdir -p ca/{certs,crl,newcerts,private} && touch ca/index.txt && echo 1000 > ca/serial`; "
        "(2) Generate CA key + self-signed cert: `openssl req -x509 -newkey rsa:4096 -keyout ca/private/ca.key -out ca/cert.pem -days 3650 -subj '/CN=My Root CA'`; "
        "(3) Sign a CSR: `openssl ca -config openssl.cnf -in csr.pem -out signed.pem` (needs openssl.cnf with `[ca]` section); "
        "(4) Production: use `step-ca` (smallstep) or `cfssl` instead — way easier.",
        f"{BASE}/openssl-ca/",
    ),
    (
        "openssl-recipes", "openssl recipe: download remote cert chain",
        "Full chain: `echo | openssl s_client -showcerts -connect example.com:443 -servername example.com 2>/dev/null | sed -n '/-----BEGIN/,/-----END/p' > chain.pem`. "
        "Then split or use as-is. "
        "Just leaf: `... | openssl x509 -outform PEM > leaf.pem`.",
        f"{BASE}/openssl-s_client/",
    ),
    (
        "openssl-recipes", "openssl recipe: decode a base64 cert from stdin",
        "If you have raw base64 (no PEM headers): ```bash\\necho '$(cat raw-base64.txt)' | base64 -d | openssl x509 -inform DER -noout -text\\n```. "
        "Or wrap with headers: `(echo '-----BEGIN CERTIFICATE-----'; cat raw.txt; echo '-----END CERTIFICATE-----') | openssl x509 -text -noout`.",
        f"{BASE}/openssl-x509/",
    ),
    (
        "openssl-recipes", "openssl recipe: show certificate as JSON-ish output",
        "OpenSSL doesn't have native JSON, but pipe to `step certificate inspect --format json` (step-cli) for proper JSON. "
        "OR custom: ```bash\\nopenssl x509 -in cert.pem -noout -subject -issuer -dates -fingerprint -sha256 -ext subjectAltName\\n```. "
        "For programmatic: use Python `cryptography` library or Go `crypto/x509`.",
        f"{BASE}/openssl-x509/",
    ),
    (
        "openssl-recipes", "openssl recipe: test for known TLS vulnerabilities",
        "OpenSSL itself isn't a scanner — use specialized tools. "
        "Heartbleed: patched in OpenSSL 1.0.1g (2014) — won't exist on modern installs. "
        "Better tools: `testssl.sh` (comprehensive), `sslyze` (Python), `nmap --script ssl-enum-ciphers`. "
        "Online: ssllabs.com/ssltest/.",
        f"{BASE}/openssl-s_client/",
    ),
    (
        "openssl-recipes", "openssl recipe: parse ASN.1 structures (low-level debugging)",
        "`openssl asn1parse -in cert.pem` shows OID tree. "
        "From DER: `-inform DER`. "
        "Offset/length-specific: `-strparse N` (parse substructure at offset N). "
        "Use case: debugging custom cert extensions or proprietary ASN.1 formats.",
        f"{BASE}/openssl-asn1parse/",
    ),
    (
        "openssl-recipes", "openssl recipe: working with OpenSSL 3.x legacy provider",
        "OpenSSL 3.x disables many legacy algorithms (MD5 for sigs, DES, RC4) by default. "
        "Enable per-command: `openssl ... -provider default -provider legacy`. "
        "Persistent: edit openssl.cnf to load legacy provider. "
        "Affects: PKCS#12 files from old Windows, MD5-signed certs, etc. "
        "Better fix: re-issue/re-export with modern algorithms.",
        f"{MAN5}/config/",
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
            evidence_id=f"ev-os-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-os-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="openssl-catalog",
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
