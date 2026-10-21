# 实施与验收状态

## 本轮目标与门禁

模式为**implementation，发布待门禁**。在基线`d3555349b611685d6193bcb0e5f12c9668e9d6aa`上，用成熟parser、现成config-guard分类和官方插件分发替换对应自研基础；无人值守授权政策保持。A阶段选型与固定许可/来源见[REUSE.md](REUSE.md)，前版研究见[RESEARCH.md](RESEARCH.md)。

本分支实现及本地验证已完成，待PR的CI和指定复查。只有评论明确给出Claude复查通过，且完整40位SHA等于当前PR头，才可squash合并并安装；头改变必须重新复查。此文件不代表已批准或已发布。

## 实现范围与接口

- 官方shfmt v3.14.1二进制经固定SHA256校验，安装到配置根`tools/shfmt/shfmt`，Python标准库读取AST；不用Rust/Node/Go。`--shfmt PATH`仅导入精确匹配官方资产，`CCU_SHFMT`主要供隔离测试。hooks运行时不联网；替换旧手写分词、引号/heredoc处理，不把AST接受当安全证明。
- MIT config-guard分类改编为Python，保留原版权/许可及固定来源；Bash复用AST，Edit/MultiEdit/Write解析路径与symlink。只deny，先结构化脱敏审计；不保留上游ask/allow override/原文日志。
- 可选官方plugin+marketplace，市场名和插件名均为`claude-code-unattended`；`hooks/hooks.json`经plugin_entry路由，SessionStart注入同一规则文件。持久配置/审计/预算留配置根。插件检测managed注册后不重复执行；managed安装拒绝同名启用插件。
- `--prepare-plugin`仅准备二进制、许可和配置，不改settings/CLAUDE.md；现有managed用户显式`--uninstall`后再准备并安装插件。默认发布仍使用managed安装及`--proactive`，不自动迁移入口、不设置提问超时。
- 保留HID三百秒门控、低风险答案、计划拒绝、PermissionRequest只deny、Stop窄匹配/每人类回合预算/去重与审计政策；Stop仍用稳定block输出，不额外改变兼容性。
- DCG不安装、不vendor；许可rider及故障政策不匹配。smart-approval仅参考思路，不引入模型审批、永久allow规则或第三方通知。

## 验证状态

前版基线为51项；本分支在Python 3.9和3.14各通过**102项unittest**。包含全部原场景、48条原shell分类与25条新增语义样例、共享parser故障/非阻塞超时、config-guard读写/审计/已知缺口、真实调用临时安装的guards、插件迁移、幂等/备份/卸载，以及CRLF/混合换行/无结尾换行/尾部空白逐字节保留。官方release网络下载流程也已在隔离目录使用系统Python成功验证；hooks运行时无网络。

独立源码复核发现并修复旧安装器首次安装裁剪正文空白、通用换行转换的问题；新增逐字节回归场景。公开内容隐私扫描和diff检查通过。测试命令：

```bash
CCU_SHFMT=/path/to/verified/shfmt python3 -m unittest discover -s tests -v
```

测试一律使用临时配置目录；CI预备固定校验二进制并离线导入。未进行真实Desktop/手机端到端验收，未调用付费模型。HID只代表本机输入；事件只采样一次，已有等待不自动补答；拒绝ExitPlanMode后仍只读；原生浏览器/App/MCP与系统权限不保证走本hook。

## 风险、发布与后续

配置保护是纵深防御，动态解释器/变量/函数/外部程序仍有缺口，fail-open不能保证防篡改。parser成功也不等于危险操作被完整分类。新增自动决定先成功写脱敏审计；原有改写/重定向审计保持前版尽力而为语义。审计和会话预算不自动清理，卸载保留，日志至少186天。

下一步推PR并验证CI，等待匹配当前头的Claude复查；如需改动，推新头并重新等待。复查通过后按授权合并、更新安装副本、运行`python3 install.py --proactive`，比对settings和用户规则块外正文，再记录合并SHA与live状态。发布前不得修改实际用户配置。精确提交与发布结果由PR和任务进度记录追踪，避免为写入自己的SHA而生成新头。

回滚可`python3 install.py --uninstall`移除managed项，或先卸载插件再按备份恢复；卸载不撤销通知/风格/超时偏好，也不删除审计。自然使用中依据审计与实际任务结果观察误判/漏判，不能用确定性测试替代产品验收。
