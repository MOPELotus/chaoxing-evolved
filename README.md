# 超星助手桌面版

基于 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) 的桌面化 fork。

当前仓库已经收敛为 `桌面端 UI + JSON 配置 + 核心刷课逻辑` 的结构，不再保留旧 Web 控制台、INI 模板和命令交互入口。

## 主要特点

- 多账号并行隔离：不同配置自动使用各自独立的 `cookies/cache`
- 桌面控制台：基于 `PyQt5 + PyQt-Fluent-Widgets`
- JSON 配置：前台统一使用 `desktop_state/profiles/*.json`
- 原生运行链路：桌面端直接读取 JSON 配置执行任务
- 全局设置：题库、AI、通知等默认凭据集中维护
- 桌面提醒：支持系统通知与应用内右下角提示，可在全局设置中开关
- 通知能力：支持 OneBot v11 反向 WebSocket，可推送到 QQ 私聊或群聊
- 多题库协同：支持 `MultiTiku` 与仲裁题库
- 课程块选择：刷新课程列表后直接按标签选择课程
- 批量操作：支持批量启动、批量停止、批量删除

## 环境要求

- Python `3.13+`

安装依赖：

```bash
pip install -r requirements.txt
```

或：

```bash
pip install .
```

## 启动方式

```bash
python desktop_app.py
```

## 界面结构

- `概览`：主页仪表盘、关键指标、数据目录、按配置排列的日志卡片
- `配置管理`：配置列表、批量操作、结构化编辑器、JSON 高级编辑
- `全局设置`：题库、AI 与通知的全局默认值

## 配置文件位置

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

说明：

- `profiles/*.json` 是桌面端主配置
- `*.cookies.txt` 与 `*.cache.json` 会按配置名自动生成，用于隔离登录状态和题库缓存
- `logs/` 用于保存每次运行的独立日志文件，可用于通知推送和问题排查

## 使用建议

推荐流程：

1. 先在 `全局设置` 中填写题库与 AI 默认凭据
2. 在 `配置管理` 中为每个账号建立独立配置
3. 通过课程标签选择课程，通过题库标签选择协同题库
4. 在 `概览` 页直接查看日志卡片并启动任务

## 与上游的关系

- 上游以命令行刷课流程为主
- 本 fork 重点补的是桌面控制层、JSON 配置层、多账号隔离和多题库协同

## 免责声明

- 本项目遵循 [GPL-3.0 License](LICENSE)
- 仅用于学习与技术研究，请勿用于盈利或违法用途
- 使用本项目产生的风险与后果，由使用者自行承担
