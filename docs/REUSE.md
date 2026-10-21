# 用成熟解析与分发，保留明确的授权边界

**结论：本轮应直接复用 shfmt 官方固定版本二进制，替换手写 shell 分词、引号屏蔽和 heredoc 处理；以 MIT 的 claude-code-hooks/config-guard 分类实现为基础移植配置保护；直接采用 Claude Code 官方 plugin + marketplace 格式作为可选分发入口，保留现有安装器处理旧版本迁移、备份和幂等。** 无人值守核心中的 HID、低风险答案、拒绝计划、PermissionRequest 只 deny、有限 Stop 和先脱敏审计，目前没有找到同时满足全部边界的成熟直接替代，应保留小范围政策适配。官方 Proactive 已是日常少问的现成功能，继续显式启用；官方 Stop 非错误反馈接口值得评估，但不为了形式复用而无验证地改动稳定输出。**DCG 不在本轮安装或 vendor**：当前许可证带 OpenAI/Anthropic rider，不能当普通 MIT；其不可验证输入处理也不符合本任务统一 fail-open。以下为 A 阶段的实现决策依据，**不是 B 已实现或已发布的声明**。基线为 PR #1 的 51 项 unittest；隔离实验仅证明 48 条 shell 基线能被 shfmt 解析，不证明分类器或 Desktop 端到端效果。

## 组件替换范围：复用基础设施，保留本地政策

本报告先读取上一轮报告及仓库 RESEARCH，再重新核查候选源码、许可证、维护和新项目。事实分为【官方契约】、【上游源码/文档】、【本轮实验】和【工程判断】；后者只表示选型判断。星数、最近提交和 issue 状态是 **2026-10-21** 快照，父仓库活跃不等于具体组件最近修改，少量 issue 样本也不构成响应 SLA。社区问题报告未在本机复现的，均不标成实测故障。

| 当前组件/新增需求 | 决定 | 直接使用或改编的基础 | 保留范围与不能突破的边界 |
|---|---|---|---|
| bash_guard shell 解析 | **直接用 shfmt v3.14.1** | 官方 JSON AST；Python stdlib 遍历 | 删除手写分词/引号/heredoc 解析；保留 shell-c/eval、`${VAR:?}`、find、路径来源等小范围改写政策；只 deny 或无意见，绝不 allow |
| config-guard | **以现成为基础改** | claude-code-hooks 固定 SHA 的 MIT 分类表、路径解析与 CLI 枚举；shell 路径交给同一 AST | 不移植 ask、ALLOW override、原文日志；审计失败放行；不能宣称防任意解释器或同用户外部程序 |
| plugin + marketplace | **直接用官方格式** | plugin manifest、hooks/hooks.json、marketplace；SessionStart 提供同一规则文本 | 可选入口，先保留 managed 安装；互斥注册；不将审计放可被卸载删除的插件目录 |
| install.py | **保留为迁移适配** | 官方插件安装负责新入口，现安装器负责既有用户状态 | 幂等、改前备份、卸载、临时配置根、保留规则块外正文与186天日志；不默默切换真实环境 |
| HID presence | **保留** | Apple HIDIdleTime（已有） | 只代表本机输入空闲；信号未知/超时不介入；不等于人体在场或手机活动 |
| AskUserQuestion | **保留小适配** | 官方 allow + updatedInput.answers 协议 | 原输入完整保留，已有答案不覆盖；仅已授权低风险决定；风险问题不能凭 Recommended 扩权 |
| ExitPlanMode | **保留 deny** | 官方 PreToolUse | 不盲批、不切权限；拒绝后仍受计划只读模式约束 |
| PermissionRequest | **保留 deny-only** | 官方 deny + interrupt:false | 不 allow、不学习永久规则、不引导换工具绕过危险删除 |
| 有限 Stop | **保留判定/预算；可测试后采用官方非错误输出** | 官方 Stop additionalContext；现有 block 仍是合法接口 | 每人类回合预算、会话隔离、重复指纹、先审计；不全局引入 Ralph 无限循环 |
| 脱敏先审计 | **保留** | 现有 JSONL + 锁 | 自动决定先成功记录；不存原问题、选项、命令和路径；日志至少186天 |
| spawn_task_redirect | **保留小 shim** | 官方 Agent 后台/worktree语义 | 未找到 Desktop 专用卡片替代；只执行既有授权，不引入第三方 runner 的 push/deploy 权限 |
| CLAUDE.md 行为规则 | **直接继续用 Proactive，保留本地差异** | 官方 outputStyle；插件 SessionStart | 不强制覆盖用户风格；模型规则不替代工具守卫；安装/卸载保持用户正文 |
| DCG 额外删除防线 | **本轮不安装、不 vendor** | 后续只有许可证及 fail-open 兼容后再评估 | 不能把成熟度当作许可证豁免；不能宣传所有危险命令都 deny 且绝不 ask |

