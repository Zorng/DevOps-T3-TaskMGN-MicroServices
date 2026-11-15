-- Establish the service-owned namespace. Domain tables arrive with their features.
CREATE SCHEMA IF NOT EXISTS task_service;
COMMENT ON SCHEMA task_service IS 'Private persistence for the Task microservice';
