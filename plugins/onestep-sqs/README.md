# onestep-sqs

Amazon SQS connector plugin for `onestep`.

```bash
pip install onestep-sqs
```

The package registers these YAML resource types through the `onestep.resources`
entry point:

- `sqs`
- `sqs_queue`

Python usage:

```python
from onestep_sqs import SQSConnector
```
