# Free-tier backend foundation: one network, one Arm VM, nothing else.
# Deliberately NOT created (they cost money): NAT gateway, load balancer,
# extra volumes, reserved IPs, extra instances.

data "oci_identity_availability_domains" "ads" {
  compartment_id = var.tenancy_ocid
}

locals {
  availability_domain = data.oci_identity_availability_domains.ads.availability_domains[var.availability_domain_index].name
}

resource "oci_identity_compartment" "project" {
  compartment_id = var.tenancy_ocid
  name           = var.compartment_name
  description    = "Task Management DevOps project (managed by Terraform)"
  enable_delete  = true
}

# ---- Network ----

resource "oci_core_vcn" "main" {
  compartment_id = oci_identity_compartment.project.id
  display_name   = "taskmgn-vcn"
  cidr_blocks    = ["10.0.0.0/16"]
  dns_label      = "taskmgn"
}

resource "oci_core_internet_gateway" "main" {
  compartment_id = oci_identity_compartment.project.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = "taskmgn-igw"
  enabled        = true
}

resource "oci_core_route_table" "public" {
  compartment_id = oci_identity_compartment.project.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = "taskmgn-public"

  route_rules {
    destination       = "0.0.0.0/0"
    destination_type  = "CIDR_BLOCK"
    network_entity_id = oci_core_internet_gateway.main.id
  }
}

# Only SSH (from the admin address) and HTTP/HTTPS (public) reach the VM. Databases,
# the broker and the services never listen on a public port.
resource "oci_core_security_list" "public" {
  compartment_id = oci_identity_compartment.project.id
  vcn_id         = oci_core_vcn.main.id
  display_name   = "taskmgn-public"

  ingress_security_rules {
    protocol    = "6" # TCP
    source      = var.admin_cidr
    source_type = "CIDR_BLOCK"
    description = "SSH from the administrator only"
    tcp_options {
      min = 22
      max = 22
    }
  }

  ingress_security_rules {
    protocol    = "6"
    source      = "0.0.0.0/0"
    source_type = "CIDR_BLOCK"
    description = "HTTP (certificate issuing and redirect to HTTPS)"
    tcp_options {
      min = 80
      max = 80
    }
  }

  ingress_security_rules {
    protocol    = "6"
    source      = "0.0.0.0/0"
    source_type = "CIDR_BLOCK"
    description = "HTTPS (the public API)"
    tcp_options {
      min = 443
      max = 443
    }
  }

  egress_security_rules {
    protocol         = "all"
    destination      = "0.0.0.0/0"
    destination_type = "CIDR_BLOCK"
    description      = "Outbound: package installs, image pulls, Google sign-in, certificates"
  }
}

resource "oci_core_subnet" "public" {
  compartment_id    = oci_identity_compartment.project.id
  vcn_id            = oci_core_vcn.main.id
  display_name      = "taskmgn-public"
  cidr_block        = "10.0.0.0/24"
  dns_label         = "public"
  route_table_id    = oci_core_route_table.public.id
  security_list_ids = [oci_core_security_list.public.id]
}

# ---- Compute ----

data "oci_core_images" "ubuntu" {
  compartment_id           = var.tenancy_ocid
  operating_system         = "Canonical Ubuntu"
  operating_system_version = "24.04"
  shape                    = var.shape
  sort_by                  = "TIMECREATED"
  sort_order               = "DESC"
}

resource "oci_core_instance" "backend" {
  compartment_id      = oci_identity_compartment.project.id
  availability_domain = local.availability_domain
  display_name        = "taskmgn-backend"
  shape               = var.shape

  shape_config {
    ocpus         = var.ocpus
    memory_in_gbs = var.memory_gb
  }

  source_details {
    source_type             = "image"
    source_id               = data.oci_core_images.ubuntu.images[0].id
    boot_volume_size_in_gbs = var.boot_volume_gb
  }

  create_vnic_details {
    subnet_id        = oci_core_subnet.public.id
    assign_public_ip = true
    hostname_label   = "taskmgn"
  }

  metadata = {
    ssh_authorized_keys = file(pathexpand(var.ssh_public_key_path))
    user_data = base64encode(templatefile("${path.module}/cloud-init.yaml.tftpl", {
      repo_url = var.repo_url
      repo_ref = var.repo_ref
    }))
  }

  # Docker data lives on this boot volume and is deleted with the VM. Back up first
  # if the data matters (see the runbook).
  preserve_boot_volume = false

  lifecycle {
    # A newer Ubuntu image must not make Terraform replace the VM (and its data).
    ignore_changes = [source_details[0].source_id]
  }
}
