.PHONY: docker-build docker-build-api docker-build-frontend docker-push docker-push-api docker-push-frontend docker-print-images \
	docker-build-multi-arch docker-build-api-multi-arch docker-build-frontend-multi-arch

IMAGE_REPOSITORY ?=
IMAGE_TAG ?= $(shell git rev-parse --short HEAD)
API_IMAGE_NAME ?= onestep-control-plane-api
FRONTEND_IMAGE_NAME ?= onestep-control-plane-frontend

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

docker-build-api-multi-arch:
	docker buildx build --platform linux/amd64,linux/arm64 \
		--target api \
		-t $(API_IMAGE) \
		--push \
		.

docker-build-frontend-multi-arch:
	docker buildx build --platform linux/amd64,linux/arm64 \
		--target frontend \
		-t $(FRONTEND_IMAGE) \
		--push \
		.
