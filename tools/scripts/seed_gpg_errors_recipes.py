"""Direct-insert gpg (GnuPG) error + recipe claims."""

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

BASE = "https://www.gnupg.org/documentation/manuals/gnupg"

CLAIMS = [
    # ============================================================
    # gpg-errors — 30 common errors
    # ============================================================
    (
        "gpg-errors", "gpg error: decryption failed: No secret key",
        "You don't have the private key for this ciphertext. Either it was encrypted for someone else, or your keyring doesn't have it. "
        "Fix: (1) list available secret keys: `gpg --list-secret-keys`; "
        "(2) check who it was for: `gpg --list-packets <file>` shows recipient key IDs; "
        "(3) import your secret key: `gpg --import secret-backup.asc`; "
        "(4) if you lost the secret key: data is unrecoverable — that's the whole point of public-key crypto.",
        f"{BASE}/Operational-GPG-Commands.html",
    ),
    (
        "gpg-errors", "gpg error: no default secret key / no secret key for signing",
        "You ran `gpg --sign` or `--clearsign` but no signing key is set or available. "
        "Fix: (1) list yours: `gpg --list-secret-keys`; "
        "(2) set default in ~/.gnupg/gpg.conf: `default-key <KEYID>`; "
        "(3) one-off: `gpg --local-user <KEYID> --sign <file>` (or `-u`); "
        "(4) if you genuinely have no secret key, generate one: `gpg --full-generate-key`.",
        f"{BASE}/GPG-Configuration-Options.html",
    ),
    (
        "gpg-errors", "gpg error: invalid recipient / public key not found for user X",
        "You're encrypting to a recipient whose public key isn't in your keyring. "
        "Fix: (1) list known keys: `gpg --list-keys`; "
        "(2) import: `gpg --import their-key.asc`; "
        "(3) fetch from keyserver: `gpg --keyserver hkps://keys.openpgp.org --search-keys their@email`; "
        "(4) use full fingerprint, not email, if multiple keys share an email; "
        "(5) trust check: `gpg --recipient <KEYID> --encrypt` may warn 'untrusted' — see 'not certified with a trusted signature' fix.",
        f"{BASE}/GPG-Commands.html",
    ),
    (
        "gpg-errors", "gpg warning: This key is not certified with a trusted signature!",
        "You're encrypting to a key that isn't in your web-of-trust. Encryption WILL succeed — this is informational. "
        "Fix: (1) verify fingerprint with the owner (out-of-band) then trust: `gpg --edit-key <KEYID>`, type `trust`, pick level (4 = fully); "
        "(2) suppress for a one-off: `gpg --trust-model always --recipient <KEYID> --encrypt`; "
        "(3) for repeated use, set in gpg.conf: `trust-model tofu+pgp` or `always`; "
        "(4) properly: sign their key with `lsign-key` (locally) or `sign-key` (publish to keyservers).",
        f"{BASE}/Trust-Model-Configuration.html",
    ),
    (
        "gpg-errors", "gpg error: can't connect to the agent / IPC connect call failed",
        "gpg-agent isn't running, OR its socket path differs from what gpg expects. "
        "Fix: (1) start it: `gpg-agent --daemon`; "
        "(2) restart cleanly: `gpgconf --kill gpg-agent` (will auto-restart); "
        "(3) socket location: `gpgconf --list-dirs agent-socket`; "
        "(4) Linux+systemd: `systemctl --user start gpg-agent.socket`; "
        "(5) on remote SSH: forward the agent socket with `RemoteForward` or `gpgconf --launch gpg-agent` on remote.",
        f"{BASE}/Invoking-GPG_002dAGENT.html",
    ),
    (
        "gpg-errors", "gpg error: BAD passphrase / Bad session key",
        "Wrong passphrase (or stale cached one). "
        "Fix: (1) retry — pinentry may show the wrong prompt if multiple keys share passphrases; "
        "(2) clear cached: `gpgconf --reload gpg-agent` (or `echo RELOADAGENT | gpg-connect-agent`); "
        "(3) for fully scripted use: `--pinentry-mode loopback --passphrase 'X'` (insecure in shell history); "
        "(4) prefer `--passphrase-file <path>` or `--passphrase-fd 0` with secure source.",
        f"{BASE}/GPG-Options.html",
    ),
    (
        "gpg-errors", "gpg error: agent_genkey failed: No pinentry / Inappropriate ioctl for device",
        "No pinentry program found to prompt for passphrase — common in headless/SSH/CI contexts. "
        "Fix: (1) install pinentry: macOS `brew install pinentry-mac`; Linux `apt install pinentry-tty` or `pinentry-curses`; "
        "(2) configure in ~/.gnupg/gpg-agent.conf: `pinentry-program /usr/bin/pinentry-curses` (path varies); "
        "(3) reload: `gpgconf --kill gpg-agent`; "
        "(4) for SSH: `export GPG_TTY=$(tty)` in ~/.bashrc and use TTY-based pinentry; "
        "(5) in CI: use loopback mode (`--pinentry-mode loopback`) + passphrase via stdin.",
        f"{BASE}/Invoking-GPG_002dAGENT.html",
    ),
    (
        "gpg-errors", "gpg error: keyserver receive failed / connection failed",
        "Couldn't reach keyserver — usually network, firewall, or keyserver is down/migrating. "
        "Fix: (1) try a different keyserver: `--keyserver hkps://keys.openpgp.org` (most reliable currently); "
        "(2) try `keys.mailvelope.com` or `pgp.mit.edu` (legacy); "
        "(3) HTTPS issue? add `--keyserver-options no-honor-keyserver-url`; "
        "(4) corporate proxy: set `dirmngr` `http-proxy` in ~/.gnupg/dirmngr.conf; "
        "(5) test directly: `curl https://keys.openpgp.org/vks/v1/by-fingerprint/<FPR>`.",
        f"{BASE}/Invoking-DIRMNGR.html",
    ),
    (
        "gpg-errors", "gpg error: cannot open '<file>': No such file or directory",
        "Input file not found or not readable. "
        "Fix: (1) check spelling + path: `ls <file>`; "
        "(2) for stdin: omit filename — `cat file | gpg --encrypt -r ...`; "
        "(3) absolute path is safest; "
        "(4) check perms: `ls -l <file>` — gpg can't read non-readable files; "
        "(5) for unusual filenames: use `--` separator: `gpg --decrypt -- --weird-name.gpg`.",
        f"{BASE}/GPG-Commands.html",
    ),
    (
        "gpg-errors", "gpg warning: unsafe permissions on configuration file / homedir",
        "GnuPG refuses to use a homedir or config with permissions > 700 (security). "
        "Fix: (1) `chmod 700 ~/.gnupg` and `chmod 600 ~/.gnupg/*`; "
        "(2) owner check: must be your user, not root — `chown -R $USER ~/.gnupg`; "
        "(3) for custom homedir: `gpg --homedir /path` with same perms; "
        "(4) Docker volume mounts often expose this — adjust the host directory.",
        f"{BASE}/GPG-Configuration.html",
    ),
    (
        "gpg-errors", "gpg error: bad signature / signature verification failed",
        "The signed file or signature doesn't match. Three causes: signature is fake/tampered, file was modified after signing, or you have the wrong public key. "
        "Fix: (1) re-download both file + signature from the source; "
        "(2) confirm you have the right signing key: `gpg --verify <sig>` shows the key ID — verify that ID matches the signer's published fingerprint; "
        "(3) for detached sigs: `gpg --verify file.sig file` (NOT just `--verify file.sig`); "
        "(4) check that key isn't expired or revoked.",
        f"{BASE}/GPG-Commands.html",
    ),
    (
        "gpg-errors", "gpg warning: key X is expired",
        "Key passed its expiration date. Encryption/signing with it is refused (verification of OLD signatures still works). "
        "Fix: (1) for your own key: extend it — `gpg --edit-key <KEYID>`, then `key 0` (select primary), `expire`, set new date, save; "
        "(2) republish: `gpg --send-keys <KEYID>` and/or re-export public key; "
        "(3) for someone else's expired key: contact them to renew, or use their renewed key from keyserver: `gpg --refresh-keys`.",
        f"{BASE}/Operational-GPG-Commands.html",
    ),
    (
        "gpg-errors", "gpg error: skipped '<recipient>': Unusable public key",
        "The key you tried to encrypt to is revoked, expired, or has no encryption-capable subkey. "
        "Fix: (1) inspect: `gpg --list-keys <KEYID>` — look for 'expired', 'revoked', or missing 'E' (encrypt) capability; "
        "(2) for refresh: `gpg --refresh-keys` (re-fetches from keyserver — may get a new subkey); "
        "(3) ask key owner for an updated copy; "
        "(4) if key has only [SC] (sign/cert), they need to add an [E] subkey.",
        f"{BASE}/GPG-Commands.html",
    ),
    (
        "gpg-errors", "gpg warning: digest algorithm SHA1 is deprecated",
        "Modern gpg flags SHA1 as weak. Usually a config issue, not blocking. "
        "Fix: (1) set in ~/.gnupg/gpg.conf: `personal-digest-preferences SHA512 SHA384 SHA256` and `cert-digest-algo SHA512`; "
        "(2) update key preferences: `gpg --edit-key <KEY>`, then `setpref SHA512 SHA384 SHA256 AES256 ZLIB Uncompressed`; save; "
        "(3) ignore if you're only verifying old signatures.",
        f"{BASE}/OpenPGP-Options.html",
    ),
    (
        "gpg-errors", "gpg error: key X: signature not checked due to a missing key",
        "You're verifying a signature whose signing key isn't in your keyring. "
        "Fix: (1) note the long key ID from the message; "
        "(2) fetch it: `gpg --keyserver hkps://keys.openpgp.org --recv-keys <KEYID>`; "
        "(3) auto-retrieve on verify: gpg.conf `keyserver-options auto-key-retrieve`; "
        "(4) re-verify: `gpg --verify <sig>`.",
        f"{BASE}/Key-related-Options.html",
    ),
    (
        "gpg-errors", "gpg error: not a detached signature / invalid armor header",
        "Either you used wrong invocation, or the signature file is corrupted. "
        "Fix: (1) detached sig verify: `gpg --verify file.sig file.txt` (TWO args, sig first); "
        "(2) inline sig (sig + data in one file): `gpg --verify file.sig.asc` (one arg); "
        "(3) clearsign verify: `gpg --verify file.txt.asc`; "
        "(4) for ASCII armor: file should start with `-----BEGIN PGP SIGNATURE-----`; "
        "(5) for binary: not human-readable but `gpg --list-packets` shows structure.",
        f"{BASE}/GPG-Commands.html",
    ),
    (
        "gpg-errors", "gpg error: gpg: WARNING: server 'gpg-agent' is older than us",
        "Mixed-version installation — `gpg` binary is newer than the running agent. "
        "Fix: (1) kill old agent: `gpgconf --kill gpg-agent`; "
        "(2) start fresh: it auto-starts on next gpg invocation; "
        "(3) check versions: `gpg --version` and `gpg-agent --version` should match; "
        "(4) common after macOS Homebrew upgrades — `brew upgrade gnupg` may leave a stale agent.",
        f"{BASE}/Invoking-GPG_002dAGENT.html",
    ),
    (
        "gpg-errors", "gpg error: There is no assurance this key belongs to the named user",
        "Same root cause as the 'not certified with trusted signature' warning — printed before encrypting/signing for an untrusted key. "
        "Fix: (1) verify fingerprint with owner over a trusted channel: `gpg --fingerprint <KEYID>`; "
        "(2) once verified, sign their key: `gpg --edit-key <KEYID>` → `sign` (publish) or `lsign` (local only); "
        "(3) or set trust: `gpg --edit-key <KEYID>` → `trust` → pick level; "
        "(4) for systematic trust: configure `trust-model tofu+pgp`.",
        f"{BASE}/Trust-Model-Configuration.html",
    ),
    (
        "gpg-errors", "gpg error: error sending to agent: <error>",
        "Communication with gpg-agent broken — usually socket issues or stale agent. "
        "Fix: (1) reset agent: `gpgconf --kill gpg-agent`; "
        "(2) check socket: `ls -la $(gpgconf --list-dirs agent-socket)`; "
        "(3) `GPG_TTY` env var: `export GPG_TTY=$(tty)` in ~/.bashrc; "
        "(4) Wayland/Xwayland users: may need `pinentry-gtk2` or `pinentry-qt`; "
        "(5) Docker/SSH: agent forwarding needs `--mount type=bind,source=$(gpgconf --list-dirs agent-extra-socket),target=/...`.",
        f"{BASE}/Invoking-GPG_002dAGENT.html",
    ),
    (
        "gpg-errors", "gpg error: keyring '<path>' created",
        "Informational, not an error. First-time use creates ~/.gnupg/ and keyrings. "
        "Fix: (1) ignore — next operations will work normally; "
        "(2) safe to suppress: `--quiet`; "
        "(3) for custom homedir: `gpg --homedir /path` creates there instead.",
        f"{BASE}/GPG-Configuration.html",
    ),
    (
        "gpg-errors", "gpg error: pinentry-mac / pinentry-curses not found",
        "Configured pinentry program doesn't exist. "
        "Fix: (1) find one: `which pinentry pinentry-mac pinentry-curses pinentry-tty pinentry-gtk-2`; "
        "(2) update ~/.gnupg/gpg-agent.conf: `pinentry-program /full/path/to/pinentry`; "
        "(3) reload: `gpgconf --kill gpg-agent`; "
        "(4) macOS: `brew install pinentry-mac`; "
        "(5) Linux headless: `apt install pinentry-curses` (TTY-based).",
        f"{BASE}/Invoking-GPG_002dAGENT.html",
    ),
    (
        "gpg-errors", "gpg error: WARNING: cipher algorithm CAST5 not found in preferences",
        "Key has outdated cipher preferences. "
        "Fix: (1) update key: `gpg --edit-key <KEY>`, then `setpref AES256 AES192 AES SHA512 SHA384 SHA256 ZLIB`, save; "
        "(2) gpg.conf default: `personal-cipher-preferences AES256 AES192 AES`; "
        "(3) ignore if just decrypting old material — algorithm is auto-detected from the message.",
        f"{BASE}/OpenPGP-Options.html",
    ),
    (
        "gpg-errors", "gpg error: agent_genkey failed: Cancelled / key generation cancelled",
        "User dismissed the pinentry prompt. "
        "Fix: (1) re-run `gpg --full-generate-key` and complete the prompt; "
        "(2) for headless: use `--batch` mode with a config file: `gpg --batch --generate-key < params.txt`; "
        "(3) sample params.txt: `%no-protection\\nKey-Type: RSA\\nKey-Length: 4096\\nName-Real: Alice\\nName-Email: a@b\\nExpire-Date: 1y\\n%commit`.",
        f"{BASE}/Howto-Specify-a-User-ID.html",
    ),
    (
        "gpg-errors", "gpg error: deleting secret key failed / no secret key",
        "You tried `--delete-secret-keys` for a key you don't have the secret for. "
        "Fix: (1) for public: `gpg --delete-keys <KEYID>` (not `--delete-secret-keys`); "
        "(2) to remove both: `gpg --delete-secret-and-public-keys <KEYID>`; "
        "(3) requires interactive confirmation (or `--yes` to skip); "
        "(4) for partial smartcard scenarios: keys can be marked as 'card present' — actual removal of card needs the physical token.",
        f"{BASE}/Operational-GPG-Commands.html",
    ),
    (
        "gpg-errors", "gpg error: CRC error / invalid armor / bad MIC",
        "ASCII-armored file is corrupted (truncated, wrong line endings, copy-paste damage). "
        "Fix: (1) re-download or re-paste the entire `-----BEGIN`...`-----END` block; "
        "(2) check for whitespace damage (especially Windows CRLF vs LF); "
        "(3) try binary instead: ask sender to omit `--armor`; "
        "(4) test: `gpg --list-packets <file>` should print structure if armor is valid.",
        f"{BASE}/GPG-Input-and-Output.html",
    ),
    (
        "gpg-errors", "gpg error: WARNING: using insecure memory! / Locked memory failed",
        "On Linux, gpg wanted to lock pages in RAM (prevent swap-out of secrets) but kernel refused. "
        "Fix: (1) add gpg user to memlock-allowed group, OR install as setuid root (legacy, not recommended); "
        "(2) raise limit: `ulimit -l 64` in shell init; "
        "(3) systemd-user override: `LimitMEMLOCK=infinity` in service unit; "
        "(4) for most use cases: ignore — the warning is informational, gpg falls back to safe practices.",
        f"{BASE}/GPG-Options.html",
    ),
    (
        "gpg-errors", "gpg error: subkey X has been revoked",
        "Subkey is revoked but parent key may still be valid. "
        "Fix: (1) check status: `gpg --list-keys <KEYID>` — `rev!` marks revoked subkeys; "
        "(2) ask owner if a replacement subkey exists: `gpg --refresh-keys` may pull it; "
        "(3) for verification of old signatures made by revoked-but-not-tampered subkey: `--allow-weak-key-signatures`.",
        f"{BASE}/Key-related-Options.html",
    ),
    (
        "gpg-errors", "gpg error: SmartCard not available / scdaemon not running",
        "GPG can't reach the smartcard daemon (scdaemon), or no card is inserted/reachable. "
        "Fix: (1) check card: `gpg --card-status`; "
        "(2) install drivers: macOS `brew install --cask yubico-yubikey-manager`; Linux `apt install scdaemon pcscd`; "
        "(3) `systemctl start pcscd` (linux); "
        "(4) restart scdaemon: `gpgconf --kill scdaemon`; "
        "(5) for conflicts (other apps using card): close all then retry.",
        f"{BASE}/Invoking-GPG_002dAGENT.html",
    ),
    (
        "gpg-errors", "gpg error: WARNING: legacy pubkey algorithm DSA / RSA1024 in use",
        "Key uses deprecated algorithm — still works but security is lower. "
        "Fix: (1) generate a new key (RSA 4096 or ED25519): `gpg --full-generate-key`; "
        "(2) for ed25519: select ECC → Curve 25519; "
        "(3) transfer signatures: sign new key with old key (`--sign-key`); "
        "(4) publish new key + revocation cert for old: `gpg --send-keys NEW; gpg --revoke-key OLD`.",
        f"{BASE}/Compliance-Options.html",
    ),
    (
        "gpg-errors", "gpg error: detected unsupported version of GnuPG / file is older format",
        "Incompatible gpg versions. Older 1.x can't read all 2.x material, and vice versa for some new packet types. "
        "Fix: (1) upgrade: `brew install gnupg` / `apt install gnupg2`; "
        "(2) check version: `gpg --version` — modern is 2.4+; "
        "(3) for legacy export (1.4): `gpg --export-options export-pka` or `--export-options no-export-attributes`; "
        "(4) GPG 2.x homedir is generally backward-readable.",
        f"{BASE}/Invoking-GPG.html",
    ),

    # ============================================================
    # gpg-recipes — 36 common workflows
    # ============================================================
    (
        "gpg-recipes", "gpg recipe: generate a new key pair",
        "Modern interactive: `gpg --full-generate-key` (or `--full-gen-key`). "
        "Recommended choices: (1) `ECC and ECC` (option 9), then `Curve 25519` — smallest, fastest, modern; OR (2) `RSA and RSA`, then `4096` (broader compat). "
        "Real name + email (used as User ID), expiry (`1y` or `2y` is sensible — extend later, never set 0). "
        "Quick (no prompts): `gpg --quick-generate-key 'Alice <a@b>' default default 1y`. "
        "After: print fingerprint and BACK UP secret key (see export recipe).",
        f"{BASE}/Operational-GPG-Commands.html",
    ),
    (
        "gpg-recipes", "gpg recipe: list your keys (public and secret)",
        "Public: `gpg --list-keys` (or `--list-public-keys`). "
        "Secret/private: `gpg --list-secret-keys`. "
        "Long format with full keygrips: `--list-keys --keyid-format LONG --with-fingerprint --with-subkey-fingerprint`. "
        "Filter by uid: `gpg --list-keys alice@example.com`. "
        "Verbose (capabilities, algos, dates): `--list-keys -v`.",
        f"{BASE}/Operational-GPG-Commands.html",
    ),
    (
        "gpg-recipes", "gpg recipe: export your public key",
        "ASCII-armored to share: `gpg --armor --export <KEYID> > alice-pub.asc`. "
        "By email: `gpg --armor --export alice@example.com > pub.asc`. "
        "Binary (smaller): drop `--armor`. "
        "All keys: `gpg --armor --export > all-pub.asc`. "
        "Then share `alice-pub.asc` or upload to keyserver (see send-keys recipe).",
        f"{BASE}/GPG-Commands.html",
    ),
    (
        "gpg-recipes", "gpg recipe: export your secret key (back up safely)",
        "BACKUP! Loss = unrecoverable data. "
        "`gpg --armor --export-secret-keys <KEYID> > alice-secret.asc`. "
        "Treat the output like a password: chmod 600, encrypt-at-rest (LUKS/FileVault/encrypted USB), DO NOT commit to git. "
        "Also export ownertrust: `gpg --export-ownertrust > trust.txt` — re-import with `--import-ownertrust`. "
        "For paper backup: paperkey tool (`paperkey --secret-key key.asc --output key.txt`).",
        f"{BASE}/GPG-Commands.html",
    ),
    (
        "gpg-recipes", "gpg recipe: import a public key from a file",
        "`gpg --import alice-pub.asc`. "
        "From URL: `curl -s https://example.com/key.asc | gpg --import`. "
        "Bulk: `gpg --import *.asc`. "
        "Verify the imported key's fingerprint matches what was advertised: `gpg --fingerprint alice@example.com`. "
        "Then trust if appropriate (`--edit-key` + `trust`).",
        f"{BASE}/Operational-GPG-Commands.html",
    ),
    (
        "gpg-recipes", "gpg recipe: import keys from a keyserver",
        "By key ID: `gpg --keyserver hkps://keys.openpgp.org --recv-keys 0xABCDEF12...`. "
        "Search by email: `gpg --keyserver hkps://keys.openpgp.org --search-keys alice@example.com` (interactive). "
        "Most reliable keyservers (2024+): `keys.openpgp.org`, `pgp.surf.nl`. "
        "Set default in gpg.conf: `keyserver hkps://keys.openpgp.org`. "
        "Auto-retrieve on signature verify: gpg.conf `keyserver-options auto-key-retrieve`.",
        f"{BASE}/Operational-GPG-Commands.html",
    ),
    (
        "gpg-recipes", "gpg recipe: send your public key to a keyserver",
        "`gpg --keyserver hkps://keys.openpgp.org --send-keys <KEYID>`. "
        "For keys.openpgp.org specifically: must verify email after first upload (it emails confirmation links). "
        "Caveat: keyservers don't enforce identity — anyone can upload your public key. Combine with your website's `pubkey.asc` for stronger pinning. "
        "WKD (Web Key Directory) is more modern: publish at `https://example.com/.well-known/openpgpkey/...`.",
        f"{BASE}/Operational-GPG-Commands.html",
    ),
    (
        "gpg-recipes", "gpg recipe: edit a key (add UID, change expiry, add subkey)",
        "`gpg --edit-key <KEYID>` enters an interactive prompt. Commands: "
        "`adduid` (add another email/name); `deluid` (mark deleted); "
        "`expire` (change expiry of primary or selected subkey); `key N` (select subkey by index 1..N); "
        "`addkey` (add a subkey — useful for separating sign vs encrypt); "
        "`passwd` (change passphrase); `trust` (set ownertrust); "
        "`save` (commit changes) or `quit`. "
        "Always `save` — `quit` without save discards.",
        f"{BASE}/Operational-GPG-Commands.html",
    ),
    (
        "gpg-recipes", "gpg recipe: generate + store a revocation certificate (do this NOW)",
        "Generates a 'kill switch' for your key if you ever lose/leak the secret. "
        "`gpg --output revoke.asc --gen-revoke <KEYID>`. "
        "Choose reason 0 (compromised) — most general. Provide passphrase. "
        "Save revoke.asc to OFFLINE storage (USB drive in safe, printed paper). "
        "If key is ever compromised, `gpg --import revoke.asc && gpg --send-keys <KEYID>` to push revocation to keyservers. "
        "Even if you lose secret key, anyone with revoke.asc can broadcast invalidation.",
        f"{BASE}/Operational-GPG-Commands.html",
    ),
    (
        "gpg-recipes", "gpg recipe: encrypt a file for one recipient",
        "`gpg --encrypt --recipient alice@example.com --output file.gpg file.txt`. "
        "Short form: `gpg -e -r alice@example.com file.txt` (output → file.txt.gpg). "
        "ASCII-armored (for email/paste): add `--armor` (or `-a`) — output → `file.txt.asc`. "
        "For untrusted recipient: add `--trust-model always`. "
        "By key fingerprint (avoid email ambiguity): `--recipient 0xABCDEF...`.",
        f"{BASE}/GPG-Examples.html",
    ),
    (
        "gpg-recipes", "gpg recipe: encrypt for multiple recipients",
        "Just repeat `-r`: `gpg --encrypt -r alice@x -r bob@y -r carol@z file.txt`. "
        "All three can independently decrypt. (Symmetric session key is shared; output has one PKESK packet per recipient.) "
        "Auto-include self: gpg.conf `encrypt-to <YOUR-KEYID>` — ensures you can always decrypt your own outgoing files. "
        "From a group: gpg.conf `group team = alice@x bob@y carol@z`, then `gpg -e -r team file.txt`.",
        f"{BASE}/GPG-Options.html",
    ),
    (
        "gpg-recipes", "gpg recipe: encrypt with a passphrase only (symmetric — no keys)",
        "`gpg --symmetric --cipher-algo AES256 file.txt`. "
        "Short: `gpg -c file.txt`. "
        "Prompts for passphrase, no recipient needed. Outputs `file.txt.gpg`. "
        "Decrypt: `gpg --decrypt file.txt.gpg > file.txt` (will prompt for passphrase). "
        "Use case: backups, sharing with someone who doesn't have a key — but you must transmit the passphrase securely separately.",
        f"{BASE}/GPG-Examples.html",
    ),
    (
        "gpg-recipes", "gpg recipe: decrypt a file",
        "`gpg --output file.txt --decrypt file.txt.gpg`. "
        "Or shorter: `gpg --decrypt file.txt.gpg > file.txt`. "
        "Inferred from extension: `gpg file.txt.gpg` (auto-decrypts to file.txt). "
        "Prompts for passphrase via pinentry. "
        "For batch / non-interactive: `--pinentry-mode loopback --passphrase 'X'` (insecure) OR `--passphrase-file <path>` (better — restrict perms on file).",
        f"{BASE}/GPG-Examples.html",
    ),
    (
        "gpg-recipes", "gpg recipe: sign a file (detached signature)",
        "Most common form. Recipient verifies authenticity of original file without modifying it. "
        "`gpg --detach-sign --armor file.txt` → produces `file.txt.asc`. "
        "Distribute both `file.txt` AND `file.txt.asc`. "
        "Verify: `gpg --verify file.txt.asc file.txt`. "
        "Binary sig (smaller): drop `--armor` → `file.txt.sig`. "
        "Use specific signing key: `--local-user <KEYID>` (or `-u`).",
        f"{BASE}/GPG-Examples.html",
    ),
    (
        "gpg-recipes", "gpg recipe: sign + encrypt at once",
        "`gpg --sign --encrypt --recipient bob@example.com file.txt`. "
        "Recipient verifies it came from you AND only they can read it. "
        "Short: `gpg -se -r bob@example.com file.txt`. "
        "Output: `file.txt.gpg` (or `.asc` with `--armor`). "
        "Receiver: `gpg --decrypt file.txt.gpg > out.txt` — prints signature verification info on stderr.",
        f"{BASE}/GPG-Examples.html",
    ),
    (
        "gpg-recipes", "gpg recipe: clearsign text (sign + keep readable)",
        "Embed signature in the text itself — common for emails, code releases, public notices. "
        "`gpg --clearsign message.txt` → produces `message.txt.asc` containing the original text wrapped in `-----BEGIN PGP SIGNED MESSAGE-----`. "
        "Anyone with your public key can verify: `gpg --verify message.txt.asc`. "
        "Modifications to the text break the signature.",
        f"{BASE}/GPG-Examples.html",
    ),
    (
        "gpg-recipes", "gpg recipe: verify a signature",
        "Detached: `gpg --verify file.txt.sig file.txt` (sig file first, then original). "
        "Inline (clearsign or sign+armor): `gpg --verify file.txt.asc` (one arg). "
        "Good output: `gpg: Good signature from \"Alice <a@b>\" ... using EDDSA key ABCDEF...`. "
        "Caveats: ALSO check the key fingerprint matches what you expect — a 'Good signature from someone unknown' is not security; "
        "auto-fetch missing keys: gpg.conf `keyserver-options auto-key-retrieve`.",
        f"{BASE}/GPG-Commands.html",
    ),
    (
        "gpg-recipes", "gpg recipe: sign git commits + tags",
        "Configure: `git config --global user.signingkey <KEYID>` (find with `gpg --list-secret-keys --keyid-format LONG`) and `git config --global commit.gpgsign true`. "
        "Per-commit: `git commit -S -m 'msg'`. "
        "Sign tag: `git tag -s v1.0 -m 'release'`. "
        "Show signature on log: `git log --show-signature`. "
        "On GitHub: upload public key at https://github.com/settings/keys for 'Verified' badge. "
        "For SSH keys instead of GPG: `git config gpg.format ssh` (Git 2.34+).",
        f"{BASE}/GPG-Commands.html",
    ),
    (
        "gpg-recipes", "gpg recipe: cache passphrase in gpg-agent",
        "gpg-agent caches passphrase between operations. Tune in ~/.gnupg/gpg-agent.conf: "
        "`default-cache-ttl 3600` (1 hour from last use); "
        "`max-cache-ttl 7200` (2 hour absolute max). "
        "Force re-cache: `gpgconf --reload gpg-agent`. "
        "Clear cache now: `echo RELOADAGENT | gpg-connect-agent`. "
        "For ssh-key (when used as SSH agent): separate TTLs `default-cache-ttl-ssh` and `max-cache-ttl-ssh`.",
        f"{BASE}/Agent-Options.html",
    ),
    (
        "gpg-recipes", "gpg recipe: use gpg-agent as SSH agent",
        "Replace ssh-agent with gpg-agent (handy if you store SSH key in YubiKey via GPG). "
        "Enable in ~/.gnupg/gpg-agent.conf: `enable-ssh-support`. "
        "Set env: `export SSH_AUTH_SOCK=$(gpgconf --list-dirs agent-ssh-socket)` in ~/.bashrc. "
        "Add an authentication-capable subkey to your GPG key, then `ssh-add -l` should show it. "
        "Reload: `gpgconf --kill gpg-agent`.",
        f"{BASE}/Invoking-GPG_002dAGENT.html",
    ),
    (
        "gpg-recipes", "gpg recipe: change a key's expiration date",
        "`gpg --edit-key <KEYID>`. "
        "For primary key: `expire`, type new date (`1y`, `2y`, `2025-12-31`, or `0` for never — discouraged); confirm; `save`. "
        "For specific subkey: `key 1` (select subkey 1), then `expire`. Repeat for each. "
        "Don't forget to redistribute: `gpg --send-keys <KEYID>` to update keyservers, or re-share your exported public key.",
        f"{BASE}/Operational-GPG-Commands.html",
    ),
    (
        "gpg-recipes", "gpg recipe: add a signing-only subkey (separate from encryption)",
        "Best practice: keep primary key offline, use subkeys for daily ops. "
        "`gpg --edit-key <KEYID>` → `addkey` → choose `RSA (sign only)` or `ECC (sign only)`. "
        "Pick size + expiry. Repeat for `encrypt only` and `auth only` if needed. "
        "Move primary secret offline (paperkey or USB), keep only subkey secrets on daily machines: see 'remove primary secret key' recipe.",
        f"{BASE}/Operational-GPG-Commands.html",
    ),
    (
        "gpg-recipes", "gpg recipe: store keys on a YubiKey (smartcard)",
        "Keep secrets in hardware. "
        "(1) Insert YubiKey, run `gpg --card-edit`; "
        "(2) `admin` then `factory-reset` (only on new card); "
        "(3) set new PINs: `passwd` → 3 (admin), 1 (user); "
        "(4) generate keys ON-CARD: `generate` (keys never leave card) OR move existing: `gpg --edit-key <KEY>`, `key 1`, `keytocard`; "
        "(5) test: remove + insert card, then `gpg --sign test.txt` → prompts for PIN, not passphrase. "
        "Backup: revocation cert + a copy of unique keys outside the card BEFORE moving.",
        f"{BASE}/Operational-GPG-Commands.html",
    ),
    (
        "gpg-recipes", "gpg recipe: encrypt to self automatically",
        "So you can decrypt files you sent out. "
        "In ~/.gnupg/gpg.conf: `encrypt-to <YOUR-KEYID>`. "
        "Now every `gpg -e -r recipient` automatically includes you. "
        "Verify: `gpg --list-packets <out.gpg>` should show 2+ PKESK packets.",
        f"{BASE}/GPG-Options.html",
    ),
    (
        "gpg-recipes", "gpg recipe: bulk-encrypt files in a directory",
        "Loop: `for f in *.txt; do gpg --batch --yes --encrypt -r alice@x -o \"$f.gpg\" \"$f\"; done`. "
        "Skip if .gpg exists: `[[ -f \"$f.gpg\" ]] || gpg ...`. "
        "Recursive: combine `find . -type f -name '*.txt'` with the loop. "
        "For archive instead: `tar czf - dir/ | gpg -e -r alice@x > dir.tar.gz.gpg` (single output).",
        f"{BASE}/GPG-Examples.html",
    ),
    (
        "gpg-recipes", "gpg recipe: pipe data through gpg via stdin/stdout",
        "Encrypt: `echo 'secret' | gpg --encrypt --armor -r alice@x`. "
        "Decrypt: `cat file.gpg | gpg --decrypt > file`. "
        "Sign: `echo 'msg' | gpg --clearsign`. "
        "Stream large files: `tar c dir | gpg -e -r alice@x | ssh remote 'cat > backup.tgz.gpg'`. "
        "Batch (no prompts): add `--batch --yes`.",
        f"{BASE}/GPG-Input-and-Output.html",
    ),
    (
        "gpg-recipes", "gpg recipe: migrate keys to a new machine",
        "On old machine: export everything. ```\ngpg --export --armor > pubkeys.asc\ngpg --export-secret-keys --armor > seckeys.asc\ngpg --export-ownertrust > trust.txt\n```. "
        "Transfer all three over an encrypted channel (scp + LUKS-encrypted disk, NOT email). "
        "On new machine: ```\ngpg --import pubkeys.asc\ngpg --import seckeys.asc\ngpg --import-ownertrust < trust.txt\n```. "
        "Verify: `gpg --list-secret-keys` on new machine matches old. "
        "On old machine: securely wipe `~/.gnupg` if you're not keeping it.",
        f"{BASE}/GPG-Commands.html",
    ),
    (
        "gpg-recipes", "gpg recipe: set TOFU (trust on first use) trust model",
        "Lighter than web-of-trust. Each new key is marked unknown; subsequent verifies build confidence. "
        "Enable: gpg.conf `trust-model tofu+pgp` (combines TOFU with WoT — most flexible). "
        "Inspect: `gpg --with-tofu-info --list-keys <KEYID>`. "
        "If TOFU flags conflict (same email, different key): manually resolve with `gpg --tofu-policy <good|unknown|bad> <KEY>`.",
        f"{BASE}/Trust-Model-Configuration.html",
    ),
    (
        "gpg-recipes", "gpg recipe: refresh local keyring from keyservers",
        "Pull updates (new signatures, revocations, expiry extensions) for all your imported keys. "
        "`gpg --refresh-keys`. "
        "From specific server: `gpg --keyserver hkps://keys.openpgp.org --refresh-keys`. "
        "Caveat: keyservers can pollute keys with spam signatures — modern hkps://keys.openpgp.org strips third-party sigs; older servers don't. "
        "For one key: `gpg --refresh-keys <KEYID>`.",
        f"{BASE}/Operational-GPG-Commands.html",
    ),
    (
        "gpg-recipes", "gpg recipe: verify a signed file you downloaded (real-world example)",
        "Downloading a release tarball + signature: ```\ncurl -O https://example.com/app-1.0.tar.gz\ncurl -O https://example.com/app-1.0.tar.gz.sig\ngpg --keyserver hkps://keys.openpgp.org --recv-keys <MAINTAINER-FPR>\ngpg --verify app-1.0.tar.gz.sig app-1.0.tar.gz\n```. "
        "Check: (1) 'Good signature' line; (2) signer fingerprint matches what project advertises; (3) signing key not expired/revoked.",
        f"{BASE}/GPG-Examples.html",
    ),
    (
        "gpg-recipes", "gpg recipe: search and inspect key details before importing",
        "Search keyserver without importing yet: `gpg --keyserver hkps://keys.openpgp.org --search-keys alice@example.com`. "
        "Inspect ASCII-armored key file: `gpg --show-keys alice.asc` (Gnupg 2.2+) shows User IDs + subkeys + fingerprints WITHOUT importing. "
        "List packets (very low-level): `gpg --list-packets alice.asc`. "
        "Only import after verifying fingerprint via independent channel.",
        f"{BASE}/Operational-GPG-Commands.html",
    ),
    (
        "gpg-recipes", "gpg recipe: noninteractive / batch generate a key (CI)",
        "Generate key without prompts. ```\ncat > params.txt <<EOF\n%no-protection\nKey-Type: EDDSA\nKey-Curve: Ed25519\nName-Real: CI Bot\nName-Email: ci@example.com\nExpire-Date: 1y\n%commit\nEOF\ngpg --batch --generate-key params.txt\nrm params.txt\n```. "
        "`%no-protection` = no passphrase (suitable for ephemeral CI signing keys only). "
        "For protected: omit that line and add `Passphrase: <secret>`.",
        f"{BASE}/Operational-GPG-Commands.html",
    ),
    (
        "gpg-recipes", "gpg recipe: encrypt for self only (e.g. encrypted personal notes)",
        "`gpg --encrypt --recipient <YOUR-EMAIL> --output notes.txt.gpg notes.txt`. "
        "Or use symmetric (no key needed): `gpg -c notes.txt` (passphrase prompt). "
        "Decrypt: `gpg --decrypt notes.txt.gpg`. "
        "For a directory: `tar c notes/ | gpg -e -r self > notes.tar.gpg`. "
        "Convenience tools: `pass` (passwordstore.org) uses gpg under the hood for password storage.",
        f"{BASE}/GPG-Examples.html",
    ),
    (
        "gpg-recipes", "gpg recipe: configure gpg.conf for sensible defaults",
        "Recommended ~/.gnupg/gpg.conf: ```\nkeyid-format LONG\nwith-fingerprint\nuse-agent\npersonal-cipher-preferences AES256 AES192 AES\npersonal-digest-preferences SHA512 SHA384 SHA256\ncert-digest-algo SHA512\ndefault-preference-list AES256 AES192 AES SHA512 SHA384 SHA256 ZLIB ZIP Uncompressed\nkeyserver hkps://keys.openpgp.org\nkeyserver-options auto-key-retrieve\n```. "
        "And ~/.gnupg/gpg-agent.conf: ```\ndefault-cache-ttl 3600\nmax-cache-ttl 86400\npinentry-program /usr/local/bin/pinentry-mac\n```.",
        f"{BASE}/GPG-Configuration-Options.html",
    ),
    (
        "gpg-recipes", "gpg recipe: troubleshoot tty / pinentry not appearing in SSH",
        "Symptom: `gpg --sign` hangs or fails silently over SSH. "
        "Fix: in ~/.bashrc add `export GPG_TTY=$(tty)` and `gpgconf --launch gpg-agent`. "
        "Use TTY pinentry: ~/.gnupg/gpg-agent.conf set `pinentry-program /usr/bin/pinentry-tty` (or `pinentry-curses`). "
        "Reload: `gpgconf --kill gpg-agent`. "
        "For agent forwarding from local → SSH server: forward the extra-socket: `ssh -R /run/user/1000/gnupg/S.gpg-agent:$(gpgconf --list-dirs agent-extra-socket) user@host`.",
        f"{BASE}/Invoking-GPG_002dAGENT.html",
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
            evidence_id=f"ev-gpg-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-gpg-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="gpg-catalog",
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
