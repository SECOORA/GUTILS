#!/bin/bash
# Build a vsftpd config from environment variables. Every knob that matters to
# the FTPS compatibility matrix is exposed so one image can serve all three
# cases in .claude/recommendations/01-ftp-reuse.md ("Testing"):
#
#   SSL_ENABLE=NO                        -> plaintext only, exercises the downgrade path
#   SSL_ENABLE=YES REQUIRE_SSL_REUSE=YES -> reproduces the data-channel session reuse bug
#   SSL_ENABLE=YES + self-signed cert    -> client must fail loudly, not downgrade
set -euo pipefail

FTP_USER_NAME="${FTP_USER_NAME:-thisisme}"
FTP_USER_PASS="${FTP_USER_PASS:-thisismypass}"
FTP_USER_HOME="${FTP_USER_HOME:-/home/ftpusers/${FTP_USER_NAME}}"
PUBLICHOST="${PUBLICHOST:-localhost}"
PASV_MIN_PORT="${PASV_MIN_PORT:-30000}"
PASV_MAX_PORT="${PASV_MAX_PORT:-30009}"
SSL_ENABLE="${SSL_ENABLE:-YES}"
# vsftpd's own default is YES. Set explicitly so the setting under test is
# visible in the compose file rather than implied.
REQUIRE_SSL_REUSE="${REQUIRE_SSL_REUSE:-YES}"
# With ssl_enable=YES these force TLS for logins/data, so a cleartext client is
# rejected instead of silently succeeding.
FORCE_LOCAL_LOGINS_SSL="${FORCE_LOCAL_LOGINS_SSL:-YES}"
FORCE_LOCAL_DATA_SSL="${FORCE_LOCAL_DATA_SSL:-YES}"
TLS_CN="${TLS_CN:-${PUBLICHOST}}"

# vsftpd authenticates through PAM, and Debian's /etc/pam.d/vsftpd includes
# pam_shells -- so the login shell has to be one listed in /etc/shells.
if ! id -u "${FTP_USER_NAME}" >/dev/null 2>&1; then
    useradd --create-home --home-dir "${FTP_USER_HOME}" --shell /bin/sh "${FTP_USER_NAME}"
fi
echo "${FTP_USER_NAME}:${FTP_USER_PASS}" | chpasswd
mkdir -p "${FTP_USER_HOME}"
chown "${FTP_USER_NAME}:${FTP_USER_NAME}" "${FTP_USER_HOME}"

CERT=/etc/ssl/private/vsftpd.pem
if [ "${SSL_ENABLE}" = "YES" ] && [ ! -f "${CERT}" ]; then
    # Self-signed on purpose: case 3 of the matrix needs a cert that a verifying
    # client rejects. subjectAltName is set so that turning verification on
    # fails on the untrusted issuer rather than on a missing SAN.
    openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
        -keyout "${CERT}" -out "${CERT}" \
        -subj "/C=US/O=GUTILS/CN=${TLS_CN}" \
        -addext "subjectAltName=DNS:${TLS_CN},DNS:localhost,IP:127.0.0.1" 2>/dev/null
    chmod 600 "${CERT}"
fi

cat > /etc/vsftpd.conf <<EOF
listen=YES
listen_ipv6=NO
background=NO
anonymous_enable=NO
local_enable=YES
write_enable=YES
local_umask=022
chroot_local_user=YES
allow_writeable_chroot=YES
# vsftpd's seccomp filter kills the process on unexpected syscalls, which is a
# common false positive under container runtimes.
seccomp_sandbox=NO

pasv_enable=YES
pasv_min_port=${PASV_MIN_PORT}
pasv_max_port=${PASV_MAX_PORT}
pasv_address=${PUBLICHOST}
pasv_addr_resolve=YES

ssl_enable=${SSL_ENABLE}
require_ssl_reuse=${REQUIRE_SSL_REUSE}
force_local_logins_ssl=${FORCE_LOCAL_LOGINS_SSL}
force_local_data_ssl=${FORCE_LOCAL_DATA_SSL}
rsa_cert_file=${CERT}
rsa_private_key_file=${CERT}
ssl_ciphers=HIGH

# Log the full FTP dialogue so 'docker compose logs ftps' shows the
# AUTH/PBSZ/PROT exchange and the 522 when session reuse is refused.
# vsftpd opens this file after dropping privileges, so it cannot be /dev/stdout;
# a tail below relays it to the container's stdout instead.
xferlog_enable=YES
log_ftp_protocol=YES
vsftpd_log_file=/var/log/vsftpd.log
syslog_enable=NO
EOF

# vsftpd's privilege-separation chroot. /var/run is tmpfs, so the directory the
# Debian package ships is gone by the time the container starts.
mkdir -p /var/run/vsftpd/empty

touch /var/log/vsftpd.log
tail -n +1 -F /var/log/vsftpd.log &

# Deliberately not exec'd. vsftpd segfaults on the first successful login when
# it is PID 1 -- it is not written to be an init process. Keeping the shell as
# PID 1 costs nothing here and makes the container survive logins.
/usr/sbin/vsftpd /etc/vsftpd.conf &
VSFTPD_PID=$!
trap 'kill -TERM "${VSFTPD_PID}" 2>/dev/null' TERM INT
wait "${VSFTPD_PID}"
