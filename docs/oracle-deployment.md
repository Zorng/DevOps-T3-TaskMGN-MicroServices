# Oracle Cloud deployment (issue #32)

Status: The VM is provisioned with Terraform from `infra/terraform/`. The application stack is deployed and externally reachable through HTTPS. Several deployment checks have passed. Remaining work is listed under [Not done yet](#not-done-yet).

## Topology

| Item | Value |
|---|---|
| VM | One `VM.Standard.A1.Flex` (Arm / aarch64), 2 OCPU, 12 GB memory |
| Operating system | Ubuntu 24.04, 100 GB boot volume |
| Network | One VCN and public subnet. Public ports: 80 and 443. SSH (22): restricted to `admin_cidr`. No other application ports are publicly exposed |
| HTTPS | Caddy container on the VM obtains and renews a Let's Encrypt certificate and forwards requests to the gateway on `127.0.0.1:8080` |
| Application | Stack from `infra/compose.yaml`. Only the gateway publishes a host port, bound to `127.0.0.1`. Databases, broker and application services remain on private Docker networks |
| Persistent storage | Named Docker volumes for RabbitMQ and all three PostgreSQL databases. Caddy also uses persistent configuration and certificate volumes |
| Secrets | Generated on the VM by `infra/scripts/setup_env.py` into `infra/.env` and `infra/.secrets/`. Never commit these files or Terraform state |

The VM uses the `VM.Standard.A1.Flex` Arm shape with 2 OCPU, 12 GB memory and a 100 GB boot volume. The team initially encountered an A1 Flex out-of-host-capacity error. The team leader upgraded the Oracle tenancy to Pay As You Go to address the capacity blocker. The account is now Pay As You Go; monitor actual charges through Oracle Cost Analysis. Do not assume the deployment is cost-free or that all tenancy resources qualify for Always Free allowances.

## Decisions recorded

| Decision | Choice | Notes |
|---|---|---|
| Provisioning method | Terraform, OCI provider, local state | State and `terraform.tfvars` must remain ignored by Git |
| VM count | 1 | The Compose deployment is single-host. A split deployment would require encrypted traffic between hosts |
| Region | Tenancy's home region | The region is configured in `terraform.tfvars` |
| HTTPS | Caddy on the VM | Uses a domain pointing to the VM's public IP |
| Administrator access | SSH keys only, restricted source address | See the deployment workflow note below |

## Create, update, stop, recover

**Create** (from `infra/terraform/`):

```bash
terraform init
terraform plan
terraform apply
```

Then create a DNS A record for `domain_name` pointing to the `public_ip` output. The bootstrap log is `/var/log/taskmgn-bootstrap.log` on the VM and should end with `bootstrap finished`.

**First start** on the VM, from `/opt/taskmgn`:

1. Put the public Google client ID in `infra/.env` (`IDENTITY_GOOGLE_CLIENT_ID=`).
2. Log in to GHCR with a read-only package token if using published images.
3. Deploy tested images using the deployment instructions in `infra/README.md`.

**Update:** Use `infra/scripts/deploy.sh deploy` one service at a time. Use `deploy.sh rollback` to return to the previous image.

**Stop:** Run:

```bash
docker compose -f infra/compose.yaml down
```

This keeps named volumes and their data. Stopping the VM in the Oracle console keeps its boot volume.

**Back up before destructive operations:**

```bash
python3 infra/scripts/backup_restore.py backup
```

Copy `infra/.backups/` off the VM.

**Recover:** Terminating the VM or running `terraform destroy` deletes the boot volume and its data. To rebuild, run `terraform apply`, update DNS if the public IP changes, restore secrets and follow the restore instructions in `infra/README.md`.

## Deployment verification

The following checks were performed on the deployed VM.

| Check | Result | Evidence |
|---|---|---|
| Application containers | Passed | Gateway, identity, notification, task, databases and broker started |
| Database migrations | Passed | Identity, notification and task migration containers exited successfully |
| Gateway health | Passed | Local `/healthz` returned HTTP 200 |
| Public HTTPS | Passed | `https://taskmgn-cadt.duckdns.org/healthz` returned HTTP 200 |
| HTTP redirect | Passed | HTTP request returned 308 redirect to HTTPS |
| TLS certificate | Passed | Caddy successfully obtained a Let's Encrypt certificate |
| Gateway exposure | Passed | Docker inspection confirmed `127.0.0.1:8080` |
| Database and broker exposure | Passed | No published host ports for PostgreSQL or RabbitMQ |
| Persistent volume mounts | Passed | All four application data volumes were attached to their respective containers |
| Database restart | Passed | Task database returned to `running healthy` after restart |
| Public API routing | Passed | Task API request reached the application and returned `401 AUTH_REQUIRED`, as expected without authentication |
| ARM64 compatibility | Passed | All deployed application, database, broker and broker-init images verified as linux/arm64 or linux/arm64/v8 |
| External port isolation | Passed | From an external Mac, ports 80 and 443 connected; ports 8080, 5432 and 5672 timed out |
| Backup and restore drill | Passed | Oracle VM drill completed on 28 November 2026: 53 checks passed, 0 failed; throw-away containers removed |
| Production checkout | Passed | Fast-forwarded to 06a619d; main matches origin/main and working tree is clean |

The current deployment runs on one VM. Service, database and broker communication uses private Docker networks on that host. No inter-host transport is currently required. If services are moved to separate hosts, encrypted transport must be configured and verified.

External checks:

```bash
curl -i https://taskmgn-cadt.duckdns.org/healthz
curl -I http://taskmgn-cadt.duckdns.org/healthz
```

The Task API routing check:

```bash
curl -i https://taskmgn-cadt.duckdns.org/api/v1/workspaces/00000000-0000-4000-8000-000000000003/tasks
```

Expected without authentication: `401 AUTH_REQUIRED`.

Private port checks should also be performed from an external computer:

```bash
nc -zv -w 3 <public_ip> 8080
nc -zv -w 3 <public_ip> 5432
nc -zv -w 3 <public_ip> 5672
nc -zv -w 3 <public_ip> 15672
```

These connections should fail.

## Not done yet

- **Frontend origin / CORS:** The frontend origin is not yet configured. Add the actual deployed frontend URL to `frontend_origin` and the relevant application configuration once provided by the team. Browser-based frontend integration remains pending.

- **Full persistence verification:** Persistent volume mounts have been verified for all three databases and RabbitMQ. The Task database container restarted successfully. A complete restart test covering every database and RabbitMQ, followed by application data verification, is still pending.

- **Backup and restore drill:** Completed on the Oracle VM on 28 November 2026. The drill passed 53 checks with 0 failures. Restore verification used throw-away containers; live containers were only read. A full recovery into a replacement or live VM has not been rehearsed. Scheduled backups and off-VM backup copies are also not configured.

- **Architecture build compatibility:** Verified on the deployed Oracle VM. All deployed application, database, broker and broker-init images support ARM64 (`linux/arm64` or `linux/arm64/v8`).

- **Deployment workflow:** The Oracle VM is provisioned and running. The production checkout is synchronized with `main` at `06a619d`. The GitHub deployment workflow connects over SSH, while `admin_cidr` restricts SSH to an approved source address. The team must still decide and verify how automated deployment access should work.

- **Rate limits behind HTTPS:** The gateway must correctly handle forwarded client addresses from Caddy so all visitors are not treated as one client. This still needs verification.

- **Production integration:** Public HTTPS, gateway health and unauthenticated API routing have been verified. The API returned `401 AUTH_REQUIRED` for an unauthenticated request as expected. Authenticated end-to-end workflows still need testing with valid credentials and real application data.

- **Secrets and team prerequisites:** The Google client ID, package access token and frontend URL must be supplied or configured by the team where required. Never commit credentials or generated secrets.

- **Oracle account and capacity:** The team initially encountered an A1 Flex out-of-host-capacity error. The team leader upgraded the tenancy to Pay As You Go. The deployed VM uses `VM.Standard.A1.Flex` with 2 OCPU, 12 GB RAM and a 100 GB boot volume. Monitor actual charges through Oracle Cost Analysis; do not assume the deployment is cost-free.

**Important:** Terraform currently reports no infrastructure changes. Do not apply or recreate the VM just to complete these application verification checks.