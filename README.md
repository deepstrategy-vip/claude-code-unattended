# claude-code-unattended

减少 Claude Code 离席时的例行提问和等待，保留真正需要授权的边界。

> **English:** Presence-gated Claude Code hooks: answer routine questions, bound optional-offer continuations, and deny pending permissions while away. No automatic approval of destructive operations or plans. Existing shell guards and background-task redirects remain active. Owner decisions require successful private audit; protective rewrite/redirect/config denials use best-effort audit. Hook/runtime failures still fail open.

解析、配置保护和分发已改用现成基础；验证与发布状态见[交接记录](docs/HANDOFF.md)。外部复查要求的四项修改已落实；修订后在 Python 3.9 与 3.14 各通过 113 项确定性测试，待新头CI与匹配SHA的再次复查。尚未验证真实 Desktop/手机端到端效果。背景见[原研究](docs/RESEARCH.md)，本轮逐组件选型和固定来源见[复用报告](docs/REUSE.md)。

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
| [`hooks/bash_guard.py`](hooks/bash_guard.py) | 使用 shfmt JSON AST 识别 Bash 执行节点和变量结构，再应用本项目的安全改写政策；拒绝时给出具体写法 |
| 配置保护 hook | 从 MIT config-guard 改编，拦截已知配置、hook、插件文件与配置 CLI 的修改；只 deny，先尝试脱敏审计；日志失败仍保留保护拒绝 |
| 官方插件入口 | plugin + marketplace 复用相同 hooks；SessionStart 注入同一规则文件并检查依赖健康；与 managed 安装互斥 |
| SessionStart 依赖健康检查 | managed/native 两入口检查固定 shfmt 版本；健康不输出，缺失/版本不符时给出修复提示和更保守的 Shell safety 上下文 |
| [`hooks/spawn_task_redirect.py`](hooks/spawn_task_redirect.py) | 拒绝创建分支任务卡片，让 Claude 改用后台子代理在独立 worktree 里做。做完由主会话合并或删除，分支不会留着 |
| [`hooks/unattended.py`](hooks/unattended.py) | 仅离席时：低风险 AskUserQuestion 填推荐项/第一项；窄范围结尾反问触发有限 Stop 续跑；PermissionRequest 与 ExitPlanMode 拒绝并延期处理 |
| [`rules/CLAUDE.md`](rules/CLAUDE.md) | 把同样的规则写给 Claude，让它一开始就写对。hook 只是兜底 |
| `inputNeededNotifEnabled` | 有弹窗或提问在等你时推送到手机。会话开了 Remote Control 的话，可以直接在 Claude App 里处理 |

### 何时视为离席

默认 `auto` 模式在 macOS 用 `ioreg -r -c IOHIDSystem -d 1` 读取本机输入空闲时间，**连续 300 秒**后才介入。这个阈值为读屏/短暂离席留出余量；探测最多等待两秒。近期有输入时保持原来的提问和权限流程；非 macOS、空结果、超时、解析/配置错误都不介入。多个 idle 值取最小值。

这是本机输入信号：读屏不动键鼠可能被视为离席，手机/Remote Control 在线也不代表本机有输入。hook 只在事件发生时采样一次，**已经打开的弹窗不会在之后超过阈值时被补答**。可显式选择 `away`（专用无人值守会话，无条件视为离席）或 `off`（关闭新增自动处理）；既有 Bash/分支任务 hook 仍然工作。

### 自动选择与续跑的边界

- AskUserQuestion：用官方 `allow + updatedInput.answers` 协议保留完整输入与已有答案，选标有 `(Recommended)` / `（推荐）` 的选项，否则第一项。多选只选一个稳妥选项。重复问题/选项、多个推荐项或无效格式不介入。
- 涉及删除、付费、发布、生产、凭据、权限等关键词的问题保守拒绝，要求记入「待你决定」，继续独立的已授权工作。关键词无法证明真实授权，也可能误判；推荐标签不产生权限。
- Stop：只检查 assistant 最后非空行的明确中英征询；用户文字、工具输出、代码/引用、明确待决定或风险内容不应触发。风险只检查征询本身和紧邻上一行，避免长汇报里的历史操作词误伤。持久化预算默认每个人类回合 **3 次**，指纹去重并发加锁；active 为 true 时仍受同一预算约束。UserPromptSubmit 在新用户消息时重置本会话预算，不调用 ioreg。正常完成后应结束，不创造工作；旧运行时只回读最多 256 KiB 的 assistant 文本。
- PermissionRequest：离席时只返回 **deny**，`interrupt:false`，附安全改写/跳过指示；不自动 allow，不切换权限模式，不绕过拒绝。只能处理实际经过该事件的请求。
- ExitPlanMode：离席时拒绝审批，仍在计划模式，只能做该模式允许的只读工作。不能保证剩余实现完成。

