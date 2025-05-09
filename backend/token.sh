#!/bin/bash

# === Credentials ===
api_url="https://57.203.253.112:443/api/auth/login"
user_id="CAXCL164"
password="G/WnZ1n%LN#VYa"

# === Get token ===
response=$(curl -sk -X POST "$api_url" \
  -H "accept: application/json" \
  -H "Content-Type: application/json" \
  -d "{\"userId\": \"$user_id\", \"password\": \"$password\"}")

# === Parse token ===
token=$(echo "$response" | jq -r '.accessToken')

# === Save token ===
if [[ "$token" != "null" && -n "$token" ]]; then
  echo "$token" > /app/token.txt
  echo "[INFO] $(date) Token saved: $token"
else
  echo "[ERROR] $(date) Failed to get token. Response:"
  echo "$response"
fi
