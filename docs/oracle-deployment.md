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

The VM uses the team's selected Always Free Arm shape. Confirm current Oracle Always Free limits before changing its resource allocation.

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

- **Frontend origin / CORS:** The frontend origin is not yet configured. Add the actual deployed frontend URL to `frontend_origin` and the relevant application configuration once provided by the team.
- **Full persistence verification:** The Task database restart was successful. A complete restart test covering all databases and RabbitMQ, followed by application data verification, is still pending.
- **Backup and restore drill:** Backup and restore procedures exist, but a complete verified recovery drill is still pending.
- **Architecture build compatibility:** Confirm that every deployed service image supports the VM's Arm architecture. Do not assume an image built for x86 also supports ARM.
- **Deployment workflow:** The GitHub deployment workflow connects over SSH, while `admin_cidr` restricts SSH to an approved source address. The team must decide how automated deployment access should work.
- **Rate limits behind HTTPS:** The gateway must correctly handle forwarded client addresses from Caddy so all visitors are not treated as one client.
- **Production integration:** Public API routing has been verified, but authenticated end-to-end workflows still need testing with valid credentials and real application data.
- **Secrets and team prerequisites:** The Google client ID, package access token and frontend URL must be supplied by the team. Never commit credentials or generated secrets.

**Important:** Terraform currently reports no infrastructure changes. Do not apply or recreate the VM just to complete these application verification checks.