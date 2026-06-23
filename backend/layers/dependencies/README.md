# Shared Lambda Layer — Dependencies

This layer provides pydantic-ai and related packages to the AI segmentation
and transition detection Lambdas.

## Critical: ARM64 Build

The SAM template **must** include `BuildArchitecture: arm64` in the layer's
Metadata section. Without this, SAM builds x86_64 binaries that fail at
runtime with:

```
No module named 'pydantic_core._pydantic_core'
```

### Correct template definition:

```yaml
PipelineDependenciesLayer:
  Type: AWS::Serverless::LayerVersion
  Properties:
    LayerName: !Sub 'autolabel-deps-${Environment}'
    ContentUri: ../layers/dependencies/
    CompatibleRuntimes:
      - python3.12
    CompatibleArchitectures:
      - arm64
    RetentionPolicy: Delete
  Metadata:
    BuildMethod: python3.12
    BuildArchitecture: arm64    # ← REQUIRED for pydantic_core
```

### Build command:

```bash
sam build --use-container  # Builds inside Docker with correct architecture
```

### Verify after build:

```bash
ls .aws-sam/build/PipelineDependenciesLayer/python/pydantic_core/*.so
# Must show: _pydantic_core.cpython-312-aarch64-linux-gnu.so
# NOT:      _pydantic_core.cpython-312-x86_64-linux-gnu.so
```

### Manual layer build (fallback):

If SAM builds the wrong architecture despite the config:

```bash
rm -rf /tmp/layer && mkdir -p /tmp/layer/python

python3 -m pip install pydantic "pydantic-ai-slim[bedrock]" python-dotenv rich \
  --target /tmp/layer/python \
  --platform manylinux2014_aarch64 \
  --python-version 3.12 \
  --implementation cp \
  --only-binary=:all:

cd /tmp/layer && zip -r /tmp/pipeline-layer.zip python/

aws lambda publish-layer-version \
  --layer-name autolabel-deps-prod \
  --zip-file fileb:///tmp/pipeline-layer.zip \
  --compatible-runtimes python3.12 \
  --compatible-architectures arm64 \
  --profile <PROFILE>
```
