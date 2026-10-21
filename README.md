# claude-code-unattended

让 Claude Code 在你不在电脑边时也能一直跑下去，不被弹窗卡住。

> **English:** Presence-gated Claude Code hooks: answer routine questions, bound optional-offer continuations, and deny pending permissions while away. No automatic approval of destructive operations or plans. Existing shell guards and background-task redirects remain active. Errors pass through to Claude's original flow.

**当前边界：已验证 Python 协议、安装器与本机 HID 检测，尚未验证真实 Desktop/手机端到端效果。** 完整调研和来源见 [研究报告](docs/RESEARCH.md)，实施与验收状态见 [交接记录](docs/HANDOFF.md)。

## 为什么需要

开了 bypass permissions，Claude Code 还是会为下面几类操作弹窗等人确认：

| 你看到的弹窗 | 触发它的写法 |
|---|---|
| `Dangerous rm operation on possibly-empty variable path` | `rm -rf $S/pgdata`：变量为空时会变成删根目录 |
| `This shell -c script runs rm and could not be checked` | `bash -c "..."`、`nohup zsh -c "..." &`：检查器看不透字符串里的脚本 |
| 开分支任务的卡片（桌面版 Code 标签页） | Claude 发现范围外的事时用 spawn_task 留的卡片，要点一下才开始 |
| AskUserQuestion | 没人选答案就等待；连接 Remote Control 时提问超时计时不启动 |
| 结尾「要我继续吗？」 | 已经结束本轮，只有新消息才会继续 |
| 其他工具权限、计划审批 | 还需要有效授权，不能用推荐标签代替授权 |

