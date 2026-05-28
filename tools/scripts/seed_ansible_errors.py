"""Direct-insert 30 common ansible error-recovery claims into the bulk DB.

These don't fit the URL-ingest path because the canonical knowledge isn't on
a single docs.ansible.com page — each error's diagnosis is synthesized from
multiple sources (the troubleshooting page, become docs, intro_inventory,
github issues). We attach an OFFICIAL_DOCS evidence pointing to the closest
authoritative ansible docs page so the orchestrator can promote each claim
to L2_source_verified.

After creation we run the orchestrator and embed each claim via the same
EmbeddingService the query engine uses, so semantic re-rank works the same
way on errors as on modules / patterns.
"""

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

ERRORS = [
    # ============================================================
    # Authentication / SSH (top of agent error traffic)
    # ============================================================
    (
        "ansible error: Authentication or permission failure",
        "Ansible cannot SSH into the target host. Common causes: (1) SSH key for ansible_user is not in ~/.ssh/authorized_keys on the target; "
        "(2) ansible_user doesn't exist on the target; (3) ssh_args in ansible.cfg conflicts. "
        "Fix: verify with `ssh -vvv <user>@<host>` from the controller. Add the public key with `ssh-copy-id <user>@<host>` or set "
        "ansible_ssh_private_key_file per host in inventory. If using become, ensure -K is passed or ansible_become_password is set.",
        "https://docs.ansible.com/ansible/latest/inventory_guide/connection_details.html",
    ),
    (
        "ansible error: Permission denied (publickey,password)",
        "SSH key authentication failed AND password auth is disabled or wrong. The target host expects a key the controller didn't send. "
        "Fix: check which key ansible is using with `ansible -m ping -vvv <host>` — look for the 'IdentityFile' line. "
        "Add the right key to ~/.ssh/authorized_keys on the target, or set ansible_ssh_private_key_file in inventory/host_vars to the correct .pem/.key path. "
        "On EC2, also verify the user matches the AMI default (ubuntu, ec2-user, admin).",
        "https://docs.ansible.com/ansible/latest/inventory_guide/connection_details.html",
    ),
    (
        "ansible error: Failed to connect to the host via ssh",
        "The SSH connection itself never opened — host unreachable, wrong hostname/IP, or firewall blocking port 22. "
        "Fix: (1) `ping <host>` to verify network reachability; (2) `nc -zv <host> 22` to confirm port 22 is open; "
        "(3) check ansible_host in inventory if the inventory hostname differs from the resolvable name; "
        "(4) if behind a bastion, set ansible_ssh_common_args with a ProxyJump directive.",
        "https://docs.ansible.com/ansible/latest/inventory_guide/connection_details.html",
    ),
    (
        "ansible error: Host key verification failed",
        "The target host's SSH host key doesn't match what's in the controller's ~/.ssh/known_hosts. "
        "Either the host was reinstalled, or it's a different machine at the same hostname/IP. "
        "Fix: (1) remove the stale entry with `ssh-keygen -R <host>`; (2) re-accept the new key with `ssh <user>@<host>` interactively once; "
        "(3) for ephemeral hosts (cloud instances), set host_key_checking = False in ansible.cfg or ANSIBLE_HOST_KEY_CHECKING=False env var (relaxes security — don't use in prod).",
        "https://docs.ansible.com/ansible/latest/reference_appendices/config.html",
    ),
    (
        "ansible error: sudo: a password is required",
        "Ansible's become (privilege escalation) requires a sudo password but none was provided. "
        "Fix: pass `-K` (or `--ask-become-pass`) on the command line for interactive prompt; OR "
        "set `ansible_become_password` per host (best practice: encrypt with ansible-vault); OR "
        "configure passwordless sudo on the target with `<user> ALL=(ALL) NOPASSWD: ALL` in /etc/sudoers.d/ — preferred for automation.",
        "https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_privilege_escalation.html",
    ),
    (
        "ansible error: Missing sudo password",
        "Identical root cause to `sudo: a password is required` — become is enabled but no password supplied. "
        "Fix: use -K, set ansible_become_password (vault-encrypted), or NOPASSWD in sudoers. "
        "If become is unnecessary for the task, remove `become: true` from the play or task.",
        "https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_privilege_escalation.html",
    ),
    # ============================================================
    # Python interpreter
    # ============================================================
    (
        "ansible error: ansible_python_interpreter not found",
        "Ansible cannot find a usable Python interpreter on the target host. Most modules require Python on the managed node. "
        "Fix: set `ansible_python_interpreter=/usr/bin/python3` explicitly in inventory, group_vars, or host_vars — pointing to the actual Python path on the target. "
        "Common locations: /usr/bin/python3 (most Linux), /usr/local/bin/python3 (BSD/macOS), /opt/homebrew/bin/python3 (Apple Silicon homebrew). "
        "Setting it explicitly also silences the auto-discovery deprecation warning.",
        "https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_python_version.html",
    ),
    (
        "ansible error: Failed to import the required Python library",
        "An ansible module needs a Python library on the TARGET host that isn't installed. "
        "Example: ansible.builtin.uri needs `python3-requests`; community.mysql.mysql_db needs `PyMySQL`. "
        "Fix: install the library on the managed node first using ansible.builtin.pip or the OS package manager. "
        "Run the failing module's docs page (e.g., ansible-doc ansible.builtin.uri) to see the exact `requirements:` section listing the needed libs.",
        "https://docs.ansible.com/ansible/latest/collections/ansible/builtin/pip_module.html",
    ),
    (
        "ansible error: Module yum requires python-yum-utils",
        "The ansible.builtin.yum module needs yum-utils installed on the managed node. "
        "Fix: install it first via raw module which bypasses the dependency: `ansible.builtin.raw: yum install -y yum-utils python3-dnf` — then yum module works. "
        "Better fix on modern RHEL: use ansible.builtin.dnf instead — it doesn't require yum-utils. "
        "For Ubuntu/Debian targets, use ansible.builtin.apt entirely (yum module is RHEL-family only).",
        "https://docs.ansible.com/ansible/latest/collections/ansible/builtin/yum_module.html",
    ),
    (
        "ansible warning: Discovered python: auto_silent",
        "Deprecation notice that Ansible's automatic Python interpreter discovery will go away. Not a failure but suppresses if you act. "
        "Fix: set `ansible_python_interpreter` explicitly per host or group. Once set, this warning disappears and your playbooks are deprecation-safe.",
        "https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_python_version.html",
    ),
    # ============================================================
    # Variables / templating / Jinja2
    # ============================================================
    (
        "ansible error: Variable X is undefined",
        "Ansible cannot resolve a variable referenced in a playbook, template, or `when` clause. Causes: typo, conditional definition skipped, vars file not loaded, wrong precedence. "
        "Fix: (1) use the default filter for optional vars: `{{ myvar | default('fallback') }}`; "
        "(2) for required vars, fail fast: `{{ myvar | mandatory }}`; "
        "(3) inspect what ansible actually sees with `ansible-inventory --host <host> --list` and `-vvv` on the failing task; "
        "(4) verify group_vars/host_vars file location and that the file is named correctly (group_vars/all/main.yml works; group_vars/all.yml also works).",
        "https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_variables.html",
    ),
    (
        "ansible error: AnsibleUndefinedVariable",
        "Internal exception thrown when a Jinja2 template references an undefined variable. Same root cause as `Variable X is undefined`. "
        "Fix: use default filter, mandatory filter, or check `is defined` in a `when` clause before referencing the variable. "
        "For nested attributes like `host.config.port`, use `host.config.port | default(omit)` to skip the parameter entirely.",
        "https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_filters.html",
    ),
    (
        "ansible error: Conditional check failed",
        "A `when:` expression couldn't be evaluated — usually a syntax issue, undefined variable in the expression, or type mismatch. "
        "Fix: (1) wrap string comparisons in quotes: `when: my_var == \"value\"`; "
        "(2) for variable existence checks use `when: my_var is defined`; "
        "(3) for boolean checks: `when: my_var | bool`. "
        "Run with -vvv to see the rendered Jinja2 expression before evaluation.",
        "https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_conditionals.html",
    ),
    (
        "ansible error: TypeError dict object has no attribute",
        "A Jinja2 expression tried to access a missing key on a dict, e.g. `{{ host.config.port }}` when `host.config` has no `port` key. "
        "Fix: use the default filter: `{{ host.config.port | default(8080) }}`; OR use `get`: `{{ host.config.get('port', 8080) }}`. "
        "For deeply nested optional structures, check existence with `is defined` first.",
        "https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_filters.html",
    ),
    # ============================================================
    # Inventory
    # ============================================================
    (
        "ansible error: Could not match supplied host pattern",
        "The hosts: clause or -l limit didn't match any host in your inventory. Causes: typo, host not in inventory, wrong group name. "
        "Fix: (1) `ansible-inventory --list` to dump what ansible actually sees; (2) `ansible-inventory --graph` for the group/host tree; "
        "(3) verify -i points at the right inventory file; (4) for dynamic inventory, check the plugin can authenticate (e.g., aws_ec2 needs AWS creds).",
        "https://docs.ansible.com/ansible/latest/inventory_guide/intro_patterns.html",
    ),
    (
        "ansible error: No hosts matched",
        "Playbook ran but no host selection matched. Often the same cause as `Could not match supplied host pattern` — host pattern in the play didn't intersect the inventory. "
        "Fix: check the `hosts:` line in the play, run `ansible-playbook --list-hosts <playbook>` to see what would run, and verify the inventory has those hosts via `ansible-inventory --list`.",
        "https://docs.ansible.com/ansible/latest/inventory_guide/intro_patterns.html",
    ),
    (
        "ansible error: no inventory was parsed only implicit localhost is available",
        "Ansible found no inventory file or all sources failed to parse. Causes: -i flag missing or pointing at non-existent file, inventory file has a syntax error. "
        "Fix: (1) explicitly pass -i <path> with a valid file; (2) check ansible.cfg inventory= setting; (3) for YAML inventory, validate with `yamllint` first; (4) for INI inventory, watch for trailing whitespace or duplicate group headers.",
        "https://docs.ansible.com/ansible/latest/inventory_guide/intro_inventory.html",
    ),
    # ============================================================
    # Modules / Galaxy / Collections
    # ============================================================
    (
        "ansible error: the role X was not found",
        "Ansible can't find a role you tried to use. Causes: role isn't installed, roles_path doesn't include where it lives, role name is misspelled. "
        "Fix: (1) install with `ansible-galaxy role install <role>`; (2) for project-local roles, ensure they live in `./roles/<role-name>/` relative to the playbook; "
        "(3) set roles_path in ansible.cfg if your roles directory is elsewhere: `roles_path = /etc/ansible/roles:./roles`.",
        "https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_reuse_roles.html",
    ),
    (
        "ansible error: Couldn't resolve module/action",
        "Ansible can't find a module by the name you used. Causes: module is in a collection you haven't installed, you used a short name when the collection requires FQCN, or the module name is misspelled. "
        "Fix: (1) use FQCN: `ansible.builtin.copy` instead of `copy`; (2) install missing collection: `ansible-galaxy collection install community.general`; "
        "(3) list available collections: `ansible-galaxy collection list`; (4) confirm the module exists: `ansible-doc <fqcn>`.",
        "https://docs.ansible.com/ansible/latest/collections_guide/collections_using_playbooks.html",
    ),
    (
        "ansible error: FQCN not found",
        "Same root as `Couldn't resolve module/action` — fully-qualified collection name doesn't resolve. The collection isn't installed in the controller's collections path. "
        "Fix: `ansible-galaxy collection install <namespace>.<collection>` (e.g. `ansible-galaxy collection install community.docker`). "
        "Pin versions in requirements.yml for reproducibility. Verify with `ansible-galaxy collection list`.",
        "https://docs.ansible.com/ansible/latest/collections_guide/collections_installing.html",
    ),
    (
        "ansible error: Collection X is not installed",
        "A playbook or role references a collection that isn't installed in any configured collections path. "
        "Fix: install with `ansible-galaxy collection install <namespace>.<collection>` (e.g. `ansible-galaxy collection install kubernetes.core`). "
        "For reproducible installs, declare in requirements.yml: `collections: [{name: kubernetes.core, version: '>=3.0.0'}]` and run `ansible-galaxy install -r requirements.yml`.",
        "https://docs.ansible.com/ansible/latest/collections_guide/collections_installing.html",
    ),
    # ============================================================
    # Vault
    # ============================================================
    (
        "ansible error: Attempting to decrypt but no vault secrets found",
        "A vault-encrypted file was found but ansible has no password to decrypt it. "
        "Fix: provide a password via one of: (1) --ask-vault-pass for interactive prompt; (2) --vault-password-file <path> to a script or plaintext file (chmod 600); "
        "(3) set ANSIBLE_VAULT_PASSWORD_FILE env var; (4) configure vault_password_file in ansible.cfg; "
        "(5) for multiple vault IDs, use --vault-id <label>@<source>.",
        "https://docs.ansible.com/ansible/latest/vault_guide/vault_managing_passwords.html",
    ),
    (
        "ansible error: ansible-vault decryption failed",
        "Vault decryption failed — usually wrong password, or a file from a different vault ID. "
        "Fix: (1) verify the password by trying `ansible-vault view <encrypted_file>` manually; (2) for multi-vault setups, check --vault-id labels match the encrypting label; "
        "(3) the file might not actually be vault-encrypted — `head -1 <file>` should start with `$ANSIBLE_VAULT;1.1;AES256`.",
        "https://docs.ansible.com/ansible/latest/vault_guide/vault_managing_passwords.html",
    ),
    (
        "ansible error: input not encrypted no ANSIBLE_VAULT header",
        "The file you tried to decrypt or rekey isn't actually vault-encrypted. Probably you ran ansible-vault on the wrong file or forgot to encrypt it first. "
        "Fix: (1) verify the file starts with `$ANSIBLE_VAULT;1.1;AES256` (run `head -1 <file>`); (2) if it's plaintext that should be encrypted, run `ansible-vault encrypt <file>`; "
        "(3) if you only need one variable encrypted, `ansible-vault encrypt_string` produces an inline vault block you can paste into vars files.",
        "https://docs.ansible.com/ansible/latest/vault_guide/vault_encrypting_content.html",
    ),
    # ============================================================
    # Connection / runtime
    # ============================================================
    (
        "ansible error: Connection timeout",
        "Ansible's SSH or other connection plugin hit a timeout. The target might be slow, overloaded, or behind a slow network. "
        "Fix: (1) increase ansible_connection_timeout in inventory/group_vars (default 10s); (2) for genuinely slow operations (large file copy, long shell command) use async/poll instead of synchronous; "
        "(3) check the target isn't paging or OOM-killing the SSH daemon; (4) for remote APIs (uri module), increase the module's timeout: parameter.",
        "https://docs.ansible.com/ansible/latest/reference_appendices/config.html",
    ),
    (
        "ansible error: task failed unreachable",
        "Ansible considers the host unreachable — connection couldn't be established at all (vs. the task failing for application-level reasons). "
        "Fix: (1) verify the host is up and SSH is listening (`ping <host>` and `nc -zv <host> 22`); "
        "(2) check ansible can connect: `ansible <host> -m ping`; "
        "(3) review ansible_host, ansible_user, ansible_ssh_private_key_file in inventory; "
        "(4) for SELinux/AppArmor issues, check audit.log on the target.",
        "https://docs.ansible.com/ansible/latest/network/user_guide/troubleshooting.html",
    ),
    (
        "ansible error: OSError errno 2 No such file or directory",
        "Ansible (or a module it called) couldn't find a file on disk. Could be on controller (template src, copy src, vars files) or target. "
        "Fix: (1) the error message names the missing path — verify it exists; (2) for copy/template module, src paths are relative to the playbook directory unless absolute; "
        "(3) use `stat` module first to check file existence and branch with `when:`; (4) for templates that pull in other files, check `lookup('file', ...)` paths.",
        "https://docs.ansible.com/ansible/latest/collections/ansible/builtin/stat_module.html",
    ),
    # ============================================================
    # Misc but high-traffic
    # ============================================================
    (
        "ansible error: Failed to lock apt for exclusive operation",
        "Two apt operations tried to run at once (one ansible-driven, one likely unattended-upgrades on the target). "
        "Fix: (1) disable or wait for unattended-upgrades before running ansible: `ansible.builtin.service: name=unattended-upgrades state=stopped` (then start after); "
        "(2) wait for the lock to release: `ansible.builtin.shell: while fuser /var/lib/dpkg/lock-frontend; do sleep 5; done`; "
        "(3) use update_cache + lock_timeout: `ansible.builtin.apt: update_cache=true lock_timeout=300`.",
        "https://docs.ansible.com/ansible/latest/collections/ansible/builtin/apt_module.html",
    ),
    (
        "ansible error: Could not find or access on the Ansible Controller",
        "A module that runs on the controller (template, copy with src on the controller, lookup) couldn't find a file at the path you gave. "
        "Fix: (1) verify the path exists relative to the playbook's directory (or absolute); "
        "(2) for templates, check `templates/` subdirectory of a role; "
        "(3) ansible.cfg's `roles_path` and `collections_path` affect where role-local files are found; "
        "(4) try absolute paths to isolate path-resolution issues, then convert back to relative once it works.",
        "https://docs.ansible.com/ansible/latest/collections/ansible/builtin/template_module.html",
    ),
    (
        "ansible error: Idempotency check failed task always reports changed",
        "A task is reporting changed=true on every run even when nothing should have changed. Usually a shell/command module with no `changed_when` clause, or a module being misused. "
        "Fix: (1) prefer a real ansible module over shell/command — modules are idempotent by design; "
        "(2) for unavoidable shell tasks, set `changed_when:` to a real condition (e.g., `changed_when: result.rc != 0 and 'already exists' not in result.stderr`); "
        "(3) use `creates:` or `removes:` to skip the task when the result already exists.",
        "https://docs.ansible.com/ansible/latest/collections/ansible/builtin/command_module.html",
    ),
    (
        "ansible error: Could not import collection",
        "An import_playbook or include_tasks tried to load a path that doesn't exist. "
        "Fix: (1) for `import_playbook:`, the path is relative to the importing playbook's directory; "
        "(2) for `include_tasks:`, the path is relative to the role's tasks/ or the playbook's directory; "
        "(3) for collection-namespaced playbook imports: `import_playbook: namespace.collection.playbook_name` requires the collection installed and the playbook in its `playbooks/` directory.",
        "https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_reuse.html",
    ),
]


def main() -> None:
    store = get_claim_store()
    orchestrator = CanonOrchestrator(store)
    svc = EmbeddingService.get_default()
    now = datetime.now(timezone.utc)
    ok = 0
    failed = 0
    for i, (subject, statement, evidence_url) in enumerate(ERRORS):
        suffix = f"{i:03d}"
        # Hash includes the subject so different errors get distinct hashes
        # even if the statement happens to be similar.
        body_for_hash = f"{subject}\n{statement}"
        evidence = Evidence(
            evidence_id=f"ev-ansible-error-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-ansible-error-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id="ansible-errors",
            submitted_by="ansible-errors-catalog",
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
        # Embed for semantic re-rank
        text = claim_embedding_text(subject=subject, statement=statement)
        vec = svc.embed_one(text)
        from app.services.embedding_service import serialize_embedding as _ser
        store.set_embedding(claim.claim_id, _ser(vec))
        ok += 1
        print(f"  ✓ #{suffix} {subject[:60]}")
    print(f"\nDone: {ok} created / {failed} failed")


if __name__ == "__main__":
    main()
