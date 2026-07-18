# onestep-control-plane

Control-plane reporter and WebSocket command integration plugin for `onestep`.

```bash
pip install onestep-control-plane
```

Most applications should install it through the core extra:

```bash
pip install 'onestep[control-plane]'
```

YAML usage:

```yaml
reporter: true
```

Python usage:

```python
from onestep_control_plane import ControlPlaneReporter, ControlPlaneReporterConfig
```

Compatibility imports also work when this plugin is installed:

```python
from onestep import ControlPlaneReporter, ControlPlaneReporterConfig
from onestep.control_plane_ws import ControlPlaneWsSender
```
