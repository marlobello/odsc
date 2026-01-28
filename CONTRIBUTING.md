# Contributing to OneDrive Sync Client (ODSC)

Thank you for your interest in contributing to ODSC! This document provides guidelines and instructions for contributing.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/odsc.git`
3. Create a branch: `git checkout -b feature/your-feature-name`
4. Make your changes
5. Test your changes
6. Commit your changes: `git commit -am 'Add new feature'`
7. Push to your fork: `git push origin feature/your-feature-name`
8. Create a Pull Request

## Development Setup

```bash
# Install in development mode
pip3 install -e .

# Install development dependencies
pip3 install pytest black flake8
```

## Code Style

- Follow PEP 8 guidelines
- Use type hints where appropriate
- Add docstrings to functions and classes
- Keep functions focused and modular

## Testing

Before submitting a PR:

1. Test the daemon: `python3 -m odsc.daemon`
2. Test the GUI: `python3 -m odsc.gui`
3. Verify authentication flow works
4. Test file upload and download functionality

## Areas for Contribution

- **Tests**: Add unit tests and integration tests
- **Features**: 
  - Two-way sync (download changes from OneDrive)
  - Conflict resolution
  - Bandwidth limiting
  - File filters/exclusions
- **UI Improvements**:
  - Progress bars for uploads/downloads
  - System tray icon
  - Notifications
- **Documentation**: Improve README, add tutorials
- **Bug Fixes**: Check the Issues page

## Reporting Bugs

When reporting bugs, please include:

- Operating system and version
- Python version
- Steps to reproduce
- Expected behavior
- Actual behavior
- Error messages or logs

## Feature Requests

Feature requests are welcome! Please:

- Check if the feature has already been requested
- Clearly describe the feature and its use case
- Explain why it would be valuable to users

## Questions?

Feel free to open an issue for questions or discussions.

Thank you for contributing!
