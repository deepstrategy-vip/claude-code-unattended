# claude-code-unattended

让 Claude Code 在你不在电脑边时也能一直跑下去，不被弹窗卡住。

> **English:** A few Claude Code hooks and `CLAUDE.md` rules for unattended `bypassPermissions` sessions. They don't bypass any safety check. Instead they make Claude rewrite commands so they pass the built-in checks, and they turn "click to start" side-task cards into background subagents that are merged or deleted when done.

## 为什么需要

开了 bypass permissions，Claude Code 还是会为下面几类操作弹窗等人确认：

| 你看到的弹窗 | 触发它的写法 |
|---|---|
| `Dangerous rm operation on possibly-empty variable path` | `rm -rf $S/pgdata`：变量为空时会变成删根目录 |
| `This shell -c script runs rm and could not be checked` | `bash -c "..."`、`nohup zsh -c "..." &`：检查器看不透字符串里的脚本 |
| 开分支任务的卡片（桌面版 Code 标签页） | Claude 发现范围外的事时用 spawn_task 留的卡片，要点一下才开始 |

前两类是 Claude Code 有意保留的安全检查，`permissions.allow` 规则和 PreToolUse hook 返回 `allow` 都关不掉（见[官方文档](https://code.claude.com/docs/en/permission-modes#critical-paths)）。人不在电脑边，会话就一直卡在那里。

## 它做了什么

| 组件 | 作用 |
|---|---|
| [`hooks/bash_guard.py`](hooks/bash_guard.py) | Bash 命令执行前检查。发现会触发上述检查的写法，直接拒绝，并把具体改写方法告诉 Claude。Claude 改完重跑，不弹窗 |
| [`hooks/spawn_task_redirect.py`](hooks/spawn_task_redirect.py) | 拒绝创建分支任务卡片，让 Claude 改用后台子代理在独立 worktree 里做。做完由主会话合并或删除，分支不会留着 |
| [`rules/CLAUDE.md`](rules/CLAUDE.md) | 把同样的规则写给 Claude，让它一开始就写对。hook 只是兜底 |
| `inputNeededNotifEnabled` | 有弹窗或提问在等你时推送到手机。会话开了 Remote Control 的话，可以直接在 Claude App 里处理 |

### bash_guard 拦什么

| 原来的写法 | 让 Claude 改成 |
|---|---|
| `rm -rf $S/pgdata $S/*.tsv` | `rm -rf "${S:?}"/pgdata "${S:?}"/*.tsv` |
| `rm -rf $(cat dirfile)`、`` rm -rf `cmd` `` | 先单独运行那条命令，再用打印出来的字面路径删除 |
| `rm -rf "${HOME:?}"/x/*` | 字面绝对路径（`$HOME` 总是有值，加 `:?` 也过不了检查） |
| `D=$(pwd); rm -rf "${D:?}"` | 字面绝对路径 |
| `rm -rf logs/*/*`、`cd logs && rm -rf a/*` | 先 `ls` 出具体路径，再用字面路径删除 |
| `find $D -delete`、`find $D -exec rm {} +` | 同上，变量加 `:?` |
| `bash -c "..."`、`zsh -lc '...'`、`eval "$X"` | 直接写命令；切目录用 `( cd 目录 && 命令 )` 或 `git -C` / `go -C`；后台任务用 Bash 工具的 `run_in_background`，或者写成脚本文件再执行 |

**不拦**：引号里的文字（`grep "bash -c"`、commit message）、heredoc 内容（用 heredoc 写脚本文件没问题）、`git rm $X`、`python3 -c`、`ssh -c` 等。

**放行口**：确实只能用 `-c` 的场景（比如 `docker exec 容器 sh -c ...`），在命令末尾加注释 `# allow-shell-c`。这种情况下内置检查仍可能弹窗。

改写规则参照官方文档的 [Rewrite a flagged command](https://code.claude.com/docs/en/permission-modes#rewrite-a-flagged-command)。

### 分支任务怎么处理

1. Claude 不弹卡片，而是用 Agent 工具开后台子代理（`run_in_background: true`，在 git 仓库里加 `isolation: "worktree"`）。
2. 子代理只在自己的 worktree 和分支里改动、提交、跑测试，自己不合并、不 push、不部署。
3. 子代理做完后由主会话收尾：测试通过就 `git merge --no-ff` 合并进当前分支，然后清掉 worktree 和分支；测试不过或没价值就记下 commit hash 后删除。
4. 主任务结束前，先等所有后台任务处理完；最终汇报里列出每个后台任务的结果，删除的附上 commit hash，方便找回。

这样更省心，但每个后台任务都是一个完整的子代理，会多耗额度。

## 安装

```bash
git clone https://github.com/deepstrategy-vip/claude-code-unattended.git
cd claude-code-unattended
python3 install.py
```

`install.py` 会：
- 把两个 hook 复制到 `~/.claude/hooks/`，并注册到 `~/.claude/settings.json`；
- 把规则写进 `~/.claude/CLAUDE.md`，用标记注释包起来，不会动你自己写的内容；
- 打开 `inputNeededNotifEnabled`。

重复运行是安全的，不会产生重复条目。改动任何文件之前，都会先备份一份 `.bak-时间戳`。

| 参数 | 作用 |
|---|---|
| `--no-push` | 不打开手机推送 |
| `--claude-dir DIR` | 指定配置目录，默认 `$CLAUDE_CONFIG_DIR` 或 `~/.claude` |
| `--uninstall` | 卸载：移除 hook、settings 条目和 CLAUDE.md 里的规则块（`inputNeededNotifEnabled` 保持不动） |

更新：`git pull && python3 install.py`

已经开着的会话会读到新的 hook；CLAUDE.md 里的规则要新开会话才会生效。

要求：Python 3.9+，Claude Code（命令行或桌面版）。分支任务那部分只对桌面版 Code 标签页有意义，命令行里没有这个卡片。

## 设计原则

- **不绕过安全检查**。这个项目只是让命令写成能通过检查的样子，真正的保护仍然由 Claude Code 内置检查负责。
- **出错就放行（fail-open）**。hook 自己解析失败或者异常时，一律放行，不会因为 hook 把会话卡死。
- **拒绝时给出具体改写方法**，而不是只说"不行"，这样 Claude 能一次改对。
- **用启发式判断，不是完整的 shell 解析器**。可能漏判或误判；误判时 Claude 换种写法就能过。

## 还处理不了的

- Claude 主动提问（AskUserQuestion）、你自己配的 `ask` 规则：这些本来就需要人来回答。
- 电脑操控（computer-use）授权：每个会话问一次，设计上就不能自动授权。
- 浏览器站点授权：在弹窗里选 **Always allow**，同一个网站以后就不会再问。
- 不在 bypass 模式下的普通权限弹窗：不在本项目范围内。
- Claude Code 的内置检查规则以后可能会变，到时 bash_guard 需要跟着更新。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

CI 在 Ubuntu 和 macOS、Python 3.9 和 3.12 上运行。

## 参考

- Claude Code 官方文档：[Permission modes / Critical paths](https://code.claude.com/docs/en/permission-modes#critical-paths)、[Hooks](https://code.claude.com/docs/en/hooks)
- [destructive_command_guard](https://github.com/Dicklesworthstone/destructive_command_guard)：区分"数据"和"执行"、拒绝时给替代写法
- [karanb192/claude-code-hooks](https://github.com/karanb192/claude-code-hooks)：分级安全 hook、防止 agent 改自己的护栏配置
- [claude-remote-approver](https://github.com/yuuichieguchi/claude-remote-approver)：用 PermissionRequest hook 加 ntfy 在手机上审批

## License

[MIT](LICENSE)
