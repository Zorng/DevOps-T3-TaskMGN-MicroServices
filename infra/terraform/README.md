# Oracle Cloud provisioning (Terraform)

Creates the free-tier backend foundation for issue #32: one compartment, one network
(only SSH from the admin address, HTTP and HTTPS public) and one Always Free Arm VM.
On first boot, cloud-init adds swap, opens the VM's own firewall for ports 80 and 443,
installs Docker and Compose, enforces key-only SSH and clones this repository to
`/opt/taskmgn`. The application itself is deployed with Docker Compose (see the runbook).

Nothing in this folder is a credential. Your values live in `terraform.tfvars`, and
Terraform state in `terraform.tfstate`; Git ignores both, and neither may be committed.
Commit `.terraform.lock.hcl`, which pins the provider version.

## Prerequisites

- An Oracle Cloud user that can manage compute, networking and compartments in the tenancy.
- An SSH key pair (`~/.ssh/taskmgn_oracle` and `.pub`).
- Terraform 1.5 or newer: `brew tap hashicorp/tap && brew install hashicorp/tap/terraform`.

## Use

```sh
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # fill in your values
terraform init
terraform validate
terraform plan        # read it: nothing is created yet
terraform apply       # type "yes" to create the resources
terraform output      # public_ip and ssh_command
```

Wait for the first-boot setup, then check the VM:

```sh
ssh -i ~/.ssh/taskmgn_oracle ubuntu@<public_ip>
cloud-init status --wait          # "status: done"
tail -n 5 /var/log/taskmgn-bootstrap.log    # ends with "bootstrap finished"
docker --version && docker compose version
uname -m                          # aarch64 (Arm)
sudo iptables -L INPUT -n | grep -E "dpt:(80|443)"
```

Log out and in again once so your user picks up the `docker` group.

## Capacity and cost

- Always Free Arm capacity is often unavailable. If `apply` fails with "Out of host
  capacity", it is not a bug: run `terraform apply` again later, or try
  `availability_domain_index = 1` if your region has more than one domain.
- Only free resources are created. Do not add a NAT gateway, load balancer, reserved IP,
  extra volumes or instances, and do not upgrade the account.
- `terraform destroy` deletes everything it created, including the VM's data.

## Limits of this setup

- Terraform state is local to the machine that ran `apply`. Keep that machine's
  `terraform.tfstate` safe; if it is lost, the resources still exist in the console and
  can be imported or deleted by hand.
- The public IP is ephemeral: it stays while the VM exists but changes if the VM is
  recreated, so update the DNS record after any re-creation.
