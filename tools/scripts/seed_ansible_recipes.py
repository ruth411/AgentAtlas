"""Direct-insert 20 ansible workflow recipes + 10 best-practice claims.

Like the errors catalog (tools/scripts/seed_ansible_errors.py), these are
synthesized claims that don't map to a single canonical docs page. Each
attaches OFFICIAL_DOCS evidence to the most relevant ansible docs page so
the orchestrator can promote it to L2_source_verified. Embeddings are
computed via the same EmbeddingService the query engine uses.

Recipes land under tool_id=ansible-recipes (new surface, allowlisted in
tool_trust_sources). Best practices land under ansible-playbook since
they're playbook-centric guidance.
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

# (tool_id, subject, statement, evidence_url)
CLAIMS = [
    # ====================================================================
    # WORKFLOW RECIPES — ansible-recipes
    # ====================================================================
    (
        "ansible-recipes",
        "ansible recipe: bootstrap fresh Ubuntu/Debian server",
        "Standard playbook to take a brand-new Ubuntu/Debian host from default install to ansible-managed. "
        "Steps: (1) `ansible.builtin.raw: apt-get update && apt-get install -y python3` (bypasses module's Python requirement); "
        "(2) `ansible.builtin.setup` to gather facts now that Python is available; "
        "(3) `ansible.builtin.user` to create the ansible service user with SSH key; "
        "(4) `ansible.builtin.authorized_key` to install your public key; "
        "(5) `community.general.sudoers` to grant passwordless sudo: `user: ansible, commands: ALL, nopassword: true`; "
        "(6) `ansible.builtin.apt` upgrade and install baseline packages (curl, git, unattended-upgrades); "
        "(7) `ansible.builtin.timezone` to set UTC; (8) `community.general.ufw` to enable firewall with default deny + allow SSH. "
        "After this play runs once, subsequent plays use ansible_user=<service user> with key-based auth.",
        "https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_intro.html",
    ),
    (
        "ansible-recipes",
        "ansible recipe: bootstrap fresh RHEL/CentOS/Rocky server",
        "RHEL-family bootstrap. Steps: (1) `ansible.builtin.raw: dnf install -y python3` if Python isn't installed; "
        "(2) gather_facts; (3) `ansible.builtin.user` + `ansible.builtin.authorized_key`; "
        "(4) `ansible.posix.sudoers`-equivalent: drop a file into /etc/sudoers.d/ with NOPASSWD; "
        "(5) `ansible.builtin.dnf` baseline packages (epel-release, vim, curl, git, python3-pip); "
        "(6) SELinux: leave enforcing in prod, use `ansible.posix.seboolean` for app-specific bool flips; "
        "(7) `ansible.posix.firewalld` to manage firewall instead of ufw; "
        "(8) `ansible.builtin.systemd` to enable+start sshd. "
        "For Amazon Linux 2/2023 the same pattern works; default user is `ec2-user` (not `ubuntu`).",
        "https://docs.ansible.com/ansible/latest/collections/ansible/posix/firewalld_module.html",
    ),
    (
        "ansible-recipes",
        "ansible recipe: deploy Django app",
        "Common Django deployment pattern. Steps: "
        "(1) `ansible.builtin.git` to pull the repo to /opt/<app>/current with version: master; "
        "(2) `ansible.builtin.pip` with requirements: /opt/<app>/current/requirements.txt virtualenv: /opt/<app>/venv; "
        "(3) `ansible.builtin.template` for production settings.py with database url + secret_key (vault-encrypted vars); "
        "(4) `ansible.builtin.command: ./manage.py migrate` with `args.creates` skipping if already done OR with `register` + `changed_when` checking migration output; "
        "(5) `ansible.builtin.command: ./manage.py collectstatic --noinput`; "
        "(6) `ansible.builtin.template` for the systemd unit file (gunicorn) and notify Restart gunicorn handler; "
        "(7) `ansible.builtin.template` for nginx site config + notify Reload nginx. "
        "Tag with `tags: [deploy]` to allow `--tags=deploy` for code-only updates without bootstrap.",
        "https://docs.ansible.com/ansible/latest/collections/ansible/builtin/git_module.html",
    ),
    (
        "ansible-recipes",
        "ansible recipe: rolling deployment with serial",
        "Zero-downtime deploy across a pool. Use `serial: <n>` on the play (or a percentage like `serial: 25%`) to limit how many hosts deploy concurrently. "
        "Combine with `max_fail_percentage: 10` so the whole rollout aborts if >10% of hosts in any batch fail. "
        "Inside the play: (1) remove the host from the load balancer (`community.general.haproxy: state=disabled`); "
        "(2) wait for in-flight requests to drain (`ansible.builtin.wait_for: timeout=30 state=stopped`); "
        "(3) deploy the new code + restart the app; "
        "(4) probe the app's health endpoint with `ansible.builtin.uri: url=http://localhost/health`; "
        "(5) re-add the host to the load balancer. "
        "Set `any_errors_fatal: true` to halt the entire play (across all batches) on the first error rather than continuing.",
        "https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_strategies.html",
    ),
    (
        "ansible-recipes",
        "ansible recipe: provision AWS EC2 fleet",
        "Spin up N EC2 instances and add them to the dynamic inventory. Steps: "
        "(1) `amazon.aws.ec2_vpc_net` to ensure VPC exists; (2) `amazon.aws.ec2_vpc_subnet` for the subnet; "
        "(3) `amazon.aws.ec2_security_group` allowing SSH from your bastion + app ports; "
        "(4) `amazon.aws.ec2_key` to upload your public key if not already; "
        "(5) `amazon.aws.ec2_instance` with image_id: ami-..., count: <n>, wait: true, tags: {Name: web, Group: webservers}; "
        "(6) `ansible.builtin.add_host` to add each instance to a runtime group OR rely on the `amazon.aws.aws_ec2` inventory plugin to pick them up next play. "
        "Use `ansible_python_interpreter=/usr/bin/python3` for Ubuntu AMIs; ec2-user for Amazon Linux.",
        "https://docs.ansible.com/ansible/latest/collections/amazon/aws/ec2_instance_module.html",
    ),
    (
        "ansible-recipes",
        "ansible recipe: install Docker on a target",
        "Standard Docker installation. Steps for Ubuntu: "
        "(1) `ansible.builtin.apt` install ca-certificates, curl, gnupg, lsb-release; "
        "(2) `ansible.builtin.get_url` to fetch Docker's GPG key to /etc/apt/keyrings/docker.gpg; "
        "(3) `ansible.builtin.apt_repository` adding `deb [signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu <distro> stable`; "
        "(4) `ansible.builtin.apt update_cache=true name=[docker-ce,docker-ce-cli,containerd.io,docker-buildx-plugin,docker-compose-plugin]`; "
        "(5) `ansible.builtin.user name={{ ansible_user }} groups=docker append=true` so the user can run docker without sudo (note: requires logout/login); "
        "(6) `ansible.builtin.systemd name=docker enabled=true state=started`. "
        "For RHEL-family, swap apt steps for dnf + the CentOS/RHEL docker repo.",
        "https://docs.ansible.com/ansible/latest/collections/ansible/builtin/apt_repository_module.html",
    ),
    (
        "ansible-recipes",
        "ansible recipe: configure firewall (ufw / firewalld)",
        "Lock down network ingress. Ubuntu (ufw): "
        "(1) `community.general.ufw rule=allow port=22 proto=tcp`; "
        "(2) `community.general.ufw rule=allow port=80 proto=tcp`; "
        "(3) `community.general.ufw rule=allow port=443 proto=tcp`; "
        "(4) `community.general.ufw state=enabled policy=deny direction=incoming`. "
        "RHEL (firewalld): use `ansible.posix.firewalld zone=public service=ssh state=enabled permanent=true immediate=true` for each service, then "
        "`ansible.posix.firewalld zone=public state=enabled permanent=true`. "
        "For dynamic IP allowlists (e.g., team office IPs), loop the rule task with `loop:` over a vars file maintained in ansible-vault.",
        "https://docs.ansible.com/ansible/latest/collections/ansible/posix/firewalld_module.html",
    ),
    (
        "ansible-recipes",
        "ansible recipe: Let's Encrypt certificate issuance",
        "Issue a TLS cert via ACME (Let's Encrypt). Steps: "
        "(1) `community.crypto.openssl_privatekey` to generate the account key + cert key; "
        "(2) `community.crypto.openssl_csr_pipe` to build a CSR for the domain; "
        "(3) `community.crypto.acme_certificate` with acme_directory: https://acme-v02.api.letsencrypt.org/directory, challenge: http-01, account_key_src: ... — this returns a challenge file to drop into the webroot; "
        "(4) `ansible.builtin.copy` the challenge data into /var/www/html/.well-known/acme-challenge/; "
        "(5) re-run `community.crypto.acme_certificate` to complete the challenge — produces the signed cert; "
        "(6) restart nginx/haproxy to pick up the new cert. "
        "Renewal is the same playbook with renewal_threshold_days: 30; ansible re-runs only when within 30 days of expiry.",
        "https://docs.ansible.com/ansible/latest/collections/community/crypto/acme_certificate_module.html",
    ),
    (
        "ansible-recipes",
        "ansible recipe: rolling kernel patch + reboot",
        "Apply kernel updates and reboot the fleet without downtime. With serial: 1 (or a small percentage), per host: "
        "(1) `ansible.builtin.dnf name='*' state=latest update_only=true` (or apt upgrade); "
        "(2) `ansible.builtin.reboot reboot_timeout=600 connect_timeout=20 post_reboot_delay=30`; "
        "(3) `ansible.builtin.wait_for_connection timeout=300` to confirm host is back; "
        "(4) `ansible.builtin.command: uname -r register: kver` then `ansible.builtin.debug var=kver.stdout` to log the new kernel; "
        "(5) probe a health endpoint with `ansible.builtin.uri url=http://localhost/healthz` before moving to the next host. "
        "Wrap in a block with `delegate_to: localhost` for LB drain/restore around each host.",
        "https://docs.ansible.com/ansible/latest/collections/ansible/builtin/reboot_module.html",
    ),
    (
        "ansible-recipes",
        "ansible recipe: multi-environment deployment (dev/staging/prod)",
        "Standard layout to deploy the same code to different environments cleanly. Directory structure: "
        "inventories/{dev,staging,prod}/{hosts.yml,group_vars/all.yml,group_vars/web.yml,host_vars/...}. "
        "Run with `-i inventories/staging/hosts.yml` to target a specific env. "
        "group_vars/all.yml per env holds env-specific values (db_host, secret_key with vault, api_url). "
        "Common playbook: `ansible-playbook -i inventories/staging/hosts.yml site.yml --tags deploy`. "
        "Use `ansible-vault encrypt_string` to inline-encrypt secrets per env — group_vars/staging/all.yml has the staging secret, prod has the prod secret. "
        "Same vault_password_file or different password per env (--vault-id staging@~/.vault_pass.staging --vault-id prod@~/.vault_pass.prod).",
        "https://docs.ansible.com/ansible/latest/inventory_guide/intro_inventory.html",
    ),
    (
        "ansible-recipes",
        "ansible recipe: database migration with check_mode preview",
        "Preview the changes a migration would make before applying. Run with `--check --diff` first: "
        "`ansible-playbook deploy.yml --tags migrate --check --diff`. This shows what each task would change without making it real. "
        "For migrations specifically: (1) `community.postgresql.postgresql_query` with check_mode-aware queries (SELECT-only is safe); "
        "(2) `ansible.builtin.command: ./manage.py migrate --plan` to show pending migrations — works in check mode; "
        "(3) re-run without --check once the diff looks right. "
        "For irreversible operations wrap in a block with `when: not ansible_check_mode` to skip them in check mode.",
        "https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_checkmode.html",
    ),
    (
        "ansible-recipes",
        "ansible recipe: setup HAProxy load balancer",
        "Configure HAProxy as the LB in front of an app pool. Steps: "
        "(1) `ansible.builtin.apt name=haproxy` (or dnf); "
        "(2) `ansible.builtin.template src=haproxy.cfg.j2 dest=/etc/haproxy/haproxy.cfg validate='haproxy -c -f %s'` — the validate flag fails the task on syntax errors before writing; "
        "(3) the template iterates over groups['webservers'] to emit one `server <name> <addr>:80 check` line per host; "
        "(4) `ansible.builtin.systemd name=haproxy enabled=true state=reloaded` on config change (use reload not restart to keep existing connections). "
        "For dynamic enable/disable in rolling deploys, use `community.general.haproxy: state=disabled backend=webservers host={{ inventory_hostname }}`.",
        "https://docs.ansible.com/ansible/latest/collections/ansible/builtin/template_module.html",
    ),
    (
        "ansible-recipes",
        "ansible recipe: backup MySQL/Postgres to S3",
        "Periodic database dump + S3 upload. Steps: "
        "(1) `community.mysql.mysql_db state=dump name=mydb target=/tmp/mydb-{{ ansible_date_time.iso8601_basic_short }}.sql` (or `community.postgresql.postgresql_db` for Postgres); "
        "(2) `ansible.builtin.command: gzip /tmp/mydb-*.sql`; "
        "(3) `amazon.aws.s3_object mode=put bucket=my-backups object='db/{{ inventory_hostname }}/mydb-...sql.gz' src=/tmp/mydb-...sql.gz`; "
        "(4) `ansible.builtin.file path=/tmp/mydb-...sql.gz state=absent` to clean up local; "
        "(5) tag with `tags: [backup]` and schedule via cron with `community.general.cron special_time=daily job='ansible-playbook backup.yml --tags backup'`. "
        "Restore: pull from S3, gunzip, mysql_db with state=import.",
        "https://docs.ansible.com/ansible/latest/collections/amazon/aws/s3_object_module.html",
    ),
    (
        "ansible-recipes",
        "ansible recipe: bootstrap Kubernetes node with kubeadm",
        "Join a worker to a kubeadm cluster. Steps: "
        "(1) `ansible.builtin.systemd name=swap.target masked=true` + `ansible.builtin.command: swapoff -a` (kubelet refuses to start with swap on); "
        "(2) install containerd via apt/dnf with the docker repo; "
        "(3) add the kubernetes apt/yum repo and install kubeadm, kubelet, kubectl (versioned to match the control plane); "
        "(4) `ansible.builtin.copy` a kubeadm config file with the cluster's discovery token + ca-cert-hash; "
        "(5) `ansible.builtin.command: kubeadm join <control-plane>:6443 --token <token> --discovery-token-ca-cert-hash <hash>` with `creates: /etc/kubernetes/kubelet.conf` so it skips on re-run; "
        "(6) verify the node appears in `kubectl get nodes` from the control plane (delegate_to: localhost or a kube-control host).",
        "https://docs.ansible.com/ansible/latest/collections/kubernetes/core/k8s_module.html",
    ),
    (
        "ansible-recipes",
        "ansible recipe: set up nginx reverse proxy with TLS",
        "Front-door nginx for an upstream app. Steps: "
        "(1) `ansible.builtin.apt name=nginx`; "
        "(2) issue/install a cert via the Let's Encrypt recipe; "
        "(3) `ansible.builtin.template src=nginx-site.j2 dest=/etc/nginx/sites-available/{{ domain }} validate='nginx -t -c %s'` — config has upstream block + ssl_certificate paths; "
        "(4) `ansible.builtin.file src=/etc/nginx/sites-available/{{ domain }} dest=/etc/nginx/sites-enabled/{{ domain }} state=link`; "
        "(5) `ansible.builtin.file path=/etc/nginx/sites-enabled/default state=absent` to remove the default; "
        "(6) `ansible.builtin.systemd name=nginx enabled=true state=reloaded` (reload not restart). "
        "Use a handler for the reload so the task is only triggered when the template actually changes.",
        "https://docs.ansible.com/ansible/latest/collections/ansible/builtin/template_module.html",
    ),
    (
        "ansible-recipes",
        "ansible recipe: install Prometheus node_exporter",
        "Stand up Prometheus monitoring on every host in the fleet. Steps: "
        "(1) `ansible.builtin.user name=node_exporter system=true shell=/usr/sbin/nologin`; "
        "(2) `ansible.builtin.get_url url='https://github.com/prometheus/node_exporter/releases/download/v1.7.0/node_exporter-1.7.0.linux-amd64.tar.gz' dest=/tmp/node_exporter.tgz checksum=sha256:...`; "
        "(3) `ansible.builtin.unarchive src=/tmp/node_exporter.tgz dest=/opt remote_src=true`; "
        "(4) `ansible.builtin.copy src=/opt/node_exporter-1.7.0.linux-amd64/node_exporter dest=/usr/local/bin/node_exporter mode=0755 owner=node_exporter`; "
        "(5) `ansible.builtin.template src=node_exporter.service.j2 dest=/etc/systemd/system/node_exporter.service` (systemd unit pointing at /usr/local/bin/node_exporter); "
        "(6) `ansible.builtin.systemd daemon_reload=true name=node_exporter enabled=true state=started`; "
        "(7) firewall: allow port 9100 from the Prometheus server's IP only.",
        "https://docs.ansible.com/ansible/latest/collections/ansible/builtin/get_url_module.html",
    ),
    (
        "ansible-recipes",
        "ansible recipe: LDAP-backed user management",
        "Sync local user accounts with an LDAP directory. Steps: "
        "(1) `ansible.builtin.apt name=[libnss-ldap,libpam-ldap,nscd]` (or `[nss-pam-ldapd,nscd]` on RHEL); "
        "(2) `ansible.builtin.template src=ldap.conf.j2 dest=/etc/ldap.conf` with `uri ldap://ldap.example.com base dc=example,dc=com binddn=cn=ansible,dc=...`; "
        "(3) `community.general.ldap_attrs` to manage the cn=ansible bind user's attributes if needed; "
        "(4) `ansible.builtin.lineinfile path=/etc/nsswitch.conf regexp='^passwd:' line='passwd: files ldap'` and similar for group/shadow; "
        "(5) `ansible.builtin.systemd name=nscd state=restarted` to refresh the cache; "
        "(6) verify with `getent passwd <ldap-user>`. "
        "For ad-hoc per-host overrides, keep local users in /etc/passwd via ansible.builtin.user — local users take precedence in nsswitch order.",
        "https://docs.ansible.com/ansible/latest/collections/community/general/ldap_attrs_module.html",
    ),
    (
        "ansible-recipes",
        "ansible recipe: secret rotation with ansible-vault",
        "Rotate a vault-encrypted credential. Steps: "
        "(1) decrypt the existing vars file: `ansible-vault decrypt group_vars/all/secrets.yml`; "
        "(2) edit the file to replace the old secret with the new one; "
        "(3) re-encrypt: `ansible-vault encrypt group_vars/all/secrets.yml`; "
        "(4) commit the change to git (the encrypted blob is safe to commit); "
        "(5) re-run the deploy playbook with `--tags=secrets` (or whatever tag the relevant tasks carry) to push the new credential to every host; "
        "(6) if you also rotated the vault password itself, run `ansible-vault rekey group_vars/all/secrets.yml` and distribute the new vault password file to operators out-of-band. "
        "For per-secret rotation without touching unrelated values, use ansible-vault encrypt_string on a single inline value instead of a whole file.",
        "https://docs.ansible.com/ansible/latest/vault_guide/vault_managing_passwords.html",
    ),
    (
        "ansible-recipes",
        "ansible recipe: deploy Node.js app with PM2",
        "Deploy a Node.js application managed by PM2. Steps: "
        "(1) `ansible.builtin.apt name=nodejs` (or get a specific version from nodesource); "
        "(2) `community.general.npm name=pm2 global=true`; "
        "(3) `ansible.builtin.git repo=https://... dest=/opt/<app>/current version=master force=true`; "
        "(4) `community.general.npm path=/opt/<app>/current production=true`; "
        "(5) `ansible.builtin.template src=ecosystem.config.js.j2 dest=/opt/<app>/ecosystem.config.js` (PM2 process file with env vars + script path); "
        "(6) `ansible.builtin.command: pm2 reload /opt/<app>/ecosystem.config.js --update-env` — reload is graceful (no dropped connections); "
        "(7) `ansible.builtin.command: pm2 save` to persist for reboot; "
        "(8) on the first deploy only, `ansible.builtin.command: pm2 startup systemd -u <user>` to install the systemd unit that runs PM2 on boot.",
        "https://docs.ansible.com/ansible/latest/collections/community/general/npm_module.html",
    ),
    (
        "ansible-recipes",
        "ansible recipe: patch management with reporting",
        "Apply security updates and report results back. Steps: "
        "(1) Pre-task: `ansible.builtin.dnf list updates security=true register: pending_security`; "
        "(2) `ansible.builtin.debug var=pending_security.results` to log what's about to change; "
        "(3) `ansible.builtin.dnf security=true state=latest update_only=true register: dnf_result`; "
        "(4) `ansible.builtin.reboot` if `dnf_result.changed and 'kernel' in dnf_result.results | join(' ')` (only reboot when kernel changed); "
        "(5) Post-task: `ansible.builtin.uri url=http://your-reporting.example.com/patch-runs method=POST body_format=json body='{\"host\":\"{{ inventory_hostname }}\",\"packages\":{{ dnf_result.results }}}'` to log the run centrally. "
        "Schedule via cron at off-hours; gate with serial: 5 + max_fail_percentage: 0 for safety.",
        "https://docs.ansible.com/ansible/latest/collections/ansible/builtin/dnf_module.html",
    ),

    # ====================================================================
    # BEST PRACTICES — ansible-playbook
    # ====================================================================
    (
        "ansible-playbook",
        "ansible best practice: production directory layout",
        "Recommended directory layout for production ansible repos: "
        "ansible.cfg at repo root (with collections_paths, roles_path, inventory, vault_password_file); "
        "inventories/{prod,staging,dev}/ each containing hosts.yml + group_vars/ + host_vars/; "
        "roles/ for project-local roles (one directory per role, with tasks/, handlers/, templates/, files/, vars/, defaults/, meta/); "
        "playbooks/site.yml as the main entry point, with imports for sub-playbooks like webservers.yml, dbservers.yml; "
        "requirements.yml for collections + roles you depend on; "
        ".ansible-lint and yamllint configs for CI; "
        "vault password files OUTSIDE the repo (in ~/.vault_pass or via a script in vault_password_file). "
        "Avoid: roles inside playbooks/ (collisions), inventories at the top level (no environment separation), secrets in plaintext.",
        "https://docs.ansible.com/ansible/latest/tips_tricks/sample_setup.html",
    ),
    (
        "ansible-playbook",
        "ansible best practice: prefer modules over shell",
        "Always use a real ansible module when one exists; only fall back to shell/command modules when no module covers the operation. "
        "Why: modules are idempotent by design (they check current state and skip if no change is needed), they handle changed_when correctly, they return structured data you can register and branch on, "
        "and they're cross-platform where shells aren't. "
        "If you MUST use shell (e.g., a complex pipeline or a custom binary), always set: "
        "(1) `creates:` or `removes:` so the task skips when the result already exists; "
        "(2) `changed_when:` to express the real change condition (not just non-zero exit code); "
        "(3) `register:` the output and inspect it in a `when:` on downstream tasks. "
        "Anti-pattern: `command: 'systemctl restart nginx'` (use ansible.builtin.systemd with state=restarted instead — handles disabled units, returns proper changed=true/false).",
        "https://docs.ansible.com/ansible/latest/collections/ansible/builtin/command_module.html",
    ),
    (
        "ansible-playbook",
        "ansible best practice: always use FQCN (fully-qualified collection name)",
        "Reference every module by its full namespace.collection.module name, e.g. `ansible.builtin.copy` instead of `copy`. "
        "Why: (1) explicit about which collection provides the module — no surprises if a third-party collection adds a module with the same short name; "
        "(2) future-proof: modules occasionally migrate between collections (community.general → community.X); FQCN keeps your playbook working; "
        "(3) ansible-lint warns on short names. "
        "Exception: the `collections:` keyword on a play can declare a search list, allowing short names for THAT play only. Avoid this — it makes the play less portable. "
        "When in doubt: `ansible-doc <fqcn>` to verify the module exists and learn its parameters.",
        "https://docs.ansible.com/ansible/latest/collections_guide/collections_using_playbooks.html",
    ),
    (
        "ansible-playbook",
        "ansible best practice: idempotent task design",
        "Design every task so re-running the playbook is a no-op when nothing has changed. "
        "Markers of idempotent design: "
        "(1) tasks report changed=true on the first run and changed=false on subsequent runs; "
        "(2) use module parameters like state=present/absent/started/stopped rather than imperative commands; "
        "(3) for unavoidable shell tasks, set creates:/removes: or changed_when: to a real condition (not exit code); "
        "(4) use `ansible.builtin.lineinfile` (idempotent) over `echo >> file` (not); "
        "(5) for downloads, use `ansible.builtin.get_url` with checksum: parameter — re-downloads only if the file's hash doesn't match. "
        "Anti-pattern: `command: 'echo hello >> /etc/motd'` (always reports changed; pollutes the file on every run). "
        "Test: run your playbook twice — the second run should produce 0 changed tasks.",
        "https://docs.ansible.com/ansible/latest/collections/ansible/builtin/command_module.html",
    ),
    (
        "ansible-playbook",
        "ansible best practice: variable layering strategy",
        "Ansible has 22 levels of variable precedence. Practical layering: "
        "(1) `roles/<role>/defaults/main.yml` — lowest priority, the role's sensible defaults. Override-friendly. "
        "(2) `inventories/<env>/group_vars/all/main.yml` — env-wide defaults (e.g., dns_server, log_level). "
        "(3) `inventories/<env>/group_vars/<group>/main.yml` — per-group settings (web servers' nginx_worker_processes). "
        "(4) `inventories/<env>/host_vars/<host>/main.yml` — per-host overrides (rare; usually a sign of inventory smell). "
        "(5) `--extra-vars` (CLI) — highest priority, for one-off overrides or CI pipelines. "
        "Never put production secrets in defaults/main.yml (committed to git). Use ansible-vault for secrets, keep them in inventories/<env>/group_vars/<group>/vault.yml. "
        "Debug with `ansible-inventory --host <host> --list` to see the final merged variable set.",
        "https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_variables.html",
    ),
    (
        "ansible-playbook",
        "ansible best practice: tag strategy",
        "Use tags to enable selective playbook runs: `ansible-playbook site.yml --tags deploy` runs only tasks/blocks/plays tagged `deploy`. "
        "Useful tag conventions: `bootstrap` (one-time setup like users + sudo), `deploy` (code + restart, the common one), `config` (config-only changes), `data` (db migrations, expensive), `monitoring` (observability stack). "
        "Use `--skip-tags=<tag>` to omit specific work (`--skip-tags=monitoring` for a fast deploy). "
        "Special tag `always` runs even when other tags are specified — use for facts gathering or pre-tasks that always need to happen. "
        "Special tag `never` skips by default — use for destructive ops like `db_destroy`; only runs when `--tags=never,db_destroy` explicitly. "
        "Anti-pattern: untagged tasks in a tagged playbook — they only run when no --tags is passed, which is surprising. Tag everything explicitly.",
        "https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_tags.html",
    ),
    (
        "ansible-playbook",
        "ansible best practice: secrets management with ansible-vault",
        "Encrypt every secret. Recommended pattern: "
        "(1) Per-environment vault files: `inventories/prod/group_vars/all/vault.yml` (encrypted) holds all prod secrets as `vault_<varname>: <value>`. "
        "(2) Reference them in plaintext files: `inventories/prod/group_vars/all/main.yml` has `my_secret: '{{ vault_my_secret }}'` — this lets ansible-lint and grep work on variable names without exposing the encrypted blob. "
        "(3) Vault password file outside the repo: `~/.vault_pass.prod` (chmod 600), referenced via `vault_password_file = ~/.vault_pass.prod` in ansible.cfg OR `--vault-password-file ~/.vault_pass.prod` per invocation. "
        "(4) For per-secret rotation without changing the rest, use `ansible-vault encrypt_string '<value>' --name '<var>'` to generate an inline vault block to paste into a plaintext vars file. "
        "(5) Multi-vault setups: `--vault-id prod@~/.vault_pass.prod --vault-id staging@~/.vault_pass.staging` lets one playbook decrypt vars from different environments.",
        "https://docs.ansible.com/ansible/latest/vault_guide/vault_managing_passwords.html",
    ),
    (
        "ansible-playbook",
        "ansible best practice: testing with molecule",
        "Test individual roles with `molecule`. Setup: `pip install molecule molecule-plugins[docker]` then `cd roles/<role> && molecule init scenario`. "
        "A scenario lives in `roles/<role>/molecule/<scenario>/` with: "
        "(1) `molecule.yml` declaring the driver (docker, ec2, vagrant) and the test platforms (Ubuntu 22.04, RHEL 9, etc.); "
        "(2) `converge.yml` — a playbook that applies the role under test; "
        "(3) `verify.yml` — assertions about the post-converge state (file exists, service running, port listening). "
        "Run `molecule test` to: create the container, converge, check idempotence (converge a second time and assert 0 changed), verify, destroy. "
        "For collections, use `ansible-test` instead of molecule — it integrates with the collection's CI: `ansible-test sanity`, `ansible-test units`, `ansible-test integration`.",
        "https://docs.ansible.com/ansible/latest/dev_guide/testing.html",
    ),
    (
        "ansible-playbook",
        "ansible best practice: connection performance tuning",
        "Speed up large fleets with three knobs: "
        "(1) `pipelining = true` in ansible.cfg [ssh_connection] — fewer SSH connections per task (~3x speedup); requires sudo without 'requiretty' on targets. "
        "(2) `forks = 50` (or higher) in ansible.cfg — process this many hosts in parallel. Default 5 is conservative; for 100+ hosts increase to 20-50. "
        "(3) `ControlMaster=auto ControlPersist=60s` in [ssh_connection] ssh_args — reuses one SSH connection across multiple tasks per host (~2x speedup beyond pipelining). "
        "Additionally: enable fact caching with `fact_caching = jsonfile fact_caching_connection = /tmp/ansible_facts` so gather_facts doesn't re-run every play. "
        "Use `gather_facts: false` on plays that don't need facts (e.g., a play that only runs a single shell command).",
        "https://docs.ansible.com/ansible/latest/playbook_guide/playbooks_strategies.html",
    ),
    (
        "ansible-playbook",
        "ansible best practice: inventory organization",
        "Keep inventories versioned, environment-separated, and self-documenting. "
        "Directory pattern: `inventories/<env>/{hosts.yml,group_vars/,host_vars/}`. "
        "hosts.yml uses YAML inventory format (not INI) — supports nested groups, type-safe vars, and is easier to diff. "
        "Define groups by ROLE not by physical attributes: `web`, `db`, `cache` — not `aws_us_east_1`. Cloud locality is metadata, not the primary group. "
        "Use group_vars/<group>/main.yml for shared values; group_vars/<group>/vault.yml for shared secrets. "
        "For dynamic environments (cloud autoscale groups), use an inventory plugin (amazon.aws.aws_ec2, azure.azcollection.azure_rm) alongside the static file. "
        "Verify with `ansible-inventory -i inventories/prod --graph` (shows hosts + groups) and `ansible-inventory -i inventories/prod --host <host> --list` (shows final merged vars for one host).",
        "https://docs.ansible.com/ansible/latest/inventory_guide/intro_inventory.html",
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
            evidence_id=f"ev-ansible-recipe-{suffix}",
            evidence_type=EvidenceType.OFFICIAL_DOCS,
            source_uri=evidence_url,
            excerpt=statement[:500],
            hash=f"sha256:{sha256(body_for_hash.encode()).hexdigest()}",
            captured_at=now,
            trust_level=TrustLevel.HIGH,
        )
        claim = KnowledgeClaim(
            claim_id=f"claim-ansible-recipe-{suffix}",
            claim_type=ClaimType.CLI_COMMAND_EXISTS,
            subject=subject,
            statement=statement,
            tool_id=tool_id,
            submitted_by="ansible-recipes-catalog",
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
