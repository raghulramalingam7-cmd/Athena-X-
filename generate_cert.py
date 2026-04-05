"""
generate_cert.py — Generate a self-signed SSL certificate for Athena-X HTTPS.
Run once before starting the server:  python generate_cert.py

Produces:
  cert.pem   — public certificate (valid 3 years)
  key.pem    — private key

These are used by Gunicorn to serve HTTPS on the LAN.
Browser will show "Not secure" warning on first visit — click Advanced → Proceed.
For a trusted cert, replace cert.pem/key.pem with one from Let's Encrypt or your CA.
"""

import os
import datetime
import ipaddress
import socket

try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
except ImportError:
    print("Installing cryptography...")
    os.system("pip install cryptography -q")
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

CERT_FILE = "cert.pem"
KEY_FILE  = "key.pem"

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def generate():
    if os.path.exists(CERT_FILE) and os.path.exists(KEY_FILE):
        print(f"✅  {CERT_FILE} and {KEY_FILE} already exist — skipping generation.")
        print("    Delete them and re-run to regenerate.")
        return

    local_ip = get_local_ip()
    hostname = socket.gethostname()

    print(f"🔐  Generating self-signed SSL certificate...")
    print(f"    Hostname : {hostname}")
    print(f"    LAN IP   : {local_ip}")

    # Generate RSA private key
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Build certificate
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "IN"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Athena-X Sports"),
        x509.NameAttribute(NameOID.COMMON_NAME, hostname),
    ])

    san_list = [
        x509.DNSName("localhost"),
        x509.DNSName(hostname),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]
    try:
        san_list.append(x509.IPAddress(ipaddress.IPv4Address(local_ip)))
    except Exception:
        pass

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1095))  # 3 years
        .add_extension(x509.SubjectAlternativeName(san_list), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )

    # Write key
    with open(KEY_FILE, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        ))

    # Write cert
    with open(CERT_FILE, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

    # Secure the key file
    try:
        os.chmod(KEY_FILE, 0o600)
    except Exception:
        pass

    print(f"\n✅  Certificate generated successfully!")
    print(f"    cert.pem → valid for 3 years")
    print(f"    key.pem  → keep this file private!\n")
    print(f"📱  Users will see a browser warning on first visit.")
    print(f"    Tell them: click 'Advanced' → 'Proceed to {local_ip} (unsafe)'")
    print(f"    This is normal for self-signed certs on a LAN.\n")

if __name__ == "__main__":
    generate()
