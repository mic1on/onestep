.PHONY: docker-build docker-build-api docker-build-frontend docker-push docker-push-api docker-push-frontend docker-print-images \
	docker-build-multi-arch docker-build-api-multi-arch docker-build-frontend-multi-arch docker-buildx-ensure-builder

IMAGE_REPOSITORY ?=
IMAGE_TAG ?= $(shell git rev-parse --short HEAD)
API_IMAGE_NAME ?= onestep-control-plane-api
FRONTEND_IMAGE_NAME ?= onestep-control-plane-frontend
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
API_IMAGE := $(IMAGE_PREFIX)$(API_IMAGE_NAME):$(IMAGE_TAG)
FRONTEND_IMAGE := $(IMAGE_PREFIX)$(FRONTEND_IMAGE_NAME):$(IMAGE_TAG)

docker-build: docker-build-api docker-build-frontend

docker-build-api:
	docker build --target api -t $(API_IMAGE) .

docker-build-frontend:
	docker build --target frontend -t $(FRONTEND_IMAGE) .

docker-push: docker-push-api docker-push-frontend

docker-push-api:
	docker push $(API_IMAGE)

docker-push-frontend:
	docker push $(FRONTEND_IMAGE)

docker-print-images:
	@printf 'API_IMAGE=%s\n' "$(API_IMAGE)"
	@printf 'FRONTEND_IMAGE=%s\n' "$(FRONTEND_IMAGE)"

# Multi-architecture build targets (amd64 + arm64)
# Note: Multi-arch builds require --push to registry, cannot create local images
docker-build-multi-arch: docker-build-api-multi-arch docker-build-frontend-multi-arch

docker-buildx-ensure-builder:
	@docker buildx inspect $(BUILDX_BUILDER) >/dev/null 2>&1 || docker buildx create --name $(BUILDX_BUILDER) --driver $(BUILDX_DRIVER) $(BUILDX_CREATE_OPTS) --use
	@docker buildx inspect --bootstrap $(BUILDX_BUILDER) >/dev/null

docker-build-api-multi-arch: docker-buildx-ensure-builder
	docker buildx build --builder $(BUILDX_BUILDER) --platform $(BUILDX_PLATFORMS) \
		$(BUILDX_BUILD_ARGS) \
		--target api \
		-t $(API_IMAGE) \
		--push \
		.

docker-build-frontend-multi-arch: docker-buildx-ensure-builder
	docker buildx build --builder $(BUILDX_BUILDER) --platform $(BUILDX_PLATFORMS) \
		$(BUILDX_BUILD_ARGS) \
		--target frontend \
		-t $(FRONTEND_IMAGE) \
		--push \
		.
