# 超星助手桌面版

基于 [Samueli924/chaoxing](https://github.com/Samueli924/chaoxing) 的 fork。

这个 fork 主要补的是多账号管理、协同题库、全局凭据、批量启动和桌面控制界面。

## 主要特点

- 多账号并行隔离：不同配置会自动使用各自独立的 `cookies/cache`，同仓库可以同时跑多个账号。
- 桌面控制台：基于 `PyQt5 + PyQt-Fluent-Widgets`，支持分页管理、批量启动停止、实时日志查看。
- JSON 配置：前台使用 `desktop_state/profiles/*.json` 管理配置，不需要在界面里直接折腾 ini。
- 运行时桥接：启动时会自动生成 `desktop_state/runtime_configs/*.ini`，继续复用原有核心逻辑。
- 全局设置：Enncy、SiliconFlow、通用 AI、LIKE、TikuAdapter、通知等默认凭据只填一次即可。
- 多题库协同：支持 `MultiTiku`，例如 `TikuYanxi + SiliconFlow`，答案冲突时会交给仲裁题库再判一次。
- 课程块选择：刷新课程列表后，可以直接按课程块勾选，不用手填 `courseId`。
- JSON 高级编辑：默认使用结构化表单，必要时也可以展开 JSON 编辑器直接修改。

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

程序内主要有 4 个页面：

- `概览`：查看配置数量、运行状态和数据目录
- `配置管理`：批量勾选、批量启动停止、结构化编辑配置
- `全局设置`：集中填写各类默认凭据
- `运行日志`：一个配置一个日志框，自动排版显示实时输出

## 配置文件位置

```text
desktop_state/
  global_settings.json
  profiles/
    user1.json
    user2.json
  runtime_configs/
    user1.ini
    user2.ini
```

说明：

- `profiles/*.json` 是前台主配置
- `runtime_configs/*.ini` 是运行时自动生成的桥接配置

## 导入旧 ini

如果你之前已经有：

```text
profiles/
  user1.ini
  user2.ini
```

桌面端启动后会自动尝试迁移；也可以在 `配置管理` 页面点击 `导入旧 ini`。

## 多题库协同

在 `配置管理` 页面里：

- 主题库下拉框可以指定单题库
- 协同题库区域可以直接点选多个题库块
- 勾 1 个时直接使用该题库
- 勾 2 个以上时自动按 `MultiTiku` 运行
- 冲突时交给 `decision_provider` 仲裁

常见组合：

- `TikuYanxi + SiliconFlow`
- `TikuYanxi + AI`

## 全局设置建议

推荐做法：

1. 先去 `全局设置` 填好通用令牌、密钥、接口地址和模型
2. 单个配置里只填写账号、课程和个别特殊项
3. 需要单独覆盖时，再在某个配置里填写对应字段

这样就不用在每个配置里重复填写：

- `tokens`
- `siliconflow_key`
- `endpoint`
- `key`
- `model`
- `http_proxy`
- 通知地址 / Telegram 会话 ID

## 批量启动和日志

在 `配置管理` 页面：

- 勾选多个配置
- 点击 `启动勾选` 或 `停止勾选`

在 `运行日志` 页面：

- 每个配置都有独立卡片
- 每张卡片都有启动、停止和日志框
- 终端 ANSI 彩色日志会自动转成普通文本显示

## 命令行入口

原有命令行入口仍然保留：

```bash
python main.py -c profiles/user1.ini
```

或：

```bash
python main.py -u 手机号 -p 密码 -l 课程ID1,课程ID2
```

如果你只是想快速批量生成 ini，也还能继续用：

```bash
python manage_profiles.py create user1 user2 user3
```

## 与上游的关系

- 上游保留的是命令行刷课主逻辑
- 本 fork 重点补的是多账号隔离、多题库协同、桌面控制层和 JSON 配置层

## 免责声明

- 本项目遵循 [GPL-3.0 License](LICENSE)
- 仅用于学习与技术研究，请勿用于盈利或违法用途
- 使用本项目产生的风险与后果，由使用者自行承担
