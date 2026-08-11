# Pytest Performance Optimization Strategy

## Test Suite Acceleration Architecture

```mermaid
graph TD
    subgraph Strategy ["Pytest Performance Optimization Layers"]
        P1["1. Parallel Execution (pytest-xdist)\npytest -n auto"]
        P2["2. Fast Package & Env Manager (uv)\nuv run pytest"]
        P3["3. Test Profiling & Bottleneck Tracking\npytest --durations=10"]
        P4["4. In-Memory Storage & Scope Reuse\n(SQLite :memory: & session-scoped fixtures)"]
        P5["5. Fast Collection Optimization\n(testpaths & norecursedirs in pyproject.toml)"]
    end

    P1 --> Speed["⚡ 3x - 8x Faster Execution"]
    P2 --> Speed
    P3 --> Speed
    P4 --> Speed
    P5 --> Speed
```

## Key Optimization Tools Summary

| Tool / Strategy | Type | Speed Impact | Purpose |
| :--- | :--- | :---: | :--- |
| **`pytest-xdist`** | Pytest Plugin | 🚀 **High (3x - 8x)** | Spreads test execution across all available CPU cores (`pytest -n auto`). |
| **`uv` (`uv run pytest`)** | Package Manager | ⚡ **Medium** | Rust-based venv & package runner; eliminates Python startup overhead. |
| **`pytest --durations=10`** | CLI Flag | 🔍 **Diagnostic** | Instantly surfaces the top 10 slowest tests in your codebase. |
| **SQLite `:memory:` DB** | Architecture | ⚡ **High** | Runs DB integration tests entirely in RAM instead of reading/writing to disk. |
| **`pyproject.toml` configuration** | Configuration | ⚡ **Medium** | Restricts `testpaths = ["tests"]` to skip scanning non-test directories. |
