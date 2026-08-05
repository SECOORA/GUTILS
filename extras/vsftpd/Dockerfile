# vsftpd test target for the FTP -> FTPS migration.
#
# The point of this image is `require_ssl_reuse`, which vsftpd defaults to YES
# and which Python's stdlib ftplib cannot satisfy (it does not pass session= when
# wrapping the data socket). pure-ftpd has no equivalent option, so it cannot
# reproduce the failure. See .claude/recommendations/01-ftp-reuse.md item 1.
FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends vsftpd openssl \
 && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 21
ENTRYPOINT ["/entrypoint.sh"]
