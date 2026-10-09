#!/usr/bin/env python3
"""PreToolUse(mcp__ccd_session__spawn_task) redirect.

The user is often away from the computer and wants out-of-scope work done
automatically, so a spawn_task chip (which waits for a click) is denied and
Claude is told to run the work itself as a background subagent instead.
"""
import json
import sys

REASON = (
    "已被 spawn_task_redirect hook 拦截：用户经常不在电脑边，不要用 spawn_task 留卡片等人点击。"
    "用户的规定是：范围外的事全部自动在后台做。请改用 Agent 工具自己处理："
    "把刚才 spawn_task 的 prompt 作为任务（必须自包含），设 run_in_background: true；"
    "当前目录是 git 仓库时加 isolation: \"worktree\"，让它在独立 worktree 里改。"
    "后台子代理只在自己的 worktree/分支里改动、提交并跑测试；它自己不合并、不 push、不部署、不动线上环境。"
    "然后继续手头的主任务。后台任务完成后由你收尾，每个分支都要处理掉，不能留着："
    "测试通过且符合预期就 `git merge --no-ff <分支>` 合并进你当前所在的分支（有冲突就解决并重跑测试），"
    "再用 `git worktree remove` + `git branch -d` 清掉；"
    "测试不过、没价值或冲突解决不了，就先记下分支名和最后的 commit hash，"
    "再用 `git worktree remove --force` + `git branch -D` 删除。只合并到本地，不为后台任务单独 push 或部署。"
    "主任务结束前先等所有后台任务处理完；最终汇报里列出每个后台任务做了什么、合并还是删除、理由、测试结果，"
    "删除的附上 commit hash。"
)


def main():
    try:
        sys.stdin.read()
    except Exception:
        pass
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": REASON,
        }
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
