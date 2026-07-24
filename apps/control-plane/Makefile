.PHONY: docker-build docker-build-plane docker-build-api docker-build-frontend docker-push docker-push-plane docker-push-api docker-push-frontend docker-print-images \
	docker-build-multi-arch docker-build-plane-multi-arch docker-build-api-multi-arch docker-build-frontend-multi-arch docker-buildx-ensure-builder \
	release-preflight release-migrate release-up release-smoke release-down

IMAGE_REPOSITORY ?=
IMAGE_TAG ?= $(shell git rev-parse --short HEAD)
PLANE_IMAGE_NAME ?= onestep-control-plane
DOCKER_CONTEXT ?= $(shell docker context show 2>/dev/null || printf default)
BUILDX_PROXY_SUFFIX := $(if $(or $(HTTP_PROXY),$(HTTPS_PROXY),$(ALL_PROXY)),-proxy,)
BUILDX_BUILDER ?= multiarch-builder-$(DOCKER_CONTEXT)$(BUILDX_PROXY_SUFFIX)
BUILDX_DRIVER ?= docker-container
BUILDX_PLATFORMS ?= linux/amd64,linux/arm64
BUILDX_PROXY_HOST ?= host.docker.internal
DOCKER_HTTP_PROXY := $(subst localhost,$(BUILDX_PROXY_HOST),$(subst 127.0.0.1,$(BUILDX_PROXY_HOST),$(HTTP_PROXY)))
DOCKER_HTTPS_PROXY := $(subst localhost,$(BUILDX_PROXY_HOST),$(subst 127.0.0.1,$(BUILDX_PROXY_HOST),$(HTTPS_PROXY)))
DOCKER_ALL_PROXY := $(subst localhost,$(BUILDX_PROXY_HOST),$(subst 127.0.0.1,$(BUILDX_PROXY_HOST),$(ALL_PROXY)))
BUILDX_CREATE_OPTS :=
BUILDX_CREATE_OPTS += $(if $(DOCKER_HTTP_PROXY),--driver-opt env.http_proxy=$(DOCKER_HTTP_PROXY))
BUILDX_CREATE_OPTS += $(if $(DOCKER_HTTPS_PROXY),--driver-opt env.https_proxy=$(DOCKER_HTTPS_PROXY))
BUILDX_CREATE_OPTS += $(if $(DOCKER_ALL_PROXY),--driver-opt env.all_proxy=$(DOCKER_ALL_PROXY))
BUILDX_CREATE_OPTS += $(if $(NO_PROXY),--driver-opt env.no_proxy=$(NO_PROXY))
BUILDX_BUILD_ARGS :=
BUILDX_BUILD_ARGS += $(if $(DOCKER_HTTP_PROXY),--build-arg HTTP_PROXY=$(DOCKER_HTTP_PROXY))
BUILDX_BUILD_ARGS += $(if $(DOCKER_HTTPS_PROXY),--build-arg HTTPS_PROXY=$(DOCKER_HTTPS_PROXY))
BUILDX_BUILD_ARGS += $(if $(DOCKER_ALL_PROXY),--build-arg ALL_PROXY=$(DOCKER_ALL_PROXY))
BUILDX_BUILD_ARGS += $(if $(NO_PROXY),--build-arg NO_PROXY=$(NO_PROXY))

IMAGE_PREFIX := $(if $(IMAGE_REPOSITORY),$(IMAGE_REPOSITORY)/,)
PLANE_IMAGE := $(IMAGE_PREFIX)$(PLANE_IMAGE_NAME):$(IMAGE_TAG)
COMPOSE_FILE ?= docker-compose.yml
ENV_FILE ?= .env
SMOKE_BUILD ?= 0
SMOKE_MANAGE_STACK ?= 0
SMOKE_CLEANUP ?= 0

docker-build: docker-build-plane

docker-build-plane:
	docker build --target plane -t $(PLANE_IMAGE) .

docker-build-api docker-build-frontend: docker-build-plane

docker-push: docker-push-plane

docker-push-plane:
	docker push $(PLANE_IMAGE)

docker-push-api docker-push-frontend: docker-push-plane

docker-print-images:
	@printf 'PLANE_IMAGE=%s\n' "$(PLANE_IMAGE)"

# Multi-architecture build targets (amd64 + arm64)
# Note: Multi-arch builds require --push to registry, cannot create local images
docker-build-multi-arch: docker-build-plane-multi-arch

docker-buildx-ensure-builder:
	@docker buildx inspect $(BUILDX_BUILDER) >/dev/null 2>&1 || docker buildx create --name $(BUILDX_BUILDER) --driver $(BUILDX_DRIVER) $(BUILDX_CREATE_OPTS) --use
	@docker buildx inspect --bootstrap $(BUILDX_BUILDER) >/dev/null

docker-build-plane-multi-arch: docker-buildx-ensure-builder
	docker buildx build --builder $(BUILDX_BUILDER) --platform $(BUILDX_PLATFORMS) \
		$(BUILDX_BUILD_ARGS) \
		--target plane \
		-t $(PLANE_IMAGE) \
		--push \
		.

docker-build-api-multi-arch docker-build-frontend-multi-arch: docker-build-plane-multi-arch

release-preflight:
	bash scripts/release-preflight.sh --compose-file $(COMPOSE_FILE) --env-file $(ENV_FILE)

release-migrate:
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) run --rm migrate

release-up:
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) up -d --build plane

release-smoke:
	SMOKE_BUILD=$(SMOKE_BUILD) SMOKE_MANAGE_STACK=$(SMOKE_MANAGE_STACK) SMOKE_CLEANUP=$(SMOKE_CLEANUP) \
		bash scripts/run-smoke.sh --compose-file $(COMPOSE_FILE) --env-file $(ENV_FILE)

release-down:
	docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE) down --remove-orphans
