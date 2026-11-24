"""Eigenes HTTPS-Zertifikat fuer die Web-App (Voraussetzung fuer den Offline-Betrieb auf dem iPhone).

Das Add-on erzeugt einmalig eine eigene Zertifizierungsstelle (CA, 10 Jahre gueltig). Diese wird
einmal auf dem iPhone installiert und als vertrauenswuerdig markiert. Damit signiert das Add-on ein
Serverzertifikat fuer alle Adressen, unter denen die App aufgerufen wird (gelernt aus den Aufrufen
ueber den direkten Port, plus eigene Eintraege). Das Serverzertifikat laeuft 800 Tage (Apple
erlaubt fuer selbst installierte CAs hoechstens 825) und wird automatisch erneuert.
"""
import ipaddress
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from . import db

log = logging.getLogger("ausgaben.tls")

ORDNER = os.environ.get("AUSGABEN_TLS", "/data/tls")
CA_KEY, CA_CRT = os.path.join(ORDNER, "ca.key"), os.path.join(ORDNER, "ca.crt")
SRV_KEY, SRV_CRT = os.path.join(ORDNER, "server.key"), os.path.join(ORDNER, "server.crt")
PORT = 8443
GRUND_NAMEN = ["localhost", "127.0.0.1", "homeassistant.local", "homeassistant"]
SERVER_TAGE = 800
ERNEUERN_TAGE = 60
MAX_NAMEN = 30

_lock = threading.Lock()
ssl_kontext = None  # wird von main gesetzt, damit ein neues Zertifikat ohne Neustart greift


def _schreiben(pfad, daten, geheim=False):
    os.makedirs(ORDNER, exist_ok=True)
    with open(pfad, "wb") as f:
        f.write(daten)
    if geheim:
        os.chmod(pfad, 0o600)


def _schluessel():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _pem_key(k):
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                           serialization.NoEncryption())


def _ca_anlegen():
    k = _schluessel()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Ausgaben Add-on CA"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Ausgaben (Home Assistant)")])
    jetzt = datetime.now(timezone.utc)
    crt = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
           .serial_number(x509.random_serial_number())
           .not_valid_before(jetzt - timedelta(days=1)).not_valid_after(jetzt + timedelta(days=3650))
           .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
           .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                        content_commitment=False, key_encipherment=False, data_encipherment=False,
                                        key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
           .add_extension(x509.SubjectKeyIdentifier.from_public_key(k.public_key()), critical=False)
           .sign(k, hashes.SHA256()))
    _schreiben(CA_KEY, _pem_key(k), geheim=True)
    _schreiben(CA_CRT, crt.public_bytes(serialization.Encoding.PEM))
    log.info("Eigene Zertifizierungsstelle angelegt")


def _laden(pfad):
    with open(pfad, "rb") as f:
        return x509.load_pem_x509_certificate(f.read())


def namen():
    """Alle Adressen fuer das Serverzertifikat (Grundnamen, gelernte, eigene)."""
    try:
        gelernt = json.loads(db.einstellung("https_gelernt") or "[]")
        eigene = json.loads(db.einstellung("https_eigene") or "[]")
    except ValueError:
        gelernt, eigene = [], []
    alle = []
    for n in GRUND_NAMEN + eigene + gelernt:
        n = (n or "").strip().lower().strip("[]")
        if n and n not in alle:
            alle.append(n)
    return alle[:MAX_NAMEN]


def _san(n):
    try:
        return x509.IPAddress(ipaddress.ip_address(n))
    except ValueError:
        return x509.DNSName(n)


def _server_anlegen():
    ca_k = serialization.load_pem_private_key(open(CA_KEY, "rb").read(), password=None)
    ca = _laden(CA_CRT)
    k = _schluessel()
    liste = namen()
    jetzt = datetime.now(timezone.utc)
    crt = (x509.CertificateBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Ausgaben")]))
           .issuer_name(ca.subject).public_key(k.public_key()).serial_number(x509.random_serial_number())
           .not_valid_before(jetzt - timedelta(days=1)).not_valid_after(jetzt + timedelta(days=SERVER_TAGE))
           .add_extension(x509.SubjectAlternativeName([_san(n) for n in liste]), critical=False)
           .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
           .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=True, key_cert_sign=False,
                                        crl_sign=False, content_commitment=False, data_encipherment=False,
                                        key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
           .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
           .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_k.public_key()), critical=False)
           .sign(ca_k, hashes.SHA256()))
    _schreiben(SRV_KEY, _pem_key(k), geheim=True)
    _schreiben(SRV_CRT, crt.public_bytes(serialization.Encoding.PEM) + open(CA_CRT, "rb").read())
    log.info("Serverzertifikat fuer %s erstellt", ", ".join(liste))
    if ssl_kontext is not None:
        try:
            ssl_kontext.load_cert_chain(SRV_CRT, SRV_KEY)
        except Exception:  # noqa: BLE001
            log.exception("Neues Zertifikat konnte nicht geladen werden")


def _server_namen():
    crt = _laden(SRV_CRT)
    san = crt.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return {str(n.value).lower() for n in san}, crt.not_valid_after_utc


def sicherstellen():
    """CA und Serverzertifikat anlegen bzw. erneuern, wenn noetig. Gibt (cert, key) zurueck."""
    with _lock:
        if not (os.path.exists(CA_KEY) and os.path.exists(CA_CRT)):
            _ca_anlegen()
            for p in (SRV_KEY, SRV_CRT):
                if os.path.exists(p):
                    os.remove(p)
        erneuern = not (os.path.exists(SRV_KEY) and os.path.exists(SRV_CRT))
        if not erneuern:
            vorhanden, ablauf = _server_namen()
            erneuern = (not set(namen()) <= vorhanden
                        or ablauf - datetime.now(timezone.utc) < timedelta(days=ERNEUERN_TAGE))
        if erneuern:
            _server_anlegen()
    return SRV_CRT, SRV_KEY


_bekannt = set()


def adresse_lernen(host):
    """Adresse aus einem Aufruf ueber den direkten Port merken (z. B. 192.168.0.193)."""
    host = (host or "").strip().lower().strip("[]")
    if not host or host in _bekannt or len(host) > 100:
        return
    _bekannt.add(host)
    if host in namen():
        return
    try:
        gelernt = json.loads(db.einstellung("https_gelernt") or "[]")
    except ValueError:
        gelernt = []
    if host in gelernt or len(gelernt) >= MAX_NAMEN:
        return
    gelernt.append(host)
    db.setze_einstellung("https_gelernt", json.dumps(gelernt))
    threading.Thread(target=sicherstellen, daemon=True).start()


def eigene_setzen(liste):
    sauber = [n.strip().lower() for n in liste if n and n.strip()][:MAX_NAMEN]
    db.setze_einstellung("https_eigene", json.dumps(sauber))
    sicherstellen()


def ca_pem():
    sicherstellen()
    return open(CA_CRT, "rb").read()


def status():
    sicherstellen()
    ca = _laden(CA_CRT)
    vorhanden, ablauf = _server_namen()
    fp = ca.fingerprint(hashes.SHA256()).hex().upper()
    try:
        eigene = json.loads(db.einstellung("https_eigene") or "[]")
    except ValueError:
        eigene = []
    return {"port": PORT, "namen": [n for n in namen() if n not in GRUND_NAMEN], "eigene": eigene,
            "gueltig_bis": ablauf.date().isoformat(), "ca_gueltig_bis": ca.not_valid_after_utc.date().isoformat(),
            "fingerabdruck": ":".join(fp[i:i + 2] for i in range(0, 32, 2)) + " …"}
