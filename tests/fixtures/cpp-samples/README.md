# cpp-samples

Fixture C++ sources used by methoddep unit/integration tests. Each
subdirectory exercises a specific analyzer concern.

| Fixture | Exercises |
|---------|-----------|
| `interface_impl`    | Pure virtual interface + `src/acme`/`src/globex` customer variants + gmock mock |
| `with_deps`         | Multi-dep class/struct, enum, globals, static locals, ordered calls |
| `templated`         | Template class + template free function |
| `free_functions`    | Non-member functions in namespaces |
| `gmock_pattern`     | Real mock `: public IClient` + decoy same-name class (no inheritance) |
| `pch_project`       | Sources that rely on a precompiled header (`/Yu`) |
| `unity_build`       | Jumbo `.cpp` that `#include`s other `.cpp`s |
| `generated_sources` | `moc_*.cpp`, `.rc` — must be skipped |
| `mfc_atl_macros`    | MFC-style macro preambles |

These trees do not ship a CMakeLists; analyzers are pointed at them
directly via methoddep config.
