## Shell safety
- 在 Bash 中使用 rm（尤其是 rm -rf 或带通配符时），路径里的每个变量都必须写成 "${VAR:?}" 的形式，或者直接用字面绝对路径。永远不要写 `rm $DIR/...` 这种未加保护的变量路径。
- 不要用 `bash -c` / `zsh -c` / `sh -c` / `eval` 把命令包成字符串执行，直接写命令。需要切换目录时用 `( cd 目录 && 命令 )` 子 shell，或 `git -C` / `go -C` 这类自带目录参数的写法；需要后台跑一串命令时，用 Bash 工具的 run_in_background，或把命令写进脚本文件后直接执行该脚本，不要用 `nohup zsh -c "..." &`。

## 范围外的事（分支任务）
- 用户经常不在电脑边。发现当前任务范围外、值得做的事时，不要用 spawn_task 弹卡片等用户点击，直接用 Agent 工具开后台子代理去做：设 run_in_background: true，在 git 仓库里再加 isolation: "worktree"，prompt 要自包含。
- 后台子代理只在自己的 worktree/分支里改动、提交并跑测试；它自己不合并、不 push、不部署、不动线上环境。
- 后台任务完成后由主会话收尾，每个分支都要处理掉，不能留着：
  - 测试通过、改动符合预期：`git merge --no-ff <分支>` 合并进主会话当前所在的分支；有冲突就解决，解决后再跑一遍测试。合并后用 `git worktree remove <路径>` 和 `git branch -d <分支>` 清掉。
  - 测试不过、改动没价值、或冲突解决不了：先记下分支名和最后一个 commit hash，再用 `git worktree remove --force <路径>` 和 `git branch -D <分支>` 删除。
  - 只合并到本地当前分支。要不要 push 跟主任务原本的流程走，不为后台任务单独 push 或部署。
- 主任务结束前，先等所有后台任务完成并处理完，再做最终汇报。汇报里列出每个后台任务：做了什么、合并还是删除、理由、测试结果；删除的附上 commit hash，方便用 `git branch <名字> <hash>` 找回。
