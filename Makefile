# Pull GLIDER_DATA_VOLUME (and anything else) out of .env so the paths used
# here match what docker compose mounts.
-include .env

GLIDER_DATA_VOLUME ?= /mnt/gliders
DEPLOYMENTS_DIR := $(GLIDER_DATA_VOLUME)/secoora/dev/deployments

# Newest example deployment shipped in extras/.
SAMPLE_DATA := $(lastword $(sort $(wildcard extras/salacia*.tar.gz)))

# Seconds to let the FTP services and the gutils watchers settle before
# dropping data in front of them.
INTEGRATION_WAIT ?= 15

.PHONY: docker-local integration-test

docker-local:
	docker build -t gutils:latest .

integration-test: docker-local
	@test -n "$(SAMPLE_DATA)" || { echo "no sample data matching extras/salacia*.tar.gz"; exit 1; }
	mkdir -p \
		$(GLIDER_DATA_VOLUME)/ftp \
		$(GLIDER_DATA_VOLUME)/ftps \
		$(GLIDER_DATA_VOLUME)/ftps-noreuse \
		$(DEPLOYMENTS_DIR)
	docker compose up -d
	@echo "waiting $(INTEGRATION_WAIT)s for the services to come up..."
	@sleep $(INTEGRATION_WAIT)
	tar -C $(DEPLOYMENTS_DIR) -zxvf $(SAMPLE_DATA)
	@echo
	@echo "sample data extracted to $(DEPLOYMENTS_DIR)"
	@echo "watch processing with: docker compose logs -f gutils"
	@echo "uploaded profiles land in: $(GLIDER_DATA_VOLUME)/ftps-noreuse/"
