# Contributing

Contributions are welcome! If you find any issues or have suggestions for improvement, please open an issue or submit a pull request on the GitHub repository.

## Development Setup

1. Clone the repository
2. Install [uv](https://docs.astral.sh/uv/) if you haven't already:

    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

3. Install development dependencies:

    ```bash
    uv sync --all-groups
    ```

## Running Tests

To run the test suite:

```bash
uv run pytest
```

Or with coverage:

```bash
uv run pytest --cov=nshconfig --cov-report=term-missing
```

## Building Documentation

To build the documentation locally:

```bash
cd docs
uv run sphinx-build -b html source build/html
```

The built documentation will be in `docs/build/html`.
