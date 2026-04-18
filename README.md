# 超星助手桌面版

本仓库是基于 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) 持续维护的桌面化分支，定位为面向多账号、多题库协同与桌面集中管理场景的 Windows 客户端版本。

当前版本已经完成从旧命令行交互与 Web 控制页到桌面端的收敛，前台统一采用 JSON 配置，运行链路直接对接桌面端控制中心，不再保留旧式 INI 桥接方案。

## 功能概览

- 多账号并行隔离：每个档案自动使用独立的 Cookies 与题库缓存
- 桌面控制中心：基于 `PyQt6 + PyQt6-Fluent-Widgets`
- JSON 配置体系：统一使用 `desktop_state/profiles/*.json`
- 原生运行链路：桌面端直接读取 JSON 配置并启动任务
- 全局设置：题库、AI、通知与桌面提醒的默认值集中维护
- 多题库协同：支持 `MultiTiku`、一致性比对与仲裁题库
- 课程选择：支持刷新课程列表后按块选择课程
- 批量操作：支持批量启动、批量停止、批量删除
- 通知能力：支持 `OneBot v11` 反向 WebSocket，可推送到 QQ 私聊或群聊
- 桌面提醒：支持系统通知与应用内右下角提示，可按事件类型开关

## 运行环境

- Python `3.13+`
- Windows 桌面环境

## 安装方式

安装项目依赖：

```bash
pip install -r requirements.txt
```

或直接安装当前项目：

```bash
pip install .
```

如需单独安装适配 `PyQt6` 的 Fluent 组件，可直接执行以下命令。

To install lite version for PyQt6:

```bash
pip install PyQt6-Fluent-Widgets -i https://pypi.org/simple/
```

## 启动方式

```bash
python desktop_app.py
```

## 界面说明

- `概览`：显示主页概况、关键指标、数据目录与按档案排列的实时日志卡片
- `配置管理`：用于维护档案列表、批量操作、结构化表单与高级 JSON 编辑
- `全局设置`：用于维护题库默认值、通知默认值与桌面提醒开关

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
3. 通过课程块选择课程，通过题库块选择协同题库
4. 在 `概览` 页查看运行日志，并按需启动或停止任务

## Release 构建

仓库已提供基于 GitHub Actions 的手动发布工作流，可用于触发 `Nuitka` 编译并自动创建 Release。

典型流程如下：

1. 打开仓库 `Actions`
2. 选择 `Release`
3. 手动填写 `tag_name`、`release_name` 与 `prerelease`
4. 工作流将在 `Windows` 环境下完成桌面端编译、压缩产物并自动发布到 GitHub Release

## 与上游的关系

- 上游项目以命令行刷课流程为主
- 本分支重点维护桌面控制层、JSON 配置体系、多账号隔离、多题库协同与桌面通知体验

## 许可与声明

- 本项目遵循 [GPL-3.0 License](LICENSE)
- 本项目仅用于学习、研究与技术交流
- 使用本项目产生的风险与后果，由使用者自行承担