前两类是 Claude Code 有意保留的安全检查，`permissions.allow` 规则和 PreToolUse hook 返回 `allow` 都关不掉（见[官方文档](https://code.claude.com/docs/en/permission-modes#critical-paths)）。人不在电脑边，会话就一直卡在那里。

## 它做了什么

| 组件 | 作用 |
|---|---|
| [`hooks/bash_guard.py`](hooks/bash_guard.py) | Bash 命令执行前检查。发现会触发上述检查的写法，直接拒绝，并把具体改写方法告诉 Claude。Claude 改完重跑，不弹窗 |
| [`hooks/spawn_task_redirect.py`](hooks/spawn_task_redirect.py) | 拒绝创建分支任务卡片，让 Claude 改用后台子代理在独立 worktree 里做。做完由主会话合并或删除，分支不会留着 |
| [`hooks/unattended.py`](hooks/unattended.py) | 仅离席时：低风险 AskUserQuestion 填推荐项/第一项；窄范围结尾反问触发有限 Stop 续跑；PermissionRequest 与 ExitPlanMode 拒绝并延期处理 |
| [`rules/CLAUDE.md`](rules/CLAUDE.md) | 把同样的规则写给 Claude，让它一开始就写对。hook 只是兜底 |
| `inputNeededNotifEnabled` | 有弹窗或提问在等你时推送到手机。会话开了 Remote Control 的话，可以直接在 Claude App 里处理 |

### 何时视为离席

默认 `auto` 模式在 macOS 用 `ioreg -r -c IOHIDSystem -d 1` 读取本机输入空闲时间，**连续 300 秒**后才介入。这个阈值为读屏/短暂离席留出余量；实测单次探测约 17 ms，最多等待两秒。近期有输入时保持原来的提问和权限流程；非 macOS、空结果、超时、解析/配置错误都不介入。多个 idle 值取最小值。

这是本机输入信号：读屏不动键鼠可能被视为离席，手机/Remote Control 在线也不代表本机有输入。hook 只在事件发生时采样一次，**已经打开的弹窗不会在之后超过阈值时被补答**。可显式选择 `away`（专用无人值守会话，无条件视为离席）或 `off`（关闭新增自动处理）；既有 Bash/分支任务 hook 仍然工作。

### 自动选择与续跑的边界

- AskUserQuestion：用官方 `allow + updatedInput.answers` 协议保留完整输入与已有答案，选标有 `(Recommended)` / `（推荐）` 的选项，否则第一项。多选只选一个稳妥选项。重复问题/选项、多个推荐项或无效格式不介入。
- 涉及删除、付费、发布、生产、凭据、权限等关键词的问题保守拒绝，要求记入「待你决定」，继续独立的已授权工作。关键词无法证明真实授权，也可能误判；推荐标签不产生权限。
- Stop：只检查 assistant 最后非空行的明确中英征询；用户文字、工具输出、代码/引用、明确待决定或风险内容不应触发。`stop_hook_active` 必须为 false，指纹去重，持久化会话预算默认 **3 次**，并发加锁。正常完成后应结束，不创造工作；旧运行时只回读最多 256 KiB 的 assistant 文本。
- PermissionRequest：离席时只返回 **deny**，`interrupt:false`，附安全改写/跳过指示；不自动 allow，不切换权限模式，不绕过拒绝。只能处理实际经过该事件的请求。
- ExitPlanMode：离席时拒绝审批，仍在计划模式，只能做该模式允许的只读工作。不能保证剩余实现完成。

行为规则要求能自行决定的事不问，记录假设，其余做完后把真正缺授权的事统一列在最终「待你决定」。规则属于模型指令，启发式 hook 也不是系统安全边界。

### 决策记录

新旧 hooks 的每次自动回答、拒绝、重定向和强制继续都先追加到配置目录的 `unattended-decisions.jsonl`（默认 `~/.claude/unattended-decisions.jsonl`），成功后才输出决定。文件权限 `0600`，时间为 UTC，字段为 `time/session/type/summary/selected/content_sha256`。不存原始问题、命令、路径、选项文字；自动答案记录问题/选项 SHA-256、从 1 开始的选项索引和推荐标志，可结合对应会话原文核对。审计失败不介入。

会话 Stop 预算放在 `unattended-state/`，不跨会话复用。不自动清理审计或状态，卸载也保留，日志保留至少 **186 天**；自行备份时也需遵守保留期。日志不加密，勿原样公开。

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
- 把三个 hook 脚本复制到配置目录 `hooks/`，注册 PreToolUse、PermissionRequest 和 Stop；
- 把规则写进 `~/.claude/CLAUDE.md`，用标记注释包起来，不会动你自己写的内容；
- 打开 `inputNeededNotifEnabled`，保存 `unattended-config.json`；默认不覆盖提问超时、主动推送或输出风格。

重复运行是安全的，不会产生重复条目。改动任何文件之前，都会先备份一份 `.bak-时间戳`。

| 参数 | 作用 |
|---|---|
| `--no-push` | 不打开手机推送 |
| `--claude-dir DIR` | 指定配置目录，默认 `$CLAUDE_CONFIG_DIR` 或 `~/.claude` |
| `--uninstall` | 卸载：移除 hook、settings 条目和 CLAUDE.md 里的规则块（`inputNeededNotifEnabled` 保持不动） |
| `--idle-seconds N` | 本机空闲阈值，1–86400 秒，默认 300；更新未指定时保留已有值 |
| `--mode auto\|off\|away` | 默认 auto；off 停用新增处理，away 显式始终视为离席 |
| `--max-stop-continuations N` | 每会话 Stop 预算，1–20，默认 3；预算耗尽后正常结束 |
| `--question-timeout 60s\|5m\|10m\|never` | 可选全局 askUserQuestionTimeout；Remote Control 连接时不计时 |
| `--agent-push` | 可选开启 agentPushNotifEnabled，让 Claude 可主动发手机通知 |
| `--proactive` | 可选设置全局 outputStyle=Proactive；不会改变权限模式 |

运行时环境变量优先于配置：`CCU_MODE`、`CCU_IDLE_SECONDS`、`CCU_MAX_STOP_CONTINUATIONS`。`CCU_CONFIG_DIR` 可指定 hooks 的配置/日志根目录；通常不必设置，默认使用 `$CLAUDE_CONFIG_DIR` 或脚本所属配置目录。非法配置静默回到原流程。卸载保留通知/超时/风格设置和审计历史，避免覆盖后续个人改动。

先用临时目录验证，例如 `python3 install.py --claude-dir /tmp/example-claude-config --mode away --no-push`，不会安装到默认配置。

更新：`git pull && python3 install.py`

安装后新开会话，确保 hooks 和规则都加载；已有 Desktop 会话可能缓存配置。

要求：Python 3.9+，Claude Code（命令行或桌面版）。分支任务那部分只对桌面版 Code 标签页有意义，命令行里没有这个卡片。

## 设计原则

- **不绕过安全检查**。这个项目只是让命令写成能通过检查的样子，真正的保护仍然由 Claude Code 内置检查负责。
- **出错就放行（fail-open）**。hook 自己解析失败或者异常时，一律放行，不会因为 hook 把会话卡死。
- **拒绝时给出具体改写方法**，而不是只说"不行"，这样 Claude 能一次改对。
- **用启发式判断，不是完整的 shell 解析器**。可能漏判或误判；误判时 Claude 换种写法就能过。

## 还处理不了的

- 真正缺授权的事、检测离席之前已经打开的弹窗、正则没有匹配的反问、Stop 预算耗尽后再次反问。
- 电脑操控（computer-use）授权：每个会话问一次，设计上就不能自动授权。
- 浏览器站点授权：在弹窗里选 **Always allow**，同一个网站以后就不会再问。
- 本机 HID 不代表手机在场；读取失败保留原始等待。沙箱网络授权等不触发 PermissionRequest 的提示不在覆盖范围。
- `dialogExpiry` 不控制权限和 AskUserQuestion；手机不能保证处理所有 Desktop/系统弹窗。Proactive 仅调整行为偏好，敏感操作仍会问。
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
