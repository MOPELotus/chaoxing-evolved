# 超星助手 TUI 版

本仓库是基于 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) 持续维护的终端控制台分支，定位为面向多账号、多题库协同与轻量化运行场景的 PowerShell TUI 版本。

当前版本以前台 JSON 配置为核心，运行链路直接对接后台宿主进程，并通过 PowerShell 全屏终端界面统一管理档案、任务与日志。

## 功能概览

- 多账号并行隔离：每个档案自动使用独立的 Cookies 与题库缓存
- PowerShell TUI 控制台：以全屏终端界面集中管理档案、任务与日志
- JSON 配置体系：统一使用 `desktop_state/profiles/*.json`
- 原生运行链路：后台宿主进程直接读取 JSON 配置并启动任务
- 全局设置：题库、AI、通知默认值集中维护
- 多题库协同：支持 `MultiTiku`、一致性比对与仲裁题库
- 课程选择：支持刷新课程列表后按编号选择课程
- 常用操作：支持创建、删除、启动、停止与查看日志
- 通知能力：支持 `OneBot v11` 反向 WebSocket，可推送到 QQ 私聊或群聊

## 运行环境

- Python `3.13+`
- PowerShell `7+`
- 终端环境：
  - Windows `x64` / `ARM64`
  - Linux `x64` / `ARM64`

## 安装方式

安装项目依赖：

```bash
pip install -r requirements.txt
```

或直接安装当前项目：

```bash
pip install .
```

## 启动方式

推荐直接执行：

```powershell
pwsh -NoLogo -File .\tui.ps1
```

也可以继续使用统一入口：

```bash
python desktop_app.py
```

## 界面说明

- `概览`：显示当前档案总览、运行状态与当前选中档案摘要
- `档案`：显示选中档案的账号、题库、课程与任务配置
- `日志`：显示选中档案最近一次运行的日志尾部
- `全局`：显示当前生效的题库与通知默认值

## 数据目录

```text
desktop_state/
  global_settings.json
  profiles/
    user1.json
    user2.json
    user1.cookies.txt
    user1.cache.json
  logs/
    user1/
      20260417-090000-ab12cd34.log
  tui/
    runs.json
```

说明如下：

- `profiles/*.json` 为 TUI 主配置文件
- `*.cookies.txt` 与 `*.cache.json` 会按档案名自动生成，用于隔离登录状态与题库缓存
- `logs/` 用于保存每次运行的独立日志文件
- `tui/runs.json` 用于保存 TUI 当前的后台任务状态

## 使用建议

建议按以下顺序完成初始化：

1. 在 `全局` 页中填写题库、AI 与通知默认值
2. 在档案列表中为每个账号创建独立档案
3. 在档案编辑菜单中填写账号、题库与课程信息
4. 在主界面查看运行状态，并按需启动或停止任务

## Release 构建

仓库保留了 GitHub Actions 发布工作流，但当前 TUI 分支的主入口已经切换为 PowerShell 终端界面，后续发布策略会以该入口为准。

典型流程如下：

1. 打开仓库 `Actions`
2. 选择 `Release`
3. 手动填写 `tag_name`、`release_name` 与 `prerelease`
4. 当前主线以源码运行方式为主，构建工作流后续会继续针对 TUI 入口调整

如需直接运行源码，当前推荐：

```powershell
pwsh -NoLogo -File .\tui.ps1
```

## 与上游的关系

- 上游项目以命令行刷课流程为主
- 本分支重点维护 PowerShell TUI 控制层、JSON 配置体系、多账号隔离与多题库协同体验

## 许可与声明

- 本项目遵循 [GPL-3.0 License](LICENSE)
- 本项目仅用于学习、研究与技术交流
- 使用本项目产生的风险与后果，由使用者自行承担
