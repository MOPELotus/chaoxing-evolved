# 超星助手桌面版

本仓库是基于 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) 持续维护的桌面化分支，定位为面向多账号与桌面集中管理场景的 Windows 客户端版本。

当前版本已经完成从旧命令行交互与 Web 控制页到桌面端的收敛，前台统一采用 JSON 配置，运行链路直接对接桌面端控制中心，不再保留旧式 INI 桥接方案。

## 功能概览

- 多账号并行隔离：每个档案自动使用独立的 Cookies 与题库缓存
- 桌面控制中心：基于 `PyQt6 + PyQt6-Fluent-Widgets`
- JSON 配置体系：统一使用 `desktop_state/profiles/*.json`
- 原生运行链路：桌面端直接读取 JSON 配置并启动任务
- 原生 Responses AI：支持多个站点、多个模型和思考强度；一次运行只使用一个站点/模型
- 题型与素材：支持选择、判断、填空、简答、匹配、排序、完形、阅读等题型，保留下划线、挖空、题干/材料图片
- 题库缓存：按题目语义、站点、模型和思考强度缓存答案，不执行全量题库扫描
- 课程选择：支持刷新课程列表后按块选择课程
- 批量操作：支持批量启动、批量停止、批量删除
- 进度控制：阅读停留时长、任意正倍速、任意正并发数；直播任务固定 1 倍速
- 完成确认：任务执行后通过课程知识点接口重新确认完成状态，挑战模式支持有限次重试

## 运行环境

- Python `3.13+`
- 桌面环境：
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
- `全局设置`：用于维护 Responses AI 默认值

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
- `logs/` 用于保存每次运行的独立日志文件，便于问题排查

## 使用建议

建议按以下顺序完成初始化：

1. 在 `配置管理` 的 Responses AI 区域填写站点 JSON、当前站点、模型和思考强度
2. 在 `配置管理` 中为每个账号创建独立档案
3. 通过课程块选择课程，并按需设置阅读停留时长、倍速和并发数
4. 在 `概览` 页查看运行日志，并按需启动或停止任务

站点配置示例：

```json
[
  {
    "name": "openai",
    "base_url": "https://api.openai.com",
    "api_key_env": "OPENAI_API_KEY",
    "models": ["gpt-5"]
  },
  {
    "name": "兼容站点",
    "base_url": "https://example.com",
    "api_key": "your-key",
    "protocol": "responses",
    "models": ["model-a", "model-b"]
  }
]
```

## Release 构建

仓库已提供基于 GitHub Actions 的手动发布工作流，可用于触发多平台并行构建，并在构建完成后统一创建 Release。

典型流程如下：

1. 打开仓库 `Actions`
2. 选择 `Release`
3. 手动填写 `tag_name`、`release_name` 与 `prerelease`
4. 工作流会并行构建以下目标：
   - Windows `x64`
   - Windows `ARM64`
   - Linux `x64`
   - Linux `ARM64`
5. 所有成功产物会在最后统一汇总，并自动发布到 GitHub Release

如需本地构建，可执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_release_local.ps1 -Tag vtest -Architecture x64
```

```bash
bash scripts/build_release_unix.sh --tag vtest --os linux --arch x64 --output-dir build-linux-x64 --release-dir release
```

说明如下：

- 本地构建必须使用与目标架构一致的 Python 环境
- `ARM64` 本地构建建议直接在 `Windows ARM64` 设备上执行
- Linux 发布会额外生成 `AppImage`、`deb` 与 `rpm`
- 由于 Nuitka 当前对 `PyQt6 on macOS` 的支持受限，GitHub Release 工作流暂不发布 macOS 构建

## 与上游的关系

- 上游项目以命令行刷课流程为主
- 本分支重点维护桌面控制层、JSON 配置体系、多账号隔离、Responses AI 答题与任务完成确认

## 许可与声明

- 本项目遵循 [GPL-3.0 License](LICENSE)
- 本项目仅用于学习、研究与技术交流
- 使用本项目产生的风险与后果，由使用者自行承担
