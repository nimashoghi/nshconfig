# Contributing

Contributions are welcome! If you find any issues or have suggestions for improvement, please open an issue or submit a pull request on the GitHub repository.

## Development Setup

1. Clone the repository
2. Install development dependencies:

   ```bash
   pip install -e ".[dev]"
   ```

3. Install pre-commit hooks:

   ```bash
   pre-commit install
   ```

## Running Tests

To run the test suite:

```bash
pytest
```

## Building Documentation

To build the documentation locally:

```bash
cd docs
make html
```

The built documentation will be in `docs/build/html`.
