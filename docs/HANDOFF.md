# 实施与验收状态

## 本轮目标与门禁

模式为**implementation，发布待门禁**。在基线`d3555349b611685d6193bcb0e5f12c9668e9d6aa`上，用成熟parser、现成config-guard分类和官方插件分发替换对应自研基础；无人值守授权政策保持。A阶段选型与固定许可/来源见[REUSE.md](REUSE.md)，前版研究见[RESEARCH.md](RESEARCH.md)。

PR #2 的提交`b765901b788a335a2e3cd3a7ea566100f18c166f`收到外部Claude复查的四项修改请求，四项修改已落实并完整重跑本地测试；旧头没有通过，新增安装器控制修订的新头CI及再次复查仍待完成。只有评论明确给出Claude复查通过，且完整40位SHA等于当前PR头，才可squash合并并安装；头改变必须重新复查。此文件不代表已批准或已发布。

## 实现范围与接口

- 官方shfmt v3.14.1二进制经固定SHA256校验，安装到配置根`tools/shfmt/shfmt`，Python标准库读取AST；不用Rust/Node/Go。`--shfmt PATH`仅导入精确匹配官方资产，`CCU_SHFMT`主要供隔离测试。hooks运行时不联网；替换旧手写分词、引号/heredoc处理，不把AST接受当安全证明。
- MIT config-guard分类改编为Python，保留原版权/许可及固定来源；Bash复用AST，Edit/MultiEdit/Write解析路径与symlink。只deny，先尝试结构化脱敏审计；确定性保护拒绝不因日志故障放行；不保留上游ask/allow override/原文日志。默认high不保护全局CLAUDE.md，hooks manifest仅匹配hooks/hooks.json。
- 可选官方plugin+marketplace，市场名和插件名均为`claude-code-unattended`；`hooks/hooks.json`经plugin_entry路由，SessionStart注入同一规则文件。持久配置/审计/预算留配置根。插件检测managed注册后不重复执行；managed安装拒绝同名启用插件。
- SessionStart在managed/native两入口检查shfmt固定版本；健康时检查不输出，缺失/版本不符时给systemMessage修复提示和additionalContext保守Shell safety上下文；仍fail-open，不自动联网修复。
- 负责人开关为unattended-config.json的`config_guard: on|off`，默认on，安装器`--config-guard on|off`，不提供环境覆盖。off放开普通受保护目标，但开关文件/已识别自停用仍保护；Claude转交负责人手动、Codex或负责人运行安装器，不自行停用。它是逻辑控制，不是OS权限边界。
- `--prepare-plugin`仅准备二进制、许可和配置，不改settings/CLAUDE.md；现有managed用户显式`--uninstall`后再准备并安装插件。默认发布仍使用managed安装及`--proactive`，不自动迁移入口、不设置提问超时。
- 保留HID三百秒门控、低风险答案、计划拒绝、PermissionRequest只deny、Stop窄匹配/每人类回合预算/去重与审计政策；Stop仍用稳定block输出，不额外改变兼容性。
- DCG不安装、不vendor；许可rider及故障政策不匹配。smart-approval仅参考思路，不引入模型审批、永久allow规则或第三方通知。

## 验证状态

前版基线为51项；复查前提交在Python 3.9和3.14各通过**102项unittest**；**四项修改后Python 3.9与3.14各113项完整unittest通过；新头CI与精确SHA复查待完成**。包含全部原场景、48条原shell分类与25条新增语义样例、共享parser故障/非阻塞超时、config-guard读写/审计/已知缺口、真实调用临时安装的guards、插件迁移、幂等/备份/卸载，以及CRLF/混合换行/无结尾换行/尾部空白逐字节保留。官方release网络下载流程也已在隔离目录使用系统Python成功验证；hooks运行时无网络。

新增测试覆盖managed/native SessionStart的健康、缺失、版本不符与超时路径；配置开关默认/更新/off普通目标/开关自保护、保护拒绝在日志故障时保持等场景也有回归覆盖。

独立源码复核发现并修复旧安装器首次安装裁剪正文空白、通用换行转换的问题；新增逐字节回归场景。公开内容隐私扫描和diff检查通过。测试命令：

```bash
CCU_SHFMT=/path/to/verified/shfmt python3 -m unittest discover -s tests -v
```

测试一律使用临时配置目录；CI预备固定校验二进制并离线导入。未进行真实Desktop/手机端到端验收，未调用付费模型。HID只代表本机输入；事件只采样一次，已有等待不自动补答；拒绝ExitPlanMode后仍只读；原生浏览器/App/MCP与系统权限不保证走本hook。

## 风险、发布与后续

配置保护是纵深防御，动态解释器/变量/函数/外部程序仍有缺口，fail-open不能保证防篡改。parser成功也不等于危险操作被完整分类。替使用者作答、计划/权限拒绝与Stop续跑仍先成功写脱敏审计；Bash改写、分支重定向和config-guard确定性保护拒绝的审计尽力而为，日志失败仍deny。解析/运行错误仍fail-open。审计和会话预算不自动清理，卸载保留，日志至少186天。

下一步向PR #2推四项修订的新头、重跑CI，重新写READY-FOR-REVIEW并等待与该40位SHA完全一致的Claude复查通过。旧头的意见不算新头批准；任何后续提交再次重置门禁。复查通过后按授权合并、更新安装副本、运行`python3 install.py --proactive`，比对settings和用户规则块外正文，再记录合并SHA与live状态。发布前不得修改实际用户配置。精确提交与发布结果由PR和任务进度记录追踪，避免为写入自己的SHA而生成新头。

回滚可`python3 install.py --uninstall`移除managed项，或先卸载插件再按备份恢复；卸载不撤销通知/风格/超时偏好，也不删除审计。自然使用中依据审计与实际任务结果观察误判/漏判，不能用确定性测试替代产品验收。

## 外部复查四项修订（已落实，待新头复查）

1. 固定shfmt依赖从静默失效改为SessionStart健康告知；健康不输出，异常仍不阻塞。
2. 配置保护的确定性deny与原有Bash/redirect一致，审计故障不解除保护；无人值守使用者决定仍须成功审计。
3. 缩小默认high误拦：排除全局CLAUDE.md，将hook manifest收窄到hooks/hooks.json，允许src/hooks.json等普通应用文件。
4. 增加负责人on/off配置开关与安装参数，无环境逃生口；关闭普通保护后仍保护开关/自停用路径，明确人工/Codex处理方式及其非OS安全边界。

上述修订已实现，Python 3.9和3.14各113项完整测试通过；尚未取得修订头批准，未合并、未安装实际用户配置。


复查补充：提交`e4da460572f64518595ac8bee25f4797064984b6`的CI 8/8通过，Claude确认四项修改均已验证，再要求堵住安装器卸载/切换入口。现已将`--uninstall`、`--config-guard`、`--mode`、`--prepare-plugin`全部作为负责人专用参数，在config_guard on/off时均拒绝Claude调用；普通不带这些参数的安装器更新及其他setup.py安装命令仍可运行。任何新头仍须重新通过CI及精确SHA复查。
