output "public_ip" {
  description = "Point your domain at this address."
  value       = oci_core_instance.backend.public_ip
}

output "ssh_command" {
  description = "Log in with your private key."
  value       = "ssh -i ${trimsuffix(var.ssh_public_key_path, ".pub")} ubuntu@${oci_core_instance.backend.public_ip}"
}

output "compartment_id" {
  value = oci_identity_compartment.project.id
}

output "instance_id" {
  value = oci_core_instance.backend.id
}
