#!/usr/bin/env bash
# Publish KARN to PyPI and npm
# Usage: ./publish.sh [--test]
#   --test  = upload to test servers (testpypi, npm --dry-run)

set -e

TEST=false
if [ "$1" = "--test" ]; then
    TEST=true
fi

echo "=== Building KARN for distribution ==="

# Clean previous builds
rm -rf dist/ build/ *.egg-info

# ── PyPI ──
echo ""
echo "=== Building PyPI package ==="
python3 -m pip install --upgrade build twine
python3 -m build

if [ "$TEST" = true ]; then
    echo "Uploading to TestPyPI..."
    python3 -m twine upload --repository testpypi dist/*
    echo "Install from TestPyPI: pip install --index-url https://test.pypi.org/simple/ karn-lang"
else
    echo "Uploading to PyPI..."
    python3 -m twine upload dist/*
    echo "Install from PyPI: pip install karn-lang"
fi

# ── npm ──
echo ""
echo "=== Building npm package ==="

if [ "$TEST" = true ]; then
    echo "Dry-run npm publish..."
    npm publish --dry-run
else
    echo "Publishing to npm..."
    npm publish
    echo "Install from npm: npm install karn-lang"
fi

echo ""
echo "=== Done ==="
echo "PyPI:    https://pypi.org/project/karn-lang/"
echo "npm:     https://www.npmjs.com/package/karn-lang"
echo "GitHub:  https://github.com/karn-lang/karn"
