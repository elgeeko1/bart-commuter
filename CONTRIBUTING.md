# Contributing

Thanks for helping make BART Commuter better. Bug reports, ideas, and pull requests are all welcome, and small fixes need no discussion first.

## Report a problem or suggest an idea

Open an [issue](https://github.com/elgeeko1/bart-commuter/issues). For a wrong or missing train, include your station codes, the widget size, and when you saw it. Report security problems as described in [SECURITY.md](SECURITY.md).

## Make a change

For a large change, open an issue first so we can agree on the approach.

1. Fork the repository and create a branch from `main`.
1. Install Python 3.12 or later, then run `make config` to install the tools and pre-commit hooks.
1. Make your change, with a test for any behavior it changes.
1. Run `make check`.
1. Open a pull request against `main` that says what changed and why.

## Guidelines

- The widget runs only in [Scriptable](https://docs.scriptable.app), with no dependencies.
- Anything a rider sees follows the [product guidelines](doc/PRODUCT_GUIDELINES.md). For a layout change, attach images from `make gallery` (macOS) and check it on an iPhone if you can.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org).
- [doc/DEVELOPMENT.md](doc/DEVELOPMENT.md) explains how the widget works and how to test it.

## License

By contributing, you agree that your work is licensed under this project's [BSD 3-Clause License](LICENSE).
