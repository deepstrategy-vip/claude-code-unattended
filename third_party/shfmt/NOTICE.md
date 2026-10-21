# shfmt attribution

This project uses the unmodified official `shfmt` v3.14.1 executable from
[mvdan/sh](https://github.com/mvdan/sh/releases/tag/v3.14.1) as a subprocess to
produce a typed shell AST. The executable is downloaded separately by the
installer; the BSD 3-Clause license and copyright notice are preserved in
[LICENSE](LICENSE). Source: [v3.14.1](https://github.com/mvdan/sh/tree/v3.14.1).

`hooks/shell_ast.py` consumes the official JSON AST. The project's rewrite
policy remains separate from the parser. The AST traversal approach was also
informed by the MIT-licensed
[claude-smart-approval](https://github.com/froggeric/claude-smart-approval/tree/60b44fede472983f6dc730a4012f54422b54c102).
No smart-approval runtime, permission approvals, learned allow rules, model
calls, jq filters or logging code are included.
