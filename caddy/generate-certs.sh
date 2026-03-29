#!/bin/sh
# Generate a self-signed CA and server certificate for HTTPS.
# Runs once — skips if certs already exist in /certs/.

set -e

CERT_DIR="/certs"
HOSTNAME="${SERVER_HOSTNAME:-browserguardian}"

if [ -f "$CERT_DIR/ca.crt" ] && [ -f "$CERT_DIR/server.crt" ]; then
    echo "[cert-init] Certificates already exist, skipping generation."
    exit 0
fi

echo "[cert-init] Generating CA and server certificates for hostname: $HOSTNAME"

# 1. Generate CA private key and self-signed certificate (10 years)
openssl genrsa -out "$CERT_DIR/ca.key" 4096 2>/dev/null
openssl req -new -x509 -days 3650 -key "$CERT_DIR/ca.key" \
    -out "$CERT_DIR/ca.crt" \
    -subj "/C=AU/ST=Victoria/O=BrowserGuardian/CN=BrowserGuardian CA"

# 2. Generate server private key
openssl genrsa -out "$CERT_DIR/server.key" 2048 2>/dev/null

# 3. Create SAN config for the server certificate
cat > "$CERT_DIR/san.cnf" <<EOF
[req]
default_bits = 2048
prompt = no
distinguished_name = dn
req_extensions = v3_req

[dn]
C = AU
ST = Victoria
O = BrowserGuardian
CN = $HOSTNAME

[v3_req]
subjectAltName = @alt_names

[alt_names]
DNS.1 = $HOSTNAME
DNS.2 = localhost
IP.1 = 127.0.0.1
EOF

# 4. Generate CSR with SAN
openssl req -new -key "$CERT_DIR/server.key" \
    -out "$CERT_DIR/server.csr" \
    -config "$CERT_DIR/san.cnf"

# 5. Sign server cert with CA (1 year)
openssl x509 -req -days 365 \
    -in "$CERT_DIR/server.csr" \
    -CA "$CERT_DIR/ca.crt" \
    -CAkey "$CERT_DIR/ca.key" \
    -CAcreateserial \
    -out "$CERT_DIR/server.crt" \
    -extensions v3_req \
    -extfile "$CERT_DIR/san.cnf" 2>/dev/null

# Clean up temp files
rm -f "$CERT_DIR/server.csr" "$CERT_DIR/san.cnf" "$CERT_DIR/ca.srl"

# Make certs readable
chmod 644 "$CERT_DIR/ca.crt" "$CERT_DIR/server.crt"
chmod 600 "$CERT_DIR/ca.key" "$CERT_DIR/server.key"

echo "[cert-init] Certificates generated successfully:"
echo "  CA cert:     $CERT_DIR/ca.crt  (upload this to Google Admin Console)"
echo "  Server cert: $CERT_DIR/server.crt"
echo "  Hostname:    $HOSTNAME"
