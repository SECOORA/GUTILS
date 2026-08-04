#!/usr/bin/env python
"""FTP/FTPS compatibility and configuration diagnostic.

Point this at a data assembly center's FTP endpoint and it reports what the
server actually supports, so the FTP -> FTPS migration can be planned per host
instead of guessed at. Every check is read-only: the data channel is exercised
with LIST, never an upload, unless --upload is given explicitly.

The check that matters most is "TLS session reuse on the data channel". vsftpd
defaults to require_ssl_reuse=YES and IIS behaves the same way, but Python's
stdlib ftplib never reuses the control connection's TLS session -- so AUTH TLS,
login and PROT P all succeed and only the first transfer fails. This tool
separates that case from a passive-mode or firewall problem by retrying the
same listing with a client that does reuse the session.

Exit status is 0 when FTPS works against this host with a stock client,
1 when FTPS works but only with the session reuse workaround, and
2 when FTPS is unusable.
"""

import ssl
import sys
import socket
import hashlib
import ftplib
import os
from getpass import getpass
from argparse import ArgumentParser

RED = 1
GREEN = 2
YELLOW = 3
BLUE = 4

EXIT_OK = 0
EXIT_NEEDS_REUSE = 1
EXIT_UNUSABLE = 2


def color(key, msg):
    prefix = {
        RED: "\033[31m",
        GREEN: "\033[32m",
        YELLOW: "\033[33m",
        BLUE: "\033[34m",
    }.get(key, "")
    reset = "\033[0m"

    if sys.stdout.isatty():
        print(f"{prefix}{msg}{reset}")
    else:
        print(msg)


def ok(msg):
    color(GREEN, f"  [ ok ] {msg}")


def fail(msg):
    color(RED, f"  [fail] {msg}")


def warn(msg):
    color(YELLOW, f"  [warn] {msg}")


def info(msg):
    print(f"  [info] {msg}")


def section(msg):
    print("")
    color(BLUE, msg)