行为规则要求能自行决定的事不问，记录假设，其余做完后把真正缺授权的事统一列在最终「待你决定」。规则属于模型指令，启发式 hook 也不是系统安全边界。

### 决策记录

新增无人值守 hook 的每次自动回答、拒绝和强制继续都先追加到配置目录的 `unattended-decisions.jsonl`（默认 `~/.claude/unattended-decisions.jsonl`），成功后才输出决定。文件权限 `0600`，时间为 UTC，字段为 `time/session/type/summary/selected/content_sha256`。不存原始问题、命令、路径、选项文字；自动答案记录问题/选项 SHA-256、从 1 开始的选项索引和推荐标志，可结合对应会话原文核对。新增自动决定审计失败不介入。Bash 改写、分支任务重定向和配置保护属于确定性保护拒绝：都先尝试记录，但审计尽力而为，失败仍输出原来的 deny；重定向收到非 JSON 输入也保持拒绝，不因日志故障恢复弹窗。

按会话隔离的每回合 Stop 预算放在 `unattended-state/`，不跨会话复用。不自动清理审计或状态，卸载也保留，日志保留至少 **186 天**；自行备份时也需遵守保留期。日志不加密，勿原样公开。

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

**数据与执行分开**：grep 搜索词、commit message、单引号 heredoc 等纯文本不当作执行；命令替换（包括未加引号 heredoc 中的命令替换）仍会执行，应检查其 AST。`git rm $X`、`python3 -c`、`ssh -c` 不属于这套 shell 改写政策的完整分类范围，不能据此认为它们安全。

**放行口**：确实只能用 `-c` 的场景（比如 `docker exec 容器 sh -c ...`），在命令末尾加注释 `# allow-shell-c`。这种情况下内置检查仍可能弹窗。

