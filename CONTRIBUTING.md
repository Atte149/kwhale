# Contributing to KWhale

Thank you for your interest in contributing to KWhale! This document provides guidelines and instructions for contributing.

## Code of Conduct

Be respectful, inclusive, and constructive. We're building this together.

## How to Contribute

### Reporting Bugs

Before creating a bug report:
1. Check existing issues to avoid duplicates
2. Collect relevant information (logs, environment, steps to reproduce)

Create a bug report with:
- Clear, descriptive title
- Detailed description of the issue
- Steps to reproduce
- Expected vs actual behavior
- Environment details (OS, Docker version, etc.)
- Relevant logs or error messages

### Suggesting Features

Feature requests are welcome! Please:
1. Check if the feature has already been requested
2. Explain the use case and why it would be valuable
3. Describe the proposed solution or behavior
4. Consider implementation complexity and maintenance burden

### Pull Requests

1. **Fork and clone** the repository
2. **Create a branch** for your changes: `git checkout -b feature/my-feature`
3. **Make your changes** following the code style guidelines
4. **Test your changes** thoroughly
5. **Commit** with clear, descriptive messages
6. **Push** to your fork: `git push origin feature/my-feature`
7. **Open a pull request** with a clear description

#### Pull Request Guidelines

- Keep changes focused and atomic
- Include tests for new functionality
- Update documentation as needed
- Ensure all tests pass
- Follow existing code style and conventions
- Write clear commit messages

## Development Setup

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for detailed development setup instructions.

Quick start:
```bash
git clone https://github.com/yourusername/kwhale.git
cd kwhale
bash scripts/setup.sh
docker compose up -d
```

## Code Style

### Python

- Follow PEP 8
- Use type hints for function signatures
- Write docstrings for public functions and classes
- Keep functions focused and small (< 50 lines ideally)
- Use async/await for I/O operations
- Prefer explicit over implicit

Example:
```python
async def search_library(query: str, limit: int = 30) -> list[dict]:
    """Search the music library by title, artist, or album.
    
    Args:
        query: Search query string
        limit: Maximum number of results (default: 30)
        
    Returns:
        List of track dictionaries with id, title, artist, album
    """
    # Implementation
    pass
```

### Naming Conventions

- Functions/variables: `snake_case`
- Classes: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`
- Private methods: `_leading_underscore`

### Imports

Group imports in this order:
1. Standard library
2. Third-party packages
3. Local modules

```python
import os
from typing import Any

import httpx
from fastapi import FastAPI

from .config import settings
from .db import get_pool
```

## Testing

### Running Tests

```bash
# API tests
docker compose exec api pytest -v

# Worker tests
docker compose exec worker pytest -v

# With coverage
docker compose exec worker pytest --cov=app tests/
```

### Writing Tests

- Use pytest with async support
- Write tests for new features and bug fixes
- Aim for good coverage of critical paths
- Use fixtures for common setup

Example:
```python
import pytest

@pytest.mark.asyncio
async def test_search_library(db_pool):
    """Test library search returns expected results."""
    results = await search_library("test query")
    assert isinstance(results, list)
    assert len(results) > 0
```

## Adding Source Plugins

To add a new music source:

1. Create `worker/app/providers/my_source.py`
2. Subclass `BaseProvider`
3. Implement `search()`, `resolve()`, `download()`
4. Set `name` class attribute
5. Add tests in `worker/tests/`
6. Update documentation

Example:
```python
from .base import BaseProvider

class MySourceProvider(BaseProvider):
    name = "my_source"
    
    async def search(self, query: str, limit: int = 10) -> list[dict]:
        """Search for tracks."""
        # Implementation
        return results
    
    async def resolve(self, provider_id: str) -> dict:
        """Get track details."""
        # Implementation
        return track_info
    
    async def download(self, provider_id: str, output_path: str) -> str:
        """Download track."""
        # Implementation
        return output_path
```

## Documentation

- Update relevant documentation when adding features
- Keep README.md up to date
- Add docstrings to new functions and classes
- Update API.md for new endpoints
- Include examples where helpful

## Commit Messages

Write clear, descriptive commit messages:

```
Add semantic search endpoint

- Implement /api/search/semantic endpoint
- Add lyrics embedding query support
- Include tests for semantic search
- Update API documentation
```

Format:
- First line: Brief summary (50 chars or less)
- Blank line
- Detailed description with bullet points if needed

## Review Process

1. Maintainers will review your PR
2. Address any feedback or requested changes
3. Once approved, your PR will be merged
4. Your contribution will be credited in release notes

## Questions?

- Open a [GitHub Discussion](https://github.com/yourusername/kwhale/discussions)
- Check existing [documentation](docs/)
- Review [closed issues](https://github.com/yourusername/kwhale/issues?q=is%3Aissue+is%3Aclosed)

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

Thank you for contributing to KWhale! 🎵
