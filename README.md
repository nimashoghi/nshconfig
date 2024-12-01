# nshconfig

Fully typed configuration management, powered by [Pydantic](https://github.com/pydantic/pydantic/).

**[📚 Documentation](https://nima.sh/nshconfig/) | [🔧 Installation Guide](https://nima.sh/nshconfig/installation.html)**

## Overview

`nshconfig` is a Python library that enhances Pydantic with additional features for configuration management, particularly useful for machine learning experiments and other applications requiring strongly typed configurations.

## Installation

```bash
pip install nshconfig
```

To install all optional dependencies, use:

```bash
pip install nshconfig[extra]
```

Please see the [Installation Guide](https://nima.sh/nshconfig/installation.html) for more details.

## Quick Start

```python
import nshconfig as C

class MyConfig(C.Config):
    field1: int
    field2: str
    field3: C.AllowMissing[float] = C.MISSING

config = MyConfig.draft()
config.field1 = 42
config.field2 = "hello"
final_config = config.finalize()
```

## Key Features

- Draft configs for intuitive configuration creation
- Multiple configuration formats (Python, JSON, YAML)
- Dynamic type registry for plugin systems
- Configuration code generation tools
- Built-in PyTorch Lightning integration
- MISSING constant for optional fields

For detailed examples and API reference, please visit the [documentation](https://nima.sh/nshconfig/).

## Contributing

Contributions are welcome! Please see our [Contributing Guide](https://nima.sh/nshconfig/contributing.html) for details.

## Credit

Built on top of the excellent [pydantic](https://github.com/pydantic/pydantic/) library.

## License

MIT License