改写规则参照官方文档的 [Rewrite a flagged command](https://code.claude.com/docs/en/permission-modes#rewrite-a-flagged-command)。

### 分支任务怎么处理

1. Claude 不弹卡片，而是用 Agent 工具开后台子代理（`run_in_background: true`，在 git 仓库里加 `isolation: "worktree"`）。
2. 子代理只在自己的 worktree 和分支里改动、提交、跑测试，自己不合并、不 push、不部署。
3. 子代理做完后由主会话收尾：测试通过就 `git merge --no-ff` 合并进当前分支，然后清掉 worktree 和分支；测试不过或没价值就记下 commit hash 后删除。
4. 主任务结束前，先等所有后台任务处理完；最终汇报里列出每个后台任务的结果，删除的附上 commit hash，方便找回。

这样更省心，但每个后台任务都是一个完整的子代理，会多耗额度。

## 安装

默认使用 Python 安装器，要求 Python 3.9+ 和 Claude Code。安装器从 [shfmt 官方 v3.14.1 release](https://github.com/mvdan/sh/releases/tag/v3.14.1) 下载当前平台的独立二进制，校验固定 SHA256 后放到配置目录的 `tools/shfmt/shfmt`；**不用安装 Rust、Node 或 Go**。支持的资产与摘要见[复用报告](docs/REUSE.md#第三方实验前来源版本与校验计划)。hooks 运行时不联网；parser 依赖的分析缺失、超时或解析失败时不介入。SessionStart 在 parser 缺失或版本不是固定版本时通过 `systemMessage` 提示运行 `install.py` 修复，并用 `additionalContext` 告知 Claude 该层保护暂不可用、严格遵守 Shell safety；不会因此阻塞会话或自动联网修复。健康时检查本身没有输出。

```bash
git clone https://github.com/deepstrategy-vip/claude-code-unattended.git
cd claude-code-unattended
python3 install.py
```

`install.py` 复制 hooks、共享适配和许可证，注册 Bash/配置保护、分支重定向、无人值守的相应事件；将规则写入配置目录的 `CLAUDE.md` 标记块，并保留块外原文。它保存 `unattended-config.json`，默认打开 `inputNeededNotifEnabled`，不默认覆盖提问超时、主动推送或输出风格。更新使用 `git pull` 后重新运行安装命令；已有 Desktop 会话可能缓存配置，安装后新开会话。

重复安装幂等，不重复注册；覆盖或删除已有文件前备份。卸载仅删除本产品管理项，不删审计/状态，通知、超时和风格偏好保留，避免覆盖使用者后续改动。

| 参数 | 作用 |
|---|---|
| `--shfmt PATH` | 离线导入官方固定版本二进制，仍须匹配对应平台的固定摘要；不接受任意同版本构建 |
| `--prepare-plugin` | 仅准备 parser、许可证和配置，不写 settings/CLAUDE.md；随后显式安装插件 |
| `--no-push` | 不打开手机推送 |
| `--claude-dir DIR` | 指定配置目录，默认 `$CLAUDE_CONFIG_DIR` 或 `~/.claude` |
| `--uninstall` | 移除 managed hooks、settings 条目与规则块；保留个人正文和审计 |
| `--config-guard on\|off` | 配置保护的负责人开关，默认on；写入unattended-config.json，不提供环境变量覆盖 |
| `--idle-seconds N` | 本机空闲阈值1–86400秒，默认300；更新未指定时保留已有值 |
| `--mode auto\|off\|away` | 默认auto；off关闭新增自动处理，away显式始终视为离席 |
| `--max-stop-continuations N` | 每个人类回合Stop预算1–20，默认3 |
| `--question-timeout 60s\|5m\|10m\|never` | 显式设置提问超时；Remote Control连接时不计时 |
| `--agent-push` | 显式开启主动手机通知 |
| `--proactive` | 显式设置全局outputStyle=Proactive，不改变权限模式 |

离线安装或隔离验证可先取得并校验官方二进制，再运行：

```bash
python3 install.py --claude-dir /tmp/ccu-example --mode away --no-push --shfmt /path/to/verified/shfmt
```

运行时覆盖包括 `CCU_MODE`、`CCU_IDLE_SECONDS`、`CCU_MAX_STOP_CONTINUATIONS`；`CCU_CONFIG_DIR`可选择配置/审计根。`CCU_SHFMT`主要用于隔离测试指定 parser，不是自动下载开关。非法配置回到原流程。

### 可选：使用官方插件与marketplace格式

插件与managed注册只能选一个。已用安装器的用户先显式卸载managed入口，再准备依赖：

```bash
python3 install.py --uninstall
python3 install.py --prepare-plugin
```

然后在Claude Code中运行：

```text
/plugin marketplace add deepstrategy-vip/claude-code-unattended
/plugin install claude-code-unattended@claude-code-unattended
```

插件用SessionStart的`additionalContext`读取同一份规则，不修改用户CLAUDE.md。parser、配置、审计与预算留在配置根，不依赖插件版本缓存；卸载插件后继续保留审计至少186天。若已有managed注册，插件入口不重复执行；managed安装器发现同名插件启用时拒绝重复安装。自定义配置根时，准备命令和会话运行环境必须使用同一根。

插件安装/卸载走Claude Code官方界面或CLI；依赖准备仍由本项目处理。要切回managed方式，先卸载或禁用插件，再运行`python3 install.py`。插件JSON和临时目录测试不代表真实Desktop已加载，产品效果仍需自然使用验证。

## 配置保护的范围

配置守卫从现成config-guard分类表、路径解析和CLI变更识别移植，使用Python标准库；Bash复用同一shfmt AST。默认high保护已知settings、hooks、MCP/插件配置及本产品实际配置/安装路径，区分读取和修改，并处理直接文件工具的symlink。**默认不保护用户级CLAUDE.md，也不一刀切保护所有项目CLAUDE.md**；hook manifest限定为`hooks/hooks.json`，普通`src/hooks.json`可以正常编辑，减少日常误拦。

配置保护只deny，不自动批准、不提供ask或上游allow逃生环境开关。确定性保护拒绝先尝试写不含原文的结构化审计；即使日志失败仍deny，避免因为审计故障让Claude关闭护栏。这与替使用者回答问题、拒绝计划/权限及强制续跑不同：后者仍须成功写入审计才输出决定。解析/运行异常继续fail-open。

负责人可在`unattended-config.json`设置`"config_guard": "on"`或`"off"`，默认on；也可亲自运行`python3 install.py --config-guard off`或`--config-guard on`。不支持环境变量关闭。off时普通受保护目标不再由此守卫拒绝，但开关配置文件及已识别的自我禁用尝试仍受保护；Claude收到拒绝应继续独立工作，把需要调整之处交给负责人手动处理、Codex处理，或由负责人在终端运行安装器，不能自行执行停用命令绕过拒绝。负责人专用参数包括`--uninstall`、`--config-guard`、`--mode`和`--prepare-plugin`；在on/off两种状态均拒绝Claude调用这些参数。不带这些参数的安装器正常更新不拦截。

这是逻辑上的负责人控制，不是操作系统访问控制或不可逆锁定。动态变量、解释器程序、别名/函数、外部同用户进程及未覆盖写入机制仍有缺口；fail-open也不能保证防篡改。不会为了保护hook而扩大权限或切换模式。完整选型、误判/漏判和许可差异见[复用报告](docs/REUSE.md)。

## 设计原则

- **不绕过安全检查**。这个项目只是让命令写成能通过检查的样子，真正的保护仍然由 Claude Code 内置检查负责。
- **出错就放行（fail-open）**。解析或运行异常不介入；替使用者决定的审计失败也不介入。确定性改写/重定向/配置保护拒绝的日志尽力而为，审计失败仍保留deny；重定向兼容无效输入。
- **拒绝时给出具体改写方法**，而不是只说"不行"，这样 Claude 能一次改对。
- **解析与政策分开**。shell语法交给成熟shfmt AST，风险分类与本项目改写边界仍是有限规则；AST解析成功不证明命令安全。

## 还处理不了的

- 真正缺授权的事、检测离席之前已经打开的弹窗、正则没有匹配的反问、Stop 预算耗尽后再次反问。
- 电脑操控（computer-use）授权：每个会话问一次，设计上就不能自动授权。
- 浏览器站点授权：在弹窗里选 **Always allow**，同一个网站以后就不会再问。
- 本机 HID 不代表手机在场；读取失败保留原始等待。沙箱网络授权等不触发 PermissionRequest 的提示不在覆盖范围。
- `dialogExpiry` 不控制权限和 AskUserQuestion；手机不能保证处理所有 Desktop/系统弹窗。Proactive 仅调整行为偏好，敏感操作仍会问。
- Claude Code 的内置检查规则以后可能会变，到时 bash_guard 需要跟着更新。

## 测试

先准备精确校验的shfmt二进制，然后指定其路径运行确定性测试：

```bash
CCU_SHFMT=/path/to/verified/shfmt python3 -m unittest discover -s tests -v
```

CI准备同一官方固定摘要的二进制，测试中离线导入临时配置目录；不会安装到实际用户配置。复查前提交在 Python 3.9 与 3.14 各通过102项，包括原51项全部场景、48条原shell分类样例、新AST/config-guard/插件/迁移与逐字节正文保留测试。四项复查修改后已完整重跑，Python 3.9与3.14各113项通过；新增覆盖managed/plugin健康、缺失、版本不符与超时提示，以及配置开关和保护拒绝日志故障场景。102项仅是旧头历史结果。协议测试不等于Desktop/手机端到端验收，CI与发布结果见[交接记录](docs/HANDOFF.md)。

## 致谢与来源

| 使用的现成项目 | 用在哪里、许可与固定来源 |
|---|---|
| [mvdan/sh / shfmt v3.14.1](https://github.com/mvdan/sh/releases/tag/v3.14.1) | 直接使用官方二进制输出JSON AST，替换手写shell解析；[BSD-3-Clause](https://github.com/mvdan/sh/blob/v3.14.1/LICENSE)，分发时保留版权和许可 |
| [karanb192/claude-code-hooks](https://github.com/karanb192/claude-code-hooks/tree/57a28c7687305e4a9b9dd50490e9b3c552c71ca4) | 从[config-guard](https://github.com/karanb192/claude-code-hooks/blob/57a28c7687305e4a9b9dd50490e9b3c552c71ca4/plugins/config-guard/config-guard.js)改编Python路径/操作分类；[MIT](https://github.com/karanb192/claude-code-hooks/blob/57a28c7687305e4a9b9dd50490e9b3c552c71ca4/LICENSE)，保留原版权，未移植ask/allow override/原文日志 |
| [Claude Code官方接口](https://code.claude.com/docs/en/plugins-reference) | 直接采用plugin/marketplace、SessionStart上下文和交互工具答案协议；Proactive作为显式风格选项；本项目不vendor官方循环插件代码 |
| [froggeric/claude-smart-approval](https://github.com/froggeric/claude-smart-approval/tree/60b44fede472983f6dc730a4012f54422b54c102) | 参考shfmt AST遍历思路，未复制代码、自动审批、模型调用或学习规则；上游[MIT](https://github.com/froggeric/claude-smart-approval/blob/60b44fede472983f6dc730a4012f54422b54c102/LICENSE) |

HID门控、低风险决策、deny-only权限/计划、有限Stop、脱敏先审计和Desktop分支卡片适配保留小范围自研：候选项目未同时满足授权、审计、故障放行和无需额外付费服务的约束。维护/许可/替代比较见[逐组件报告](docs/REUSE.md)。DCG未集成：当前[许可证带附加rider](https://github.com/Dicklesworthstone/destructive_command_guard/blob/v0.15.3/LICENSE)，故障处理政策也不匹配本项目，不把它归类为普通MIT。

## License

[MIT](LICENSE)
