# ---- Public HTTPS (all optional: leave empty to create the VM without HTTPS) ----

variable "domain_name" {
  description = "Public hostname of the API, e.g. api.example.com. Empty skips HTTPS."
  type        = string
  default     = ""

  validation {
    condition     = var.domain_name == "" || can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", var.domain_name))
    error_message = "domain_name must be a plain lowercase hostname such as api.example.com."
  }
}

variable "frontend_origin" {
  description = "Exact origin of the Netlify frontend, e.g. https://taskmgn.netlify.app (no trailing slash). Empty keeps the local dev origins."
  type        = string
  default     = ""

  validation {
    condition     = var.frontend_origin == "" || can(regex("^https://[a-z0-9]([a-z0-9.-]*[a-z0-9])?(:[0-9]+)?$", var.frontend_origin))
    error_message = "frontend_origin must look like https://name.netlify.app (https, no path, no trailing slash)."
  }
}

variable "acme_email" {
  description = "Optional contact email for certificate expiry notices."
  type        = string
  default     = ""

  validation {
    condition     = var.acme_email == "" || can(regex("^[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+$", var.acme_email))
    error_message = "acme_email must be a plain email address."
  }
}