class TLSReusedSessionFTP(ftplib.FTP_TLS):
    """FTP_TLS that resumes the control connection's TLS session on the data
    connection.

    Required by vsftpd (require_ssl_reuse=YES, its default) and by IIS. The
    stdlib does not do this: FTP_TLS.ntransfercmd wraps the data socket with no
    session= argument, so the server sees an unrelated TLS session and refuses
    the transfer.
    """

    def ntransfercmd(self, cmd, rest=None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(
                conn,
                server_hostname=self.host,
                session=self.sock.session,
            )
        return conn, size


def make_context(verify):
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def check_reachable(args):
    """TCP reachability, separated out so a firewall does not look like a TLS
    problem."""
    section(f"Reachability -- {args.host}:{args.port}")
    try:
        with socket.create_connection((args.host, args.port), timeout=args.timeout) as sock:
            banner = sock.recv(512).decode("utf-8", "replace").strip()
        ok(f"TCP connect to port {args.port}")
        if banner:
            info(f"banner: {banner.splitlines()[0]}")
        return True
    except Exception as exc:
        fail(f"cannot reach {args.host}:{args.port} -- {type(exc).__name__}: {exc}")
        return False


def check_feat(args):
    """Ask the server what it supports before authenticating.

    FEAT (RFC 2389) is the protocol-sanctioned way to find out whether AUTH TLS
    is available, and it is far more reliable than matching the text of an
    error reply -- servers phrase "I do not support that" every possible way.
    """
    section("Feature negotiation (FEAT, pre-auth)")
    try:
        with ftplib.FTP() as ftp:
            ftp.connect(host=args.host, port=args.port, timeout=args.timeout)
            try:
                response = ftp.sendcmd("FEAT")
            except ftplib.all_errors as exc:
                warn(f"FEAT not supported -- {exc}")
                warn("TLS support cannot be probed directly; falling back to trying AUTH TLS")
                return None

            features = [
                line.strip()
                for line in response.splitlines()[1:-1]
            ]
            info(f"features: {', '.join(features) if features else '(none reported)'}")

            auth_tls = any(f.upper().startswith("AUTH") and "TLS" in f.upper() for f in features)
            if auth_tls:
                ok("server advertises AUTH TLS")
            else:
                fail("server does not advertise AUTH TLS -- it is plaintext-only")
            return auth_tls
    except Exception as exc:
        fail(f"FEAT probe failed -- {type(exc).__name__}: {exc}")
        return None


def check_plaintext(args, password):
    """Report whether a cleartext login still works.

    This is a security finding, not a failure: if the server still accepts
    plaintext logins then a client that downgrades will send DAC credentials in
    the clear, which is the exposure the FTPS migration exists to close.
    """
    section("Plaintext FTP")
    try:
        with ftplib.FTP() as ftp:
            ftp.connect(host=args.host, port=args.port, timeout=args.timeout)
            ftp.login(user=args.user, passwd=password)
            entries = []
            ftp.retrlines("LIST", entries.append)
            warn(f"plaintext login accepted -- credentials are sent in the clear ({len(entries)} entries listed)")
            warn("this host still permits a downgrade; do not enable a fallback once it has migrated")
            ftp.quit()
            return True
    except ftplib.all_errors as exc:
        ok(f"plaintext login rejected -- {exc}")
        return False
    except Exception as exc:
        fail(f"unexpected error -- {type(exc).__name__}: {exc}")
        return False


def describe_cert(sock):
    """Print what can be learned about the peer certificate.

    With verification disabled getpeercert() returns nothing parsed, so fall
    back to a fingerprint of the DER form -- enough to confirm two hosts share a
    cert, or to pin one.
    """
    cert = sock.getpeercert()
    if cert:
        subject = dict(x[0] for x in cert.get("subject", ()))
        issuer = dict(x[0] for x in cert.get("issuer", ()))
        info(f"subject:   {subject.get('commonName', '?')}")
        info(f"issuer:    {issuer.get('commonName', '?')} / {issuer.get('organizationName', '?')}")
        info(f"valid:     {cert.get('notBefore')} .. {cert.get('notAfter')}")
        sans = [v for k, v in cert.get("subjectAltName", ()) if k in ("DNS", "IP Address")]
        info(f"SANs:      {', '.join(sans) if sans else '(none)'}")
    der = sock.getpeercert(binary_form=True)
    if der:
        info(f"sha256:    {hashlib.sha256(der).hexdigest()}")


def check_cert_verification(args):
    """Try the handshake with verification on, as a separate connection.

    Kept apart from the functional checks so that a bad certificate is reported
    as a bad certificate, rather than being masked by -k.

    Returns True if the certificate verified, False if it was rejected, and
    None if TLS never got far enough for the certificate to matter -- those are
    three different problems and must not be collapsed into one.
    """
    section("Certificate verification (against the system trust store)")
    try:
        with ftplib.FTP_TLS(context=make_context(verify=True)) as ftps:
            ftps.connect(host=args.host, port=args.port, timeout=args.timeout)
            ftps.auth()
            ok("certificate verified")
            describe_cert(ftps.sock)
            return True
    except ssl.SSLCertVerificationError as exc:
        fail(f"certificate rejected -- {exc.verify_message or exc}")
        info("a client that verifies certs will refuse this host; it must not silently downgrade")
        return False
    except ftplib.error_perm as exc:
        fail(f"AUTH TLS rejected, no certificate to check -- {exc}")
        return None
    except Exception as exc:
        fail(f"handshake failed -- {type(exc).__name__}: {exc}")
        return None


def open_ftps(args, password, cls):
    """Connect, AUTH TLS, log in and switch the data channel to PROT P."""
    ftps = cls(context=make_context(verify=args.verify))
    ftps.connect(host=args.host, port=args.port, timeout=args.timeout)
    ftps.auth()
    ftps.login(user=args.user, passwd=password)
    ftps.prot_p()
    return ftps


def list_dir(ftps):
    entries = []
    ftps.retrlines("LIST", entries.append)
    return entries


def check_ftps(args, password):
    """The functional FTPS check, including the session reuse determination.

    Returns one of "ok", "needs-reuse", or "broken".
    """
    section("FTPS (explicit AUTH TLS)")

    try:
        ftps = open_ftps(args, password, ftplib.FTP_TLS)
    except ftplib.error_perm as exc:
        fail(f"AUTH TLS or login rejected -- {exc}")
        info("note: FTP_TLS.login() performs AUTH TLS internally, so a rejected")
        info("      cipher, a rejected certificate and a bad password all arrive")
        info("      here as error_perm. Compare against the FEAT result above.")
        return "broken"
    except Exception as exc:
        fail(f"could not establish FTPS session -- {type(exc).__name__}: {exc}")
        return "broken"

    ok("AUTH TLS, login and PROT P succeeded")
    info(f"protocol:  {ftps.sock.version()}")
    info(f"cipher:    {ftps.sock.cipher()[0]}")
    if not args.verify:
        warn("certificate verification disabled for this check (-k)")
    describe_cert(ftps.sock)

    # The PASV address is a common misconfiguration: servers behind NAT often
    # hand back an unroutable private address, which fails at exactly the same
    # point as a session reuse refusal.
    # Read the 227 reply directly rather than calling makepasv(): for IPv4
    # ftplib deliberately throws away the address the server advertises and
    # substitutes the control connection's peer address instead
    # (trust_server_pasv_ipv4_address defaults to False). makepasv() would
    # therefore always agree with the control address and report nothing.
    try:
        advertised_host, advertised_port = ftplib.parse227(ftps.sendcmd("PASV"))
        control_ip = ftps.sock.getpeername()[0]
        info(f"passive:   server advertises {advertised_host}:{advertised_port}")
        if advertised_host != control_ip:
            warn(f"advertised PASV address {advertised_host} is not the control "
                 f"address {control_ip} -- the server is probably behind NAT")
            info(f"ftplib ignores it and connects to {control_ip} anyway, so GUTILS is")
            info("unaffected, but other FTP clients will try to reach it directly.")
    except Exception as exc:
        warn(f"PASV probe failed -- {type(exc).__name__}: {exc}")

    # The data channel is where session reuse is enforced. LIST is enough:
    # it opens a PROT P data connection exactly like a STOR would, and is
    # read-only.
    section("Data channel (LIST over PROT P)")
    try:
        entries = list_dir(ftps)
        ok(f"stock ftplib data transfer succeeded ({len(entries)} entries)")
        _quit(ftps)
        return "ok"
    except Exception as exc:
        fail(f"stock ftplib data transfer failed -- {type(exc).__name__}: {exc}")
        _quit(ftps)

    # Same listing, this time resuming the control connection's TLS session. If
    # this succeeds the diagnosis is unambiguous.
    info("retrying with TLS session reuse to identify the cause...")
    try:
        reused = open_ftps(args, password, TLSReusedSessionFTP)
        entries = list_dir(reused)
        _quit(reused)
    except Exception as exc:
        fail(f"session reuse did not help -- {type(exc).__name__}: {exc}")
        info("the data channel is failing for another reason: passive port range")
        info("blocked by a firewall, an unroutable PASV address, or a proxy.")
        return "broken"

    ok(f"data transfer succeeded with TLS session reuse ({len(entries)} entries)")
    warn("this server REQUIRES TLS session reuse on the data connection")
    info("stock ftplib cannot talk to it; the client must resume the control")
    info("session when wrapping the data socket (see ReusedSessionFTP_TLS above).")
    return "needs-reuse"


def check_upload(args, password, needs_reuse):
    """Optional write test, off by default because it leaves a file behind."""
    section(f"Upload test (--upload, writes {args.upload})")
    cls = TLSReusedSessionFTP if needs_reuse else ftplib.FTP_TLS
    try:
        ftps = open_ftps(args, password, cls)
        import io
        ftps.storbinary(f"STOR {args.upload}", io.BytesIO(b"gutils ftps_check\n"))
        ok(f"uploaded {args.upload}")
        try:
            ftps.delete(args.upload)
            ok(f"deleted {args.upload}")
        except ftplib.all_errors as exc:
            warn(f"could not delete {args.upload} -- {exc}; remove it manually")
        _quit(ftps)
        return True
    except Exception as exc:
        fail(f"upload failed -- {type(exc).__name__}: {exc}")
        return False


def _quit(ftps):
    try:
        ftps.quit()
    except Exception:
        try:
            ftps.close()
        except Exception:
            pass


def summarize(result, advertises_tls, plaintext_ok, cert_ok):
    section("Summary")
    if result == "ok":
        ok("FTPS works against this host with a stock client")
    elif result == "needs-reuse":
        warn("FTPS works only with a client that reuses the TLS session")
    else:
        fail("FTPS is not usable against this host")

    if advertises_tls is False:
        info("server does not advertise AUTH TLS: it is plaintext-only and cannot be migrated yet")
    elif advertises_tls and result == "broken":
        # Seen on pure-ftpd built with TLS support but started without --tls:
        # FEAT lists AUTH TLS and the server then rejects it. FEAT is necessary
        # but not sufficient -- a rejected AUTH TLS still has to be handled.
        warn("server advertises AUTH TLS in FEAT but rejects it in practice")
        info("do not treat the FEAT listing alone as proof that TLS is usable")

    if plaintext_ok:
        info("plaintext logins are still accepted here")
    if cert_ok is False:
        info("certificate does not verify; use a trusted cert or pin the fingerprint above")


def main():
    """FTP/FTPS compatibility and configuration diagnostic."""
    parser = ArgumentParser(description=main.__doc__)
    parser.add_argument('-u', '--user', required=True, help="FTP user account")
    parser.add_argument('-H', '--host', required=True, help='FTP hostname')
    parser.add_argument('-p', '--port', type=int, default=21, help='FTP port (default: 21)')
    parser.add_argument('-k', '--insecure', action='store_true',
                        help="Disable TLS certificate verification for the functional checks. "
                             "Certificate verification is still reported separately.")
    parser.add_argument('-t', '--timeout', type=float, default=30.0,
                        help='Socket timeout in seconds (default: 30)')
    parser.add_argument('--ftps', action='store_true',
                        help="Skip the plaintext checks and only exercise FTPS")
    parser.add_argument('--password-env', metavar='VAR',
                        help="Read the password from this environment variable "
                             "instead of prompting, for unattended runs")
    parser.add_argument('--upload', metavar='NAME', nargs='?', const='gutils_ftps_check.txt',
                        help="Also perform a write test, uploading and then deleting NAME. "
                             "Off by default: every other check is read-only.")
    args = parser.parse_args()

    args.verify = not args.insecure

    if args.password_env:
        try:
            password = os.environ[args.password_env]
        except KeyError:
            parser.error(f"environment variable {args.password_env} is not set")
    else:
        password = getpass("Enter password: ")

    if not check_reachable(args):
        return EXIT_UNUSABLE

    advertises_tls = check_feat(args)

    plaintext_ok = False
    if not args.ftps:
        plaintext_ok = check_plaintext(args, password)

    cert_ok = check_cert_verification(args)
    result = check_ftps(args, password)

    if args.upload and result != "broken":
        check_upload(args, password, needs_reuse=(result == "needs-reuse"))

    summarize(result, advertises_tls, plaintext_ok, cert_ok)

    return {
        "ok": EXIT_OK,
        "needs-reuse": EXIT_NEEDS_REUSE,
    }.get(result, EXIT_UNUSABLE)


if __name__ == "__main__":
    sys.exit(main())
