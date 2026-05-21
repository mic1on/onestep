FROM onestep-worker-local:test

WORKDIR /workspace

COPY tests/assets/worker_image/mounted_pyproject/pyproject.toml /workspace/pyproject.toml
COPY tests/assets/worker_image/mounted_pyproject/worker.yaml /workspace/worker.yaml
COPY tests/assets/worker_image/mounted_pyproject/src/worker_image_demo_pyproject /workspace/src/worker_image_demo_pyproject

ENV ONESTEP_TARGET=/workspace/worker.yaml
