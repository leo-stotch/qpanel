.PHONY: buildx-setup buildx-build buildx-build-windows buildx-push

IMAGE ?= ghcr.io/$(shell basename $(shell git rev-parse --show-toplevel))/qpanel
TAGS ?= latest
PLATFORMS ?= linux/amd64,linux/arm64

buildx-setup:
	docker buildx create --use --name qpanel-builder || true
	docker run --privileged --rm tonistiigi/binfmt --install all

buildx-build:
	docker buildx build --platform $(PLATFORMS) -t $(IMAGE):$(TAGS) --push .

buildx-build-windows:
	docker buildx build --platform windows/amd64 -f Dockerfile.windows -t $(IMAGE):windows-ltsc2022 --push .

buildx-push: buildx-build

