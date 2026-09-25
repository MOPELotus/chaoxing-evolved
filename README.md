# 超星助手桌面版

本仓库是基于 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) 持续维护的桌面化分支，定位为面向多账号与桌面集中管理场景的 Windows 客户端版本。

当前版本已经完成从旧命令行交互与 Web 控制页到桌面端的收敛，前台统一采用 JSON 配置，运行链路直接对接桌面端控制中心，不再保留旧式 INI 桥接方案。

## 功能概览

- 多账号并行隔离：每个档案自动使用独立的 Cookies 与 AI 答题缓存
- 桌面控制中心：基于 `PyQt6 + PyQt6-Fluent-Widgets`
- JSON 配置体系：统一使用 `desktop_state/profiles/*.json`
- 原生运行链路：桌面端直接读取 JSON 配置并启动任务
- 原生 Responses AI：当前配置一个接口站点、一个模型和一种思考强度
- 题型与素材：适配选择、判断、原生编辑器填空/主观题及匹配题，保留下划线、挖空、题干/材料图片；排序、完形、阅读等未完成原生提交适配的题型会阻止自动提交，不会将通用 JSON 当作有效作答
- 答题安全：仅在每题答案完整且可映射时提交；空白、歧义、越界、答案数量不符或未知控件会停止提交与保存，保留平台已有作答。手动模式、覆盖率和重试不会绕过检查，不再随机兜底交卷
- 单题 AI 重试：超时、连接中断、临时服务错误、空答案或格式错误默认最多重试4次（共5次请求），按2/4/8/16秒退避；只重试失败题，不重跑已成功题，不重复交卷。可用题库配置 `retry_attempts` 和 `retry_delay_seconds` 调整；认证、参数等不可恢复HTTP错误直接停止当前题
- 有限猜答重做：默认关闭，可在题库设置启用 `guess_retry_enabled`，`guess_retry_limit` 默认为额外3次、最多10次。首次必须覆盖率达标且每题答案合法；仅提交结果页明确未通过并提供重做表单时，改变单选/判断的答案组合，其余题目保留首次答案。不识别逐题对错，不保证提分或完成；不向题库缓存写入猜答。结果不明、题目变化、组合耗尽或达到上限时停止。每份测验的提交记录保存在配置旁的 `.guess-retry.cache.json`，重启及章节重试不会重置次数或盲目重交；中断记录需人工核实，不自动恢复。仅保存模式不启用猜答。
- 答案缓存：旧的纯题干缓存不再复用，新缓存同时区分题型、选项顺序和材料；桌面打包程序需要重新构建后才能包含这些修复
- AI 语义缓存：可选的按题目语义、接口站点、模型和思考强度缓存答案，不执行全量扫描；默认关闭
- 课程选择：支持刷新课程列表后按班级精确选择，同名课程的不同班级独立处理
- 课程失败隔离：单门课程执行失败后记录错误并继续下一门，全部尝试后统一汇总失败课程；有失败时仍以失败状态结束，不冒充全部完成。手动中断或退出请求立即停止，不继续后续课程。
- 阅读任务：依据平台任务标记识别待完成阅读，不再将 `property.read=true` 当作完成标记。无最低时长要求的任务使用当前阅读完成接口并复查知识点；有时长要求或要求不明时停止直接标记，交由阅读页面完成。接口返回失败不再误报成功。
- 结束报告：每个账号运行结束（包括执行异常）后，在日志逐课程输出平台确认的知识点完成数/总数、完成率、未完成及未知数量，并给出总汇总。按章节节点而非题目或附件任务计数；跳过、过期不冒充完成，读取失败或空列表不显示100%。中途失败时仍列出未执行的所选课程；强制终止进程无法保证输出报告。
- 批量操作：支持批量启动、批量停止、批量删除
- 进度控制：阅读页滚动日志计时、任意正倍速、任意正并发数；直播任务固定 1 倍速
- 完成确认：普通任务依据具体任务接口结果判定，直播和挑战任务额外确认知识点状态；已过期、平台明确不可提交的作业按跳过处理，不阻断课程

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

1. 在 `配置管理` 的 Responses AI 区域填写接口地址、密钥、模型和思考强度；需要复用答案时再勾选“启用语义缓存（默认关闭）”
2. 在 `配置管理` 中为每个账号创建独立档案
3. 通过课程块按班级精确选课，并按需设置阅读计时、倍速和并发数
4. 在 `概览` 页查看运行日志，并按需启动或停止任务

语义缓存开关位于配置的 `tiku` 节：

```json
{
  "semantic_cache_enabled": false
}
```

默认值为 `false`。关闭时不会读取或写入 AI 答案缓存；开启后才会按当前题目、站点、模型和思考强度懒加载缓存。

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
