# config-guard provenance

`hooks/config_guard.py` adapts the MIT-licensed
[config-guard by Karan Bansal](https://github.com/karanb192/claude-code-hooks/blob/57a28c7687305e4a9b9dd50490e9b3c552c71ca4/plugins/config-guard/config-guard.js),
pinned to commit `57a28c7687305e4a9b9dd50490e9b3c552c71ca4` (plugin version 1.0.0).
Copyright (c) 2026 Karan Bansal. The original license accompanies this notice.

The protected-path taxonomy, pattern identifiers, level ordering, mutation
families, Claude CLI configuration-write grammar and symlink-resolution approach
are ported to Python. Tests adapt upstream scenarios from
[the pinned test suite](https://github.com/karanb192/claude-code-hooks/blob/57a28c7687305e4a9b9dd50490e9b3c552c71ca4/plugins/config-guard/tests/config-guard.test.js).

Local changes: use shfmt AST commands/redirections instead of matching an entire
command string; resolve missing nested paths; protect actual configuration and
plugin locations and known location environment aliases; recognize static command prefixes, git restore/checkout and
chmod/chown; require successful private, content-hash-only audit before denial.
No Node runtime, network calls, upstream raw-target logging, ask mode or
`CONFIG_GUARD_ALLOW` escape hatch is used. Failures produce no decision.

Known limits: this is not an OS security boundary. Unknown tools, external
scripts, interpreter code, dynamic variable paths and runtime directory changes
can evade classification, including path changes through `env --chdir`.
Static cd handling is conservative and may overmatch across branches/subshells.
Common copy/link grammar checks only destinations; unfamiliar options retain
conservative operand checks and may include read sources. Moving a protected
source is still mutation. Existing native permission and
destructive-command guards remain necessary. Fail-open intentionally means
parser/runtime/audit failures do not enforce this guard. Audit records and
session state are not rotated or removed; retain them for at least 186 days.
