#!/bin/sh
# Builds the broker's password file at startup from plaintext DEV-ONLY
# credentials (mosquitto/dev-passwords.txt). A real deployment would issue
# each device its own X.509 certificate (mTLS) instead of a shared-secret
# password, and would never keep secrets in the repo.
set -eu

cp /mosquitto/config/dev-passwords.txt /mosquitto/data/passwd
cp /mosquitto/config/acl /mosquitto/data/acl
chown mosquitto:mosquitto /mosquitto/data/passwd /mosquitto/data/acl
chmod 0600 /mosquitto/data/passwd /mosquitto/data/acl

mosquitto_passwd -U /mosquitto/data/passwd   # hash the plaintext in place

exec mosquitto -c /mosquitto/config/mosquitto.conf
