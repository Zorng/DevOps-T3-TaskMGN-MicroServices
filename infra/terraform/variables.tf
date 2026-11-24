# ---- Credentials (never commit values: they live in terraform.tfvars, ignored by Git) ----

variable "tenancy_ocid" {
  description = "OCID of the tenancy (Profile menu, Tenancy)."
  type        = string
}

variable "user_ocid" {
  description = "OCID of the user whose API key Terraform uses."
  type        = string
}

variable "fingerprint" {
  description = "Fingerprint of that user's API key (shown when the key is added)."
  type        = string
}

variable "private_key_path" {
  description = "Path to the API private key file on your machine."
  type        = string
}

# ---- Where and how big ----

variable "region" {
  description = "Home region. The Always Free VM can only be created here."
  type        = string
  default     = "ap-singapore-1"
}

variable "compartment_name" {
  description = "Terraform creates this compartment under the tenancy and keeps every resource in it."
  type        = string
  default     = "taskmgn"
}

variable "shape" {
  description = "Always Free Arm shape."
  type        = string
  default     = "VM.Standard.A1.Flex"

  validation {
    condition     = var.shape == "VM.Standard.A1.Flex"
    error_message = "Only the Always Free Arm shape VM.Standard.A1.Flex is allowed, to avoid charges."
  }
}

variable "ocpus" {
  description = "CPUs. Oracle's free Arm allowance is 4 (reported to drop to 2); the default is the smaller one."
  type        = number
  default     = 2

  validation {
    condition     = var.ocpus >= 1 && var.ocpus <= 4
    error_message = "ocpus must be between 1 and 4 (the Always Free maximum)."
  }
}

variable "memory_gb" {
  description = "Memory in GB. Free allowance is 24 (reported to drop to 12)."
  type        = number
  default     = 12

  validation {
    condition     = var.memory_gb >= 1 && var.memory_gb <= 24
    error_message = "memory_gb must be between 1 and 24 (the Always Free maximum)."
  }
}

variable "boot_volume_gb" {
  description = "Boot volume size. Docker data lives here. Always Free storage totals 200 GB."
  type        = number
  default     = 100

  validation {
    condition     = var.boot_volume_gb >= 50 && var.boot_volume_gb <= 200
    error_message = "boot_volume_gb must be between 50 and 200."
  }
}

variable "availability_domain_index" {
  description = "Which availability domain to use (Singapore has one). Try another index if capacity is missing."
  type        = number
  default     = 0
}

# ---- Access ----

variable "ssh_public_key_path" {
  description = "Public SSH key installed on the VM."
  type        = string
  default     = "~/.ssh/taskmgn_oracle.pub"
}

variable "admin_cidr" {
  description = "The only address allowed to SSH to the VM, e.g. 203.0.113.7/32 (your public IP)."
  type        = string

  validation {
    condition     = can(cidrhost(var.admin_cidr, 0)) && var.admin_cidr != "0.0.0.0/0"
    error_message = "admin_cidr must be a valid CIDR such as 203.0.113.7/32, and must not be 0.0.0.0/0."
  }
}

# ---- What gets deployed ----

variable "repo_url" {
  description = "Repository cloned onto the VM."
  type        = string
  default     = "https://github.com/Zorng/DevOps-T3-TaskMGN-MicroServices.git"
}

variable "repo_ref" {
  description = "Branch or tag cloned onto the VM."
  type        = string
  default     = "main"
}
