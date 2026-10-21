# 让 Claude 离席继续，保留真正的决策边界

**结论：最适合本次桌面 Code 场景的组合，是少问的行为规则，加上仅在本机持续空闲后介入的确定性 hooks，而不是全局自动批准或无限续跑。** AskUserQuestion 可以用官方支持的 `PreToolUse allow + updatedInput.answers` 作答；只替负责人选择已授权、低风险问题的推荐项或第一项。文字结尾“要我继续吗？”可用保守 Stop hook 提醒继续已授权工作，但要限制次数、隔离会话并防重复。剩余权限请求在离席时统一拒绝、要求改写或跳过；危险删除永不自动允许，计划审批也不盲批。**Remote Control 连接时，`askUserQuestionTimeout` 的计时器不启动；当前文档还明确 `dialogExpiry` 不控制权限请求和 AskUserQuestion，两个超时设置都不能充当本场景的主要保障。** 本报告区分官方契约、安装包静态核验、本机确定性测量、社区实现和工程推断；尚未证明这些策略在真实 Desktop Code 会话及手机端的端到端效果。

## 调研发现：减少等待要分别处理提问、停止和权限

### A1：AskUserQuestion 有明确自动作答协议，Remote Control 则限制超时

**【官方文档】** `askUserQuestionTimeout` 接受 `60s`、`5m`、`10m`、`never`，默认 `never`。到期提交已经选中的选项，并告诉 Claude 用户可能离开，由它判断能否继续；文档没有承诺自动选择推荐项。没有选择任何答案时，精确底层结果究竟是空 `answers`、省略字段还是特殊文字，文档没有给出结构保证，不能把“自动继续”写成“自动选第一项”。任意按键重置计时，终端报告窗口聚焦时暂停；后台会话、屏幕阅读器模式、**已连接 Remote Control 时不启动计时**。它也不解决 ExitPlanMode 或权限弹窗。官方 CHANGELOG 记载 2.1.200 后 AskUserQuestion 默认不再自动继续，应避免沿用旧版默认超时的经验。([工具超时说明](https://code.claude.com/docs/en/tools-reference#question-auto-continue-timeout)，[设置参考](https://code.claude.com/docs/en/settings-reference#askuserquestiontimeout)，[官方变更记录](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md))

**【官方文档】** `PreToolUse` 对 AskUserQuestion 仅返回 `allow` 不够，必须同时提供 `updatedInput`。`answers` 是“完整问题文本 → 完整选项 label 字符串”的对象；多选答案用逗号连接 label。`updatedInput` 替换整个输入，因此实现应复制原始 `tool_input` 后补答案，保留 `questions` 及其他字段，不能只返回 `answers`。文档给出 Desktop 等会话对 hooks 和设置的支持范围，但产品支持范围不能替代本机实际对话框实验；MCP 工具自身声明 `requiresUserInteraction` 时，也不能据此绕过其交互。([交互工具及答案格式](https://code.claude.com/docs/en/hooks#tools-that-require-user-interaction)，[hooks 支持范围](https://code.claude.com/docs/en/hooks)，[设置作用范围](https://code.claude.com/docs/en/settings))

下面是协议示例，不是 settings 配置；实际实现仍应复制整个原始输入：

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow",
    "updatedInput": {
      "questions": [{
        "question": "Which test runner should we use?",
        "header": "Tests",
        "options": [
          {"label": "Existing runner (Recommended)", "description": "Reuse the project setup"},
          {"label": "Alternative runner", "description": "Introduce another setup"}
        ],
        "multiSelect": false
      }],
      "answers": {"Which test runner should we use?": "Existing runner (Recommended)"}
    }
  }
}
```

**【社区代码】** gstack 把可逆决定和不可逆决定分开：前者可以 deny 并提示按推荐方案继续，后者保留人工决定；其历史源码关于答案不能注入的解释，已被当前官方协议超越。WorkerDeck 在 SDK host 的 `canUseTool` 路径返回 `updatedInput.answers`，自动模式选每问第一项，提供了答案映射的实现参考，但它不是 Desktop hooks 的现场证明。部分 Slack approval README 仍说不能注入答案，coucou 的相关讨论也明确方案未经验证，应按证据时效和产品层次消解冲突。([gstack 源码](https://github.com/garrytan/gstack/blob/main/hosts/claude/hooks/question-preference-hook.ts)，[WorkerDeck 权限指南](https://workerdeck.github.io/workerdeck/docs/guides/permissions/)，[Slack approval](https://github.com/doyasu24/claude-hooks-slack-approval)，[coucou #94](https://github.com/Louis-CFM/coucou/issues/94))

**【工程判断】** 三种方案应组合而非互相替代。CLAUDE.md 能在源头减少问题，但只是模型指令；超时简单，却在本任务常用的 Remote Control 条件下不工作，也不给出确定的推荐项选择；离席 hook 则能确定性回答有限类型的问题，并留下审计记录。因此推荐“规则优先、离席低风险自动答、超时可选”。推荐项不是安全证明：付费、删除、对外发送、发布、生产操作、凭据、权限变化和超出既有授权的事项不能只凭 `(Recommended)` 自动批准。已有有效答案应保留，重复问题键、类型错误、歧义推荐项等异常应不介入。

### A2：Stop 能阻止多余反问结束，但不能证明任务仍需继续

**【官方文档】** Stop 在主 agent 回答结束时触发，用户主动中断不触发，API 错误归 StopFailure。输入包含 `session_id`、`transcript_path`、`stop_hook_active` 和 `last_assistant_message`。输出 `{"decision":"block","reason":"…"}` 会把原因交给 Claude，使它继续；这不是后台唤醒已经结束会话的调度器。Stop 支持 command、prompt 和 agent 类型；prompt hook 的 `ok:false` 原因可成为继续指令，`impossible:true` 可结束，agent hook 仍为实验性。([Stop 输入与输出](https://code.claude.com/docs/en/hooks#stop)，[prompt hooks](https://code.claude.com/docs/en/hooks#prompt-based-hooks)，[agent hooks](https://code.claude.com/docs/en/hooks#agent-based-hooks))

**【官方文档与社区源码】** 官方要求在 `stop_hook_active:true` 时提前放行；内置连续阻止八次的限制只覆盖中间没有 tool call 的情况，不能当作完整的循环保护。`claude-verify-before-stop` 也先检查这个字段，再根据验证记录决定是否阻止。另一个 deference mirror 项目专门处理 “would you like me to…” 结尾，但维护讨论记录了误报、引用文本识别不足、强制继续产生低价值忙碌等问题。([Stop 循环上限](https://code.claude.com/docs/en/hooks-guide#stop-hook-hits-the-block-cap)，[verify-before-stop 源码](https://github.com/ianymu/claude-verify-before-stop/blob/main/verify-before-stop.sh)，[deference mirror 讨论](https://github.com/neomjs/neo-agent-brain/issues/136))

**【工程判断】** 确定性 hook 只检查可靠的 assistant 最后非空行，对“要我……吗？”、“需要我……吗？”、“Would you like me to…?”、“Shall I…?” 等可选继续邀请做窄匹配。代码、引用、用户文本、工具返回、一般问号和明确“待你决定”不应触发。拿不到可靠 assistant 文本就不介入，不能扫描整段历史寻找一句“要我”。即使命中，也只要求完成已经授权、可逆的剩余工作；没有剩余工作应给完整汇报后停止，不能创造新任务。涉及发布、付款、生产、永久删除或明确缺授权的结尾保守放行，允许漏掉部分多余请示以减少越权风险。

本轮选择 `stop_hook_active` 提前放行、会话隔离状态、相同结尾指纹去重，以及默认每会话最多三次强制继续。计数和追加记录需要并发锁，日志失败则不输出 block。prompt/agent hook 能增加语义判断，但引入模型成本、延迟和新的失败路径；本轮不采用它们，也不声称正则能判定真实授权。

### A3：HIDIdleTime 实测成本低，但它只代表本机输入空闲

**【Apple 源码】** IOHIDSystem 把 uptime 与 `lastUndimEvent` 的差值转换成纳秒来输出 idle time；活动报告路径能重置该时间，events 未开启时还可能返回零。这是输入活动信号，并非人体在场认证。Apple 文档将 IOHIDSystem 标为 deprecated，没有找到把 HIDIdleTime 保证为稳定公开在场 API 的依据。历史 Karabiner issue 曾出现虚拟键盘输入没有重置 idle timer 的情况；这是历史反例，不代表当前版本必然如此。([Apple 实现](https://github.com/apple-oss-distributions/IOHIDFamily/blob/main/IOHIDSystem/IOHIDSystem.cpp#L3299-L3320)，[活动重置路径](https://github.com/apple-oss-distributions/IOHIDFamily/blob/main/IOHIDSystem/IOHIDSystem.cpp#L3483-L3501)，[Apple API 文档](https://developer.apple.com/documentation/kernel/iohidsystem)，[Karabiner 历史问题](https://github.com/pqrs-org/Karabiner-Elements/issues/385))

**【本机只读测量，2026-10-21】** 在 Python 3.9 环境分别采样十次，不注入键鼠事件。普通 `ioreg -c IOHIDSystem` 每次得到一个十进制 HIDIdleTime，输出约 476 KB，中位 31.422 ms、最大 32.052 ms。`ioreg -r -c IOHIDSystem -d 1` 输出约 4 KB，中位 16.993 ms、最大 17.772 ms，适合作为实现命令。不能漏掉 `-r`：`ioreg -c IOHIDSystem -d 1` 虽每次退出码为零，却没有 HIDIdleTime，因为深度限制从注册树根开始截断。采样值由约 0.666 秒增至 0.947 秒，命令自身没有重置计时；这些数字只描述本次机器和采样。

**【工程判断】** 默认连续 **300 秒** 无本机输入才介入，给阅读屏幕和短暂离席留出余量，同时把无人等待控制在分钟级；这不是 Apple 推荐值。使用固定 argv、子进程两秒超时，解析非负整数纳秒；多个有效值取最小值，有活动即保守视为有人。非 macOS、超时、空输出、非零退出、解析异常均按未知处理，不介入。阈值和启用状态应可配置。

看屏幕但不动键鼠会被误判离席；手机或 Remote Control 发消息不一定重置本机 HID；自动输入和远程桌面也可能干扰计时。因此“人在电脑前时不介入”的实际保证只能是**检测到近期本机 HID 活动时不介入**，不能扩展为“任何设备上有人时都不介入”。本轮未制造锁屏、睡眠唤醒、远程桌面、多用户切换或虚拟输入事件，以免干扰正在工作的环境。不要记录原始 ioreg 输出，其中可能包含设备标识。

### A4：PermissionRequest 可以拒绝后继续，不能覆盖全部桌面授权层

**【官方文档】** PermissionRequest 在工具即将请求权限或无法显示提示而将自动拒绝时执行，输入包括工具名、输入、会话和权限模式等。拒绝应返回 `hookSpecificOutput.decision.behavior:"deny"`，`message` 给模型解释，`interrupt:false` 避免直接中止 Claude；仅退出码 2 不构成这个事件的有效决定。沙箱命令的网络权限请求不触发该 hook。([PermissionRequest 事件](https://code.claude.com/docs/en/hooks#permissionrequest)，[decision control](https://code.claude.com/docs/en/hooks#permissionrequest-decision-control))

**【官方文档】** 在会询问的模式中，PermissionRequest hook 可以回答 critical-path 删除提示；普通 PreToolUse allow 不能批准这种删除。Desktop auto 模式对该类删除直接拒绝，bypass 仍可能询问。新版终端对 auto/bypass critical-path 删除提供两分钟倒计时，并在同会话三次超时后立即拒绝，但显式 ask 规则没有这种倒计时；这些终端行为不能自动推广为所有 Desktop 原生提示都已解决。([各模式危险删除](https://code.claude.com/docs/en/permission-modes#critical-path-removals-in-each-permission-mode)，[超时与拒绝](https://code.claude.com/docs/en/permission-modes#time-limits-and-denials-in-auto-and-bypasspermissions-modes))

**【工程判断】** 离席时对实际到达 PermissionRequest 的剩余请求统一 deny，不尝试维护另一套“哪些足够安全可 allow”的清单。message 要求不重复同一请求、不削弱保护，只在已有授权下改用明确范围的安全替代；不能安全完成则记入“待你决定”，继续其他独立工作。尤其不能暗示把受保护删除改用 Python、解释器或别的工具绕过。真实危险删除永不自动 allow。hook 出错、空闲状态未知或审计失败时保持原流程；不能给 hook 配失败即 block。

内置浏览器站点授权、computer-use 应用和剪贴板授权位于 Desktop/MCP 交互层；官方通用 PermissionRequest 契约不足以证明它们全部经过此 hook。尚未验证的弹窗必须明确列为覆盖缺口。

### A5：ExitPlanMode 可以取消等待，但拒绝不等于批准实现

**【官方文档】** ExitPlanMode 的 `plan` 和 `planFilePath` 通常由 Claude Code 在模型输入后注入，旧 `allowedPrompts` 已废弃。其交互也要求 `allow + updatedInput` 才能跳过提示。计划模式内，普通 PreToolUse allow 不能批准非只读操作；ExitPlanMode 是特殊例外。([工具输入](https://code.claude.com/docs/en/hooks#pretooluse-input)，[交互工具协议](https://code.claude.com/docs/en/hooks#tools-that-require-user-interaction)，[plan mode hook 批准](https://code.claude.com/docs/en/permissions#hook-approvals-in-plan-mode))

**【社区实现与本轮判断】** `auto-approve-claude-plan` 在 PermissionRequest 自动批准计划，还请求切换 bypassPermissions，明显扩大后续权限。本轮只借鉴事件匹配，离席时拒绝 ExitPlanMode 并说明“计划没有获得新的批准；只继续当前模式允许且已授权的工作；不要反复请求批准；其余记入待你决定”。**拒绝通常意味着仍在 plan mode，只能继续相应只读工作，不能承诺代码实现继续完成。** 若原任务已经明确授权实现，CLAUDE.md 应减少主动进入 plan mode；若用户要求先计划后批准，不能用 hook 改写该边界。([自动批准项目](https://github.com/yigitkonur/auto-approve-claude-plan))

### A6：手机能处理部分等待，通知不等于无人执行保障

**【官方文档】** Desktop Code 本机会话连接 Remote Control 后，可出现在同账号设备；Desktop 的新会话连接设置及会话 Environment card 用于检查配置和连接状态。手机可看进度、发消息、回答问题和部分权限请求，本机与进程仍需保持运行。`inputNeededNotifEnabled` 在问题或权限等待时推送；`agentPushNotifEnabled` 允许 Claude 自行决定发送完成等主动通知，二者默认 false，均依赖 Remote Control。手机需同账号同组织并允许系统通知，Focus、摘要、省电策略等会造成延迟，终端聚焦/输入状态也可能抑制推送。([Desktop 连接](https://code.claude.com/docs/en/desktop#control-which-sessions-appear-on-your-other-devices)，[input-needed](https://code.claude.com/docs/en/settings-reference#inputneedednotifenabled)，[agent push](https://code.claude.com/docs/en/settings-reference#agentpushnotifenabled)，[手机推送](https://code.claude.com/docs/en/remote-control#mobile-push-notifications)，[手机能力](https://code.claude.com/docs/en/mobile))

**【官方文档与静态 schema 差异】** 当前 `dialogExpiry` 文档明确：默认五分钟，只控制其他远程转发 dialog 的取消期限，**权限请求和 AskUserQuestion 使用各自流程，不受它控制**；Remote Control 限制页也说明两者继续等待。本机只读核验的 Desktop 3.1623.0 安装包 schema 仍用更宽泛的 remote permission/user dialog 描述。应保留这种证据差异，以更具体的当前文档限制作为实现依据，不能宣传缩短 dialogExpiry 就能解决权限或提问卡住。([dialogExpiry](https://code.claude.com/docs/en/settings-reference#dialogexpiry)，[Remote Control 限制](https://code.claude.com/docs/en/remote-control#limitations))

**【官方文档】** 内置浏览器可按站点选择 Allow once、Always allow 或 Deny；Always allow 按设备和站点含子域保存，敏感操作仍会提示。computer-use 应用能力通常按会话允许，Dispatch 发起的会话授权还有期限；macOS Accessibility、Screen Recording 等属于系统授权。某些远程弹窗不转发，例如用量 credits 同意。**没有证据证明手机能操作每一种浏览器、剪贴板、App 能力或系统 TCC 提示。** ([浏览器授权](https://code.claude.com/docs/en/desktop#approve-claudes-actions-on-a-site)，[Desktop app 权限](https://code.claude.com/docs/en/desktop#app-permissions)，[computer-use 会话权限](https://code.claude.com/docs/en/computer-use#approve-apps-per-session)，[Remote Control](https://code.claude.com/docs/en/remote-control))

本轮保留已有 input-needed 通知，主动推送做显式可选项，不新增第三方消息链路；未给手机发送测试通知，也未启动登录或真实授权卡片。通知是人工例外的补充，不能作为无人必定恢复的证据。

### A7：Proactive 适合可选启用，不能消除授权边界

**【官方文档】** `Proactive` 让 Claude 对例行问题作合理假设并直接实施，除用户要求外少进入 plan mode；删除数据或修改共享/生产系统前仍会在对话中确认。它只是行为指令，不改变 permission mode，不消除工具权限提示。`outputStyle` 值大小写敏感，应为 `Proactive`；用户级默认可被项目覆盖，当前版本切换后从下一条用户消息生效。([Proactive 行为](https://code.claude.com/docs/en/output-styles#proactive)，[切换方式](https://code.claude.com/docs/en/output-styles#change-your-output-style))

**【工程判断】** 适合作为安装时可选项，不宜覆盖用户现有全局 outputStyle。自定义 CLAUDE.md 能表达更准确的权限和汇报规则，Stop 仍需有预算；官方曾修复 Proactive 等待后台命令时反复查日志的 busy loop，也说明“更自主”本身不是完成标准。没有进行模型 A/B 测试，不能声称本机少问了多少。([官方变更记录](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md))

### A8：社区持续执行值得借鉴的是完成条件、会话隔离和有限重试

**【作者说明与源码】** Geoffrey Huntley 的 Ralph 本质是把任务反复交给 agent 的 Bash loop，强调观察失败、调整 prompt；它不是统一的桌面授权修复。Anthropic Ralph 插件在共享 `.claude/ralph-loop.local.md` 中维护 iteration、max_iterations、completion_promise，到上限或命中完成承诺才停止；所审源码没有把该共享状态匹配 session_id。官方插件 issue 有同项目另一会话被 Ralph 状态影响的报告，属于已报告缺陷，未在本机复现。([Ralph 原文](https://ghuntley.com/ralph/)，[Anthropic Stop 源码](https://github.com/anthropics/claude-code/blob/main/plugins/ralph-wiggum/hooks/stop-hook.sh)，[跨会话问题 #64](https://github.com/anthropics/claude-plugins-official/issues/64))

**【社区文档】** `upneja/ralph-wiggum` 以 RALPH.md 是否存在决定 Stop/SubagentStop 是否继续，作者也限定适用任务应有可测完成条件；`snarktank/ralph` 每轮新上下文，借助 Git、progress.txt、prd.json 持久化进度，默认最多十轮、每次一个小故事。官方最佳实践强调给 agent 可运行的测试、构建、截图等完成证据，还可用独立子代理质疑实现者结论。可借鉴明确 done、有限迭代、独立验证与持久进度，不能把模型写一个 DONE 当作验收。([upneja 项目](https://github.com/upneja/ralph-wiggum)，[snarktank 项目](https://github.com/snarktank/ralph)，[官方验证建议](https://code.claude.com/docs/en/best-practices#give-claude-a-way-to-verify-its-work))

**【通知与远程审批】** `claude-remote-approver` 和 `claude-push` 用 PermissionRequest 加 ntfy 在手机上允许/拒绝，默认等待分别约 120 秒与 90 秒，无人回应回退终端；前者计划审批等待更久。项目也提示命令、路径经过第三方服务器，topic 泄露有风险。这能改善手机审批，却不能消除无人响应后的原生等待。本轮已有官方 Remote Control，应先验证现有链路，不默认引入第三方通知或公开命令数据。([remote-approver](https://github.com/yuuichieguchi/claude-remote-approver)，[claude-push](https://github.com/coa00/claude-push))

**【模型审批与守卫】** `claude-smart-approval` 先做 deny 规则及 shfmt 解析，再调用 Haiku，不确定则保留人工问题，还支持持久 allow 规则；`claude-code-haiku-guard` 已归档，其作者认为原生 auto mode 后额外分类器可能带来双重成本和分歧。本轮不引入这些付费模型审批，也不让模型自动 allow 危险删除。`destructive_command_guard` 区分执行上下文、普通数据、注释与 heredoc，值得借鉴减少误报的思路；`claude-code-hooks` 的 config-guard 提醒守卫配置也需保护，同时承认不是完整 shell parser。`permission-hook` 展示显式规则、通知限流和可选模型层，但 trust mode 或学习 allow 会扩大授权。([smart approval](https://github.com/froggeric/claude-smart-approval/blob/master/README.md)，[Haiku guard](https://github.com/Serg2000Mr/claude-code-haiku-guard)，[destructive command guard](https://github.com/Dicklesworthstone/destructive_command_guard#context-classification-system)，[config guard](https://github.com/karanb192/claude-code-hooks/blob/main/plugins/config-guard/README.md)，[permission hook](https://github.com/tantk/permission-hook))

上述社区项目均仅审阅公开说明或指定源码，没有运行其自动批准、推送或循环机制；自报成功、延迟或宣传数字不代表本机独立测试结果。

## 采用方案：在离席信号之后做有限、可审计的处理

本轮实现以 `claude-code-unattended` 现有 command hooks 和安装器为基础，不迁移到外层 runner。macOS 用 `ioreg -r -c IOHIDSystem -d 1`，子进程超时两秒，默认阈值 300 秒；检测到近期本机输入、状态未知、运行错误都不介入。运行参数为 `CCU_MODE=auto|off|away`、`CCU_IDLE_SECONDS=1..86400`（默认 300）、`CCU_MAX_STOP_CONTINUATIONS=1..20`（默认 3）。其中 `auto` 使用 HID 空闲判断，`off` 关闭自动介入，`away` 是使用者显式声明离席、跳过 HID 判断；`away` 不代表检测到了无人，也不扩大授权。安装器通过 `--mode`、`--idle-seconds`、`--max-stop-continuations` 写入 `unattended-config.json`，环境变量用于运行时覆盖。不支持阈值零。AskUserQuestion 只对低风险问题填入答案，保留已有答案，优先完整匹配推荐项，否则选第一项；存在高风险决定就拒绝并要求记入“待你决定”，不把推荐标签等同于授权。ExitPlanMode 离席时拒绝，不切换权限，明确仍受 plan mode 的只读约束。

Stop 仅拦截最后一行的窄范围可选继续邀请，优先读取 `last_assistant_message`，不依赖无界 transcript 扫描；跳过 `stop_hook_active`，会话隔离、指纹去重、默认每会话最多三次。固定 reason 只要求完成原已授权工作、记录合理假设，不能执行未获批准的对外动作或创造新任务。PermissionRequest 离席时统一 deny，并设 `interrupt:false`，引导安全替代、记录和跳过，拒绝不是授权。

CLAUDE.md 规则要求：例行可逆决定自己选稳妥默认并记录假设；只有确实缺授权的不可逆、对外、花钱、生产或权限扩张决定才汇总至最终“待你决定”；遇到阻塞继续独立的已授权工作；没有剩余工作就完整汇报并停止。它与 hook 的保守范围互补，也应保留用户自写规则和安装标记块之外的原文。

所有本轮自动作答、自动拒绝和强制继续，以及已有 bash guard 和 task redirect 的拒绝，也必须先成功追加 `~/.claude/unattended-decisions.jsonl`，然后才输出控制决定。字段包括 `time`、`session`、`type`、`summary`、`selected`、`content_sha256`；为防问题或命令包含凭据，只保存结构化摘要、问题/选项摘要指纹、从一开始的索引和推荐项标记等信息，不保存原始问题、命令、选项文本或完整对话。指纹可帮助关联重复内容，但不等于原文已加密保存。会话状态与审计并发写入加锁；审计失败则 fail-open，不产生看不见的自动决定。日志不主动清理，保留至少 186 天；这不等于加密日志，使用者仍需按本机资料管理方式保护它。

安装器维持幂等、备份、卸载和临时 `--claude-dir` 测试路径，默认保留已有通知与风格偏好；`--question-timeout`、`--agent-push`、`--proactive` 是显式 opt-in，不改全局 permission mode 或 dialogExpiry。这些可选全局设置在卸载时保留，避免撤销使用者后续偏好；卸载不意味着恢复安装前所有全局设置。审查通过前不安装到真实用户配置。确定性测试已在 Python 3.9 通过，后续仍在补充用例；覆盖近期 HID 活动不干预、未知信号 fail-open、低风险答案协议、风险拒绝、Stop 防循环与并发审计、安装更新保留用户内容等逻辑。测试数量与最终验收应以对应提交的测试记录为准，这些测试仍不等于 Desktop 产品验收。

## 未采用方案：不靠扩大权限或永久忙碌解决等待

不默认启用提问超时作为主方案，因为 Remote Control 会禁用其计时；不缩短 dialogExpiry 作为权限兜底，因为当前官方文档排除 AskUserQuestion 和权限请求；不默认覆盖 Proactive 或主动推送，因为它们是跨项目偏好且不解决授权层。

不自动批准危险删除、计划或所有 ask 规则，不切换 bypass、不持久学习新的 allow 规则，也不通过 shell、解释器、自动按 Enter 或 UI 点击绕过受保护动作。对明确缺授权的决定，“离席”不产生额外权限。缺审批时保留待办并推进独立工作，某些任务因此不能全部完成，这是必要的边界。

不全局启用无限 Ralph/keep-going、无条件 Stop/SubagentStop block、共享项目状态续跑或 DONE 字符串验收。它们可能浪费额度、重复执行、跨会话污染或违背停止意图；本轮只借鉴有限迭代、会话隔离、可运行完成标准和审计。不默认迁移到每轮新 `claude -p` 的 runner，因为本任务要改善现有桌面会话，迁移会增加上下文、鉴权和调度差异。

不接入 ntfy 或小模型审批：前者增加第三方数据与网络依赖，且超时后仍回原等待；后者引入本轮禁止的付费模型请求、延迟和判断分歧，自动 allow 也违反危险删除边界。配置自保护值得后续独立评估，本轮不叠加另一套 config-guard 权限语义，也不把 Python/正则守卫宣传成不可绕过的系统安全边界。

## 仍未解决：确定性协议通过不能替代真实桌面与手机验收

**关键时序限制：hooks 只在对应事件到来时采样一次空闲状态，不在后台持续监控或轮询已经打开的卡片。** 如果问题或权限请求在达到空闲阈值之前触发，hook 会按当时“在场”状态放行；随后负责人离开，已经打开的对话框不会因此自动重跑 hook、补答或拒绝。因此不能承诺“离开五分钟后，所有已有等待都会自动解除”。超时零不受支持；显式 `away` 模式也只影响之后发生的事件。需要靠规则在提问前减少例行请示、在任务开始前选择合适模式，并用 Remote Control/通知处理此类既有等待。

本次完成了公开文档与社区源码研究、本机安装包 schema 的只读核验，以及 HIDIdleTime 的原生只读测量。研究环境的 `claude` 不在 shell PATH，未安装 CLI，未发起模型/API 请求，也未修改真实配置或正在使用的会话。因此尚未现场验证 Desktop Code 的自动答案是否跳过可见卡片、空选择超时的精确返回结构、ExitPlanMode deny 后的 UI 和模式状态、PermissionRequest 对每种桌面弹窗的覆盖；不能写成已通过端到端验收。

手机通知投递、原生浏览器站点/剪贴板/App 能力卡片的远程响应、macOS 系统 TCC 权限、Remote Control 断连或机器休眠，仍不能由本轮 hooks 保证。初次站点与会话能力授权可能需要人工预授权或暂缓；不应自动操作系统权限界面。HID 在手机操作、阅读屏幕、远程桌面、锁屏、唤醒及多用户环境中的误判，也仍需自然使用观察。

推荐项选择、风险关键词和结尾正则都是可审计的启发式策略，不能证明真实意图或授权。保守实现会漏掉部分可以自动继续的情况；过度扩大匹配则会把询问当批准。后续应以真实任务的决定日志、漏报/误报和待办是否合理为依据收窄或调整规则，保持“明确完成才停止、明确缺授权才留待决定”的目标；不能用少弹窗数量替代正确完成与权限边界的验证。

本机确定性验证：Python 3.9 与 3.14 分别通过 48 个 unittest；包括临时目录安装与实际启动已安装 hook。仍未进行真实 Desktop/手机端到端实验。