【官方契约】AskUserQuestion 的程序答案需要 `allow` 配合完整 `updatedInput`，不能仅输出 allow；PermissionRequest 拒绝使用 decision 对象；SessionStart 能注入上下文。Proactive 是行为指令，不能消除权限提示。这些是直接复用的协议基础，不需要另加审批模型。（[交互工具协议](https://code.claude.com/docs/en/hooks#tools-that-require-user-interaction)、[PermissionRequest](https://code.claude.com/docs/en/hooks#permissionrequest-decision-control)、[Proactive](https://code.claude.com/docs/en/output-styles#proactive)）

## shell 采用成熟 AST，但 AST 不等于删除安全分类

| Candidate | Maintenance and issue sample | License and dependency | Conclusion |
| --- | --- | --- | --- |
| [mvdan/sh](https://github.com/mvdan/sh) | 9,141 stars; HEAD [c2cf6f25a6eae8154d4827ac8473b13229d3dffd](https://github.com/mvdan/sh/commit/c2cf6f25a6eae8154d4827ac8473b13229d3dffd), 2026-10-20; official release v3.14.1 dated 2026-09-06. [#1440](https://github.com/mvdan/sh/issues/1440) opened Oct 20, only author follow-up in sampled page, so do not claim maintainer response from its comment count. Older issues #1068/#757 closed Oct 19 in API metadata. | [BSD-3-Clause](https://github.com/mvdan/sh/blob/v3.14.1/LICENSE); official standalone macOS/Linux binaries. Go required to build source, not to execute binaries. | **直接用** official pinned binary; Python stdlib reads JSON AST. |
| [idank/bashlex](https://github.com/idank/bashlex) | 651 stars; HEAD [3f6b76c4c8462f4b90d9b1c73a555c45cde42f62](https://github.com/idank/bashlex/commit/3f6b76c4c8462f4b90d9b1c73a555c45cde42f62), 2024-04-08; no GitHub release. [#101](https://github.com/idank/bashlex/issues/101) open since 2025-03-31 with zero comments; [#99](https://github.com/idank/bashlex/issues/99) single-quoted heredoc parser issue open since 2024-12-29. | [GPL v3+](https://github.com/idank/bashlex/blob/3f6b76c4c8462f4b90d9b1c73a555c45cde42f62/LICENSE); pure Python, no Rust/Node. README states arithmetic expressions unsupported and complex parameter expansions taken literally. | **不 vendor** per requested GPL restriction; maintenance/parser limitations further weaken fit. |
| [tree-sitter-bash](https://github.com/tree-sitter/tree-sitter-bash) | 332 stars; HEAD [a06c2e4415e9bc0346c6b86d401879ffb44058f7](https://github.com/tree-sitter/tree-sitter-bash/commit/a06c2e4415e9bc0346c6b86d401879ffb44058f7), 2025-12-02; release v0.25.1 same date. pushed_at Sep 2026 is not a latest-main-commit date. [#349](https://github.com/tree-sitter/tree-sitter-bash/issues/349) open since Sep 23 2026, describes valid Bash misparsed as ERROR and comments confused with quoted data. [#364](https://github.com/tree-sitter/tree-sitter-bash/issues/364) and [#363](https://github.com/tree-sitter/tree-sitter-bash/issues/363) new Oct 20, zero comments at snapshot; no response-time guarantee. | [MIT](https://github.com/tree-sitter/tree-sitter-bash/blob/v0.25.1/LICENSE), but [Python packaging](https://github.com/tree-sitter/tree-sitter-bash/blob/v0.25.1/pyproject.toml) requires Python >=3.10, native grammar + tree-sitter runtime; [setup.py](https://github.com/tree-sitter/tree-sitter-bash/blob/v0.25.1/setup.py) builds C extension. | **本轮不用**; modern wheel incompatible with /usr/bin Python 3.9, backward-version pin adds burden. Good editor CST is not automatic security classification. |
| [claude-smart-approval](https://github.com/froggeric/claude-smart-approval) | 4 stars; HEAD [60b44fede472983f6dc730a4012f54422b54c102](https://github.com/froggeric/claude-smart-approval/commit/60b44fede472983f6dc730a4012f54422b54c102), 2026-10-14. No issues or releases returned; cannot infer support responsiveness. | [MIT](https://github.com/froggeric/claude-smart-approval/blob/60b44fede472983f6dc730a4012f54422b54c102/LICENSE); Bash 3.2+, shfmt and jq. [auto-approve.sh](https://github.com/froggeric/claude-smart-approval/blob/60b44fede472983f6dc730a4012f54422b54c102/auto-approve.sh) traverses AST, but reconstructs ParamExp as `$NAME`, losing `${NAME:?}` distinction. | **只以解析思路为基础改**; do not copy runtime approval. Its allow outputs, Haiku calls, learned allow rules, original-command logs and 1000-line log rotation conflict with our boundaries. Python AST adapter needs no jq. |
| [DCG](https://github.com/Dicklesworthstone/destructive_command_guard) | 6,197 stars; HEAD [c1d9be25b50e939d2d5ce4db684e638a12e05352](https://github.com/Dicklesworthstone/destructive_command_guard/commit/c1d9be25b50e939d2d5ce4db684e638a12e05352), Oct 21; latest release v0.15.3 Oct 8. [#582](https://github.com/Dicklesworthstone/destructive_command_guard/issues/582) installer field-loss bug opened Oct 19, owner explained fix and closed Oct 21, next release pending. [#555](https://github.com/Dicklesworthstone/destructive_command_guard/issues/555) reports latency scaling on v0.15.3; still open in snapshot. | [v0.15.3 custom license](https://github.com/Dicklesworthstone/destructive_command_guard/blob/v0.15.3/LICENSE), **MIT with OpenAI/Anthropic rider**, GitHub Other/NOASSERTION. [Release](https://github.com/Dicklesworthstone/destructive_command_guard/releases/tag/v0.15.3) has standalone macOS binary, SHA256, minisign and sigstore bundles: no Rust runtime required. | **本轮不装、不 vendor**. Maturity does not remove licensing and failure-policy mismatch. |

上表维护来源可通过各项目 [sh](https://api.github.com/repos/mvdan/sh)、[bashlex](https://api.github.com/repos/idank/bashlex)、[tree-sitter-bash](https://api.github.com/repos/tree-sitter/tree-sitter-bash)、[smart-approval](https://api.github.com/repos/froggeric/claude-smart-approval)、[DCG](https://api.github.com/repos/Dicklesworthstone/destructive_command_guard) 元数据复核。没有 issue 的项目只能说缺响应证据，不能写成支持完善。

【上游源码】shfmt 将前缀赋值和命令参数分开，保留 ParamExp 的 `:?` 与引号结构，能够区分真正执行节点与引用数据；CLI 提供 `--to-json`，旧 `-tojson` 计划 v4 移除，应使用新参数。smart-approval 已展示使用 shfmt 分类的路径，但重建参数为 `$NAME` 会丢失本项目需要的 `${NAME:?}` 区别，其自动 allow、Haiku、学习规则与原文日志不应移植。（[shfmt 手册](https://github.com/mvdan/sh/blob/v3.14.1/cmd/shfmt/shfmt.1.scd)、[CLI 源码](https://github.com/mvdan/sh/blob/v3.14.1/cmd/shfmt/main.go)、[smart-approval 固定源码](https://github.com/froggeric/claude-smart-approval/blob/60b44fede472983f6dc730a4012f54422b54c102/auto-approve.sh)）

【工程判断】统一 Python AST 适配要固定 argv、限制输入与输出大小、设置子进程超时，解析失败返回无意见。AST 是 UTF-8 字节偏移，不能直接用 Python 字符串索引截取中文命令；直接使用结构化 Parts，保留参数展开与命令替换信息。`sudo`、`env`、`command` 以及 `find -exec sh -c` 仍是命令特定参数语法，需要少量明确适配，不能宣称“装 parser 后不需要任何政策代码”。应保留既有 shell rewrite 优先级和 shell-c 专用 override 语义，补充 wrappers、quoted command name、Unicode、未加引号 heredoc 的 command substitution 与 parser 缺失/超时测试。

误判方面，`echo bash -c ls`、`printf '%s' 'rm $X'`、grep/commit-message/quoted heredoc 应是数据；`'bash' -c ls` 和 `echo "$(rm -rf $X)"` 则真正执行，前者不能因带引号漏判，后者不能因外层 echo 漏判。动态 alias、函数改写、运行时字符串执行、解释器及未知 shell 方言仍有漏判空间；fail-open 表示不能把解析失败等同“命令安全”。这些是拟实现行为与新增验收点，不是已经通过的新分类器结果。

【本轮实验】只在事先登记来源、版本和摘要后，下载并运行官方 shfmt macOS arm64 二进制，核验精确 SHA256 和 `--version=v3.14.1`。48 条既有 shell fixtures 全部能输出 JSON AST；未执行 fixtures 中的 shell 命令、未触发 Claude/API、未修改实际用户配置。**48/48 是 parser acceptance，51 项是旧版完整基线，两者都不能替代 B 阶段的新完整测试。**

DCG 的价值是更广的危险命令上下文分类，但不能按“始终静默 deny”概括。【上游文档】不可验证结果默认 ask，bypass/dontAsk 自动 deny，存在可选 allowlist、one-time grant 与原文历史功能；deadline 的严格策略与本任务“hook出错放行”不同。公开 issue 既有误报也有漏报：[inspection test/explain 被当危险执行 #170](https://github.com/Dicklesworthstone/destructive_command_guard/issues/170)、[VAR=value 前缀影响 hook协议识别 #231](https://github.com/Dicklesworthstone/destructive_command_guard/issues/231)、[仅打包 Python tar 被误拦 #562](https://github.com/Dicklesworthstone/destructive_command_guard/issues/562)。main 的修复不能自动视为10月8日 release 已包含；没有独立运行 DCG。（[固定 failure policy](https://github.com/Dicklesworthstone/destructive_command_guard/blob/c1d9be25b50e939d2d5ce4db684e638a12e05352/README.md#failure-policy)）

【工程判断】DCG 和 bash_guard 理论上可并列 PreToolUse：前者做危险分类，后者做语法改写；但不能互相 allow 覆盖，不能重复输出矛盾审批。当前许可及故障政策不匹配，故本轮不装、不 vendor，不需要在真实环境为它接受新许可，也不为了多一层保护而偷换边界。未来若用户另行选择兼容政策，才可在隔离环境验证常用 git、缓存清理、临时目录删除与归档的误报。

## config-guard 改编其分类，插件负责分发而非改写用户规则

| 候选/方式 | 维护、许可与依赖 | 可满足与缺口 | 结论 |
|---|---|---|---|
| claude-code-hooks/config-guard | 547★；仓库最新2026-10-17，`57a28c7687305e4a9b9dd50490e9b3c552c71ca4`；组件自身最近2026-08-18；MIT，Copyright (c) 2026 Karan Bansal；Node fs/path，无npm依赖。[元数据](https://api.github.com/repos/karanb192/claude-code-hooks)、[固定源码](https://github.com/karanb192/claude-code-hooks/blob/57a28c7687305e4a9b9dd50490e9b3c552c71ca4/plugins/config-guard/config-guard.js) | high 保护settings/hooks/MCP/manifests/CLI写配置，strict还护所有CLAUDE.md；原版有ALLOW escape、ask和含target/cwd原文日志，日志失败仍deny。 | **以现成为基础改**为Python，同一AST处理Bash；保留许可与明确归属，不照搬运行入口 |
| 官方 ConfigChange | Claude Code官方契约，无需额外运行时。[协议](https://code.claude.com/docs/en/hooks#configchange) | 发生于配置变化；block阻止新值应用当前会话，不能阻止文件已被写坏，也不能阻止policy_settings；不是自保护替代 | 可补脱敏审计，**不用它替代PreToolUse** |
| 上游 config-watch | 同仓库MIT；[固定代码](https://github.com/karanb192/claude-code-hooks/blob/57a28c7687305e4a9b9dd50490e9b3c552c71ca4/plugins/config-watch/config-watch.js) | 记录raw payload；注释仍称schema未知，与当前官方契约有差异 | 不直接使用原文日志/可见告警承诺 |
| 官方plugin+marketplace | 平台标准，无需额外Node/Rust；[manifest](https://code.claude.com/docs/en/plugins-reference#standard-layout)、[components](https://code.claude.com/docs/en/plugins/components#hooks) | 自动加载hooks；根CLAUDE.md不自动入上下文；默认卸载会删plugin data | **直接采用可选入口**，SessionStart读同份规则，日志留持久配置根 |
| 保留install.py | 本项目现有51项基线中的安装覆盖 | 可统一旧注册迁移、备份、标记块、可选全局偏好；要新增parser二进制生命周期及互斥注册 | **保留并缩为适配层**；不为追随插件形式丢弃既有保证 |

维护响应样本：[重复hooks #51](https://github.com/karanb192/claude-code-hooks/issues/51) 约4分钟关闭；[正则卡死 #67](https://github.com/karanb192/claude-code-hooks/issues/67) 约7天关闭；[eval #60](https://github.com/karanb192/claude-code-hooks/issues/60) 仍开。125个上游 `it()` 定义是静态计数，**未在本机执行125项**，不能写成通过。（[固定测试](https://github.com/karanb192/claude-code-hooks/blob/57a28c7687305e4a9b9dd50490e9b3c552c71ca4/plugins/config-guard/tests/config-guard.test.js)）

【上游明确限制】config-guard 不是真正 shell parser：变量路径、解释器写入、git checkout/restore、chmod及Bash symlink重定向存在缺口；同一段命令出现 mutation verb 与保护路径可能误拦互不相关参数。直接Edit/Write会解析symlink及不存在文件的父目录。这些缺口应在改编文档中继续列出；接入 AST 可减少“同段不同命令”误判，但不能证明阻断任意程序写文件。（[known limits](https://github.com/karanb192/claude-code-hooks/blob/57a28c7687305e4a9b9dd50490e9b3c552c71ca4/plugins/config-guard/README.md#known-limits)）

【工程判断】应移植保护路径/level/CLI变更枚举与symlink语义，保留MIT原许可/版权、固定SHA、差异说明及对应测试场景。默认high，加上实际本产品配置、hook与安装目录；不strict封锁所有项目CLAUDE.md，避免妨碍正常开发。删除上游ask、allow逃生口、原文log入口，输出前用本项目固定pattern id + hash审计。它只能是纵深防御：同用户进程本有文件写权限，且fail-open意味着某些故障会放行，不能宣传成不可绕过边界。（[MIT条款](https://github.com/karanb192/claude-code-hooks/blob/57a28c7687305e4a9b9dd50490e9b3c552c71ca4/LICENSE)）

【官方契约】plugin标准目录是manifest与根hooks；`${CLAUDE_PLUGIN_ROOT}`指向插件脚本，市场source相对市场根。SessionStart提供规则上下文，包含resume/compact等来源；插件output-style会改变用户风格，`force-for-plugin`会强制覆盖，不适合默认使用。审计不能放在随更新变动的plugin root或默认卸载会删除的数据目录。（[marketplace](https://code.claude.com/docs/en/plugins/create-marketplace#write-relative-paths-from-the-marketplace-root)、[SessionStart](https://code.claude.com/docs/en/hooks#sessionstart)、[styles](https://code.claude.com/docs/en/output-styles#frontmatter-reference)、[环境变量](https://code.claude.com/docs/en/plugins-reference#environment-variables)）

【工程判断】两条入口共用一份Python/hooks/rules，必须互斥，不能一次事件双写审计/双耗预算。旧用户默认仍用managed安装；显式迁移时改前备份，只移除本产品命令和标记块，逐字节验证块外正文不变。插件卸载停止其hook，但不删除长期审计；managed卸载也保留使用者后续偏好。不要假定插件安装本身会替本产品备份/迁移旧settings。插件manifest不要重复声明默认hooks文件，上游重复声明已导致加载错误。

## 无人值守项目多在批准或循环，不能替代本任务授权政策

| 候选 | 维护/许可证（官方 API 或仓库） | 源码/文档事实 | 建议与边界原因（判断） |
|---|---|---|---|
| gstack question-preference-hook | 137,116★；最新 2026-10-21，`b616d6c53dd8fc0d8db210444b44a131b553b83e`；MIT；[API](https://api.github.com/repos/garrytan/gstack)、[提交](https://github.com/garrytan/gstack/commit/b616d6c53dd8fc0d8db210444b44a131b553b83e)。近期 [PR #3328](https://github.com/garrytan/gstack/pull/3328) 当日关闭并有 2 评论；这是活动证据，不能推广成所有 issue 都及时处理。 | Bun TypeScript；依赖项目 slug、question registry、one-way classifier、存储偏好、日志命令。带 `<gstack-qid:...>` 且用户已有 never-ask 偏好的可逆问题才 deny 并提示推荐项；one-way 总是询问；推荐项不明/多项则放行。直接日志包含问题摘要前 200 字和选择前 64 字，日志错误捕获后仍继续 deny。[固定源码](https://github.com/garrytan/gstack/blob/b616d6c53dd8fc0d8db210444b44a131b553b83e/hosts/claude/hooks/question-preference-hook.ts) | **保留自己的** AskUserQuestion 适配；可借鉴其“多问题全满足才介入”和拒绝歧义，但不直接拉整套。裸 AUQ 没有 gstack marker/偏好，直接装通常不会处理；原文审计及 best-effort 日志不满足本任务的先写且不存原文。源码仍称答案协议未 pinned，已落后于当前官方协议，不能原样复制其历史理由。 |
| Anthropic ralph-wiggum | 父仓库 152,714★；最新 2026-10-21，`d014fc0fa64280540728772fc84063918ed185cc`；API 无 SPDX；根 [LICENSE.md](https://github.com/anthropics/claude-code/blob/d014fc0fa64280540728772fc84063918ed185cc/LICENSE.md) 是 all-rights-reserved / Commercial Terms，不是 MIT/Apache。插件目录 `LICENSE` 请求 404。 | Bash + jq + Perl；共享 `.claude/ralph-loop.local.md`，迭代/完成字符串控制，max=0 可无限；扫描 transcript；源码不校验 session_id；没有 HID 和脱敏审计。[固定 Stop](https://github.com/anthropics/claude-code/blob/d014fc0fa64280540728772fc84063918ed185cc/plugins/ralph-wiggum/hooks/stop-hook.sh)。官方插件的 [跨会话问题 #64](https://github.com/anthropics/claude-plugins-official/issues/64) 是历史已报告风险，未现场复现。 | **不全局采用、不 vendor**。可作为用户明确启动、拥有测试完成条件的独立循环工具；不等价于低打扰 Stop。不能因“Anthropic 官方”假定可按 MIT vendor。最新父仓库活跃也不能证明该插件最近有维护。 |
| ianymu/claude-verify-before-stop | 0★；最新 2026-05-20，`4ba3281c2068ce18578a1599d292c25bb8c203ce`；MIT；[API](https://api.github.com/repos/ianymu/claude-verify-before-stop)。[issue #2](https://github.com/ianymu/claude-verify-before-stop/issues/2) 5 月 20 日起仍 open、0 评论，不能建立响应能力。 | Bash + Python stdlib；stop_hook_active=true 直接放行；git diff/untracked 有改动就要求最近五分钟 VERIFY_ACTION 和 VERIFIED；模型自行 echo 两个标记可过；exit 2 阻 Stop。状态文件项目共享，没有 HID/会话审计。[固定源码](https://github.com/ianymu/claude-verify-before-stop/blob/4ba3281c2068ce18578a1599d292c25bb8c203ce/verify-before-stop.sh) | **保留自己的**。其需求是“改文件要验证”，不是“少问”；会把无关 dirty tree 当成本任务未验收，模型自报标记也不是独立测试证据。没有必要为了 50 行左右的不同政策引入 vendor。 |
| yuuichieguchi/claude-remote-approver | 72★，最新 2026-03-02，`3df14bd404d937551ada31566657960dc4ba05dd`；MIT；[API](https://api.github.com/repos/yuuichieguchi/claude-remote-approver)。[issue #8](https://github.com/yuuichieguchi/claude-remote-approver/issues/8) 7 月 25 日提出 hook 超时缺陷，仍 open、0 评论。 | Node >=18 + ntfy 手机端；PermissionRequest 转发手机按钮；默认 120 秒、计划 300 秒，无回答回 CLI ask；工具名/命令/路径经第三方；topic 泄露可被别人回应。[固定 README](https://github.com/yuuichieguchi/claude-remote-approver/blob/3df14bd404d937551ada31566657960dc4ba05dd/README.md) | **默认不采用**；只适合未来显式可选远程人工审批。默认引入第三方且超时仍等待，与本需求相反；远程 Allow 也不能替代 PermissionRequest 只 deny 政策。 |
| coa00/claude-push | 8★；最新 2026-02-24，`7671be44f612d5d5fca4af08afbb41b62bcb94e9`；MIT；[API](https://api.github.com/repos/coa00/claude-push)；issues 查询为空，无响应样本。 | Bash + jq + curl + ntfy；90 秒后回终端，安装器发送测试通知。[固定 README](https://github.com/coa00/claude-push/blob/7671be44f612d5d5fca4af08afbb41b62bcb94e9/README.md) | **默认不采用**；没有解决无人回复，增加网络、第三方和安装副作用。不是不装 Node 就自动适合。 |
| tantk/permission-hook | 4★；最新 2026-02-10，`aeb72f4d5e12f34d359090d52c5f232315a602c9`；API license=null；[README 自称 MIT](https://github.com/tantk/permission-hook/blob/aeb72f4d5e12f34d359090d52c5f232315a602c9/README.md)，但[根树](https://api.github.com/repos/tantk/permission-hook/git/trees/aeb72f4d5e12f34d359090d52c5f232315a602c9)没有 LICENSE 文件；issues 空，无响应样本。 | Rust >=1.70 源码构建，release 二进制路径可避开本地 Rust；三层 auto-approve/auto-deny/ask，trust 模式允许除 deny 之外全部；可选 LLM/webhook；CSV 日志例子存原命令。[固定 README](https://github.com/tantk/permission-hook/blob/aeb72f4d5e12f34d359090d52c5f232315a602c9/README.md) | **不 vendor / 默认不采用**。许可版权文本不完整，策略范围比只拒绝大，原文日志不合要求。可选 LLM 即便默认关也不是此次要引入的必要依赖。 |
| auto-approve-claude-plan | 6★；最新 2026-06-06，`268e8aa1f4877075c5fccce00a34fe11c576ac95`；MIT；[API](https://api.github.com/repos/yigitkonur/auto-approve-claude-plan)；[issue #1](https://github.com/yigitkonur/auto-approve-claude-plan/issues/1) 从 2 月 18 到 3 月 8 关闭、1 评论。 | 自动批准计划并可切 bypass；前轮已检查，不重复运行。[固定仓库](https://github.com/yigitkonur/auto-approve-claude-plan/tree/268e8aa1f4877075c5fccce00a34fe11c576ac95) | **排除**，直接违反不盲批计划/不切换权限模式。 |

【工程判断】gstack是本组维护最活跃、最有参考价值的工程，但其marker/registry/偏好属于整个技能系统；裸AUQ通常不会命中，直接安装并不自然解决当前卡片。其原文审计、best-effort记录和Bun依赖也不匹配本项目。可参考歧义推荐项不决策、多问全满足才介入及一向门/双向门分流；不应为了“借鉴别人”复制一个更大的不匹配系统。Ralph用于用户明确启动的可测任务循环，不是全局Stop反问修复；verify-before-stop验证的是模型写的标记，不是测试真实成功。

【官方契约】当前Stop除了合法的 `decision:block`，也支持 `hookSpecificOutput.additionalContext` 继续会话，转录标为feedback而不是hook error；仍受active与连续上限约束，工具调用会重置平台连续计数。因此平台8次上限不能替代本项目每人类回合3次预算。（[Stop协议](https://code.claude.com/docs/en/hooks#stop-decision-control)）【工程判断】这可作为减少错误通知的改进，但文档未说明最低版本；先针对运行版本与现有测试验证，再决定是否更改输出。不把这个可选改动列作接入第三方的必要条件。

【上轮证据继续有效】HID只能报告本机输入空闲；hook只在事件到来时采样一次，不会在五分钟后自动重跑已经打开的卡片。Remote Control会抑制提问超时计时，dialogExpiry不接管AUQ或权限请求；全局Proactive也不解决权限层。拒绝ExitPlanMode后仍可能只能做只读工作，不能承诺实现继续。浏览器站点、App能力、剪贴板及macOS系统授权没有证据保证全部流经PermissionRequest；不能把桌面专有弹窗全部写成已覆盖。（[提问超时](https://code.claude.com/docs/en/tools-reference#question-auto-continue-timeout)、[dialogExpiry](https://code.claude.com/docs/en/settings-reference#dialogexpiry)、[Remote Control限制](https://code.claude.com/docs/en/remote-control#limitations)、[Apple IOHID](https://github.com/apple-oss-distributions/IOHIDFamily/blob/main/IOHIDSystem/IOHIDSystem.cpp)）

本次对Desktop spawn_task专用替换没有找到合适公开项目，检索无结果不证明不存在。继续用小型deny重定向到Agent后台/worktree，保留原任务授权、分支收尾与验证要求；既不需要新的调度器，也不能借此扩大push/deploy权限。规则层继续直接用Proactive处理日常假设，只保留授权边界、审计引用、安全改写、待办与分支收尾这些本地差异。（[官方subagents](https://code.claude.com/docs/en/sub-agents)、[官方Proactive](https://code.claude.com/docs/en/output-styles#proactive)）

### 2026年10月新项目贴题，但尚不成熟

| 新候选（均为 2026） | 维护和许可证 | 文档/代码事实 | 判断 |
|---|---|---|---|
| federbenjamin/dialog-timeout | 创建 10 月 8 日；3★；10 月 20 日最新；MIT；`24357ed1485306320a6c2688ad8c703698a54539`；[API](https://api.github.com/repos/federbenjamin/dialog-timeout)、[固定源码](https://github.com/federbenjamin/dialog-timeout/blob/24357ed1485306320a6c2688ad8c703698a54539/hooks/register.ts)；issues=0，无响应样本。 | README 自报测试 CLI 2.1.295；每 30 秒用 `$.prompt.fill` 探测 dialog，默认 30 分钟后 `$.turn.abort` 关闭未回答 dialog，再 `$.prompt.submit({asUser:true})` 注入 away 消息；不触碰非交互运行；新 TypeScript `claude-code` Register API；无 HID、无先审计。[固定 README](https://github.com/federbenjamin/dialog-timeout/blob/24357ed1485306320a6c2688ad8c703698a54539/README.md) | 不默认采用；是值得后续隔离实验的“已有卡片超时”方向，不作为当前成熟依赖。其“人还在”实际上是有没有回答 dialog，阅读屏幕也会超时；turn abort 可能取消本轮其他状态，需验证。 |
| varunmoka7/unattended | 创建 10 月 13 日；0★；仅 10 月 13 日提交；MIT；`8b1258d6ed6443467507ead1e7e8c4d3e66bd15f`；[API](https://api.github.com/repos/varunmoka7/unattended)、[固定源码](https://github.com/varunmoka7/unattended/blob/8b1258d6ed6443467507ead1e7e8c4d3e66bd15f/hooks/register.ts)；issues=0。 | README 要求 CLI >=2.1.287；手动 `/unattended` 或环境开关；拒绝 AUQ 和 EnterPlanMode，提示模型自己选；明确不处理权限提示；无网络/模型调用；不读取 HID；不写审计。[固定 README](https://github.com/varunmoka7/unattended/blob/8b1258d6ed6443467507ead1e7e8c4d3e66bd15f/README.md) | 不采用：不区分风险问题，通用“自己选”不等于只处理已授权实现细节；不覆盖 ExitPlanMode/权限；新 mod API 增加 Desktop 兼容未知。 |
| metamol0627/ask-later | 创建 10 月 8 日；0★；最新 10 月 8 日；MIT；`db4aa53867d3d61fa7dc3f01f3c7cc91eb69ce7b`；[API](https://api.github.com/repos/metamol0627/ask-later)；issues=0。 | Node >=20、无 npm 依赖；PENDING/INBOX Markdown 异步队列；SessionStart/UserPromptSubmit/PostToolUse/Stop/SessionEnd 五 hooks，Stop 每 unread set/session 默认最多 2；README 只验证 Windows 11 + Node24 + CLI2.1.268，macOS/Linux 未试；uninit 不删 CLAUDE.md/.gitignore 追加内容。[固定 README](https://github.com/metamol0627/ask-later/blob/db4aa53867d3d61fa7dc3f01f3c7cc91eb69ce7b/README.en.md) | 不默认采用。它是异步人工输入队列，不实际拦截 AUQ；节点/新增文档状态面较大；可借鉴明确“棚置依赖决策的工作、继续独立工作”的规则，无需搬整套代码。 |

另外发现 [yrambler2001/claude-code-permission-hook](https://github.com/yrambler2001/claude-code-permission-hook)，描述称自动回答PermissionRequest避免rm safety卡住；其创建于10月10日、0★，未进一步执行验证。不能因专门解决无人值守就采用与危险删除边界冲突的批准策略。

【工程判断】dialog-timeout触及“已有卡片持续等待”这个真实缺口，但turn abort/asUser注入是新mod接口，和传统command hook覆盖不同。它无先审计、无HID，按未回答时间视为离席；不能据README扩展到Desktop全授权层。ask-later可参考棚置依赖决策、继续独立工作的规则，但它是人类异步队列，不实际截获所有AUQ，也不需要为此引入Node和更多状态文件。三个新项目都无足够issue样本，“0 issues”是缺证据，不是响应优秀。

## B阶段应保留全部旧场景，再验证新增边界

当前交付是A选型与研究，不改变实际用户配置、没有发布，也未声称新组件测试通过。B应在最新main分支上实现shfmt适配、MIT来源配置保护、官方可选插件入口，删掉被替代解析代码。第三方来源/版权必须进入README致谢及许可文件；无可匹配成熟实现的本地政策明确说明保留原因，而不是宣称全部已被现成方案替换。

| 验证范围 | 不能少的场景 | 通过的含义与局限 |
|---|---|---|
| 原51项基线 | HID活跃/未知；安全答案与风险拒绝；ExitPlanMode/PermissionRequest deny；Stop预算、去重、回合重置、并发审计；安装幂等/卸载/正文保留 | 原场景覆盖不能因换组件缩水；这是确定性协议验证 |
| AST改造 | 原48条分类语义；quoted data与执行节点；wrappers/find-exec；Unicode offset；重定向；参数guard；parser缺失/超时/坏JSON | parser接受不等于分类成功；失败必须静默无意见 |
| config-guard | settings/hooks/manifests/CLI写入与读取区分；symlink/不存在父路径；自定义根；sed/tee/mv/cp/rm/redirect；无原文审计；审计失败；不接受上游ASK/ALLOW开关 | 已知路径纵深防御，仍不涵盖任意运行时代码 |
| plugin与迁移 | root含空格；全部event；compact重新上下文；规则缺失fail-open；互斥注册；幂等无新增备份；卸载保留日志与用户正文 | JSON/临时目录成功不等于真实Desktop已加载 |
| 发布核对 | 仅当前PR头获得Claude通过后合并；再安装；settings与规则块外正文核对 | 必须用精确SHA门禁，头变重新复查；真实可见行为未验仍保留缺口 |

所有自动决定先写脱敏审计，不存原文，至少186天；解析失败、脚本错误、超时和审计失败按本任务fail-open，不配置 `onFailure:block`。config-guard只deny；无任何自动学习的新allow规则、无权限模式切换、无默认第三方通知、无付费模型调用。复查前只用临时配置根和任务临时二进制，实际用户配置不改。发布仍需与当前头完全一致的Claude复查通过，不能从文档或旧测试推断批准。（[官方故障语义](https://code.claude.com/docs/en/hooks#block-the-action-when-a-hook-fails)）

## 第三方实验前来源、版本与校验计划

以下计划先写入报告，随后才允许下载到任务临时目录开展确定性实验。复查前不修改真实 Claude 配置、不安装到全局 PATH、不调用任何模型。

| 工具 | 来源与版本 | 校验方式与固定 SHA256 | 用途 |
| --- | --- | --- | --- |
| shfmt | [官方 GitHub release v3.14.1](https://github.com/mvdan/sh/releases/tag/v3.14.1)，2026-09-06 | 使用 [GitHub release API](https://api.github.com/repos/mvdan/sh/releases/tags/v3.14.1) 的 release asset `digest`，下载后以 hashlib.sha256 校验精确字节，匹配后 chmod 并核对 `--version` | 临时目录解析 stdin 输出 JSON AST |

固定资产摘要（2026-10-21 实时读取）：

- `shfmt_v3.14.1_darwin_arm64`：`b7c872db63553ccffc7253aba3ed7d4885a27d83f1ba567b1138c6315a5847e5`
- `shfmt_v3.14.1_darwin_amd64`：`d33eee0da0f92835b3562e9767a05cee7e4eaeef47daa03bfd09da17b4b590a6`
- `shfmt_v3.14.1_linux_arm64`：`5f2db09dae91fca848f7adbdd014632e921a383863a2ad7e0450ad3aba0c6489`
- `shfmt_v3.14.1_linux_amd64`：`76e77641faa025814b77f153b29796b8e6fa2fca03e0c76a691608b86c7ea7bf`

许可证为 [BSD-3-Clause](https://github.com/mvdan/sh/blob/v3.14.1/LICENSE)，分发执行文件需要保留许可证/版权；不需要安装 Go、Rust 或 Node。安装器若支持下载，应只使用固定官方 URL 与以上摘要，下载/校验失败必须明确报告而不以未校验文件继续。

DCG 不下载执行文件、不运行：当前和 [v0.15.3 的 LICENSE](https://github.com/Dicklesworthstone/destructive_command_guard/blob/v0.15.3/LICENSE) 均是带 OpenAI/Anthropic rider 的自定义许可证，禁止把它归类为普通 MIT。


## B阶段本地实现与验证记录

2026-10-21：已完成shfmt AST替换、MIT config-guard移植、可选官方插件入口、固定摘要依赖安装和迁移适配。Stop输出保持已有block协议，未增加未验证的兼容性改动。原手写shell分段、引号屏蔽和heredoc扫描已删除；仍保留命令政策和少量静态参数解码。

Python 3.9与3.14分别通过102项unittest，保留前版51项全部场景与48条shell基线，并新增25条shell语义样例、parser故障/非阻塞超时、配置保护读写与脱敏审计、实际调用临时安装的guards、插件迁移及逐字节用户正文保留。官方release下载与摘要验证也通过系统Python在隔离目录完成。独立源码复核发现并修复了旧安装器尾部空白裁剪和CRLF转换问题。

这些结论只证明确定性本地行为；未调用付费模型，未验证Desktop/手机端到端，尚需PR CI与精确当前头的Claude复查。发布状态以任务进度和PR为准。复查通过前没有修改真实配置，临时依赖将在不再需要时清理。
