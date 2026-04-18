# 超星助手轻量桌面版

本仓库是基于 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) 持续维护的轻量桌面分支，定位为面向多账号、多题库协同与桌面集中管理场景的低依赖客户端版本。

当前分支前台采用 `tkinter + ttk` 实现，统一使用 JSON 配置，运行链路直接对接桌面端控制中心，不再保留旧式 INI 桥接方案。

## 功能概览

- 多账号并行隔离：每个档案自动使用独立的 Cookies 与题库缓存
- 轻量桌面控制中心：基于 `tkinter + ttk`
- JSON 配置体系：统一使用 `desktop_state/profiles/*.json`
- 原生运行链路：桌面端直接读取 JSON 配置并启动任务
- 全局设置：题库、AI、通知与桌面提醒的默认值集中维护
- 多题库协同：支持 `MultiTiku`、一致性比对与仲裁题库
- 课程选择：支持刷新课程列表后选择课程
- 批量操作：支持批量启动、批量停止、批量删除
- 通知能力：支持 `OneBot v11` 反向 WebSocket，可推送到 QQ 私聊或群聊
- 高级 JSON：支持直接编辑档案 JSON 与全局设置 JSON
- 低资源占用：不依赖 Qt、Fluent Widgets 或 GPU 加速

## 运行环境

- Python `3.13+`
- 桌面环境：
  - Windows `x64` / `ARM64`
  - macOS `Intel` / `Apple Silicon`
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

当前分支不需要额外安装 Qt 或 Fluent Widgets。

## 启动方式

```bash
python desktop_app.py
```

## 界面说明

- `概览`：显示档案统计、当前选中档案摘要与常用操作
- `档案设置`：用于维护档案列表、批量操作、结构化表单与课程刷新
- `全局设置`：用于维护题库默认值、通知默认值与桌面提醒开关
- `高级 JSON`：用于直接编辑当前档案 JSON
- `运行日志`：用于查看各档案的运行状态与实时日志

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
```

说明如下：

- `profiles/*.json` 为桌面端主配置文件
- `*.cookies.txt` 与 `*.cache.json` 会按档案名自动生成，用于隔离登录状态与题库缓存
- `logs/` 用于保存每次运行的独立日志文件，便于通知推送与问题排查

## 使用建议

建议按以下顺序完成初始化：

1. 在 `全局设置` 中填写题库、AI、通知与桌面提醒默认值
2. 在 `配置管理` 中为每个账号创建独立档案
3. 按需刷新课程列表，选择课程并配置协同题库
4. 在 `运行日志` 页查看运行状态，并按需启动或停止任务

## Release 构建

仓库已提供基于 GitHub Actions 的手动发布工作流，可用于触发多平台并行构建，并在构建完成后统一创建 Release。

典型流程如下：

1. 打开仓库 `Actions`
2. 选择 `Release`
3. 手动填写 `tag_name`、`release_name` 与 `prerelease`
4. 工作流会并行构建以下目标：
   - Windows `x64`
   - Windows `ARM64`
   - macOS `Intel`
   - macOS `Apple Silicon`
   - Linux `x64`
   - Linux `ARM64`
5. 所有成功产物会在最后统一汇总，并自动发布到 GitHub Release

如需本地构建，可执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_release_local.ps1 -Tag vtest -Architecture x64
```

```bash
bash scripts/build_release_unix.sh --tag vtest --os macos --arch arm64 --output-dir build-macos-arm64 --release-dir release
```

```bash
bash scripts/build_release_unix.sh --tag vtest --os linux --arch x64 --output-dir build-linux-x64 --release-dir release
```

说明如下：

- 本地构建必须使用与目标架构一致的 Python 环境
- `ARM64` 本地构建建议直接在 `Windows ARM64` 设备上执行
- Linux 发布会额外生成 `AppImage`、`deb` 与 `rpm`
- macOS 发布当前输出为压缩后的 `.app` 应用包
- 本分支 Windows 本地构建会使用 `tk-inter` 插件打包 `tkinter`

## 与上游的关系

- 上游项目以命令行刷课流程为主
- 本分支重点维护轻量桌面控制层、JSON 配置体系、多账号隔离、多题库协同与桌面通知体验

## 许可与声明

- 本项目遵循 [GPL-3.0 License](LICENSE)
- 本项目仅用于学习、研究与技术交流
- 使用本项目产生的风险与后果，由使用者自行承担
