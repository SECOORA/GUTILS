import os
import ftplib
import ssl


class TLSReusedSessionFTP(ftplib.FTP_TLS):
    """FTP_TLS that resumes the control connection's TLS session on the data
    connection.

    When vsftpd is configured with require_ssl_reuse=YES and by IIS, the service
    requires the client to reuse TLS sessions in the data connection. The stdlib
    does not support this: FTP_TLS.ntransfercmd wraps the data socket with no
    session= argument, so the server sees an unrelated TLS session and refuses
    the transfer.
    """
    def ntransfercmd(self, cmd, rest = None):
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(
                conn,
                server_hostname=self.host,
                session=self.sock.session,
            )
        return conn, size


def connect_ftp_auto(
    host: str,
    port: int = 21,
    user: str = "",
    passwd: str = "",
    timeout: float = 30.0,
    verify: bool = True
):
    ctx = ssl.create_default_context()
    if "GUTILS_FTPS_NO_VERIFY" in os.environ or not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    ftps = TLSReusedSessionFTP(context=ctx)
    try:
        ftps.connect(host, port)
        ftps.login(user, passwd)
        ftps.prot_p()
        return ftps
    except ftplib.error_perm as exc:
        # Only downgrade if the server explicitly indicates that AUTH TLS is
        # unsupported
        response = str(exc)

        try:
            ftps.close()
        except Exception:
            pass

        if not _tls_not_supported(response):
            raise

        # Reconnect from sctach as plain FTP
        ftp = ftplib.FTP()
        ftp.connect(host, port, timeout=timeout)
        ftp.login(user, passwd)
        return ftp


def _tls_not_supported(response: str) -> bool:
    response = response.lower()

    return (
        response.startswith(("500 ", "502 ", "504 "))
        and any(
            phrase in response
            for phrase in (
                "not implemented",
                "not supported",
                "unknown command",
                "unrecognized command",
                "security scheme is not implemented",
            )
        )
    )
